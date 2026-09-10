"""Versioned fixed-route contract and supervised execution state machine."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HARD_MAX_LINEAR_MPS = 0.2
MAX_ROUTE_POSES = 100_000
ROUTE_SOURCE_KINDS = {
    "odometry_teaching_candidate",
    "onsite_taught",
    "synthetic_contract_fixture",
}


class FixedRouteError(ValueError):
    """A route cannot be trusted or executed under the first-release contract."""


@dataclass(frozen=True)
class RoutePose:
    """One reviewed planar pose in the route's map frame."""

    x_m: float
    y_m: float
    yaw_rad: float


@dataclass(frozen=True)
class InspectionStation:
    """A physical stop on the route, optionally linked to opaque markers."""

    station_id: str
    pose_index: int
    required: bool
    tag_ids: tuple[str, ...]


@dataclass(frozen=True)
class FixedRoute:
    """A human-taught, reviewed and immutable route definition."""

    route_id: str
    source_kind: str
    frame_id: str
    verified: bool
    verified_at: Optional[str]
    source_reference: str
    approved_max_linear_mps: float
    poses: tuple[RoutePose, ...]
    stations: tuple[InspectionStation, ...]
    source_sha256: str

    def segment_for_station(self, station_index: int) -> tuple[RoutePose, ...]:
        """Return the inclusive path segment ending at ``station_index``."""
        if not 0 <= station_index < len(self.stations):
            raise FixedRouteError("station index is outside the fixed route")
        start = 0
        if station_index > 0:
            start = self.stations[station_index - 1].pose_index
        end = self.stations[station_index].pose_index
        return self.poses[start:end + 1]


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise FixedRouteError(f"{field} is not a valid identifier")
    return value


def _finite_number(value: Any, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise FixedRouteError(f"{field} must be a finite number")
    return float(value)


def _utc_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise FixedRouteError(f"{field} must be an ISO 8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FixedRouteError(f"{field} must be an ISO 8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FixedRouteError(f"{field} must use UTC")
    return value


def _identifier_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise FixedRouteError(f"{field} must be a list")
    identifiers = tuple(_identifier(item, f"{field}[]") for item in value)
    if len(set(identifiers)) != len(identifiers):
        raise FixedRouteError(f"{field} must not contain duplicates")
    return identifiers


def _parse_route(document: dict[str, Any], source_sha256: str) -> FixedRoute:
    required = {
        "schema_version",
        "route_id",
        "source_kind",
        "frame_id",
        "verified",
        "verified_at",
        "source_reference",
        "approved_max_linear_mps",
        "poses",
        "stations",
    }
    if set(document) != required:
        raise FixedRouteError("fixed route has missing or unexpected top-level fields")
    if document["schema_version"] != "1.0":
        raise FixedRouteError("fixed route schema_version must be 1.0")

    route_id = _identifier(document["route_id"], "route_id")
    source_kind = document["source_kind"]
    if source_kind not in ROUTE_SOURCE_KINDS:
        raise FixedRouteError(
            "source_kind must be odometry_teaching_candidate, onsite_taught, "
            "or synthetic_contract_fixture"
        )
    frame_id = document["frame_id"]
    if frame_id != "map":
        raise FixedRouteError("fixed route frame_id must be map")
    verified = document["verified"]
    if not isinstance(verified, bool):
        raise FixedRouteError("verified must be a boolean")
    verified_at_value = document["verified_at"]
    verified_at = None
    if verified_at_value is not None:
        verified_at = _utc_timestamp(verified_at_value, "verified_at")
    source_reference = document["source_reference"]
    if not isinstance(source_reference, str):
        raise FixedRouteError("source_reference must be a string")

    approved_speed = _finite_number(
        document["approved_max_linear_mps"],
        "approved_max_linear_mps",
    )
    if not 0.0 < approved_speed <= HARD_MAX_LINEAR_MPS:
        raise FixedRouteError("approved_max_linear_mps must be in (0.0, 0.2]")

    pose_values = document["poses"]
    if not isinstance(pose_values, list) or not 2 <= len(pose_values) <= MAX_ROUTE_POSES:
        raise FixedRouteError("poses must contain 2-100000 route points")
    poses = []
    for index, value in enumerate(pose_values):
        if not isinstance(value, dict) or set(value) != {"x_m", "y_m", "yaw_rad"}:
            raise FixedRouteError(f"poses[{index}] must contain only x_m/y_m/yaw_rad")
        pose = RoutePose(
            x_m=_finite_number(value["x_m"], f"poses[{index}].x_m"),
            y_m=_finite_number(value["y_m"], f"poses[{index}].y_m"),
            yaw_rad=_finite_number(value["yaw_rad"], f"poses[{index}].yaw_rad"),
        )
        if not -math.pi <= pose.yaw_rad <= math.pi:
            raise FixedRouteError(f"poses[{index}].yaw_rad must be in [-pi, pi]")
        if poses and pose.x_m == poses[-1].x_m and pose.y_m == poses[-1].y_m:
            raise FixedRouteError("adjacent route poses must not share the same position")
        poses.append(pose)

    station_values = document["stations"]
    if not isinstance(station_values, list) or not station_values:
        raise FixedRouteError("stations must contain at least one inspection station")
    stations = []
    seen_station_ids: set[str] = set()
    previous_pose_index = 0
    for index, value in enumerate(station_values):
        if not isinstance(value, dict) or set(value) != {
            "station_id",
            "pose_index",
            "required",
            "tag_ids",
        }:
            raise FixedRouteError(
                f"stations[{index}] must contain only "
                "station_id/pose_index/required/tag_ids"
            )
        station_id = _identifier(value["station_id"], f"stations[{index}].station_id")
        if station_id in seen_station_ids:
            raise FixedRouteError("station_id values must be unique")
        pose_index = value["pose_index"]
        if not isinstance(pose_index, int) or isinstance(pose_index, bool):
            raise FixedRouteError(f"stations[{index}].pose_index must be an integer")
        if pose_index <= previous_pose_index or pose_index >= len(poses):
            raise FixedRouteError("station pose_index values must increase within the route")
        required_station = value["required"]
        if not isinstance(required_station, bool):
            raise FixedRouteError(f"stations[{index}].required must be a boolean")
        station = InspectionStation(
            station_id=station_id,
            pose_index=pose_index,
            required=required_station,
            tag_ids=_identifier_list(value["tag_ids"], f"stations[{index}].tag_ids"),
        )
        stations.append(station)
        seen_station_ids.add(station_id)
        previous_pose_index = pose_index
    if stations[-1].pose_index != len(poses) - 1:
        raise FixedRouteError("the final route pose must be an inspection station")

    if verified:
        if source_kind != "onsite_taught":
            raise FixedRouteError("a verified route must have source_kind=onsite_taught")
        if verified_at is None:
            raise FixedRouteError("a verified route requires verified_at")
        if not source_reference.strip():
            raise FixedRouteError("a verified route requires source_reference")
    elif verified_at is not None:
        raise FixedRouteError("an unverified route must have verified_at=null")

    return FixedRoute(
        route_id=route_id,
        source_kind=source_kind,
        frame_id=frame_id,
        verified=verified,
        verified_at=verified_at,
        source_reference=source_reference,
        approved_max_linear_mps=approved_speed,
        poses=tuple(poses),
        stations=tuple(stations),
        source_sha256=source_sha256,
    )


def load_fixed_route(path: Path, *, allow_unverified: bool = False) -> FixedRoute:
    """Load and validate a fixed route, rejecting commissioning data by default."""
    if not path.is_file() or path.is_symlink():
        raise FixedRouteError(f"fixed route must be a regular file: {path}")
    try:
        raw = path.read_bytes()
        document = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise FixedRouteError(f"cannot load fixed route: {exc}") from exc
    if not isinstance(document, dict):
        raise FixedRouteError("fixed route root must be a mapping")
    route = _parse_route(document, hashlib.sha256(raw).hexdigest())
    if not allow_unverified and not route.verified:
        raise FixedRouteError("fixed route is not verified for onsite execution")
    return route


class RouteState(str, Enum):
    """Operator-visible supervised-route states."""

    IDLE = "idle"
    ARMING = "arming"
    EXECUTING = "executing"
    PAUSED = "paused"
    AT_STATION = "at_station"
    COMPLETED = "completed"
    COMPLETED_WITH_EXCEPTIONS = "completed_with_exceptions"
    CANCELED = "canceled"


class RouteCommand(str, Enum):
    """Side effects requested by a state transition."""

    NONE = "none"
    SEND_SEGMENT = "send_segment"
    CANCEL_GOAL = "cancel_goal"


@dataclass(frozen=True)
class StationOutcome:
    """Operator acknowledgement for one inspection station."""

    station_id: str
    outcome: str


@dataclass(frozen=True)
class RouteTransition:
    """Result of one supervised-route event."""

    accepted: bool
    state: RouteState
    reason: str
    command: RouteCommand = RouteCommand.NONE


class RouteMachine:
    """Pure lock-latched state machine around segmented ``FollowPath`` goals."""

    def __init__(self, route: FixedRoute) -> None:
        self.route = route
        self.state = RouteState.IDLE
        self.reason = "idle"
        self.station_index = 0
        self.outcomes: list[StationOutcome] = []

    @property
    def current_station(self) -> Optional[InspectionStation]:
        """Return the segment target, if the route has not terminated."""
        if 0 <= self.station_index < len(self.route.stations):
            return self.route.stations[self.station_index]
        return None

    def current_segment(self) -> tuple[RoutePose, ...]:
        """Return the route segment for the current station target."""
        return self.route.segment_for_station(self.station_index)

    def _transition(
        self,
        *,
        accepted: bool,
        state: Optional[RouteState] = None,
        reason: str,
        command: RouteCommand = RouteCommand.NONE,
    ) -> RouteTransition:
        if accepted and state is not None:
            self.state = state
            self.reason = reason
        return RouteTransition(accepted, self.state, reason, command)

    def start(self) -> RouteTransition:
        """Start a fresh route from the first reviewed segment."""
        if self.state not in {
            RouteState.IDLE,
            RouteState.CANCELED,
            RouteState.COMPLETED,
            RouteState.COMPLETED_WITH_EXCEPTIONS,
        }:
            return self._transition(accepted=False, reason="route_already_active")
        self.station_index = 0
        self.outcomes = []
        return self._transition(
            accepted=True,
            state=RouteState.ARMING,
            reason="waiting_for_safe_auto_command",
            command=RouteCommand.SEND_SEGMENT,
        )

    def controller_active(self) -> RouteTransition:
        """Confirm that the current goal is producing safety-approved auto commands."""
        if self.state != RouteState.ARMING:
            return self._transition(accepted=False, reason="route_not_arming")
        return self._transition(
            accepted=True,
            state=RouteState.EXECUTING,
            reason="executing",
        )

    def pause(self, reason: str = "paused_by_operator") -> RouteTransition:
        """Cancel motion and latch a pause until an explicit resume request."""
        if self.state not in {RouteState.ARMING, RouteState.EXECUTING}:
            return self._transition(accepted=False, reason="route_not_moving")
        return self._transition(
            accepted=True,
            state=RouteState.PAUSED,
            reason=reason,
            command=RouteCommand.CANCEL_GOAL,
        )

    def controller_failed(self, reason: str) -> RouteTransition:
        """Latch controller/action failures without automatically retrying."""
        if self.state not in {RouteState.ARMING, RouteState.EXECUTING}:
            return self._transition(accepted=False, reason="stale_controller_result")
        return self._transition(
            accepted=True,
            state=RouteState.PAUSED,
            reason=reason,
        )

    def resume(self) -> RouteTransition:
        """Re-arm the same route segment after operator acknowledgement."""
        if self.state != RouteState.PAUSED:
            return self._transition(accepted=False, reason="route_not_paused")
        return self._transition(
            accepted=True,
            state=RouteState.ARMING,
            reason="waiting_for_safe_auto_command",
            command=RouteCommand.SEND_SEGMENT,
        )

    def reached_station(self) -> RouteTransition:
        """Stop at the current inspection station and request an outcome."""
        if self.state not in {RouteState.ARMING, RouteState.EXECUTING}:
            return self._transition(accepted=False, reason="stale_controller_result")
        return self._transition(
            accepted=True,
            state=RouteState.AT_STATION,
            reason="station_outcome_required",
        )

    def record_station(self, *, skipped: bool) -> RouteTransition:
        """Record a station result, then dispatch the next segment or finish."""
        if self.state != RouteState.AT_STATION or self.current_station is None:
            return self._transition(accepted=False, reason="route_not_at_station")
        station = self.current_station
        outcome = "skipped" if skipped else "completed"
        self.outcomes.append(StationOutcome(station.station_id, outcome))
        self.station_index += 1
        if self.station_index == len(self.route.stations):
            final_state = (
                RouteState.COMPLETED_WITH_EXCEPTIONS
                if any(item.outcome != "completed" for item in self.outcomes)
                else RouteState.COMPLETED
            )
            return self._transition(
                accepted=True,
                state=final_state,
                reason=final_state.value,
            )
        return self._transition(
            accepted=True,
            state=RouteState.ARMING,
            reason="waiting_for_safe_auto_command",
            command=RouteCommand.SEND_SEGMENT,
        )

    def cancel(self) -> RouteTransition:
        """Cancel the route; a later start begins again at station zero."""
        if self.state in {
            RouteState.IDLE,
            RouteState.CANCELED,
            RouteState.COMPLETED,
            RouteState.COMPLETED_WITH_EXCEPTIONS,
        }:
            return self._transition(accepted=False, reason="route_not_active")
        command = (
            RouteCommand.CANCEL_GOAL
            if self.state in {RouteState.ARMING, RouteState.EXECUTING}
            else RouteCommand.NONE
        )
        return self._transition(
            accepted=True,
            state=RouteState.CANCELED,
            reason="canceled_by_operator",
            command=command,
        )


def route_pose_length(poses: Sequence[RoutePose]) -> float:
    """Return planar path length for diagnostics and acceptance evidence."""
    return sum(
        math.hypot(current.x_m - previous.x_m, current.y_m - previous.y_m)
        for previous, current in zip(poses, poses[1:])
    )

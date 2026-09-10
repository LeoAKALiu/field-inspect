"""Immutable Scene Version, Verified Alignment, and ArUco Landmark registration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from .db import Database
from .inspection_package import Identifier, Pose3D, PoseTolerance, Sha256, StrictModel


class RegisteredLandmark(StrictModel):
    landmark_id: Identifier
    marker_id: int = Field(ge=0)
    dictionary: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    role: Literal["required", "auxiliary"]
    physical_size_m: float = Field(gt=0)
    registered_pose: Pose3D
    pose_tolerance: PoseTolerance
    min_valid_samples: int = Field(ge=1)
    route_portion: Literal["beginning", "middle", "end"] | None = None

    @model_validator(mode="after")
    def required_landmarks_need_route_portion(self) -> RegisteredLandmark:
        if self.role == "required" and self.route_portion is None:
            raise ValueError("required ArUco landmarks must declare route_portion")
        return self


class SceneRegistration(StrictModel):
    scene_id: Identifier
    scene_version_id: Identifier
    asset_sha256: Sha256
    alignment_id: Identifier
    evidence_sha256: Sha256
    landmarks: list[RegisteredLandmark] = Field(min_length=3, max_length=512)

    @model_validator(mode="after")
    def alignment_has_three_required_portions(self) -> SceneRegistration:
        required = [item for item in self.landmarks if item.role == "required"]
        portions = {item.route_portion for item in required}
        if len(required) < 3 or portions != {"beginning", "middle", "end"}:
            raise ValueError(
                "Verified Alignment must include required ArUco landmarks "
                "at beginning, middle, and end route portions"
            )
        marker_ids = [item.marker_id for item in self.landmarks]
        if len(set(marker_ids)) != len(marker_ids):
            raise ValueError("registered ArUco marker_id values must be unique")
        landmark_ids = [item.landmark_id for item in self.landmarks]
        if len(set(landmark_ids)) != len(landmark_ids):
            raise ValueError("registered landmark_id values must be unique")
        return self


class SceneRegistrationError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def register_scene(
    db: Database,
    registration: SceneRegistration,
    *,
    operator_label: str,
    registered_at: datetime | None = None,
) -> dict[str, Any]:
    """Insert an immutable Scene Version + Verified Alignment, or return idempotent success."""
    label = operator_label.strip()
    if not 1 <= len(label) <= 80:
        raise SceneRegistrationError(
            "invalid_operator_label",
            "operator_label must be 1-80 characters",
            status_code=400,
        )
    when = _iso_utc(registered_at or datetime.now(UTC))
    existing_version = db.query_one(
        "SELECT scene_id, asset_sha256 FROM scene_versions WHERE scene_version_id = ?",
        (registration.scene_version_id,),
    )
    existing_alignment = db.query_one(
        "SELECT scene_version_id, evidence_sha256 FROM verified_alignments WHERE alignment_id = ?",
        (registration.alignment_id,),
    )
    if existing_version is not None:
        same_version = (
            existing_version["scene_id"] == registration.scene_id
            and existing_version["asset_sha256"] == registration.asset_sha256
        )
        same_alignment = existing_alignment is not None and (
            existing_alignment["scene_version_id"] == registration.scene_version_id
            and existing_alignment["evidence_sha256"] == registration.evidence_sha256
        )
        if not (same_version and same_alignment):
            raise SceneRegistrationError(
                "registration_conflict",
                "Scene Version or Verified Alignment already exists with different content",
            )
        return {
            "status": "duplicate",
            "scene_version_id": registration.scene_version_id,
            "alignment_id": registration.alignment_id,
        }

    scene = db.query_one("SELECT id FROM scenes WHERE id = ?", (registration.scene_id,))
    if scene is None:
        raise SceneRegistrationError(
            "scene_not_found",
            f"scene {registration.scene_id} does not exist",
        )

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO scene_versions "
            "(scene_version_id, scene_id, asset_sha256, operator_label, registered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                registration.scene_version_id,
                registration.scene_id,
                registration.asset_sha256,
                label,
                when,
            ),
        )
        conn.execute(
            "INSERT INTO verified_alignments "
            "(alignment_id, scene_version_id, evidence_sha256, operator_label, registered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                registration.alignment_id,
                registration.scene_version_id,
                registration.evidence_sha256,
                label,
                when,
            ),
        )
        conn.executemany(
            "INSERT INTO registered_landmarks "
            "(landmark_id, alignment_id, marker_id, dictionary, role, physical_size_m, "
            "pos_x, pos_y, pos_z, roll_deg, pitch_deg, yaw_deg, translation_m, rotation_deg, "
            "min_valid_samples, route_portion) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.landmark_id,
                    registration.alignment_id,
                    item.marker_id,
                    item.dictionary,
                    item.role,
                    item.physical_size_m,
                    item.registered_pose.position.x,
                    item.registered_pose.position.y,
                    item.registered_pose.position.z,
                    item.registered_pose.rotation_rpy_deg.roll,
                    item.registered_pose.rotation_rpy_deg.pitch,
                    item.registered_pose.rotation_rpy_deg.yaw,
                    item.pose_tolerance.translation_m,
                    item.pose_tolerance.rotation_deg,
                    item.min_valid_samples,
                    item.route_portion,
                )
                for item in registration.landmarks
            ],
        )
    return {
        "status": "registered",
        "scene_version_id": registration.scene_version_id,
        "alignment_id": registration.alignment_id,
        "operator_label": label,
        "registered_at": when,
        "required_landmarks": sum(1 for item in registration.landmarks if item.role == "required"),
    }


def register_scene_from_file(
    db: Database,
    path: Path | str,
    *,
    operator_label: str,
    registered_at: datetime | None = None,
) -> dict[str, Any]:
    source = Path(path)
    if source.stat().st_size > 1_048_576:
        raise SceneRegistrationError(
            "registration_too_large",
            "registration JSON exceeds 1 MiB",
            status_code=400,
        )
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SceneRegistrationError(
            "invalid_registration",
            "registration file must contain a JSON object",
            status_code=400,
        )
    registration = SceneRegistration.model_validate(payload)
    return register_scene(
        db, registration, operator_label=operator_label, registered_at=registered_at
    )


def get_verified_alignment(db: Database, alignment_id: str, scene_version_id: str) -> dict | None:
    row = db.query_one(
        "SELECT a.alignment_id, a.scene_version_id, a.evidence_sha256, "
        "v.scene_id, v.asset_sha256 "
        "FROM verified_alignments a "
        "JOIN scene_versions v ON v.scene_version_id = a.scene_version_id "
        "WHERE a.alignment_id = ? AND a.scene_version_id = ?",
        (alignment_id, scene_version_id),
    )
    return dict(row) if row is not None else None

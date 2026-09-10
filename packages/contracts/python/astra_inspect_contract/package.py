"""Formal Inspection Package v2 boundary models and semantic validator."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import bagit
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
GitCommit = Annotated[str, StringConstraints(pattern=r"^(unknown|[a-f0-9]{7,40})$")]
RelativePayloadPath = Annotated[str, StringConstraints(min_length=1, max_length=512)]
ExceptionCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
DictionaryName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_]+$"),
]

MANIFEST_PATH = Path("data/manifest.json")
MAX_JSON_BYTES = 128 * 1024 * 1024
PERCENTILE_P = 95.0


class StrictModel(BaseModel):
    """Forbid undeclared fields and non-finite numeric values."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Position3D(StrictModel):
    x: float
    y: float
    z: float


class Point2D(StrictModel):
    x: float
    y: float


class RotationRpyDeg(StrictModel):
    roll: float
    pitch: float
    yaw: float


class Pose3D(StrictModel):
    position: Position3D
    rotation_rpy_deg: RotationRpyDeg


class PoseTolerance(StrictModel):
    translation_m: float = Field(gt=0)
    rotation_deg: float = Field(gt=0)


class SceneVersionRef(StrictModel):
    scene_id: Identifier
    scene_version_id: Identifier
    asset_sha256: Sha256


class AlignmentRef(StrictModel):
    alignment_id: Identifier
    evidence_sha256: Sha256


class BagReference(StrictModel):
    storage_id: Literal["mcap"]
    path: RelativePayloadPath


class ReplayReference(StrictModel):
    trajectory_path: RelativePayloadPath


class InspectionPackageArtifact(StrictModel):
    path: RelativePayloadPath
    kind: Literal["evidence_image", "pointcloud", "inspection_result"]
    display: bool


class PayloadInventoryEntry(StrictModel):
    path: RelativePayloadPath
    sha256: Sha256


class InspectionPackageException(StrictModel):
    code: ExceptionCode
    message: ShortText


class Detector(StrictModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    configuration: dict[str, Any]


class EvidenceLineage(StrictModel):
    camera_calibration_sha256: Sha256
    detector: Detector
    transform_id: Identifier
    transform_sha256: Sha256


class LandmarkObservation(StrictModel):
    seq: int = Field(ge=0)
    mcap_frame_timestamp: datetime
    corners_px: list[Point2D] = Field(min_length=4, max_length=4)
    pose_camera: Pose3D
    pose_scene: Pose3D
    translation_residual_m: float = Field(ge=0)
    rotation_residual_deg: float = Field(ge=0)
    camera_calibration_sha256: Sha256
    detector: Detector
    transform_id: Identifier
    transform_sha256: Sha256


class LandmarkEvidence(StrictModel):
    landmark_id: Identifier
    marker_id: int = Field(ge=0)
    dictionary: DictionaryName
    role: Literal["required", "auxiliary"]
    physical_size_m: float = Field(gt=0)
    registered_pose: Pose3D
    pose_tolerance: PoseTolerance
    min_valid_samples: int = Field(ge=1)
    route_portion: Literal["beginning", "middle", "end"] | None = None
    annotated_image_path: RelativePayloadPath
    lineage: EvidenceLineage
    observations: list[LandmarkObservation] = Field(min_length=1, max_length=100_000)
    sample_count: int = Field(ge=1)
    translation_p95_m: float = Field(ge=0)
    rotation_p95_deg: float = Field(ge=0)

    @model_validator(mode="after")
    def required_landmarks_declare_route_portion(self) -> LandmarkEvidence:
        if self.role == "required" and self.route_portion is None:
            raise ValueError("required ArUco landmarks must declare route_portion")
        return self


class FrameEvidence(StrictModel):
    path: RelativePayloadPath
    timestamp: datetime
    mcap_frame_timestamp: datetime


class AcceptanceEvidence(StrictModel):
    first_frame: FrameEvidence
    last_frame: FrameEvidence
    landmarks: list[LandmarkEvidence] = Field(min_length=1, max_length=512)


class InspectionPackageTrajectoryPoint(StrictModel):
    seq: int = Field(ge=0)
    timestamp: datetime
    position: Position3D
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    speed_mps: float | None = Field(default=None, ge=0)
    battery_pct: float | None = Field(default=None, ge=0, le=100)


class InspectionPackageTrajectory(StrictModel):
    schema_version: Literal["2.0"]
    run_id: Identifier
    scene_version_id: Identifier
    alignment_id: Identifier
    coordinate_system: Literal["scene_local_yup"]
    points: list[InspectionPackageTrajectoryPoint] = Field(min_length=1, max_length=1_000_000)


class InspectionPackageManifest(StrictModel):
    schema_version: Literal["2.0"]
    source_kind: Literal["synthetic_contract_fixture", "inspection_run"]
    run_id: Identifier
    package_sha256: Sha256
    name: ShortText
    status: Literal["completed", "completed_with_exceptions"]
    exceptions: list[InspectionPackageException] = Field(max_length=100)
    started_at: datetime
    ended_at: datetime
    coordinate_system: Literal["scene_local_yup"]
    scene_version: SceneVersionRef
    alignment: AlignmentRef
    git_commit: GitCommit
    config_sha256: Sha256
    model_version: str | None
    bag: BagReference
    replay: ReplayReference
    acceptance_evidence: AcceptanceEvidence
    artifacts: list[InspectionPackageArtifact] = Field(max_length=1000)
    payload_inventory: list[PayloadInventoryEntry] = Field(min_length=1, max_length=10_000)
    exit_reason: ShortText

    @model_validator(mode="after")
    def status_keeps_independent_exception_semantics(self) -> InspectionPackageManifest:
        if self.status == "completed" and self.exceptions:
            raise ValueError("completed packages must not declare exceptions")
        if self.status == "completed_with_exceptions" and not self.exceptions:
            raise ValueError("completed_with_exceptions must declare at least one exception")
        return self


def percentile_linear(values: Sequence[float], percentile: float = PERCENTILE_P) -> float:
    """Linear-interpolation percentile: index = (n-1) * p/100."""
    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] * (high - rank) + ordered[high] * (rank - low)


def canonical_package_sha256(entries: Sequence[tuple[str, str]]) -> str:
    """Hash sorted inventory lines `digest  path\\n` (paths relative to data/)."""
    lines = "".join(
        f"{digest.lower()}  {path}\n" for path, digest in sorted(entries, key=lambda item: item[0])
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use an explicit UTC offset")
    return value.astimezone(UTC)


def _payload_path(value: str, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"{field} must be a non-empty POSIX relative path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} must not contain control characters")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"{field} must stay inside the BagIt payload")
    return path


def _load_json(path: Path) -> dict:
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise ValueError(f"{path.name} exceeds the {MAX_JSON_BYTES}-byte JSON limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_plain_tree(root: Path) -> None:
    import os
    import stat
    if root.is_symlink() or not root.is_dir():
        raise ValueError("package must be a real directory")
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"package contains a link or special file: {relative}")
            if any(ord(c) < 32 or ord(c) == 127 for c in relative):
                raise ValueError("package contains a control character in a path")


def _payload_files(staged: Path) -> dict[str, Path]:
    data_root = staged / "data"
    files: dict[str, Path] = {}
    for path in data_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(data_root).as_posix()
        files[relative] = path
    return files


def _resolve(staged: Path, value: str, field: str) -> Path:
    relative = _payload_path(value, field)
    target = staged / "data" / Path(*relative.parts)
    if not target.is_file():
        raise ValueError(f"referenced payload path does not exist: {value}")
    if target.stat().st_size == 0:
        raise ValueError(f"referenced payload path is empty: {value}")
    return target


def _assert_bagit_complete(staged: Path, bag: bagit.Bag) -> None:
    if bag.version_info != (1, 0):
        raise ValueError(f"BagIt-Version must be 1.0, got {bag.version_info}")
    if (staged / "fetch.txt").exists():
        raise ValueError("fetch.txt is forbidden; USB packages must be complete")
    if not (staged / "manifest-sha256.txt").is_file():
        raise ValueError("manifest-sha256.txt is required")


def _validate_inventory(
    staged: Path, manifest: InspectionPackageManifest
) -> dict[str, str]:
    payload_files = _payload_files(staged)
    listed = {entry.path: entry.sha256 for entry in manifest.payload_inventory}
    if len(listed) != len(manifest.payload_inventory):
        raise ValueError("payload_inventory paths must be unique")
    expected = {path for path in payload_files if path != "manifest.json"}
    if set(listed) != expected:
        raise ValueError("payload_inventory must list every payload file except data/manifest.json")
    for path, digest in listed.items():
        actual = _file_sha256(payload_files[path])
        if actual != digest:
            raise ValueError(f"payload_inventory sha256 does not match {path}")
    computed = canonical_package_sha256(list(listed.items()))
    if computed != manifest.package_sha256:
        raise ValueError("package_sha256 does not match the canonical payload inventory")
    return listed


def _require_digest_file(staged: Path, relative: str, expected: str, field: str) -> None:
    path = staged / "data" / relative
    if not path.is_file():
        raise ValueError(f"{relative} is required to bind {field}")
    if _file_sha256(path) != expected:
        raise ValueError(f"{field} does not match {relative}")


def _validate_trajectory(
    staged: Path,
    manifest: InspectionPackageManifest,
    started_at: datetime,
    ended_at: datetime,
) -> InspectionPackageTrajectory:
    trajectory_path = _payload_path(manifest.replay.trajectory_path, "trajectory_path")
    trajectory = InspectionPackageTrajectory.model_validate(
        _load_json(staged / "data" / Path(*trajectory_path.parts))
    )
    if trajectory.run_id != manifest.run_id:
        raise ValueError("trajectory run_id does not match manifest run_id")
    if trajectory.scene_version_id != manifest.scene_version.scene_version_id:
        raise ValueError("trajectory scene_version_id does not match manifest scene version")
    if trajectory.alignment_id != manifest.alignment.alignment_id:
        raise ValueError("trajectory alignment_id does not match manifest alignment_id")
    previous_time: datetime | None = None
    for expected_seq, point in enumerate(trajectory.points):
        if point.seq != expected_seq:
            raise ValueError("trajectory seq must be contiguous and start at zero")
        point_time = _require_utc(point.timestamp, f"points[{expected_seq}].timestamp")
        if not started_at <= point_time <= ended_at:
            raise ValueError("trajectory timestamps must be inside the run interval")
        if previous_time is not None and point_time <= previous_time:
            raise ValueError("trajectory timestamps must be strictly increasing UTC")
        previous_time = point_time
    return trajectory


def _validate_acceptance(
    staged: Path,
    manifest: InspectionPackageManifest,
    listed: dict[str, str],
    started_at: datetime,
    ended_at: datetime,
) -> None:
    evidence = manifest.acceptance_evidence
    first_ts = _require_utc(evidence.first_frame.timestamp, "first_frame.timestamp")
    last_ts = _require_utc(evidence.last_frame.timestamp, "last_frame.timestamp")
    _require_utc(evidence.first_frame.mcap_frame_timestamp, "first_frame.mcap_frame_timestamp")
    _require_utc(evidence.last_frame.mcap_frame_timestamp, "last_frame.mcap_frame_timestamp")
    if not started_at <= first_ts <= ended_at or not started_at <= last_ts <= ended_at:
        raise ValueError("acceptance frame timestamps must be inside the run interval")
    if last_ts < first_ts:
        raise ValueError("last_frame timestamp must not precede first_frame")

    landmark_ids: set[str] = set()
    marker_ids: set[int] = set()
    for landmark in evidence.landmarks:
        if landmark.landmark_id in landmark_ids:
            raise ValueError(f"duplicate landmark_id: {landmark.landmark_id}")
        if landmark.marker_id in marker_ids:
            raise ValueError(f"duplicate ArUco marker_id: {landmark.marker_id}")
        landmark_ids.add(landmark.landmark_id)
        marker_ids.add(landmark.marker_id)
        if landmark.sample_count != len(landmark.observations):
            raise ValueError(f"{landmark.landmark_id} sample_count must equal observation count")
        previous_time: datetime | None = None
        translations: list[float] = []
        rotations: list[float] = []
        for expected_seq, observation in enumerate(landmark.observations):
            if observation.seq != expected_seq:
                raise ValueError(
                    f"{landmark.landmark_id} observation seq must be contiguous and start at zero"
                )
            observed_at = _require_utc(
                observation.mcap_frame_timestamp,
                f"{landmark.landmark_id}.observations[{expected_seq}].mcap_frame_timestamp",
            )
            if not started_at <= observed_at <= ended_at:
                raise ValueError(
                    f"{landmark.landmark_id} observation timestamps must be inside the run interval"
                )
            if previous_time is not None and observed_at <= previous_time:
                raise ValueError(
                    f"{landmark.landmark_id} observation timestamps must be strictly increasing UTC"
                )
            previous_time = observed_at
            translations.append(observation.translation_residual_m)
            rotations.append(observation.rotation_residual_deg)
            if observation.camera_calibration_sha256 != landmark.lineage.camera_calibration_sha256:
                raise ValueError(
                    f"{landmark.landmark_id} observation calibration digest "
                    "must match landmark lineage"
                )
            if observation.transform_id != landmark.lineage.transform_id:
                raise ValueError(
                    f"{landmark.landmark_id} observation transform_id must match landmark lineage"
                )
            if observation.transform_sha256 != landmark.lineage.transform_sha256:
                raise ValueError(
                    f"{landmark.landmark_id} observation transform digest "
                    "must match landmark lineage"
                )
            if observation.detector != landmark.lineage.detector:
                raise ValueError(
                    f"{landmark.landmark_id} observation detector must match landmark lineage"
                )
        translation_p95 = percentile_linear(translations)
        rotation_p95 = percentile_linear(rotations)
        if not math.isclose(translation_p95, landmark.translation_p95_m, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(
                f"{landmark.landmark_id} translation_p95_m does not match the residual series"
            )
        if not math.isclose(rotation_p95, landmark.rotation_p95_deg, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(
                f"{landmark.landmark_id} rotation_p95_deg does not match the residual series"
            )
        _require_digest_file(
            staged,
            "config/camera_calibration.json",
            landmark.lineage.camera_calibration_sha256,
            "camera_calibration_sha256",
        )
        _require_digest_file(
            staged,
            "alignment/camera_to_scene.json",
            landmark.lineage.transform_sha256,
            "transform_sha256",
        )

    referenced = [
        (manifest.bag.path, "bag.path"),
        (manifest.replay.trajectory_path, "trajectory_path"),
        (evidence.first_frame.path, "first_frame.path"),
        (evidence.last_frame.path, "last_frame.path"),
        *[(artifact.path, "artifacts.path") for artifact in manifest.artifacts],
        *[
            (landmark.annotated_image_path, "annotated_image_path")
            for landmark in evidence.landmarks
        ],
    ]
    for value, field in referenced:
        _payload_path(value, field)
        _resolve(staged, value, field)
        if value not in listed:
            raise ValueError(f"{field} must appear in payload_inventory: {value}")


def validate_v2_semantics(
    staged: Path,
    bag: bagit.Bag,
    raw: dict | None = None,
) -> tuple[InspectionPackageManifest, InspectionPackageTrajectory]:
    """Validate a staged BagIt tree as Formal Inspection Package v2."""
    _assert_bagit_complete(staged, bag)
    payload = raw if raw is not None else _load_json(staged / MANIFEST_PATH)
    if payload.get("schema_version") != "2.0":
        raise ValueError("Formal Inspection Package must declare schema_version 2.0")

    manifest = InspectionPackageManifest.model_validate(payload)
    started_at = _require_utc(manifest.started_at, "started_at")
    ended_at = _require_utc(manifest.ended_at, "ended_at")
    if ended_at < started_at:
        raise ValueError("ended_at must not precede started_at")

    config_path = staged / "data/config/system.yaml"
    if not config_path.is_file():
        raise ValueError("data/config/system.yaml is required")
    if _file_sha256(config_path) != manifest.config_sha256:
        raise ValueError("config_sha256 does not match data/config/system.yaml")

    listed = _validate_inventory(staged, manifest)
    _require_digest_file(
        staged,
        "alignment/evidence.json",
        manifest.alignment.evidence_sha256,
        "alignment.evidence_sha256",
    )
    _require_digest_file(
        staged,
        "scene/scene-version.json",
        manifest.scene_version.asset_sha256,
        "scene_version.asset_sha256",
    )
    trajectory = _validate_trajectory(staged, manifest, started_at, ended_at)
    _validate_acceptance(staged, manifest, listed, started_at, ended_at)
    return manifest, trajectory


def validate_inspection_package(
    root: Path | str,
) -> tuple[InspectionPackageManifest, InspectionPackageTrajectory]:
    """Public seam: validate one Formal Inspection Package v2 directory."""
    staged = Path(root)
    if (staged / "fetch.txt").exists():
        raise ValueError("fetch.txt is forbidden; USB packages must be complete")
    _assert_plain_tree(staged)
    bag = bagit.Bag(str(staged))
    try:
        bag.validate(processes=1)
    except bagit.BagError as exc:
        raise ValueError(str(exc)) from exc
    return validate_v2_semantics(staged, bag)

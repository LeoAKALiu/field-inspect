"""Create immutable per-run snapshots of robot configuration inputs."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

import yaml
from ament_index_python.packages import get_package_share_directory

from inspection_pipeline.camera_calibration import (
    CameraCalibrationError,
    load_calibration_target,
    load_calibration_profile,
    validate_camera_calibration,
)
from inspection_pipeline.fixed_route import FixedRouteError, load_fixed_route


class ConfigSnapshotError(RuntimeError):
    """The configured run inputs cannot be snapshotted safely."""


@dataclass(frozen=True)
class RunConfigSnapshot:
    """Traceable external files copied beside one inspection run."""

    camera_calibration: Optional[dict[str, Any]]
    fixed_route: Optional[dict[str, Any]]
    fast_lio_config: Optional[dict[str, str]]
    extrinsics_verified: bool


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _camera_parameters(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigSnapshotError("system config root must be a mapping")
    section = data.get("hik_camera_node", {})
    if not isinstance(section, dict):
        raise ConfigSnapshotError("hik_camera_node config must be a mapping")
    params = section.get("ros__parameters", section)
    if not isinstance(params, dict):
        raise ConfigSnapshotError("hik_camera_node.ros__parameters must be a mapping")
    return params


def _section_parameters(data: Any, section_name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigSnapshotError("system config root must be a mapping")
    section = data.get(section_name, {})
    if not isinstance(section, dict):
        raise ConfigSnapshotError(f"{section_name} config must be a mapping")
    params = section.get("ros__parameters", section)
    if not isinstance(params, dict):
        raise ConfigSnapshotError(f"{section_name}.ros__parameters must be a mapping")
    return params


def resolve_local_calibration_url(
    url: str,
    *,
    package_resolver: Callable[[str], str] = get_package_share_directory,
) -> Path:
    """Resolve the local CameraInfoManager URL forms accepted by this project."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            raise ConfigSnapshotError("file calibration URL must refer to the local host")
        path = Path(unquote(parsed.path))
    elif parsed.scheme == "package":
        if not parsed.netloc or not parsed.path.strip("/"):
            raise ConfigSnapshotError("package calibration URL must include package and file")
        try:
            share = Path(package_resolver(parsed.netloc)).resolve()
        except (LookupError, ValueError) as exc:
            raise ConfigSnapshotError(
                f"camera calibration package is unavailable: {parsed.netloc}"
            ) from exc
        path = (share / unquote(parsed.path.lstrip("/"))).resolve()
        if not path.is_relative_to(share):
            raise ConfigSnapshotError("package calibration URL escapes the package directory")
    else:
        raise ConfigSnapshotError("camera calibration URL must use file:// or package://")

    if not path.is_file():
        raise ConfigSnapshotError(f"camera calibration file does not exist: {path}")
    return path


def snapshot_run_config(
    system_config: Path,
    destination: Path,
    *,
    package_resolver: Callable[[str], str] = get_package_share_directory,
) -> RunConfigSnapshot:
    """Copy system config and its optional calibration into ``destination``."""
    if not system_config.is_file():
        raise ConfigSnapshotError(f"system config does not exist: {system_config}")
    try:
        data = yaml.safe_load(system_config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigSnapshotError(f"cannot load system config: {exc}") from exc

    camera_params = _camera_parameters(data)
    calibration_url = camera_params.get("calibration_url", "")
    if not isinstance(calibration_url, str):
        raise ConfigSnapshotError("hik_camera_node.calibration_url must be a string")

    inspection_params = _section_parameters(data, "inspection_manager")
    target_url = inspection_params.get("calibration_target_url", "")
    if not isinstance(target_url, str):
        raise ConfigSnapshotError(
            "inspection_manager.calibration_target_url must be a string"
        )
    profile_url = inspection_params.get("calibration_profile_url", "")
    if not isinstance(profile_url, str):
        raise ConfigSnapshotError(
            "inspection_manager.calibration_profile_url must be a string"
        )
    if len({bool(calibration_url), bool(target_url), bool(profile_url)}) != 1:
        raise ConfigSnapshotError(
            "camera calibration, target, and profile URLs must be configured together"
        )

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(system_config, destination / "system.yaml")
    camera_calibration = None
    if calibration_url:
        source = resolve_local_calibration_url(
            calibration_url,
            package_resolver=package_resolver,
        )
        suffix = source.suffix.lower()
        if suffix not in (".yaml", ".yml", ".ini"):
            raise ConfigSnapshotError("camera calibration file must be YAML or INI")
        snapshot = destination / f"camera_calibration{suffix}"
        shutil.copy2(source, snapshot)
        try:
            validate_camera_calibration(snapshot, camera_params)
        except CameraCalibrationError as exc:
            raise ConfigSnapshotError(f"invalid camera calibration: {exc}") from exc
        camera_calibration = {
            "source_url": calibration_url,
            "path": f"config/{snapshot.name}",
            "sha256": _sha256(snapshot),
        }

        target_source = resolve_local_calibration_url(
            target_url,
            package_resolver=package_resolver,
        )
        if target_source.suffix.lower() not in (".yaml", ".yml"):
            raise ConfigSnapshotError("camera calibration target must be YAML")
        try:
            target = load_calibration_target(target_source)
        except CameraCalibrationError as exc:
            raise ConfigSnapshotError(f"invalid camera calibration target: {exc}") from exc
        if not target.dimensions_verified:
            raise ConfigSnapshotError(
                "camera calibration target dimensions are not verified"
            )
        target_snapshot = destination / "camera_calibration_target.yaml"
        shutil.copy2(target_source, target_snapshot)

        profile_source = resolve_local_calibration_url(
            profile_url,
            package_resolver=package_resolver,
        )
        if profile_source.suffix.lower() not in (".yaml", ".yml"):
            raise ConfigSnapshotError("camera calibration profile must be YAML")
        try:
            profile = load_calibration_profile(
                profile_source,
                camera=camera_params,
                calibration_path=source,
                target_path=target_source,
            )
        except CameraCalibrationError as exc:
            raise ConfigSnapshotError(f"invalid camera calibration profile: {exc}") from exc
        profile_snapshot = destination / "camera_calibration_profile.yaml"
        shutil.copy2(profile_source, profile_snapshot)
        camera_calibration.update(
            {
                "target_id": target.target_id,
                "target_pattern": target.pattern,
                "target_verified_at": target.verified_at,
                "target_source_url": target_url,
                "target_path": f"config/{target_snapshot.name}",
                "target_sha256": _sha256(target_snapshot),
                "profile_id": profile["profile_id"],
                "profile_source_url": profile_url,
                "profile_path": f"config/{profile_snapshot.name}",
                "profile_sha256": _sha256(profile_snapshot),
                "identity_sha256": profile["identity_sha256"],
                "camera_model": profile["identity"]["camera"]["camera_model"],
                "camera_serial": profile["identity"]["camera"]["camera_serial"],
                "lens_id": profile["identity"]["camera"]["lens_id"],
            }
        )

    bringup_params = _section_parameters(data, "scout_bringup")
    fast_lio_name = bringup_params.get("fast_lio_config_file", "")
    if not isinstance(fast_lio_name, str):
        raise ConfigSnapshotError("scout_bringup.fast_lio_config_file must be a string")
    fast_lio_config = None
    if fast_lio_name:
        relative = Path(fast_lio_name)
        if relative.name != fast_lio_name or relative.suffix.lower() not in {".yaml", ".yml"}:
            raise ConfigSnapshotError(
                "scout_bringup.fast_lio_config_file must be a YAML filename"
            )
        try:
            fast_lio_share = Path(package_resolver("fast_lio"))
        except (LookupError, ValueError) as exc:
            raise ConfigSnapshotError("fast_lio package is unavailable") from exc
        config_root = (fast_lio_share / "config").resolve()
        candidate = config_root / fast_lio_name
        source = candidate.resolve()
        if not candidate.is_file():
            raise ConfigSnapshotError(f"FAST-LIO config does not exist: {source}")
        snapshot = destination / "fast_lio.yaml"
        shutil.copy2(source, snapshot)
        fast_lio_config = {
            "source_package": "fast_lio",
            "source_name": fast_lio_name,
            "path": "config/fast_lio.yaml",
            "sha256": _sha256(snapshot),
        }

    route_params = _section_parameters(data, "route_orchestrator")
    route_path_value = route_params.get("fixed_route_path", "")
    if not isinstance(route_path_value, str):
        raise ConfigSnapshotError("route_orchestrator.fixed_route_path must be a string")
    fixed_route = None
    if route_path_value:
        route_path = Path(route_path_value)
        if not route_path.is_absolute():
            raise ConfigSnapshotError(
                "route_orchestrator.fixed_route_path must be absolute"
            )
        if not route_path.is_file() or route_path.is_symlink():
            raise ConfigSnapshotError(
                f"fixed route must be a regular file: {route_path}"
            )
        route_snapshot = destination / "fixed_route.yaml"
        try:
            shutil.copy2(route_path, route_snapshot)
            route = load_fixed_route(route_snapshot, allow_unverified=True)
        except (OSError, FixedRouteError) as exc:
            raise ConfigSnapshotError(f"cannot snapshot fixed route: {exc}") from exc
        fixed_route = {
            "route_id": route.route_id,
            "source_path": str(route_path),
            "path": "config/fixed_route.yaml",
            "sha256": _sha256(route_snapshot),
            "verified": route.verified,
        }

    return RunConfigSnapshot(
        camera_calibration=camera_calibration,
        fixed_route=fixed_route,
        fast_lio_config=fast_lio_config,
        extrinsics_verified=bringup_params.get("extrinsics_verified") is True,
    )

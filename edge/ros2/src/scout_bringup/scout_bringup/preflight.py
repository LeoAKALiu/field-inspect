"""Preflight checks for optional devices and third-party packages."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
except ImportError:  # pragma: no cover - exercised only outside ROS installs
    PackageNotFoundError = Exception  # type: ignore[misc, assignment]

    def get_package_share_directory(_package_name: str) -> str:
        raise PackageNotFoundError("ament_index_python is unavailable")


@dataclass(frozen=True)
class Diagnostic:
    """Single preflight diagnostic message."""

    level: str
    code: str
    message: str

    def format(self) -> str:
        """Return a single-line diagnostic string."""
        return f"{self.level.upper():5} [{self.code}] {self.message}"


@dataclass(frozen=True)
class CanInterfaceStatus:
    """Observable SocketCAN readiness used by the startup gate."""

    exists: bool
    is_can: bool
    is_up: bool
    bitrate: int | None
    restart_ms: int | None


VENDOR_PACKAGES = {
    "start_scout": ("scout_base", "third_party/scout_ros2"),
    "start_livox": ("livox_ros_driver2", "livox_ros_driver2"),
    "start_fast_lio": ("fast_lio", "third_party/FAST_LIO"),
}
ROUTE_PACKAGES = (
    "nav2_controller",
    "nav2_lifecycle_manager",
    "nav2_msgs",
    "nav2_regulated_pure_pursuit_controller",
)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ping_host(host: str, *, count: int = 1, timeout_s: float = 1.0) -> bool:
    if not host or shutil.which("ping") is None:
        return False
    command = ["ping", "-c", str(count), "-W", str(max(int(timeout_s), 1)), host]
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _interface_exists(name: str) -> bool:
    return Path("/sys/class/net").joinpath(name).exists()


def _interface_has_ip(name: str, address: str) -> bool:
    if not name or not address or shutil.which("ip") is None:
        return False
    completed = subprocess.run(
        ["ip", "-j", "address", "show", "dev", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return False
    try:
        interfaces = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    return any(
        item.get("local") == address
        for interface in interfaces
        for item in interface.get("addr_info", [])
    )


def _inspect_can_interface(name: str) -> CanInterfaceStatus:
    if not name or shutil.which("ip") is None:
        return CanInterfaceStatus(False, False, False, None, None)
    completed = subprocess.run(
        ["ip", "-details", "-json", "link", "show", "dev", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return CanInterfaceStatus(False, False, False, None, None)
    try:
        interfaces = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return CanInterfaceStatus(True, False, False, None, None)
    if not interfaces:
        return CanInterfaceStatus(False, False, False, None, None)
    interface = interfaces[0]
    link_info = interface.get("linkinfo", {})
    info_data = link_info.get("info_data", {})
    bitrate = info_data.get("bittiming", {}).get("bitrate")
    restart_ms = info_data.get("restart_ms")
    return CanInterfaceStatus(
        exists=True,
        is_can=link_info.get("info_kind") == "can",
        is_up="UP" in interface.get("flags", []),
        bitrate=int(bitrate) if isinstance(bitrate, int) else None,
        restart_ms=int(restart_ms) if isinstance(restart_ms, int) else None,
    )


def _running_kernel_release() -> str:
    return platform.release()


def _gs_usb_module_release() -> str | None:
    if shutil.which("modinfo") is None:
        return None
    completed = subprocess.run(
        ["modinfo", "-F", "vermagic", "gs_usb"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    fields = completed.stdout.split()
    return fields[0] if fields else None


def _vendor_source_exists(relative_path: str, workspace_root: Path | None) -> bool:
    if workspace_root is None:
        return False
    return (workspace_root / "src" / relative_path).exists()


def _ros_package_exists(package_name: str) -> bool:
    try:
        get_package_share_directory(package_name)
    except PackageNotFoundError:
        return False
    return True


def collect_preflight_diagnostics(
    config: dict[str, Any],
    *,
    start_camera: bool,
    start_scout: bool,
    start_livox: bool,
    start_fast_lio: bool,
    start_teleop: bool = False,
    start_inspection: bool = False,
    start_route: bool = False,
    workspace_root: Path | None = None,
) -> list[Diagnostic]:
    """Collect non-fatal diagnostics for enabled optional components."""
    diagnostics: list[Diagnostic] = []
    bringup = config.get("scout_bringup", {})
    camera = config.get("hik_camera_node", {})

    sensor_interface = str(bringup.get("sensor_interface", ""))
    if start_camera or start_livox:
        if sensor_interface and not _interface_exists(sensor_interface):
            diagnostics.append(
                Diagnostic(
                    "warn",
                    "NET_IFACE_MISSING",
                    f"sensor interface '{sensor_interface}' not found; "
                    "camera/lidar connectivity checks may fail",
                )
            )
        elif sensor_interface:
            host_ip = str(bringup.get("host_ip", ""))
            if host_ip and not _interface_has_ip(sensor_interface, host_ip):
                diagnostics.append(
                    Diagnostic(
                        "warn",
                        "NET_HOST_IP_MISSING",
                        f"sensor interface '{sensor_interface}' does not have configured "
                        f"host IP {host_ip}",
                    )
                )

    if start_camera:
        camera_ip = str(camera.get("camera_ip", ""))
        if camera_ip and not _ping_host(camera_ip):
            diagnostics.append(
                Diagnostic(
                    "warn",
                    "CAMERA_UNREACHABLE",
                    f"camera at {camera_ip} did not respond to ping; "
                    "hik_camera_node will start but cannot capture frames yet",
                )
            )

    if start_livox:
        lidar_ip = str(bringup.get("lidar_ip", ""))
        if lidar_ip and not _ping_host(lidar_ip):
            diagnostics.append(
                Diagnostic(
                    "warn",
                    "LIDAR_UNREACHABLE",
                    f"lidar at {lidar_ip} did not respond to ping; "
                    "livox driver is not wired in P0 and will remain disabled",
                )
            )

    if start_scout:
        can_interface = str(bringup.get("can_interface", ""))
        expected_bitrate = int(bringup.get("can_bitrate", 0))
        expected_restart_ms = int(bringup.get("can_restart_ms", -1))
        kernel_release = _running_kernel_release()
        module_release = _gs_usb_module_release()
        if module_release is None:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "CAN_MODULE_MISSING",
                    "gs_usb is not installed for the running kernel; rebuild and install "
                    "the verified module before starting SCOUT",
                )
            )
        elif module_release != kernel_release:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "CAN_MODULE_MISMATCH",
                    f"gs_usb vermagic release {module_release} does not match "
                    f"running kernel {kernel_release}",
                )
            )

        can_status = _inspect_can_interface(can_interface)
        if not can_status.exists:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "CAN_IFACE_MISSING",
                    f"CAN interface '{can_interface}' not found; run the stable CAN "
                    "installer and reconnect the adapter",
                )
            )
        elif not can_status.is_can:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "CAN_IFACE_NOT_SOCKETCAN",
                    f"interface '{can_interface}' is not a SocketCAN device",
                )
            )
        elif not can_status.is_up:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "CAN_IFACE_DOWN",
                    f"CAN interface '{can_interface}' is not up",
                )
            )
        elif can_status.bitrate != expected_bitrate:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "CAN_BITRATE_MISMATCH",
                    f"CAN interface '{can_interface}' bitrate is {can_status.bitrate}; "
                    f"expected {expected_bitrate}",
                )
            )
        elif can_status.restart_ms != expected_restart_ms:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "CAN_RESTART_MISMATCH",
                    f"CAN interface '{can_interface}' restart-ms is "
                    f"{can_status.restart_ms}; expected {expected_restart_ms}",
                )
            )

    switches = {
        "start_scout": start_scout,
        "start_livox": start_livox,
        "start_fast_lio": start_fast_lio,
    }
    for switch_name, enabled in switches.items():
        if not enabled:
            continue
        package_name, source_path = VENDOR_PACKAGES[switch_name]
        if _ros_package_exists(package_name):
            continue
        if _vendor_source_exists(source_path, workspace_root):
            diagnostics.append(
                Diagnostic(
                    "warn",
                    "VENDOR_NOT_BUILT",
                    f"{switch_name}=true but ROS package '{package_name}' is not installed; "
                    f"run colcon build after importing {source_path}",
                )
            )
        else:
            diagnostics.append(
                Diagnostic(
                    "warn",
                    "VENDOR_SOURCE_MISSING",
                    f"{switch_name}=true but source '{source_path}' is missing; "
                    "run: vcs import src < third_party.repos && ./scripts/prepare_vendor.sh",
                )
            )

    if start_teleop:
        missing_teleop_packages = [
            package
            for package in ("joy", "teleop_twist_joy")
            if not _ros_package_exists(package)
        ]
        if missing_teleop_packages:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "TELEOP_PACKAGE_MISSING",
                    "start_teleop=true but required ROS package(s) are missing: "
                    + ", ".join(missing_teleop_packages),
                )
            )

    if start_inspection and not _ros_package_exists("rosbag2_storage_mcap"):
        diagnostics.append(
            Diagnostic(
                "error",
                "MCAP_STORAGE_MISSING",
                "inspection recording requires ros-humble-rosbag2-storage-mcap; "
                "run ./scripts/install_deps.sh before start_inspection=true",
            )
        )

    if start_route:
        missing_route_packages = [
            package for package in ROUTE_PACKAGES if not _ros_package_exists(package)
        ]
        if missing_route_packages:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "NAV2_ROUTE_PACKAGE_MISSING",
                    "start_route=true but required ROS package(s) are missing: "
                    + ", ".join(missing_route_packages)
                    + "; run ./scripts/install_deps.sh",
                )
            )
        route_config = config.get("route_orchestrator", {})
        if not route_config.get("controller_tuning_verified", False):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "ROUTE_CONTROLLER_UNVERIFIED",
                    "route_orchestrator.controller_tuning_verified=false; complete "
                    "onsite tracking and fault-injection acceptance first",
                )
            )
        route_path_value = route_config.get("fixed_route_path", "")
        if not isinstance(route_path_value, str) or not route_path_value:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "FIXED_ROUTE_MISSING",
                    "start_route=true requires an absolute fixed_route_path",
                )
            )
        else:
            try:
                from inspection_pipeline.fixed_route import load_fixed_route

                route = load_fixed_route(Path(route_path_value))
            except (ImportError, ValueError) as exc:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "FIXED_ROUTE_INVALID",
                        f"fixed route cannot authorize onsite motion: {exc}",
                    )
                )
            else:
                safety_limit = config.get("safety_mux", {}).get("max_linear_mps")
                if (
                    isinstance(safety_limit, (int, float))
                    and not isinstance(safety_limit, bool)
                    and route.approved_max_linear_mps > float(safety_limit)
                ):
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "FIXED_ROUTE_SPEED_MISMATCH",
                            "fixed route approved speed exceeds safety_mux.max_linear_mps",
                        )
                    )

    if not bringup.get("extrinsics_verified", False):
        diagnostics.append(
            Diagnostic(
                "warn",
                "EXTRINSICS_UNVERIFIED",
                "scout_bringup.extrinsics_verified=false; static TF uses placeholder values",
            )
        )

    return diagnostics


def collect_launch_diagnostics(
    config_path: Path,
    launch_arguments: Iterable[tuple[str, str]],
    *,
    workspace_root: Path | None = None,
) -> tuple[list[str], list[Diagnostic]]:
    """Validate config and collect launch diagnostics."""
    from scout_bringup.config_validator import load_config, validate_config

    errors: list[str] = []
    try:
        config = load_config(config_path)
    except (OSError, ValueError) as exc:
        return [f"failed to load config {config_path}: {exc}"], []

    errors.extend(validate_config(config))

    args = {key: value for key, value in launch_arguments}
    start_safety = _parse_bool(args.get("start_safety", "true"))
    start_scout = _parse_bool(args.get("start_scout", "false"))
    start_teleop = _parse_bool(args.get("start_teleop", "false"))
    start_route = _parse_bool(args.get("start_route", "false"))
    if start_teleop and not start_safety:
        errors.append("start_teleop=true requires start_safety=true")
    if start_scout and not start_safety:
        errors.append("start_scout=true requires start_safety=true")
    if start_route:
        route_requirements = {
            "start_safety": start_safety,
            "start_scout": start_scout,
            "start_livox": _parse_bool(args.get("start_livox", "false")),
            "start_fast_lio": _parse_bool(args.get("start_fast_lio", "false")),
            "start_collision": _parse_bool(args.get("start_collision", "false")),
            "start_teleop": start_teleop,
            "start_inspection": _parse_bool(args.get("start_inspection", "false")),
            "start_camera": _parse_bool(args.get("start_camera", "true")),
        }
        for requirement, enabled in route_requirements.items():
            if not enabled:
                errors.append(f"start_route=true requires {requirement}=true")
        if not config.get("scout_bringup", {}).get("extrinsics_verified", False):
            errors.append("start_route=true requires scout_bringup.extrinsics_verified=true")
        if config.get("inspection_manager", {}).get("run_mode") != "production":
            errors.append(
                "start_route=true requires inspection_manager.run_mode=production"
            )
    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=_parse_bool(args.get("start_camera", "true")),
        start_scout=start_scout,
        start_livox=_parse_bool(args.get("start_livox", "false")),
        start_fast_lio=_parse_bool(args.get("start_fast_lio", "false")),
        start_teleop=start_teleop,
        start_inspection=_parse_bool(args.get("start_inspection", "false")),
        start_route=start_route,
        workspace_root=workspace_root,
    )
    errors.extend(item.format() for item in diagnostics if item.level == "error")
    return errors, diagnostics


def main() -> None:
    """CLI entry point for preflight checks."""
    import argparse
    import sys

    from scout_bringup.config_validator import validate_config_file

    parser = argparse.ArgumentParser(description="Run SCOUT Mini preflight checks")
    parser.add_argument("config", type=Path, help="Path to system.yaml")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--start-camera", default="true")
    parser.add_argument("--start-scout", default="false")
    parser.add_argument("--start-livox", default="false")
    parser.add_argument("--start-fast-lio", default="false")
    parser.add_argument("--start-teleop", default="false")
    parser.add_argument("--start-inspection", default="true")
    parser.add_argument("--start-route", default="false")
    args = parser.parse_args()

    config_errors = validate_config_file(args.config)
    if config_errors:
        for item in config_errors:
            print(f"ERROR {item}", file=sys.stderr)
        raise SystemExit(1)

    from scout_bringup.config_validator import load_config

    config = load_config(args.config)
    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=_parse_bool(args.start_camera),
        start_scout=_parse_bool(args.start_scout),
        start_livox=_parse_bool(args.start_livox),
        start_fast_lio=_parse_bool(args.start_fast_lio),
        start_teleop=_parse_bool(args.start_teleop),
        start_inspection=_parse_bool(args.start_inspection),
        start_route=_parse_bool(args.start_route),
        workspace_root=args.workspace_root,
    )
    for item in diagnostics:
        stream = sys.stderr if item.level == "error" else sys.stdout
        print(item.format(), file=stream)

    if any(item.level == "error" for item in diagnostics):
        raise SystemExit(1)

    print(f"OK   preflight: {args.config}")


if __name__ == "__main__":
    main()

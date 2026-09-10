"""Run lifecycle, rosbag recording and manifest management."""

from __future__ import annotations

import signal
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Optional

import rclpy
import rosbag2_py
from ament_index_python.packages import get_package_share_directory
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from inspection_pipeline.bag_health import BagHealthError, BagSummary, inspect_bag
from inspection_pipeline.config_snapshot import ConfigSnapshotError, snapshot_run_config
from inspection_pipeline.recording import (
    RecordingConfigError,
    RecordingSettings,
    StorageHealth,
    load_recording_settings,
    preflight_storage,
    scan_recorder_log,
    snapshot_recording_settings,
    storage_health,
)
from inspection_pipeline.run_manifest import (
    build_manifest,
    now_utc,
    route_status_matches_snapshot,
    stop_outcome_from_route,
    update_route_execution,
    write_manifest,
)
from rclpy.node import Node
from std_srvs.srv import Trigger


def storage_writer_available(storage_id: str) -> bool:
    """Return whether rosbag2 has a writer plugin for the configured storage."""
    return storage_id in rosbag2_py.get_registered_writers()


def camera_diagnostic_is_ready(
    status: DiagnosticStatus,
    expected_name: str,
) -> bool:
    """Return whether one camera diagnostic proves calibrated image output."""
    if status.name != expected_name or status.level != DiagnosticStatus.OK:
        return False
    values = {item.key: item.value for item in status.values}
    return (
        values.get("connected") == "true"
        and values.get("calibration_state") == "ready"
        and values.get("camera_info_valid") == "true"
    )


class InspectionManager(Node):
    """Manage inspection runs, rosbag recording and manifest output."""

    def __init__(self) -> None:
        super().__init__("inspection_manager")
        self.declare_parameter("output_root", "/data/scout_runs")
        self.declare_parameter("storage_id", "mcap")
        self.declare_parameter("config_path", "")
        self.declare_parameter("recording_config", "recording.yaml")
        self.declare_parameter("run_mode", "commissioning")
        self.declare_parameter("status_topic", "/inspection/status")
        self.declare_parameter("route_status_topic", "/route/status")
        self.declare_parameter("diagnostics_topic", "/diagnostics")
        self.declare_parameter("camera_diagnostic_name", "hik_camera_ros2")
        self.declare_parameter("camera_diagnostic_timeout_ms", 2000)
        self.declare_parameter("calibration_target_url", "")
        self.declare_parameter("calibration_profile_url", "")

        self._output_root = Path(self.get_parameter("output_root").value)
        self._storage_id = self.get_parameter("storage_id").value
        self._status_topic = self.get_parameter("status_topic").value
        self._run_mode = str(self.get_parameter("run_mode").value)
        if self._run_mode not in {"commissioning", "production"}:
            raise RecordingConfigError(
                "inspection run_mode must be commissioning or production"
            )
        self._camera_diagnostic_name = str(
            self.get_parameter("camera_diagnostic_name").value
        )
        camera_timeout_ms = self.get_parameter("camera_diagnostic_timeout_ms").value
        if (
            not isinstance(camera_timeout_ms, int)
            or isinstance(camera_timeout_ms, bool)
            or camera_timeout_ms <= 0
        ):
            raise RecordingConfigError(
                "camera_diagnostic_timeout_ms must be a positive integer"
            )
        self._camera_diagnostic_timeout_ns = camera_timeout_ms * 1_000_000
        self._camera_diagnostic_received_ns = 0
        self._camera_diagnostic_ready = False
        self._recording = self._load_recording_settings()
        if self._storage_id != self._recording.storage_id:
            raise RecordingConfigError(
                "inspection storage_id contradicts recording config storage_id"
            )
        self._run_id: Optional[str] = None
        self._run_dir: Optional[Path] = None
        self._last_run_id: Optional[str] = None
        self._last_run_dir: Optional[Path] = None
        self._started_at: Optional[datetime] = None
        self._status = "idle"
        self._exit_reason: Optional[str] = None
        self._camera_calibration: Optional[dict[str, Any]] = None
        self._fixed_route: Optional[dict[str, Any]] = None
        self._route_execution: Optional[dict[str, Any]] = None
        self._route_attempts = 0
        self._run_started_ns = 0
        self._record_process: Optional[subprocess.Popen[Any]] = None
        self._record_log: Optional[BinaryIO] = None
        self._recording_reference: Optional[dict[str, Any]] = None
        self._bag_summary: Optional[BagSummary] = None
        self._record_log_offset = 0
        self._storage_health = StorageHealth(free_bytes=0, run_size_bytes=0)
        self._config_path = self._resolve_config_path()

        self._status_pub = self.create_publisher(DiagnosticArray, self._status_topic, 10)
        self.create_subscription(
            DiagnosticArray,
            str(self.get_parameter("route_status_topic").value),
            self._on_route_status,
            10,
        )
        self.create_subscription(
            DiagnosticArray,
            str(self.get_parameter("diagnostics_topic").value),
            self._on_diagnostics,
            10,
        )
        self.create_service(Trigger, "inspection/start", self._start_run)
        self.create_service(Trigger, "inspection/stop", self._stop_run)
        self.create_timer(self._recording.monitor_period_seconds, self._publish_status)

    def _resolve_config_path(self) -> Path:
        configured = self.get_parameter("config_path").value
        if configured:
            return Path(configured)
        share = Path(get_package_share_directory("scout_bringup"))
        return share / "config" / "system.yaml"

    def _load_recording_settings(self) -> RecordingSettings:
        recording_name = self.get_parameter("recording_config").value
        if not isinstance(recording_name, str) or Path(recording_name).name != recording_name:
            raise RecordingConfigError("recording_config must be a filename")
        share = Path(get_package_share_directory("inspection_pipeline"))
        # colcon --symlink-install intentionally exposes package data as links;
        # resolve that trusted package entry before applying the runtime policy's
        # own no-symlink checks to per-run snapshots.
        recording_path = (share / "config" / recording_name).resolve()
        return load_recording_settings(recording_path)

    def _start_run(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        del request
        if self._run_id is not None:
            response.success = False
            response.message = f"run already active: {self._run_id}"
            return response
        if self._run_mode == "production" and not self._camera_ready():
            response.success = False
            response.message = (
                "production run requires a fresh camera diagnostic with "
                "calibration_state=ready and camera_info_valid=true"
            )
            self.get_logger().error(response.message)
            return response
        if not storage_writer_available(str(self._storage_id)):
            response.success = False
            response.message = (
                f"rosbag2 storage writer is unavailable: {self._storage_id}; "
                "install ros-humble-rosbag2-storage-mcap"
            )
            self.get_logger().error(response.message)
            return response
        try:
            self._storage_health = preflight_storage(self._output_root)
            free_bytes = self._storage_health.free_bytes
        except OSError as exc:
            response.success = False
            response.message = f"recording output is unavailable: {exc}"
            self.get_logger().error(response.message)
            return response
        if free_bytes < self._recording.min_start_free_space_bytes:
            response.success = False
            response.message = (
                f"insufficient recording space: free={free_bytes} "
                f"required={self._recording.min_start_free_space_bytes}"
            )
            self.get_logger().error(response.message)
            return response

        self._run_id = uuid.uuid4().hex[:12]
        self._started_at = now_utc()
        self._recording_reference = None
        self._bag_summary = None
        self._record_log_offset = 0
        self._run_started_ns = int(self._started_at.timestamp() * 1_000_000_000)
        self._route_execution = None
        self._route_attempts = 0
        stamp = self._started_at.strftime("%Y%m%dT%H%M%SZ")
        self._run_dir = self._output_root / f"{stamp}_{self._run_id}"
        self._run_dir.mkdir(parents=True, exist_ok=True)
        (self._run_dir / "bag").mkdir(exist_ok=True)
        (self._run_dir / "artifacts").mkdir(exist_ok=True)
        (self._run_dir / "replay").mkdir(exist_ok=True)
        (self._run_dir / "recording").mkdir(exist_ok=True)
        config_dir = self._run_dir / "config"
        config_dir.mkdir(exist_ok=True)

        try:
            snapshot = snapshot_run_config(self._config_path, config_dir)
            self._recording_reference = snapshot_recording_settings(
                self._recording,
                config_dir,
            )
            self._camera_calibration = snapshot.camera_calibration
            self._fixed_route = snapshot.fixed_route
            if self._run_mode == "production":
                if self._camera_calibration is None:
                    raise ConfigSnapshotError(
                        "production run requires versioned camera calibration provenance"
                    )
                if not snapshot.extrinsics_verified:
                    raise ConfigSnapshotError(
                        "production run requires verified installation extrinsics"
                    )
        except (ConfigSnapshotError, OSError) as exc:
            failed_dir = self._run_dir
            shutil.rmtree(failed_dir)
            self._run_id = None
            self._run_dir = None
            self._started_at = None
            self._camera_calibration = None
            self._fixed_route = None
            self._route_execution = None
            self._route_attempts = 0
            self._run_started_ns = 0
            self._recording_reference = None
            response.success = False
            response.message = f"cannot snapshot run config: {exc}"
            self.get_logger().error(response.message)
            return response

        bag_path = self._run_dir / "bag" / self._run_id
        command = self._recording.recorder_command(
            bag_path,
            storage_config_path=config_dir / self._recording.storage_config_path.name,
        )
        log_path = self._run_dir / "recording/rosbag.log"
        try:
            self._record_log = log_path.open("ab", buffering=0)
            self._record_process = subprocess.Popen(
                command,
                stdout=self._record_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            if self._record_log is not None:
                self._record_log.close()
                self._record_log = None
            self._status = "failed"
            self._exit_reason = f"rosbag_start_failed:{type(exc).__name__}"
            self._write_manifest()
            failed_run = self._run_dir
            self._reset_active_run()
            response.success = False
            response.message = f"cannot start rosbag recorder; retained {failed_run}: {exc}"
            self.get_logger().error(response.message)
            return response
        self._status = "running"
        self._exit_reason = None
        self._storage_health = StorageHealth(
            free_bytes=free_bytes,
            run_size_bytes=0,
        )
        self._write_manifest()

        response.success = True
        response.message = f"started run {self._run_id} at {self._run_dir}"
        self.get_logger().info(response.message)
        return response

    def _camera_ready(self, now_ns: Optional[int] = None) -> bool:
        now = time.monotonic_ns() if now_ns is None else now_ns
        return (
            self._camera_diagnostic_ready
            and self._camera_diagnostic_received_ns > 0
            and now - self._camera_diagnostic_received_ns
            <= self._camera_diagnostic_timeout_ns
        )

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        for status in message.status:
            if status.name != self._camera_diagnostic_name:
                continue
            self._camera_diagnostic_received_ns = time.monotonic_ns()
            self._camera_diagnostic_ready = camera_diagnostic_is_ready(
                status,
                self._camera_diagnostic_name,
            )
            return

    def _stop_run(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        del request
        if self._run_id is None:
            response.success = False
            response.message = "no active run"
            return response

        run_id = self._run_id
        status, exit_reason = stop_outcome_from_route(
            self._route_execution,
            route_required=self._fixed_route is not None,
        )
        self._finalize_run(status=status, exit_reason=exit_reason)
        response.success = True
        response.message = f"stopped run {run_id}"
        return response

    def _publish_status(self) -> None:
        self._check_recording_health()
        status = DiagnosticStatus()
        status.name = "inspection_pipeline"
        status.hardware_id = self._run_id or self._last_run_id or "none"
        if self._status in {"running", "completed", "completed_with_exceptions"}:
            status.level = DiagnosticStatus.OK
        elif self._status == "idle":
            status.level = DiagnosticStatus.STALE
        else:
            status.level = DiagnosticStatus.ERROR
        status.message = self._status
        status.values = [
            KeyValue(key="run_mode", value=self._run_mode),
            KeyValue(key="camera_calibration_ready", value=str(self._camera_ready()).lower()),
            KeyValue(key="run_id", value=self._run_id or self._last_run_id or ""),
            KeyValue(key="run_dir", value=str(self._run_dir or self._last_run_dir or "")),
            KeyValue(key="exit_reason", value=self._exit_reason or ""),
            KeyValue(key="disk_free_bytes", value=str(self._storage_health.free_bytes)),
            KeyValue(key="run_size_bytes", value=str(self._storage_health.run_size_bytes)),
            KeyValue(
                key="min_runtime_free_space_bytes",
                value=str(self._recording.min_runtime_free_space_bytes),
            ),
            KeyValue(
                key="max_run_size_bytes",
                value=str(self._recording.max_run_size_bytes),
            ),
            KeyValue(
                key="recorder_exit_code",
                value=str(
                    ""
                    if self._recording_reference is None
                    else self._recording_reference.get("recorder_exit_code", "")
                ),
            ),
        ]

        message = DiagnosticArray()
        message.header.stamp = self.get_clock().now().to_msg()
        message.status = [status]
        self._status_pub.publish(message)

    def _check_recording_health(self) -> None:
        if self._run_id is None or self._run_dir is None:
            return
        if self._record_process is None:
            self._finalize_run(status="incomplete", exit_reason="rosbag_process_missing")
            return
        return_code = self._record_process.poll()
        if return_code is not None:
            self._finalize_run(
                status="incomplete",
                exit_reason=f"rosbag_unexpected_exit_{return_code}",
            )
            return
        try:
            self._storage_health = storage_health(
                self._output_root,
                self._run_dir,
            )
        except OSError as exc:
            self.get_logger().error(f"cannot inspect recording storage: {exc}")
            self._finalize_run(
                status="incomplete",
                exit_reason=f"storage_health_failed:{type(exc).__name__}",
            )
            return
        log_path = self._run_dir / "recording/rosbag.log"
        try:
            self._record_log_offset, log_reason = scan_recorder_log(
                log_path,
                self._record_log_offset,
            )
        except OSError as exc:
            self.get_logger().error(f"cannot inspect rosbag log: {exc}")
            self._finalize_run(
                status="incomplete",
                exit_reason=f"recorder_log_read_failed:{type(exc).__name__}",
            )
            return
        if log_reason is not None:
            self._finalize_run(status="incomplete", exit_reason=log_reason)
            return
        if self._storage_health.free_bytes < self._recording.min_runtime_free_space_bytes:
            self._finalize_run(
                status="incomplete",
                exit_reason="runtime_free_space_limit",
            )
        elif self._storage_health.run_size_bytes >= self._recording.max_run_size_bytes:
            self._finalize_run(
                status="incomplete",
                exit_reason="run_size_limit",
            )

    def _finalize_run(self, *, status: str, exit_reason: str) -> None:
        if self._record_process is not None:
            if self._record_process.poll() is None:
                self._record_process.send_signal(signal.SIGINT)
                try:
                    self._record_process.wait(timeout=self._recording.stop_timeout_seconds)
                except subprocess.TimeoutExpired:
                    self._record_process.kill()
                    self._record_process.wait(timeout=5)
                    status = "incomplete"
                    exit_reason = "rosbag_stop_timeout"
            return_code = self._record_process.returncode
            if self._recording_reference is not None:
                self._recording_reference["recorder_exit_code"] = return_code
            if return_code != 0:
                status = "incomplete"
                if not exit_reason.startswith("rosbag_unexpected_exit_"):
                    exit_reason = f"rosbag_exit_{return_code}"
            self._record_process = None
        if self._record_log is not None:
            self._record_log.close()
            self._record_log = None
        if self._run_dir is not None:
            try:
                self._record_log_offset, log_reason = scan_recorder_log(
                    self._run_dir / "recording/rosbag.log",
                    self._record_log_offset,
                )
            except OSError as exc:
                status = "incomplete"
                exit_reason = f"recorder_log_read_failed:{type(exc).__name__}"
            else:
                if log_reason is not None:
                    status = "incomplete"
                    exit_reason = log_reason

        if self._run_dir is not None and self._run_id is not None:
            try:
                self._bag_summary = inspect_bag(
                    self._run_dir / "bag" / self._run_id,
                    storage_id=str(self._storage_id),
                    required_topics=self._recording.required_topics,
                )
            except BagHealthError as exc:
                self.get_logger().error(f"recorded bag validation failed: {exc}")
                if status in {"completed", "completed_with_exceptions"}:
                    status = "incomplete"
                    exit_reason = f"rosbag_invalid:{exc}"

        if self._run_dir is not None:
            try:
                self._storage_health = storage_health(
                    self._output_root,
                    self._run_dir,
                )
            except OSError as exc:
                self.get_logger().error(f"cannot measure finalized run: {exc}")

        self._status = status
        self._exit_reason = exit_reason
        self._write_manifest()
        self._reset_active_run()

    def _reset_active_run(self) -> None:
        self._last_run_id = self._run_id
        self._last_run_dir = self._run_dir
        self._run_id = None
        self._run_dir = None
        self._started_at = None
        self._camera_calibration = None
        self._fixed_route = None
        self._route_execution = None
        self._route_attempts = 0
        self._run_started_ns = 0

    def _on_route_status(self, message: DiagnosticArray) -> None:
        if self._run_id is None or self._started_at is None:
            return
        for status in message.status:
            if status.name != "inspection_pipeline/route_orchestrator":
                continue
            values = {item.key: item.value for item in status.values}
            if not route_status_matches_snapshot(
                self._fixed_route,
                route_id=values.get("route_id", ""),
                route_sha256=values.get("route_sha256", ""),
            ):
                return
            execution_id = values.get("execution_id", "")
            try:
                execution_started_ns = int(values.get("execution_started_ns", ""))
            except ValueError:
                return
            self._route_execution, self._route_attempts = update_route_execution(
                self._route_execution,
                self._route_attempts,
                run_started_ns=self._run_started_ns,
                execution_id=execution_id,
                execution_started_ns=execution_started_ns,
                state=values.get("route_state", "unknown"),
                reason=values.get("reason", status.message),
            )
            return

    def _write_manifest(self) -> None:
        self._write_manifest_body()

    def _write_manifest_body(self) -> None:
        if self._run_dir is None or self._run_id is None or self._started_at is None:
            return

        ended_at = None if self._status == "running" else now_utc()
        manifest = build_manifest(
            run_id=self._run_id,
            status=self._status,
            started_at=self._started_at,
            ended_at=ended_at,
            config_path=self._config_path,
            run_dir=self._run_dir,
            storage_id=self._storage_id,
            exit_reason=self._exit_reason,
            run_mode=self._run_mode,
            camera_calibration=self._camera_calibration,
            fixed_route=self._fixed_route,
            route_execution=self._route_execution,
            recording=self._recording_reference,
            bag_summary=(
                None if self._bag_summary is None else self._bag_summary.manifest_fields()
            ),
        )
        write_manifest(self._run_dir / "manifest.json", manifest)

    def destroy_node(self) -> bool:
        if self._run_id is not None:
            self._finalize_run(status="incomplete", exit_reason="node_shutdown")
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = InspectionManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

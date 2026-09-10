"""Real MCAP to station evidence to durable BagIt integration tests."""

import hashlib
import json
import shutil

import pytest
import rosbag2_py
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.serialization import serialize_message
from std_msgs.msg import String

from inspection_pipeline.bag_health import inspect_bag
from inspection_pipeline.bagit import verify_bagit_bundle
from inspection_pipeline.bundle_export import BundleExportError, export_run_bundle
from inspection_pipeline.run_validation import RunValidationError, validate_run_directory
from inspection_pipeline.station_attempts import StationEvidenceError
from inspection_pipeline.station_attempts_export import (
    export_station_attempts, validate_station_attempts_artifact,
)
from test_bundle_export import add_verified_fixed_route, create_finalized_run
from test_bundle_export import fake_pinned_mcap_cli  # noqa: F401


def station_run(tmp_path):
    """Create synthetic diagnostics in a real MCAP using the shared run fixture."""
    run = create_finalized_run(tmp_path)
    add_verified_fixed_route(run)
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    bag = run / "bag" / manifest["run_id"]
    shutil.rmtree(bag)
    writer = rosbag2_py.SequentialWriter()
    writer.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="mcap"),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    for name, msg_type in [("/test", "std_msgs/msg/String"),
                           ("/route/status", "diagnostic_msgs/msg/DiagnosticArray")]:
        writer.create_topic(rosbag2_py.TopicMetadata(
            name=name, type=msg_type, serialization_format="cdr", offered_qos_profiles="",
        ))
    start = manifest["route_execution"]["execution_started_ns"]
    writer.write("/test", serialize_message(String(data="fixture")), start)
    for offset, state, outcomes in [
        (100_000_000, "arming", []),
        (1_000_000_000, "completed", [{"station_id": "station-001", "outcome": "completed"}]),
    ]:
        message = DiagnosticArray()
        stamp = start + offset
        message.header.stamp.sec, message.header.stamp.nanosec = divmod(stamp, 1_000_000_000)
        values = {
            "route_id": "route-a-v1", "route_sha256": manifest["fixed_route"]["sha256"],
            "execution_id": "routeexec001", "execution_started_ns": str(start),
            "route_state": state, "station_id": "station-001" if not outcomes else "",
            "station_outcomes": json.dumps(outcomes),
        }
        status = DiagnosticStatus(name="inspection_pipeline/route_orchestrator")
        status.values = [KeyValue(key=k, value=v) for k, v in values.items()]
        message.status = [status]
        writer.write("/route/status", serialize_message(message), stamp)
    del writer
    manifest["bag"].update(inspect_bag(bag).manifest_fields())
    manifest_path.write_text(json.dumps(manifest))
    return run


def test_recorded_station_attempts_are_indexed_validated_and_delivered(tmp_path):
    run = station_run(tmp_path)
    artifact = export_station_attempts(run)
    before = artifact.read_bytes()
    assert export_station_attempts(run).read_bytes() == before
    document = json.loads(before)
    assert document["attempts"][0]["result"] == "success"
    assert document["attempts"][0]["station_id"] == "station-001"
    assert validate_station_attempts_artifact(run) == "artifacts/stations/attempts.json"
    usb = tmp_path / "usb"
    usb.mkdir()
    bundle = export_run_bundle(run, usb, scene_id="scene-fixture",
                               alignment_id="alignment-fixture-v1", reserve_free_bytes=0)
    verify_bagit_bundle(bundle)
    assert (bundle / "data/artifacts/stations/attempts.json").read_bytes() == before
    semantic = json.loads((bundle / "data/manifest.json").read_text())
    assert {"path": "artifacts/stations/attempts.json", "kind": "inspection_result",
            "display": False} in semantic["artifacts"]


def test_tampered_station_result_cannot_be_exported(tmp_path):
    run = station_run(tmp_path)
    artifact = export_station_attempts(run)
    document = json.loads(artifact.read_text())
    document["attempts"][0]["station_id"] = "foreign"
    artifact.write_text(json.dumps(document))
    with pytest.raises(StationEvidenceError):
        validate_station_attempts_artifact(run)
    usb = tmp_path / "usb"
    usb.mkdir()
    with pytest.raises(BundleExportError, match="station"):
        export_run_bundle(run, usb, scene_id="scene-fixture",
                          alignment_id="alignment-fixture-v1", reserve_free_bytes=0)
    assert list(usb.iterdir()) == []


@pytest.mark.parametrize("mutation", [
    "rehash_result", "source_config", "missing_index", "missing_file",
])
def test_recording_only_validation_rejects_broken_station_provenance(tmp_path, mutation):
    run = station_run(tmp_path)
    artifact = export_station_attempts(run)
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "rehash_result":
        document = json.loads(artifact.read_text())
        document["attempts"][0]["mcap_range"]["start_ns"] += 1
        artifact.write_text(json.dumps(document))
        manifest["station_attempts"]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
    elif mutation == "source_config":
        with (run / "config/system.yaml").open("a") as stream:
            stream.write("\n# changed source\n")
    elif mutation == "missing_index":
        del manifest["station_attempts"]
        manifest_path.write_text(json.dumps(manifest))
    else:
        artifact.unlink()
    with pytest.raises(StationEvidenceError):
        validate_station_attempts_artifact(run)
    with pytest.raises(RunValidationError, match="station"):
        validate_run_directory(run, recording_only=True)


def test_orphaned_artifact_is_recovered_without_rewriting_evidence(tmp_path):
    run = station_run(tmp_path)
    artifact = export_station_attempts(run)
    before = artifact.read_bytes()
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["station_attempts"]
    manifest_path.write_text(json.dumps(manifest))
    export_station_attempts(run)
    assert artifact.read_bytes() == before
    assert validate_station_attempts_artifact(run)


@pytest.mark.parametrize("field,value", [
    ("execution_id", "unrecorded-execution"),
    ("execution_started_ns", 1_787_472_000_000_000_001),
    ("state", "paused"),
    ("attempts", 2),
])
def test_manifest_cannot_relabel_recorded_route_execution(tmp_path, field, value):
    run = station_run(tmp_path)
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["route_execution"][field] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(StationEvidenceError):
        export_station_attempts(run)
    assert not (run / "artifacts/stations/attempts.json").exists()


def test_explicit_acceptance_gate_rejects_legacy_run_without_station_evidence(tmp_path):
    run = station_run(tmp_path)
    with pytest.raises(RunValidationError, match="station"):
        validate_run_directory(run, recording_only=True, require_station_attempts=True)
    usb = tmp_path / "usb"
    usb.mkdir()
    with pytest.raises(BundleExportError, match="station"):
        export_run_bundle(run, usb, scene_id="scene-fixture",
                          alignment_id="alignment-fixture-v1", reserve_free_bytes=0,
                          require_station_attempts=True)
    export_station_attempts(run)
    report = validate_run_directory(run, recording_only=True, require_station_attempts=True)
    assert report["checks"]["station_attempts"] is True


def test_export_does_not_accept_a_corrupted_generation_timestamp(tmp_path):
    run = station_run(tmp_path)
    artifact = export_station_attempts(run)
    document = json.loads(artifact.read_text())
    document["generated_at"] = "yesterday"
    artifact.write_text(json.dumps(document))
    before = artifact.read_bytes()
    with pytest.raises(StationEvidenceError):
        export_station_attempts(run)
    assert artifact.read_bytes() == before

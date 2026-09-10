"""Formal Inspection Package v2 contract, validator, and golden-fixture seams."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import get_args

import bagit
import jsonschema
import pytest

from app.inspection_package import (
    InspectionPackageManifest,
    InspectionPackageTrajectory,
    canonical_package_sha256,
    percentile_linear,
    validate_inspection_package,
)
from app.run_bundle import BundleImportError, import_inbox_directory, import_run_bundle
from app.scene_registration import register_scene_from_file

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPLETED = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed"
EXCEPTIONS = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed-with-exceptions"
V1_FIXTURE = REPO_ROOT / "data/contract-fixtures/scout-run-bagit-v1"
REGISTRATION = REPO_ROOT / "data/contract-fixtures/scene-registration-scene-001-v1.json"
SCHEMA_PATH = REPO_ROOT / "packages/contracts/inspection-package-v2.schema.json"
V1_SCHEMA_PATH = REPO_ROOT / "packages/contracts/run-bundle.schema.json"
OPENAPI_PATH = REPO_ROOT / "packages/contracts/openapi.yaml"
TS_PATH = REPO_ROOT / "packages/contracts/src/inspection-package.ts"


def _schema_validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    return validator, schema


def _validate_schema_docs(manifest: dict, trajectory: dict) -> None:
    validator, schema = _schema_validator()
    validator.evolve(schema=schema["$defs"]["Manifest"]).validate(manifest)
    validator.evolve(schema=schema["$defs"]["Trajectory"]).validate(trajectory)


def _load_package_json(root: Path) -> tuple[dict, dict]:
    manifest = json.loads((root / "data/manifest.json").read_text(encoding="utf-8"))
    trajectory = json.loads((root / "data/replay/trajectory.json").read_text(encoding="utf-8"))
    return manifest, trajectory


def _copy_package(tmp_path: Path, source: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(source, target)
    return target


def _save_bag(path: Path) -> None:
    bag = bagit.Bag(str(path))
    bag.save(manifests=True)
    bag.validate(processes=1)


def _refresh_inventory(root: Path) -> None:
    data_root = root / "data"
    entries = []
    for path in sorted(data_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(data_root).as_posix()
        if relative == "manifest.json":
            continue
        entries.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest_path = root / "data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["payload_inventory"] = entries
    manifest["package_sha256"] = canonical_package_sha256(
        [(entry["path"], entry["sha256"]) for entry in entries]
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _save_bag(root)


def _rewrite_manifest(root: Path, mutate) -> dict:
    path = root / "data/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _save_bag(root)
    return manifest


def _rewrite_trajectory(root: Path, mutate) -> dict:
    path = root / "data/replay/trajectory.json"
    trajectory = json.loads(path.read_text(encoding="utf-8"))
    mutate(trajectory)
    path.write_text(json.dumps(trajectory, indent=2) + "\n", encoding="utf-8")
    _refresh_inventory(root)
    return trajectory


def test_completed_golden_package_matches_schema_pydantic_and_bagit() -> None:
    bag = bagit.Bag(str(COMPLETED))
    bag.validate(processes=1)
    assert bag.version_info == (1, 0)
    assert not (COMPLETED / "fetch.txt").exists()

    manifest, trajectory = _load_package_json(COMPLETED)
    _validate_schema_docs(manifest, trajectory)
    parsed_manifest, parsed_trajectory = validate_inspection_package(COMPLETED)

    assert parsed_manifest.schema_version == "2.0"
    assert parsed_manifest.source_kind == "inspection_run"
    assert parsed_manifest.status == "completed"
    assert parsed_manifest.exceptions == []
    assert parsed_manifest.bag.storage_id == "mcap"
    assert parsed_trajectory.coordinate_system == "scene_local_yup"
    assert [point.seq for point in parsed_trajectory.points] == [0, 1, 2]
    assert len(parsed_manifest.acceptance_evidence.landmarks) == 4
    assert any(artifact.kind == "pointcloud" for artifact in parsed_manifest.artifacts)


def test_exceptions_golden_package_keeps_independent_status() -> None:
    manifest, trajectory = _load_package_json(EXCEPTIONS)
    _validate_schema_docs(manifest, trajectory)
    parsed, _ = validate_inspection_package(EXCEPTIONS)

    assert parsed.status == "completed_with_exceptions"
    assert parsed.status != "completed"
    assert parsed.exceptions[0].code == "display_artifact_not_provided"
    assert parsed.artifacts == []
    assert all(landmark.role == "required" for landmark in parsed.acceptance_evidence.landmarks)


def test_package_sha256_matches_independent_canonical_inventory() -> None:
    manifest, _ = _load_package_json(COMPLETED)
    lines = "".join(
        f"{entry['sha256']}  {entry['path']}\n"
        for entry in sorted(manifest["payload_inventory"], key=lambda row: row["path"])
    )
    independent = hashlib.sha256(lines.encode("utf-8")).hexdigest()
    assert independent == manifest["package_sha256"]
    assert independent == canonical_package_sha256(
        [(entry["path"], entry["sha256"]) for entry in manifest["payload_inventory"]]
    )


def test_declared_percentiles_match_independent_linear_interpolation() -> None:
    values = [0.008, 0.010, 0.012]
    expected = 0.010 * 0.1 + 0.012 * 0.9
    assert percentile_linear(values, 95) == pytest.approx(expected)

    manifest, _ = _load_package_json(COMPLETED)
    landmark = manifest["acceptance_evidence"]["landmarks"][0]
    translations = [item["translation_residual_m"] for item in landmark["observations"]]
    rotations = [item["rotation_residual_deg"] for item in landmark["observations"]]
    assert landmark["translation_p95_m"] == percentile_linear(translations, 95)
    assert landmark["rotation_p95_deg"] == percentile_linear(rotations, 95)


def test_schema_keeps_completed_and_exceptions_status_independent() -> None:
    manifest, trajectory = _load_package_json(COMPLETED)
    completed_with_extra = copy.deepcopy(manifest)
    completed_with_extra["exceptions"] = [
        {"code": "display_artifact_not_provided", "message": "should not appear"}
    ]
    with pytest.raises(jsonschema.ValidationError):
        _validate_schema_docs(completed_with_extra, trajectory)

    exceptions_manifest, exceptions_trajectory = _load_package_json(EXCEPTIONS)
    exceptions_without_list = copy.deepcopy(exceptions_manifest)
    exceptions_without_list["exceptions"] = []
    with pytest.raises(jsonschema.ValidationError):
        _validate_schema_docs(exceptions_without_list, exceptions_trajectory)

    incomplete = copy.deepcopy(manifest)
    incomplete["status"] = "incomplete"
    with pytest.raises(jsonschema.ValidationError):
        _validate_schema_docs(incomplete, trajectory)


def test_v1_schema_rejects_v2_manifest_and_v2_schema_rejects_v1_manifest() -> None:
    v2_manifest, _ = _load_package_json(COMPLETED)
    v1_manifest = json.loads((V1_FIXTURE / "data/manifest.json").read_text(encoding="utf-8"))
    v2_validator, v2_schema = _schema_validator()
    v1_schema = json.loads(V1_SCHEMA_PATH.read_text(encoding="utf-8"))
    v1_validator = jsonschema.Draft202012Validator(
        v1_schema, format_checker=jsonschema.FormatChecker()
    )

    with pytest.raises(jsonschema.ValidationError):
        v1_validator.evolve(schema=v1_schema["$defs"]["Manifest"]).validate(v2_manifest)
    with pytest.raises(jsonschema.ValidationError):
        v2_validator.evolve(schema=v2_schema["$defs"]["Manifest"]).validate(v1_manifest)


def test_validate_inspection_package_rejects_v1_fixture() -> None:
    with pytest.raises(ValueError, match="schema_version 2.0"):
        validate_inspection_package(V1_FIXTURE)


def test_fetch_txt_is_rejected(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, COMPLETED, "with-fetch")
    (package / "fetch.txt").write_text("https://example.invalid/file.txt\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fetch.txt"):
        validate_inspection_package(package)


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, COMPLETED, "traversal")
    _rewrite_manifest(
        package,
        lambda manifest: manifest["artifacts"].append(
            {"path": "../secret.txt", "kind": "pointcloud", "display": True}
        ),
    )
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_inspection_package(package)


def test_missing_mcap_reference_is_rejected(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, COMPLETED, "no-mcap")
    (package / "data/bags/run.mcap").unlink()
    _rewrite_manifest(package, lambda manifest: None)
    with pytest.raises(ValueError, match="payload_inventory|does not exist"):
        validate_inspection_package(package)


def test_non_strictly_increasing_trajectory_is_rejected(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, COMPLETED, "equal-time")
    _rewrite_trajectory(
        package,
        lambda trajectory: trajectory["points"].__setitem__(
            2,
            {**trajectory["points"][2], "timestamp": trajectory["points"][1]["timestamp"]},
        ),
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_inspection_package(package)


def test_wrong_declared_percentile_is_rejected(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, COMPLETED, "bad-p95")
    _rewrite_manifest(
        package,
        lambda manifest: manifest["acceptance_evidence"]["landmarks"][0].__setitem__(
            "translation_p95_m", 0.99
        ),
    )
    with pytest.raises(ValueError, match="translation_p95_m"):
        validate_inspection_package(package)


def test_v2_checksum_tamper_is_rejected(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, COMPLETED, "tampered-v2")
    trajectory_path = package / "data/replay/trajectory.json"
    trajectory_path.write_text(trajectory_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum|sha256|Payload-Oxum|isclose|valid"):
        validate_inspection_package(package)


def test_optional_display_artifact_listed_but_missing_is_rejected(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, COMPLETED, "missing-ply")
    (package / "data/artifacts/preview-pointcloud.ply").unlink()
    _save_bag(package)
    with pytest.raises(ValueError, match="payload_inventory|does not exist"):
        validate_inspection_package(package)


def _import_named(client, name: str):
    return import_inbox_directory(
        client.app.state.db, client.app.state.settings.import_root, name
    )


def _register_golden_scene(client) -> None:
    register_scene_from_file(
        client.app.state.db,
        REGISTRATION,
        operator_label="fixture-operator",
    )


def test_v1_inspection_run_is_rejected_at_import_boundary(client, tmp_path: Path) -> None:
    source = tmp_path / "v1-inspection-run"
    shutil.copytree(V1_FIXTURE, source)
    inbox = Path(client.app.state.settings.import_root) / "inbox" / "v1-inspection-run"
    shutil.copytree(source, inbox)
    manifest_path = inbox / "data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_kind"] = "inspection_run"
    manifest["bag"] = {"storage_id": "mcap", "path": "bags/run.mcap"}
    (inbox / "data/bags").mkdir(parents=True, exist_ok=True)
    (inbox / "data/bags/run.mcap").write_bytes(b"\x89MCAP0\r\n")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _save_bag(inbox)

    with pytest.raises(BundleImportError) as exc:
        _import_named(client, "v1-inspection-run")

    assert exc.value.code == "bundle_invalid"
    assert "2.0" in exc.value.message
    assert client.get("/api/tasks/fixture-run-001").status_code == 404


def test_v1_synthetic_fixture_remains_importable_for_development(client, tmp_path: Path) -> None:
    inbox = Path(client.app.state.settings.import_root) / "inbox" / "v1-dev"
    shutil.copytree(V1_FIXTURE, inbox)
    result = _import_named(client, "v1-dev")
    assert result["status"] == "imported"
    assert result["run_id"] == "fixture-run-001"


def test_v2_completed_golden_package_imports_through_inbox(client) -> None:
    _register_golden_scene(client)
    inbox = Path(client.app.state.settings.import_root) / "inbox" / "v2-completed"
    shutil.copytree(COMPLETED, inbox)
    result = _import_named(client, "v2-completed")

    assert result["status"] == "imported"
    assert result["run_id"] == "golden-run-v2-completed"
    assert result["trajectory_points"] == 3

    task = client.get("/api/tasks/golden-run-v2-completed")
    assert task.status_code == 200
    body = task.json()
    assert body["run_kind"] == "recorded"
    assert body["acceptance_state"] == "pending_acceptance"
    assert body["package_status"] == "completed"
    assert body["provenance"]["status"] == "pending_confirmation"
    listing = client.get("/api/imports").json()[0]
    assert "archive_path" not in listing
    row = client.app.state.db.query_one(
        "SELECT archive_path FROM run_bundle_imports WHERE run_id = ?",
        ("golden-run-v2-completed",),
    )
    archived = json.loads(
        (Path(row["archive_path"]) / "data/manifest.json").read_text(encoding="utf-8")
    )
    assert archived["status"] == "completed"
    assert archived["schema_version"] == "2.0"


def test_v2_exceptions_package_import_preserves_package_status(client) -> None:
    _register_golden_scene(client)
    inbox = Path(client.app.state.settings.import_root) / "inbox" / "v2-exceptions"
    shutil.copytree(EXCEPTIONS, inbox)
    result = _import_named(client, "v2-exceptions")
    assert result["status"] == "imported"
    task = client.get("/api/tasks/golden-run-v2-completed-with-exceptions").json()
    assert task["status"] == "completed_with_exceptions"
    assert task["package_status"] == "completed_with_exceptions"
    assert task["run_kind"] == "recorded"
    assert task["acceptance_state"] == "pending_acceptance"
    listing = client.get("/api/imports").json()[0]
    assert listing["package_status"] == "completed_with_exceptions"
    assert "archive_path" not in listing


def test_contract_lock_schema_python_openapi_and_typescript() -> None:
    _, schema = _schema_validator()
    pydantic_required = set(InspectionPackageManifest.model_json_schema()["required"])
    schema_required = set(schema["$defs"]["Manifest"]["required"])
    assert pydantic_required == schema_required

    trajectory_required = set(InspectionPackageTrajectory.model_json_schema()["required"])
    assert trajectory_required == set(schema["$defs"]["Trajectory"]["required"])

    status_enum = set(schema["$defs"]["Manifest"]["properties"]["status"]["enum"])
    assert status_enum == {"completed", "completed_with_exceptions"}
    assert status_enum == set(get_args(InspectionPackageManifest.model_fields["status"].annotation))

    source_enum = set(schema["$defs"]["Manifest"]["properties"]["source_kind"]["enum"])
    assert source_enum == set(
        get_args(InspectionPackageManifest.model_fields["source_kind"].annotation)
    )

    openapi = OPENAPI_PATH.read_text(encoding="utf-8")
    assert "./inspection-package-v2.schema.json#/$defs/Manifest" in openapi
    assert "./inspection-package-v2.schema.json#/$defs/Trajectory" in openapi
    assert "does not parse MCAP" in openapi
    assert "online upload" in openapi.lower() or "网络文件上传" in openapi

    typescript = TS_PATH.read_text(encoding="utf-8")
    for token in (
        "schema_version: '2.0'",
        "completed_with_exceptions",
        "package_sha256",
        "acceptance_evidence",
        "payload_inventory",
        "scene_version_id",
        "evidence_sha256",
        "translation_p95_m",
        "rotation_p95_deg",
    ):
        assert token in typescript

    for field in schema["$defs"]["Manifest"]["required"]:
        assert field in typescript


def test_import_function_rejects_v1_inspection_run_without_http(tmp_path: Path, client) -> None:
    source = tmp_path / "direct-v1"
    shutil.copytree(V1_FIXTURE, source)
    manifest_path = source / "data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_kind"] = "inspection_run"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _save_bag(source)
    with pytest.raises(Exception, match="2.0"):
        import_run_bundle(
            client.app.state.db,
            source,
            client.app.state.settings.import_root,
        )

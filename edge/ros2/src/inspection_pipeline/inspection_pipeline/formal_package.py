"""Materialize formal v2 semantics after the validated source was copied.

Operational/provenance documents retain their own v1 schemas. Only the transfer
manifest and derived transfer trajectory use the formal v2 contract.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _contract():
    try:
        from astra_inspect_contract import package
    except ModuleNotFoundError as exc:
        if exc.name not in {"astra_inspect_contract", "astra_inspect_contract.package"}:
            raise
        import sys
        for parent in Path(__file__).resolve().parents:
            source = parent / "packages/contracts/python"
            if (source / "astra_inspect_contract").is_dir():
                sys.path.insert(0, str(source))
                break
        from astra_inspect_contract import package
    return package


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path.name}")
    return value


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def upgrade_formal_payload(data: Path, legacy: dict[str, Any]) -> dict[str, Any]:
    """Bind measured inputs; never manufacture missing acceptance evidence."""
    contract = _contract()
    binding = _json(data / "config/package-v2.json")
    if set(binding) != {"scene_version", "alignment"}:
        raise ValueError("package-v2.json requires exactly scene_version and alignment")
    scene = contract.SceneVersionRef.model_validate(binding["scene_version"])
    alignment = contract.AlignmentRef.model_validate(binding["alignment"])
    if scene.scene_id != legacy["scene_id"] or alignment.alignment_id != legacy["alignment_id"]:
        raise ValueError("v2 binding disagrees with the requested scene/alignment")
    scene_doc = _json(data / "scene/scene-version.json")
    evidence_doc = _json(data / "alignment/evidence.json")
    if (scene_doc.get("scene_id") != scene.scene_id
            or scene_doc.get("scene_version_id") != scene.scene_version_id
            or evidence_doc.get("alignment_id") != alignment.alignment_id
            or evidence_doc.get("scene_version_id") != scene.scene_version_id):
        raise ValueError("scene/alignment snapshot identities disagree with v2 binding")
    evidence = _json(data / "acceptance/evidence.json")
    trajectory_path = data / legacy["replay"]["trajectory_path"]
    original_trajectory = trajectory_path.read_bytes()
    trajectory = json.loads(original_trajectory)
    # Preserve the exact source bytes so the existing provenance digest remains auditable.
    source_path = data / "metadata/source-trajectory-v1.json"
    source_path.write_bytes(original_trajectory)
    trajectory.update(schema_version="2.0", scene_version_id=scene.scene_version_id)
    _write(trajectory_path, trajectory)
    _write(data / "metadata/transfer-trajectory.json", {
        "source_path": "metadata/source-trajectory-v1.json",
        "source_sha256": hashlib.sha256(original_trajectory).hexdigest(),
        "output_path": legacy["replay"]["trajectory_path"],
        "operation": "bind scene version; preserve every trajectory point",
        "scene_version_id": scene.scene_version_id,
    })
    mcap_files = sorted((data / legacy["bag"]["path"]).glob("*.mcap"))
    if not mcap_files:
        raise ValueError("formal package requires recorded MCAP files")
    manifest = {k: v for k, v in legacy.items() if k not in {"scene_id", "alignment_id"}}
    manifest.update(
        schema_version="2.0", scene_version=scene.model_dump(),
        alignment=alignment.model_dump(), acceptance_evidence=evidence,
        bag={"storage_id": "mcap", "path": mcap_files[0].relative_to(data).as_posix()},
        exceptions=([{ "code": "run_completed_with_exceptions", "message": legacy["exit_reason"] }]
                    if legacy["status"] == "completed_with_exceptions" else []),
    )
    inventory = [
        {"path": path.relative_to(data).as_posix(), "sha256": contract._file_sha256(path)}
        for path in sorted(data.rglob("*"))
        if path.is_file() and path != data / "manifest.json"
    ]
    manifest["payload_inventory"] = inventory
    manifest["package_sha256"] = contract.canonical_package_sha256(
        [(entry["path"], entry["sha256"]) for entry in inventory])
    contract.InspectionPackageManifest.model_validate(manifest)
    return manifest


def validate_formal_bundle(path: Path) -> None:
    _contract().validate_inspection_package(path)

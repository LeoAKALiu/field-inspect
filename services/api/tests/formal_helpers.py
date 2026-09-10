"""Shared helpers for Formal Inspection Package inbox tests."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import bagit

from app.inspection_package import canonical_package_sha256, percentile_linear
from app.run_bundle import import_inbox_directory
from app.scene_registration import register_scene_from_file

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRATION = REPO_ROOT / "data/contract-fixtures/scene-registration-scene-001-v1.json"
COMPLETED = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed"
EXCEPTIONS = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed-with-exceptions"
V1_FIXTURE = REPO_ROOT / "data/contract-fixtures/scout-run-bagit-v1"


def register_golden_scene(client, operator: str = "fixture-operator") -> dict:
    return register_scene_from_file(
        client.app.state.db,
        REGISTRATION,
        operator_label=operator,
    )


def inbox_copy(client, source: Path, name: str) -> Path:
    inbox = Path(client.app.state.settings.import_root) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / name
    shutil.copytree(source, target)
    return target


def import_named(client, name: str, **kwargs):
    return import_inbox_directory(
        client.app.state.db,
        client.app.state.settings.import_root,
        name,
        **kwargs,
    )


def save_bag(path: Path) -> None:
    bag = bagit.Bag(str(path))
    bag.save(manifests=True)
    bag.validate(processes=1)


def refresh_inventory(root: Path) -> None:
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
    save_bag(root)


def rewrite_manifest(root: Path, mutate) -> dict:
    path = root / "data/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    save_bag(root)
    return manifest


def rewrite_trajectory(root: Path, mutate) -> dict:
    path = root / "data/replay/trajectory.json"
    trajectory = json.loads(path.read_text(encoding="utf-8"))
    mutate(trajectory)
    path.write_text(json.dumps(trajectory, indent=2) + "\n", encoding="utf-8")
    refresh_inventory(root)
    return trajectory


def set_landmark_residuals(
    root: Path,
    landmark_id: str,
    translations: list[float],
    rotations: list[float],
) -> None:
    def mutate(manifest: dict) -> None:
        for landmark in manifest["acceptance_evidence"]["landmarks"]:
            if landmark["landmark_id"] != landmark_id:
                continue
            kept = landmark["observations"][: len(translations)]
            for index, observation in enumerate(kept):
                observation["translation_residual_m"] = translations[index]
                observation["rotation_residual_deg"] = rotations[index]
                observation["seq"] = index
            landmark["observations"] = kept
            landmark["sample_count"] = len(kept)
            landmark["translation_p95_m"] = percentile_linear(
                [item["translation_residual_m"] for item in kept]
            )
            landmark["rotation_p95_deg"] = percentile_linear(
                [item["rotation_residual_deg"] for item in kept]
            )

    rewrite_manifest(root, mutate)

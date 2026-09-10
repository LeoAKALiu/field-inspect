"""Field-Replay Acceptance evaluation, records, and withdrawal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .db import Database
from .inspection_package import InspectionPackageManifest, percentile_linear


class AcceptanceError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def evaluate_landmarks(
    manifest: InspectionPackageManifest,
    registered_rows: list[Any],
) -> list[dict[str, Any]]:
    """Recompute sample counts and 95th-percentile residuals; do not trust package summaries."""
    observed = {item.landmark_id: item for item in manifest.acceptance_evidence.landmarks}
    verdicts: list[dict[str, Any]] = []
    for row in registered_rows:
        landmark_id = row["landmark_id"]
        item = observed.get(landmark_id)
        base = {
            "landmark_id": landmark_id,
            "role": row["role"],
            "min_valid_samples": row["min_valid_samples"],
            "translation_limit_m": row["translation_m"],
            "rotation_limit_deg": row["rotation_deg"],
        }
        if item is None:
            passed = row["role"] == "auxiliary"
            verdicts.append(
                {
                    **base,
                    "observed": False,
                    "sample_count": 0,
                    "translation_p95_m": None,
                    "rotation_p95_deg": None,
                    "passed": passed,
                    "reason": ("auxiliary_unobserved" if passed else "required_unobserved"),
                }
            )
            continue
        translations = [obs.translation_residual_m for obs in item.observations]
        rotations = [obs.rotation_residual_deg for obs in item.observations]
        sample_count = len(item.observations)
        translation_p95 = percentile_linear(translations)
        rotation_p95 = percentile_linear(rotations)
        reason = "within_tolerance"
        passed = True
        if sample_count < row["min_valid_samples"]:
            passed = False
            reason = "sample_count_below_minimum"
        elif translation_p95 > row["translation_m"]:
            passed = False
            reason = "translation_p95_exceeds_tolerance"
        elif rotation_p95 > row["rotation_deg"]:
            passed = False
            reason = "rotation_p95_exceeds_tolerance"
        verdicts.append(
            {
                **base,
                "observed": True,
                "sample_count": sample_count,
                "translation_p95_m": translation_p95,
                "rotation_p95_deg": rotation_p95,
                "passed": passed,
                "reason": reason,
            }
        )
    return verdicts


def automatic_acceptance_pass(verdicts: list[dict[str, Any]]) -> bool:
    required = [item for item in verdicts if item["role"] == "required"]
    if not required or not all(item["passed"] for item in required):
        return False
    return all(item["passed"] for item in verdicts)


def _manifest_for_run(db: Database, run_id: str) -> tuple[dict, InspectionPackageManifest]:
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (run_id,))
    if task is None or (task["run_kind"] if "run_kind" in task.keys() else "") != "recorded":
        raise AcceptanceError("run_not_recorded", f"{run_id} is not a Recorded Run")
    row = db.query_one(
        "SELECT manifest_json, bundle_sha256, archive_path, scene_version_id, alignment_id "
        "FROM run_bundle_imports WHERE run_id = ?",
        (run_id,),
    )
    if row is None:
        raise AcceptanceError("import_not_found", f"no imported package for {run_id}")
    manifest = InspectionPackageManifest.model_validate(json.loads(row["manifest_json"]))
    return dict(row), manifest


def identities_unchanged(db: Database, run_id: str) -> tuple[bool, str]:
    """Return (ok, reason) whether package, scene, and alignment identities still match."""
    imported, manifest = _manifest_for_run(db, run_id)
    scene = db.query_one(
        "SELECT asset_sha256 FROM scene_versions WHERE scene_version_id = ?",
        (imported["scene_version_id"],),
    )
    alignment = db.query_one(
        "SELECT evidence_sha256 FROM verified_alignments WHERE alignment_id = ?",
        (imported["alignment_id"],),
    )
    if scene is None or alignment is None:
        return False, "scene_or_alignment_missing"
    if scene["asset_sha256"] != manifest.scene_version.asset_sha256:
        return False, "scene_version_changed"
    if alignment["evidence_sha256"] != manifest.alignment.evidence_sha256:
        return False, "alignment_changed"
    from pathlib import Path

    import bagit

    from .run_bundle import _bundle_hash

    archive = Path(imported["archive_path"])
    if not archive.is_dir():
        return False, "archive_missing"
    try:
        bag = bagit.Bag(str(archive))
        bag.validate(processes=1)
        current = _bundle_hash(bag)
    except (OSError, ValueError, bagit.BagError):
        return False, "package_identity_changed"
    if current != imported["bundle_sha256"]:
        return False, "package_identity_changed"
    return True, "unchanged"


def accept_run(
    db: Database,
    run_id: str,
    *,
    operator_label: str,
    first_frame: str,
    last_frame: str,
    remarks: str,
) -> dict[str, Any]:
    label = operator_label.strip()
    if not 1 <= len(label) <= 80:
        raise AcceptanceError("invalid_operator_label", "operator_label must be 1-80 characters")
    if first_frame != "pass" or last_frame != "pass":
        raise AcceptanceError(
            "frames_not_passed",
            "first_frame and last_frame must both be pass",
        )
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (run_id,))
    if task is None:
        raise AcceptanceError("run_not_found", f"{run_id} does not exist")
    state = task["acceptance_state"]
    if state == "accepted":
        raise AcceptanceError("already_accepted", f"{run_id} is already field-accepted")
    imported, manifest = _manifest_for_run(db, run_id)
    if state == "withdrawn":
        unchanged, reason = identities_unchanged(db, run_id)
        if not unchanged:
            raise AcceptanceError(
                "reacceptance_forbidden",
                "package, scene, or alignment changed; import a new package or new run",
                details={"reason": reason},
            )
    registered = db.query_all(
        "SELECT * FROM registered_landmarks WHERE alignment_id = ?",
        (imported["alignment_id"],),
    )
    verdicts = evaluate_landmarks(manifest, registered)
    if not automatic_acceptance_pass(verdicts):
        raise AcceptanceError(
            "automatic_landmarks_failed",
            "automatic landmark checks did not all pass",
            details={"landmarks": verdicts},
        )
    recorded_at = _iso_now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO acceptance_records "
            "(run_id, kind, operator_label, recorded_at, first_frame, last_frame, "
            "landmark_results_json, outcome, remarks) "
            "VALUES (?, 'acceptance', ?, ?, ?, ?, ?, 'accepted', ?)",
            (
                run_id,
                label,
                recorded_at,
                first_frame,
                last_frame,
                json.dumps(verdicts, ensure_ascii=False),
                remarks,
            ),
        )
        conn.execute(
            "UPDATE tasks SET acceptance_state = 'accepted', acceptance_recorded_at = ? "
            "WHERE id = ?",
            (recorded_at, run_id),
        )
        conn.execute(
            "UPDATE run_bundle_imports SET acceptance_state = 'accepted' WHERE run_id = ?",
            (run_id,),
        )
    return {
        "run_id": run_id,
        "outcome": "accepted",
        "operator_label": label,
        "recorded_at": recorded_at,
        "landmarks": verdicts,
        "identity_note": "Operator Label is self-reported traceability, not authenticated identity",
    }


def withdraw_acceptance(
    db: Database,
    run_id: str,
    *,
    operator_label: str,
    reason: str,
) -> dict[str, Any]:
    label = operator_label.strip()
    note = reason.strip()
    if not 1 <= len(label) <= 80:
        raise AcceptanceError("invalid_operator_label", "operator_label must be 1-80 characters")
    if not note:
        raise AcceptanceError("reason_required", "withdrawal requires a reason")
    task = db.query_one("SELECT acceptance_state FROM tasks WHERE id = ?", (run_id,))
    if task is None:
        raise AcceptanceError("run_not_found", f"{run_id} does not exist")
    if task["acceptance_state"] != "accepted":
        raise AcceptanceError("not_accepted", f"{run_id} is not in formal accepted state")
    recorded_at = _iso_now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO acceptance_records "
            "(run_id, kind, operator_label, recorded_at, first_frame, last_frame, "
            "landmark_results_json, outcome, remarks) "
            "VALUES (?, 'withdrawal', ?, ?, NULL, NULL, '[]', 'withdrawn', ?)",
            (run_id, label, recorded_at, note),
        )
        conn.execute(
            "UPDATE tasks SET acceptance_state = 'withdrawn', acceptance_recorded_at = ? "
            "WHERE id = ?",
            (recorded_at, run_id),
        )
        conn.execute(
            "UPDATE run_bundle_imports SET acceptance_state = 'withdrawn' WHERE run_id = ?",
            (run_id,),
        )
    return {
        "run_id": run_id,
        "outcome": "withdrawn",
        "operator_label": label,
        "recorded_at": recorded_at,
        "reason": note,
    }


def acceptance_summary_for(db: Database, run_id: str) -> dict[str, Any] | None:
    """Safe public summary from the latest Field-Replay Acceptance record."""
    row = db.query_one(
        "SELECT first_frame, last_frame, landmark_results_json FROM acceptance_records "
        "WHERE run_id = ? AND kind = 'acceptance' ORDER BY id DESC",
        (run_id,),
    )
    if row is None:
        return None
    landmarks = json.loads(row["landmark_results_json"] or "[]")
    required = [item for item in landmarks if item.get("role") == "required"]
    auxiliary_failed = sum(
        1 for item in landmarks if item.get("role") == "auxiliary" and not item.get("passed")
    )
    return {
        "first_frame": row["first_frame"],
        "last_frame": row["last_frame"],
        "required_passed": sum(1 for item in required if item.get("passed")),
        "required_total": len(required),
        "auxiliary_failed": auxiliary_failed,
    }


def list_acceptance_records(db: Database, run_id: str) -> list[dict[str, Any]]:
    rows = db.query_all(
        "SELECT id, run_id, kind, operator_label, recorded_at, first_frame, last_frame, "
        "landmark_results_json, outcome, remarks FROM acceptance_records "
        "WHERE run_id = ? ORDER BY id ASC",
        (run_id,),
    )
    records = []
    for row in rows:
        item = dict(row)
        item["landmarks"] = json.loads(item.pop("landmark_results_json") or "[]")
        records.append(item)
    return records

"""Loopback-only Local Evidence Report. Not part of the public Digital Twin API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .acceptance import evaluate_landmarks
from .config import Settings, assert_durable_paths
from .db import Database
from .errors import raise_error
from .inspection_package import InspectionPackageManifest

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK:
        return False
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded and forwarded not in {"127.0.0.1", "::1", "localhost"}:
        return False
    return True


def _require_loopback(request: Request) -> None:
    if not is_loopback_request(request):
        raise_error(403, "loopback_only", "Local Evidence Report is bound to 127.0.0.1")


def load_evidence_payload(db: Database, run_id: str) -> dict:
    imported = db.query_one(
        "SELECT archive_path, manifest_json, alignment_id FROM run_bundle_imports WHERE run_id = ?",
        (run_id,),
    )
    if imported is None:
        raise_error(404, "import_not_found", f"no imported package for {run_id}")
    manifest = InspectionPackageManifest.model_validate(json.loads(imported["manifest_json"]))
    registered = db.query_all(
        "SELECT * FROM registered_landmarks WHERE alignment_id = ?",
        (imported["alignment_id"],),
    )
    verdicts = evaluate_landmarks(manifest, registered)
    evidence = manifest.acceptance_evidence
    return {
        "run_id": run_id,
        "archive_path": imported["archive_path"],
        "manifest": manifest,
        "first_frame": evidence.first_frame.path,
        "last_frame": evidence.last_frame.path,
        "landmarks": [
            {
                "landmark_id": item.landmark_id,
                "role": item.role,
                "annotated_image_path": item.annotated_image_path,
                "registered_pose": item.registered_pose.model_dump(),
                "lineage": item.lineage.model_dump(),
                "observations": [obs.model_dump(mode="json") for obs in item.observations],
                "declared_sample_count": item.sample_count,
                "declared_translation_p95_m": item.translation_p95_m,
                "declared_rotation_p95_deg": item.rotation_p95_deg,
            }
            for item in evidence.landmarks
        ],
        "computed": verdicts,
        "identity_note": "Operator Label is not authenticated identity",
    }


def render_evidence_html(payload: dict) -> str:
    run_id = payload["run_id"]
    files = f"/local/evidence-reports/{run_id}/files"
    computed = {item["landmark_id"]: item for item in payload["computed"]}
    landmark_rows = []
    for item in payload["landmarks"]:
        verdict = computed.get(item["landmark_id"], {})
        pose = item["registered_pose"]["position"]
        lineage = item["lineage"]
        landmark_rows.append(
            "<tr>"
            f"<td>{item['landmark_id']}</td><td>{item['role']}</td>"
            f"<td>{pose['x']}, {pose['y']}, {pose['z']}</td>"
            f"<td>{verdict.get('sample_count')}</td>"
            f"<td>{verdict.get('translation_p95_m')}</td>"
            f"<td>{verdict.get('rotation_p95_deg')}</td>"
            f"<td>{verdict.get('translation_limit_m')}</td>"
            f"<td>{verdict.get('rotation_limit_deg')}</td>"
            f"<td>{verdict.get('passed')}</td><td>{verdict.get('reason')}</td>"
            f"<td>{lineage['transform_id']}</td>"
            "</tr>"
            "<tr><td colspan='11'>"
            f"<img alt='{item['landmark_id']}' "
            f"src='{files}/{item['annotated_image_path']}' width='320'>"
            "</td></tr>"
        )
    rows = "".join(landmark_rows)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>Local Evidence Report {run_id}</title></head>
<body>
<h1>Local Evidence Report</h1>
<p>Run {run_id} — loopback only. Images are not published on the public API.</p>
<p>{payload["identity_note"]}</p>
<h2>Frames</h2>
<p>First: {payload["first_frame"]}</p>
<img alt="first frame" src="{files}/{payload["first_frame"]}" width="480">
<p>Last: {payload["last_frame"]}</p>
<img alt="last frame" src="{files}/{payload["last_frame"]}" width="480">
<h2>Computed landmark verdicts</h2>
<table border="1">
<tr><th>id</th><th>role</th><th>registered pose</th><th>n</th>
<th>t p95</th><th>r p95</th><th>t limit</th><th>r limit</th>
<th>passed</th><th>reason</th><th>transform</th></tr>
{rows}</table>
</body></html>
"""


def create_local_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    assert_durable_paths(settings)
    db = Database(settings.db_path)
    db.init_schema()
    app = FastAPI(title="Digital Twin Local Evidence Report", version="0.1.0")
    app.state.db = db
    app.state.settings = settings

    @app.middleware("http")
    async def loopback_middleware(request: Request, call_next):
        if not is_loopback_request(request):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "loopback_only", "message": "bound to 127.0.0.1"}},
            )
        return await call_next(request)

    @app.get("/local/evidence-reports/{run_id}/html")
    def evidence_html(run_id: str, request: Request):
        _require_loopback(request)
        payload = load_evidence_payload(db, run_id)
        return HTMLResponse(render_evidence_html(payload))

    @app.get("/local/evidence-reports/{run_id}")
    def evidence_report(run_id: str, request: Request):
        _require_loopback(request)
        payload = load_evidence_payload(db, run_id)
        public = {
            "run_id": payload["run_id"],
            "first_frame": payload["first_frame"],
            "last_frame": payload["last_frame"],
            "landmarks": payload["landmarks"],
            "computed": payload["computed"],
            "identity_note": payload["identity_note"],
        }
        return public

    @app.get("/local/evidence-reports/{run_id}/files/{name:path}")
    def evidence_file(run_id: str, name: str, request: Request):
        _require_loopback(request)
        payload = load_evidence_payload(db, run_id)
        root = (Path(payload["archive_path"]) / "data").resolve()
        relative = Path(name)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise_error(404, "not_found", name)
        if relative.parts[0] != "acceptance":
            raise_error(404, "not_found", name)
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise_error(404, "not_found", name)
        return FileResponse(target)

    return app

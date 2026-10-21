"""Station screenshots are bound to imported hashes, never arbitrary paths."""
import base64
import hashlib
import json
from pathlib import PurePosixPath

import pytest

from app.db import Database
from app.instruments import save
from app.station_import import capture_image


@pytest.fixture()
def capture(tmp_path):
    db = Database(":memory:")
    db.init_schema()
    archive = tmp_path / "imports" / "archive" / "run-1"
    relative = "artifacts/station-captures/station-1/1/captured.json"
    folder = archive / "data" / "artifacts/station-captures/station-1/1"
    folder.mkdir(parents=True)
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aAt8AAAAASUVORK5CYII="
    )
    image_sha = hashlib.sha256(image).hexdigest()
    metadata = json.dumps({"state": "captured", "image_path": "frame.png", "image_sha256": image_sha}).encode()
    metadata_sha = hashlib.sha256(metadata).hexdigest()
    (folder / "captured.json").write_bytes(metadata)
    (folder / "frame.png").write_bytes(image)
    manifest = {"payload_inventory": [
        {"path": relative, "sha256": metadata_sha},
        {"path": str(PurePosixPath(relative).parent / "frame.png"), "sha256": image_sha},
    ]}
    db.execute("INSERT INTO run_bundle_imports "
               "(run_id,bundle_sha256,schema_version,source_kind,archive_path,manifest_json,imported_at) "
               "VALUES(?,?,?,?,?,?,?)", ("run-1", "a"*64, "2.0", "inspection_run", str(archive),
                                         json.dumps(manifest), "2026-10-20T00:00:00Z"))
    save(db, "station_evidence", "attempt-1", "run-1", {"run_id": "run-1", "captures": [{
        "capture_attempt_seq": 1, "state": "captured", "artifact_path": relative,
        "artifact_sha256": metadata_sha, "image_sha256": image_sha,
    }]}, "test")
    yield db, folder, image
    db.close()


def test_reads_the_original_capture(capture):
    db, _, image = capture
    assert capture_image(db, "attempt-1", 1) == image


def test_image_endpoint_returns_png_without_public_caching(capture, client, monkeypatch):
    from app.routers import get_db

    db, _, image = capture
    monkeypatch.delenv("FIELD_AUTH_FILE", raising=False)
    monkeypatch.setenv("ASTRA_API_TOKEN", "station-image-test-token")
    client.app.dependency_overrides[get_db] = lambda: db
    try:
        url = "/api/instruments/stations/attempt-1/captures/1/image"
        assert client.get(url).status_code == 401
        response = client.get(url, headers={"Authorization": "Bearer station-image-test-token"})
        assert response.status_code == 200
        assert response.content == image
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.parametrize("name", ["frame.png", "captured.json"])
def test_rejects_modified_capture(capture, name):
    db, folder, _ = capture
    (folder / name).write_bytes(b"changed after import")
    with pytest.raises(ValueError, match="digest mismatch"):
        capture_image(db, "attempt-1", 1)


def test_rejects_missing_sequence(capture):
    db, _, _ = capture
    with pytest.raises(ValueError, match="successful capture"):
        capture_image(db, "attempt-1", 2)


def test_rejects_link_outside_archive(capture, tmp_path):
    db, folder, image = capture
    outside = tmp_path / "private.png"
    outside.write_bytes(image)
    (folder / "frame.png").unlink()
    (folder / "frame.png").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        capture_image(db, "attempt-1", 1)

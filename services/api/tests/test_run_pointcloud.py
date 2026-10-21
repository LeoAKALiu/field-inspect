"""Synthetic preview fixtures; no recorded hardware evidence is created or accepted."""
import hashlib
import json
import struct
from pathlib import Path

import pytest

from app.db import Database
from app import run_pointcloud
from app.writer_lock import ImportWriterLock, WriterLockHeld

RUN = "golden-run-v2-completed"


@pytest.fixture()
def cloud(tmp_path):
    db = Database(":memory:")
    db.init_schema()
    root = tmp_path / "imports/archive" / RUN / "data"
    root.mkdir(parents=True)
    manifest = json.loads((Path(__file__).resolve().parents[3] /
        "data/contract-fixtures/inspection-package-v2-completed/data/manifest.json").read_text())
    manifest["artifacts"] = []
    manifest["payload_inventory"] = []

    def write(path, raw):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        manifest["payload_inventory"] = [i for i in manifest["payload_inventory"] if i["path"] != path]
        manifest["payload_inventory"].append({"path": path, "sha256": digest})
        return digest

    alignment_sha = write("config/scene_alignment.yaml", b"synthetic fixture only\n")
    chunks = []
    for seq in range(2):
        path = f"artifacts/pointcloud/chunks/{seq:06d}.pcd"
        points = [(float(seq * 3 + i), 2.0, 3.0, 4.0) for i in range(3)]
        raw = ("# .PCD v0.7\nVERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\n"
               "TYPE F F F F\nCOUNT 1 1 1 1\nWIDTH 3\nHEIGHT 1\n"
               "VIEWPOINT 0 0 0 1 0 0 0\nPOINTS 3\nDATA binary\n").encode()
        raw += b"".join(struct.pack("<ffff", *p) for p in points)
        digest = write(path, raw)
        manifest["artifacts"].append({"path": path, "kind": "pointcloud", "display": True})
        chunks.append({"seq": seq, "path": path, "sha256": digest, "output_point_count": 3})
    document = {"schema_version": "1.0", "run_id": RUN, "coordinate_system": "scene_local_yup",
        "alignment": {"alignment_id": manifest["alignment"]["alignment_id"], "verified": True,
                      "source_kind": "onsite_verified", "sha256": alignment_sha}, "chunks": chunks}

    def save():
        write(run_pointcloud.POINTCLOUD_INDEX, json.dumps(document).encode())
        db.execute("UPDATE run_bundle_imports SET manifest_json=? WHERE run_id=?", (json.dumps(manifest), RUN))

    db.execute("INSERT INTO tasks (id,scene_id,name,mode,status,run_kind,scene_version_id,alignment_id) "
               "VALUES(?,?,?,'replay','completed','recorded',?,?)",
               (RUN, manifest["scene_version"]["scene_id"], "Synthetic preview test",
                manifest["scene_version"]["scene_version_id"], manifest["alignment"]["alignment_id"]))
    db.execute("INSERT INTO run_bundle_imports "
               "(run_id,bundle_sha256,schema_version,source_kind,archive_path,manifest_json,imported_at) "
               "VALUES(?,?,?,?,?,?,?)", (RUN, "a" * 64, "2.0", "inspection_run", str(root.parent),
                                         json.dumps(manifest), "2026-10-21T00:00:00Z"))
    save()
    yield db, root, document, manifest, write, save
    db.close()


def test_merges_all_chunks_without_retransforming(cloud):
    db, root, *_ = cloud
    originals = {p: p.read_bytes() for p in root.rglob("*.pcd")}
    raw, headers = run_pointcloud.preview(db, RUN)
    points = list(struct.iter_unpack("<fff", raw.split(b"end_header\n", 1)[1]))
    assert points == [(float(i), 2.0, 3.0) for i in range(6)]
    assert headers["X-Pointcloud-Source-Points"] == headers["X-Pointcloud-Preview-Points"] == "6"
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_sampling_is_global_and_keeps_later_chunks(cloud, monkeypatch):
    db, *_ = cloud
    monkeypatch.setattr(run_pointcloud, "MAX_PREVIEW_POINTS", 3)
    raw, headers = run_pointcloud.preview(db, RUN)
    assert list(struct.iter_unpack("<fff", raw.split(b"end_header\n", 1)[1])) == [
        (0.0, 2.0, 3.0), (2.0, 2.0, 3.0), (4.0, 2.0, 3.0)]
    assert headers["X-Pointcloud-Sample-Step"] == "2"


def test_rejects_tampered_chunk(cloud):
    db, root, document, *_ = cloud
    (root / document["chunks"][1]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        run_pointcloud.preview(db, RUN)


def test_rejects_symlink(cloud, tmp_path):
    db, root, document, *_ = cloud
    path = root / document["chunks"][0]["path"]
    outside = tmp_path / "outside.pcd"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        run_pointcloud.preview(db, RUN)


@pytest.mark.parametrize("bad", ["../private.pcd", "C:/private.pcd", "artifacts/pointcloud/chunks/000001.pcd"])
def test_rejects_ambiguous_or_unsafe_chunk_identity(cloud, bad):
    db, _, document, manifest, _, save = cloud
    document["chunks"][0]["path"] = bad
    manifest["artifacts"][0]["path"] = bad
    save()
    with pytest.raises(ValueError):
        run_pointcloud.preview(db, RUN)


def test_rejects_alignment_mismatch_and_size_limit(cloud, monkeypatch):
    db, _, document, _, _, save = cloud
    monkeypatch.setattr(run_pointcloud, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(ValueError, match="512 MiB"):
        run_pointcloud.preview(db, RUN)
    document["alignment"]["verified"] = False
    save()
    with pytest.raises(ValueError, match="provenance"):
        run_pointcloud.preview(db, RUN)


def test_rejects_nonfinite_and_incomplete_pcd():
    raw = ("VERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\nTYPE F F F F\n"
           "COUNT 1 1 1 1\nWIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA binary\n").encode()
    with pytest.raises(ValueError, match="non-finite"):
        run_pointcloud._pcd(raw + struct.pack("<ffff", float("nan"), 0, 0, 1))
    with pytest.raises(ValueError, match="incomplete"):
        run_pointcloud._pcd(raw + b"truncated")


@pytest.mark.parametrize("encoding", ["ascii", "binary_little_endian", "binary_big_endian"])
def test_existing_ply_is_returned_unchanged(cloud, encoding):
    db, _, _, manifest, write, save = cloud
    path = "artifacts/preview.ply"
    header = (f"ply\nformat {encoding} 1.0\nelement vertex 1\nproperty float z\n"
              "property uchar red\nproperty double x\nproperty short y\n"
              "element face 1\nproperty list uchar int vertex_indices\nend_header\n")
    body = b"3 255 1 2\n3 0 0 0\n" if encoding == "ascii" else struct.pack(
        ("<" if encoding == "binary_little_endian" else ">") + "fBdhBiii", 3, 255, 1, 2, 3, 0, 0, 0)
    raw = header.encode() + body
    write(path, raw)
    manifest["artifacts"] = [{"path": path, "kind": "pointcloud", "display": True}]
    save()
    result, headers = run_pointcloud.preview(db, RUN)
    assert result == raw
    assert headers["X-Pointcloud-Source-Points"] == headers["X-Pointcloud-Preview-Points"] == "1"


@pytest.mark.parametrize("encoding", ["ascii", "binary_little_endian", "binary_big_endian"])
@pytest.mark.parametrize("fault", ["truncated", "extra", "nonfinite", "out_of_bounds", "missing_z"])
def test_ply_body_is_checked_even_with_a_matching_inventory_digest(cloud, encoding, fault):
    db, root, _, manifest, write, save = cloud
    path = "artifacts/preview.ply"
    properties = "property float x\nproperty float y\nproperty float z\n"
    coordinates = [1, 2, 3]
    if fault == "missing_z":
        properties = "property float x\nproperty float y\n"
        coordinates = [1, 2]
    if fault == "nonfinite": coordinates[0] = float("nan")
    if fault == "out_of_bounds": coordinates[0] = 10_001
    header = f"ply\nformat {encoding} 1.0\nelement vertex 2\n{properties}end_header\n"
    if encoding == "ascii":
        row = (" ".join(str(value) for value in coordinates) + "\n").encode()
    else:
        row = struct.pack(("<" if encoding == "binary_little_endian" else ">") + "f" * len(coordinates), *coordinates)
    raw = header.encode() + row * (1 if fault == "truncated" else 3 if fault == "extra" else 2)
    write(path, raw)
    manifest["artifacts"] = [{"path": path, "kind": "pointcloud", "display": True}]
    save()
    with pytest.raises(run_pointcloud.PointCloudPreviewError):
        run_pointcloud.preview(db, RUN)
    assert (root / path).read_bytes() == raw


def test_ply_point_limit_and_numeric_tokens():
    header = ("ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\n"
              "property float y\nproperty float z\nend_header\n").encode()
    with pytest.raises(run_pointcloud.PointCloudPreviewError, match="numeric token"):
        run_pointcloud._ply_count(header + b"1_0 2 3\n")
    oversized = header.replace(b"vertex 1", b"vertex 500001")
    with pytest.raises(run_pointcloud.PointCloudPreviewError) as caught:
        run_pointcloud._ply_count(oversized)
    assert caught.value.code == "pointcloud_size_limit"


def test_preview_does_not_overlap_import_writer(cloud):
    db, root, *_ = cloud
    with ImportWriterLock(root.parents[2]):
        with pytest.raises(WriterLockHeld):
            run_pointcloud.preview(db, RUN)


def test_preview_endpoint_is_protected_and_private(cloud, client, monkeypatch):
    from app.routers import get_db

    db, *_ = cloud
    monkeypatch.delenv("FIELD_AUTH_FILE", raising=False)
    monkeypatch.setenv("ASTRA_API_TOKEN", "pointcloud-test-token")
    client.app.dependency_overrides[get_db] = lambda: db
    try:
        url = f"/api/tasks/{RUN}/pointcloud.ply"
        assert client.get(url).status_code == 401
        response = client.get(url, headers={"Authorization": "Bearer pointcloud-test-token"})
        assert response.status_code == 200
        assert response.content.startswith(b"ply\n")
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.parametrize("fault, code", [
    ("digest", "pointcloud_digest_mismatch"), ("size", "pointcloud_size_limit"),
    ("missing", "pointcloud_file_missing"), ("format", "pointcloud_invalid_format"),
    ("coordinates", "pointcloud_invalid_coordinates"), ("count", "pointcloud_count_mismatch"),
    ("binding", "pointcloud_binding_mismatch"), ("symlink", "pointcloud_unsafe_path"),
])
def test_preview_endpoint_preserves_safe_failure_reasons(cloud, client, monkeypatch, tmp_path, fault, code):
    from app.routers import get_db

    db, root, document, _, write, save = cloud
    chunk = document["chunks"][0]
    path = root / chunk["path"]
    if fault == "digest": path.write_bytes(b"changed")
    elif fault == "size": monkeypatch.setattr(run_pointcloud, "MAX_SOURCE_BYTES", 1)
    elif fault == "missing": path.unlink()
    elif fault == "format":
        chunk["sha256"] = write(chunk["path"], b"POINTS 3\nDATA binary\n")
    elif fault == "coordinates":
        raw = path.read_bytes()
        header, body = raw.split(b"DATA binary\n", 1)
        chunk["sha256"] = write(chunk["path"], header + b"DATA binary\n" + struct.pack("<f", float("nan")) + body[4:])
    elif fault == "count": chunk["output_point_count"] = 2
    elif fault == "binding": document["alignment"]["verified"] = False
    elif fault == "symlink":
        outside = tmp_path / "private-cloud.pcd"
        path.rename(outside)
        path.symlink_to(outside)
    save()
    monkeypatch.delenv("FIELD_AUTH_FILE", raising=False)
    monkeypatch.setenv("ASTRA_API_TOKEN", "pointcloud-test-token")
    client.app.dependency_overrides[get_db] = lambda: db
    try:
        response = client.get(f"/api/tasks/{RUN}/pointcloud.ply", headers={"Authorization": "Bearer pointcloud-test-token"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["message"]
        assert str(root) not in response.text
        assert "private-cloud" not in response.text
        if fault == "size": assert "512 MiB" in response.json()["error"]["message"]
    finally:
        client.app.dependency_overrides.clear()

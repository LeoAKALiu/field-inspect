"""Read-only, bounded PLY previews of imported v2 point-cloud evidence."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath

from .inspection_package import InspectionPackageManifest
from .run_bundle import _assert_plain_tree
from .writer_lock import ImportWriterLock

MAX_PREVIEW_POINTS = 500_000
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_CHUNK_BYTES = 32 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024
POINTCLOUD_INDEX = "artifacts/pointcloud/manifest.json"


def _pcd(raw):
    """Accept the binary FLOAT32 XYZI subset emitted by inspection_pcd_processor."""
    import numpy as np

    stream = io.BytesIO(raw)
    header = {}
    while stream.tell() < MAX_HEADER_BYTES:
        line = stream.readline(MAX_HEADER_BYTES + 1).decode("ascii").strip()
        if not line or line.startswith("#"):
            if not line and stream.tell() == len(raw):
                break
            continue
        key, *values = line.split()
        if key in header:
            raise ValueError("duplicate PCD header field")
        header[key] = values
        if key == "DATA":
            break
    count = int(header.get("POINTS", ["0"])[0])
    if (stream.tell() > MAX_HEADER_BYTES or header.get("VERSION") not in ([".7"], ["0.7"])
            or header.get("FIELDS") != ["x", "y", "z", "intensity"]
            or header.get("SIZE") != ["4"] * 4 or header.get("TYPE") != ["F"] * 4
            or header.get("COUNT") != ["1"] * 4 or header.get("HEIGHT") != ["1"]
            or header.get("WIDTH") != [str(count)] or header.get("DATA") != ["binary"]
            or count < 1 or len(raw) != stream.tell() + count * 16):
        raise ValueError("unsupported or incomplete binary XYZI PCD")
    points = np.frombuffer(raw, dtype="<f4", offset=stream.tell()).reshape(count, 4)
    if not np.isfinite(points).all() or (np.abs(points[:, :3]) > 10_000).any():
        raise ValueError("PCD contains non-finite or out-of-bounds coordinates")
    return points


def preview(db, run_id):
    row = db.query_one("SELECT * FROM run_bundle_imports WHERE run_id=?", (run_id,))
    task = db.query_one("SELECT * FROM tasks WHERE id=?", (run_id,))
    if row is None or task is None or task["run_kind"] != "recorded":
        raise ValueError("unknown imported recorded run")
    manifest = InspectionPackageManifest.model_validate_json(row["manifest_json"])
    if (manifest.run_id != run_id or manifest.source_kind != "inspection_run"
            or manifest.scene_version.scene_id != task["scene_id"]
            or manifest.scene_version.scene_version_id != task["scene_version_id"]
            or manifest.alignment.alignment_id != task["alignment_id"]):
        raise ValueError("point cloud does not match the recorded run")
    inventory = {item.path: item.sha256 for item in manifest.payload_inventory}
    artifacts = [a.path for a in manifest.artifacts if a.kind == "pointcloud" and a.display]
    if not artifacts:
        raise ValueError("package has no displayable point cloud")
    archive = Path(row["archive_path"])

    def location(relative):
        parts = PurePosixPath(relative)
        if (parts.is_absolute() or ".." in parts.parts or "\\" in relative or ":" in relative
                or parts.as_posix() != relative or relative not in inventory):
            raise ValueError("point cloud must be an inventoried relative payload")
        return archive / "data" / relative

    def read(relative, limit=MAX_CHUNK_BYTES):
        with location(relative).open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("point-cloud preview source exceeds its size limit")
        if hashlib.sha256(raw).hexdigest() != inventory[relative]:
            raise ValueError("point-cloud digest mismatch")
        return raw

    with ImportWriterLock(archive.parent.parent):
        _assert_plain_tree(archive)
        if len(artifacts) == 1 and artifacts[0].endswith(".ply"):
            raw = read(artifacts[0])
            header = raw[:MAX_HEADER_BYTES].replace(b"\r\n", b"\n")
            header_end = header.find(b"end_header\n")
            if not header.startswith(b"ply\n") or header_end < 0:
                raise ValueError("unsupported PLY header")
            vertices = [line.split()[2] for line in header[:header_end].splitlines()
                        if line.startswith(b"element vertex ")]
            if len(vertices) != 1 or not 0 < int(vertices[0]) <= MAX_PREVIEW_POINTS:
                raise ValueError("PLY preview exceeds the vertex limit")
            return raw, _headers(int(vertices[0]), int(vertices[0]), 1, 1)

        document = json.loads(read(POINTCLOUD_INDEX, 1024 * 1024))
        alignment = document["alignment"]
        if (document["schema_version"] != "1.0" or document["run_id"] != run_id
                or document["coordinate_system"] != "scene_local_yup"
                or alignment["alignment_id"] != manifest.alignment.alignment_id
                or alignment["verified"] is not True or alignment["source_kind"] != "onsite_verified"
                or alignment["sha256"] != inventory.get("config/scene_alignment.yaml")):
            raise ValueError("point-cloud coordinate provenance mismatch")
        chunks = document["chunks"]
        if not isinstance(chunks, list) or not 0 < len(chunks) <= 1000:
            raise ValueError("point-cloud chunk list is empty or exceeds its limit")
        if (len(set(artifacts)) != len(artifacts)
                or {chunk["path"] for chunk in chunks} != set(artifacts)):
            raise ValueError("point-cloud chunks do not match display artifacts")
        total_points, total_bytes = 0, 0
        for seq, chunk in enumerate(chunks):
            if (chunk["seq"] != seq or chunk["path"] != f"artifacts/pointcloud/chunks/{seq:06d}.pcd"
                    or chunk["sha256"] != inventory.get(chunk["path"])
                    or type(chunk["output_point_count"]) is not int
                    or not 0 < chunk["output_point_count"] <= MAX_CHUNK_BYTES // 16):
                raise ValueError("point-cloud chunk identity/count mismatch")
            size = location(chunk["path"]).stat().st_size
            if size > MAX_CHUNK_BYTES:
                raise ValueError("point-cloud chunk exceeds preview size limit")
            total_bytes += size
            total_points += chunk["output_point_count"]
        if total_bytes > MAX_SOURCE_BYTES:
            raise ValueError("point-cloud sources exceed the 512 MiB preview limit")
        step = (total_points + MAX_PREVIEW_POINTS - 1) // MAX_PREVIEW_POINTS
        body = bytearray()
        offset = 0
        for chunk in chunks:
            points = _pcd(read(chunk["path"]))
            if len(points) != chunk["output_point_count"]:
                raise ValueError("PCD count does not match point-cloud index")
            body.extend(points[(-offset) % step::step, :3].tobytes())
            offset += len(points)
        count = len(body) // 12
        header = ("ply\nformat binary_little_endian 1.0\n"
                  f"comment run_id {run_id}\ncomment coordinate_system scene_local_yup\n"
                  f"comment source_points {total_points}\ncomment sample_step {step}\n"
                  f"element vertex {count}\nproperty float x\nproperty float y\nproperty float z\nend_header\n")
        return header.encode("ascii") + body, _headers(total_points, count, step, len(chunks))


def _headers(source, shown, step, chunks):
    return {"X-Pointcloud-Source-Points": str(source), "X-Pointcloud-Preview-Points": str(shown),
            "X-Pointcloud-Sample-Step": str(step), "X-Pointcloud-Chunks": str(chunks),
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}

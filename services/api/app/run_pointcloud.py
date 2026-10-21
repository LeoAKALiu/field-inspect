"""Read-only, bounded PLY previews of imported v2 point-cloud evidence."""
import hashlib
import io
import json
import math
import re
import struct
from pathlib import Path, PurePosixPath

from .inspection_package import InspectionPackageManifest
from .run_bundle import _assert_plain_tree
from .writer_lock import ImportWriterLock

MAX_PREVIEW_POINTS = 500_000
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_CHUNK_BYTES = 32 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024
POINTCLOUD_INDEX = "artifacts/pointcloud/manifest.json"


class PointCloudPreviewError(ValueError):
    def __init__(self, message, code="pointcloud_invalid_format"):
        super().__init__(message)
        self.code = code


def _ply_count(raw):
    """Validate complete ASCII/binary PLY records before trusting their vertex count."""
    types = {"char": "b", "int8": "b", "uchar": "B", "uint8": "B",
             "short": "h", "int16": "h", "ushort": "H", "uint16": "H",
             "int": "i", "int32": "i", "uint": "I", "uint32": "I",
             "float": "f", "float32": "f", "double": "d", "float64": "d"}
    stream = io.BytesIO(raw)
    if stream.readline().rstrip(b"\r\n") != b"ply":
        raise PointCloudPreviewError("unsupported PLY header")
    elements, encoding = [], None
    while stream.tell() < MAX_HEADER_BYTES:
        fields = stream.readline(MAX_HEADER_BYTES + 1).decode("ascii").split()
        if not fields or stream.tell() > MAX_HEADER_BYTES:
            raise PointCloudPreviewError("incomplete or oversized PLY header")
        if fields == ["end_header"]:
            break
        if fields[0] in {"comment", "obj_info"}:
            continue
        if fields[0] == "format" and len(fields) == 3 and encoding is None:
            encoding, version = fields[1:]
            if encoding not in {"ascii", "binary_little_endian", "binary_big_endian"} or version != "1.0":
                raise PointCloudPreviewError("unsupported PLY encoding/version")
        elif fields[0] == "element" and len(fields) == 3:
            if not fields[2].isdigit():
                raise PointCloudPreviewError("invalid PLY element count")
            count = int(fields[2])
            if fields[1] == "vertex" and count > MAX_PREVIEW_POINTS:
                raise PointCloudPreviewError("PLY preview exceeds the 500000 vertex limit", "pointcloud_size_limit")
            if count < 0 or count > len(raw) or any(name == fields[1] for name, _, _ in elements):
                raise PointCloudPreviewError("invalid or duplicate PLY element")
            elements.append((fields[1], count, []))
        elif fields[0] == "property" and elements:
            if len(fields) == 3 and fields[1] in types:
                item, name = types[fields[1]], fields[2]
                count_type = None
            elif len(fields) == 5 and fields[1] == "list" and fields[2] in types and fields[3] in types:
                count_type, item, name = types[fields[2]], types[fields[3]], fields[4]
                if count_type in "fd":
                    raise PointCloudPreviewError("PLY list count must be an integer")
            else:
                raise PointCloudPreviewError("unsupported PLY property")
            properties = elements[-1][2]
            if any(prop[0] == name for prop in properties):
                raise PointCloudPreviewError("duplicate PLY property")
            properties.append((name, item, count_type))
        else:
            raise PointCloudPreviewError("unsupported PLY header field")
    else:
        raise PointCloudPreviewError("incomplete or oversized PLY header")
    vertices = [(count, props) for name, count, props in elements if name == "vertex"]
    if encoding is None or len(vertices) != 1 or not vertices[0][0]:
        raise PointCloudPreviewError("PLY requires a vertex element and encoding")
    count, properties = vertices[0]
    if not {"x", "y", "z"} <= {name for name, _, count_type in properties if count_type is None}:
        raise PointCloudPreviewError("PLY requires scalar x/y/z coordinates")
    offset = stream.tell()
    tokens = iter(re.finditer(rb"\S+", raw[offset:])) if encoding == "ascii" else None
    endian = ">" if encoding == "binary_big_endian" else "<"

    def number(code):
        nonlocal offset
        if tokens is not None:
            token = next(tokens).group()
            if b"_" in token:
                raise PointCloudPreviewError("invalid PLY numeric token")
            value = float(token) if code in "fd" else int(token)
            struct.pack(endian + code, value)
        else:
            value = struct.unpack_from(endian + code, raw, offset)[0]
            offset += struct.calcsize(code)
        return value

    try:
        for element, size, properties in elements:
            if size and not properties:
                raise PointCloudPreviewError("PLY element has no properties")
            for _ in range(size):
                for name, code, count_type in properties:
                    if count_type is not None:
                        length = number(count_type)
                        if not 0 <= length <= len(raw):
                            raise PointCloudPreviewError("invalid PLY list length")
                        if tokens is not None:
                            for _ in range(length):
                                number(code)
                        else:
                            offset += length * struct.calcsize(code)
                            if offset > len(raw):
                                raise PointCloudPreviewError("incomplete PLY list")
                    else:
                        value = number(code)
                        if element == "vertex" and name in {"x", "y", "z"} and (not math.isfinite(value) or abs(value) > 10_000):
                            raise PointCloudPreviewError("PLY contains non-finite or out-of-bounds coordinates", "pointcloud_invalid_coordinates")
        if (tokens is not None and next(tokens, None) is not None) or (tokens is None and offset != len(raw)):
            raise PointCloudPreviewError("PLY body differs from declared record counts")
    except (StopIteration, struct.error, OverflowError) as exc:
        raise PointCloudPreviewError("PLY body is incomplete or contains invalid scalar values") from exc
    return count


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
            raise PointCloudPreviewError("duplicate PCD header field")
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
        raise PointCloudPreviewError("unsupported or incomplete binary XYZI PCD")
    points = np.frombuffer(raw, dtype="<f4", offset=stream.tell()).reshape(count, 4)
    if not np.isfinite(points).all() or (np.abs(points[:, :3]) > 10_000).any():
        raise PointCloudPreviewError("PCD contains non-finite or out-of-bounds coordinates", "pointcloud_invalid_coordinates")
    return points


def preview(db, run_id):
    row = db.query_one("SELECT * FROM run_bundle_imports WHERE run_id=?", (run_id,))
    task = db.query_one("SELECT * FROM tasks WHERE id=?", (run_id,))
    if row is None or task is None or task["run_kind"] != "recorded":
        raise PointCloudPreviewError("unknown imported recorded run", "pointcloud_not_found")
    manifest = InspectionPackageManifest.model_validate_json(row["manifest_json"])
    if (manifest.run_id != run_id or manifest.source_kind != "inspection_run"
            or manifest.scene_version.scene_id != task["scene_id"]
            or manifest.scene_version.scene_version_id != task["scene_version_id"]
            or manifest.alignment.alignment_id != task["alignment_id"]):
        raise PointCloudPreviewError("point cloud does not match the recorded run", "pointcloud_binding_mismatch")
    inventory = {item.path: item.sha256 for item in manifest.payload_inventory}
    artifacts = [a.path for a in manifest.artifacts if a.kind == "pointcloud" and a.display]
    if not artifacts:
        raise PointCloudPreviewError("package has no displayable point cloud", "pointcloud_not_found")
    archive = Path(row["archive_path"])

    def location(relative):
        parts = PurePosixPath(relative)
        if (parts.is_absolute() or ".." in parts.parts or "\\" in relative or ":" in relative
                or parts.as_posix() != relative or relative not in inventory):
            raise PointCloudPreviewError("point cloud must be an inventoried relative payload", "pointcloud_unsafe_path")
        return archive / "data" / relative

    def read(relative, limit=MAX_CHUNK_BYTES):
        with location(relative).open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise PointCloudPreviewError(f"point-cloud preview source exceeds its {limit}-byte size limit", "pointcloud_size_limit")
        if hashlib.sha256(raw).hexdigest() != inventory[relative]:
            raise PointCloudPreviewError("point-cloud digest mismatch", "pointcloud_digest_mismatch")
        return raw

    with ImportWriterLock(archive.parent.parent):
        try:
            _assert_plain_tree(archive)
        except ValueError as exc:
            raise PointCloudPreviewError("point-cloud archive contains unsafe paths, symbolic links or special files", "pointcloud_unsafe_path") from exc
        if len(artifacts) == 1 and artifacts[0].endswith(".ply"):
            raw = read(artifacts[0])
            count = _ply_count(raw)
            return raw, _headers(count, count, 1, 1)

        document = json.loads(read(POINTCLOUD_INDEX, 1024 * 1024))
        alignment = document["alignment"]
        if (document["schema_version"] != "1.0" or document["run_id"] != run_id
                or document["coordinate_system"] != "scene_local_yup"
                or alignment["alignment_id"] != manifest.alignment.alignment_id
                or alignment["verified"] is not True or alignment["source_kind"] != "onsite_verified"
                or alignment["sha256"] != inventory.get("config/scene_alignment.yaml")):
            raise PointCloudPreviewError("point-cloud coordinate provenance mismatch", "pointcloud_binding_mismatch")
        chunks = document["chunks"]
        if not isinstance(chunks, list) or not 0 < len(chunks) <= 1000:
            raise PointCloudPreviewError("point-cloud chunk list must contain 1 to 1000 chunks", "pointcloud_size_limit")
        if (len(set(artifacts)) != len(artifacts)
                or {chunk["path"] for chunk in chunks} != set(artifacts)):
            raise PointCloudPreviewError("point-cloud chunks do not match display artifacts", "pointcloud_binding_mismatch")
        total_points, total_bytes = 0, 0
        for seq, chunk in enumerate(chunks):
            if (chunk["seq"] != seq or chunk["path"] != f"artifacts/pointcloud/chunks/{seq:06d}.pcd"
                    or chunk["sha256"] != inventory.get(chunk["path"])
                    or type(chunk["output_point_count"]) is not int
                    or not 0 < chunk["output_point_count"] <= MAX_CHUNK_BYTES // 16):
                raise PointCloudPreviewError("point-cloud chunk identity/count mismatch", "pointcloud_binding_mismatch")
            size = location(chunk["path"]).stat().st_size
            if size > MAX_CHUNK_BYTES:
                raise PointCloudPreviewError("point-cloud chunk exceeds the 32 MiB preview size limit", "pointcloud_size_limit")
            total_bytes += size
            total_points += chunk["output_point_count"]
        if total_bytes > MAX_SOURCE_BYTES:
            raise PointCloudPreviewError("point-cloud sources exceed the 512 MiB preview limit", "pointcloud_size_limit")
        step = (total_points + MAX_PREVIEW_POINTS - 1) // MAX_PREVIEW_POINTS
        body = bytearray()
        offset = 0
        for chunk in chunks:
            points = _pcd(read(chunk["path"]))
            if len(points) != chunk["output_point_count"]:
                raise PointCloudPreviewError("PCD count does not match point-cloud index", "pointcloud_count_mismatch")
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

"""Read-back verification for RFC 8493 BagIt inspection bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any


class BundleVerificationError(ValueError):
    """A transfer bag is incomplete, unsafe, or checksum-invalid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value: str, *, payload: bool) -> str:
    relative = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or ".." in relative.parts
        or value != relative.as_posix()
    ):
        raise BundleVerificationError(f"invalid BagIt path: {value!r}")
    if payload and (len(relative.parts) < 2 or relative.parts[0] != "data"):
        raise BundleVerificationError(f"payload path is outside data/: {value}")
    return value


def _checksum_manifest(path: Path, *, payload: bool) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BundleVerificationError(f"cannot read {path.name}: {exc}") from exc
    entries: dict[str, str] = {}
    for line in lines:
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise BundleVerificationError(f"invalid line in {path.name}")
        checksum, relative_value = parts
        relative = _relative_path(relative_value.strip(), payload=payload)
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise BundleVerificationError(f"invalid SHA-256 in {path.name}")
        if relative in entries:
            raise BundleVerificationError(f"duplicate path in {path.name}: {relative}")
        entries[relative] = checksum
    if not entries:
        raise BundleVerificationError(f"{path.name} is empty")
    return entries


def _plain_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise BundleVerificationError("bundle root must be a real directory")
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        for name in [*directories, *names]:
            path = Path(current) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise BundleVerificationError(
                    f"bundle contains a symbolic link: {path.relative_to(root)}"
                )
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise BundleVerificationError(
                    f"bundle contains a special file: {path.relative_to(root)}"
                )
        files.extend(Path(current) / name for name in names)
    return files


def _bag_info(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BundleVerificationError(f"cannot read bag-info.txt: {exc}") from exc
    values: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            raise BundleVerificationError("invalid bag-info.txt line")
        key, value = line.split(":", 1)
        if key in values:
            raise BundleVerificationError(f"duplicate bag-info.txt field: {key}")
        values[key] = value.strip()
    return values


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleVerificationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleVerificationError(f"{path} must contain a JSON object")
    return value


def verify_bagit_bundle(bundle: Path | str) -> dict[str, Any]:
    """Re-read every payload/tag byte and verify the inspection identity."""
    unresolved = Path(bundle).expanduser()
    if unresolved.is_symlink():
        raise BundleVerificationError("bundle must not be a symbolic link")
    root = unresolved.resolve()
    all_files = _plain_files(root)
    declaration = root / "bagit.txt"
    try:
        if declaration.read_text(encoding="utf-8") != (
            "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
        ):
            raise BundleVerificationError("bagit.txt declaration is invalid")
    except OSError as exc:
        raise BundleVerificationError(f"cannot read bagit.txt: {exc}") from exc

    payload_manifest = _checksum_manifest(
        root / "manifest-sha256.txt",
        payload=True,
    )
    actual_payload = {
        path.relative_to(root).as_posix()
        for path in all_files
        if path.is_relative_to(root / "data")
    }
    if set(payload_manifest) != actual_payload:
        missing = sorted(actual_payload - set(payload_manifest))
        extra = sorted(set(payload_manifest) - actual_payload)
        raise BundleVerificationError(
            f"payload manifest inventory mismatch; missing={missing}, extra={extra}"
        )
    for relative, checksum in payload_manifest.items():
        if _sha256(root / relative) != checksum:
            raise BundleVerificationError(f"payload checksum mismatch: {relative}")

    tag_manifest = _checksum_manifest(
        root / "tagmanifest-sha256.txt",
        payload=False,
    )
    expected_tags = {"bag-info.txt", "bagit.txt", "manifest-sha256.txt"}
    if set(tag_manifest) != expected_tags:
        raise BundleVerificationError("tag manifest inventory mismatch")
    actual_tags = {
        path.relative_to(root).as_posix()
        for path in all_files
        if not path.is_relative_to(root / "data")
    }
    if actual_tags != expected_tags | {"tagmanifest-sha256.txt"}:
        raise BundleVerificationError("bundle contains undeclared or missing tag files")
    for relative, checksum in tag_manifest.items():
        if _sha256(root / relative) != checksum:
            raise BundleVerificationError(f"tag checksum mismatch: {relative}")

    info = _bag_info(root / "bag-info.txt")
    payload_bytes = sum((root / relative).stat().st_size for relative in payload_manifest)
    expected_oxum = f"{payload_bytes}.{len(payload_manifest)}"
    if info.get("Payload-Oxum") != expected_oxum:
        raise BundleVerificationError("Payload-Oxum does not match payload inventory")
    semantic = _load_json(root / "data/manifest.json")
    operational = _load_json(root / "data/metadata/jetson-manifest.json")
    run_id = semantic.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", run_id) is None
    ):
        raise BundleVerificationError("semantic manifest run_id is invalid")
    if operational.get("run_id") != run_id:
        raise BundleVerificationError("semantic and operational run_id differ")
    if info.get("External-Identifier") != run_id:
        raise BundleVerificationError("BagIt External-Identifier differs from run_id")
    bag_reference = semantic.get("bag")
    expected_bag = {"storage_id": "mcap", "path": f"bag/{run_id}"}
    if bag_reference != expected_bag:
        raise BundleVerificationError("semantic MCAP bag reference is invalid")
    if not (root / "data" / expected_bag["path"] / "metadata.yaml").is_file():
        raise BundleVerificationError("transferred rosbag metadata is missing")
    return {
        "schema_version": "1.0",
        "valid": True,
        "run_id": run_id,
        "payload_bytes": payload_bytes,
        "payload_file_count": len(payload_manifest),
        "bundle_sha256_entries": len(payload_manifest) + len(tag_manifest),
    }

"""Verify, flush, and optionally unmount a SCOUT inspection USB device."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from inspection_pipeline.bagit import BundleVerificationError, verify_bagit_bundle


class UsbFinalizeError(ValueError):
    """The requested path is not a verified removable-media mount."""


@dataclass(frozen=True)
class UsbMount:
    """Resolved removable-media mount identity."""

    target: Path
    source: str
    filesystem: str
    uuid: str


def _run_json(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        value = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise UsbFinalizeError(f"command failed: {' '.join(command)}: {exc}") from exc
    if not isinstance(value, dict):
        raise UsbFinalizeError(f"command returned invalid JSON: {' '.join(command)}")
    return value


def _block_device_is_usb(source: str) -> bool:
    document = _run_json(
        ["lsblk", "--json", "--output", "PATH,PKNAME,TRAN,RM,TYPE", source]
    )
    devices = document.get("blockdevices")
    if not isinstance(devices, list) or len(devices) != 1:
        raise UsbFinalizeError(f"cannot resolve block device identity: {source}")
    device = devices[0]
    if not isinstance(device, dict):
        raise UsbFinalizeError(f"cannot resolve block device identity: {source}")
    if device.get("tran") == "usb" or device.get("rm") in (True, 1):
        return True
    parent_name = device.get("pkname")
    if not isinstance(parent_name, str) or not parent_name:
        return False
    parent = f"/dev/{parent_name}"
    parent_document = _run_json(
        ["lsblk", "--json", "--output", "PATH,PKNAME,TRAN,RM,TYPE", parent]
    )
    parents = parent_document.get("blockdevices")
    if not isinstance(parents, list) or len(parents) != 1:
        return False
    parent_device = parents[0]
    return bool(
        isinstance(parent_device, dict)
        and (
            parent_device.get("tran") == "usb"
            or parent_device.get("rm") in (True, 1)
        )
    )


def inspect_usb_mount(mountpoint: Path | str) -> UsbMount:
    """Require an exact mounted filesystem backed by a USB/removable block device."""
    unresolved = Path(mountpoint).expanduser()
    if unresolved.is_symlink():
        raise UsbFinalizeError("USB mountpoint must not be a symbolic link")
    target = unresolved.resolve()
    if target == Path("/") or not target.is_dir():
        raise UsbFinalizeError("USB mountpoint must be an existing non-root directory")
    document = _run_json(
        [
            "findmnt",
            "--json",
            "--target",
            str(target),
            "--output",
            "TARGET,SOURCE,FSTYPE,OPTIONS,UUID",
        ]
    )
    filesystems = document.get("filesystems")
    if not isinstance(filesystems, list) or len(filesystems) != 1:
        raise UsbFinalizeError("cannot resolve USB mountpoint")
    filesystem = filesystems[0]
    if not isinstance(filesystem, dict):
        raise UsbFinalizeError("cannot resolve USB mountpoint")
    mounted_target = filesystem.get("target")
    source = filesystem.get("source")
    filesystem_type = filesystem.get("fstype")
    options = filesystem.get("options")
    filesystem_uuid = filesystem.get("uuid")
    if not isinstance(mounted_target, str) or Path(mounted_target).resolve() != target:
        raise UsbFinalizeError("path is inside a filesystem but is not its mount root")
    if not isinstance(source, str) or not source.startswith("/dev/"):
        raise UsbFinalizeError("USB mount must be backed by a /dev block device")
    if not isinstance(filesystem_type, str) or not filesystem_type:
        raise UsbFinalizeError("USB filesystem type is unavailable")
    if filesystem_type not in {"ext4", "exfat"}:
        raise UsbFinalizeError(
            f"USB filesystem must be field-approved ext4 or exfat: {filesystem_type}"
        )
    if not isinstance(options, str) or "rw" not in options.split(","):
        raise UsbFinalizeError("USB filesystem is not mounted read-write")
    if not isinstance(filesystem_uuid, str) or not filesystem_uuid:
        raise UsbFinalizeError("USB filesystem UUID is unavailable")
    if not _block_device_is_usb(source):
        raise UsbFinalizeError(f"refusing non-USB/non-removable device: {source}")
    return UsbMount(
        target=target,
        source=source,
        filesystem=filesystem_type,
        uuid=filesystem_uuid,
    )


def finalize_usb(
    mountpoint: Path | str,
    *,
    unmount: bool = False,
) -> dict[str, Any]:
    """Checksum all SCOUT bundles twice around a filesystem flush."""
    mount = inspect_usb_mount(mountpoint)
    bundles = sorted(
        path
        for path in mount.target.glob("scout-run-*.bag")
        if path.is_dir() and not path.is_symlink()
    )
    if not bundles:
        raise UsbFinalizeError("USB mount contains no scout-run-*.bag bundles")
    first_reports = []
    try:
        for bundle in bundles:
            first_reports.append(verify_bagit_bundle(bundle))
    except BundleVerificationError as exc:
        raise UsbFinalizeError(f"USB checksum verification failed: {exc}") from exc
    try:
        subprocess.run(
            ["sync", "-f", str(mount.target)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise UsbFinalizeError(f"USB filesystem flush failed: {exc}") from exc
    if inspect_usb_mount(mount.target) != mount:
        raise UsbFinalizeError("USB mount identity changed during finalization")
    try:
        second_reports = [verify_bagit_bundle(bundle) for bundle in bundles]
    except BundleVerificationError as exc:
        raise UsbFinalizeError(f"USB read-back verification failed: {exc}") from exc
    if first_reports != second_reports:
        raise UsbFinalizeError("USB bundle inventory changed during finalization")

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "valid": True,
        "mountpoint": str(mount.target),
        "device": mount.source,
        "filesystem": mount.filesystem,
        "uuid": mount.uuid,
        "synced": True,
        "unmounted": False,
        "bundles": second_reports,
    }
    if unmount:
        try:
            subprocess.run(
                ["udisksctl", "unmount", "--block-device", mount.source],
                check=True,
                capture_output=True,
                text=True,
            )
            mounted = subprocess.run(
                ["findmnt", "--mountpoint", str(mount.target)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise UsbFinalizeError(f"USB unmount command failed: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            raise UsbFinalizeError(f"USB unmount failed: {exc.stderr.strip()}") from exc
        if mounted.returncode == 0:
            raise UsbFinalizeError("USB device still appears mounted")
        report["unmounted"] = True
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mountpoint", type=Path)
    parser.add_argument(
        "--unmount",
        action="store_true",
        help="unmount with udisksctl after verification and flush",
    )
    args = parser.parse_args(argv)
    try:
        report = finalize_usb(args.mountpoint, unmount=args.unmount)
    except UsbFinalizeError as exc:
        print(
            json.dumps(
                {"schema_version": "1.0", "valid": False, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

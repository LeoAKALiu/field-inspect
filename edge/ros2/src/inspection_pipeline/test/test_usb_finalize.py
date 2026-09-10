"""Tests for removable-media identity and mount safety gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from inspection_pipeline import usb_finalize
from inspection_pipeline.usb_finalize import UsbFinalizeError, inspect_usb_mount


def test_inspect_usb_mount_requires_exact_rw_usb_with_uuid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountpoint = tmp_path / "usb"
    mountpoint.mkdir()

    def fake_json(command: list[str]) -> dict[str, object]:
        if command[0] == "findmnt":
            return {
                "filesystems": [
                    {
                        "target": str(mountpoint),
                        "source": "/dev/sdz1",
                        "fstype": "exfat",
                        "options": "rw,nosuid,nodev",
                        "uuid": "usb-fixture",
                    }
                ]
            }
        return {
            "blockdevices": [
                {
                    "path": "/dev/sdz1",
                    "pkname": "sdz",
                    "tran": "usb",
                    "rm": False,
                    "type": "part",
                }
            ]
        }

    monkeypatch.setattr(usb_finalize, "_run_json", fake_json)

    mount = inspect_usb_mount(mountpoint)

    assert mount.source == "/dev/sdz1"
    assert mount.filesystem == "exfat"
    assert mount.uuid == "usb-fixture"


@pytest.mark.parametrize(
    ("target", "filesystem", "options", "match"),
    [
        ("parent", "exfat", "rw,nosuid", "not its mount root"),
        ("exact", "vfat", "rw,nosuid", "ext4 or exfat"),
        ("exact", "ext4", "ro,nosuid", "not mounted read-write"),
    ],
)
def test_inspect_usb_mount_rejects_unsafe_mounts(
    target: str,
    filesystem: str,
    options: str,
    match: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountpoint = tmp_path / "usb"
    mountpoint.mkdir()
    resolved_target = tmp_path if target == "parent" else mountpoint

    monkeypatch.setattr(
        usb_finalize,
        "_run_json",
        lambda _command: {
            "filesystems": [
                {
                    "target": str(resolved_target),
                    "source": "/dev/sdz1",
                    "fstype": filesystem,
                    "options": options,
                    "uuid": "usb-fixture",
                }
            ]
        },
    )

    with pytest.raises(UsbFinalizeError, match=match):
        inspect_usb_mount(mountpoint)

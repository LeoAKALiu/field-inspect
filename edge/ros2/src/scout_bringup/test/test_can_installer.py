"""Black-box tests for the persistent SocketCAN installer."""

from __future__ import annotations

import subprocess
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[3] / "scripts" / "install_scout_can.sh"


def test_installer_renders_stable_udev_and_systemd_assets(tmp_path: Path) -> None:
    """A dry root must receive deterministic adapter and link configuration."""
    completed = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--serial",
            "SCOUT-CAN-TEST",
            "--output-dir",
            str(tmp_path),
            "--skip-module-check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    rule = (tmp_path / "etc/udev/rules.d/99-scout-can.rules").read_text()
    assert 'ENV{ID_SERIAL_SHORT}=="SCOUT-CAN-TEST"' in rule
    assert 'ENV{ID_VENDOR_ID}=="1d50"' in rule
    assert 'ENV{ID_MODEL_ID}=="606f"' in rule
    assert 'NAME="can_scout"' in rule
    assert "scout-can@can_scout.service" in rule
    assert 'SUBSYSTEM=="usb"' in rule
    assert 'ENV{DEVTYPE}=="usb_device"' in rule
    assert 'ATTR{idVendor}=="1d50"' in rule
    assert 'ATTR{idProduct}=="606f"' in rule
    assert 'ATTR{serial}=="SCOUT-CAN-TEST"' in rule
    assert rule.count("ENV{SYSTEMD_WANTS}") == 1

    service = (tmp_path / "etc/systemd/system/scout-can@.service").read_text()
    assert "type can bitrate 500000 restart-ms 100" in service
    assert "ExecStart=/usr/sbin/ip link set dev %i up" in service
    assert (tmp_path / "etc/modules-load.d/scout-can.conf").read_text() == "gs_usb\n"


def test_installer_rejects_unsafe_interface_name(tmp_path: Path) -> None:
    """Names rendered into a root-owned service must not accept shell syntax."""
    completed = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--serial",
            "SCOUT-CAN-TEST",
            "--interface",
            "can;bad",
            "--output-dir",
            str(tmp_path),
            "--skip-module-check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "invalid interface" in completed.stderr


def test_installer_rejects_empty_output_directory() -> None:
    """An empty dry-run root must never collapse into the live /etc path."""
    completed = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--serial",
            "SCOUT-CAN-TEST",
            "--output-dir",
            "",
            "--skip-module-check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "output directory must not be empty" in completed.stderr

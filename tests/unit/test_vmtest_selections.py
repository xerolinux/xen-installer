"""The VM boot suite must hand the engine the same selection keys the GUI does."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from tests.mock_disks import mock_disks  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "vmtest"))

import fixtures  # noqa: E402

BOOT_ONLY = {"squashfs_path"}


@pytest.fixture(scope="module")
def bridge() -> EngineBridge:
    if QApplication.instance() is None:
        QApplication([])
    engine = Engine.from_config_file(fixtures.BOOT_CONFIG)
    with patch("xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=True):
        obj = EngineBridge(
            engine, ROOT / "config" / "examples", dry_run=True, skip_requirements=True
        )
    obj._on_disk_scan_finished(mock_disks())
    obj.setSelectedLocale("en_US.UTF-8")
    obj.setSelectedTimezone("UTC")
    obj.setSelectedKeymap("us")
    obj.setSelectedKeyboardVariant("")
    obj.setUsername("xen")
    obj.setFullName("Xen Boot")
    obj.setHostname(fixtures.BOOT_HOSTNAME)
    obj.setPassword("vmtest-password")
    obj.setRootSameAsUser(True)
    obj.setSelectedDisk("nvme1n1")
    return obj


ALONGSIDE = {
    "alongside_partition": 3,
    "alongside_keep_size_bytes": 1,
    "alongside_esp": {"disk": "vda", "number": 1},
}


MODES = [("auto", {}), ("alongside", ALONGSIDE), ("manual", {})]


@pytest.mark.parametrize(("mode", "extra"), MODES)
def test_boot_selections_have_the_bridge_keys(bridge: EngineBridge, mode: str, extra: dict) -> None:
    bridge.setPartitionMode(mode)
    bridge.applySelectionsToContext()
    gui = bridge._engine._selections
    with patch.object(fixtures, "xero_squashfs", return_value="/run/airootfs.sfs"):
        boot = fixtures.boot_selections("/dev/vda", mode, "ext4", True, **extra)
    assert set(boot) - BOOT_ONLY == set(gui)
    assert boot["btrfs_subvolumes"] == gui["btrfs_subvolumes"]

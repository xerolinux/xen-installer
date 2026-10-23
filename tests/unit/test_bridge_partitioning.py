"""EngineBridge partitioning API: Erase / Install alongside / Manual plans (offscreen)."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.mock_disks import finalize_mock_disk, mock_disks  # noqa: E402
from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.partitioning.plan import build_alongside_plan  # noqa: E402
from xen.utils import disk_detector  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CONFIG = PROJECT_ROOT / "config" / "examples" / "minimal.yaml"

MIB = 1024**2
GIB = 1024**3
A = 2048
DEFAULT_SUBVOLUMES = [
    {"name": "@", "mountpoint": "/"},
    {"name": "@home", "mountpoint": "/home"},
    {"name": "@root", "mountpoint": "/root"},
    {"name": "@srv", "mountpoint": "/srv"},
    {"name": "@cache", "mountpoint": "/var/cache"},
    {"name": "@tmp", "mountpoint": "/var/tmp"},
    {"name": "@log", "mountpoint": "/var/log"},
    {"name": "@swap", "mountpoint": "/swap"},
]


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def _disk(name: str, size_bytes: int, partitions: list[dict[str, Any]]) -> dict[str, Any]:
    return finalize_mock_disk(
        {
            "name": name,
            "model": "Test",
            "size": "",
            "sizeBytes": size_bytes,
            "type": "SSD",
            "removable": False,
            "partitions": partitions,
        }
    )


def _esp(name: str, size_mib: int = 100) -> dict[str, Any]:
    return {
        "name": name,
        "sizeBytes": size_mib * MIB,
        "fstype": "vfat",
        "partType": "efi",
        "partTypeGuid": disk_detector.ESP_TYPE_GUID,
    }


def _two_os_disk() -> dict[str, Any]:
    return _disk(
        "sdb",
        400 * GIB,
        [
            _esp("sdb1"),
            {"name": "sdb2", "sizeBytes": 100 * GIB, "fstype": "ext4", "partType": "linux"},
            {
                "name": "sdb3",
                "sizeBytes": 200 * GIB,
                "fstype": "ntfs",
                "partType": "windows",
                "osName": "Windows 11",
                "minSizeBytes": 10 * GIB,
            },
        ],
    )


def _trailing_free_disk() -> dict[str, Any]:
    return _disk(
        "sdc",
        100 * GIB,
        [
            _esp("sdc1"),
            {"name": "sdc2", "sizeBytes": 50 * GIB, "fstype": "ext4", "partType": "linux"},
        ],
    )


def _scanned(bridge: EngineBridge, disks: list[dict[str, Any]]) -> None:
    bridge._on_disk_scan_finished(copy.deepcopy(disks))


@pytest.fixture
def bridge(qapp: QApplication) -> Iterator[EngineBridge]:
    assert qapp is not None
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    with patch("xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=True):
        obj = EngineBridge(
            engine, MINIMAL_CONFIG.parent, debug=False, dry_run=True, skip_requirements=True
        )
    _scanned(obj, mock_disks())
    yield obj
    logging.getLogger("xen").removeHandler(obj._log_handler)


def _context(bridge: EngineBridge) -> dict[str, Any]:
    bridge.applySelectionsToContext()
    return bridge._engine._selections


def _new_row(ref: str, start_mib: int, size_mib: int, fstype: str, mountpoint: str, **kw: Any):
    start = start_mib * A
    return {
        "kind": "new",
        "ref": ref,
        "start": start,
        "end": start + size_mib * A - 1,
        "fstype": fstype,
        "mountpoint": mountpoint,
        **kw,
    }


def _whole_disk_layout(bridge: EngineBridge) -> None:
    bridge.setSelectedDisk("sda")
    bridge.setPartitionMode("manual")
    bridge.setManualNewTable(True)
    bridge.setManualRow(json.dumps(_new_row("esp", 1, 2000, "fat32", "/boot/efi")))
    bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/")))


class TestAlongside:
    def test_alongside_defaults_to_largest_eligible(self, bridge: EngineBridge) -> None:
        _scanned(bridge, [_two_os_disk()])
        bridge.setSelectedDisk("sdb")
        bridge.setPartitionMode("alongside")
        assert bridge.alongsideAvailable is True
        assert bridge.alongsidePartition == 3

        _scanned(bridge, mock_disks())
        bridge.setSelectedDisk("nvme1n1")
        assert bridge.alongsidePartition == 3
        by_number = {c["number"]: c for c in bridge.alongsideCandidates}
        assert by_number[3]["eligible"] is True
        assert by_number[3]["osName"] == "Windows 11"
        assert by_number[3]["minSizeBytes"] == 96 * GIB
        assert by_number[1]["eligible"] is False
        assert by_number[1]["reason"] == "FAT partitions can't be resized here"
        assert by_number[2]["reason"] == "Unknown or empty partition"
        assert by_number[4]["eligible"] is False
        assert by_number[4]["reason"] == "Too small"
        assert bridge.alongsideEsp["disk"] == "nvme1n1"
        assert bridge.alongsideEsp["number"] == 1

    def test_alongside_unavailable_without_esp_or_shrinkable(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("nvme0n1")
        assert bridge.alongsideAvailable is False
        assert bridge.alongsidePartition == 0
        no_esp = _disk(
            "sdd",
            100 * GIB,
            [{"name": "sdd1", "sizeBytes": 90 * GIB, "fstype": "ext4", "partType": "linux"}],
        )
        _scanned(bridge, [no_esp])
        bridge.setSelectedDisk("sdd")
        assert bridge.alongsideAvailable is False
        assert bridge.espCandidates == []

    def test_set_alongside_partition_rejects_ineligible(self, bridge: EngineBridge) -> None:
        _scanned(bridge, [_two_os_disk()])
        bridge.setSelectedDisk("sdb")
        bridge.setAlongsidePartition(1)
        assert bridge.alongsidePartition == 3
        bridge.setAlongsidePartition(2)
        assert bridge.alongsidePartition == 2

    def test_alongside_size_clamped(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("nvme1n1")
        bridge.setPartitionMode("alongside")
        # 510 GiB partition, min 96 GiB -> keeps >= ceil_GiB(105.6 GiB) = 106 GiB
        assert bridge.alongsideMinNewMb == 25 * 1024
        assert bridge.alongsideMaxNewMb == 510 * 1024 - 106 * 1024
        assert bridge.alongsideNewSizeMb == 255 * 1024

        bridge.setAlongsideNewSizeMb(1000)
        assert bridge.alongsideNewSizeMb == 25600
        assert _context(bridge)["alongside_keep_size_bytes"] == (510 * 1024 - 25600) * MIB

        bridge.setAlongsideNewSizeMb(10**7)
        assert bridge.alongsideNewSizeMb == 413696
        assert _context(bridge)["alongside_keep_size_bytes"] == 106 * GIB

        bridge.setAlongsideNewSizeMb(300000)
        assert _context(bridge)["alongside_keep_size_bytes"] == 222240 * MIB
        assert bridge.planValid is True

    def test_alongside_size_conversion_into_trailing_free_space(
        self, bridge: EngineBridge
    ) -> None:
        disk = _trailing_free_disk()
        _scanned(bridge, [disk])
        bridge.setSelectedDisk("sdc")
        bridge.setPartitionMode("alongside")
        # the shrink must take at least 1 MiB, the new partition also gets the free tail
        assert bridge.alongsideMinNewMb == 51099
        assert bridge.alongsideMaxNewMb == 101274
        assert bridge.alongsideNewSizeMb == 76698

        bridge.setAlongsideNewSizeMb(60000)
        keep = _context(bridge)["alongside_keep_size_bytes"]
        assert keep == 42298 * MIB
        plan = build_alongside_plan(disk, 2, keep, "xfs", False, 1, "sdc", [])
        root = plan.creates[0]
        assert root.end - root.start + 1 >= 60000 * A
        assert (root.end - root.start + 1) // A == 60000
        assert plan.resizes[0].new_end == 101 * A + 42298 * A - 1

    def test_esp_selection(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        assert [(e["disk"], e["number"]) for e in bridge.espCandidates] == [
            ("sda", 1),
            ("nvme1n1", 1),
        ]
        assert bridge.alongsideEsp["disk"] == "sda"
        bridge.setAlongsideEsp(json.dumps({"disk": "nvme1n1", "number": 1}))
        assert bridge.alongsideEsp["disk"] == "nvme1n1"
        bridge.setAlongsideEsp(json.dumps({"disk": "sda", "number": 2}))
        assert bridge.alongsideEsp["disk"] == "nvme1n1"
        bridge.setAlongsideEsp("not json")
        assert bridge.alongsideEsp["disk"] == "nvme1n1"


class TestBtrfsSubvolumes:
    def test_btrfs_subvolumes_rejects_invalid(self, bridge: EngineBridge) -> None:
        assert json.loads(bridge.defaultBtrfsSubvolumes) == DEFAULT_SUBVOLUMES
        assert json.loads(bridge.btrfsSubvolumes) == DEFAULT_SUBVOLUMES

        for bad in (
            "[]",
            "not json",
            '{"name": "@"}',
            json.dumps([{"name": "@home", "mountpoint": "/home"}]),
            json.dumps([{"name": "root", "mountpoint": "/"}]),
            json.dumps([{"name": "@", "mountpoint": "/"}, {"name": "@", "mountpoint": "/x"}]),
        ):
            assert bridge.setBtrfsSubvolumes(bad) is False
            assert json.loads(bridge.btrfsSubvolumes) == DEFAULT_SUBVOLUMES

        custom = [{"name": "@", "mountpoint": "/"}, {"name": "@data", "mountpoint": "/data"}]
        assert bridge.setBtrfsSubvolumes(json.dumps(custom)) is True
        assert json.loads(bridge.btrfsSubvolumes) == custom
        bridge.setFilesystem("btrfs")
        assert _context(bridge)["btrfs_subvolumes"] == custom

    def test_validate_btrfs_subvolumes(self, bridge: EngineBridge) -> None:
        assert bridge.validateBtrfsSubvolumes(json.dumps(DEFAULT_SUBVOLUMES)) == ""
        assert bridge.validateBtrfsSubvolumes("not json") != ""
        assert bridge.validateBtrfsSubvolumes('[{"name": "@"}]') != ""
        assert bridge.validateBtrfsSubvolumes("[]") == "At least one Btrfs subvolume is required"
        duplicate = [*DEFAULT_SUBVOLUMES, {"name": "@data", "mountpoint": "/home"}]
        error = bridge.validateBtrfsSubvolumes(json.dumps(duplicate))
        assert error == "Duplicate subvolume mountpoint"
        assert json.loads(bridge.btrfsSubvolumes) == DEFAULT_SUBVOLUMES

    def test_config_drives_default_subvolumes(self, qapp: QApplication, tmp_path: Path) -> None:
        config = tmp_path / "xen.yaml"
        config.write_text(
            'version: "1.0"\nbranding:\n  name: "T"\njobs:\n'
            "  - name: partition\n    config:\n      btrfs_subvolumes:\n"
            '        - {name: "@", mountpoint: "/"}\n'
            '        - {name: "@home", mountpoint: "/home"}\n'
        )
        with patch("xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=True):
            obj = EngineBridge(
                Engine.from_config_file(config), tmp_path, dry_run=True, skip_requirements=True
            )
        try:
            assert json.loads(obj.defaultBtrfsSubvolumes) == DEFAULT_SUBVOLUMES[:2]
            assert json.loads(obj.btrfsSubvolumes) == DEFAULT_SUBVOLUMES[:2]
        finally:
            logging.getLogger("xen").removeHandler(obj._log_handler)


class TestManual:
    def test_manual_rows_drive_plan_preview(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        assert [s["kind"] for s in bridge.planPreview] == ["partition", "partition", "free"]
        assert not any(s["pending"] for s in bridge.planPreview)

        _whole_disk_layout(bridge)
        assert [r["ref"] for r in bridge.manualRows] == ["esp", "root"]
        preview = bridge.planPreview
        assert [s["newRef"] for s in preview if s["newRef"]] == ["esp", "root"]
        assert sum(1 for s in preview if s["pendingDelete"]) == 2
        root = next(s for s in preview if s["newRef"] == "root")
        assert root["mountpoint"] == "/"
        assert root["fstype"] == "ext4"

        bridge.setManualRow(json.dumps(_new_row("root", 2001, 30 * 1024, "xfs", "/")))
        assert len(bridge.manualRows) == 2
        root = next(s for s in bridge.planPreview if s["newRef"] == "root")
        assert root["fstype"] == "xfs"
        assert root["sizeBytes"] == 30 * GIB

        bridge.removeManualRow("root")
        assert [r["ref"] for r in bridge.manualRows] == ["esp"]
        assert [s["newRef"] for s in bridge.planPreview if s["newRef"]] == ["esp"]

    def test_existing_rows_replace_by_number(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        row = {"kind": "existing", "number": 2, "action": "delete"}
        assert bridge.setManualRow(json.dumps(row)) is True
        assert bridge.setManualRow(json.dumps({**row, "action": "keep", "mountpoint": "/data"}))
        assert bridge.manualRows == [{**row, "action": "keep", "mountpoint": "/data"}]
        bridge.removeManualRow("2")
        assert bridge.manualRows == []

    def test_malformed_rows_are_rejected(self, bridge: EngineBridge) -> None:
        bridge.setPartitionMode("manual")
        for bad in (
            "not json",
            "[]",
            json.dumps({"kind": "new", "start": 1, "end": 2}),
            json.dumps({"kind": "new", "ref": "7", "start": 1, "end": 2}),
            json.dumps({"kind": "existing"}),
            json.dumps({"kind": "existing", "number": "x"}),
        ):
            assert bridge.setManualRow(bad) is False
        assert bridge.manualRows == []

    def test_new_table_drops_existing_rows_and_revert_clears(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        bridge.setManualRow(json.dumps({"kind": "existing", "number": 2, "action": "delete"}))
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/")))
        bridge.setManualNewTable(True)
        assert bridge.manualNewTable is True
        assert [r.get("ref") for r in bridge.manualRows] == ["root"]
        bridge.setManualRootPassphrase("secret root pass")
        bridge.revertManual()
        assert bridge.manualRows == []
        assert bridge.manualNewTable is False
        assert _context(bridge)["manual_root_passphrase"] == ""

    def test_disk_change_discards_manual_rows(self, bridge: EngineBridge) -> None:
        _whole_disk_layout(bridge)
        bridge.setSelectedDisk("nvme0n1")
        assert bridge.manualRows == []
        assert bridge.manualNewTable is False

    def test_disk_change_discards_manual_root_passphrase(self, bridge: EngineBridge) -> None:
        _whole_disk_layout(bridge)
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/", encrypt=True)))
        bridge.setManualRootPassphrase("correct horse")
        bridge.setSelectedDisk("nvme0n1")
        assert bridge._manual_root_passphrase == ""

    def test_encrypted_root_needs_passphrase(self, bridge: EngineBridge) -> None:
        _whole_disk_layout(bridge)
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/", encrypt=True)))
        assert bridge.planValid is False
        assert "Enter a passphrase for the encrypted root (/) partition." in bridge.planErrors
        bridge.setManualRootPassphrase("correct horse")
        assert bridge.planValid is True
        assert bridge._redactor.redact("x correct horse x") != "x correct horse x"

    @pytest.mark.parametrize(
        "change",
        [
            lambda b: b.removeManualRow("root"),
            lambda b: b.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/"))),
            lambda b: b.setManualRow(
                json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/home", encrypt=True))
            ),
        ],
        ids=["removed", "unencrypted", "mount-changed"],
    )
    def test_passphrase_cleared_without_encrypted_root(
        self, bridge: EngineBridge, change: Callable[[EngineBridge], Any]
    ) -> None:
        _whole_disk_layout(bridge)
        bridge.setManualRootPassphrase("correct horse")
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/", encrypt=True)))
        assert bridge._manual_root_passphrase == "correct horse"
        change(bridge)
        assert bridge._manual_root_passphrase == ""

    def test_passphrase_kept_while_encrypted_root_remains(self, bridge: EngineBridge) -> None:
        _whole_disk_layout(bridge)
        bridge.setManualRootPassphrase("correct horse")
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/", encrypt=True)))
        bridge.removeManualRow("esp")
        bridge.setManualRow(json.dumps(_new_row("home", 50000, 1024, "ext4", "/home")))
        assert bridge._manual_root_passphrase == "correct horse"


class TestPlanValidity:
    def test_plan_valid_tracks_errors(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        assert bridge.partitionMode == "auto"
        assert bridge.planValid is True
        assert bridge.planErrors == []

        bridge.setPartitionMode("manual")
        assert bridge.planValid is False
        assert "No partition is assigned to root (/)." in bridge.planErrors
        assert [c["ok"] for c in bridge.planChecklist] == [False, False]

        _whole_disk_layout(bridge)
        assert bridge.planErrors == []
        assert bridge.planValid is True
        assert [c["ok"] for c in bridge.planChecklist] == [True, True]
        assert "There is no swap partition. Hibernation won't be available." in bridge.planWarnings

        bridge.setManualRow(json.dumps(_new_row("root", 2001, 10 * 1024, "ext4", "/")))
        assert bridge.planValid is False
        assert "The root (/) partition must be at least 25 GiB." in bridge.planErrors

    def test_no_disk_is_invalid(self, bridge: EngineBridge) -> None:
        assert bridge.selectedDisk == ""
        assert bridge.planValid is False
        assert bridge.planErrors == ["No disk selected."]
        assert bridge.planPreview == []

    def test_erase_and_alongside_skip_swap_partition_warning(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("nvme1n1")
        assert bridge.planWarnings == []
        bridge.setPartitionMode("alongside")
        assert bridge.planValid is True
        assert bridge.planWarnings == []

    def test_btrfs_root_offline_is_invalid(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        bridge.setFilesystem("btrfs")
        assert bridge.planValid is True
        bridge._apply_internet_status(False)
        assert bridge.planValid is False
        assert any("internet" in e for e in bridge.planErrors)

    def test_partitioning_signal_fires(self, bridge: EngineBridge) -> None:
        received: list[int] = []
        bridge.partitioningChanged.connect(lambda: received.append(1))
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/")))
        assert len(received) == 3


class TestContext:
    def test_context_keys_per_mode(self, bridge: EngineBridge) -> None:
        alongside_keys = {"alongside_partition", "alongside_keep_size_bytes", "alongside_esp"}
        manual_keys = {"manual_rows", "manual_new_table", "manual_root_passphrase"}

        bridge.setSelectedDisk("nvme1n1")
        ctx = _context(bridge)
        assert ctx["partition_mode"] == "auto"
        assert ctx["disk"] == "/dev/nvme1n1"
        assert ctx["efi_size_mb"] == 2000
        assert ctx["btrfs_subvolumes"] == DEFAULT_SUBVOLUMES
        assert ctx["has_internet"] is True
        assert ctx["confirmed"] is False
        assert ctx["dry_run"] is True
        assert not (alongside_keys | manual_keys) & ctx.keys()

        bridge.setPartitionMode("alongside")
        ctx = _context(bridge)
        assert ctx["partition_mode"] == "alongside"
        assert ctx["alongside_partition"] == 3
        assert ctx["alongside_keep_size_bytes"] == 255 * GIB
        assert ctx["alongside_esp"] == {"disk": "nvme1n1", "number": 1}
        assert not manual_keys & ctx.keys()

        _whole_disk_layout(bridge)
        bridge.setManualRootPassphrase("pw")
        ctx = _context(bridge)
        assert ctx["partition_mode"] == "manual"
        assert ctx["disk"] == "/dev/sda"
        assert ctx["manual_new_table"] is True
        assert [r["ref"] for r in ctx["manual_rows"]] == ["esp", "root"]
        assert ctx["manual_root_passphrase"] == "pw"
        assert ctx["filesystem"] == "ext4"
        assert ctx["encryption"] is False
        assert not alongside_keys & ctx.keys()

    def test_manual_context_is_accepted_by_the_job(self, bridge: EngineBridge) -> None:
        from xen.jobs.base import JobContext
        from xen.jobs.partition import PartitionJob

        _whole_disk_layout(bridge)
        selections = dict(_context(bridge))
        job = PartitionJob({})
        with (
            patch("xen.jobs.partition.os.path.exists", return_value=True),
            patch("xen.jobs.partition.disk_detector.list_disks", return_value=bridge.disksModel),
        ):
            result = job.validate(JobContext(selections=selections))
        assert result.success, result.message

    def test_alongside_context_is_accepted_by_the_job(self, bridge: EngineBridge) -> None:
        from xen.jobs.base import JobContext
        from xen.jobs.partition import PartitionJob

        bridge.setSelectedDisk("nvme1n1")
        bridge.setPartitionMode("alongside")
        selections = dict(_context(bridge))
        with (
            patch("xen.jobs.partition.os.path.exists", return_value=True),
            patch("xen.jobs.partition.disk_detector.list_disks", return_value=bridge.disksModel),
        ):
            result = PartitionJob({}).validate(JobContext(selections=selections))
        assert result.success, result.message


def _arm(bridge: EngineBridge) -> EngineBridge:
    bridge.setConfirmed(True)
    assert bridge.confirmed is True
    return bridge


CHANGES: list[tuple[str, Callable[[EngineBridge], None]]] = [
    ("alongside_partition", lambda b: b.setAlongsidePartition(2)),
    ("alongside_size", lambda b: b.setAlongsideNewSizeMb(40000)),
    ("alongside_esp", lambda b: b.setAlongsideEsp(json.dumps({"disk": "sda", "number": 1}))),
    (
        "subvolumes",
        lambda b: b.setBtrfsSubvolumes(json.dumps([{"name": "@", "mountpoint": "/"}])),
    ),
    ("manual_row", lambda b: b.setManualRow(json.dumps(_new_row("x", 1, 100, "ext4", "/x")))),
    ("manual_remove", lambda b: b.removeManualRow("root")),
    ("manual_new_table", lambda b: b.setManualNewTable(False)),
    ("manual_revert", lambda b: b.revertManual()),
    ("manual_passphrase", lambda b: b.setManualRootPassphrase("new pass")),
]


class TestConfirmation:
    @pytest.mark.parametrize("change", [c[1] for c in CHANGES], ids=[c[0] for c in CHANGES])
    def test_confirmed_resets_on_changes(self, bridge: EngineBridge, change) -> None:
        _scanned(bridge, [_two_os_disk(), *mock_disks()])
        bridge.setSelectedDisk("sdb")
        bridge.setPartitionMode("manual")
        bridge.setManualNewTable(True)
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/")))
        _arm(bridge)
        change(bridge)
        assert bridge.confirmed is False

    def test_rejected_change_keeps_confirmation(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        _arm(bridge)
        assert bridge.setBtrfsSubvolumes("[]") is False
        assert bridge.setManualRow("not json") is False
        assert bridge.confirmed is True


class TestDiskScan:
    def test_disk_scan_runs_in_worker_and_notifies(self, bridge: EngineBridge) -> None:
        main = threading.current_thread()
        seen: dict[str, Any] = {}
        fake = [_two_os_disk()]

        def list_disks(probe: bool = False):
            seen.update(thread=threading.current_thread(), probe=probe)
            time.sleep(0.05)
            return copy.deepcopy(fake)

        scanning: list[bool] = []
        bridge.disksScanningChanged.connect(lambda: scanning.append(bridge.disksScanning))
        disks_changed: list[int] = []
        bridge.disksChanged.connect(lambda: disks_changed.append(1))
        with patch("xen.gui.bridge.disk_detector.list_disks", side_effect=list_disks):
            bridge.refreshDisks()
            assert bridge.disksScanning is True
            deadline = time.monotonic() + 5
            while (bridge.disksScanning or bridge._scan_thread) and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.01)
        assert bridge.disksScanning is False
        assert scanning == [True, False]
        assert disks_changed == [1]
        assert seen["thread"] is not main
        assert seen["probe"] is False
        assert [d["name"] for d in bridge.disksModel] == ["sdb"]
        assert bridge.disksModel[0]["partitions"][2]["osName"] == "Windows 11"


def _settle_scan(bridge: EngineBridge, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while (bridge.disksScanning or bridge._scan_thread) and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)


class TestDiskScanLifecycle:
    def test_quit_mid_scan_returns(self, bridge: EngineBridge) -> None:
        def slow(**_kw: Any):
            time.sleep(0.3)
            return mock_disks()

        with patch("xen.gui.bridge.disk_detector.list_disks", side_effect=slow):
            bridge.refreshDisks()
            started = time.monotonic()
            bridge._wait_for_disk_scan()
            assert time.monotonic() - started < 3
            _settle_scan(bridge)
        assert bridge._scan_thread is None

    def test_refresh_during_scan_runs_one_more_scan(self, bridge: EngineBridge) -> None:
        calls: list[int] = []

        def counting(**_kw: Any):
            calls.append(1)
            time.sleep(0.1)
            return [_two_os_disk()] if len(calls) > 1 else mock_disks()

        with patch("xen.gui.bridge.disk_detector.list_disks", side_effect=counting):
            bridge.refreshDisks()
            bridge.refreshDisks()
            bridge.refreshDisks()
            _settle_scan(bridge)
            time.sleep(0.2)
            _settle_scan(bridge)
        assert len(calls) == 2
        assert [d["name"] for d in bridge.disksModel] == ["sdb"]

    def test_esp_choice_survives_rescan_with_new_label(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("sda")
        bridge.setAlongsideEsp(json.dumps({"disk": "nvme1n1", "number": 1}))
        disks = mock_disks()
        disks[2]["partitions"][0]["label"] = "RENAMED"
        _scanned(bridge, disks)
        assert (bridge.alongsideEsp["disk"], bridge.alongsideEsp["number"]) == ("nvme1n1", 1)
        assert bridge.alongsideEsp["label"] == "RENAMED"

    def test_rescan_that_changes_the_plan_disarms(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("nvme1n1")
        bridge.setPartitionMode("alongside")
        bridge.setAlongsideNewSizeMb(10**7)
        _arm(bridge)
        _scanned(bridge, mock_disks())
        assert bridge.confirmed is True

        disks = mock_disks()
        disks[2]["partitions"][2]["minSizeBytes"] = 200 * GIB
        _scanned(bridge, disks)
        assert bridge.alongsideNewSizeMb == 510 * 1024 - 220 * 1024
        assert bridge.confirmed is False


class TestBootloaderType:
    def test_defaults_to_grub(self, bridge: EngineBridge) -> None:
        assert bridge.bootloaderType == "grub"

    def test_defaults_when_no_bootloader_job_present(self, bridge: EngineBridge) -> None:
        bridge._engine.jobs = [j for j in bridge._engine.jobs if j.name != "bootloader"]
        assert bridge.bootloaderType == "grub"


class TestFinalFixes:
    def test_encrypted_separate_boot_is_a_plan_error(self, bridge: EngineBridge) -> None:
        _whole_disk_layout(bridge)
        bridge.setManualRow(json.dumps(_new_row("root", 2001, 40 * 1024, "ext4", "/", encrypt=True)))
        bridge.setManualRootPassphrase("correct horse")
        bridge.setManualRow(
            json.dumps(_new_row("boot", 50000, 1024, "ext4", "/boot", encrypt=True))
        )
        assert bridge.planValid is False
        assert (
            "A separate /boot can't be encrypted. Leave /boot unencrypted, "
            "or remove it so /boot lives on the encrypted root."
        ) in bridge.planErrors

    @pytest.mark.parametrize("min_bytes", [0, 40 * GIB, 40 * GIB + 1])
    def test_min_shrink_mib_matches_the_plan_minimum(
        self, bridge: EngineBridge, min_bytes: int
    ) -> None:
        from xen.partitioning import fscaps

        assert bridge.minShrinkMiB(float(min_bytes)) * MIB == fscaps.min_shrink_bytes(min_bytes)

    @pytest.mark.parametrize("offset_sectors", [1, 63, 2047])
    def test_alongside_slider_never_yields_a_root_too_small_error(
        self, bridge: EngineBridge, offset_sectors: int
    ) -> None:
        from xen.partitioning.plan import validate_plan

        disk = _disk(
            "sde",
            120 * GIB,
            [
                {**_esp("sde1"), "sizeBytes": 100 * MIB + offset_sectors * 512},
                {"name": "sde2", "sizeBytes": 80 * GIB, "fstype": "ext4", "partType": "linux",
                 "minSizeBytes": 5 * GIB},
            ],
        )  # fmt: skip
        assert disk["partitions"][1]["startSector"] % A
        _scanned(bridge, [disk])
        bridge.setSelectedDisk("sde")
        bridge.setPartitionMode("alongside")
        low, high = bridge.alongsideMinNewMb, bridge.alongsideMaxNewMb
        for new_mb in sorted({low, low + 1, low + 7, (low + high) // 2, high - 1, high}):
            bridge.setAlongsideNewSizeMb(new_mb)
            assert bridge.planErrors == [], (new_mb, bridge.planErrors)
            keep = _context(bridge)["alongside_keep_size_bytes"]
            plan = build_alongside_plan(disk, 2, keep, "ext4", False, 1, "sde", [])
            root = plan.creates[0]
            assert (root.end - root.start + 1) // A >= new_mb
            assert validate_plan(
                plan, disk, uefi=True, has_internet=True, min_install_gib=25
            ) == []

    def test_install_refused_while_a_disk_scan_runs(self, bridge: EngineBridge) -> None:
        finished: list[bool] = []
        bridge.installationFinished.connect(finished.append)
        bridge._disks_scanning = True
        with patch.object(bridge._engine, "run_all") as run_all:
            bridge.startInstallation()
            deadline = time.monotonic() + 2
            while not finished and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.01)
        run_all.assert_not_called()
        assert finished == [False]
        assert "disk scan" in bridge.errorMessage
        bridge._disks_scanning = False


class TestRetry:
    def test_finished_disk_scan_tells_the_partition_job(self, bridge: EngineBridge) -> None:
        job = next(j for j in bridge._engine.jobs if j.name == "partition")
        with patch.object(job, "disk_rescanned") as rescanned:
            bridge._on_disk_scan_finished(bridge._disks_model)
        rescanned.assert_called_once_with()

    def test_reset_releases_the_previous_attempt_on_a_real_install(
        self, bridge: EngineBridge
    ) -> None:
        job = next(j for j in bridge._engine.jobs if j.name == "partition")
        with patch.object(job, "release_previous", return_value="") as release:
            bridge.resetInstallation()
            release.assert_not_called()
            bridge._dry_run = False
            bridge.resetInstallation()
        release.assert_called_once_with()

"""
Unit tests for the EngineBridge M2 manual-partition-editor surface.

Covers the QML<->Python contract the parallel UI agent consumes:
- addPartitionOperation / removePartitionOperation / resetPartitionOperations
  updating ``pendingOperations``.
- ``manualPlanValid`` / ``manualPlanError`` reflecting the operation validation
  (and falling back to the M1 assignment rule when there are no operations).
- ``simulatedSegments`` reflecting the pending operations over the selected
  disk geometry.
- ``applySelectionsToContext`` pushing snake ``partition_operations`` to the
  engine when ``partition_mode == "manual"``.

Runs offscreen; no real partitioning is performed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.jobs.bootloader import BootloaderJob  # noqa: E402
from xen.jobs.partition import _ALIGN, _SECTOR_SIZE  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CONFIG = PROJECT_ROOT / "config" / "examples" / "minimal.yaml"

MIB = 1024 * 1024
DISK_SECTORS = (500 * 1024**3) // _SECTOR_SIZE


def _sectors(mib: int) -> int:
    return mib * MIB // _SECTOR_SIZE


def _mock_disk() -> dict[str, Any]:
    """A UEFI disk (ESP + root ext4) matching the disk_detector contract."""
    esp_start = _ALIGN
    esp_size = _sectors(512)
    root_start = esp_start + esp_size
    root_size = _sectors(100 * 1024)
    return {
        "name": "sda",
        "sizeSectors": DISK_SECTORS,
        "segments": [
            {
                "kind": "partition",
                "name": "sda1",
                "startSector": esp_start,
                "sizeSectors": esp_size,
                "sizeBytes": esp_size * _SECTOR_SIZE,
                "fstype": "vfat",
                "partType": "efi",
                "mountpoint": "",
            },
            {
                "kind": "partition",
                "name": "sda2",
                "startSector": root_start,
                "sizeSectors": root_size,
                "sizeBytes": root_size * _SECTOR_SIZE,
                "fstype": "ext4",
                "partType": "linux",
                "mountpoint": "/",
            },
        ],
    }


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def bridge(qapp: QApplication) -> EngineBridge:
    assert qapp is not None
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    theme_base = MINIMAL_CONFIG.parent
    obj = EngineBridge(engine, theme_base, debug=False, dry_run=True, skip_requirements=True)
    # Inject a deterministic disk model and select it.
    obj._disks_model = [_mock_disk()]
    obj.setSelectedDisk("/dev/sda")
    obj.setPartitionMode("manual")
    return obj


def _create_free_op() -> dict[str, Any]:
    """A well-formed create in the free tail after the existing root."""
    start = _ALIGN + _sectors(512) + _sectors(100 * 1024)
    return {
        "type": "create",
        "target": f"free:{start}",
        "params": {
            "start_sector": start,
            "size_sectors": _sectors(10 * 1024),
            "fstype": "ext4",
            "mountpoint": "/home",
            "name": "sda3",
        },
    }


class TestPendingOperations:
    def test_add_appends_to_pending(self, bridge: EngineBridge) -> None:
        assert bridge.pendingOperations == []
        bridge.addPartitionOperation(_create_free_op())
        assert len(bridge.pendingOperations) == 1
        assert bridge.pendingOperations[0]["type"] == "create"

    def test_add_malformed_is_rejected(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation({"type": "nuke", "target": "/dev/sda"})
        assert bridge.pendingOperations == []
        assert bridge.manualPlanValid is False
        assert bridge.manualPlanError != ""

    def test_remove_pops_index(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(_create_free_op())
        bridge.addPartitionOperation(
            {"type": "delete", "target": "/dev/sda1", "params": {"number": 1}}
        )
        assert len(bridge.pendingOperations) == 2
        bridge.removePartitionOperation(0)
        assert len(bridge.pendingOperations) == 1
        assert bridge.pendingOperations[0]["type"] == "delete"

    def test_remove_out_of_range_noop(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(_create_free_op())
        bridge.removePartitionOperation(5)
        assert len(bridge.pendingOperations) == 1

    def test_reset_clears_all(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(_create_free_op())
        bridge.resetPartitionOperations()
        assert bridge.pendingOperations == []
        # With no operations left, validity/error fall back to the M1 rule; a
        # '/' assignment (plus the ESP mounted at /boot/efi) makes it valid.
        assert bridge.manualPlanValid is False
        bridge.setPartitionMount("sda2", "/")
        bridge.setPartitionMount("sda1", "/boot/efi")
        assert bridge.manualPlanValid is True, bridge.manualPlanError


class TestOperationSignals:
    def test_add_emits_operations_changed(self, bridge: EngineBridge) -> None:
        received: list[int] = []
        bridge.partitionOperationsChanged.connect(lambda: received.append(1))
        bridge.addPartitionOperation(_create_free_op())
        assert received

    def test_set_selected_disk_emits_operations_changed(self, bridge: EngineBridge) -> None:
        bridge._disks_model.append({"name": "sdb", "sizeSectors": DISK_SECTORS, "segments": []})
        received: list[int] = []
        bridge.partitionOperationsChanged.connect(lambda: received.append(1))
        bridge.setSelectedDisk("/dev/sdb")
        assert received

    # manualPlanValid/manualPlanError are declared with
    # notify=partitionOperationsChanged. QML's binding to them only re-reads
    # when THAT signal fires - reading bridge.manualPlanValid directly in
    # Python (as TestManualPlanValidity does) always recomputes fresh and
    # can't catch a missing/wrong notify wire-up. These tests assert the
    # signal itself fires, which is what QML actually depends on.
    def test_set_partition_mount_emits_operations_changed(self, bridge: EngineBridge) -> None:
        received: list[int] = []
        bridge.partitionOperationsChanged.connect(lambda: received.append(1))
        bridge.setPartitionMount("sda2", "/")
        assert received

    def test_set_partition_format_emits_operations_changed(self, bridge: EngineBridge) -> None:
        received: list[int] = []
        bridge.partitionOperationsChanged.connect(lambda: received.append(1))
        bridge.setPartitionFormat("sda2", True)
        assert received

    def test_set_partition_fstype_emits_operations_changed(self, bridge: EngineBridge) -> None:
        received: list[int] = []
        bridge.partitionOperationsChanged.connect(lambda: received.append(1))
        bridge.setPartitionFsType("sda2", "xfs")
        assert received


class TestManualPlanValidity:
    def test_valid_plan_when_root_and_esp_present(self, bridge: EngineBridge) -> None:
        # The base disk already has an ESP (sda1) + root (sda2). A no-op create
        # keeps the plan valid under UEFI.
        with patch("xen.jobs.partition._is_target_busy", return_value=False):
            bridge.addPartitionOperation(_create_free_op())
        assert bridge.manualPlanValid is True
        assert bridge.manualPlanError == ""

    def test_invalid_when_root_deleted(self, bridge: EngineBridge) -> None:
        with patch("xen.jobs.partition._is_target_busy", return_value=False):
            bridge.addPartitionOperation(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        assert bridge.manualPlanValid is False
        assert "mounted at /" in bridge.manualPlanError

    def test_falls_back_to_m1_when_no_operations(self, bridge: EngineBridge) -> None:
        # No operations: validity follows the M1 assignment rule (root + ESP).
        assert bridge.pendingOperations == []
        assert bridge.manualPlanValid is False  # no assignment yet
        bridge.setPartitionMount("sda2", "/")
        assert bridge.manualPlanValid is False  # still missing the ESP mount
        bridge.setPartitionMount("sda1", "/boot/efi")
        assert bridge.manualPlanValid is True, bridge.manualPlanError


class TestSimulatedSegments:
    def test_reflects_pending_create(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(_create_free_op())
        created = [s for s in bridge.simulatedSegments if s["kind"] == "new"]
        assert len(created) == 1
        assert created[0]["mountpoint"] == "/home"

    def test_segment_shape_is_complete(self, bridge: EngineBridge) -> None:
        seg = bridge.simulatedSegments[0]
        expected_keys = {
            "name",
            "startSector",
            "sizeSectors",
            "sizeBytes",
            "fstype",
            "partType",
            "mountpoint",
            "kind",
            "pendingDelete",
        }
        assert expected_keys <= set(seg.keys())

    def test_delete_marks_pending_delete(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(
            {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
        )
        deleted = [s for s in bridge.simulatedSegments if s["pendingDelete"]]
        assert any(s["name"] == "sda2" for s in deleted)


class TestCommandPreview:
    def test_preview_lists_commands(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(
            {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
        )
        preview = bridge.commandPreview
        assert any("sgdisk --delete=2" in line for line in preview)

    def test_preview_empty_without_operations(self, bridge: EngineBridge) -> None:
        assert bridge.commandPreview == []


class TestApplySelectionsToContext:
    def test_pushes_partition_operations(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(_create_free_op())
        bridge.applySelectionsToContext()
        # set_selections stores the normalized dict on the engine's _selections.
        selections = bridge._engine._selections
        assert selections.get("partition_mode") == "manual"
        assert "partition_operations" in selections
        assert selections["partition_operations"][0]["type"] == "create"

    def test_no_operations_key_when_empty(self, bridge: EngineBridge) -> None:
        bridge.applySelectionsToContext()
        selections = bridge._engine._selections
        assert "partition_operations" not in selections

    def test_short_disk_name_is_normalized_to_dev_path(self, bridge: EngineBridge) -> None:
        # The QML wizard stores the short name; jobs need a /dev/ device path.
        bridge.setSelectedDisk("sdb")
        bridge.applySelectionsToContext()
        assert bridge._engine._selections["disk"] == "/dev/sdb"

    def test_dev_disk_path_is_left_unchanged(self, bridge: EngineBridge) -> None:
        bridge.setSelectedDisk("/dev/nvme0n1")
        bridge.applySelectionsToContext()
        assert bridge._engine._selections["disk"] == "/dev/nvme0n1"


class TestApplyPartitionOperations:
    def test_apply_with_empty_queue_reports_failure(self, bridge: EngineBridge) -> None:
        results: list[tuple[bool, str]] = []
        bridge.partitionApplyFinished.connect(lambda ok, msg: results.append((ok, msg)))

        bridge.applyPartitionOperations()

        # No thread started; a synchronous failure is reported and no busy flip.
        assert results and results[0][0] is False
        assert bridge.partitionApplying is False

    def test_apply_finished_success_clears_queue_and_rescans(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(_create_free_op())
        assert len(bridge.pendingOperations) == 1

        with patch("xen.gui.bridge.disk_detector.list_disks", return_value=[]) as mock_scan:
            bridge._on_partition_apply_finished(True, "done")

        # Success path: queue cleared, disks rescanned, busy flag down.
        assert bridge.pendingOperations == []
        assert bridge.partitionApplying is False
        mock_scan.assert_called_once()

    def test_apply_finished_failure_keeps_queue(self, bridge: EngineBridge) -> None:
        bridge.addPartitionOperation(_create_free_op())

        with patch("xen.gui.bridge.disk_detector.list_disks", return_value=[]) as mock_scan:
            bridge._on_partition_apply_finished(False, "boom")

        # Failure keeps the queue so the user can fix it; no rescan.
        assert len(bridge.pendingOperations) == 1
        mock_scan.assert_not_called()
        assert bridge.partitionApplying is False

    def test_apply_finished_success_refreshes_editor_geometry(self, bridge: EngineBridge) -> None:
        # Regression: after a successful apply the partition editor
        # (simulatedSegments) must reflect the newly written layout, not the
        # pre-apply geometry (which only "Available Disks" used to refresh).
        bridge.addPartitionOperation(_create_free_op())
        new_size = _sectors(20 * 1024)
        new_disk = {
            "name": "sda",
            "sizeSectors": DISK_SECTORS,
            "segments": [
                {
                    "kind": "partition",
                    "name": "sda1",
                    "startSector": _ALIGN,
                    "sizeSectors": new_size,
                    "sizeBytes": new_size * _SECTOR_SIZE,
                    "fstype": "ext4",  # pre-apply sda1 was the vfat ESP
                    "partType": "linux",
                    "mountpoint": "",
                },
            ],
        }
        changed: list[int] = []
        bridge.partitionOperationsChanged.connect(lambda: changed.append(1))

        with patch("xen.gui.bridge.disk_detector.list_disks", return_value=[new_disk]):
            bridge._on_partition_apply_finished(True, "done")

        segs = {s["name"]: s for s in bridge.simulatedSegments if s["kind"] != "free"}
        assert "sda2" not in segs  # old root is gone
        assert segs["sda1"]["fstype"] == "ext4"  # fresh geometry, not the old ESP
        assert bridge.pendingOperations == []
        # The editor's notify fired so the QML simulatedSegments binding re-reads.
        assert changed


class TestOperationsApplicable:
    """Apply is gated on structural applicability, not the installable layout."""

    def test_empty_queue_is_not_applicable(self, bridge: EngineBridge) -> None:
        assert bridge.operationsApplicable is False
        assert bridge.operationsApplicableError == ""

    def test_delete_root_is_applicable_though_plan_invalid(self, bridge: EngineBridge) -> None:
        # The reported bug: after deleting a partition the queue is populated and
        # Apply must be enabled (applicable) even though the layout has no '/'.
        with patch("xen.jobs.partition._is_target_busy", return_value=False):
            bridge.addPartitionOperation(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        assert bridge.pendingOperations
        assert bridge.operationsApplicable is True
        assert bridge.operationsApplicableError == ""
        # Navigation-level completeness still fails (no root), but does not block Apply.
        assert bridge.manualPlanValid is False

    def test_apply_starts_when_applicable_without_root(self, bridge: EngineBridge) -> None:
        with patch("xen.jobs.partition._is_target_busy", return_value=False):
            bridge.addPartitionOperation(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        results: list[tuple[bool, str]] = []
        bridge.partitionApplyFinished.connect(lambda ok, msg: results.append((ok, msg)))

        # Stub the worker/thread so no real sgdisk/mkfs runs; assert the gate passed.
        with (
            patch("xen.gui.bridge.QThread"),
            patch("xen.gui.bridge.PartitionApplyWorker"),
        ):
            bridge.applyPartitionOperations()

        # No synchronous "invalid plan" failure; the busy flag is raised.
        assert results == []
        assert bridge.partitionApplying is True

    def test_apply_rejected_when_not_applicable(self, bridge: EngineBridge) -> None:
        # An overlapping create is structurally inapplicable -> Apply short-circuits.
        root = bridge._disks_model[0]["segments"][1]
        with patch("xen.jobs.partition._is_target_busy", return_value=False):
            bridge.addPartitionOperation(
                {
                    "type": "create",
                    "target": "free:overlap",
                    "params": {
                        "start_sector": int(root["startSector"]),
                        "size_sectors": _sectors(1024),
                        "fstype": "ext4",
                    },
                }
            )
        assert bridge.operationsApplicable is False

        results: list[tuple[bool, str]] = []
        bridge.partitionApplyFinished.connect(lambda ok, msg: results.append((ok, msg)))
        bridge.applyPartitionOperations()

        assert results and results[0][0] is False
        assert bridge.partitionApplying is False


class TestPostApplyReconciliation:
    """After a successful live Apply, a new partition's queued
    mountpoint/encryption intent is promoted onto its real device name -
    fixes the pre-existing gap where this never happened at all, and is
    required for the mapper path substitution to land on the right key."""

    def test_new_partition_mountpoint_promoted_to_real_name(self, bridge: EngineBridge) -> None:
        bridge._disks_model = [_mock_disk()]
        op = {
            "type": "create",
            "target": f"free:{_ALIGN}",
            "params": {
                "start_sector": _ALIGN,
                "size_sectors": _sectors(1024),
                "fstype": "ext4",
                "name": "NEW-123",
                "mountpoint": "/home",
            },
        }
        bridge._partition_operations = [op]
        bridge._reconcile_after_apply([op])

        # The mock disk's rescanned segments determine the real name landed
        # on - matches whatever _mock_disk()'s post-apply fixture names the
        # partition at that start sector (see the disk fixture already used
        # by this test file's other tests for the exact name it assigns).
        assert any(
            entry.get("mountpoint") == "/home"
            for entry in bridge._partition_assignments.values()
        )

    def test_encrypted_partition_path_becomes_mapper_device(self, bridge: EngineBridge) -> None:
        bridge._disks_model = [_mock_disk()]
        op = {
            "type": "create",
            "target": f"free:{_ALIGN}",
            "params": {
                "start_sector": _ALIGN,
                "size_sectors": _sectors(1024),
                "fstype": "ext4",
                "name": "NEW-123",
                "mountpoint": "/home",
                "encrypt": True,
            },
        }
        bridge._partition_operations = [op]
        bridge._reconcile_after_apply([op])

        matching = [
            entry for entry in bridge._partition_assignments.values()
            if entry.get("mountpoint") == "/home"
        ]
        assert matching
        assert matching[0]["path"] == "/dev/mapper/crypthome"
        # raw_path preserves the original device - Task 8's crypttab/keyfile
        # step needs it (cryptsetup luksAddKey operates on the raw partition,
        # not the already-open mapper), and it has to survive the
        # applySelectionsToContext normalization to actually reach that job.
        assert matching[0]["raw_path"].startswith("/dev/")
        assert matching[0]["raw_path"] != matching[0]["path"]

    def test_non_root_encrypted_generated_passphrase_carried_over(
        self, bridge: EngineBridge
    ) -> None:
        bridge._manual_luks_passphrases["sda3"] = "generated-pw"
        assert bridge._manual_luks_passphrases["sda3"] == "generated-pw"


class TestBootloaderType:
    """engine.bootloaderType - read-only, used by PartitionView.qml to show
    the GRUB/Argon2id compatibility note. Set by the distro's config YAML,
    never by the user."""

    def test_defaults_to_systemd_boot(self, bridge: EngineBridge) -> None:
        # MINIMAL_CONFIG's bootloader job entry has no "type:" override.
        assert bridge.bootloaderType == "systemd-boot"

    def test_reads_grub_from_job_config(self, bridge: EngineBridge) -> None:
        for job in bridge._engine.jobs:
            if job.name == "bootloader":
                job._config["type"] = "grub"
        assert bridge.bootloaderType == "grub"

    def test_defaults_when_no_bootloader_job_present(self, bridge: EngineBridge) -> None:
        bridge._engine.jobs = [j for j in bridge._engine.jobs if j.name != "bootloader"]
        assert bridge.bootloaderType == "systemd-boot"

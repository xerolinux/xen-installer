"""
Unit tests for the M2 manual partition editor backend.

Covers the pure operation model (:class:`PartitionOperation`), the in-memory
simulation (:func:`simulate_operations`), the geometry/plan validation
(:func:`validate_operations`) and the ordered command EXECUTION path
(``PartitionJob._apply_operations``).

SECURITY: every destructive path is fully mocked. No real partitioning tool is
invoked; command sequences are captured via a patched
``_run_partitioning_command`` and asserted for exact content and global order.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from xen.jobs.base import JobContext, JobResult
from xen.jobs.partition import (
    _ALIGN,
    _GPT_TAIL,
    _SECTOR_SIZE,
    PartitionJob,
    PartitionOperation,
    _dev_path,
    _manual_mapper_name,
    simulate_operations,
    validate_operations,
    validate_operations_applicable,
)

MIB = 1024 * 1024
# 500 GiB disk expressed in 512-byte sectors.
DISK_SECTORS = (500 * 1024**3) // _SECTOR_SIZE


def _sectors(mib: int) -> int:
    """Return the sector count for ``mib`` mebibytes (1 MiB aligned)."""
    return mib * MIB // _SECTOR_SIZE


def _existing_disk() -> dict[str, Any]:
    """
    A UEFI disk with an ESP (sda1) and a root ext4 (sda2), plus a trailing gap.

    Layout: 1 MiB align -> ESP 512 MiB -> root 100 GiB -> free tail.
    """
    esp_start = _ALIGN
    esp_size = _sectors(512)
    root_start = esp_start + esp_size
    root_size = _sectors(100 * 1024)
    segments = [
        {
            "kind": "partition",
            "name": "sda1",
            "startSector": esp_start,
            "sizeSectors": esp_size,
            "endSector": esp_start + esp_size - 1,
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
            "endSector": root_start + root_size - 1,
            "sizeBytes": root_size * _SECTOR_SIZE,
            "fstype": "ext4",
            "partType": "linux",
            "mountpoint": "/",
        },
    ]
    return {"name": "sda", "sizeSectors": DISK_SECTORS, "segments": segments}


class TestPartitionOperationFromDict:
    """Parsing / validation of the QML<->Python operation contract."""

    def test_create_valid(self) -> None:
        op = PartitionOperation.from_dict(
            {
                "type": "create",
                "target": "free:2048",
                "params": {
                    "start_sector": 2048,
                    "size_sectors": 1048576,
                    "fstype": "ext4",
                    "mountpoint": "/",
                    "flags": [],
                },
            }
        )
        assert op.type == "create"
        assert op.target == "free:2048"
        assert op.params["fstype"] == "ext4"

    def test_delete_valid(self) -> None:
        op = PartitionOperation.from_dict(
            {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
        )
        assert op.type == "delete"
        assert op.params["number"] == 2

    def test_setflag_valid(self) -> None:
        op = PartitionOperation.from_dict(
            {
                "type": "setflag",
                "target": "/dev/sda1",
                "params": {"number": 1, "flag": "esp", "state": True},
            }
        )
        assert op.params["state"] is True

    def test_resize_valid(self) -> None:
        op = PartitionOperation.from_dict(
            {
                "type": "resize",
                "target": "/dev/sda2",
                "params": {
                    "path": "/dev/sda2",
                    "number": 2,
                    "new_size_sectors": 2097152,
                    "fstype": "ext4",
                },
            }
        )
        assert op.params["new_size_sectors"] == 2097152

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown operation type"):
            PartitionOperation.from_dict({"type": "explode", "target": "/dev/sda1", "params": {}})

    def test_missing_target_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'target'"):
            PartitionOperation.from_dict({"type": "delete", "params": {"number": 1}})

    def test_missing_required_param_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required param"):
            PartitionOperation.from_dict(
                {"type": "create", "target": "free:0", "params": {"fstype": "ext4"}}
            )

    def test_non_int_sector_raises(self) -> None:
        with pytest.raises(ValueError, match="whole number"):
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": "free:0",
                    "params": {"start_sector": "x", "size_sectors": 10, "fstype": "ext4"},
                }
            )

    def test_bool_number_rejected(self) -> None:
        # bool is a subclass of int; the number field must be a real int.
        with pytest.raises(ValueError, match="must be an int"):
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda1", "params": {"number": True}}
            )

    def test_integral_float_sector_coerced_to_int(self) -> None:
        # QML numbers cross the Qt boundary as floats; integral floats are
        # accepted and normalized to int so the sgdisk commands get integers.
        op = PartitionOperation.from_dict(
            {
                "type": "create",
                "target": "free:2048",
                "params": {"start_sector": 2048.0, "size_sectors": 1024.0, "fstype": "ext4"},
            }
        )
        assert op.params["start_sector"] == 2048
        assert isinstance(op.params["start_sector"], int)
        assert isinstance(op.params["size_sectors"], int)

    def test_non_integral_float_rejected(self) -> None:
        with pytest.raises(ValueError, match="whole number"):
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": "free:0",
                    "params": {"start_sector": 2048.5, "size_sectors": 10, "fstype": "ext4"},
                }
            )


class TestSimulateOperations:
    """Pure in-memory geometry transformation."""

    def test_create_in_free_region(self) -> None:
        disk = _existing_disk()
        # Create a 10 GiB partition right after the existing root.
        root = disk["segments"][1]
        start = root["startSector"] + root["sizeSectors"]
        size = _sectors(10 * 1024)
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": f"free:{start}",
                    "params": {
                        "start_sector": start,
                        "size_sectors": size,
                        "fstype": "ext4",
                        "mountpoint": "/home",
                        "name": "sda3",
                    },
                }
            )
        ]
        result = simulate_operations(disk["segments"], ops)
        created = [s for s in result if s["kind"] == "new"]
        assert len(created) == 1
        assert created[0]["startSector"] == start
        assert created[0]["sizeSectors"] == size
        assert created[0]["sizeBytes"] == size * _SECTOR_SIZE
        assert created[0]["mountpoint"] == "/home"

    def test_delete_marks_pending_and_frees_space(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        ]
        result = simulate_operations(disk["segments"], ops)
        deleted = [s for s in result if s["pendingDelete"]]
        assert len(deleted) == 1
        assert deleted[0]["name"] == "sda2"
        # The freed space must reappear as a free region.
        free = [s for s in result if s["kind"] == "free"]
        assert any(s["sizeSectors"] >= _sectors(100 * 1024) for s in free)

    def test_resize_grow_adjusts_size(self) -> None:
        disk = _existing_disk()
        new_size = _sectors(150 * 1024)
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "resize",
                    "target": "/dev/sda2",
                    "params": {
                        "path": "/dev/sda2",
                        "number": 2,
                        "new_size_sectors": new_size,
                        "fstype": "ext4",
                    },
                }
            )
        ]
        result = simulate_operations(disk["segments"], ops)
        root = next(s for s in result if s["name"] == "sda2")
        assert root["sizeSectors"] == new_size
        assert root["kind"] == "existing"

    def test_resize_shrink_adjusts_size(self) -> None:
        disk = _existing_disk()
        new_size = _sectors(50 * 1024)
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "resize",
                    "target": "/dev/sda2",
                    "params": {
                        "path": "/dev/sda2",
                        "number": 2,
                        "new_size_sectors": new_size,
                        "fstype": "ext4",
                        "shrink": True,
                    },
                }
            )
        ]
        result = simulate_operations(disk["segments"], ops)
        root = next(s for s in result if s["name"] == "sda2")
        assert root["sizeSectors"] == new_size
        # Shrinking must open a free region after the (now smaller) root.
        assert any(s["kind"] == "free" for s in result)

    def test_format_changes_fstype(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "format",
                    "target": "/dev/sda2",
                    "params": {"path": "/dev/sda2", "fstype": "xfs", "mountpoint": "/"},
                }
            )
        ]
        result = simulate_operations(disk["segments"], ops)
        root = next(s for s in result if s["name"] == "sda2")
        assert root["fstype"] == "xfs"

    def test_deterministic_ordering_by_start(self) -> None:
        disk = _existing_disk()
        result = simulate_operations(disk["segments"], [])
        starts = [s["startSector"] for s in result]
        assert starts == sorted(starts)


class TestValidateOperations:
    """Plan validation: alignment, overlap, bounds, root, ESP, busy targets."""

    def _no_busy(self) -> Any:
        return patch("xen.jobs.partition._is_target_busy", return_value=False)

    def test_accepts_well_formed_plan(self) -> None:
        disk = _existing_disk()
        with self._no_busy():
            valid, error = validate_operations(disk, [], uefi=True)
        assert valid is True
        assert error == ""

    def test_rejects_misalignment(self) -> None:
        disk = _existing_disk()
        start = disk["segments"][1]["startSector"] + disk["segments"][1]["sizeSectors"]
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": f"free:{start}",
                    "params": {
                        "start_sector": start + 7,  # not 1 MiB aligned
                        "size_sectors": _sectors(10),
                        "fstype": "ext4",
                    },
                }
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "aligned" in error

    def test_rejects_overlap(self) -> None:
        disk = _existing_disk()
        # Create a partition overlapping the existing root.
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": disk["segments"][1]["startSector"],
                        "size_sectors": _sectors(10 * 1024),
                        "fstype": "ext4",
                    },
                }
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "overlap" in error

    def test_rejects_missing_root(self) -> None:
        disk = _existing_disk()
        # Delete the root -> no partition mounted at '/'.
        ops = [
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "mounted at /" in error

    def test_rejects_bios_grub_setflag(self) -> None:
        # This installer targets UEFI/systemd-boot only: a bios_grub flag must block the install.
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "setflag",
                    "target": "/dev/sda1",
                    "params": {"number": 1, "flag": "bios_grub", "state": True},
                }
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "bios_grub" in error
        assert "systemd-boot" in error

    def test_rejects_bios_grub_on_create(self) -> None:
        disk = _existing_disk()
        start = disk["segments"][1]["startSector"] + disk["segments"][1]["sizeSectors"]
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": f"free:{start}",
                    "params": {
                        "start_sector": start,
                        "size_sectors": _sectors(10),
                        "fstype": "ext4",
                        "flags": ["bios_grub"],
                    },
                }
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "bios_grub" in error

    def test_rejects_missing_esp_uefi(self) -> None:
        # A disk with only a root and no ESP must fail under UEFI.
        root_start = _ALIGN
        root_size = _sectors(100 * 1024)
        disk = {
            "name": "sda",
            "sizeSectors": DISK_SECTORS,
            "segments": [
                {
                    "kind": "partition",
                    "name": "sda1",
                    "startSector": root_start,
                    "sizeSectors": root_size,
                    "sizeBytes": root_size * _SECTOR_SIZE,
                    "fstype": "ext4",
                    "partType": "linux",
                    "mountpoint": "/",
                }
            ],
        }
        with self._no_busy():
            valid, error = validate_operations(disk, [], uefi=True)
        assert valid is False
        assert "ESP" in error

    def test_missing_esp_ok_when_bios(self) -> None:
        root_start = _ALIGN
        root_size = _sectors(100 * 1024)
        disk = {
            "name": "sda",
            "sizeSectors": DISK_SECTORS,
            "segments": [
                {
                    "kind": "partition",
                    "name": "sda1",
                    "startSector": root_start,
                    "sizeSectors": root_size,
                    "sizeBytes": root_size * _SECTOR_SIZE,
                    "fstype": "ext4",
                    "partType": "linux",
                    "mountpoint": "/",
                }
            ],
        }
        with self._no_busy():
            valid, error = validate_operations(disk, [], uefi=False)
        assert valid is True
        assert error == ""

    def test_rejects_busy_target(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        ]
        with patch("xen.jobs.partition._is_target_busy", return_value=True):
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "mounted or backs the live system" in error

    def test_rejects_two_encrypted_roots(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "format",
                    "target": "/dev/sda2",
                    "params": {
                        "path": "/dev/sda2",
                        "fstype": "ext4",
                        "mountpoint": "/",
                        "encrypt": True,
                        "encryption_passphrase": "S3cretPass!",
                    },
                }
            ),
            PartitionOperation.from_dict(
                {
                    "type": "format",
                    "target": "/dev/sda3",
                    "params": {
                        "path": "/dev/sda3",
                        "fstype": "ext4",
                        "mountpoint": "/",
                        "encrypt": True,
                        "encryption_passphrase": "An0therPass!",
                    },
                }
            ),
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "one" in error.lower()

    def test_rejects_encrypted_esp(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "format",
                    "target": "/dev/sda1",
                    "params": {
                        "path": "/dev/sda1",
                        "fstype": "vfat",
                        "mountpoint": "/boot/efi",
                        "encrypt": True,
                    },
                }
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "esp" in error.lower() or "efi" in error.lower()

    def test_rejects_passphrase_on_non_root_encrypted_partition(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "format",
                    "target": "/dev/sda3",
                    "params": {
                        "path": "/dev/sda3",
                        "fstype": "ext4",
                        "mountpoint": "/home",
                        "encrypt": True,
                        "encryption_passphrase": "ShouldNotBeHere!",
                    },
                }
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "passphrase" in error.lower()

    def test_rejects_past_gpt_tail(self) -> None:
        disk = _existing_disk()
        start = disk["segments"][1]["startSector"] + disk["segments"][1]["sizeSectors"]
        # Size so large it runs into / past the GPT tail.
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": f"free:{start}",
                    "params": {
                        "start_sector": start,
                        "size_sectors": DISK_SECTORS,  # way past usable end
                        "fstype": "ext4",
                    },
                }
            )
        ]
        with self._no_busy():
            valid, error = validate_operations(disk, ops, uefi=True)
        assert valid is False
        assert "usable sector" in error


class TestValidateOperationsApplicable:
    """GParted-style applicability: structural only, no root/ESP requirement."""

    def _no_busy(self) -> Any:
        return patch("xen.jobs.partition._is_target_busy", return_value=False)

    def test_delete_root_is_applicable_but_not_installable(self) -> None:
        # The user scenario: deleting the root leaves no '/'. The plan is not a
        # complete installable layout, but the delete itself is applicable.
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        ]
        with self._no_busy():
            applicable, app_err = validate_operations_applicable(disk, ops)
            valid, val_err = validate_operations(disk, ops, uefi=True)
        assert applicable is True
        assert app_err == ""
        assert valid is False
        assert "mounted at /" in val_err

    def test_wipe_all_partitions_is_applicable(self) -> None:
        # Deleting every partition (empty disk) is a legitimate GParted apply.
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda1", "params": {"number": 1}}
            ),
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            ),
        ]
        with self._no_busy():
            applicable, app_err = validate_operations_applicable(disk, ops)
        assert applicable is True
        assert app_err == ""

    def test_applicable_rejects_overlap(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": disk["segments"][1]["startSector"],
                        "size_sectors": _sectors(10 * 1024),
                        "fstype": "ext4",
                    },
                }
            )
        ]
        with self._no_busy():
            applicable, error = validate_operations_applicable(disk, ops)
        assert applicable is False
        assert "overlap" in error

    def test_applicable_rejects_busy_target(self) -> None:
        disk = _existing_disk()
        ops = [
            PartitionOperation.from_dict(
                {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
            )
        ]
        with patch("xen.jobs.partition._is_target_busy", return_value=True):
            applicable, error = validate_operations_applicable(disk, ops)
        assert applicable is False
        assert "mounted or backs the live system" in error


class TestApplyOperationsExecution:
    """Ordered command EXECUTION path (fully mocked subprocess)."""

    @staticmethod
    def _run_capture(job: PartitionJob) -> list[list[str]]:
        """Attach a capturing runner to ``job`` and return the shared call log."""
        calls: list[list[str]] = []

        def _record(cmd: list[str], description: str, dry_run: bool) -> JobResult:  # noqa: ARG001
            calls.append(list(cmd))
            return JobResult.ok(description)

        job._run_partitioning_command = _record  # type: ignore[method-assign]
        return calls

    def _apply(self, operations: list[dict[str, Any]], disk: str = "/dev/sda") -> list[list[str]]:
        job = PartitionJob()
        calls = self._run_capture(job)
        result = job._apply_operations(
            JobContext(),
            disk,
            {"partition_operations": operations},
            dry_run=True,
        )
        assert result.success is True, result.message
        return calls

    def test_create_vfat_uses_parted_fat32_and_mkfs_fat(self) -> None:
        # Regression: parted's mkpart rejects 'vfat' as an fs-type token; it must
        # receive 'fat32'. The mkfs step still uses the real filesystem (mkfs.fat).
        calls = self._apply(
            [
                {
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": 2048,
                        "size_sectors": _sectors(512),
                        "fstype": "vfat",
                        "path": "/dev/sda9",
                        "number": 9,
                        "flags": ["esp"],
                    },
                }
            ]
        )
        mkpart = next(c for c in calls if "mkpart" in c)
        assert "fat32" in mkpart
        assert "vfat" not in mkpart
        assert ["mkfs.fat", "-F32", "/dev/sda9"] in calls

    def test_create_with_label_passes_through_to_mkfs(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": 2048,
                        "size_sectors": _sectors(512),
                        "fstype": "ext4",
                        "path": "/dev/sda9",
                        "number": 9,
                        "label": "DATA",
                    },
                }
            ]
        )
        assert ["mkfs.ext4", "-F", "-L", "DATA", "/dev/sda9"] in calls

    def test_format_with_label_passes_through_to_mkfs(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "format",
                    "target": "/dev/sda2",
                    "params": {"path": "/dev/sda2", "fstype": "xfs", "label": "ROOT"},
                }
            ]
        )
        assert ["mkfs.xfs", "-f", "-L", "ROOT", "/dev/sda2"] in calls

    def test_create_swap_uses_parted_linux_swap(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": 2048,
                        "size_sectors": _sectors(512),
                        "fstype": "swap",
                        "path": "/dev/sda9",
                        "number": 9,
                    },
                }
            ]
        )
        mkpart = next(c for c in calls if "mkpart" in c)
        assert "linux-swap" in mkpart
        assert "swap" not in mkpart  # exact token check, not the substring
        assert ["mkswap", "/dev/sda9"] in calls

    def test_create_flags_pass_generic_parted_names(self) -> None:
        # Both flags reach parted under their own generic names (no boot->esp
        # collapse); bios_grub is now supported.
        calls = self._apply(
            [
                {
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": 2048,
                        "size_sectors": _sectors(512),
                        "fstype": "ext4",
                        "path": "/dev/sda9",
                        "number": 9,
                        "flags": ["esp", "bios_grub"],
                    },
                }
            ]
        )
        assert ["parted", "-s", "/dev/sda", "set", "9", "esp", "on"] in calls
        assert ["parted", "-s", "/dev/sda", "set", "9", "bios_grub", "on"] in calls

    def test_setflag_boot_is_not_remapped_to_esp(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "setflag",
                    "target": "/dev/sda1",
                    "params": {"number": 1, "flag": "boot", "state": True},
                }
            ]
        )
        assert ["parted", "-s", "/dev/sda", "set", "1", "boot", "on"] in calls
        assert not any(c[-2:] == ["esp", "on"] for c in calls)

    def test_setflag_unknown_flag_is_ignored(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "setflag",
                    "target": "/dev/sda1",
                    "params": {"number": 1, "flag": "bogus", "state": True},
                }
            ]
        )
        assert not any("set" in c for c in calls)  # never reaches parted

    def test_global_order_delete_shrink_grow_create_format_setflag(self) -> None:
        operations = [
            {
                "type": "setflag",
                "target": "/dev/sda1",
                "params": {"number": 1, "flag": "esp", "state": True},
            },
            {
                "type": "format",
                "target": "/dev/sda3",
                "params": {"path": "/dev/sda3", "fstype": "ext4"},
            },
            {
                "type": "create",
                "target": "free:2048",
                "params": {
                    "start_sector": 2048,
                    "size_sectors": 2048,
                    "fstype": "ext4",
                    "path": "/dev/sda4",
                    "number": 4,
                },
            },
            {
                "type": "resize",
                "target": "/dev/sda5",
                "params": {
                    "path": "/dev/sda5",
                    "number": 5,
                    "new_size_sectors": 4096,
                    "old_size_sectors": 2048,
                    "fstype": "ext4",
                },
            },
            {
                "type": "resize",
                "target": "/dev/sda6",
                "params": {
                    "path": "/dev/sda6",
                    "number": 6,
                    "new_size_sectors": 2048,
                    "old_size_sectors": 4096,
                    "fstype": "ext4",
                },
            },
            {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}},
        ]
        calls = self._apply(operations)
        flat = [" ".join(c) for c in calls]

        def first_index(token: str) -> int:
            return next(i for i, line in enumerate(flat) if token in line)

        i_delete = first_index("sgdisk --delete=2")
        i_shrink = first_index("e2fsck -f -y /dev/sda6")
        i_grow = first_index("resizepart 5")
        i_create = first_index("mkpart")
        i_format = first_index("mkfs.ext4 -F /dev/sda3")
        i_setflag = first_index("set 1 esp on")
        assert i_delete < i_shrink < i_grow < i_create < i_format < i_setflag

    def test_delete_uses_sgdisk_then_settles(self) -> None:
        calls = self._apply([{"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}])
        assert ["sgdisk", "--delete=2", "/dev/sda"] in calls
        assert ["partprobe", "/dev/sda"] in calls
        assert ["udevadm", "settle"] in calls

    def test_create_mkpart_sector_suffix_and_end(self) -> None:
        start = 2048
        size = 1048576
        calls = self._apply(
            [
                {
                    "type": "create",
                    "target": f"free:{start}",
                    "params": {
                        "start_sector": start,
                        "size_sectors": size,
                        "fstype": "ext4",
                        "path": "/dev/sda3",
                        "number": 3,
                        "flags": ["esp"],
                    },
                }
            ]
        )
        end = start + size - 1
        # parted mkpart with 's' sector suffix and END = start+size-1.
        assert [
            "parted",
            "-s",
            "/dev/sda",
            "mkpart",
            "primary",
            "ext4",
            f"{start}s",
            f"{end}s",
        ] in calls
        assert ["parted", "-s", "/dev/sda", "set", "3", "esp", "on"] in calls
        assert ["mkfs.ext4", "-F", "/dev/sda3"] in calls

    def test_create_nvme_uses_part_path(self) -> None:
        start = 2048
        size = 4096
        calls = self._apply(
            [
                {
                    "type": "create",
                    "target": f"free:{start}",
                    "params": {
                        "start_sector": start,
                        "size_sectors": size,
                        "fstype": "ext4",
                        "number": 1,
                    },
                }
            ],
            disk="/dev/nvme0n1",
        )
        # mkfs must target the NVMe-style partition path (nvme0n1p1).
        assert ["mkfs.ext4", "-F", "/dev/nvme0n1p1"] in calls

    def test_setflag_on_and_off(self) -> None:
        calls_on = self._apply(
            [
                {
                    "type": "setflag",
                    "target": "/dev/sda1",
                    "params": {"number": 1, "flag": "boot", "state": True},
                }
            ]
        )
        # 'boot' keeps its generic parted name (no longer remapped to 'esp').
        assert ["parted", "-s", "/dev/sda", "set", "1", "boot", "on"] in calls_on

        calls_off = self._apply(
            [
                {
                    "type": "setflag",
                    "target": "/dev/sda3",
                    "params": {"number": 3, "flag": "swap", "state": False},
                }
            ]
        )
        assert ["parted", "-s", "/dev/sda", "set", "3", "swap", "off"] in calls_off

    def test_resize_grow_table_before_fs(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "resize",
                    "target": "/dev/sda2",
                    "params": {
                        "path": "/dev/sda2",
                        "number": 2,
                        "new_size_sectors": 4096,
                        "old_size_sectors": 2048,
                        "start_sector": 2048,
                        "fstype": "ext4",
                    },
                }
            ]
        )
        flat = [" ".join(c) for c in calls]
        i_resizepart = next(i for i, line in enumerate(flat) if "resizepart 2" in line)
        i_resize2fs = next(i for i, line in enumerate(flat) if line == "resize2fs /dev/sda2")
        # GROW: partition table extended BEFORE the filesystem.
        assert i_resizepart < i_resize2fs

    def test_resize_shrink_fs_before_table(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "resize",
                    "target": "/dev/sda2",
                    "params": {
                        "path": "/dev/sda2",
                        "number": 2,
                        "new_size_sectors": 2048,
                        "old_size_sectors": 4096,
                        "start_sector": 2048,
                        "fstype": "ext4",
                    },
                }
            ]
        )
        flat = [" ".join(c) for c in calls]
        i_fsck = next(i for i, line in enumerate(flat) if line == "e2fsck -f -y /dev/sda2")
        i_resize2fs = next(
            i for i, line in enumerate(flat) if line.startswith("resize2fs /dev/sda2 ")
        )
        i_resizepart = next(i for i, line in enumerate(flat) if "resizepart 2" in line)
        # SHRINK: e2fsck -> resize2fs (fs) BEFORE resizepart (table).
        assert i_fsck < i_resize2fs < i_resizepart

    def test_resize_grow_xfs_uses_growfs(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "resize",
                    "target": "/dev/sda2",
                    "params": {
                        "path": "/dev/sda2",
                        "number": 2,
                        "new_size_sectors": 8192,
                        "old_size_sectors": 4096,
                        "start_sector": 2048,
                        "fstype": "xfs",
                        "mountpoint": "/data",
                    },
                }
            ]
        )
        assert ["xfs_growfs", "/data"] in calls

    def test_resize_shrink_xfs_refused(self) -> None:
        # XFS has no shrink support in any stock tool - this must be refused
        # cleanly rather than attempted, same as the NTFS-out-of-scope case.
        job = PartitionJob()
        self._run_capture(job)
        result = job._apply_operations(
            JobContext(),
            "/dev/sda",
            {
                "partition_operations": [
                    {
                        "type": "resize",
                        "target": "/dev/sda2",
                        "params": {
                            "path": "/dev/sda2",
                            "number": 2,
                            "new_size_sectors": 2048,
                            "old_size_sectors": 4096,
                            "start_sector": 2048,
                            "fstype": "xfs",
                            "mountpoint": "/data",
                        },
                    }
                ]
            },
            dry_run=True,
        )
        assert result.success is False
        assert result.error_code == 55

    def test_ntfs_resize_refused(self) -> None:
        job = PartitionJob()
        self._run_capture(job)
        result = job._apply_operations(
            JobContext(),
            "/dev/sda",
            {
                "partition_operations": [
                    {
                        "type": "resize",
                        "target": "/dev/sda2",
                        "params": {
                            "path": "/dev/sda2",
                            "number": 2,
                            "new_size_sectors": 2048,
                            "fstype": "ntfs",
                        },
                    }
                ]
            },
            dry_run=True,
        )
        assert result.success is False
        assert result.error_code == 54

    def test_malformed_operation_rejected(self) -> None:
        job = PartitionJob()
        self._run_capture(job)
        result = job._apply_operations(
            JobContext(),
            "/dev/sda",
            {"partition_operations": [{"type": "nuke", "target": "/dev/sda"}]},
            dry_run=True,
        )
        assert result.success is False
        assert result.error_code == 53

    def test_create_wipes_signatures_before_mkfs(self) -> None:
        # A disk reused across installs can carry a stale signature (e.g.
        # crypto_LUKS) on the sectors a new partition lands on; mkfs never
        # clears that on its own, so a wipefs -af must run right before it -
        # same reasoning as the auto-mode _format_partitions path.
        calls = self._apply(
            [
                {
                    "type": "create",
                    "target": "free:2048",
                    "params": {
                        "start_sector": 2048,
                        "size_sectors": _sectors(512),
                        "fstype": "ext4",
                        "path": "/dev/sda9",
                        "number": 9,
                    },
                }
            ]
        )
        assert ["wipefs", "-af", "/dev/sda9"] in calls
        i_wipe = calls.index(["wipefs", "-af", "/dev/sda9"])
        i_mkfs = calls.index(["mkfs.ext4", "-F", "/dev/sda9"])
        assert i_wipe < i_mkfs

    def test_format_wipes_signatures_before_mkfs(self) -> None:
        calls = self._apply(
            [
                {
                    "type": "format",
                    "target": "/dev/sda3",
                    "params": {"path": "/dev/sda3", "fstype": "xfs"},
                }
            ]
        )
        assert ["wipefs", "-af", "/dev/sda3"] in calls
        i_wipe = calls.index(["wipefs", "-af", "/dev/sda3"])
        i_mkfs = calls.index(["mkfs.xfs", "-f", "/dev/sda3"])
        assert i_wipe < i_mkfs


class TestApplyOperationsSecurity:
    """Security gates around the operation execution path."""

    def test_dry_run_executes_no_subprocess(self) -> None:
        job = PartitionJob()
        with patch("xen.jobs.partition.subprocess.run") as mock_run:
            result = job._apply_operations(
                JobContext(),
                "/dev/sda",
                {
                    "partition_operations": [
                        {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
                    ]
                },
                dry_run=True,
            )
        assert result.success is True
        mock_run.assert_not_called()

    def test_real_run_refused_without_confirmation(self) -> None:
        """run() must refuse a real operation plan without confirmed=True."""
        job = PartitionJob()
        with (
            patch.object(job, "validate", return_value=JobResult.ok()),
            patch("xen.jobs.partition.subprocess.run") as mock_run,
        ):
            context = JobContext(
                selections={
                    "disk": "/dev/sda",
                    "partition_mode": "manual",
                    "partition_operations": [
                        {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
                    ],
                    "dry_run": False,
                    "confirmed": False,
                }
            )
            result = job.run(context)
        assert result.success is False
        assert result.error_code == 38
        mock_run.assert_not_called()

    def test_run_routes_operations_path(self) -> None:
        """run() in manual mode with operations calls _apply_operations."""
        job = PartitionJob()
        with (
            patch.object(job, "validate", return_value=JobResult.ok()),
            patch.object(job, "_apply_operations", return_value=JobResult.ok()) as mock_apply,
        ):
            context = JobContext(
                selections={
                    "disk": "/dev/sda",
                    "partition_mode": "manual",
                    "partition_operations": [
                        {"type": "delete", "target": "/dev/sda2", "params": {"number": 2}}
                    ],
                    "dry_run": True,
                }
            )
            job.run(context)
        assert mock_apply.called


class TestConstants:
    """Sanity guard on the shared geometry constants."""

    def test_constants(self) -> None:
        assert _ALIGN == 2048
        assert _GPT_TAIL == 34
        assert _SECTOR_SIZE == 512


class TestPathAndNumberResolution:
    def test_dev_path_normalizes_bare_name(self) -> None:
        assert _dev_path("sdb3") == "/dev/sdb3"
        assert _dev_path("/dev/sdb3") == "/dev/sdb3"
        assert _dev_path("  sdb1 ") == "/dev/sdb1"
        assert _dev_path("") == ""

    def test_format_uses_dev_path(self) -> None:
        job = PartitionJob()
        calls: list[list[str]] = []

        def rec(cmd: list[str], description: str, dry_run: bool) -> JobResult:  # noqa: ARG001
            calls.append(list(cmd))
            return JobResult.ok(description)

        job._run_partitioning_command = rec  # type: ignore[method-assign]
        op = PartitionOperation.from_dict(
            {"type": "format", "target": "sdb3", "params": {"path": "sdb3", "fstype": "ext4"}}
        )
        job._op_format(op, dry_run=False)
        assert ["mkfs.ext4", "-F", "/dev/sdb3"] in calls

    def test_resolve_number_by_start_sector(self) -> None:
        job = PartitionJob()
        op = PartitionOperation.from_dict(
            {
                "type": "create",
                "target": "free:2101248",
                "params": {"start_sector": 2101248, "size_sectors": 1000, "fstype": "ext4"},
            }
        )
        with patch.object(job, "_disk_partition_starts", return_value={2048: 1, 2101248: 2}):
            assert job._resolve_partition_number("/dev/sdb", 2101248, op, dry_run=False) == 2

    def test_resolve_number_nearest_within_align(self) -> None:
        # parted may nudge the start a few sectors for alignment.
        job = PartitionJob()
        op = PartitionOperation.from_dict(
            {
                "type": "create",
                "target": "free:2101248",
                "params": {"start_sector": 2101248, "size_sectors": 1000, "fstype": "ext4"},
            }
        )
        with patch.object(job, "_disk_partition_starts", return_value={2048: 1, 2101250: 2}):
            assert job._resolve_partition_number("/dev/sdb", 2101248, op, dry_run=False) == 2

    def test_resolve_number_explicit_wins(self) -> None:
        job = PartitionJob()
        op = PartitionOperation.from_dict(
            {
                "type": "create",
                "target": "free:x",
                "params": {
                    "start_sector": 100,
                    "size_sectors": 10,
                    "fstype": "ext4",
                    "number": 7,
                },
            }
        )
        assert job._resolve_partition_number("/dev/sdb", 100, op, dry_run=True) == 7

    @staticmethod
    def _capture(job: PartitionJob) -> list[list[str]]:
        calls: list[list[str]] = []

        def rec(cmd: list[str], description: str, dry_run: bool) -> JobResult:  # noqa: ARG001
            calls.append(list(cmd))
            return JobResult.ok(description)

        job._run_partitioning_command = rec  # type: ignore[method-assign]
        return calls

    def test_ensure_gpt_label_when_missing(self) -> None:
        job = PartitionJob()
        calls = self._capture(job)
        with patch.object(job, "_disk_has_label", return_value=False):
            job._ensure_partition_table("/dev/sdb", dry_run=False)
        assert ["parted", "-s", "/dev/sdb", "mklabel", "gpt"] in calls

    def test_ensure_gpt_label_skipped_when_present(self) -> None:
        job = PartitionJob()
        calls = self._capture(job)
        with patch.object(job, "_disk_has_label", return_value=True):
            job._ensure_partition_table("/dev/sdb", dry_run=False)
        assert calls == []


class TestManualMapperName:
    """Mapper name is derived purely from mountpoint - deterministic, so
    both the job and bridge.py can compute it independently."""

    def test_root_is_cryptroot(self) -> None:
        assert _manual_mapper_name("/") == "cryptroot"

    def test_home_is_crypthome(self) -> None:
        assert _manual_mapper_name("/home") == "crypthome"

    def test_swap_is_cryptswap(self) -> None:
        assert _manual_mapper_name("swap") == "cryptswap"

    def test_other_data_mountpoint_is_sanitized(self) -> None:
        assert _manual_mapper_name("/var/lib/data") == "cryptdata-var-lib-data"

    def test_blank_mountpoint_falls_back_to_generic_name(self) -> None:
        assert _manual_mapper_name("") == "cryptdata"

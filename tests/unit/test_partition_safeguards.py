"""
Unit tests for manual-partitioning safeguards:

- Btrfs subvolume schema validation (:func:`validate_btrfs_subvolumes`).
- Boot/EFI mountpoint consistency, enforced from both the M2 operation-queue
  path (:func:`validate_operations`) and the M1 assignment path
  (:func:`validate_manual_assignments`).
- General manual-assignment safeguards: exactly one root, no duplicate
  mountpoints (across plain partitions AND expanded subvolumes), malformed
  mountpoint strings.
- Execution: Btrfs subvolume creation wired into ``_op_format``/``_op_create``,
  and subvolume-aware mounting in ``_mount_manual``.

SECURITY: destructive paths are fully mocked via a patched
``_run_partitioning_command``; no real partitioning tool is invoked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from xen.jobs.base import JobContext, JobResult
from xen.jobs.partition import (
    _SECTOR_SIZE,
    PartitionJob,
    PartitionOperation,
    validate_btrfs_subvolumes,
    validate_manual_assignments,
    validate_operations,
)


def _no_busy() -> Any:
    return patch("xen.jobs.partition._is_target_busy", return_value=False)

MIB = 1024 * 1024
DISK_SECTORS = (500 * 1024**3) // _SECTOR_SIZE


def _sectors(mib: int) -> int:
    return mib * MIB // _SECTOR_SIZE


class TestValidateBtrfsSubvolumes:
    def test_accepts_well_formed_plan(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [
                {"name": "@", "mountpoint": "/"},
                {"name": "@home", "mountpoint": "/home"},
                {"name": "@log", "mountpoint": "/var/log"},
            ]
        )
        assert ok is True
        assert err == ""

    def test_accepts_root_mountpoint_as_empty_string(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": ""}, {"name": "@home", "mountpoint": "/home"}]
        )
        assert ok is True, err

    def test_rejects_empty_list(self) -> None:
        ok, err = validate_btrfs_subvolumes([])
        assert ok is False
        assert "subvolume" in err.lower()

    def test_rejects_missing_root_subvolume(self) -> None:
        ok, err = validate_btrfs_subvolumes([{"name": "@home", "mountpoint": "/home"}])
        assert ok is False
        assert "root" in err.lower()

    def test_rejects_two_root_subvolumes(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@root2", "mountpoint": "/"}]
        )
        assert ok is False
        assert "root" in err.lower()

    def test_rejects_duplicate_names(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@", "mountpoint": "/home"}]
        )
        assert ok is False
        assert "duplicate" in err.lower()

    def test_rejects_duplicate_mountpoints(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [
                {"name": "@", "mountpoint": "/"},
                {"name": "@home", "mountpoint": "/data"},
                {"name": "@home2", "mountpoint": "/data"},
            ]
        )
        assert ok is False
        assert "duplicate" in err.lower()

    def test_rejects_name_without_at_prefix(self) -> None:
        # This installer's Snapper/grub-btrfs tooling (see _BTRFS_SUBVOLUMES)
        # assumes '@'-prefixed top-level subvolumes; manual mode must follow
        # the same schema.
        ok, err = validate_btrfs_subvolumes(
            [{"name": "root", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}]
        )
        assert ok is False
        assert "@" in err

    def test_rejects_name_with_illegal_characters(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@ho/me", "mountpoint": "/home"}]
        )
        assert ok is False

    def test_rejects_non_root_mountpoint_without_leading_slash(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "home"}]
        )
        assert ok is False
        assert "/" in err

    def test_rejects_empty_name(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "", "mountpoint": "/home"}]
        )
        assert ok is False


def _op(op_type: str, target: str, params: dict[str, Any]) -> PartitionOperation:
    return PartitionOperation.from_dict({"type": op_type, "target": target, "params": params})


def _root_op() -> PartitionOperation:
    return _op(
        "create",
        "free:1050624",
        {
            "start_sector": 1050624,
            "size_sectors": _sectors(20 * 1024),
            "fstype": "ext4",
            "path": "/dev/sda2",
            "mountpoint": "/",
        },
    )


class TestBootMountpointConsistencyM2:
    """validate_operations() cross-checks /boot(/efi) against fstype+esp flag."""

    def _disk(self) -> dict[str, Any]:
        return {"sizeSectors": DISK_SECTORS, "segments": []}

    def test_rejects_boot_efi_on_non_vfat_partition(self) -> None:
        esp_like = _op(
            "create",
            "free:2048",
            {
                "start_sector": 2048,
                "size_sectors": _sectors(512),
                "fstype": "ext4",
                "path": "/dev/sda1",
                "mountpoint": "/boot/efi",
            },
        )
        with _no_busy():
            ok, err = validate_operations(self._disk(), [esp_like, _root_op()], uefi=True)
        assert ok is False
        assert "/boot/efi" in err

    def test_rejects_esp_mounted_somewhere_other_than_boot_efi(self) -> None:
        esp = _op(
            "create",
            "free:2048",
            {
                "start_sector": 2048,
                "size_sectors": _sectors(512),
                "fstype": "vfat",
                "path": "/dev/sda1",
                "number": 1,
                "flags": ["esp"],
                "part_type": "efi",
                "mountpoint": "/data",
            },
        )
        with _no_busy():
            ok, err = validate_operations(self._disk(), [esp, _root_op()], uefi=True)
        assert ok is False
        assert "/boot/efi" in err

    def test_accepts_esp_mounted_at_boot_efi(self) -> None:
        esp = _op(
            "create",
            "free:2048",
            {
                "start_sector": 2048,
                "size_sectors": _sectors(512),
                "fstype": "vfat",
                "path": "/dev/sda1",
                "number": 1,
                "flags": ["esp"],
                "part_type": "efi",
                "mountpoint": "/boot/efi",
            },
        )
        with _no_busy():
            ok, err = validate_operations(self._disk(), [esp, _root_op()], uefi=True)
        assert ok is True, err


class TestValidateManualAssignments:
    """The M1 (post-Apply) mount-assignment safeguard - the realistic path
    exercised once the user has committed their layout and is only mapping
    mountpoints, with no pending M2 operations left."""

    def _disk(self) -> dict[str, Any]:
        esp_size = _sectors(512)
        root_size = _sectors(100 * 1024)
        return {
            "sizeSectors": DISK_SECTORS,
            "segments": [
                {
                    "kind": "partition",
                    "name": "sda1",
                    "startSector": 2048,
                    "sizeSectors": esp_size,
                    "sizeBytes": esp_size * _SECTOR_SIZE,
                    "fstype": "vfat",
                    "partType": "efi",
                },
                {
                    "kind": "partition",
                    "name": "sda2",
                    "startSector": 2048 + esp_size,
                    "sizeSectors": root_size,
                    "sizeBytes": root_size * _SECTOR_SIZE,
                    "fstype": "ext4",
                    "partType": "linux",
                },
            ],
        }

    def test_accepts_root_and_esp_at_boot_efi(self) -> None:
        ok, err = validate_manual_assignments(
            [self._disk()],
            [
                {"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
                {"name": "sda2", "mountpoint": "/", "format": False, "fstype": ""},
            ],
        )
        assert ok is True, err

    def test_accepts_plain_ext4_boot_alongside_efi_boot_efi(self) -> None:
        # A separate unencrypted /boot (e.g. ahead of a LUKS root) is a normal
        # partition - it must NOT be required to be vfat/ESP like /boot/efi.
        disk = self._disk()
        disk["segments"].append(
            {
                "kind": "partition",
                "name": "sda3",
                "startSector": 0,
                "sizeSectors": _sectors(1024),
                "sizeBytes": _sectors(1024) * _SECTOR_SIZE,
                "fstype": "ext4",
                "partType": "linux",
            }
        )
        ok, err = validate_manual_assignments(
            [disk],
            [
                {"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
                {"name": "sda3", "mountpoint": "/boot", "format": False, "fstype": ""},
                {"name": "sda2", "mountpoint": "/", "format": False, "fstype": ""},
            ],
        )
        assert ok is True, err

    def test_rejects_missing_root(self) -> None:
        ok, err = validate_manual_assignments(
            [self._disk()],
            [{"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""}],
        )
        assert ok is False
        assert "root" in err.lower() or "/" in err

    def test_rejects_duplicate_mountpoints(self) -> None:
        ok, err = validate_manual_assignments(
            [self._disk()],
            [
                {"name": "sda1", "mountpoint": "/", "format": False, "fstype": ""},
                {"name": "sda2", "mountpoint": "/", "format": False, "fstype": ""},
            ],
        )
        assert ok is False

    def test_rejects_non_efi_partition_mounted_at_boot_efi(self) -> None:
        ok, err = validate_manual_assignments(
            [self._disk()],
            [
                {"name": "sda2", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
                {"name": "sda1", "mountpoint": "/", "format": False, "fstype": ""},
            ],
        )
        assert ok is False
        assert "efi" in err.lower() or "vfat" in err.lower()

    def test_rejects_esp_mounted_at_wrong_path(self) -> None:
        ok, err = validate_manual_assignments(
            [self._disk()],
            [
                {"name": "sda1", "mountpoint": "/mnt/esp", "format": False, "fstype": ""},
                {"name": "sda2", "mountpoint": "/", "format": False, "fstype": ""},
            ],
        )
        assert ok is False
        assert "/boot/efi" in err

    def test_rejects_mountpoint_without_leading_slash(self) -> None:
        ok, err = validate_manual_assignments(
            [self._disk()],
            [
                {"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
                {"name": "sda2", "mountpoint": "home", "format": False, "fstype": ""},
            ],
        )
        assert ok is False

    def test_accepts_swap_pseudo_mountpoint(self) -> None:
        disk = self._disk()
        disk["segments"].append(
            {
                "kind": "partition",
                "name": "sda3",
                "startSector": 0,
                "sizeSectors": _sectors(4096),
                "sizeBytes": _sectors(4096) * _SECTOR_SIZE,
                "fstype": "swap",
                "partType": "swap",
            }
        )
        ok, err = validate_manual_assignments(
            [disk],
            [
                {"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
                {"name": "sda2", "mountpoint": "/", "format": False, "fstype": ""},
                {"name": "sda3", "mountpoint": "swap", "format": False, "fstype": ""},
            ],
        )
        assert ok is True, err

    def test_validates_subvolume_schema_and_folds_into_mountpoint_checks(self) -> None:
        disk = self._disk()
        # sda2 reformatted to btrfs with a custom subvolume layout.
        assignments = [
            {"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
            {
                "name": "sda2",
                "mountpoint": "",
                "format": True,
                "fstype": "btrfs",
                "subvolumes": [
                    {"name": "@", "mountpoint": "/"},
                    {"name": "@home", "mountpoint": "/home"},
                ],
            },
        ]
        ok, err = validate_manual_assignments([disk], assignments)
        assert ok is True, err

    def test_rejects_malformed_subvolume_schema(self) -> None:
        disk = self._disk()
        assignments = [
            {"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
            {
                "name": "sda2",
                "mountpoint": "",
                "format": True,
                "fstype": "btrfs",
                "subvolumes": [{"name": "@home", "mountpoint": "/home"}],  # no root subvolume
            },
        ]
        ok, err = validate_manual_assignments([disk], assignments)
        assert ok is False
        assert "root" in err.lower()

    def test_rejects_subvolumes_on_non_btrfs_partition(self) -> None:
        disk = self._disk()
        assignments = [
            {"name": "sda1", "mountpoint": "/boot/efi", "format": False, "fstype": ""},
            {
                "name": "sda2",
                "mountpoint": "",
                "format": False,
                "fstype": "",
                "subvolumes": [{"name": "@", "mountpoint": "/"}],
            },
        ]
        ok, err = validate_manual_assignments([disk], assignments)
        assert ok is False
        assert "btrfs" in err.lower()

    def test_subvolume_mountpoint_collides_with_other_partition(self) -> None:
        disk = self._disk()
        assignments = [
            {"name": "sda1", "mountpoint": "/home", "format": False, "fstype": ""},
            {
                "name": "sda2",
                "mountpoint": "",
                "format": True,
                "fstype": "btrfs",
                "subvolumes": [
                    {"name": "@", "mountpoint": "/"},
                    {"name": "@home", "mountpoint": "/home"},
                ],
            },
        ]
        ok, err = validate_manual_assignments([disk], assignments)
        assert ok is False


class TestBtrfsSubvolumeExecution:
    """Btrfs subvolume creation wired into the M2 execution path."""

    @staticmethod
    def _run_capture(job: PartitionJob) -> list[list[str]]:
        calls: list[list[str]] = []

        def _record(cmd: list[str], description: str, dry_run: bool) -> JobResult:  # noqa: ARG001
            calls.append(list(cmd))
            return JobResult.ok(description)

        job._run_partitioning_command = _record  # type: ignore[method-assign]
        return calls

    def test_format_with_subvolumes_creates_them_after_mkfs(self) -> None:
        job = PartitionJob()
        calls = self._run_capture(job)
        result = job._apply_operations(
            JobContext(),
            "/dev/sda",
            {
                "partition_operations": [
                    {
                        "type": "format",
                        "target": "/dev/sda2",
                        "params": {
                            "path": "/dev/sda2",
                            "fstype": "btrfs",
                            "subvolumes": [
                                {"name": "@", "mountpoint": "/"},
                                {"name": "@home", "mountpoint": "/home"},
                            ],
                        },
                    }
                ]
            },
            dry_run=True,
        )
        assert result.success is True, result.message
        assert ["mkfs.btrfs", "-f", "/dev/sda2"] in calls
        scratch_create_root = ["btrfs", "subvolume", "create", "/run/xen-btrfs-scratch/@"]
        scratch_create_home = ["btrfs", "subvolume", "create", "/run/xen-btrfs-scratch/@home"]
        assert scratch_create_root in calls
        assert scratch_create_home in calls

        i_mkfs = calls.index(["mkfs.btrfs", "-f", "/dev/sda2"])
        i_mount = next(
            i for i, c in enumerate(calls) if "subvolid=5" in c and "/dev/sda2" in c
        )
        i_sub_root = calls.index(scratch_create_root)
        i_sub_home = calls.index(scratch_create_home)
        i_umount = next(
            i for i, c in enumerate(calls) if c[0] == "umount" and "xen-btrfs" in c[-1]
        )
        assert i_mkfs < i_mount < i_sub_root < i_sub_home < i_umount

    def test_format_rejects_malformed_subvolumes(self) -> None:
        job = PartitionJob()
        self._run_capture(job)
        result = job._apply_operations(
            JobContext(),
            "/dev/sda",
            {
                "partition_operations": [
                    {
                        "type": "format",
                        "target": "/dev/sda2",
                        "params": {
                            "path": "/dev/sda2",
                            "fstype": "btrfs",
                            "subvolumes": [{"name": "notprefixed", "mountpoint": "/"}],
                        },
                    }
                ]
            },
            dry_run=True,
        )
        assert result.success is False


class TestMountManualWithSubvolumes:
    def test_mounts_each_subvolume_at_its_target(self) -> None:
        # Exercises _mount_manual directly: the final-mount step, independent
        # of validate_manual_assignments's plan-completeness gate (covered by
        # TestValidateManualAssignments above).
        job = PartitionJob()
        calls = self._capture(job)
        result = job._mount_manual(
            JobContext(target_root="/mnt/target"),
            [
                {
                    "name": "sda2",
                    "path": "/dev/sda2",
                    "mountpoint": "",
                    "format": False,
                    "fstype": "",
                    "subvolumes": [
                        {"name": "@", "mountpoint": "/"},
                        {"name": "@home", "mountpoint": "/home"},
                    ],
                }
            ],
            dry_run=True,
        )
        assert result.success is True, result.message
        opts = PartitionJob._BTRFS_MOUNT_OPTIONS
        root_mount = ["mount", "-t", "btrfs", "-o", f"{opts},subvol=@", "/dev/sda2", "/mnt/target"]
        home_mount = [
            "mount",
            "-t",
            "btrfs",
            "-o",
            f"{opts},subvol=@home",
            "/dev/sda2",
            "/mnt/target/home",
        ]
        assert root_mount in calls
        assert home_mount in calls
        assert calls.index(root_mount) < calls.index(home_mount)

    @staticmethod
    def _capture(job: PartitionJob) -> list[list[str]]:
        calls: list[list[str]] = []

        def _record(cmd: list[str], description: str, dry_run: bool) -> JobResult:  # noqa: ARG001
            calls.append(list(cmd))
            return JobResult.ok(description)

        job._run_partitioning_command = _record  # type: ignore[method-assign]
        return calls

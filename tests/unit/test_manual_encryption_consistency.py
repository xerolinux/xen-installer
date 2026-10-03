"""
Regression tests for the manual-partitioning encryption final review:
one subvolume-aware root resolver shared by every layer, encryption rules
that gate Apply against the combined applied + queued state, the
install-time consistency check, and the single-prompt GRUB keyfile.
"""

from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from xen.jobs.base import JobContext, JobResult
from xen.jobs.finished import FinishedJob
from xen.jobs.luksbootkeyfile import LuksBootKeyfileJob
from xen.jobs.partition import (
    _ALIGN,
    _SECTOR_SIZE,
    PartitionJob,
    PartitionOperation,
    resolve_manual_root,
    resolve_root_encryption,
    validate_encryption_rules,
    validate_manual_encryption,
    validate_operations,
)
from xen.jobs.systemconfig import SystemConfigJob

MIB = 1024 * 1024
DISK_SECTORS = (500 * 1024**3) // _SECTOR_SIZE

DEFAULT_SUBVOLUMES = [
    {"name": "@", "mountpoint": "/"},
    {"name": "@home", "mountpoint": "/home"},
    {"name": "@log", "mountpoint": "/var/log"},
]


def _sectors(mib: int) -> int:
    return mib * MIB // _SECTOR_SIZE


def _disk() -> dict[str, Any]:
    esp_size = _sectors(512)
    root_start = _ALIGN + esp_size
    root_size = _sectors(100 * 1024)
    return {
        "name": "sda",
        "sizeSectors": DISK_SECTORS,
        "segments": [
            {
                "kind": "partition", "name": "sda1", "startSector": _ALIGN,
                "sizeSectors": esp_size, "sizeBytes": esp_size * _SECTOR_SIZE,
                "fstype": "vfat", "partType": "efi", "mountpoint": "",
            },
            {
                "kind": "partition", "name": "sda2", "startSector": root_start,
                "sizeSectors": root_size, "sizeBytes": root_size * _SECTOR_SIZE,
                "fstype": "ext4", "partType": "linux", "mountpoint": "",
            },
            {
                "kind": "partition", "name": "sda3", "startSector": root_start + root_size,
                "sizeSectors": root_size, "sizeBytes": root_size * _SECTOR_SIZE,
                "fstype": "ext4", "partType": "linux", "mountpoint": "",
            },
        ],
    }


def _op(op_type: str, target: str, **params: Any) -> PartitionOperation:
    return PartitionOperation.from_dict({"type": op_type, "target": target, "params": params})


def _no_busy() -> Any:
    return patch("xen.jobs.partition._is_target_busy", return_value=False)


class TestResolveManualRoot:
    def test_plain_root_mountpoint(self) -> None:
        a = [{"name": "sda3", "mountpoint": "/home"}, {"name": "sda2", "mountpoint": "/"}]
        assert resolve_manual_root(a)["name"] == "sda2"

    def test_btrfs_root_subvolume_with_blank_mountpoint(self) -> None:
        a = [{"name": "sda2", "mountpoint": "", "subvolumes": DEFAULT_SUBVOLUMES}]
        assert resolve_manual_root(a)["name"] == "sda2"

    def test_btrfs_root_subvolume_with_empty_subvol_mountpoint(self) -> None:
        a = [{"name": "sda2", "mountpoint": "", "subvolumes": [{"name": "@", "mountpoint": ""}]}]
        assert resolve_manual_root(a)["name"] == "sda2"

    def test_subvolumes_override_bare_mountpoint(self) -> None:
        """Matches _mount_manual: a subvolume layout replaces the bare mountpoint."""
        a = [{"name": "sda2", "mountpoint": "/", "subvolumes": [{"name": "@data", "mountpoint": "/data"}]}]
        assert resolve_manual_root(a) is None

    def test_no_root(self) -> None:
        assert resolve_manual_root([{"name": "sda3", "mountpoint": "/home"}]) is None


class TestResolveRootEncryption:
    def test_btrfs_subvolume_root_encrypted_is_detected(self) -> None:
        selections = {
            "partition_mode": "manual",
            "partition_assignments": [{
                "name": "sda2", "path": "/dev/mapper/cryptroot", "raw_path": "/dev/sda2",
                "mountpoint": "", "fstype": "btrfs", "subvolumes": DEFAULT_SUBVOLUMES,
            }],
        }
        assert resolve_root_encryption(selections) == (True, "/dev/sda2")

    def test_mapper_root_without_raw_path_reports_encrypted_with_no_raw(self) -> None:
        """Inconsistent state must not be silently reported as unencrypted."""
        selections = {
            "partition_mode": "manual",
            "partition_assignments": [
                {"name": "sda2", "path": "/dev/mapper/cryptroot", "mountpoint": "/"},
            ],
        }
        assert resolve_root_encryption(selections) == (True, "")

    def test_auto_mode_unchanged(self) -> None:
        assert resolve_root_encryption({"encryption": True, "root_partition": "/dev/vda2"}) == (
            True, "/dev/vda2",
        )
        assert resolve_root_encryption({"encryption": False}) == (False, "")


class TestBtrfsSubvolumeRootEncryption:
    """Critical #3: the reviewer's exact scenario - a new btrfs partition
    with the editor's default subvolume layout (root at '@'), Mount Point
    left blank, Encrypt checked."""

    @patch("xen.jobs.partition.PartitionJob._create_manual_btrfs_subvolumes")
    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_apply_uses_user_passphrase_and_cryptroot(
        self, mock_run: MagicMock, mock_secret: MagicMock, mock_subvols: MagicMock
    ) -> None:
        mock_run.return_value = JobResult.ok()
        mock_secret.return_value = JobResult.ok()
        mock_subvols.return_value = JobResult.ok()
        job = PartitionJob()
        op = _op(
            "format", "/dev/sda2", path="/dev/sda2", fstype="btrfs",
            subvolumes=DEFAULT_SUBVOLUMES, encrypt=True,
            encryption_passphrase="S3cretRootPass!",
        )

        result = job._op_format(op, dry_run=False, bootloader="grub", has_separate_boot=False)

        assert result.success is True
        calls = [(list(c.args[0]), c.kwargs.get("passphrase")) for c in mock_secret.call_args_list]
        fmt = next(cmd for cmd, _ in calls if "luksFormat" in cmd)
        assert "pbkdf2" in fmt, "root under GRUB must get PBKDF2"
        assert any("luksOpen" in cmd and "cryptroot" in cmd for cmd, _ in calls)
        assert all(pw == "S3cretRootPass!" for _, pw in calls)
        assert job._manual_luks_passphrases == {"/dev/sda2": "S3cretRootPass!"}

    def test_validation_requires_passphrase_for_subvolume_root(self) -> None:
        ops = [_op(
            "format", "/dev/sda2", path="/dev/sda2", fstype="btrfs",
            subvolumes=DEFAULT_SUBVOLUMES, encrypt=True,
        )]
        with _no_busy():
            ok, reason = validate_encryption_rules(_disk(), ops, [])
        assert ok is False
        assert "passphrase" in reason.lower()

    def test_validate_operations_counts_subvolume_root(self) -> None:
        ops = [
            _op("format", "/dev/sda2", path="/dev/sda2", fstype="btrfs",
                subvolumes=DEFAULT_SUBVOLUMES, encrypt=True,
                encryption_passphrase="S3cretRootPass!"),
            _op("format", "/dev/sda3", path="/dev/sda3", fstype="ext4",
                mountpoint="/data", encrypt=True),
        ]
        with _no_busy():
            ok, reason = validate_operations(_disk(), ops, uefi=False)
        assert (ok, reason) == (True, "")

    def test_boot_chain_sees_encrypted_subvolume_root(self) -> None:
        """Both bootloader and mkinitcpio must add cryptdevice=/encrypt."""
        selections = {
            "partition_mode": "manual",
            "partition_assignments": [{
                "name": "sda2", "path": "/dev/mapper/cryptroot", "raw_path": "/dev/sda2",
                "mountpoint": "", "fstype": "btrfs", "subvolumes": DEFAULT_SUBVOLUMES,
            }],
        }
        content = "HOOKS=(base udev autodetect block filesystems fsck)\n"
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            conf = Path(tmp) / "etc" / "mkinitcpio.conf"
            conf.parent.mkdir(parents=True)
            conf.write_text(content)
            result = SystemConfigJob()._configure_mkinitcpio_hooks(tmp, selections)
            assert result.success is True
            assert "encrypt filesystems" in conf.read_text()


class TestEncryptionRulesAcrossBatches:
    """Critical #1: rules judged against applied assignments + the queue."""

    def test_home_in_second_batch_accepted_when_root_encrypted_earlier(self) -> None:
        applied = [{"name": "sda2", "path": "/dev/mapper/cryptroot",
                    "raw_path": "/dev/sda2", "mountpoint": "/", "fstype": "ext4"}]
        ops = [_op("format", "/dev/sda3", path="/dev/sda3", fstype="ext4",
                   mountpoint="/home", encrypt=True)]
        with _no_busy():
            assert validate_encryption_rules(_disk(), ops, applied) == (True, "")

    def test_home_rejected_when_applied_root_is_plain(self) -> None:
        applied = [{"name": "sda2", "path": "/dev/sda2", "mountpoint": "/", "fstype": "ext4"}]
        ops = [_op("format", "/dev/sda3", path="/dev/sda3", fstype="ext4",
                   mountpoint="/home", encrypt=True)]
        with _no_busy():
            ok, reason = validate_encryption_rules(_disk(), ops, applied)
        assert ok is False
        assert "root" in reason.lower()

    def test_second_encrypted_root_after_earlier_apply_rejected(self) -> None:
        applied = [{"name": "sda2", "path": "/dev/mapper/cryptroot",
                    "raw_path": "/dev/sda2", "mountpoint": "/", "fstype": "ext4"}]
        ops = [_op("format", "/dev/sda3", path="/dev/sda3", fstype="ext4",
                   mountpoint="/", encrypt=True, encryption_passphrase="S3cretRootPass!")]
        with _no_busy():
            ok, reason = validate_encryption_rules(_disk(), ops, applied)
        assert ok is False
        assert "already" in reason.lower()

    def test_reformatting_already_encrypted_partition_rejected(self) -> None:
        applied = [{"name": "sda2", "path": "/dev/mapper/cryptroot",
                    "raw_path": "/dev/sda2", "mountpoint": "/", "fstype": "ext4"}]
        ops = [_op("format", "/dev/sda2", path="/dev/sda2", fstype="ext4",
                   mountpoint="/", encrypt=True, encryption_passphrase="S3cretRootPass!")]
        with _no_busy():
            ok, reason = validate_encryption_rules(_disk(), ops, applied)
        assert ok is False
        assert "sda2" in reason

    def test_short_root_passphrase_rejected(self) -> None:
        """Critical #2 backend defense-in-depth."""
        ops = [_op("format", "/dev/sda2", path="/dev/sda2", fstype="ext4",
                   mountpoint="/", encrypt=True, encryption_passphrase="short")]
        with _no_busy():
            ok, reason = validate_encryption_rules(_disk(), ops, [])
        assert ok is False
        assert "8" in reason

    def test_blank_mountpoint_encrypted_swap_is_swap(self) -> None:
        ops = [_op("format", "/dev/sda3", path="/dev/sda3", fstype="swap", encrypt=True)]
        with _no_busy():
            assert validate_encryption_rules(_disk(), ops, []) == (True, "")


class TestValidateManualEncryption:
    """Critical #1 part 2 / Critical #3: install-time consistency check."""

    def test_consistent_plan_accepted(self) -> None:
        a = [
            {"name": "sda2", "path": "/dev/mapper/cryptroot", "raw_path": "/dev/sda2", "mountpoint": "/"},
            {"name": "sda3", "path": "/dev/mapper/crypthome", "raw_path": "/dev/sda3", "mountpoint": "/home"},
        ]
        assert validate_manual_encryption(a) == (True, "")

    def test_root_reassigned_onto_generated_key_partition_rejected(self) -> None:
        """The 'set / via Edit after Apply' path: root sits on a mapper that
        was created with a generated key nobody can type at boot."""
        a = [{"name": "sda3", "path": "/dev/mapper/cryptdata", "raw_path": "/dev/sda3", "mountpoint": "/"}]
        ok, reason = validate_manual_encryption(a)
        assert ok is False
        assert "cryptdata" in reason

    def test_cryptroot_on_non_root_rejected(self) -> None:
        a = [
            {"name": "sda2", "path": "/dev/mapper/cryptroot", "raw_path": "/dev/sda2", "mountpoint": "/data"},
            {"name": "sda3", "path": "/dev/sda3", "mountpoint": "/"},
        ]
        ok, reason = validate_manual_encryption(a)
        assert ok is False
        assert "cryptroot" in reason

    def test_encrypted_data_without_encrypted_root_rejected(self) -> None:
        a = [
            {"name": "sda2", "path": "/dev/sda2", "mountpoint": "/"},
            {"name": "sda3", "path": "/dev/mapper/crypthome", "raw_path": "/dev/sda3", "mountpoint": "/home"},
        ]
        ok, reason = validate_manual_encryption(a)
        assert ok is False
        assert "root" in reason.lower()

    def test_encrypted_swap_without_encrypted_root_accepted(self) -> None:
        a = [
            {"name": "sda2", "path": "/dev/sda2", "mountpoint": "/"},
            {"name": "sda3", "path": "/dev/mapper/cryptswap", "raw_path": "/dev/sda3",
             "mountpoint": "", "fstype": "swap"},
        ]
        assert validate_manual_encryption(a) == (True, "")

    def test_mapper_without_raw_path_rejected(self) -> None:
        a = [{"name": "sda2", "path": "/dev/mapper/cryptroot", "mountpoint": "/"}]
        ok, _ = validate_manual_encryption(a)
        assert ok is False

    def test_partition_manual_fails_cleanly_on_inconsistent_state(self) -> None:
        job = PartitionJob()
        selections = {
            "partition_assignments": [
                {"name": "sda3", "path": "/dev/mapper/cryptdata", "raw_path": "/dev/sda3",
                 "mountpoint": "/", "format": False, "fstype": ""},
            ],
        }
        with patch("xen.jobs.partition.disk_detector.list_disks", return_value=[]):
            result = job._partition_manual(JobContext(), "/dev/sda", selections, dry_run=True)
        assert result.success is False
        assert "cryptdata" in result.message


class TestEncryptedSwapWipe:
    """Important #4: encrypted swap still gets its raw signatures wiped."""

    @patch("xen.jobs.partition.PartitionJob._run_secret_command")
    @patch("xen.jobs.partition.PartitionJob._run_partitioning_command")
    def test_wipes_before_returning(self, mock_run: MagicMock, mock_secret: MagicMock) -> None:
        mock_run.return_value = JobResult.ok()
        job = PartitionJob()
        op = _op("format", "/dev/sda3", path="/dev/sda3", fstype="swap",
                 mountpoint="swap", encrypt=True)
        assert job._op_format(op, dry_run=False).success is True
        cmds = [list(c.args[0]) for c in mock_run.call_args_list]
        assert ["wipefs", "-af", "/dev/sda3"] in cmds
        assert not any(c[0].startswith("mkswap") or c[0].startswith("mkfs") for c in cmds)
        mock_secret.assert_not_called()


class TestManualRootBootKeyfile:
    """Important #1: single prompt for GRUB + manual encrypted root."""

    def _selections(self, **extra: Any) -> dict[str, Any]:
        base = {
            "partition_mode": "manual",
            "encryption": False,
            "bootloader": "grub",
            "partition_assignments": [{
                "name": "sda2", "path": "/dev/mapper/cryptroot", "raw_path": "/dev/sda2",
                "mountpoint": "/", "fstype": "ext4",
            }],
            "manual_luks_passphrases": {"sda2": "S3cretRootPass!"},
        }
        base.update(extra)
        return base

    @patch("subprocess.run")
    def test_grub_without_separate_boot_embeds_keyfile(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            context = JobContext(target_root=tmp, selections=self._selections())
            result = LuksBootKeyfileJob().run(context)
        assert result.success is True
        assert context.selections["luks_keyfile_path"] == LuksBootKeyfileJob.KEYFILE_PATH
        args, kwargs = mock_run.call_args
        assert "/dev/sda2" in args[0]
        assert kwargs["input"] == "S3cretRootPass!"

    @patch("subprocess.run")
    def test_systemd_boot_never_embeds_keyfile(self, mock_run: Mock) -> None:
        context = JobContext(selections=self._selections(bootloader="systemd-boot"))
        result = LuksBootKeyfileJob().run(context)
        assert result.success is True
        assert "luks_keyfile_path" not in context.selections
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_grub_with_separate_boot_never_embeds_keyfile(self, mock_run: Mock) -> None:
        sel = self._selections()
        sel["partition_assignments"].append(
            {"name": "sda4", "path": "/dev/sda4", "mountpoint": "/boot", "fstype": "ext4"}
        )
        context = JobContext(selections=sel)
        result = LuksBootKeyfileJob().run(context)
        assert result.success is True
        assert "luks_keyfile_path" not in context.selections
        mock_run.assert_not_called()

    def test_missing_carried_passphrase_fails(self) -> None:
        context = JobContext(selections=self._selections(manual_luks_passphrases={}))
        result = LuksBootKeyfileJob().run(context)
        assert result.success is False


class TestFinishedSkipsSwapMapper:
    @patch("xen.jobs.finished.FinishedJob._safe_unmount_recursive", return_value=True)
    @patch("subprocess.run")
    def test_swap_mapper_not_luks_closed(self, mock_run: Mock, _unmount: Mock) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            context = JobContext(target_root=tmp, selections={
                "partition_assignments": [
                    {"path": "/dev/mapper/cryptroot", "raw_path": "/dev/sda2", "mountpoint": "/"},
                    {"path": "/dev/mapper/cryptswap", "raw_path": "/dev/sda3",
                     "mountpoint": "swap", "fstype": "swap"},
                ],
            })
            FinishedJob()._cleanup_mounts(context)
        closed = [c.args[0][2] for c in mock_run.call_args_list if c.args[0][:2] == ["cryptsetup", "luksClose"]]
        assert closed == ["cryptroot"]

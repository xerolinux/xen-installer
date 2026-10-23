"""The subvolume-aware root resolver shared by the boot chain."""

from __future__ import annotations

import tempfile
from pathlib import Path

from xen.jobs.partition import root_assignment
from xen.jobs.systemconfig import SystemConfigJob

DEFAULT_SUBVOLUMES = [
    {"name": "@", "mountpoint": "/"},
    {"name": "@home", "mountpoint": "/home"},
    {"name": "@log", "mountpoint": "/var/log"},
]


class TestRootAssignment:
    def test_plain_root_mountpoint(self) -> None:
        a = [{"name": "sda3", "mountpoint": "/home"}, {"name": "sda2", "mountpoint": "/"}]
        assert root_assignment(a)["name"] == "sda2"

    def test_btrfs_root_subvolume_with_blank_mountpoint(self) -> None:
        a = [{"name": "sda2", "mountpoint": "", "subvolumes": DEFAULT_SUBVOLUMES}]
        assert root_assignment(a)["name"] == "sda2"

    def test_btrfs_root_subvolume_with_empty_subvol_mountpoint(self) -> None:
        a = [{"name": "sda2", "mountpoint": "", "subvolumes": [{"name": "@", "mountpoint": ""}]}]
        assert root_assignment(a)["name"] == "sda2"

    def test_subvolumes_override_bare_mountpoint(self) -> None:
        a = [{"name": "sda2", "mountpoint": "/", "subvolumes": [{"name": "@data", "mountpoint": "/data"}]}]
        assert root_assignment(a) is None

    def test_no_root(self) -> None:
        assert root_assignment([{"name": "sda3", "mountpoint": "/home"}]) is None


class TestBtrfsSubvolumeRootEncryption:
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
            conf = Path(tmp) / "etc" / "mkinitcpio.conf"
            conf.parent.mkdir(parents=True)
            conf.write_text(content)
            result = SystemConfigJob()._configure_mkinitcpio_hooks(tmp, selections)
            assert result.success is True
            assert "encrypt filesystems" in conf.read_text()

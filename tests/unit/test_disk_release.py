"""Unit tests for the target-disk release helpers."""

import subprocess
from unittest.mock import patch

import pytest

try:
    from xen.utils import disk_release

    HAS_DISK_RELEASE = True
except ImportError:
    HAS_DISK_RELEASE = False

pytestmark = pytest.mark.skipif(not HAS_DISK_RELEASE, reason="disk_release not available")


def make_run(
    members: list[str],
    mounts: dict[str, list[str]] | None = None,
    types: list[tuple[str, str]] | None = None,
    calls: list[list[str]] | None = None,
):
    """Build a fake ``_run`` answering lsblk/findmnt and recording other commands."""
    mounts = mounts or {}

    def _fake(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "lsblk":
            if cmd[2] == "NAME,TYPE":
                out = "\n".join(f"{name} {kind}" for name, kind in (types or []))
            else:
                out = "\n".join(members)
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd[0] == "findmnt":
            return subprocess.CompletedProcess(cmd, 0, "\n".join(mounts.get(cmd[-1], [])), "")
        if calls is not None:
            calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return _fake


class TestHoldsRunningSystem:
    def test_installed_package_refuses_root_disk(self):
        """Running from an already-installed system: root is a real block device."""
        run = make_run(
            members=["/dev/nvme0n1", "/dev/nvme0n1p1", "/dev/nvme0n1p2"],
            mounts={"/dev/nvme0n1p1": ["/boot"], "/dev/nvme0n1p2": ["/"]},
        )
        with patch.object(disk_release, "_run", run):
            assert disk_release.holds_running_system("/dev/nvme0n1") is True

    def test_live_iso_refuses_the_boot_medium(self):
        run = make_run(
            members=["/dev/sdb", "/dev/sdb1"],
            mounts={"/dev/sdb1": ["/iso"]},
        )
        with patch.object(disk_release, "_run", run):
            assert disk_release.holds_running_system("/dev/sdb") is True

    def test_plain_target_disk_is_not_the_running_system(self):
        run = make_run(
            members=["/dev/sda", "/dev/sda1"],
            mounts={"/dev/sda1": ["/run/media/nixos/DATA"]},
        )
        with patch.object(disk_release, "_run", run):
            assert disk_release.holds_running_system("/dev/sda") is False


class TestDiskHolders:
    def test_reports_mounts_and_swap(self):
        run = make_run(
            members=["/dev/sda", "/dev/sda1", "/dev/sda2"],
            mounts={"/dev/sda1": ["/run/media/nixos/GLF"]},
        )
        with (
            patch.object(disk_release, "_run", run),
            patch.object(disk_release, "_swap_devices", return_value={"/dev/sda2"}),
        ):
            holders = disk_release.disk_holders("/dev/sda")

        assert "/dev/sda1 is mounted on /run/media/nixos/GLF" in holders
        assert "/dev/sda2 is in use as swap" in holders

    def test_free_disk_has_no_holders(self):
        run = make_run(members=["/dev/sda", "/dev/sda1"])
        with (
            patch.object(disk_release, "_run", run),
            patch.object(disk_release, "_swap_devices", return_value=set()),
        ):
            assert disk_release.disk_holders("/dev/sda") == []


class TestReleaseDisk:
    def test_unmounts_udisks_automount(self):
        """The GParted + udisks auto-mount case that made wipefs fail with EBUSY."""
        calls: list[list[str]] = []
        run = make_run(
            members=["/dev/sda", "/dev/sda1"],
            mounts={"/dev/sda1": ["/run/media/nixos/GLF"]},
            calls=calls,
        )
        with (
            patch.object(disk_release, "_run", run),
            patch.object(disk_release, "_swap_devices", return_value=set()),
        ):
            actions = disk_release.release_disk("/dev/sda")

        assert ["umount", "-R", "/run/media/nixos/GLF"] in calls
        assert ["udevadm", "settle"] in calls
        assert "unmounted /dev/sda1 from /run/media/nixos/GLF" in actions

    def test_unmounts_deepest_first(self):
        calls: list[list[str]] = []
        run = make_run(
            members=["/dev/sda", "/dev/sda1", "/dev/sda2"],
            mounts={"/dev/sda2": ["/mnt/target"], "/dev/sda1": ["/mnt/target/boot"]},
            calls=calls,
        )
        with (
            patch.object(disk_release, "_run", run),
            patch.object(disk_release, "_swap_devices", return_value=set()),
        ):
            disk_release.release_disk("/dev/sda")

        umounts = [cmd[-1] for cmd in calls if cmd[0] == "umount"]
        assert umounts.index("/mnt/target/boot") < umounts.index("/mnt/target")

    def test_never_unmounts_a_critical_mount(self):
        calls: list[list[str]] = []
        run = make_run(
            members=["/dev/sda", "/dev/sda1"],
            mounts={"/dev/sda1": ["/"]},
            calls=calls,
        )
        with (
            patch.object(disk_release, "_run", run),
            patch.object(disk_release, "_swap_devices", return_value=set()),
        ):
            actions = disk_release.release_disk("/dev/sda")

        assert not [cmd for cmd in calls if cmd[0] == "umount"]
        assert actions == []

    def test_disables_swap_and_tears_down_mappers(self):
        calls: list[list[str]] = []
        run = make_run(
            members=["/dev/sda", "/dev/sda1", "/dev/sda2"],
            types=[
                ("/dev/sda", "disk"),
                ("/dev/sda1", "part"),
                ("/dev/sda2", "part"),
                ("/dev/mapper/cr", "crypt"),
            ],
            calls=calls,
        )
        with (
            patch.object(disk_release, "_run", run),
            patch.object(disk_release, "_swap_devices", return_value={"/dev/sda1"}),
        ):
            actions = disk_release.release_disk("/dev/sda")

        assert ["swapoff", "/dev/sda1"] in calls
        assert ["cryptsetup", "close", "/dev/mapper/cr"] in calls
        assert "disabled swap on /dev/sda1" in actions
        assert "closed LUKS mapper /dev/mapper/cr" in actions

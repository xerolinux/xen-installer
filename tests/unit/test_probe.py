import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xen.partitioning import probe

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fstools"

HIBERNATED = "Windows is hibernated (Fast Startup): shut Windows down fully first"
DIRTY = "Windows didn't shut down cleanly: run chkdsk in Windows first"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FakeSystem:
    """subprocess stand-in: replies by command name, records every argv."""

    def __init__(self, replies=None, hiberfile=b"", on_mount=None):
        self.replies = replies or {}
        self.hiberfile = hiberfile
        self.on_mount = on_mount
        self.calls: list[list[str]] = []
        self.timeouts: dict[str, int] = {}

    def run(self, cmd, **kwargs):
        assert kwargs.get("timeout"), f"no timeout on {cmd}"
        self.calls.append(list(cmd))
        self.timeouts[cmd[0]] = kwargs["timeout"]
        if cmd[0] == "mount" and self.on_mount:
            ok = self.on_mount(cmd)
            err = "" if ok else "mount: wrong fs type"
            return subprocess.CompletedProcess(cmd, 0 if ok else 32, "", err)
        if cmd[0] == "umount":
            target = cmd[-1]
            for entry in os.listdir(target):
                path = os.path.join(target, entry)
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        reply = self.replies.get(cmd[0], (0, "", ""))
        if callable(reply):
            reply = reply(cmd)
        rc, out, err = reply
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def popen(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        proc = MagicMock()
        proc.stdout.read.side_effect = lambda n: self.hiberfile[:n]
        return proc

    def patch(self):
        return (
            patch("xen.partitioning.probe.subprocess.run", side_effect=self.run),
            patch("xen.partitioning.probe.subprocess.Popen", side_effect=self.popen),
        )

    def called(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == name]


def _probe(system: FakeSystem, part: dict, tmp_path: Path) -> dict:
    run_patch, popen_patch = system.patch()
    with run_patch, popen_patch:
        return probe.probe_partition(part, str(tmp_path))


def _part(fstype: str, path: str = "/dev/vda3", **extra) -> dict:
    return {"path": path, "fstype": fstype, "mounted": False, "disk": "vda", **extra}


def _blkid(fstype: str):
    return (0, fstype + "\n", "")


class TestProbePartition:
    def test_ntfs_min_size_from_ntfsresize(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {"blkid": _blkid("ntfs"), "ntfsresize": (0, _fixture("ntfsresize_info.txt"), "")}
        )
        result = _probe(system, _part("ntfs"), tmp_path)
        assert result == {"minSizeBytes": 1606885376, "resizeBlockedReason": "", "bitlocker": False}
        assert system.called("ntfsresize") == [
            ["ntfsresize", "--info", "--no-progress-bar", "/dev/vda3"]
        ]

    @pytest.mark.parametrize("magic", [b"hibr", b"HIBR"])
    def test_hibernated_ntfs_detected_from_hiberfil_header(self, tmp_path: Path, magic) -> None:
        system = FakeSystem(
            {"blkid": _blkid("ntfs"), "ntfsresize": (0, _fixture("ntfsresize_info.txt"), "")},
            hiberfile=magic + b"\0" * 12,
        )
        result = _probe(system, _part("ntfs"), tmp_path)
        assert result["resizeBlockedReason"] == HIBERNATED
        assert result["minSizeBytes"] == 0
        assert ["ntfscat", "/dev/vda3", "hiberfil.sys"] in system.calls
        assert not system.called("ntfsresize")

    def test_zeroed_hiberfil_is_not_hibernated(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {"blkid": _blkid("ntfs"), "ntfsresize": (0, _fixture("ntfsresize_info.txt"), "")},
            hiberfile=b"\0" * 16,
        )
        assert _probe(system, _part("ntfs"), tmp_path)["resizeBlockedReason"] == ""

    def test_dirty_ntfs_blocker_wins_over_failure(self, tmp_path: Path) -> None:
        out = "ntfsresize v2026.7.7\nVolume is scheduled for check.\nRun chkdsk /f\n"
        system = FakeSystem({"blkid": _blkid("ntfs"), "ntfsresize": (1, out, "ERROR: refusing")})
        result = _probe(system, _part("ntfs"), tmp_path)
        assert result["resizeBlockedReason"] == DIRTY
        assert result["minSizeBytes"] == 0

    def test_failed_probe_reports_last_stderr_line(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {"blkid": _blkid("ntfs"), "ntfsresize": (1, "partial output\n", "first\nERROR: bad\n")}
        )
        result = _probe(system, _part("ntfs"), tmp_path)
        assert result["resizeBlockedReason"] == "Couldn't check this partition: ERROR: bad"
        assert result["minSizeBytes"] == 0

    def test_unparseable_success_is_a_failure(self, tmp_path: Path) -> None:
        system = FakeSystem({"blkid": _blkid("ntfs"), "ntfsresize": (0, "weird\nlast line\n", "")})
        result = _probe(system, _part("ntfs"), tmp_path)
        assert result["resizeBlockedReason"] == "Couldn't check this partition: last line"
        assert result["minSizeBytes"] == 0

    def test_timeout_is_a_failure(self, tmp_path: Path) -> None:
        def timeout(cmd):
            raise subprocess.TimeoutExpired(cmd, 120)

        system = FakeSystem({"blkid": _blkid("ntfs"), "ntfsresize": timeout})
        result = _probe(system, _part("ntfs"), tmp_path)
        assert result["resizeBlockedReason"].startswith("Couldn't check this partition: ")
        assert result["minSizeBytes"] == 0

    def test_ext4_min_size_is_blocks_times_block_size(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {
                "blkid": _blkid("ext4"),
                "resize2fs": (0, _fixture("resize2fs_P.txt"), ""),
                "dumpe2fs": (0, _fixture("dumpe2fs_h.txt"), ""),
            }
        )
        result = _probe(system, _part("ext4"), tmp_path)
        assert result["minSizeBytes"] == 431036 * 4096
        assert result["resizeBlockedReason"] == ""

    def test_ext_check_gets_a_long_timeout(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {
                "blkid": _blkid("ext4"),
                "resize2fs": (0, _fixture("resize2fs_P.txt"), ""),
                "dumpe2fs": (0, _fixture("dumpe2fs_h.txt"), ""),
            }
        )
        _probe(system, _part("ext4"), tmp_path)
        assert system.timeouts["e2fsck"] == 900

    def test_ext_journal_needing_recovery_has_an_actionable_reason(self, tmp_path: Path) -> None:
        header = (
            "Filesystem features:      has_journal ext_attr needs_recovery extent\n"
            + _fixture("dumpe2fs_h.txt")
        )
        system = FakeSystem(
            {
                "blkid": _blkid("ext4"),
                "e2fsck": (4, "", "Warning: skipping journal recovery"),
                "dumpe2fs": (0, header, ""),
            }
        )
        result = _probe(system, _part("ext4"), tmp_path)
        assert result["resizeBlockedReason"] == (
            "This partition was not shut down cleanly. "
            "Start its own system and shut it down normally first."
        )
        assert result["minSizeBytes"] == 0

    def test_btrfs_unmounts_even_when_min_dev_size_fails(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {"blkid": _blkid("btrfs"), "btrfs": (1, "", "ERROR: not a btrfs mount")},
            on_mount=lambda cmd: True,
        )
        result = _probe(system, _part("btrfs"), tmp_path)
        reason = "Couldn't check this partition: ERROR: not a btrfs mount"
        assert result["resizeBlockedReason"] == reason
        assert system.called("umount") == [["umount", str(tmp_path)]]

    def test_btrfs_min_size(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {"blkid": _blkid("btrfs"), "btrfs": (0, _fixture("btrfs_min_dev_size.txt"), "")},
            on_mount=lambda cmd: True,
        )
        assert _probe(system, _part("btrfs"), tmp_path)["minSizeBytes"] == 1884291072

    def test_bitlocker_reason(self, tmp_path: Path) -> None:
        system = FakeSystem({"blkid": _blkid("BitLocker")})
        result = _probe(system, _part(""), tmp_path)
        assert result == {
            "minSizeBytes": 0,
            "resizeBlockedReason": "BitLocker is on: turn it off in Windows first",
            "bitlocker": True,
        }
        assert system.called("blkid") == [["blkid", "-p", "-o", "value", "-s", "TYPE", "/dev/vda3"]]
        assert [c[0] for c in system.calls] == ["blkid"]

    def test_unshrinkable_fs_is_not_probed(self, tmp_path: Path) -> None:
        system = FakeSystem({"blkid": _blkid("xfs")})
        result = _probe(system, _part("xfs"), tmp_path)
        assert result["resizeBlockedReason"] == "XFS can't be shrunk"
        assert [c[0] for c in system.calls] == ["blkid"]

    def test_mounted_partition_not_probed(self, tmp_path: Path) -> None:
        system = FakeSystem()
        result = _probe(system, _part("ntfs", mounted=True), tmp_path)
        assert result["resizeBlockedReason"] == "In use"
        assert result["minSizeBytes"] == 0
        assert system.calls == []


def _os_release_mount(contents: dict[str, str], esp_files: tuple[str, ...] = ()):
    """on_mount callback that populates the scratch dir like the mounted fs would."""

    def on_mount(cmd):
        dev, target = cmd[-2], cmd[-1]
        opts = cmd[cmd.index("-o") + 1]
        key = f"{dev}|{opts}"
        if dev in esp_files or key in esp_files:
            boot = Path(target, "EFI", "Microsoft", "Boot")
            boot.mkdir(parents=True)
            (boot / "bootmgfw.efi").write_bytes(b"MZ")
            return True
        text = contents.get(key)
        if text is None:
            return False
        etc = Path(target, "etc")
        etc.mkdir()
        (etc / "os-release").write_text(text)
        return True

    return on_mount


def _detect(system: FakeSystem, parts: list[dict], prober: bool) -> dict:
    run_patch, popen_patch = system.patch()
    which = "/usr/bin/os-prober" if prober else None
    with run_patch, popen_patch, patch("xen.partitioning.probe.shutil.which", return_value=which):
        return probe.detect_os(parts)


WINDOWS_DISK = [
    _part("vfat", "/dev/nvme0n1p1", disk="nvme0n1", isEsp=True),
    _part("", "/dev/nvme0n1p2", disk="nvme0n1"),
    _part("ntfs", "/dev/nvme0n1p3", disk="nvme0n1"),
    _part("ntfs", "/dev/nvme0n1p4", disk="nvme0n1"),
    _part("ntfs", "/dev/sdb1", disk="sdb"),
    _part("ext4", "/dev/sdc1", disk="sdc"),
]


class TestDetectOs:
    def test_detect_os_with_os_prober_output(self) -> None:
        output = (
            "/dev/nvme0n1p1@/efi/Microsoft/Boot/bootmgfw.efi:Windows Boot Manager:Windows:efi\n"
            "/dev/sdc1:Arch Linux:Arch:linux\n"
            "garbage line\n"
        )
        system = FakeSystem({"os-prober": (0, output, "")})
        names = _detect(system, WINDOWS_DISK, prober=True)
        assert names == {
            "/dev/nvme0n1p3": "Windows",
            "/dev/nvme0n1p4": "Windows",
            "/dev/sdc1": "Arch Linux",
        }
        assert not system.called("mount")

    def test_os_prober_chain_entry_names_the_partition(self) -> None:
        system = FakeSystem({"os-prober": (0, "/dev/sdb1:Windows 10:Windows:chain\n", "")})
        assert _detect(system, WINDOWS_DISK, prober=True) == {"/dev/sdb1": "Windows 10"}

    def test_detect_os_fallback_windows_esp(self) -> None:
        system = FakeSystem(on_mount=_os_release_mount({}, esp_files=("/dev/nvme0n1p1",)))
        names = _detect(system, WINDOWS_DISK, prober=False)
        assert names == {"/dev/nvme0n1p3": "Windows", "/dev/nvme0n1p4": "Windows"}
        esp_mount = system.called("mount")[0][:-1]
        assert esp_mount == ["mount", "-t", "vfat", "-o", "ro", "/dev/nvme0n1p1"]

    def test_fallback_linux_os_release(self) -> None:
        parts = [_part("ext4", "/dev/sdc1", disk="sdc")]
        release = 'NAME="Old"\nPRETTY_NAME="Old Linux 1.0"\n'
        on_mount = _os_release_mount({"/dev/sdc1|ro,noload": release})
        system = FakeSystem(on_mount=on_mount)
        assert _detect(system, parts, prober=False) == {"/dev/sdc1": "Old Linux 1.0"}

    def test_fallback_btrfs_tries_root_subvolume_then_top_level(self) -> None:
        parts = [_part("btrfs", "/dev/sdd1", disk="sdd")]
        on_mount = _os_release_mount({"/dev/sdd1|ro,rescue=nologreplay": "PRETTY_NAME=TopLevel\n"})
        system = FakeSystem(on_mount=on_mount)
        assert _detect(system, parts, prober=False) == {"/dev/sdd1": "TopLevel"}
        opts = [c[c.index("-o") + 1] for c in system.called("mount")]
        assert opts == ["ro,rescue=nologreplay,subvol=@", "ro,rescue=nologreplay"]

    def test_fallback_ignores_os_release_symlink_escaping_the_mount(self, tmp_path: Path) -> None:
        outside = tmp_path / "host-os-release"
        outside.write_text("PRETTY_NAME=Host\n")

        def on_mount(cmd):
            etc = Path(cmd[-1], "etc")
            etc.mkdir()
            (etc / "os-release").symlink_to(outside)
            return True

        system = FakeSystem(on_mount=on_mount)
        assert _detect(system, [_part("ext4", "/dev/sdc1", disk="sdc")], prober=False) == {}

    def test_fallback_skips_mounted_partitions(self) -> None:
        system = FakeSystem(on_mount=lambda cmd: True)
        _detect(system, [_part("ext4", "/dev/sdc1", disk="sdc", mounted=True)], prober=False)
        assert system.calls == []


def test_probe_uses_read_only_commands(tmp_path: Path) -> None:
    fstypes = {"/dev/a": "ext4", "/dev/b": "btrfs", "/dev/c": "ntfs"}
    system = FakeSystem(
        {
            "blkid": lambda cmd: _blkid(fstypes[cmd[-1]]),
            "resize2fs": (0, _fixture("resize2fs_P.txt"), ""),
            "dumpe2fs": (0, _fixture("dumpe2fs_h.txt"), ""),
            "btrfs": (0, _fixture("btrfs_min_dev_size.txt"), ""),
            "ntfsresize": (0, _fixture("ntfsresize_info.txt"), ""),
        },
        on_mount=lambda cmd: True,
    )
    for path, fstype in fstypes.items():
        _probe(system, _part(fstype, path), tmp_path)
    parts = [
        _part("vfat", "/dev/e", isEsp=True),
        _part("ext4", "/dev/a"),
        _part("btrfs", "/dev/b"),
    ]
    _detect(system, parts, prober=False)

    mounts = system.called("mount")
    assert len(mounts) == 5
    for cmd in mounts:
        opts = cmd[cmd.index("-o") + 1].split(",")
        assert "ro" in opts, cmd
        if cmd[-2] == "/dev/a":
            assert "noload" in opts, cmd
        if cmd[-2] == "/dev/b":
            assert "rescue=nologreplay" in opts, cmd
    for cmd in system.calls:
        assert cmd[0] not in ("ntfsfix", "mkfs", "wipefs", "sgdisk", "parted", "tune2fs"), cmd
        if cmd[0] == "e2fsck":
            assert "-n" in cmd and "-y" not in cmd and "-p" not in cmd, cmd
        if cmd[0] == "resize2fs":
            assert "-P" in cmd, cmd
        if cmd[0] == "ntfsresize":
            assert "--info" in cmd and "--force" not in cmd, cmd
        if cmd[0] == "btrfs":
            assert cmd[1:3] == ["inspect-internal", "min-dev-size"], cmd


def _umount_fails_unless_lazy(cmd):
    return (0, "", "") if "-l" in cmd else (32, "", "umount: target is busy.")


class TestScratchUmount:
    def test_failed_probe_umount_retries_lazily_and_logs(self, tmp_path: Path, caplog) -> None:
        system = FakeSystem(
            {
                "blkid": _blkid("btrfs"),
                "btrfs": (0, _fixture("btrfs_min_dev_size.txt"), ""),
                "umount": _umount_fails_unless_lazy,
            },
            on_mount=lambda cmd: True,
        )
        with caplog.at_level("WARNING", logger="xen.partitioning.probe"):
            assert _probe(system, _part("btrfs"), tmp_path)["minSizeBytes"] == 1884291072
        assert system.called("umount") == [
            ["umount", str(tmp_path)],
            ["umount", "-l", str(tmp_path)],
        ]
        assert "umount: target is busy." in caplog.text

    def test_failed_os_probe_umount_retries_lazily(self, tmp_path: Path, caplog) -> None:
        parts = [_part("ext4", "/dev/sdc1", disk="sdc")]
        on_mount = _os_release_mount({"/dev/sdc1|ro,noload": "PRETTY_NAME=Old\n"})
        system = FakeSystem({"umount": _umount_fails_unless_lazy}, on_mount=on_mount)
        with caplog.at_level("WARNING", logger="xen.partitioning.probe"):
            assert _detect(system, parts, prober=False) == {"/dev/sdc1": "Old"}
        scratch = system.called("mount")[0][-1]
        assert system.called("umount") == [["umount", scratch], ["umount", "-l", scratch]]
        assert "umount: target is busy." in caplog.text

    def test_successful_umount_is_not_retried(self, tmp_path: Path) -> None:
        system = FakeSystem(
            {"blkid": _blkid("btrfs"), "btrfs": (0, _fixture("btrfs_min_dev_size.txt"), "")},
            on_mount=lambda cmd: True,
        )
        _probe(system, _part("btrfs"), tmp_path)
        assert system.called("umount") == [["umount", str(tmp_path)]]

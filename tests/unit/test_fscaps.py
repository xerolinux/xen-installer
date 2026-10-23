from pathlib import Path

import pytest

from xen.partitioning import fscaps

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fstools"

# Wording taken from the strings table of the ntfsresize binary (v2026.7.7)
HIBERNATED_OUTPUT = (
    "ntfsresize v2026.7.7 (libntfs-3g)\n"
    "Checking filesystem consistency ...\n"
    "The NTFS partition is hibernated. Windows must be resumed and turned off\n"
    "properly, so resizing could be done safely.\n"
)
REFUSAL_OUTPUTS = [
    "NTFS is inconsistent. Run chkdsk /f on Windows then reboot it TWICE!\n"
    "The usage of the /f parameter is very IMPORTANT! No modification was\n",
    "Volume is scheduled for check.\nRun chkdsk /f and please try again, or see option -f.\n",
    "The NTFS journal file is unclean. Please shutdown Windows properly before\n"
    "using this software! Note, if you have run chkdsk previously then boot\n",
]
NO_ACTION_SUCCESS = (
    "Needed relocations : 513 (3 MB)\n"
    "Schedule chkdsk for NTFS consistency check at Windows boot time ...\n"
    "Resetting $LogFile ... (this might take a while)\n"
    "Updating Boot record ...\n"
    "The read-only test run ended successfully.\n"
)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize("fstype", ["ext2", "ext3", "ext4", "btrfs", "ntfs", "ntfs3"])
def test_caps_table_shrink_and_grow(fstype):
    caps = fscaps.caps_for(fstype)
    assert caps.can_shrink and caps.can_grow
    assert caps.blocked_reason == ""


@pytest.mark.parametrize(
    ("fstype", "needs_mount"),
    [("ext4", False), ("ntfs", False), ("btrfs", True), ("xfs", True)],
)
def test_caps_table_resize_needs_mount(fstype, needs_mount):
    assert fscaps.caps_for(fstype).resize_needs_mount is needs_mount


@pytest.mark.parametrize(
    ("fstype", "reason"),
    [
        ("vfat", "FAT partitions can't be resized here"),
        ("fat32", "FAT partitions can't be resized here"),
        ("swap", "Swap can't be resized; delete and recreate it"),
        ("crypto_LUKS", "Encrypted (LUKS): Format or Delete it to use it"),
        ("BitLocker", "BitLocker is on: turn it off in Windows first"),
        ("", "Unknown or empty partition"),
        ("zfs_member", "Unknown or empty partition"),
    ],
)
def test_caps_table_blocked(fstype, reason):
    caps = fscaps.caps_for(fstype)
    assert not caps.can_shrink and not caps.can_grow
    assert caps.blocked_reason == reason


def test_caps_table_xfs_grow_only():
    caps = fscaps.caps_for("xfs")
    assert caps.can_grow and not caps.can_shrink
    assert caps.blocked_reason == "XFS can't be shrunk"


def test_caps_are_frozen():
    with pytest.raises(AttributeError):
        fscaps.caps_for("ext4").can_shrink = False


def test_parse_ntfsresize_min_from_real_output():
    assert fscaps.parse_ntfsresize_min(fixture("ntfsresize_info.txt")) == 1606885376


def test_parse_ntfsresize_min_rejects_garbage():
    with pytest.raises(ValueError):
        fscaps.parse_ntfsresize_min("nothing useful")


def test_parse_ntfsresize_blocker_clean_output():
    assert fscaps.parse_ntfsresize_blocker(fixture("ntfsresize_info.txt")) == ""
    assert fscaps.parse_ntfsresize_blocker(fixture("ntfsresize_info_small.txt")) == ""


def test_parse_ntfsresize_blocker_hibernated():
    reason = fscaps.parse_ntfsresize_blocker(HIBERNATED_OUTPUT)
    assert reason == "Windows is hibernated (Fast Startup): shut Windows down fully first"


@pytest.mark.parametrize("text", REFUSAL_OUTPUTS)
def test_parse_ntfsresize_blocker_unclean(text):
    reason = fscaps.parse_ntfsresize_blocker(text)
    assert reason == "Windows didn't shut down cleanly: run chkdsk in Windows first"


def test_parse_ntfsresize_blocker_no_action_success_is_clean():
    assert fscaps.parse_ntfsresize_blocker(NO_ACTION_SUCCESS) == ""


def test_resize2fs_min():
    blocks = fscaps.parse_resize2fs_min_blocks(fixture("resize2fs_P.txt"))
    size = fscaps.parse_dumpe2fs_block_size(fixture("dumpe2fs_h.txt"))
    assert blocks == 431036
    assert size == 4096
    assert blocks * size == 1765523456


def test_btrfs_min():
    assert fscaps.parse_btrfs_min_dev_size(fixture("btrfs_min_dev_size.txt")) == 1884291072


@pytest.mark.parametrize(
    "parser",
    [
        fscaps.parse_resize2fs_min_blocks,
        fscaps.parse_dumpe2fs_block_size,
        fscaps.parse_btrfs_min_dev_size,
    ],
)
def test_parsers_reject_garbage(parser):
    with pytest.raises(ValueError):
        parser("nothing useful")


def test_shrink_commands_ext4_uses_kib():
    cmds = fscaps.shrink_fs_commands("ext4", "/dev/vda4", 2 * 1024**3, "/mnt/s")
    assert cmds == [["resize2fs", "/dev/vda4", "2097152K"]]


def test_shrink_commands_ntfs():
    cmds = fscaps.shrink_fs_commands("ntfs", "/dev/vda3", 3000000000, "/mnt/s")
    assert cmds == [
        ["ntfsresize", "--force", "--force", "--no-progress-bar", "-s", "3000000000", "/dev/vda3"]
    ]


def test_shrink_commands_btrfs_mounts_rw_then_umounts():
    cmds = fscaps.shrink_fs_commands("btrfs", "/dev/vda5", 2 * 1024**3, "/mnt/s")
    assert cmds == [
        ["mount", "-t", "btrfs", "/dev/vda5", "/mnt/s"],
        ["btrfs", "filesystem", "resize", "2147483648", "/mnt/s"],
        ["umount", "/mnt/s"],
    ]


@pytest.mark.parametrize("fstype", ["xfs", "vfat", "swap", "crypto_LUKS", ""])
def test_shrink_commands_reject_unshrinkable(fstype):
    with pytest.raises(ValueError):
        fscaps.shrink_fs_commands(fstype, "/dev/x", 1024, "/mnt/s")


def test_grow_commands():
    assert fscaps.grow_fs_commands("ext3", "/dev/a", "/mnt/s") == [["resize2fs", "/dev/a"]]
    assert fscaps.grow_fs_commands("ntfs3", "/dev/a", "/mnt/s") == [
        ["ntfsresize", "--force", "--force", "--no-progress-bar", "/dev/a"]
    ]
    assert fscaps.grow_fs_commands("btrfs", "/dev/a", "/mnt/s") == [
        ["mount", "-t", "btrfs", "/dev/a", "/mnt/s"],
        ["btrfs", "filesystem", "resize", "max", "/mnt/s"],
        ["umount", "/mnt/s"],
    ]
    assert fscaps.grow_fs_commands("xfs", "/dev/a", "/mnt/s") == [
        ["mount", "-t", "xfs", "/dev/a", "/mnt/s"],
        ["xfs_growfs", "/mnt/s"],
        ["umount", "/mnt/s"],
    ]


@pytest.mark.parametrize("fstype", ["vfat", "swap", "crypto_LUKS", ""])
def test_grow_commands_reject_unresizable(fstype):
    with pytest.raises(ValueError):
        fscaps.grow_fs_commands(fstype, "/dev/x", "/mnt/s")


def test_probe_ext4_read_only_then_preflight():
    assert fscaps.probe_min_size_commands("ext4", "/dev/a", "/mnt/s", read_only=True) == [
        ["e2fsck", "-n", "-f", "/dev/a"],
        ["resize2fs", "-P", "/dev/a"],
    ]
    assert fscaps.probe_min_size_commands("ext4", "/dev/a", "/mnt/s", read_only=False) == [
        ["e2fsck", "-f", "-y", "/dev/a"],
        ["resize2fs", "-P", "/dev/a"],
    ]


def test_probe_ntfs():
    expected = [["ntfsresize", "--info", "--no-progress-bar", "/dev/a"]]
    assert fscaps.probe_min_size_commands("ntfs", "/dev/a", "/mnt/s", read_only=True) == expected
    assert fscaps.probe_min_size_commands("ntfs", "/dev/a", "/mnt/s", read_only=False) == expected


def test_probe_btrfs_mount_modes():
    ro = fscaps.probe_min_size_commands("btrfs", "/dev/a", "/mnt/s", read_only=True)
    assert ro == [
        ["mount", "-t", "btrfs", "-o", "ro,rescue=nologreplay", "/dev/a", "/mnt/s"],
        ["btrfs", "inspect-internal", "min-dev-size", "/mnt/s"],
        ["umount", "/mnt/s"],
    ]
    rw = fscaps.probe_min_size_commands("btrfs", "/dev/a", "/mnt/s", read_only=False)
    assert rw[0] == ["mount", "-t", "btrfs", "/dev/a", "/mnt/s"]
    assert rw[1:] == ro[1:]


@pytest.mark.parametrize("fstype", ["ext4", "ntfs", "btrfs"])
def test_probe_read_only_never_writes(fstype):
    cmds = fscaps.probe_min_size_commands(fstype, "/dev/a", "/mnt/s", read_only=True)
    flat = [arg for cmd in cmds for arg in cmd]
    assert "-y" not in flat
    for cmd in cmds:
        if cmd[0] == "mount":
            assert "ro" in cmd[cmd.index("-o") + 1].split(",")


@pytest.mark.parametrize("fstype", ["xfs", "vfat", "swap", ""])
def test_probe_rejects_unshrinkable(fstype):
    with pytest.raises(ValueError):
        fscaps.probe_min_size_commands(fstype, "/dev/x", "/mnt/s", read_only=True)


def test_preflight_shrink_check_commands():
    assert fscaps.preflight_shrink_check_commands("ntfs", "/dev/a", 5000, "/mnt/s") == [
        ["ntfsresize", "--no-action", "--force", "--no-progress-bar", "-s", "5000", "/dev/a"]
    ]
    for fstype in ("ext4", "btrfs"):
        assert fscaps.preflight_shrink_check_commands(
            fstype, "/dev/a", 5000, "/mnt/s"
        ) == fscaps.probe_min_size_commands(fstype, "/dev/a", "/mnt/s", read_only=False)


def test_ntfs_hibernation_magic() -> None:
    assert fscaps.parse_ntfs_hibernated("hibr")
    assert fscaps.parse_ntfs_hibernated("HIBR\n")
    assert not fscaps.parse_ntfs_hibernated("")
    assert not fscaps.parse_ntfs_hibernated("wake")
    cmd = fscaps.ntfs_hibernation_command("/dev/vda3")
    assert cmd[:2] == ["sh", "-c"] and cmd[-1] == "/dev/vda3" and "hiberfil.sys" in cmd[2]

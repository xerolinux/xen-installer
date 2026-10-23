import re
from dataclasses import dataclass

from xen.i18n.messages import msg


@dataclass(frozen=True)
class FsCaps:
    fstype: str
    can_shrink: bool
    can_grow: bool
    resize_needs_mount: bool
    blocked_reason: str = ""


MIB = 1024**2
SHRINK_MARGIN = 8 * MIB
_EXT = ("ext2", "ext3", "ext4")
_REASON_FAT = msg("FAT partitions can't be resized here")
_REASON_UNKNOWN = msg("Unknown or empty partition")
REASON_HIBERNATED = msg("Windows is hibernated (Fast Startup): shut Windows down fully first")
REASON_EXT_UNCLEAN = msg(
    "This partition was not shut down cleanly. "
    "Start its own system and shut it down normally first."
)
REASON_UNCLEAN = msg("Windows didn't shut down cleanly: run chkdsk in Windows first")

_BLOCKED = {
    "xfs": msg("XFS can't be shrunk"),
    "vfat": _REASON_FAT,
    "fat32": _REASON_FAT,
    "swap": msg("Swap can't be resized; delete and recreate it"),
    "crypto_LUKS": msg("Encrypted (LUKS): Format or Delete it to use it"),
    "BitLocker": msg("BitLocker is on: turn it off in Windows first"),
}


def fs_kind(fstype: str) -> str:
    if fstype in _EXT:
        return "ext"
    if fstype in ("ntfs", "ntfs3"):
        return "ntfs"
    return fstype


def caps_for(fstype: str) -> FsCaps:
    kind = fs_kind(fstype)
    if kind in ("ext", "ntfs"):
        return FsCaps(fstype, True, True, False)
    if kind == "btrfs":
        return FsCaps(fstype, True, True, True)
    if kind == "xfs":
        return FsCaps(fstype, False, True, True, _BLOCKED["xfs"])
    return FsCaps(fstype, False, False, False, _BLOCKED.get(fstype, _REASON_UNKNOWN))


def min_shrink_bytes(min_bytes: int) -> int:
    # The shrink sets the file system to the new size minus SHRINK_MARGIN, floored to whole MiB.
    return -(-int(min_bytes) // MIB) * MIB + SHRINK_MARGIN


def _search(pattern: str, text: str, what: str, flags: int = 0) -> int:
    match = re.search(pattern, text, flags)
    if not match:
        raise ValueError(msg("no {what} in output", what=what))
    return int(match.group(1))


def parse_ntfsresize_min(text: str) -> int:
    return _search(r"You might resize at (\d+) bytes", text, msg("ntfsresize minimum size"))


def parse_ntfsresize_blocker(text: str) -> str:
    if re.search(r"hibernat", text, re.I):
        return REASON_HIBERNATED
    refusals = r"NTFS is inconsistent|Volume is scheduled for check|journal file is unclean"
    if re.search(refusals, text):
        return REASON_UNCLEAN
    return ""


def ntfs_hibernation_command(dev: str) -> list[str]:
    # hiberfil.sys can be many GiB: read only its 4-byte magic, as letters so it decodes as text
    script = 'ntfscat "$1" hiberfil.sys 2>/dev/null | head -c 4 | tr -dc A-Za-z'
    return ["sh", "-c", script, "sh", dev]


def parse_ntfs_hibernated(text: str) -> bool:
    return text.strip().lower() == "hibr"


def parse_resize2fs_min_blocks(text: str) -> int:
    pattern = r"Estimated minimum size of the filesystem:\s*(\d+)"
    return _search(pattern, text, msg("resize2fs minimum size"))


def parse_ext_needs_recovery(dumpe2fs_header: str) -> bool:
    match = re.search(r"^Filesystem features:(.*)$", dumpe2fs_header, re.M)
    return bool(match) and "needs_recovery" in match.group(1).split()


def parse_dumpe2fs_block_size(text: str) -> int:
    return _search(r"^Block size:\s*(\d+)", text, msg("block size"), re.M)


def parse_btrfs_min_dev_size(text: str) -> int:
    return _search(r"^(\d+) bytes", text, msg("btrfs minimum size"), re.M)


def _require(fstype: str, action: str) -> str:
    caps = caps_for(fstype)
    if not getattr(caps, f"can_{action}"):
        raise ValueError(f"{fstype or 'unknown'}: {caps.blocked_reason}")
    return fs_kind(fstype)


def _mounted(
    fstype: str, dev: str, scratch: str, *cmds: list[str], mount_opts: str = ""
) -> list[list[str]]:
    mount = ["mount", "-t", fstype, *(["-o", mount_opts] if mount_opts else []), dev, scratch]
    return [mount, *cmds, ["umount", scratch]]


def probe_min_size_commands(
    fstype: str, dev: str, scratch_mount: str, read_only: bool
) -> list[list[str]]:
    kind = _require(fstype, "shrink")
    if kind == "ext":
        return [
            ["e2fsck", "-n", "-f", dev] if read_only else ["e2fsck", "-f", "-y", dev],
            ["resize2fs", "-P", dev],
        ]
    if kind == "ntfs":
        return [["ntfsresize", "--info", "--no-progress-bar", dev]]
    return _mounted(
        "btrfs",
        dev,
        scratch_mount,
        ["btrfs", "inspect-internal", "min-dev-size", scratch_mount],
        mount_opts="ro,rescue=nologreplay" if read_only else "",
    )


def shrink_fs_commands(
    fstype: str, dev: str, new_bytes: int, scratch_mount: str
) -> list[list[str]]:
    kind = _require(fstype, "shrink")
    if kind == "ext":
        return [["resize2fs", dev, f"{new_bytes // 1024}K"]]
    if kind == "ntfs":
        return [
            ["ntfsresize", "--force", "--force", "--no-progress-bar", "-s", str(new_bytes), dev]
        ]
    resize = ["btrfs", "filesystem", "resize", str(new_bytes), scratch_mount]
    return _mounted("btrfs", dev, scratch_mount, resize)


def grow_fs_commands(fstype: str, dev: str, scratch_mount: str) -> list[list[str]]:
    kind = _require(fstype, "grow")
    if kind == "ext":
        return [["resize2fs", dev]]
    if kind == "ntfs":
        return [["ntfsresize", "--force", "--force", "--no-progress-bar", dev]]
    if kind == "btrfs":
        resize = ["btrfs", "filesystem", "resize", "max", scratch_mount]
        return _mounted("btrfs", dev, scratch_mount, resize)
    return _mounted("xfs", dev, scratch_mount, ["xfs_growfs", scratch_mount])


def preflight_shrink_check_commands(
    fstype: str, dev: str, new_bytes: int, scratch_mount: str
) -> list[list[str]]:
    if _require(fstype, "shrink") == "ntfs":
        return [
            ["ntfsresize", "--no-action", "--force", "--no-progress-bar", "-s", str(new_bytes), dev]
        ]
    return probe_min_size_commands(fstype, dev, scratch_mount, read_only=False)

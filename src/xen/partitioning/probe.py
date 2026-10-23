"""Read-only disk probes: nothing here may write to a scanned partition."""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from xen.i18n.messages import msg
from xen.partitioning import fscaps

logger = logging.getLogger(__name__)

REASON_IN_USE = msg("In use")
_TIMEOUT = 120
# a forced check of a large, full ext4 reads every inode
_FSCK_TIMEOUT = 900
_OS_PROBER_TIMEOUT = 300
_NTFS = ("ntfs", "ntfs3")
_EXT = ("ext2", "ext3", "ext4")
_WINDOWS = "Windows"


class _ProbeFailed(Exception):
    pass


class _Blocked(Exception):
    pass


def _run(cmd: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, -1, "", str(exc))


def _tail(proc: subprocess.CompletedProcess) -> str:
    for text in (proc.stderr, proc.stdout):
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return msg(
        "{command} exited with status {status}", command=proc.args[0], status=proc.returncode
    )


def _checked(cmd: list[str], timeout: int = _TIMEOUT) -> str:
    proc = _run(cmd, timeout)
    if proc.returncode:
        raise _ProbeFailed(_tail(proc))
    return proc.stdout


def _umount(target: str) -> None:
    proc = _run(["umount", target])
    if proc.returncode:
        logger.warning("umount %s failed (%s), detaching it lazily", target, _tail(proc))
        _run(["umount", "-l", target])


def _run_all(cmds: list[list[str]]) -> list[str]:
    if cmds[0][0] != "mount":
        return [_checked(cmd) for cmd in cmds]
    _checked(cmds[0])
    try:
        return [_checked(cmd) for cmd in cmds[1:-1]]
    finally:
        _umount(cmds[-1][-1])


def _blkid_type(dev: str) -> str:
    return _run(["blkid", "-p", "-o", "value", "-s", "TYPE", dev]).stdout.strip()


def _hibernated(dev: str) -> bool:
    # hiberfil.sys can be many GiB, so read only the magic and kill ntfscat
    try:
        proc = subprocess.Popen(
            ["ntfscat", dev, "hiberfil.sys"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except OSError:
        return False
    timer = threading.Timer(_TIMEOUT, proc.kill)
    timer.start()
    try:
        head = proc.stdout.read(4)
    finally:
        timer.cancel()
        proc.kill()
        proc.wait()
        proc.stdout.close()
    return head in (b"hibr", b"HIBR")


def _ntfs_min(dev: str, cmd: list[str]) -> int:
    if _hibernated(dev):
        raise _Blocked(fscaps.REASON_HIBERNATED)
    proc = _run(cmd)
    blocker = fscaps.parse_ntfsresize_blocker(f"{proc.stdout}\n{proc.stderr}")
    if blocker:
        raise _Blocked(blocker)
    if proc.returncode:
        raise _ProbeFailed(_tail(proc))
    try:
        return fscaps.parse_ntfsresize_min(proc.stdout)
    except ValueError:
        raise _ProbeFailed(_tail(proc)) from None


def _min_size(fstype: str, dev: str, scratch_dir: str) -> int:
    cmds = fscaps.probe_min_size_commands(fstype, dev, scratch_dir, read_only=True)
    try:
        if fstype in _NTFS:
            return _ntfs_min(dev, cmds[0])
        if fstype in _EXT:
            header = _checked(["dumpe2fs", "-h", dev])
            if fscaps.parse_ext_needs_recovery(header):
                raise _Blocked(fscaps.REASON_EXT_UNCLEAN)
            fsck, resize = cmds
            _checked(fsck, _FSCK_TIMEOUT)
            blocks = fscaps.parse_resize2fs_min_blocks(_checked(resize))
            return blocks * fscaps.parse_dumpe2fs_block_size(header)
        (out,) = _run_all(cmds)
        return fscaps.parse_btrfs_min_dev_size(out)
    except ValueError as exc:
        raise _ProbeFailed(exc.args[0]) from None


def probe_partition(part: dict[str, Any], scratch_dir: str) -> dict[str, Any]:
    if part.get("mounted"):
        return {"minSizeBytes": 0, "resizeBlockedReason": REASON_IN_USE, "bitlocker": False}
    dev = part["path"]
    fstype = _blkid_type(dev) or part.get("fstype") or ""
    result = {"minSizeBytes": 0, "resizeBlockedReason": "", "bitlocker": fstype == "BitLocker"}
    caps = fscaps.caps_for(fstype)
    if not caps.can_shrink:
        result["resizeBlockedReason"] = caps.blocked_reason
        return result
    try:
        result["minSizeBytes"] = _min_size(fstype, dev, scratch_dir)
    except _Blocked as exc:
        result["resizeBlockedReason"] = exc.args[0]
    except _ProbeFailed as exc:
        result["resizeBlockedReason"] = msg(
            "Couldn't check this partition: {error}", error=exc.args[0]
        )
    return result


def _mark_windows(esp: dict[str, Any], parts: list[dict[str, Any]], names: dict[str, str]) -> None:
    for part in parts:
        if part.get("disk") == esp.get("disk") and part.get("fstype") in _NTFS:
            names.setdefault(part["path"], _WINDOWS)


def _from_os_prober(parts: list[dict[str, Any]], names: dict[str, str]) -> None:
    by_path = {part["path"]: part for part in parts}
    for line in _run(["os-prober"], timeout=_OS_PROBER_TIMEOUT).stdout.splitlines():
        fields = line.split(":")
        if len(fields) < 4:
            continue
        dev, name = fields[0], fields[1]
        if "@" in dev:
            esp_path, loader = dev.split("@", 1)
            if esp_path in by_path and "/microsoft/" in loader.lower():
                _mark_windows(by_path[esp_path], parts, names)
        elif dev in by_path and name:
            names[dev] = name


@contextmanager
def _ro_mount(dev: str, fstype: str, opts: str, target: str) -> Iterator[bool]:
    mounted = _run(["mount", "-t", fstype, "-o", opts, dev, target]).returncode == 0
    try:
        yield mounted
    finally:
        if mounted:
            _umount(target)


def _inside(root: str, rel: str) -> str | None:
    path = os.path.realpath(os.path.join(root, rel))
    real_root = os.path.realpath(root)
    return path if path.startswith(real_root + os.sep) and os.path.isfile(path) else None


def _pretty_name(root: str) -> str:
    # an absolute os-release symlink would resolve onto the live system, so stay inside root
    for rel in ("etc/os-release", "usr/lib/os-release"):
        path = _inside(root, rel)
        if not path:
            continue
        fields = {}
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, sep, value = line.strip().partition("=")
                if sep:
                    fields[key] = value.strip().strip("\"'")
        name = fields.get("PRETTY_NAME") or fields.get("NAME")
        if name:
            return name
    return ""


def _esp_has_windows(dev: str, scratch: str) -> bool:
    with _ro_mount(dev, "vfat", "ro", scratch) as ok:
        return ok and _inside(scratch, "EFI/Microsoft/Boot/bootmgfw.efi") is not None


def _linux_name(part: dict[str, Any], scratch: str) -> str:
    fstype = part.get("fstype")
    if fstype in _EXT:
        attempts = ["ro,noload"]
    elif fstype == "btrfs":
        attempts = ["ro,rescue=nologreplay,subvol=@", "ro,rescue=nologreplay"]
    else:
        return ""
    for opts in attempts:
        with _ro_mount(part["path"], fstype, opts, scratch) as ok:
            name = _pretty_name(scratch) if ok else ""
        if name:
            return name
    return ""


def _from_mounts(parts: list[dict[str, Any]], names: dict[str, str]) -> None:
    scratch = tempfile.mkdtemp(prefix="xen-osprobe-")
    try:
        for part in parts:
            if part.get("mounted"):
                continue
            if part.get("isEsp"):
                if _esp_has_windows(part["path"], scratch):
                    _mark_windows(part, parts, names)
                continue
            name = _linux_name(part, scratch)
            if name:
                names[part["path"]] = name
    finally:
        with suppress(OSError):
            os.rmdir(scratch)


def detect_os(parts: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    if shutil.which("os-prober"):
        _from_os_prober(parts, names)
    else:
        _from_mounts(parts, names)
    return names

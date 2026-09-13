"""
Release a target disk from every holder before destructive partitioning.

A disk freshly formatted with an external tool (GParted) is usually still held by
the running session: the desktop auto-mounts the new partitions (udisks), swap may
be active and LUKS/LVM/md mappers may sit on top. ``wipefs`` then fails with EBUSY.
This module enumerates those holders and tears them down.

Xen runs both from a live ISO and as an installed native package on an
already-installed system, so the disk carrying the *running* system is
detected by its mountpoints rather than by assuming a live medium.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

# Mountpoints whose loss breaks the running Xen process, whether it runs from a
# live ISO (/iso) or as an installed package on the target system itself (/usr).
# A disk backing any of them must never be released nor wiped.
_CRITICAL_MOUNTS = ("/usr", "/iso")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError) as exc:
        logger.debug("Command unavailable: %s (%s)", cmd[0], exc)
        return subprocess.CompletedProcess(cmd, returncode=127, stdout="", stderr=str(exc))


def _is_critical_mount(target: str) -> bool:
    if target == "/":
        return True
    return any(target == mount or target.startswith(f"{mount}/") for mount in _CRITICAL_MOUNTS)


def disk_members(disk: str) -> list[str]:
    """Return ``disk`` plus every partition and stacked device below it."""
    result = _run(["lsblk", "-nrpo", "NAME", disk])
    if result.returncode != 0:
        return [disk]
    members = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return members or [disk]


def _member_types(disk: str) -> list[tuple[str, str]]:
    result = _run(["lsblk", "-nrpo", "NAME,TYPE", disk])
    if result.returncode != 0:
        return []
    pairs: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            pairs.append((fields[0], fields[1]))
    return pairs


def _mountpoints(device: str) -> list[str]:
    result = _run(["findmnt", "-rn", "-o", "TARGET", "-S", device])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _swap_devices() -> set[str]:
    try:
        with open("/proc/swaps", encoding="utf-8") as handle:
            lines = handle.read().splitlines()[1:]
    except OSError:
        return set()
    return {fields[0] for line in lines if (fields := line.split())}


def holds_running_system(disk: str) -> bool:
    """Return True when ``disk`` backs the system Xen is currently running from."""
    return any(
        _is_critical_mount(target)
        for member in disk_members(disk)
        for target in _mountpoints(member)
    )


def disk_holders(disk: str) -> list[str]:
    """Return a human-readable list of everything still holding ``disk``."""
    swaps = _swap_devices()
    holders: list[str] = []
    for member in disk_members(disk):
        holders.extend(f"{member} is mounted on {target}" for target in _mountpoints(member))
        if member in swaps:
            holders.append(f"{member} is in use as swap")
    return holders


def release_disk(disk: str) -> list[str]:
    """
    Free ``disk`` so wipefs/parted can open it exclusively.

    Unmounts every mountpoint backed by the disk (deepest first), disables swap and
    tears down stacked mappers. Best-effort: whatever survives is reported by
    :func:`disk_holders`. Callers must reject a disk backing the running system
    (see :func:`holds_running_system`) before calling this.
    """
    actions: list[str] = []
    members = disk_members(disk)

    mounts = [(target, member) for member in members for target in _mountpoints(member)]
    for target, member in sorted(mounts, key=lambda mount: mount[0].count("/"), reverse=True):
        if _is_critical_mount(target):
            continue
        if _run(["umount", "-R", target]).returncode == 0:
            actions.append(f"unmounted {member} from {target}")

    swaps = _swap_devices()
    for member in members:
        if member in swaps and _run(["swapoff", member]).returncode == 0:
            actions.append(f"disabled swap on {member}")

    for member, kind in reversed(_member_types(disk)):
        if kind == "crypt" and _run(["cryptsetup", "close", member]).returncode == 0:
            actions.append(f"closed LUKS mapper {member}")
        elif kind == "lvm" and _run(["lvchange", "-an", member]).returncode == 0:
            actions.append(f"deactivated logical volume {member}")
        elif kind.startswith("raid") and _run(["mdadm", "--stop", member]).returncode == 0:
            actions.append(f"stopped RAID array {member}")

    _run(["udevadm", "settle"])
    return actions

"""
Refuse to partition the disk the running system lives on.

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
# A disk backing any of them must never be wiped.
_CRITICAL_MOUNTS = ("/usr", "/iso")
_COMMAND_TIMEOUT = 60


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("Command timed out after %ss: %s", _COMMAND_TIMEOUT, " ".join(cmd))
        return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr=str(exc))
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


def _mountpoints(device: str) -> list[str]:
    result = _run(["findmnt", "-rn", "-o", "TARGET", "-S", device])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def holds_running_system(disk: str) -> bool:
    """Return True when ``disk`` backs the system Xen is currently running from."""
    return any(
        _is_critical_mount(target)
        for member in disk_members(disk)
        for target in _mountpoints(member)
    )

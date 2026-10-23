"""
Unified disk detection for the Xen installer.

This module is the SINGLE source of truth for disk enumeration. Both the
partition job (``xen.jobs.partition``) and the QML bridge
(``xen.gui.bridge``) consume :func:`list_disks` so the UI histobar and the
partitioning engine always see the same view of the hardware.

The live system disk (the medium carrying the running installer/ISO) is
excluded from the returned list to prevent the installer from offering to wipe
itself.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from contextlib import suppress
from typing import Any

from xen.partitioning import fscaps, probe

logger = logging.getLogger(__name__)

# lsblk columns requested (bytes for SIZE, plus topology/identity fields).
_LSBLK_COLUMNS = (
    "NAME,SIZE,TYPE,FSTYPE,MODEL,RM,HOTPLUG,TRAN,ROTA,PARTTYPENAME,START,LOG-SEC,PTTYPE,"
    "PATH,PKNAME,PARTN,PARTTYPE,PARTUUID,PARTLABEL,LABEL,UUID,MOUNTPOINTS"
)
ESP_TYPE_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
LINUX_TYPE_GUID = "0fc63daf-8483-4772-8e79-3d69d8477de4"
SWAP_TYPE_GUID = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"

# lsblk START is always in 512-byte units, whatever the disk's logical sector
# size; geometry handed to callers is in the disk's own logical sectors
# (parted's "s" unit), see "sectorSize".
_SECTOR_SIZE = 512
_GPT_TAIL = 34  # sectors reserved for the GPT secondary header
_COMMAND_TIMEOUT = 10
_GPT_ENTRIES_BYTES = 128 * 128


def align_sectors(sector_size: int) -> int:
    return 1024 * 1024 // sector_size


def last_usable_sector(disk_sectors: int, sector_size: int) -> int:
    backup = 1 + -(-_GPT_ENTRIES_BYTES // sector_size)
    return max(0, disk_sectors - 1 - backup)


def _coerce_bool(value: Any) -> bool:
    """
    Coerce a heterogeneous lsblk flag to a boolean.

    util-linux exposes RM/HOTPLUG/ROTA as booleans (newer) or as ``"0"``/``"1"``
    / ``0``/``1`` (older). This helper normalises all of those forms.
    """
    return value in (1, "1", True)


def _format_size(size_bytes: int) -> str:
    """Format a byte size to a human-readable string (e.g. ``"500.0 GB"``)."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _classify_part_type(fstype: str, parttypename: str) -> str:
    """
    Deduce a coarse partition category for UI rendering.

    Returns one of ``"efi"``, ``"linux"``, ``"windows"``, ``"swap"`` or
    ``"other"``.
    """
    fs = (fstype or "").lower()
    ptn = (parttypename or "").lower()

    # EFI System Partition: vfat backed and flagged as an EFI partition.
    if "efi" in ptn:
        return "efi"
    # Windows / Microsoft data or reserved partitions.
    if fs == "ntfs" or "microsoft" in ptn or "windows" in ptn:
        return "windows"
    # Swap.
    if fs == "swap" or "swap" in ptn:
        return "swap"
    # Linux native filesystems.
    if fs in ("ext4", "ext3", "ext2", "btrfs", "xfs"):
        return "linux"
    # Bare vfat without an EFI parttype is treated as "other" per contract.
    return "other"


def _fallback_partition_path(disk: str, number: int) -> str:
    base = disk.rsplit("/", 1)[-1]
    sep = "p" if base[-1:].isdigit() else ""
    return f"/dev/{base}{sep}{number}"


def partition_path(disk: str, number: int) -> str:
    base = disk.rsplit("/", 1)[-1]
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-l", "-o", "PATH,PKNAME,PARTN"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
        )
        rows = json.loads(result.stdout).get("blockdevices", []) if result.returncode == 0 else []
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        rows = []
    for row in rows:
        if row.get("pkname") == base and row.get("partn") == number and row.get("path"):
            return row["path"]
    return _fallback_partition_path(base, number)


def _resize_fields(fstype: str, mounted: bool) -> dict[str, Any]:
    caps = fscaps.caps_for(fstype)
    return {
        "canShrink": caps.can_shrink and not mounted,
        "canGrow": caps.can_grow and not mounted,
        "minSizeBytes": 0,
        "resizeBlockedReason": probe.REASON_IN_USE if mounted else caps.blocked_reason,
    }


def _live_source() -> str | None:
    """
    Return the block-device source backing the live root filesystem.

    Uses ``findmnt -no SOURCE /``. On a live ISO the root is often an overlay,
    tmpfs or airootfs which is NOT a block device; in that case we return
    ``None`` so the caller excludes nothing.
    """
    try:
        result = subprocess.run(
            ["findmnt", "-no", "SOURCE", "/"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("findmnt unavailable, cannot detect live disk: %s", exc)
        return None

    if result.returncode != 0:
        return None

    source = result.stdout.strip()
    if not source:
        return None

    # Non-block-device sources (live ISO): exclude nothing.
    if not source.startswith("/dev/"):
        logger.debug("Live root source is not a block device: %s", source)
        return None

    return source


def _is_live_disk(device: dict[str, Any], live_source: str | None) -> bool:
    """
    Return True if ``device`` (an lsblk disk node) carries the live system.

    Walks the disk's children (partitions) and compares their device path /
    name against the live source. Also handles the degenerate case where the
    live source IS the disk itself.
    """
    if not live_source:
        return False

    name = device.get("name", "")
    disk_path = f"/dev/{name}"

    if live_source in (disk_path, name):
        return True

    live_tail = live_source.rsplit("/", 1)[-1]
    for child in device.get("children", []):
        child_name = child.get("name", "")
        child_path = f"/dev/{child_name}"
        if live_source in (child_path, child_name):
            return True
        # Defensive: /dev/mapper/* or dm-* devices may sit above the partition.
        if child_name and child_name == live_tail:
            return True

    return False


def _build_partition(child: dict[str, Any], sector_size: int, disk_name: str) -> dict[str, Any]:
    """Build a single partition dict from an lsblk child node."""
    name = child.get("name", "")
    fstype = child.get("fstype") or ""
    parttypename = child.get("parttypename") or ""
    part_type_guid = (child.get("parttype") or "").lower()
    size_bytes = int(child.get("size", 0) or 0)
    start_sector = int(child.get("start", 0) or 0) * _SECTOR_SIZE // sector_size
    size_sectors = size_bytes // sector_size
    # a holder (opened LUKS, LVM, RAID) keeps the partition busy even with no mountpoint of its own
    mounted = any(child.get("mountpoints") or []) or bool(child.get("children"))
    return {
        "name": name,
        "path": child.get("path") or f"/dev/{name}",
        "disk": disk_name,
        "number": int(child.get("partn") or 0),
        "sizeBytes": size_bytes,
        "fstype": fstype,
        "partType": _classify_part_type(fstype, parttypename),
        "partTypeGuid": part_type_guid,
        "partUuid": child.get("partuuid") or "",
        "partLabel": child.get("partlabel") or "",
        "label": child.get("label") or "",
        "isEsp": part_type_guid == ESP_TYPE_GUID,
        "mounted": mounted,
        "bitlocker": fstype == "BitLocker",
        "osName": "",
        **_resize_fields(fstype, mounted),
        "startSector": start_sector,
        "sizeSectors": size_sectors,
        "endSector": (start_sector + size_sectors - 1) if size_sectors else start_sector,
    }


def compute_segments(
    disk_sectors: int, partitions: list[dict[str, Any]], sector_size: int = _SECTOR_SIZE
) -> list[dict[str, Any]]:
    """
    Interleave partitions and free-space regions across the whole disk.

    Produces an ordered list of segments (``kind`` = ``"partition"`` or
    ``"free"``) covering the disk from the first aligned usable sector to the
    GPT tail. Free regions smaller than 1 MiB (alignment noise, GPT headers)
    are omitted so the UI only offers usable gaps.
    """
    segments: list[dict[str, Any]] = []
    usable_end = last_usable_sector(disk_sectors, sector_size)
    align = align_sectors(sector_size)
    cursor = align

    def add_free(start: int, end: int) -> None:
        # Only surface gaps larger than the 1 MiB alignment unit; sub-MiB gaps
        # are padding (GPT headers, alignment) and never usable for a partition.
        if end - start + 1 > align:
            segments.append(
                {
                    "kind": "free",
                    "startSector": start,
                    "endSector": end,
                    "sizeSectors": end - start + 1,
                    "sizeBytes": (end - start + 1) * sector_size,
                }
            )

    for part in sorted(partitions, key=lambda p: int(p.get("startSector") or 0)):
        start = int(part.get("startSector") or 0)
        if start > cursor:
            add_free(cursor, start - 1)
        segments.append({"kind": "partition", **part})
        end = int(part.get("endSector") or start)
        cursor = max(cursor, end + 1)

    if cursor <= usable_end:
        add_free(cursor, usable_end)

    return segments


def _build_disk(device: dict[str, Any]) -> dict[str, Any]:
    """Build a single disk dict (UI contract) from an lsblk disk node."""
    size_bytes = int(device.get("size", 0) or 0)
    model = (device.get("model") or "").strip()
    is_rotational = _coerce_bool(device.get("rota"))
    removable = _coerce_bool(device.get("hotplug")) or _coerce_bool(device.get("rm"))

    sector_size = int(device.get("log-sec") or _SECTOR_SIZE)
    name = device.get("name", "")
    partitions = [
        _build_partition(child, sector_size, name)
        for child in device.get("children", [])
        if child.get("type") == "part"
    ]

    return {
        "name": name,
        "model": model,
        "size": _format_size(size_bytes),
        "sizeBytes": size_bytes,
        "sizeSectors": size_bytes // sector_size,
        "sectorSize": sector_size,
        "ptType": device.get("pttype") or "",
        "type": "HDD" if is_rotational else "SSD",
        "transport": (device.get("tran") or "").lower(),
        "removable": removable,
        "partitions": partitions,
    }


def _probe_partitions(parts: list[dict[str, Any]]) -> None:
    scratch = tempfile.mkdtemp(prefix="xen-scan-")
    try:
        for part in parts:
            if part["mounted"]:
                continue
            result = probe.probe_partition(part, scratch)
            part.update(result)
            part["canShrink"] = part["canShrink"] and not part["resizeBlockedReason"]
            if part["bitlocker"]:
                part["canShrink"] = part["canGrow"] = False
    finally:
        # never rmtree: a failed umount would leave the scanned filesystem underneath
        with suppress(OSError):
            os.rmdir(scratch)
    for path, name in probe.detect_os(parts).items():
        for part in parts:
            if part["path"] == path:
                part["osName"] = name


def list_disks(probe: bool = False) -> list[dict[str, Any]]:
    """Installable disks (the live medium excluded) as UI dicts.

    A failed lsblk gives [].
    ``probe=True`` adds the slow read-only min-size and OS probes; call it off the UI thread.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", _LSBLK_COLUMNS],
            check=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            raise OSError(f"lsblk returned {result.returncode}: {result.stderr.strip()}")
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        logger.error("Disk scan failed: %s", exc)
        return []

    live_source = _live_source()
    candidates: list[dict[str, Any]] = []
    non_live: list[dict[str, Any]] = []

    for device in data.get("blockdevices", []):
        name = device.get("name", "")
        # Only real disks: skip loop, rom and bare partitions at top level.
        if device.get("type") != "disk":
            continue
        # Skip virtual / non-installable block devices: RAM-backed swap (zram),
        # loopback (squashfs on live media), ramdisks, floppy, optical, nbd.
        if name.startswith(("zram", "loop", "ram", "fd", "sr", "nbd")):
            continue
        candidates.append(device)
        if not _is_live_disk(device, live_source):
            non_live.append(device)

    # Normally exclude the disk carrying the running system. But if doing so
    # would leave nothing to install to (e.g. a single-disk machine that
    # isn't actually booted from removable live media), show every disk
    # instead of leaving the user with an empty, dead-end disk list.
    devices_to_show = non_live if non_live else candidates
    if not non_live and candidates:
        logger.warning(
            "Live-disk exclusion would leave zero installable disks; showing all %d disk(s) instead",
            len(candidates),
        )
    elif len(non_live) < len(candidates):
        logger.debug("Excluded %d live disk(s)", len(candidates) - len(non_live))

    disks = [_build_disk(device) for device in devices_to_show]
    if probe:
        _probe_partitions([part for disk in disks for part in disk["partitions"]])
    for disk in disks:
        disk["segments"] = compute_segments(
            disk["sizeSectors"], disk["partitions"], disk["sectorSize"]
        )
    return disks

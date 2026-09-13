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
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# lsblk columns requested (bytes for SIZE, plus topology/identity fields).
_LSBLK_COLUMNS = "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,RM,HOTPLUG,TRAN,ROTA,PARTTYPENAME,START"

# Disk geometry constants (lsblk reports START in 512-byte sectors).
_SECTOR_SIZE = 512
_ALIGN = 2048  # 1 MiB alignment / first usable sector
_GPT_TAIL = 34  # sectors reserved for the GPT secondary header


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
        )
    except (FileNotFoundError, OSError) as exc:
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


def _finalize_mock_disk(disk: dict[str, Any]) -> dict[str, Any]:
    """Lay mock partitions out sequentially and attach geometry + segments."""
    disk_sectors = int(disk["sizeBytes"]) // _SECTOR_SIZE
    cursor = _ALIGN
    for part in disk["partitions"]:
        size_sectors = int(part["sizeBytes"]) // _SECTOR_SIZE
        part["startSector"] = cursor
        part["sizeSectors"] = size_sectors
        part["endSector"] = cursor + size_sectors - 1
        cursor += size_sectors
    disk["sizeSectors"] = disk_sectors
    disk["sectorSize"] = _SECTOR_SIZE
    disk["segments"] = _compute_segments(disk_sectors, disk["partitions"])
    return disk


def _mock_disks() -> list[dict[str, Any]]:
    """Return a deterministic mock disk list using the shared UI contract."""
    _disks = [
        {
            "name": "sda",
            "model": "Mock SSD 500G",
            "size": _format_size(500 * 1024**3),
            "sizeBytes": 500 * 1024**3,
            "type": "SSD",
            "removable": False,
            "partitions": [
                {
                    "name": "sda1",
                    "sizeBytes": 512 * 1024**2,
                    "fstype": "vfat",
                    "partType": "efi",
                },
                {
                    "name": "sda2",
                    "sizeBytes": 499 * 1024**3,
                    "fstype": "ext4",
                    "partType": "linux",
                },
            ],
        },
        {
            "name": "nvme0n1",
            "model": "Mock NVMe 1T",
            "size": _format_size(1024**4),
            "sizeBytes": 1024**4,
            "type": "SSD",
            "removable": False,
            "partitions": [],
        },
    ]
    return [_finalize_mock_disk(d) for d in _disks]


def _build_partition(child: dict[str, Any]) -> dict[str, Any]:
    """Build a single partition dict from an lsblk child node."""
    fstype = child.get("fstype") or ""
    parttypename = child.get("parttypename") or ""
    size_bytes = int(child.get("size", 0) or 0)
    start_sector = int(child.get("start", 0) or 0)
    size_sectors = size_bytes // _SECTOR_SIZE
    return {
        "name": child.get("name", ""),
        "sizeBytes": size_bytes,
        "fstype": fstype,
        "partType": _classify_part_type(fstype, parttypename),
        "startSector": start_sector,
        "sizeSectors": size_sectors,
        "endSector": (start_sector + size_sectors - 1) if size_sectors else start_sector,
    }


def _compute_segments(disk_sectors: int, partitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Interleave partitions and free-space regions across the whole disk.

    Produces an ordered list of segments (``kind`` = ``"partition"`` or
    ``"free"``) covering the disk from the first aligned usable sector to the
    GPT tail. Free regions smaller than 1 MiB (alignment noise, GPT headers)
    are omitted so the UI only offers usable gaps.
    """
    segments: list[dict[str, Any]] = []
    usable_end = max(0, disk_sectors - _GPT_TAIL)
    cursor = _ALIGN

    def add_free(start: int, end: int) -> None:
        # Only surface gaps larger than the 1 MiB alignment unit; sub-MiB gaps
        # are padding (GPT headers, alignment) and never usable for a partition.
        if end - start + 1 > _ALIGN:
            segments.append(
                {
                    "kind": "free",
                    "startSector": start,
                    "endSector": end,
                    "sizeSectors": end - start + 1,
                    "sizeBytes": (end - start + 1) * _SECTOR_SIZE,
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

    partitions = [
        _build_partition(child)
        for child in device.get("children", [])
        if child.get("type") == "part"
    ]

    disk_sectors = size_bytes // _SECTOR_SIZE
    return {
        "name": device.get("name", ""),
        "model": model,
        "size": _format_size(size_bytes),
        "sizeBytes": size_bytes,
        "sizeSectors": disk_sectors,
        "sectorSize": _SECTOR_SIZE,
        "type": "HDD" if is_rotational else "SSD",
        "removable": removable,
        "partitions": partitions,
        "segments": _compute_segments(disk_sectors, partitions),
    }


def list_disks() -> list[dict[str, Any]]:
    """
    Enumerate installable disks, excluding the live system disk.

    Returns a list of dicts following the shared UI histobar contract::

        {
          "name": str, "model": str, "size": str, "sizeBytes": int,
          "type": "SSD" | "HDD", "removable": bool,
          "partitions": [
            {"name": str, "sizeBytes": int, "fstype": str, "partType": str}
          ]
        }

    On any failure of ``lsblk`` (missing binary, non-zero exit, malformed JSON)
    a mock list following the same contract is returned and a warning logged.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", _LSBLK_COLUMNS],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning("lsblk returned %d, using mock disks", result.returncode)
            return _mock_disks()
        data = json.loads(result.stdout)
    except FileNotFoundError:
        logger.warning("lsblk not found, using mock disks")
        return _mock_disks()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("lsblk output unusable (%s), using mock disks", exc)
        return _mock_disks()

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

    return [_build_disk(device) for device in devices_to_show]

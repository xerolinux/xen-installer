"""
Partition Job for Xen Installer.

CRITICAL SECURITY WARNING:
This job performs DESTRUCTIVE DISK OPERATIONS that can result in IRREVERSIBLE DATA LOSS.
All operations require explicit confirmation and use dry-run mode by default.
"""

from __future__ import annotations

import logging
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.utils import disk_detector, disk_release
from xen.utils.network_helper import NetworkHelper

logger = logging.getLogger(__name__)

# Disk geometry constants shared with the unified disk detector. Re-exported
# here so the operation model / validation stay a single source of truth for
# 1 MiB alignment, GPT tail reservation and sector size.
_ALIGN = disk_detector._ALIGN  # 2048 sectors (1 MiB) first usable / alignment unit
_GPT_TAIL = disk_detector._GPT_TAIL  # 34 sectors reserved for the GPT secondary header
_SECTOR_SIZE = disk_detector._SECTOR_SIZE  # 512 bytes per sector
_MIN_ESP_BYTES = 512 * 1024 * 1024  # minimum ESP size (512 MiB) for a UEFI plan

# Manual-mode Btrfs subvolumes must follow this installer's own '@'-prefixed
# top-level convention (see PartitionJob._BTRFS_SUBVOLUMES) - Snapper/
# grub-btrfs assume it, so a user-defined layout that doesn't match would
# silently break snapshot/rollback tooling instead of failing loudly here.
_BTRFS_SUBVOL_NAME_RE = re.compile(r"^@[A-Za-z0-9_-]*$")

# Scratch mountpoint used only to create manual-mode Btrfs subvolumes (mounts
# the top-level subvolume id=5, never the real install target). Unrelated to
# JobContext.target_root, which manual-mode operation execution never touches.
_BTRFS_SCRATCH_MOUNT = "/run/xen-btrfs-scratch"

# Allowed manual-editor operation types (the strict QML<->Python contract).
_OPERATION_TYPES = frozenset({"create", "delete", "format", "setflag", "resize"})


def part_path(disk: str, n: int) -> str:
    """
    Build a partition device path, handling the NVMe/eMMC ``p`` separator.

    Block devices whose name ends with a digit (NVMe ``nvme0n1``, eMMC
    ``mmcblk0``, loop ``loop0``) require a ``p`` before the partition number,
    e.g. ``/dev/nvme0n1`` -> ``/dev/nvme0n1p1``. SATA/SCSI devices (``sda``) do
    not, e.g. ``/dev/sda`` -> ``/dev/sda1``.
    """
    base = disk.rsplit("/", 1)[-1]
    sep = "p" if base and base[-1].isdigit() else ""
    return f"{disk}{sep}{n}"


def _dev_path(p: str) -> str:
    """Normalise un chemin de partition vers /dev/ (accepte un nom nu comme 'sdb3')."""
    p = p.strip()
    return p if not p or p.startswith("/dev/") else f"/dev/{p}"


def _detect_ram_mb() -> int:
    """
    Detect total RAM in MiB from ``/proc/meminfo``.

    Returns the rounded MiB value, or ``8192`` when detection is unavailable.
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    kib = int(line.split()[1])
                    return kib // 1024
    except (OSError, ValueError, IndexError):
        logger.debug("RAM detection failed, defaulting to 8192 MB")
    return 8192


@dataclass
class PartitionOperation:
    """
    A single manual-editor operation (create/delete/format/setflag/resize).

    This is the serializable QML<->Python contract carried between the UI and
    the engine. ``params`` is kept as a raw dict validated at parse time; each
    operation type reads the subset it needs (see the module docstring / the
    interface contract in the tests).
    """

    type: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PartitionOperation:
        """
        Build a :class:`PartitionOperation` from a contract dict.

        Raises:
            ValueError: if ``type`` is missing/unknown, ``target`` is missing,
                or required params for the given type are absent/malformed.
        """
        if not isinstance(data, dict):
            raise ValueError(f"Operation must be a dict, got {type(data).__name__}")

        op_type = data.get("type")
        if not isinstance(op_type, str) or op_type not in _OPERATION_TYPES:
            raise ValueError(f"Unknown operation type: {op_type!r}")

        target = data.get("target")
        if not isinstance(target, str) or not target:
            raise ValueError(f"Operation {op_type!r} requires a non-empty 'target'")

        params = data.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"Operation {op_type!r} 'params' must be a dict")

        coerced = cls._validate_params(op_type, params)
        return cls(type=op_type, target=target, params=coerced)

    @staticmethod
    def _validate_params(op_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Validate required keys and coerce numeric params to ``int``.

        QML numbers cross the Qt boundary as JS ``Number`` and arrive here as
        Python ``float`` (e.g. ``2048.0``); the sector/number fields must be
        integers, so accept an integral float and coerce it. Returns a copy of
        ``params`` with the numeric fields normalized to ``int``.
        """
        required: dict[str, tuple[str, ...]] = {
            "create": ("start_sector", "size_sectors", "fstype"),
            "delete": ("number",),
            "format": ("path", "fstype"),
            "setflag": ("number", "flag", "state"),
            "resize": ("path", "number", "new_size_sectors", "fstype"),
        }
        for key in required[op_type]:
            if key not in params:
                raise ValueError(f"Operation {op_type!r} missing required param {key!r}")

        # Numeric fields must be whole integers. bool is a subclass of int, so
        # the setflag 'state' stays a bool; only the sector/number fields are
        # coerced here.
        int_fields = {
            "create": ("start_sector", "size_sectors"),
            "delete": ("number",),
            "setflag": ("number",),
            "resize": ("number", "new_size_sectors"),
            "format": (),
        }
        coerced = dict(params)
        for key in int_fields[op_type]:
            value = params[key]
            if isinstance(value, bool):
                raise ValueError(f"Operation {op_type!r} param {key!r} must be an int")
            if isinstance(value, int):
                coerced[key] = value
            elif isinstance(value, float) and value.is_integer():
                coerced[key] = int(value)
            else:
                raise ValueError(f"Operation {op_type!r} param {key!r} must be a whole number")
        return coerced


def _partition_number(name: str) -> int:
    """Extract the GPT partition index from a device name (sda2->2, nvme0n1p3->3)."""
    digits = ""
    for ch in reversed(name):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    return int(digits) if digits else 0


def _new_segment(
    name: str,
    start: int,
    size: int,
    fstype: str,
    part_type: str,
    mountpoint: str,
    kind: str,
    pending_delete: bool = False,
) -> dict[str, Any]:
    """Build a UI-contract segment dict with an explicit, stable shape."""
    return {
        "name": name,
        "startSector": start,
        "sizeSectors": size,
        "sizeBytes": size * _SECTOR_SIZE,
        "fstype": fstype,
        "partType": part_type,
        "mountpoint": mountpoint,
        "kind": kind,
        "pendingDelete": pending_delete,
        # Extra fields consumed by the manual editor UI:
        # - number: GPT index used to target delete/setflag/resize operations.
        # - minSizeSectors: lower bound for the resize slider (real FS minimum is
        #   enforced by e2fsck/resize2fs at apply time; 1 MiB floor for existing).
        # - freeAfterSectors: adjacent trailing free space (grow upper bound);
        #   filled in by _enrich_adjacency() once the ordered list is known.
        "number": _partition_number(name) if kind == "existing" else 0,
        "minSizeSectors": _ALIGN if kind == "existing" else 0,
        "freeAfterSectors": 0,
    }


def _segment_from_existing(seg: dict[str, Any]) -> dict[str, Any]:
    """Normalize a disk_detector segment into an editable in-memory segment."""
    start = int(seg.get("startSector") or 0)
    size = int(seg.get("sizeSectors") or 0)
    kind = "free" if seg.get("kind") == "free" else "existing"
    return _new_segment(
        name=str(seg.get("name", "")),
        start=start,
        size=size,
        fstype=str(seg.get("fstype", "")),
        part_type=str(seg.get("partType", "free" if kind == "free" else "")),
        mountpoint=str(seg.get("mountpoint", "")),
        kind=kind,
    )


def _recompute_free(disk_sectors: int, parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Rebuild the ordered segment list interleaving partitions and free gaps.

    ``parts`` are the surviving (non-deleted, non-free) partition segments.
    Free regions smaller than the 1 MiB alignment unit are omitted (padding).
    Deterministic ordering by ``startSector``.
    """
    ordered = sorted(parts, key=lambda p: int(p["startSector"]))
    usable_end = max(0, disk_sectors - _GPT_TAIL)
    segments: list[dict[str, Any]] = []
    cursor = _ALIGN

    def add_free(start: int, end: int) -> None:
        if end - start + 1 > _ALIGN:
            segments.append(
                _new_segment(
                    name="",
                    start=start,
                    size=end - start + 1,
                    fstype="",
                    part_type="free",
                    mountpoint="",
                    kind="free",
                )
            )

    for part in ordered:
        start = int(part["startSector"])
        if start > cursor:
            add_free(cursor, start - 1)
        segments.append(part)
        cursor = max(cursor, start + int(part["sizeSectors"]))

    if cursor <= usable_end:
        add_free(cursor, usable_end)

    return segments


def simulate_operations(
    segments: list[dict[str, Any]], operations: list[PartitionOperation]
) -> list[dict[str, Any]]:
    """
    Apply create/delete/resize/format IN MEMORY over a disk's geometry.

    Pure function: performs NO execution. Feeds both the UI (simulatedSegments)
    and :func:`validate_operations`. Each output segment carries ``kind``
    (``"existing"|"new"|"free"``) and ``pendingDelete``. Free regions are
    recomputed after the operations; ordering is deterministic by startSector.

    Args:
        segments: disk_detector segment list for the selected disk.
        operations: parsed operations to apply, in insertion order.

    Returns:
        A new ordered segment list (input is not mutated).
    """
    # Total disk sectors: derive from the widest segment end so free-region
    # recomputation covers the whole medium even if the input omits the tail.
    disk_sectors = 0
    working: list[dict[str, Any]] = []
    for seg in segments:
        norm = _segment_from_existing(seg)
        end = int(norm["startSector"]) + int(norm["sizeSectors"])
        disk_sectors = max(disk_sectors, end + _GPT_TAIL)
        working.append(norm)

    def find_by_name(name: str) -> dict[str, Any] | None:
        return next((s for s in working if s["name"] == name and s["kind"] != "free"), None)

    for op in operations:
        params = op.params
        if op.type == "delete":
            target = _target_partition(working, op)
            if target is not None:
                target["pendingDelete"] = True
        elif op.type == "format":
            target = find_by_name(op.target.rsplit("/", 1)[-1]) or _target_partition(working, op)
            if target is not None:
                target["fstype"] = str(params.get("fstype", target["fstype"]))
                target["mountpoint"] = str(params.get("mountpoint", target["mountpoint"]))
        elif op.type == "resize":
            target = _target_partition(working, op)
            if target is not None:
                new_size = int(params["new_size_sectors"])
                target["sizeSectors"] = new_size
                target["sizeBytes"] = new_size * _SECTOR_SIZE
                if params.get("fstype"):
                    target["fstype"] = str(params["fstype"])
        elif op.type == "create":
            start = int(params["start_sector"])
            size = int(params["size_sectors"])
            working.append(
                _new_segment(
                    name=str(params.get("name", "")),
                    start=start,
                    size=size,
                    fstype=str(params.get("fstype", "")),
                    part_type=str(params.get("part_type", "linux")),
                    mountpoint=str(params.get("mountpoint", "")),
                    kind="new",
                )
            )

    survivors = [s for s in working if s["kind"] != "free" and not s["pendingDelete"]]
    # Deleted partitions are still surfaced (pendingDelete=True) so the UI can
    # render them struck-through, but they must NOT reserve space; hence they
    # are excluded from the free-region recomputation below.
    rebuilt = _recompute_free(disk_sectors, survivors)
    deleted = [s for s in working if s["kind"] != "free" and s["pendingDelete"]]
    combined = rebuilt + deleted
    combined.sort(key=lambda s: (int(s["startSector"]), 0 if not s["pendingDelete"] else 1))
    _enrich_adjacency(rebuilt)
    return combined


def _enrich_adjacency(ordered: list[dict[str, Any]]) -> None:
    """Set ``freeAfterSectors`` on each partition = size of the next free region.

    ``ordered`` is the interleaved partition/free list (deleted segments excluded),
    so a partition immediately followed by a ``free`` segment can grow into it.
    """
    for idx, seg in enumerate(ordered):
        if seg["kind"] == "free":
            continue
        nxt = ordered[idx + 1] if idx + 1 < len(ordered) else None
        seg["freeAfterSectors"] = int(nxt["sizeSectors"]) if nxt and nxt["kind"] == "free" else 0


def _target_partition(
    segments: list[dict[str, Any]], op: PartitionOperation
) -> dict[str, Any] | None:
    """Resolve the segment an operation targets by device name (best effort)."""
    tail = op.target.rsplit("/", 1)[-1]
    match = next(
        (s for s in segments if s["name"] == tail and s["kind"] != "free"),
        None,
    )
    if match is not None:
        return match
    # Fall back to the partition number carried in params (path-independent).
    number = op.params.get("number")
    if isinstance(number, int) and not isinstance(number, bool):
        return next(
            (s for s in segments if s["name"].endswith(str(number)) and s["kind"] != "free"),
            None,
        )
    return None


def _is_target_busy(target: str) -> bool:
    """
    Return True if ``target`` is currently mounted or backs the live root.

    Uses ``findmnt`` to detect a mounted partition and the disk detector's
    live-source probe to detect the running system's medium. Mocked in tests.
    """
    tail = target.rsplit("/", 1)[-1]
    live = disk_detector._live_source()
    if live and (live == target or live.rsplit("/", 1)[-1] == tail):
        return True
    try:
        result = subprocess.run(
            ["findmnt", "-no", "TARGET", target],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def validate_operations_applicable(
    disk_geom: dict[str, Any],
    operations: list[PartitionOperation],
) -> tuple[bool, str]:
    """
    Validate that an operation plan can be safely written to the disk.

    This is the GParted-style "can I apply these changes?" check, independent of
    whether the resulting layout is a complete installable system. Checks: no
    operation touches the live/mounted target, 1 MiB alignment of every
    start/size, no overlap between resulting partitions, all boundaries within
    ``[_ALIGN, disk_sectors - _GPT_TAIL]``.

    Args:
        disk_geom: dict carrying at least ``sizeSectors`` and ``segments`` for
            the selected disk (the disk_detector disk contract).
        operations: parsed operations to validate.

    Returns:
        ``(True, "")`` on success or ``(False, "<clear reason>")``.
    """
    disk_sectors = int(disk_geom.get("sizeSectors") or 0)
    usable_end = disk_sectors - _GPT_TAIL

    # Guard-rail: refuse to touch a mounted or live partition.
    mutating = ("delete", "resize", "format", "setflag")
    for op in operations:
        targets_device = op.type in mutating and op.target.startswith("/dev/")
        if targets_device and _is_target_busy(op.target):
            return False, f"Target {op.target} is mounted or backs the live system"

    simulated = simulate_operations(disk_geom.get("segments", []), operations)
    parts = [s for s in simulated if s["kind"] != "free" and not s["pendingDelete"]]

    for seg in parts:
        start = int(seg["startSector"])
        size = int(seg["sizeSectors"])
        if start % _ALIGN != 0:
            return False, f"Partition start {start} is not 1 MiB aligned"
        if size % _ALIGN != 0:
            return False, f"Partition size {size} is not 1 MiB aligned"
        if start < _ALIGN:
            return False, f"Partition starts before the first usable sector ({start} < {_ALIGN})"
        if disk_sectors and start + size > usable_end:
            return False, "Partition extends past the last usable sector (GPT tail)"

    ordered = sorted(parts, key=lambda s: int(s["startSector"]))
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        prev_end = int(prev["startSector"]) + int(prev["sizeSectors"])
        if prev_end > int(cur["startSector"]):
            return False, "Partitions overlap"

    return True, ""


def validate_operations(
    disk_geom: dict[str, Any],
    operations: list[PartitionOperation],
    uefi: bool = True,
) -> tuple[bool, str]:
    """
    Validate a manual operation plan as a complete installable layout.

    Runs :func:`validate_operations_applicable` first (device not busy,
    alignment, no overlap, within bounds), then the install-completeness rules:
    exactly one partition mounted at ``/`` and (when ``uefi``) exactly one ESP
    (vfat + esp flag + >= 512 MiB). Gate installation/navigation on this;
    gate the GParted-style Apply on ``validate_operations_applicable`` only.

    Args:
        disk_geom: dict carrying at least ``sizeSectors`` and ``segments`` for
            the selected disk (the disk_detector disk contract).
        operations: parsed operations to validate.
        uefi: when True, require exactly one conforming ESP.

    Returns:
        ``(True, "")`` on success or ``(False, "<clear reason>")``.
    """
    ok, reason = validate_operations_applicable(disk_geom, operations)
    if not ok:
        return ok, reason

    if _plan_sets_bios_grub(operations):
        return False, (
            "The 'bios_grub' flag is not supported: this installer targets "
            "UEFI with systemd-boot, not GRUB. Remove it to continue."
        )

    encrypted_roots = [
        op
        for op in operations
        if op.type == "format" and op.params.get("encrypt") and op.params.get("mountpoint") == "/"
    ]
    if len(encrypted_roots) > 1:
        return False, "At most one partition may be encrypted and mounted at /"

    for op in operations:
        if op.type != "format" or not op.params.get("encrypt"):
            continue
        mountpoint = op.params.get("mountpoint")
        if mountpoint != "/" and op.params.get("encryption_passphrase"):
            return False, (
                "Only the partition mounted at / takes a passphrase - every "
                "other encrypted partition unlocks automatically"
            )

    simulated = simulate_operations(disk_geom.get("segments", []), operations)
    parts = [s for s in simulated if s["kind"] != "free" and not s["pendingDelete"]]

    # Apply pending setflag operations to a name->flags view for ESP detection.
    flags = _collect_flags(operations)

    # Defense-in-depth: reject encrypting the real ESP (by the same
    # partType/esp-flag detection _validate_esp trusts below), not a
    # mountpoint heuristic that could miss it on a malformed/direct
    # engine.addPartitionOperation() call.
    for op in operations:
        if op.type != "format" or not op.params.get("encrypt"):
            continue
        target = _target_partition(parts, op)
        if target is not None and _is_efi_segment(target, flags):
            return False, "The EFI System Partition cannot be encrypted"

    roots = [s for s in parts if s["mountpoint"] == "/"]
    if len(roots) != 1:
        return False, "Exactly one partition must be mounted at /"

    if uefi:
        ok, reason = _validate_esp(parts, flags)
        if not ok:
            return False, reason

    return True, ""


def _collect_flags(operations: list[PartitionOperation]) -> dict[int, set[str]]:
    """Fold setflag operations into a {partition_number: {active flags}} view."""
    flags: dict[int, set[str]] = {}
    for op in operations:
        if op.type != "setflag":
            continue
        number = op.params.get("number")
        flag = op.params.get("flag")
        state = op.params.get("state")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        bucket = flags.setdefault(number, set())
        if state and isinstance(flag, str):
            bucket.add(flag)
        elif isinstance(flag, str):
            bucket.discard(flag)
    return flags


def _segment_number(seg: dict[str, Any]) -> int | None:
    """Extract the GPT partition index from a segment's device name (sda2->2)."""
    digits = "".join(ch for ch in seg["name"] if ch.isdigit())
    return int(digits) if digits else None


def _is_efi_segment(seg: dict[str, Any], flags: dict[int, set[str]]) -> bool:
    """True if ``seg`` is the EFI System Partition: vfat + (esp flag or on-disk efi partType)."""
    number = _segment_number(seg)
    has_esp_flag = number is not None and "esp" in flags.get(number, set())
    is_efi_type = str(seg["partType"]).lower() == "efi"
    return str(seg["fstype"]).lower() in ("vfat", "fat", "fat32") and (has_esp_flag or is_efi_type)


def _validate_esp(parts: list[dict[str, Any]], flags: dict[int, set[str]]) -> tuple[bool, str]:
    """Require exactly one ESP: vfat + esp flag + >= 512 MiB (UEFI plans)."""

    # Boot-path fstype/flag consistency is checked independently of the ESP
    # count below, so a wrong partition claiming /boot/efi gets its own clear
    # error even when no conforming ESP exists anywhere on the plan.
    ok, reason = _validate_boot_mountpoint_consistency(
        [
            {"fstype": s["fstype"], "is_efi": _is_efi_segment(s, flags), "mountpoint": s["mountpoint"]}
            for s in parts
        ]
    )
    if not ok:
        return ok, reason

    esps = [s for s in parts if _is_efi_segment(s, flags)]
    if len(esps) != 1:
        return False, "A UEFI install requires exactly one ESP (vfat + esp flag)"
    if int(esps[0]["sizeBytes"]) < _MIN_ESP_BYTES:
        return False, "The ESP must be at least 512 MiB"

    return True, ""


def _validate_boot_mountpoint_consistency(parts: list[dict[str, Any]]) -> tuple[bool, str]:
    """
    Cross-check ``/boot/efi`` against the partition that claims it, and
    require the ESP itself (when mounted at all) to land exactly there.

    Convention (ArchWiki, matches this project's own Calamares config -
    ``efiSystemPartition: "/boot/efi"``): the EFI System Partition mounts at
    ``/boot/efi``; a plain ``/boot`` (e.g. a separate unencrypted boot
    partition ahead of a LUKS root) is an ordinary partition with no
    vfat/ESP requirement of its own - conflating the two would wrongly
    reject that legitimate layout.

    Each entry in ``parts`` is ``{"fstype": str, "is_efi": bool, "mountpoint": str}``.
    A non-EFI or non-vfat partition mounted at ``/boot/efi``, or an ESP
    mounted somewhere else, both fail the boot loader install later -
    catching it here turns that into a clear pre-install error.
    """
    for part in parts:
        mount = part.get("mountpoint") or ""
        if mount != "/boot/efi":
            continue
        fs = str(part.get("fstype", "")).lower()
        if fs not in ("vfat", "fat", "fat32") or not part.get("is_efi"):
            return False, (
                f"{mount} must be a FAT32 partition flagged as the EFI System "
                f"Partition, not {fs or 'unformatted'}"
            )

    for part in parts:
        if part.get("is_efi"):
            mount = part.get("mountpoint") or ""
            if mount and mount != "/boot/efi":
                return False, f"The EFI System Partition must be mounted at /boot/efi, not {mount}"

    return True, ""


def validate_btrfs_subvolumes(subvolumes: list[dict[str, Any]]) -> tuple[bool, str]:
    """
    Validate a user-defined Btrfs subvolume layout for manual partitioning.

    Schema (matches this installer's own '@'-prefixed convention, required
    for Snapper/grub-btrfs integration):

    - At least one subvolume.
    - Every ``name`` matches ``@[A-Za-z0-9_-]*`` and is unique.
    - Exactly one subvolume is the root (``mountpoint`` == "" or "/").
    - Every other ``mountpoint`` starts with "/", is not "/", and is unique.

    Args:
        subvolumes: list of ``{"name": str, "mountpoint": str}``.

    Returns:
        ``(True, "")`` on success or ``(False, "<clear reason>")``.
    """
    if not subvolumes:
        return False, "At least one Btrfs subvolume is required"

    names: list[str] = []
    mountpoints: list[str] = []
    root_count = 0
    for sv in subvolumes:
        name = str(sv.get("name", ""))
        mount = str(sv.get("mountpoint", ""))
        if not _BTRFS_SUBVOL_NAME_RE.match(name):
            return False, (
                f"Invalid subvolume name {name!r}: must start with '@' and contain only "
                "letters, digits, '-' or '_'"
            )
        names.append(name)
        if mount in ("", "/"):
            root_count += 1
            mountpoints.append("/")
        else:
            if not mount.startswith("/"):
                return False, f"Subvolume mountpoint {mount!r} must start with '/'"
            mountpoints.append(mount)

    if root_count == 0:
        return False, "One subvolume must be the root (mountpoint '/')"
    if root_count > 1:
        return False, "Only one subvolume may be the root (mountpoint '/')"
    if len(names) != len(set(names)):
        return False, "Duplicate subvolume name"
    if len(mountpoints) != len(set(mountpoints)):
        return False, "Duplicate subvolume mountpoint"

    return True, ""


def validate_manual_assignments(
    disk_geoms: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    uefi: bool = True,
) -> tuple[bool, str]:
    """
    Validate the M1 mount-assignment plan: the path actually exercised once
    the user has committed a physical layout (via M2 Apply, or reused
    pre-existing partitions) and is only mapping mountpoints, with no pending
    M2 operations left in the queue.

    Unlike :func:`validate_operations` (which reads fstype/flags from the
    operations themselves), this cross-references each assignment against the
    real geometry of every disk in the system (mount assignment is global -
    root can live on one disk and /home on another, exactly like Calamares'
    mount module mounts across whichever disks the user assigned), overlaid
    with the assignment's own ``format``/``fstype`` when reformatting.

    Args:
        disk_geoms: disk_detector contract dicts for every disk on the
            system (not just the one currently selected in the editor).
        assignments: list of ``{"name", "mountpoint", "format", "fstype",
            "subvolumes"?}``. ``subvolumes`` (optional) is a Btrfs layout per
            :func:`validate_btrfs_subvolumes`; when present it replaces the
            assignment's own bare ``mountpoint`` for the checks below.
        uefi: when True, require the ESP to exist and be mounted correctly.

    Returns:
        ``(True, "")`` on success or ``(False, "<clear reason>")``.
    """
    by_name = {
        str(seg.get("name")): seg
        for disk_geom in disk_geoms
        for seg in disk_geom.get("segments", [])
        if seg.get("kind") != "free"
    }

    effective: list[dict[str, Any]] = []
    used_mounts: list[str] = []
    for assign in assignments:
        name = str(assign.get("name", ""))
        seg = by_name.get(name, {})
        is_reformat = bool(assign.get("format")) and assign.get("fstype")
        fstype = str(assign["fstype"] if is_reformat else seg.get("fstype", "")).lower()
        # M1 can only mkfs (reformat); it never touches GPT partition type/
        # flags, so whether this partition IS the ESP is always read from the
        # real on-disk partType, independent of any reformat in progress.
        is_efi = str(seg.get("partType", "")).lower() == "efi"

        subvolumes = assign.get("subvolumes")
        if subvolumes:
            if fstype != "btrfs":
                return False, f"{name}: subvolumes require a Btrfs filesystem, not {fstype or 'none'}"
            ok, reason = validate_btrfs_subvolumes(subvolumes)
            if not ok:
                return False, f"{name}: {reason}"
            for sv in subvolumes:
                mount = str(sv.get("mountpoint", "")) or "/"
                effective.append({"fstype": "btrfs", "is_efi": False, "mountpoint": mount})
                used_mounts.append(mount)
            continue

        mount = str(assign.get("mountpoint", ""))
        if mount and mount != "swap" and not mount.startswith("/"):
            return False, f"{name}: mountpoint {mount!r} must start with '/'"
        effective.append({"fstype": fstype, "is_efi": is_efi, "mountpoint": mount})
        if mount:
            used_mounts.append(mount)

    roots = used_mounts.count("/")
    if roots == 0:
        return False, "No partition is mounted at /"
    if roots > 1:
        return False, "More than one partition is mounted at /"
    if len(used_mounts) != len(set(used_mounts)):
        return False, "A mount point is assigned to more than one partition"

    if uefi:
        ok, reason = _validate_boot_mountpoint_consistency(effective)
        if not ok:
            return False, reason
        # Unlike the M2 (pre-Apply) plan, this is the completeness gate for
        # final install - an ESP that exists but was never assigned a mount
        # point would leave the bootloader install with nowhere to write.
        esp_entries = [p for p in effective if p["is_efi"]]
        if not esp_entries:
            return False, "A UEFI install requires the EFI System Partition to be assigned"
        if esp_entries[0]["mountpoint"] != "/boot/efi":
            return False, "The EFI System Partition must be mounted at /boot/efi"

    return True, ""


# parted mkpart n'accepte qu'un vocabulaire fixe pour le fs-type (vfat/swap refuses).
_PARTED_FSTYPE = {
    "vfat": "fat32",
    "fat": "fat32",
    "fat32": "fat32",
    "fat16": "fat16",
    "swap": "linux-swap",
    "linux-swap": "linux-swap",
}


def _parted_fstype(fstype: str) -> str:
    return _PARTED_FSTYPE.get(fstype.lower(), fstype.lower())


_PARTED_FLAGS = frozenset(
    {"esp", "boot", "bios_grub", "swap", "raid", "lvm", "legacy_boot", "hidden", "msftres", "diag"}
)


def _parted_flag(flag: str) -> str | None:
    token = flag.strip().lower()
    return token if token in _PARTED_FLAGS else None


def _luks_kdf_args(bootloader: str, has_separate_boot: bool) -> list[str]:
    """Extra ``cryptsetup luksFormat`` arguments for GRUB/Argon2id compatibility.

    GRUB's ``cryptodisk`` module can only unlock a LUKS2 header using
    PBKDF2 - it has no Argon2id support at all. When GRUB itself must
    decrypt a volume directly (no separate, unencrypted ``/boot``
    partition ahead of it) to read ``/boot/grub`` and the kernel/initramfs,
    formatting with LUKS2's Argon2id default makes the system unable to
    boot - GRUB drops to its rescue shell, unable to read the header.

    Does not apply when GRUB never needs to decrypt anything directly:
    systemd-boot reads the kernel/initramfs from the ESP itself and
    unlocking happens later, in the initramfs, via mkinitcpio's "encrypt"
    hook (plain cryptsetup, no PBKDF2-only limitation) - same as GRUB with
    a separate unencrypted ``/boot`` partition, which reads its config and
    kernel from that plain partition and never touches this LUKS header.

    Args:
        bootloader: resolved bootloader type, e.g. "grub" or "systemd-boot"
            (seeded into selections by ``Engine.run_all``).
        has_separate_boot: True if there's a separate, unencrypted
            partition mounted at /boot ahead of the partition being
            formatted (always False in AUTO mode, which has no such
            partition structurally).

    Returns:
        ``["--pbkdf", "pbkdf2"]`` when GRUB must decrypt this volume
        directly; ``[]`` (cryptsetup's own default, Argon2id) otherwise.
    """
    if bootloader == "grub" and not has_separate_boot:
        return ["--pbkdf", "pbkdf2"]
    return []


def _manual_mapper_name(mountpoint: str) -> str:
    """Deterministic ``/dev/mapper/<name>`` suffix for a manually-encrypted
    partition, derived purely from its resolved mountpoint so the job (at
    Apply time) and bridge.py (at post-Apply reconciliation) always agree
    without needing to pass this name between them.

    "/" -> "cryptroot", "/home" -> "crypthome", "swap" -> "cryptswap",
    anything else -> "cryptdata" plus a sanitized suffix from the path.
    """
    mp = mountpoint.strip().strip("/")
    if mountpoint.strip() == "/":
        return "cryptroot"
    if mountpoint.strip() == "swap":
        return "cryptswap"
    if mp == "home":
        return "crypthome"
    if not mp:
        return "cryptdata"
    safe = "".join(c if c.isalnum() else "-" for c in mp)
    return f"cryptdata-{safe}"


def _plan_sets_bios_grub(operations: list[PartitionOperation]) -> bool:
    for op in operations:
        if (
            op.type == "setflag"
            and str(op.params.get("flag", "")).lower() == "bios_grub"
            and op.params.get("state")
        ):
            return True
        flags = op.params.get("flags") or []
        if any(str(f).lower() == "bios_grub" for f in flags):
            return True
    return False


class PartitionMode(Enum):
    """Partitioning mode selection."""

    AUTO = "auto"  # Automatic partitioning (wipe disk)
    MANUAL = "manual"  # Manual partition selection


class FilesystemType(Enum):
    """Supported filesystem types."""

    EXT4 = "ext4"
    XFS = "xfs"
    BTRFS = "btrfs"


@dataclass
class DiskInfo:
    """Information about a disk device."""

    name: str  # Device name (e.g., "sda")
    path: str  # Device path (e.g., "/dev/sda")
    size: int  # Size in bytes
    size_human: str  # Human-readable size
    model: str = ""
    is_removable: bool = False
    has_partitions: bool = False
    partitions: list[PartitionInfo] = field(default_factory=list)


@dataclass
class PartitionInfo:
    """Information about a partition."""

    name: str  # Partition name (e.g., "sda1")
    path: str  # Partition path (e.g., "/dev/sda1")
    size: int  # Size in bytes
    size_human: str  # Human-readable size
    fstype: str = ""  # Filesystem type
    mountpoint: str = ""
    has_data: bool = False  # True if partition appears to have data


@dataclass
class PartitionLayout:
    """Planned partition layout."""

    efi_partition: str = ""  # Path to EFI partition (e.g., "/dev/sda1")
    root_partition: str = ""  # Path to root partition
    swap_partition: str = ""  # Path to swap partition (if any)
    efi_size_mb: int = 2048
    swap_size_mb: int = 0
    encrypted: bool = False  # True once the root partition is LUKS-wrapped
    luks_mapper_name: str = "cryptroot"  # /dev/mapper name for the LUKS device

    @property
    def root_target(self) -> str:
        """Device to format/mount as root (LUKS mapper if encrypted)."""
        if self.encrypted:
            return f"/dev/mapper/{self.luks_mapper_name}"
        return self.root_partition


class PartitionJob(BaseJob):
    """
    Disk partitioning and formatting job.

    SECURITY MODEL:
    - dry_run=True by default (simulation mode)
    - confirmed=False by default (explicit user confirmation required)
    - Detects existing data and warns user
    - All destructive operations are logged for audit trail
    - No operation is reversible once executed

    Supported modes:
    - AUTO: Wipe disk and create GPT + EFI + Root (+ optional Swap)
    - MANUAL: Use existing partitions (planned for v0.4.0)
    """

    name = "partition"
    description = "Disk partitioning and formatting"

    # Matches CachyOS's real subvolume layout exactly (their Calamares
    # mount.conf btrfsSubvolumes:), plus @swap - Btrfs forbids snapshotting
    # a subvolume with an active swapfile, so it must be its own subvolume
    # regardless of naming convention.
    _BTRFS_SUBVOLUMES = ("@", "@home", "@root", "@srv", "@cache", "@tmp", "@log", "@swap")

    # (subvolume name, path relative to target_root - "" for root itself),
    # in mount order: "@" must mount at target_root before the others can
    # be mounted underneath it.
    _BTRFS_SUBVOLUME_MOUNTS = (
        ("@", ""),
        ("@home", "home"),
        ("@root", "root"),
        ("@srv", "srv"),
        ("@cache", "var/cache"),
        ("@tmp", "var/tmp"),
        ("@log", "var/log"),
        ("@swap", "swap"),
    )

    _BTRFS_MOUNT_OPTIONS = "noatime,compress=zstd,space_cache=v2"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the partition job."""
        super().__init__(config)
        self._layout: PartitionLayout | None = None
        # True once partitioning has successfully mounted the target. The mounts
        # are then a deliverable for the downstream install job, so cleanup() must
        # NOT tear them down (see cleanup() for the full rationale).
        self._succeeded: bool = False
        self._manual_generated_passphrases: dict[str, str] = {}
        self._manual_bootloader: str = "systemd-boot"
        self._manual_has_separate_boot: bool = False

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate partition configuration.

        Checks:
        - Disk exists and is accessible
        - Sufficient space available
        - Mode is valid
        - Detects existing data (WARNING level)

        Args:
            context: Execution context

        Returns:
            JobResult indicating validation status
        """
        context.report_progress(0, "Validating partition configuration...")

        selections = context.selections

        # Check required fields
        disk = selections.get("disk")
        if not disk:
            return JobResult.fail("Disk selection is required", error_code=30)

        # Backward compat: prefer partition_mode, fall back to legacy "mode".
        mode = selections.get("partition_mode", selections.get("mode", "auto"))
        if mode not in [m.value for m in PartitionMode]:
            return JobResult.fail(f"Invalid partition mode: {mode}", error_code=31)

        # Verify disk exists
        disk_path = Path(disk)
        if not disk_path.exists():
            return JobResult.fail(f"Disk not found: {disk}", error_code=32)

        # Get disk information
        try:
            disks = self._list_disks()
            disk_info = next((d for d in disks if d.path == disk), None)
            if not disk_info:
                return JobResult.fail(f"Cannot read disk info: {disk}", error_code=33)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to list disks: {e}")
            return JobResult.fail("Failed to enumerate disks", error_code=34)

        # Check minimum disk size (10 GB)
        min_size_bytes = 10 * 1024 * 1024 * 1024
        if disk_info.size < min_size_bytes:
            return JobResult.fail(
                f"Disk too small: {disk_info.size_human} (minimum 10 GB required)",
                error_code=35,
            )

        # Detect existing data (WARNING)
        if disk_info.has_partitions:
            data_partitions = [p for p in disk_info.partitions if p.has_data]
            if data_partitions:
                partition_list = ", ".join(p.path for p in data_partitions)
                logger.warning(f"⚠️  EXISTING DATA DETECTED on {disk}: {partition_list}")
                logger.warning("⚠️  ALL DATA WILL BE LOST if you proceed!")
                # This is a warning, not a failure - user must explicitly confirm

        # Validate filesystem type
        filesystem = selections.get("filesystem", "xfs")
        if filesystem not in [f.value for f in FilesystemType]:
            return JobResult.fail(f"Invalid filesystem type: {filesystem}", error_code=36)

        # Btrfs is all-or-nothing: its Snapper/grub-btrfs/btrfs-assistant
        # tooling needs packages that don't exist offline (not part of the
        # live squashfs, no pacman sync DB or cache available offline
        # either). Rather than offer a half-configured Btrfs install,
        # reject it outright when offline - the UI hides the option too,
        # this is the defensive backend guard (bridge.py's setFilesystem
        # is an unvalidated passthrough).
        if filesystem == FilesystemType.BTRFS.value:
            has_internet = selections.get("has_internet")
            if has_internet is None:
                has_internet = NetworkHelper.check_internet_connectivity()
            if not has_internet:
                return JobResult.fail(
                    "Btrfs requires an internet connection (Snapper/grub-btrfs/"
                    "btrfs-assistant aren't available offline)",
                    error_code=60,
                )

        # Validate swap size (legacy swap_size partition path)
        swap_size = selections.get("swap_size", 0)
        if not isinstance(swap_size, (int, float)) or swap_size < 0:
            return JobResult.fail("Invalid swap size", error_code=37)

        # Validate swap strategy (current path)
        swap_strategy = selections.get("swap_strategy")
        if swap_strategy is not None and swap_strategy not in ("file", "none", "hibernate"):
            return JobResult.fail(f"Invalid swap strategy: {swap_strategy}", error_code=47)

        # Validate encryption: a passphrase is mandatory when encryption is on.
        # SECURITY: never echo the passphrase in the error message.
        if selections.get("encryption", False):
            passphrase = selections.get("encryption_passphrase", "")
            if not passphrase:
                return JobResult.fail(
                    "Encryption enabled but no passphrase provided",
                    error_code=46,
                )

        context.report_progress(50, "Validation complete")

        return JobResult.ok(
            "Partition configuration valid",
            data={
                "disk": disk,
                "disk_size": disk_info.size_human,
                "has_existing_data": disk_info.has_partitions,
                "warnings": len(data_partitions) if disk_info.has_partitions else 0,
            },
        )

    def run(self, context: JobContext) -> JobResult:
        """
        Execute disk partitioning.

        SECURITY: Checks dry_run and confirmed flags before any destructive operation.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(0, "Starting partitioning job...")
        # Reset the hand-off flag so a retry starts from a clean slate.
        self._succeeded = False

        # Validate first
        validation = self.validate(context)
        if not validation.success:
            return validation

        selections = context.selections
        dry_run = selections.get("dry_run", True)  # DEFAULT TRUE
        confirmed = selections.get("confirmed", False)  # DEFAULT FALSE

        # SECURITY GATE: Require explicit confirmation for real operations
        if not dry_run and not confirmed:
            return JobResult.fail(
                "SECURITY: Cannot execute without explicit confirmation. "
                "Set confirmed=True to proceed with destructive operations.",
                error_code=38,
            )

        disk = selections["disk"]
        # Backward compat: prefer partition_mode, fall back to legacy "mode".
        mode = selections.get("partition_mode", selections.get("mode", "auto"))
        filesystem = selections.get("filesystem", "xfs")

        # SECURITY: read the passphrase locally so we can wipe it in finally.
        passphrase = str(selections.get("encryption_passphrase", ""))
        try:
            if dry_run:
                logger.info("🔒 DRY-RUN MODE: Simulating operations (no actual changes)")
            else:
                logger.warning("⚠️  EXECUTING REAL OPERATIONS - THIS WILL MODIFY THE DISK!")
                logger.warning(f"⚠️  Target disk: {disk}")
                logger.warning("⚠️  All data will be PERMANENTLY LOST!")

            if not dry_run:
                refusal = self._release_target_disk(disk, context.target_root)
                if refusal is not None:
                    return refusal

            context.report_progress(10, "Planning partition layout...")

            # Execute based on mode
            if mode == PartitionMode.AUTO.value:
                result = self._partition_auto(
                    context=context,
                    disk=disk,
                    filesystem=filesystem,
                    selections=selections,
                    passphrase=passphrase,
                    dry_run=dry_run,
                )
            elif mode == PartitionMode.MANUAL.value:
                result = self._partition_manual(
                    context=context,
                    disk=disk,
                    selections=selections,
                    dry_run=dry_run,
                )
            else:
                return JobResult.fail(f"Unknown partition mode: {mode}", error_code=39)

            if not result.success:
                return result

            context.report_progress(100, "Partitioning complete")

            # Hand off the mounted target to the downstream install job: cleanup()
            # must not unmount it now (that job installs the base system against
            # these mounts and performs the final unmount).
            self._succeeded = True

            return JobResult.ok(
                f"Disk {disk} partitioned successfully",
                data={
                    "disk": disk,
                    "mode": mode,
                    "filesystem": filesystem,
                    "dry_run": dry_run,
                    "layout": {
                        "efi": self._layout.efi_partition if self._layout else "",
                        "root": self._layout.root_partition if self._layout else "",
                        "swap": self._layout.swap_partition if self._layout else "",
                    },
                },
            )
        finally:
            # SECURITY: clear the passphrase from memory after use.
            passphrase = ""
            del passphrase

    def _release_target_disk(self, disk: str, target_root: str) -> JobResult | None:
        """
        Free the target disk before any destructive operation.

        External tools (GParted) and the desktop leave partitions auto-mounted, swap
        enabled or mappers open, which makes ``wipefs``/``parted`` fail with EBUSY.
        A residual ``target_root`` mount also makes ``partprobe`` keep stale nodes
        (« superbloc erroné » on the next mount), so it is torn down too.

        Returns a failing JobResult when the disk must not or cannot be touched,
        None when it is free.
        """
        if disk_release.holds_running_system(disk):
            return JobResult.fail(
                f"Refusing to touch {disk}: it backs the system Xen is running from.",
                error_code=56,
            )

        subprocess.run(
            ["umount", "-R", target_root],
            check=False,
            capture_output=True,
        )

        for action in disk_release.release_disk(disk):
            logger.info(f"Released target disk: {action}")

        holders = disk_release.disk_holders(disk)
        if holders:
            return JobResult.fail(
                f"Target disk {disk} is still in use: {'; '.join(holders)}",
                error_code=57,
            )

        return None

    def _list_disks(self) -> list[DiskInfo]:
        """
        List available disks via the unified :mod:`xen.utils.disk_detector`.

        Adapts the detector's UI-contract dicts into the ``DiskInfo`` /
        ``PartitionInfo`` dataclasses used by :meth:`validate`.

        Returns:
            List of DiskInfo objects
        """
        disks: list[DiskInfo] = []

        for entry in disk_detector.list_disks():
            name = entry.get("name", "")
            size = int(entry.get("sizeBytes", 0))

            partitions: list[PartitionInfo] = []
            for part in entry.get("partitions", []):
                part_name = part.get("name", "")
                part_size = int(part.get("sizeBytes", 0))
                fstype = part.get("fstype", "")
                partitions.append(
                    PartitionInfo(
                        name=part_name,
                        path=f"/dev/{part_name}",
                        size=part_size,
                        size_human=self._format_size(part_size),
                        fstype=fstype,
                        mountpoint="",
                        has_data=bool(fstype),
                    )
                )

            disks.append(
                DiskInfo(
                    name=name,
                    path=f"/dev/{name}",
                    size=size,
                    size_human=str(entry.get("size", self._format_size(size))),
                    model=str(entry.get("model", "")),
                    is_removable=bool(entry.get("removable", False)),
                    has_partitions=bool(partitions),
                    partitions=partitions,
                )
            )

        return disks

    def _partition_auto(
        self,
        context: JobContext,
        disk: str,
        filesystem: str,
        selections: dict[str, Any],
        passphrase: str,
        dry_run: bool,
    ) -> JobResult:
        """
        Automatic partitioning mode: wipe disk and create a GPT layout.

        Layout:
        - Partition 1: EFI System Partition (``efi_size_mb`` MiB, FAT32)
        - Partition 2: Root (remaining space, ext4/btrfs, optionally LUKS)

        Swap is handled by ``swap_strategy`` (swapfile under ``target_root``).
        The legacy ``swap_size`` (GB) still creates a swap PARTITION for
        backward compatibility when ``swap_strategy`` is absent.

        SECURITY: when ``encryption`` is set, the root partition is wrapped in
        LUKS2 before formatting. The passphrase is fed via stdin and never
        logged.

        Args:
            context: Execution context
            disk: Disk path (e.g., "/dev/sda")
            filesystem: Root filesystem type
            selections: Raw user selections (snake_case keys)
            passphrase: LUKS passphrase (empty if encryption disabled)
            dry_run: If True, simulate only

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(20, "Creating partition table...")

        efi_size_mb = int(selections.get("efi_size_mb", 2048))
        encryption = bool(selections.get("encryption", False))

        # Determine swap handling. swap_strategy takes precedence over the
        # legacy swap_size partition path.
        swap_strategy = selections.get("swap_strategy")
        legacy_swap_gb = int(selections.get("swap_size", 0) or 0)
        use_swap_partition = swap_strategy is None and legacy_swap_gb > 0
        swap_size_mb = legacy_swap_gb * 1024 if use_swap_partition else 0

        self._layout = PartitionLayout(
            efi_partition=part_path(disk, 1),
            root_partition=part_path(disk, 2),
            swap_partition=part_path(disk, 3) if use_swap_partition else "",
            efi_size_mb=efi_size_mb,
            swap_size_mb=swap_size_mb,
        )

        logger.info(f"Partition layout: EFI={efi_size_mb}MB, swap_partition={swap_size_mb}MB")

        # Step 0: Wipe any existing signatures/partition tables (idempotent,
        # avoids parted refusing to relabel a disk with stale metadata).
        result = self._run_partitioning_command(
            ["wipefs", "-a", disk],
            description="Wiping filesystem signatures",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        result = self._run_partitioning_command(
            ["sgdisk", "--zap-all", disk],
            description="Zapping GPT/MBR structures",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        # Step 1: Create GPT partition table
        result = self._run_partitioning_command(
            ["parted", "-s", disk, "mklabel", "gpt"],
            description="Creating GPT partition table",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        context.report_progress(30, "Creating EFI partition...")

        # Step 2: Create EFI partition (1MiB alignment via 1MiB start)
        result = self._run_partitioning_command(
            [
                "parted",
                "-s",
                disk,
                "mkpart",
                "ESP",
                "fat32",
                "1MiB",
                f"{efi_size_mb + 1}MiB",
            ],
            description="Creating EFI partition",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        # Mark EFI partition
        result = self._run_partitioning_command(
            ["parted", "-s", disk, "set", "1", "esp", "on"],
            description="Setting ESP flag",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        context.report_progress(40, "Creating root partition...")

        # Step 3: Create root partition
        root_end = "100%" if swap_size_mb == 0 else f"-{swap_size_mb}MiB"
        result = self._run_partitioning_command(
            [
                "parted",
                "-s",
                disk,
                "mkpart",
                "root",
                filesystem,
                f"{efi_size_mb + 1}MiB",
                root_end,
            ],
            description="Creating root partition",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        # Step 4: Create swap PARTITION (legacy path only)
        if use_swap_partition:
            context.report_progress(50, "Creating swap partition...")
            result = self._run_partitioning_command(
                [
                    "parted",
                    "-s",
                    disk,
                    "mkpart",
                    "swap",
                    "linux-swap",
                    f"-{swap_size_mb}MiB",
                    "100%",
                ],
                description="Creating swap partition",
                dry_run=dry_run,
            )
            if not result.success:
                return result

        # Step 5: Settle udev BEFORE any mkfs to avoid racing on missing nodes.
        result = self._run_partitioning_command(
            ["partprobe", disk],
            description="Re-reading partition table",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        result = self._run_partitioning_command(
            ["udevadm", "settle"],
            description="Waiting for udev to settle",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        # Step 6: Optionally wrap root in LUKS2 before formatting.
        if encryption:
            context.report_progress(55, "Encrypting root partition...")
            bootloader = str(selections.get("bootloader", "systemd-boot"))
            result = self._setup_luks(
                passphrase, dry_run, bootloader=bootloader, has_separate_boot=False
            )
            if not result.success:
                return result

        context.report_progress(60, "Formatting partitions...")

        # Step 7: Format partitions
        result = self._format_partitions(context, filesystem, dry_run)
        if not result.success:
            return result

        context.report_progress(80, "Mounting filesystems...")

        # Step 8: Mount partitions
        result = self._mount_partitions(context, filesystem, dry_run)
        if not result.success:
            return result

        # Step 9: Configure swap via swapfile under target_root.
        if swap_strategy in ("file", "hibernate"):
            context.report_progress(90, "Configuring swapfile...")
            result = self._setup_swapfile(context, swap_strategy, filesystem, dry_run)
            if not result.success:
                return result

        return JobResult.ok("Automatic partitioning completed")

    def _partition_manual(
        self,
        context: JobContext,
        disk: str,
        selections: dict[str, Any],
        dry_run: bool,
    ) -> JobResult:
        """
        Manual partitioning: assign existing partitions (mount point + format).

        Does NOT create, delete or resize partitions; it optionally reformats
        the partitions the user flagged and mounts them under ``target_root``.
        Exactly one partition must be mounted at ``/``.

        Args:
            context: Execution context
            disk: Target disk path (informational; not wiped in this mode)
            selections: Raw user selections; reads ``partition_assignments``
            dry_run: If True, simulate only

        Returns:
            JobResult indicating success or failure
        """
        logger.info(f"Manual partitioning on {disk} (existing partitions preserved)")

        # M2 operation path takes precedence: when the manual editor produced a
        # list of operations, drive create/delete/format/setflag/resize. The
        # legacy M1 assignment path (below) stays intact when absent.
        if selections.get("partition_operations"):
            return self._apply_operations(context, disk, selections, dry_run)

        assignments = selections.get("partition_assignments", [])
        used = [
            a
            for a in assignments
            if a.get("mountpoint") or a.get("format") or a.get("subvolumes")
        ]
        if not used:
            return JobResult.fail("No partition assignment provided", error_code=48)

        # Mount assignment is global, not scoped to the one disk this run was
        # invoked with - root and /home can each sit on a different physical
        # disk, so every disk's geometry is available to validate against.
        disk_geoms = disk_detector.list_disks()
        # "uefi" may be forced explicitly (tests, or a future BIOS/legacy
        # mode); otherwise fall back to the live environment's own boot mode,
        # same signal bridge.py's _revalidate_operations already uses.
        uefi = bool(selections.get("uefi", Path("/sys/firmware/efi").exists()))
        ok, reason = validate_manual_assignments(disk_geoms, used, uefi=uefi)
        if not ok:
            return JobResult.fail(reason, error_code=49)

        # Record layout for the summary; the EFI slot is whatever lands on
        # /boot*, and the root may live inside a Btrfs subvolume rather than
        # the assignment's own bare mountpoint.
        roots = [a for a in used if a.get("mountpoint") == "/"]
        root_path = str(roots[0]["path"]) if roots else ""
        if not root_path:
            root_path = next(
                (
                    str(a["path"])
                    for a in used
                    for sv in (a.get("subvolumes") or [])
                    if str(sv.get("mountpoint", "")) in ("", "/")
                ),
                "",
            )
        efi = next((a for a in used if a.get("mountpoint") in ("/boot", "/boot/efi")), None)
        self._layout = PartitionLayout(
            efi_partition=str(efi["path"]) if efi else "",
            root_partition=root_path,
        )

        context.report_progress(30, "Formatting assigned partitions...")

        for assignment in used:
            if not assignment.get("format"):
                continue
            path = str(assignment["path"])
            fstype = str(assignment.get("fstype") or "ext4")
            result = self._format_with_optional_subvolumes(
                path,
                fstype,
                assignment.get("subvolumes"),
                dry_run,
                label=str(assignment.get("label", "")),
            )
            if not result.success:
                return result

        context.report_progress(70, "Mounting assigned filesystems...")
        return self._mount_manual(context, used, dry_run)

    def _apply_operations(
        self,
        context: JobContext,
        disk: str,
        selections: dict[str, Any],
        dry_run: bool,
    ) -> JobResult:
        """
        Execute a manual operation plan (GParted-like editor) on ``disk``.

        Operations are parsed from ``selections["partition_operations"]`` and
        run in a strict global order that respects the partition table's
        physical constraints::

            delete -> resize-shrink -> resize-grow -> create -> format -> setflag

        ``partprobe`` + ``udevadm settle`` are issued between every group that
        mutates the table. Shrink operations resize the FILESYSTEM before the
        partition (else data loss); grow operations resize the partition first.

        Args:
            context: Execution context (unused mounts happen in the M1 path).
            disk: Target disk path (e.g. "/dev/sda").
            selections: Raw user selections; reads ``partition_operations``.
            dry_run: If True, simulate only (no subprocess is executed).

        Returns:
            JobResult indicating success or failure.
        """
        try:
            operations = [
                PartitionOperation.from_dict(op) for op in selections["partition_operations"]
            ]
        except ValueError as exc:
            return JobResult.fail(f"Invalid partition operation: {exc}", error_code=53)

        # Record a coarse layout for the summary (root/ESP if discernible).
        self._record_operations_layout(operations)

        # Consumed by _op_create/_op_format via _run_operation - set once
        # per batch so neither has to carry these through its own signature.
        self._manual_bootloader = str(selections.get("bootloader", "systemd-boot"))
        self._manual_has_separate_boot = bool(selections.get("has_separate_boot", False))

        deletes = [op for op in operations if op.type == "delete"]
        shrinks, grows = self._split_resizes(operations)
        creates = [op for op in operations if op.type == "create"]
        formats = [op for op in operations if op.type == "format"]
        setflags = [op for op in operations if op.type == "setflag"]

        if creates:
            result = self._ensure_partition_table(disk, dry_run)
            if not result.success:
                return result

        groups: list[tuple[list[PartitionOperation], bool]] = [
            (deletes, True),
            (shrinks, True),
            (grows, True),
            (creates, True),
            (formats, False),
            (setflags, True),
        ]

        step = 20
        for ops, mutates_table in groups:
            if not ops:
                continue
            for op in ops:
                context.report_progress(step, f"Applying {op.type} on {op.target}")
                result = self._run_operation(disk, op, dry_run)
                if not result.success:
                    return result
            if mutates_table:
                result = self._settle_table(disk, dry_run)
                if not result.success:
                    return result
            step = min(90, step + 12)

        return JobResult.ok("Manual operations applied")

    def _record_operations_layout(self, operations: list[PartitionOperation]) -> None:
        """Populate ``self._layout`` for the run() summary from the operations."""
        root = ""
        efi = ""
        for op in operations:
            mount = op.params.get("mountpoint", "")
            if op.type in ("create", "format"):
                if mount == "/":
                    root = str(op.params.get("path", op.target))
                elif mount in ("/boot", "/boot/efi"):
                    efi = str(op.params.get("path", op.target))
        self._layout = PartitionLayout(efi_partition=efi, root_partition=root)

    @staticmethod
    def _split_resizes(
        operations: list[PartitionOperation],
    ) -> tuple[list[PartitionOperation], list[PartitionOperation]]:
        """
        Partition resize operations into (shrinks, grows).

        A resize is a shrink when the requested new size is strictly smaller
        than the current partition size; anything else is treated as a grow.
        The current size is inferred from the difference we cannot know here,
        so we rely on an explicit ``old_size_sectors`` param when provided and
        otherwise classify by the presence of a ``shrink`` hint.
        """
        shrinks: list[PartitionOperation] = []
        grows: list[PartitionOperation] = []
        for op in operations:
            if op.type != "resize":
                continue
            old = op.params.get("old_size_sectors")
            new = int(op.params["new_size_sectors"])
            if isinstance(old, int) and not isinstance(old, bool):
                (shrinks if new < old else grows).append(op)
            elif op.params.get("shrink"):
                shrinks.append(op)
            else:
                grows.append(op)
        return shrinks, grows

    def _run_operation(self, disk: str, op: PartitionOperation, dry_run: bool) -> JobResult:
        """Dispatch a single operation to its command sequence."""
        if op.type == "delete":
            return self._op_delete(disk, op, dry_run)
        if op.type == "create":
            return self._op_create(disk, op, dry_run)
        if op.type == "format":
            return self._op_format(
                op, dry_run, bootloader=self._manual_bootloader,
                has_separate_boot=self._manual_has_separate_boot,
            )
        if op.type == "setflag":
            return self._op_setflag(disk, op, dry_run)
        if op.type == "resize":
            return self._op_resize(disk, op, dry_run)
        return JobResult.fail(f"Unsupported operation: {op.type}", error_code=53)

    def _settle_table(self, disk: str, dry_run: bool) -> JobResult:
        """Re-read the partition table (partprobe) and wait for udev to settle."""
        result = self._run_partitioning_command(
            ["partprobe", disk], description="Re-reading partition table", dry_run=dry_run
        )
        if not result.success:
            return result
        return self._run_partitioning_command(
            ["udevadm", "settle"], description="Waiting for udev to settle", dry_run=dry_run
        )

    def _op_delete(self, disk: str, op: PartitionOperation, dry_run: bool) -> JobResult:
        """Delete a partition by number via sgdisk."""
        number = int(op.params["number"])
        return self._run_partitioning_command(
            ["sgdisk", f"--delete={number}", disk],
            description=f"Deleting partition {number} on {disk}",
            dry_run=dry_run,
        )

    def _op_create(self, disk: str, op: PartitionOperation, dry_run: bool) -> JobResult:
        """Create a partition (parted mkpart), set flags, then mkfs it."""
        params = op.params
        start = int(params["start_sector"])
        size = int(params["size_sectors"])
        end = start + size - 1
        fstype = str(params["fstype"])
        name = str(params.get("name") or "primary")

        result = self._run_partitioning_command(
            ["parted", "-s", disk, "mkpart", name, _parted_fstype(fstype), f"{start}s", f"{end}s"],
            description=f"Creating partition {name} ({start}s-{end}s) on {disk}",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        # Flags requested at creation time, using parted's generic names.
        flags = params.get("flags", [])
        number = self._resolve_partition_number(disk, start, op, dry_run)
        for flag in flags:
            parted_flag = _parted_flag(str(flag))
            if parted_flag is None:
                continue
            result = self._run_partitioning_command(
                ["parted", "-s", disk, "set", str(number), parted_flag, "on"],
                description=f"Setting {parted_flag} flag on partition {number}",
                dry_run=dry_run,
            )
            if not result.success:
                return result

        settle = self._settle_table(disk, dry_run)
        if not settle.success:
            return settle

        path = _dev_path(str(params.get("path") or part_path(disk, number)))
        return self._format_with_optional_subvolumes(
            path,
            fstype,
            params.get("subvolumes"),
            dry_run,
            label=str(params.get("label", "")),
            encrypt=bool(params.get("encrypt", False)),
            passphrase=str(params.get("encryption_passphrase", "")),
            mountpoint=str(params.get("mountpoint", "")),
            bootloader=self._manual_bootloader,
            has_separate_boot=self._manual_has_separate_boot,
        )

    def _disk_has_label(self, disk: str) -> bool:
        """True si le disque a une table de partitions (sinon mkpart echoue)."""
        if self._disk_partition_starts(disk):
            return True
        try:
            rc = subprocess.run(
                ["parted", "-s", disk, "print"], capture_output=True, text=True, check=False
            ).returncode
        except OSError:
            return True
        return rc == 0

    def _ensure_partition_table(self, disk: str, dry_run: bool) -> JobResult:
        """Cree une etiquette GPT si le disque n'en a aucune."""
        if dry_run or self._disk_has_label(disk):
            return JobResult.ok("Partition table present")
        return self._run_partitioning_command(
            ["parted", "-s", disk, "mklabel", "gpt"],
            description=f"Creating GPT label on {disk}",
            dry_run=dry_run,
        )

    def _disk_partition_starts(self, disk: str) -> dict[int, int]:
        """Map secteur_de_depart -> numero, lu depuis `parted print` (vide si erreur)."""
        try:
            out = (
                subprocess.run(
                    ["parted", "-s", disk, "unit", "s", "print"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout
                or ""
            )
        except OSError:
            return {}
        mapping: dict[int, int] = {}
        for line in out.splitlines():
            toks = line.split()
            if (
                len(toks) >= 2
                and toks[0].isdigit()
                and toks[1].endswith("s")
                and toks[1][:-1].isdigit()
            ):
                mapping[int(toks[1][:-1])] = int(toks[0])
        return mapping

    def _resolve_partition_number(
        self, disk: str, start: int, op: PartitionOperation, dry_run: bool
    ) -> int:
        explicit = op.params.get("number")
        if isinstance(explicit, int) and not isinstance(explicit, bool):
            return explicit
        if not dry_run:
            starts = self._disk_partition_starts(disk)
            if start in starts:
                return starts[start]
            if starts:
                nearest = min(starts, key=lambda s: abs(s - start))
                if abs(nearest - start) <= _ALIGN:
                    return starts[nearest]
        return self._next_partition_number(disk, op)

    @staticmethod
    def _next_partition_number(disk: str, op: PartitionOperation) -> int:
        """Resolve the partition number for a freshly created partition."""
        number = op.params.get("number")
        if isinstance(number, int) and not isinstance(number, bool):
            return number
        path = str(op.params.get("path") or "")
        digits = "".join(ch for ch in path.rsplit("/", 1)[-1] if ch.isdigit())
        base_digits = "".join(ch for ch in disk.rsplit("/", 1)[-1] if ch.isdigit())
        if digits and base_digits and digits.startswith(base_digits):
            digits = digits[len(base_digits) :]
        return int(digits) if digits else 1

    def _op_format(
        self,
        op: PartitionOperation,
        dry_run: bool,
        bootloader: str = "systemd-boot",
        has_separate_boot: bool = False,
    ) -> JobResult:
        """Format an existing partition (mkfs) without touching the table."""
        path = _dev_path(str(op.params["path"]))
        fstype = str(op.params["fstype"])
        return self._format_with_optional_subvolumes(
            path,
            fstype,
            op.params.get("subvolumes"),
            dry_run,
            label=str(op.params.get("label", "")),
            encrypt=bool(op.params.get("encrypt", False)),
            passphrase=str(op.params.get("encryption_passphrase", "")),
            mountpoint=str(op.params.get("mountpoint", "")),
            bootloader=bootloader,
            has_separate_boot=has_separate_boot,
        )

    def _wipe_signatures(self, path: str, dry_run: bool) -> JobResult:
        """Strip stale filesystem/LUKS signatures from ``path`` before mkfs.

        A partition reused across installs (manual mode included) can carry a
        leftover signature - crypto_LUKS is the sharpest case: mkfs never
        clears it, so a prior encrypted install can leave the new filesystem
        misdetected at boot. Same fix already applied to the auto-mode root/EFI
        format path in :meth:`_format_partitions`.
        """
        return self._run_partitioning_command(
            ["wipefs", "-af", path],
            description=f"Wiping old signatures on {path}",
            dry_run=dry_run,
        )

    def _format_with_optional_subvolumes(
        self,
        path: str,
        fstype: str,
        subvolumes: list[dict[str, Any]] | None,
        dry_run: bool,
        label: str = "",
        encrypt: bool = False,
        passphrase: str = "",
        mountpoint: str = "",
        bootloader: str = "systemd-boot",
        has_separate_boot: bool = False,
    ) -> JobResult:
        """
        Wipe stale signatures, optionally LUKS-wrap, mkfs ``path`` (or its
        LUKS mapper), then create Btrfs subvolumes if ``subvolumes`` is
        given. Shared by the M2 create/format operations and the M1
        per-partition reformat path, so a user-defined subvolume layout
        works identically regardless of which path created the filesystem.

        ``encrypt`` on a swap partition is handled entirely differently:
        swap encryption is plain dm-crypt with a random key regenerated
        every boot (crypttab's "swap" option, wired up at install time in
        systemconfig.py) - there's no persistent LUKS container and no
        mkswap to run here at all.
        """
        if encrypt and fstype.lower() == "swap":
            return JobResult.ok(f"Encrypted swap {path}: no persistent format needed")

        target = path
        if encrypt:
            mapper_name = _manual_mapper_name(mountpoint)
            luks_result = self._setup_manual_luks(
                path, mapper_name, mountpoint, passphrase, bootloader, has_separate_boot, dry_run
            )
            if not luks_result.success:
                return luks_result
            target = f"/dev/mapper/{mapper_name}"

        mkfs = self._mkfs_command(fstype, target, label=label)
        if mkfs is None:
            return JobResult.fail(f"Unsupported filesystem for {target}: {fstype}", error_code=51)
        wipe = self._wipe_signatures(path, dry_run)
        if not wipe.success:
            return wipe
        result = self._run_partitioning_command(
            mkfs, description=f"Formatting {target} ({fstype})", dry_run=dry_run
        )
        if not result.success:
            return result

        if fstype.lower() == "btrfs" and subvolumes:
            ok, reason = validate_btrfs_subvolumes(subvolumes)
            if not ok:
                return JobResult.fail(f"Invalid Btrfs subvolume layout: {reason}", error_code=61)
            return self._create_manual_btrfs_subvolumes(target, subvolumes, dry_run)

        return result

    def _setup_manual_luks(
        self,
        raw_path: str,
        mapper_name: str,
        mountpoint: str,
        passphrase: str,
        bootloader: str,
        has_separate_boot: bool,
        dry_run: bool,
    ) -> JobResult:
        """LUKS-format + open one manually-encrypted partition.

        Root's passphrase is the user's own; every other partition gets an
        internally-generated one (never shown in the UI) recorded in
        ``self._manual_generated_passphrases`` keyed by ``raw_path`` for the
        later per-partition keyfile step (real install time - see
        systemconfig.py).
        """
        is_root = mountpoint == "/"
        if not is_root:
            passphrase = secrets.token_urlsafe(32)
            self._manual_generated_passphrases[raw_path] = passphrase

        if dry_run:
            logger.info(f"[DRY-RUN] Would LUKS-format and open {raw_path} as {mapper_name}")
            return JobResult.ok("[DRY-RUN] Manual LUKS setup")

        if not passphrase:
            return JobResult.fail("LUKS passphrase missing", error_code=46)

        kdf_args = _luks_kdf_args(bootloader, has_separate_boot) if is_root else []
        fmt = self._run_secret_command(
            ["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode", *kdf_args, raw_path],
            passphrase=passphrase,
            description=f"LUKS formatting {raw_path}",
        )
        if not fmt.success:
            return fmt

        opened = self._run_secret_command(
            ["cryptsetup", "luksOpen", raw_path, mapper_name],
            passphrase=passphrase,
            description=f"Opening LUKS container {mapper_name}",
        )
        if not opened.success:
            return opened

        return JobResult.ok(f"LUKS container {mapper_name} ready")

    def _create_manual_btrfs_subvolumes(
        self, path: str, subvolumes: list[dict[str, Any]], dry_run: bool
    ) -> JobResult:
        """
        Create each user-defined subvolume under the top-level subvolume
        (id=5), matching :meth:`_create_btrfs_subvolumes`'s auto-mode pattern:
        temporarily mount subvolid=5 at a scratch path, create every
        subvolume, unmount. The real per-mountpoint mounts happen later, at
        install time, in :meth:`_mount_manual`.
        """
        scratch = _BTRFS_SCRATCH_MOUNT
        if not dry_run:
            try:
                Path(scratch).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return JobResult.fail(f"Failed to create {scratch}: {e}", error_code=62)

        result = self._run_partitioning_command(
            ["mount", "-t", "btrfs", "-o", "subvolid=5", path, scratch],
            description=f"Mounting Btrfs top-level subvolume on {path}",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        for subvol in subvolumes:
            name = str(subvol["name"])
            result = self._run_partitioning_command(
                ["btrfs", "subvolume", "create", f"{scratch}/{name}"],
                description=f"Creating Btrfs subvolume {name}",
                dry_run=dry_run,
            )
            if not result.success:
                return result

        return self._run_partitioning_command(
            ["umount", scratch],
            description="Unmounting Btrfs top-level subvolume",
            dry_run=dry_run,
        )

    def _op_setflag(self, disk: str, op: PartitionOperation, dry_run: bool) -> JobResult:
        """Toggle a partition flag (esp/boot/bios_grub/swap/raid/lvm) via parted."""
        number = int(op.params["number"])
        parted_flag = _parted_flag(str(op.params["flag"]))
        if parted_flag is None:
            return JobResult.ok(f"Ignored unsupported flag: {op.params['flag']}")
        state = "on" if op.params.get("state") else "off"
        return self._run_partitioning_command(
            ["parted", "-s", disk, "set", str(number), parted_flag, state],
            description=f"Setting {parted_flag} {state} on partition {number}",
            dry_run=dry_run,
        )

    def _op_resize(self, disk: str, op: PartitionOperation, dry_run: bool) -> JobResult:
        """
        Resize a partition, ordering filesystem vs table ops by direction.

        GROW (table then fs): ``parted resizepart`` -> settle -> grow fs.
        SHRINK (fs then table): shrink fs (fsck + resize2fs / btrfs) -> sync ->
        ``parted resizepart``. NTFS is out of scope for M2c and refused cleanly.
        """
        params = op.params
        path = _dev_path(str(params["path"]))
        number = int(params["number"])
        fstype = str(params["fstype"]).lower()
        new_size = int(params["new_size_sectors"])

        if fstype == "ntfs":
            return JobResult.fail("NTFS resize is not supported (out of scope)", error_code=54)

        old = params.get("old_size_sectors")
        is_shrink = bool(params.get("shrink"))
        if isinstance(old, int) and not isinstance(old, bool):
            is_shrink = new_size < old

        start = int(params.get("start_sector", _ALIGN))
        new_end = start + new_size - 1
        mount = str(params.get("mountpoint") or "")

        if is_shrink:
            return self._resize_shrink(disk, path, number, fstype, new_size, new_end, dry_run)
        return self._resize_grow(disk, path, number, fstype, new_end, mount, dry_run)

    def _resize_grow(
        self,
        disk: str,
        path: str,
        number: int,
        fstype: str,
        new_end: int,
        mount: str,
        dry_run: bool,
    ) -> JobResult:
        """Grow: extend the partition table first, then the filesystem."""
        result = self._run_partitioning_command(
            ["parted", "-s", disk, "resizepart", str(number), f"{new_end}s"],
            description=f"Growing partition {number} to {new_end}s",
            dry_run=dry_run,
        )
        if not result.success:
            return result
        settle = self._settle_table(disk, dry_run)
        if not settle.success:
            return settle

        if fstype == "ext4":
            return self._run_partitioning_command(
                ["resize2fs", path],
                description=f"Growing ext4 filesystem on {path}",
                dry_run=dry_run,
            )
        if fstype == "xfs":
            # xfs_growfs operates on the mountpoint, not the block device -
            # XFS has no offline resize tool.
            if not mount:
                return JobResult.fail(
                    "Growing an XFS filesystem requires it to be mounted", error_code=55
                )
            return self._run_partitioning_command(
                ["xfs_growfs", mount],
                description=f"Growing xfs filesystem on {mount}",
                dry_run=dry_run,
            )
        return JobResult.fail(f"Unsupported filesystem for resize: {fstype}", error_code=55)

    def _resize_shrink(
        self,
        disk: str,
        path: str,
        number: int,
        fstype: str,
        new_size: int,
        new_end: int,
        dry_run: bool,
    ) -> JobResult:
        """Shrink: shrink the filesystem FIRST, then the partition table."""
        if fstype == "ext4":
            check = self._run_partitioning_command(
                ["e2fsck", "-f", "-y", path],
                description=f"Checking ext4 filesystem on {path}",
                dry_run=dry_run,
            )
            if not check.success:
                return check
            fs_size = f"{new_size * _SECTOR_SIZE // 1024}K"
            result = self._run_partitioning_command(
                ["resize2fs", path, fs_size],
                description=f"Shrinking ext4 filesystem on {path} to {fs_size}",
                dry_run=dry_run,
            )
            if not result.success:
                return result
        elif fstype == "xfs":
            # XFS has no shrink support in any stock tool (xfs_growfs only
            # grows) - this is a real filesystem limitation, not a gap here.
            return JobResult.fail(
                "XFS filesystems cannot be shrunk - back up and recreate instead",
                error_code=55,
            )
        else:
            return JobResult.fail(f"Unsupported filesystem for resize: {fstype}", error_code=55)

        sync = self._run_partitioning_command(
            ["sync"], description="Flushing filesystem buffers", dry_run=dry_run
        )
        if not sync.success:
            return sync

        result = self._run_partitioning_command(
            ["parted", "-s", disk, "resizepart", str(number), f"{new_end}s"],
            description=f"Shrinking partition {number} to {new_end}s",
            dry_run=dry_run,
        )
        if not result.success:
            return result
        return self._settle_table(disk, dry_run)

    @staticmethod
    def _mkfs_command(fstype: str, path: str, label: str = "") -> list[str] | None:
        """Return the mkfs command for a filesystem, or None if unsupported.

        ``label`` is optional (matches Calamares' "FS Label" field); blank
        or whitespace-only omits the label flag entirely. FAT labels are
        truncated to dosfstools' 11-character limit - other filesystems are
        left as the caller passed them.
        """
        fs = fstype.lower()
        label = label.strip()
        if fs == "ext4":
            cmd = ["mkfs.ext4", "-F"]
            if label:
                cmd += ["-L", label]
            cmd.append(path)
            return cmd
        if fs == "xfs":
            cmd = ["mkfs.xfs", "-f"]
            if label:
                cmd += ["-L", label]
            cmd.append(path)
            return cmd
        if fs == "btrfs":
            cmd = ["mkfs.btrfs", "-f"]
            if label:
                cmd += ["-L", label]
            cmd.append(path)
            return cmd
        if fs in ("vfat", "fat", "fat32"):
            cmd = ["mkfs.fat", "-F32"]
            if label:
                cmd += ["-n", label[:11]]
            cmd.append(path)
            return cmd
        if fs == "swap":
            cmd = ["mkswap"]
            if label:
                cmd += ["-L", label]
            cmd.append(path)
            return cmd
        return None

    def _mount_manual(
        self,
        context: JobContext,
        assignments: list[dict[str, Any]],
        dry_run: bool,
    ) -> JobResult:
        """Mount manually-assigned filesystems under target_root (parents first).

        A Btrfs assignment carrying ``subvolumes`` expands into one mount task
        per subvolume (each with its own ``subvol=<name>`` mount option)
        instead of the assignment's own bare ``mountpoint``.
        """
        target_root = context.target_root
        opts = self._BTRFS_MOUNT_OPTIONS

        tasks: list[dict[str, Any]] = []
        for assignment in assignments:
            subvolumes = assignment.get("subvolumes")
            if subvolumes:
                for subvol in subvolumes:
                    mount = str(subvol.get("mountpoint", "")) or "/"
                    tasks.append(
                        {"path": assignment["path"], "mountpoint": mount, "subvol": str(subvol["name"])}
                    )
            elif assignment.get("mountpoint") and assignment["mountpoint"] != "swap":
                tasks.append(
                    {"path": assignment["path"], "mountpoint": assignment["mountpoint"], "subvol": None}
                )

        # Shallowest path first so a parent mount exists before its children.
        tasks.sort(key=lambda t: 0 if t["mountpoint"] == "/" else t["mountpoint"].count("/"))

        for task in tasks:
            mountpoint = task["mountpoint"]
            dest = Path(target_root)
            if mountpoint != "/":
                dest = dest / mountpoint.lstrip("/")
            if not dry_run:
                try:
                    dest.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    return JobResult.fail(f"Failed to create {dest}: {e}", error_code=52)
            else:
                logger.info(f"[DRY-RUN] Would create directory: {dest}")

            if task["subvol"]:
                cmd = [
                    "mount",
                    "-t",
                    "btrfs",
                    "-o",
                    f"{opts},subvol={task['subvol']}",
                    str(task["path"]),
                    str(dest),
                ]
                description = f"Mounting Btrfs subvolume {task['subvol']} at {dest}"
            else:
                cmd = ["mount", str(task["path"]), str(dest)]
                description = f"Mounting {task['path']} at {dest}"

            result = self._run_partitioning_command(cmd, description=description, dry_run=dry_run)
            if not result.success:
                return result

        for assignment in assignments:
            if assignment.get("mountpoint") == "swap":
                result = self._run_partitioning_command(
                    ["swapon", str(assignment["path"])],
                    description=f"Enabling swap on {assignment['path']}",
                    dry_run=dry_run,
                )
                if not result.success:
                    logger.warning(f"Failed to activate swap: {result.message}")

        return JobResult.ok("Manual partitions mounted")

    def _setup_luks(
        self, passphrase: str, dry_run: bool, bootloader: str, has_separate_boot: bool
    ) -> JobResult:
        """
        Wrap the root partition in a LUKS2 container.

        SECURITY: the passphrase is fed through stdin and is never logged, nor
        included in any logged command line.

        Args:
            passphrase: LUKS passphrase (must be non-empty in real runs)
            dry_run: If True, simulate only
            bootloader: exact bootloader selection value ("grub" or
                "systemd-boot") - decides the LUKS2 key derivation function
                (see ``_luks_kdf_args``).
            has_separate_boot: True if a separate, unencrypted partition is
                mounted at /boot ahead of this one (always False in AUTO
                mode's fixed EFI+Root layout).

        Returns:
            JobResult indicating success or failure
        """
        if not self._layout:
            return JobResult.fail("Partition layout not initialized", error_code=40)

        root_part = self._layout.root_partition
        mapper_name = self._layout.luks_mapper_name
        kdf_args = _luks_kdf_args(bootloader, has_separate_boot)

        if dry_run:
            logger.info(f"[DRY-RUN] Would LUKS-format and open {root_part} as {mapper_name}")
            self._layout.encrypted = True
            return JobResult.ok("[DRY-RUN] LUKS setup")

        if not passphrase:
            return JobResult.fail("LUKS passphrase missing", error_code=46)

        fmt = self._run_secret_command(
            ["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode", *kdf_args, root_part],
            passphrase=passphrase,
            description="LUKS formatting root partition",
        )
        if not fmt.success:
            return fmt

        opened = self._run_secret_command(
            ["cryptsetup", "luksOpen", root_part, mapper_name],
            passphrase=passphrase,
            description="Opening LUKS container",
        )
        if not opened.success:
            return opened

        self._layout.encrypted = True
        return JobResult.ok("LUKS container ready")

    def _setup_swapfile(
        self, context: JobContext, strategy: str, filesystem: str, dry_run: bool
    ) -> JobResult:
        """
        Create and activate a swapfile under ``target_root``.

        - ``file``: size = min(RAM, 8192 MB), default 4096 MB if RAM unknown.
        - ``hibernate``: size >= RAM (so the image fits), default 8192 MB.

        Btrfs forbids snapshotting a subvolume containing an active
        swapfile, and a plain dd+mkswap swapfile on Btrfs doesn't work at
        all (needs NOCOW + preallocation) - it lives in its own @swap
        subvolume (mounted at ``<target_root>/swap`` by
        ``_mount_btrfs_subvolumes``) and is created via
        ``btrfs filesystem mkswapfile``, which handles NOCOW and
        preallocation itself.

        Args:
            context: Execution context (provides target_root)
            strategy: "file" or "hibernate"
            filesystem: Root filesystem type
            dry_run: If True, simulate only

        Returns:
            JobResult indicating success or failure
        """
        ram_mb = _detect_ram_mb()
        if strategy == "hibernate":
            size_mb = max(ram_mb, 8192)
        else:  # "file"
            size_mb = min(ram_mb, 8192) if ram_mb else 4096

        if filesystem == FilesystemType.BTRFS.value:
            swapfile = f"{context.target_root}/swap/swapfile"
            logger.info(f"Swapfile strategy={strategy}, size={size_mb}MB at {swapfile} (btrfs)")
            steps = [
                (
                    ["btrfs", "filesystem", "mkswapfile", "--size", f"{size_mb}m",
                     "--uuid", "clear", swapfile],
                    "Allocating Btrfs swapfile",
                ),
                (["swapon", swapfile], "Activating swapfile"),
            ]
        else:
            swapfile = f"{context.target_root}/swapfile"
            logger.info(f"Swapfile strategy={strategy}, size={size_mb}MB at {swapfile}")
            steps = [
                (
                    ["dd", "if=/dev/zero", f"of={swapfile}", "bs=1M", f"count={size_mb}"],
                    "Allocating swapfile",
                ),
                (["chmod", "600", swapfile], "Securing swapfile permissions"),
                (["mkswap", swapfile], "Formatting swapfile"),
                (["swapon", swapfile], "Activating swapfile"),
            ]

        for cmd, description in steps:
            result = self._run_partitioning_command(cmd, description=description, dry_run=dry_run)
            if not result.success:
                return result

        return JobResult.ok("Swapfile configured")

    def _format_partitions(self, context: JobContext, filesystem: str, dry_run: bool) -> JobResult:
        """
        Format partitions according to layout.

        Args:
            context: Execution context
            filesystem: Root filesystem type
            dry_run: If True, simulate only

        Returns:
            JobResult indicating success or failure
        """
        if not self._layout:
            return JobResult.fail("Partition layout not initialized", error_code=40)

        # Format EFI partition (FAT32)
        result = self._run_partitioning_command(
            ["wipefs", "-af", self._layout.efi_partition],
            description=f"Wiping old signatures on {self._layout.efi_partition}",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        result = self._run_partitioning_command(
            ["mkfs.fat", "-F32", self._layout.efi_partition],
            description=f"Formatting EFI partition ({self._layout.efi_partition})",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        # Format root target (the LUKS mapper when encryption is enabled).
        #
        # wipefs -a on the whole disk (in _partition_auto()) can't reach a
        # signature that only becomes visible once a partition exists - a
        # disk reused across installs can leave a stale signature (e.g.
        # crypto_LUKS) here that mkfs never clears, causing initramfs to
        # fail finding root at boot despite a clean install. Must wipe the
        # partition itself, matching ArchWiki's EFI-partition guidance.
        root_target = self._layout.root_target
        result = self._run_partitioning_command(
            ["wipefs", "-af", root_target],
            description=f"Wiping old signatures on {root_target}",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        if filesystem == FilesystemType.EXT4.value:
            cmd = ["mkfs.ext4", "-F", root_target]
        elif filesystem == FilesystemType.XFS.value:
            cmd = ["mkfs.xfs", "-f", root_target]
        elif filesystem == FilesystemType.BTRFS.value:
            cmd = ["mkfs.btrfs", "-f", "-L", "arch_root", root_target]
        else:
            return JobResult.fail(f"Unsupported filesystem: {filesystem}", error_code=41)

        result = self._run_partitioning_command(
            cmd,
            description=f"Formatting root partition ({root_target})",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        if filesystem == FilesystemType.BTRFS.value:
            result = self._create_btrfs_subvolumes(context.target_root, root_target, dry_run)
            if not result.success:
                return result

            # grub-btrfs's 41_snapshots-btrfs script mounts root via
            # /dev/disk/by-uuid/<uuid>, which only exists once udev has
            # processed the signature mkfs.btrfs just wrote. Actively
            # re-probes the device rather than just waiting for whatever
            # udev already happened to queue.
            self._run_partitioning_command(
                ["udevadm", "trigger", "--settle", f"--name-match={root_target}"],
                description="Refreshing udev after mkfs.btrfs",
                dry_run=dry_run,
            )

        # Format swap partition (if exists)
        if self._layout.swap_partition:
            result = self._run_partitioning_command(
                ["wipefs", "-af", self._layout.swap_partition],
                description=f"Wiping old signatures on {self._layout.swap_partition}",
                dry_run=dry_run,
            )
            if not result.success:
                return result

            result = self._run_partitioning_command(
                ["mkswap", self._layout.swap_partition],
                description=f"Formatting swap partition ({self._layout.swap_partition})",
                dry_run=dry_run,
            )
            if not result.success:
                return result

        return JobResult.ok("Partitions formatted successfully")

    def _create_btrfs_subvolumes(
        self, target_root: str, root_target: str, dry_run: bool
    ) -> JobResult:
        """
        Create the Btrfs subvolumes under the top-level subvolume (ID 5),
        which is never used as / directly. Temporarily mounts the
        top-level subvolume at target_root, creates each subvolume, then
        unmounts - _mount_btrfs_subvolumes() mounts the real layout right
        after this returns.
        """
        if not dry_run:
            try:
                Path(target_root).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return JobResult.fail(f"Failed to create {target_root}: {e}", error_code=44)

        result = self._run_partitioning_command(
            ["mount", "-t", "btrfs", "-o", "subvolid=5", root_target, target_root],
            description="Mounting Btrfs top-level subvolume",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        for subvol in self._BTRFS_SUBVOLUMES:
            result = self._run_partitioning_command(
                ["btrfs", "subvolume", "create", f"{target_root}/{subvol}"],
                description=f"Creating Btrfs subvolume {subvol}",
                dry_run=dry_run,
            )
            if not result.success:
                return result

        result = self._run_partitioning_command(
            ["umount", target_root],
            description="Unmounting Btrfs top-level subvolume",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        return JobResult.ok("Btrfs subvolumes created")

    def _mount_btrfs_subvolumes(self, context: JobContext, dry_run: bool) -> JobResult:
        """Mount each Btrfs subvolume at its target path, in order - "@"
        must land on target_root before the others can mount underneath
        it. target_root itself is already created by the caller."""
        target_root = context.target_root
        root_target = self._layout.root_target if self._layout else ""

        for subvol, relative_path in self._BTRFS_SUBVOLUME_MOUNTS:
            mount_point = f"{target_root}/{relative_path}" if relative_path else target_root
            if not dry_run:
                try:
                    Path(mount_point).mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    return JobResult.fail(f"Failed to create {mount_point}: {e}", error_code=59)

            result = self._run_partitioning_command(
                [
                    "mount",
                    "-t",
                    "btrfs",
                    "-o",
                    f"{self._BTRFS_MOUNT_OPTIONS},subvol={subvol}",
                    root_target,
                    mount_point,
                ],
                description=f"Mounting Btrfs subvolume {subvol} at {mount_point}",
                dry_run=dry_run,
            )
            if not result.success:
                return result

        return JobResult.ok("Btrfs subvolumes mounted")

    def _mount_partitions(self, context: JobContext, filesystem: str, dry_run: bool) -> JobResult:
        """
        Mount partitions to target root.

        Hierarchy:
        - /mnt (target_root) ← root partition (or LUKS mapper)
        - /mnt/boot/efi ← EFI partition (GRUB expects the ESP on /boot/efi)

        Args:
            context: Execution context
            dry_run: If True, simulate only

        Returns:
            JobResult indicating success or failure
        """
        if not self._layout:
            return JobResult.fail("Partition layout not initialized", error_code=42)

        target_root = context.target_root

        # Mount point must exist before mount(8) will target it.
        if not dry_run:
            try:
                Path(target_root).mkdir(parents=True, exist_ok=True)
                logger.info(f"Created root mount point: {target_root}")
            except OSError as e:
                logger.error(f"Failed to create root mount point: {e}")
                return JobResult.fail(f"Failed to create {target_root}: {e}", error_code=58)
        else:
            logger.info(f"[DRY-RUN] Would create directory: {target_root}")

        if filesystem == FilesystemType.BTRFS.value:
            result = self._mount_btrfs_subvolumes(context, dry_run)
        else:
            # Explicit -t: without it, mount can auto-detect a stale type
            # (blkid cache not refreshed after mkfs) and fail with a
            # "wrong fs type" error.
            result = self._run_partitioning_command(
                ["mount", "-t", filesystem, self._layout.root_target, target_root],
                description=f"Mounting root partition to {target_root}",
                dry_run=dry_run,
            )
        if not result.success:
            return result

        # Create EFI mount point: ESP lives on /boot/efi (GRUB's --efi-directory).
        efi_mount = Path(target_root) / "boot" / "efi"
        if not dry_run:
            try:
                efi_mount.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created EFI mount point: {efi_mount}")
            except OSError as e:
                logger.error(f"Failed to create EFI mount point: {e}")
                return JobResult.fail(f"Failed to create {efi_mount}: {e}", error_code=43)
        else:
            logger.info(f"[DRY-RUN] Would create directory: {efi_mount}")

        # Mount EFI partition
        result = self._run_partitioning_command(
            ["mount", "-t", "vfat", self._layout.efi_partition, str(efi_mount)],
            description=f"Mounting EFI partition to {efi_mount}",
            dry_run=dry_run,
        )
        if not result.success:
            return result

        # Activate swap (if exists)
        if self._layout.swap_partition:
            result = self._run_partitioning_command(
                ["swapon", self._layout.swap_partition],
                description=f"Activating swap ({self._layout.swap_partition})",
                dry_run=dry_run,
            )
            if not result.success:
                # Swap activation failure is not critical
                logger.warning(f"Failed to activate swap: {result.message}")

        return JobResult.ok("Partitions mounted successfully")

    def _run_partitioning_command(
        self, cmd: list[str], description: str, dry_run: bool
    ) -> JobResult:
        """
        Run a partitioning command with dry-run support.

        Args:
            cmd: Command to execute
            description: Human-readable description for logging
            dry_run: If True, simulate only

        Returns:
            JobResult indicating success or failure
        """
        if dry_run:
            logger.info(f"[DRY-RUN] {description}: {' '.join(cmd)}")
            return JobResult.ok(f"[DRY-RUN] {description}")

        logger.info(f"Executing: {description}")
        logger.debug(f"Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"✓ {description} completed successfully")
            if result.stdout:
                logger.debug(f"stdout: {result.stdout}")
            return JobResult.ok(description)

        except subprocess.CalledProcessError as e:
            logger.error(f"✗ {description} failed: {e}")
            logger.error(f"stderr: {e.stderr}")
            return JobResult.fail(
                f"{description} failed: {e.stderr}",
                error_code=44,
            )
        except FileNotFoundError:
            logger.error(f"Command not found: {cmd[0]}")
            return JobResult.fail(f"Required tool not found: {cmd[0]}", error_code=45)

    def _run_secret_command(self, cmd: list[str], passphrase: str, description: str) -> JobResult:
        """
        Run a command that consumes a secret via stdin.

        SECURITY: neither the passphrase nor the full command line is logged.
        Only the human-readable description is emitted. This is used for
        ``cryptsetup luksFormat`` / ``luksOpen`` where the passphrase is piped
        in rather than passed as an argument.

        Args:
            cmd: Command to execute (must NOT contain the passphrase)
            passphrase: Secret fed to the process stdin
            description: Human-readable description for logging

        Returns:
            JobResult indicating success or failure
        """
        logger.info(f"Executing: {description}")

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                input=passphrase,
            )
            logger.info(f"✓ {description} completed successfully")
            return JobResult.ok(description)
        except subprocess.CalledProcessError as e:
            # SECURITY: log stderr only; cryptsetup does not echo the passphrase.
            logger.error(f"✗ {description} failed: {e.stderr}")
            return JobResult.fail(f"{description} failed", error_code=44)
        except FileNotFoundError:
            logger.error(f"Command not found: {cmd[0]}")
            return JobResult.fail(f"Required tool not found: {cmd[0]}", error_code=45)

    def cleanup(self, context: JobContext) -> None:
        """
        Clean up after a partitioning FAILURE only.

        The engine calls ``cleanup()`` after every job (in a ``finally`` block),
        on success as well as failure. On success the target filesystems must
        stay mounted: the downstream install job runs against these mounts and
        performs the final unmount itself. Tearing them down here previously
        left that job's config-generation step looking at an empty target,
        which then failed because it could not determine the root filesystem.

        We therefore only unmount / close LUKS when partitioning did NOT hand off
        a mounted target (i.e. it failed).

        Args:
            context: Execution context
        """
        if self._succeeded:
            # Mounted target handed off to the downstream install job; do not tear it down.
            return

        if not self._layout:
            return

        target_root = context.target_root
        logger.info("Cleaning up mounted filesystems...")

        # Unmount in reverse order (ESP is mounted on /boot/efi)
        efi_mount = Path(target_root) / "boot" / "efi"

        # Try to unmount EFI
        try:
            result = subprocess.run(
                ["umount", str(efi_mount)],
                check=False,
                capture_output=True,
            )
            if result.returncode == 0:
                logger.info(f"Unmounted {efi_mount}")
        except Exception as e:
            logger.debug(f"Failed to unmount {efi_mount}: {e}")

        # Try to unmount root
        try:
            result = subprocess.run(
                ["umount", target_root],
                check=False,
                capture_output=True,
            )
            if result.returncode == 0:
                logger.info(f"Unmounted {target_root}")
        except Exception as e:
            logger.debug(f"Failed to unmount {target_root}: {e}")

        # Try to deactivate swap partition (legacy path)
        if self._layout.swap_partition:
            try:
                result = subprocess.run(
                    ["swapoff", self._layout.swap_partition],
                    check=False,
                    capture_output=True,
                )
                if result.returncode == 0:
                    logger.info(f"Deactivated swap {self._layout.swap_partition}")
            except Exception as e:
                logger.debug(f"Failed to deactivate swap: {e}")

        # Try to close the LUKS mapper if it was opened
        if self._layout.encrypted:
            try:
                subprocess.run(
                    ["cryptsetup", "luksClose", self._layout.luks_mapper_name],
                    check=False,
                    capture_output=True,
                )
                logger.info(f"Closed LUKS mapper {self._layout.luks_mapper_name}")
            except Exception as e:
                logger.debug(f"Failed to close LUKS mapper: {e}")

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Partitioning operations are relatively fast, but formatting
        large disks can take time.

        Returns:
            Estimated duration in seconds (60s for average disk)
        """
        return 60

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """
        Format byte size to human-readable string.

        Args:
            size_bytes: Size in bytes

        Returns:
            Human-readable size (e.g., "256 GB")
        """
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

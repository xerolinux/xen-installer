from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from xen.partitioning import fscaps
from xen.partitioning.fscaps import caps_for
from xen.i18n.messages import msg
from xen.utils.disk_detector import (
    ESP_TYPE_GUID,
    LINUX_TYPE_GUID,
    SWAP_TYPE_GUID,
    align_sectors,
    compute_segments,
    last_usable_sector,
)

MIB = 1024**2
GIB = 1024**3
_MIN_ESP_BYTES = 32 * MIB
_SMALL_ESP_BYTES = 100 * MIB
_RECOMMENDED_NEW_ESP_BYTES = 2000 * MIB
_FORMATTABLE = ("ext4", "xfs", "btrfs", "fat32", "swap")
_ROOT_FS = ("ext4", "xfs", "btrfs")
_FAT = ("vfat", "fat", "fat32")
_FS_ALIASES = {"vfat": "fat32", "fat": "fat32", "linuxswap": "swap", "linux-swap": "swap"}
_NTFS_MOUNT_BLOCKERS = (fscaps.REASON_HIBERNATED, fscaps.REASON_UNCLEAN)
_GPT_NAMES = {"/": "XeroLinux root", "/home": "XeroLinux home", "swap": "XeroLinux swap"}

# Snapper/grub-btrfs assume this installer's '@'-prefixed top-level layout.
_BTRFS_SUBVOL_NAME_RE = re.compile(r"^@[A-Za-z0-9_-]*$")


@dataclass
class Create:
    ref: str
    start: int
    end: int
    type_guid: str
    name: str


@dataclass
class Delete:
    number: int


@dataclass
class Resize:
    number: int
    new_end: int
    fstype: str
    shrink: bool


@dataclass
class Target:
    ref: str
    fstype: str
    format: bool
    mountpoint: str
    label: str = ""
    encrypt: bool = False
    subvolumes: list[dict[str, Any]] = field(default_factory=list)
    is_esp: bool = False
    # "" means the plan's own disk; set only for a reused ESP on another disk.
    disk: str = ""


@dataclass
class PartitionPlan:
    disk: str
    sector_size: int
    new_table: bool
    deletes: list[Delete] = field(default_factory=list)
    resizes: list[Resize] = field(default_factory=list)
    creates: list[Create] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    skipped_subvolumes: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_btrfs_subvolumes(subvolumes: list[dict[str, Any]]) -> tuple[bool, str]:
    if not subvolumes:
        return False, msg("At least one Btrfs subvolume is required")

    names: list[str] = []
    mountpoints: list[str] = []
    root_count = 0
    for sv in subvolumes:
        name = str(sv.get("name", ""))
        mount = str(sv.get("mountpoint", ""))
        if not _BTRFS_SUBVOL_NAME_RE.match(name):
            return False, msg(
                "Invalid subvolume name {name}: must start with '@' and contain only "
                "letters, digits, '-' or '_'",
                name=repr(name),
            )
        names.append(name)
        if mount in ("", "/"):
            root_count += 1
            mountpoints.append("/")
        else:
            if not mount.startswith("/"):
                return False, msg(
                    "Subvolume mountpoint {mount} must start with '/'", mount=repr(mount)
                )
            mountpoints.append(mount)

    if root_count == 0:
        return False, msg("One subvolume must be the root (mountpoint '/')")
    if root_count > 1:
        return False, msg("Only one subvolume may be the root (mountpoint '/')")
    if len(names) != len(set(names)):
        return False, msg("Duplicate subvolume name")
    if len(mountpoints) != len(set(mountpoints)):
        return False, msg("Duplicate subvolume mountpoint")

    return True, ""


def _base(disk_name: str) -> str:
    return disk_name.rsplit("/", 1)[-1]


def _norm_fs(fstype: Any) -> str:
    value = str(fstype or "").strip()
    return _FS_ALIASES.get(value.lower(), value)


def _gpt_name(mountpoint: str, is_esp: bool) -> str:
    return "EFI system" if is_esp else _GPT_NAMES.get(mountpoint, "XeroLinux data")


def _type_guid(fstype: str, mountpoint: str) -> str:
    if fstype == "fat32" and mountpoint == "/boot/efi":
        return ESP_TYPE_GUID
    return SWAP_TYPE_GUID if fstype == "swap" else LINUX_TYPE_GUID


def size_text(size_bytes: int) -> str:
    if size_bytes >= GIB:
        return f"{size_bytes / GIB:.1f} GiB"
    return f"{size_bytes // MIB} MiB"


def _part_by_number(disk: dict[str, Any], number: int) -> dict[str, Any] | None:
    return next((p for p in disk["partitions"] if int(p["number"]) == number), None)


def _part_name(part: dict[str, Any]) -> str:
    return str(part.get("path") or f"/dev/{part.get('name', '')}")


def _new_name(mountpoint: str) -> str:
    if mountpoint:
        return msg("the new {mountpoint} partition", mountpoint=mountpoint)
    return msg("the new partition without a mount point")


def _subvolumes(fstype: str, subvolumes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(sv) for sv in subvolumes or []] if fstype == "btrfs" else []


def build_erase_plan(
    disk: dict[str, Any],
    fstype: str,
    encrypt: bool,
    efi_size_mb: int,
    subvolumes: list[dict[str, Any]],
) -> PartitionPlan:
    sector_size = int(disk["sectorSize"])
    align = align_sectors(sector_size)
    esp_end = align + efi_size_mb * align - 1
    root_end = last_usable_sector(int(disk["sizeSectors"]), sector_size)
    return PartitionPlan(
        disk=disk["name"],
        sector_size=sector_size,
        new_table=True,
        creates=[
            Create("esp", align, esp_end, ESP_TYPE_GUID, _gpt_name("/boot/efi", True)),
            Create("root", esp_end + 1, root_end, LINUX_TYPE_GUID, _gpt_name("/", False)),
        ],
        targets=[
            Target("new:esp", "fat32", True, "/boot/efi", is_esp=True),
            Target(
                "new:root",
                fstype,
                True,
                "/",
                label="arch_root" if fstype == "btrfs" else "",
                encrypt=encrypt,
                subvolumes=_subvolumes(fstype, subvolumes),
            ),
        ],
    )


def build_alongside_plan(
    disk: dict[str, Any],
    shrink_number: int,
    keep_size_bytes: int,
    fstype: str,
    encrypt: bool,
    esp_number: int,
    esp_disk: str,
    subvolumes: list[dict[str, Any]],
) -> PartitionPlan:
    part = _part_by_number(disk, shrink_number)
    if part is None:
        raise ValueError(
            msg(
                "Partition {number} doesn't exist on {disk}.",
                number=shrink_number,
                disk=disk["name"],
            )
        )
    sector_size = int(disk["sectorSize"])
    align = align_sectors(sector_size)
    start = int(part["startSector"])
    new_end = start + (int(keep_size_bytes) // MIB) * align - 1
    root_start = -(-(new_end + 1) // align) * align
    later = [int(p["startSector"]) for p in disk["partitions"] if int(p["startSector"]) > start]
    root_end = min(
        [s - 1 for s in later] + [last_usable_sector(int(disk["sizeSectors"]), sector_size)]
    )

    same_disk = _base(esp_disk or disk["name"]) == _base(disk["name"])
    esp_part = _part_by_number(disk, esp_number) if same_disk else None
    esp = Target(
        f"part:{esp_number}",
        str(esp_part["fstype"]) if esp_part else "vfat",
        False,
        "/boot/efi",
        is_esp=True,
        disk="" if same_disk else _base(esp_disk),
    )
    return PartitionPlan(
        disk=disk["name"],
        sector_size=sector_size,
        new_table=False,
        resizes=[Resize(shrink_number, new_end, str(part["fstype"]), True)],
        creates=[Create("root", root_start, root_end, LINUX_TYPE_GUID, _gpt_name("/", False))],
        targets=[
            Target(
                "new:root",
                fstype,
                True,
                "/",
                encrypt=encrypt,
                subvolumes=_subvolumes(fstype, subvolumes),
            ),
            esp,
        ],
    )


def build_manual_plan(
    disk: dict[str, Any], rows: list[dict[str, Any]], new_table: bool
) -> PartitionPlan:
    plan = PartitionPlan(
        disk=disk["name"], sector_size=int(disk["sectorSize"]), new_table=new_table
    )
    for row in rows:
        mountpoint = str(row.get("mountpoint") or "").strip()
        label = str(row.get("label") or "")
        encrypt = bool(row.get("encrypt"))
        subvolumes = [dict(sv) for sv in row.get("subvolumes") or []]
        if row.get("kind") == "new":
            fstype = _norm_fs(row.get("fstype"))
            if fstype == "swap":
                mountpoint = "swap"
            guid = _type_guid(fstype, mountpoint)
            ref = str(row["ref"])
            is_esp = guid == ESP_TYPE_GUID
            plan.creates.append(
                Create(ref, int(row["start"]), int(row["end"]), guid, _gpt_name(mountpoint, is_esp))
            )
            plan.targets.append(
                Target(f"new:{ref}", fstype, True, mountpoint, label, encrypt, subvolumes, is_esp)
            )
            continue

        number = int(row["number"])
        part = _part_by_number(disk, number) or {}
        if row.get("action") == "delete":
            if not new_table:
                plan.deletes.append(Delete(number))
            continue
        new_end = row.get("newEnd")
        if new_end is not None and int(new_end) != int(part.get("endSector", -1)):
            shrink = int(new_end) < int(part.get("endSector", 0))
            plan.resizes.append(Resize(number, int(new_end), str(part.get("fstype", "")), shrink))
        formatting = row.get("action") == "format"
        if not (formatting or mountpoint or encrypt or subvolumes):
            continue
        fstype = _norm_fs(row.get("fstype")) if formatting else str(part.get("fstype", ""))
        if fstype == "swap" and not mountpoint:
            mountpoint = "swap"
        plan.targets.append(
            Target(
                f"part:{number}",
                fstype,
                formatting,
                mountpoint,
                label,
                encrypt,
                subvolumes,
                bool(part.get("isEsp")),
            )
        )
    _skip_claimed_subvolumes(plan)
    return plan


def _skip_claimed_subvolumes(plan: PartitionPlan) -> None:
    # Calamares does the same: a separate partition wins over the subvolume for its mount point.
    claimed = {t.mountpoint for t in plan.targets if not t.subvolumes}
    for target in plan.targets:
        if not any(str(sv.get("mountpoint") or "/") == "/" for sv in target.subvolumes):
            continue
        kept = []
        for sv in target.subvolumes:
            mount = str(sv.get("mountpoint") or "/")
            if mount != "/" and mount in claimed:
                skipped = {"name": str(sv.get("name", "")), "mountpoint": mount}
                plan.skipped_subvolumes.append(skipped)
            else:
                kept.append(sv)
        target.subvolumes = kept


@dataclass
class _Resolved:
    target: Target
    name: str
    size_bytes: int
    is_esp: bool
    fstype: str
    part: dict[str, Any] | None
    mounts: list[str]


def _target_disk(
    target: Target, disk: dict[str, Any], other_disks: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not target.disk or _base(target.disk) == _base(disk["name"]):
        return disk
    return next((d for d in other_disks if _base(d["name"]) == _base(target.disk)), None)


def _resolve(
    plan: PartitionPlan, disk: dict[str, Any], other_disks: list[dict[str, Any]]
) -> tuple[list[_Resolved], list[str]]:
    sector_size = int(disk["sectorSize"])
    creates = {c.ref: c for c in plan.creates}
    deleted = {d.number for d in plan.deletes}
    resized = {r.number: r.new_end for r in plan.resizes}
    resolved: list[_Resolved] = []
    errors: list[str] = []
    for target in plan.targets:
        if target.ref.startswith("new:"):
            create = creates.get(target.ref[4:])
            if create is None:
                errors.append(msg("No new partition matches {ref}.", ref=target.ref))
                continue
            name = _new_name(target.mountpoint)
            size = (create.end - create.start + 1) * sector_size
            is_esp, part = create.type_guid == ESP_TYPE_GUID, None
        else:
            owner = _target_disk(target, disk, other_disks)
            number = int(target.ref.removeprefix("part:") or 0)
            part = _part_by_number(owner, number) if owner else None
            if part is None:
                where = target.disk or disk["name"]
                errors.append(
                    msg("Partition {number} doesn't exist on {disk}.", number=number, disk=where)
                )
                continue
            name = _part_name(part)
            if owner is disk and plan.new_table:
                errors.append(msg("{name} is removed by the new partition table.", name=name))
                continue
            if owner is disk and number in deleted:
                errors.append(msg("{name} is being deleted and can't be used.", name=name))
                continue
            end = int(part["endSector"])
            if owner is disk:
                end = resized.get(number, end)
            size = (end - int(part["startSector"]) + 1) * int(owner["sectorSize"])
            is_esp = bool(part.get("isEsp"))
        fstype = target.fstype if target.format else str(part.get("fstype", "") if part else "")
        if target.subvolumes:
            mounts = [str(sv.get("mountpoint") or "/") for sv in target.subvolumes]
        else:
            mounts = [target.mountpoint] if target.mountpoint.startswith("/") else []
        resolved.append(_Resolved(target, name, size, is_esp, fstype, part, mounts))
    return resolved, errors


def _geometry_errors(plan: PartitionPlan, disk: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    sector_size = int(disk["sectorSize"])
    align = align_sectors(sector_size)
    last = last_usable_sector(int(disk["sizeSectors"]), sector_size)
    existing = {} if plan.new_table else {int(p["number"]): p for p in disk["partitions"]}
    deleted = {d.number for d in plan.deletes}
    resized = {r.number: r.new_end for r in plan.resizes}

    for number in sorted(deleted):
        if number not in existing:
            errors.append(
                msg("Partition {number} doesn't exist on {disk}.", number=number, disk=disk["name"])
            )
    for resize in plan.resizes:
        part = existing.get(resize.number)
        if part is None:
            errors.append(
                msg(
                    "Partition {number} doesn't exist on {disk}.",
                    number=resize.number,
                    disk=disk["name"],
                )
            )
            continue
        errors.extend(_resize_errors(resize, part, sector_size, resize.number in deleted))

    spans: list[tuple[str, int, int]] = []
    for number, part in existing.items():
        if number not in deleted:
            end = resized.get(number, int(part["endSector"]))
            spans.append((_part_name(part), int(part["startSector"]), end))
    targets = {t.ref: t for t in plan.targets}
    refs: set[str] = set()
    for create in plan.creates:
        target = targets.get(f"new:{create.ref}")
        name = _new_name(target.mountpoint if target else "")
        if create.ref in refs:
            errors.append(
                msg("Two new partitions use the same reference {ref}.", ref=create.ref)
            )
        refs.add(create.ref)
        if create.start % align:
            errors.append(msg("The start of {name} is not aligned to 1 MiB.", name=name))
        if create.start < align:
            errors.append(
                msg("The start of {name} is before the first usable sector.", name=name)
            )
        spans.append((name, create.start, create.end))

    for name, start, end in spans:
        if end < start:
            errors.append(msg("The end of {name} is before its start.", name=name))
        elif end > last:
            errors.append(msg("The end of {name} is past the end of the disk.", name=name))
    spans.sort(key=lambda s: s[1])
    for i, (name, start, end) in enumerate(spans):
        for other, other_start, _ in spans[i + 1 :]:
            if other_start <= end:
                errors.append(msg("Partitions {name} and {other} overlap.", name=name, other=other))
            else:
                break
    return errors


def _resize_errors(
    resize: Resize, part: dict[str, Any], sector_size: int, deleted: bool
) -> list[str]:
    name = _part_name(part)
    fstype = str(part.get("fstype", ""))
    start, end = int(part["startSector"]), int(part["endSector"])
    if deleted:
        return [msg("{name} can't be both deleted and resized.", name=name)]
    if part.get("resizeBlockedReason"):
        return [
            msg(
                "{name} can't be resized: {reason}.",
                name=name,
                reason=part["resizeBlockedReason"],
            )
        ]
    if resize.fstype != fstype:
        expected = resize.fstype or msg("no file system")
        actual = fstype or msg("no file system")
        return [
            msg(
                "The resize of {name} expects {expected}, but it holds {actual}.",
                name=name,
                expected=expected,
                actual=actual,
            )
        ]
    if resize.new_end < start:
        return [msg("The new end of {name} is before its start.", name=name)]
    if resize.shrink != (resize.new_end < end):
        return [msg("The resize of {name} doesn't match its direction.", name=name)]
    caps = caps_for(fstype)
    if resize.shrink:
        if not (caps.can_shrink and part.get("canShrink", True)):
            return [
                msg(
                    "{name} can't be shrunk: {reason}.",
                    name=name,
                    reason=caps.blocked_reason or msg("not supported"),
                )
            ]
        min_bytes = fscaps.min_shrink_bytes(int(part.get("minSizeBytes") or 0))
        if (resize.new_end - start + 1) * sector_size < min_bytes:
            return [
                msg(
                    "{name} can't be shrunk below {size}.", name=name, size=size_text(min_bytes)
                )
            ]
    elif not (caps.can_grow and part.get("canGrow", True)):
        return [
            msg(
                "{name} can't be grown: {reason}.",
                name=name,
                reason=caps.blocked_reason or msg("not supported"),
            )
        ]
    return []


def _in_use_errors(
    plan: PartitionPlan, disk: dict[str, Any], resolved: list[_Resolved]
) -> list[str]:
    touched = {d.number for d in plan.deletes} | {r.number for r in plan.resizes}
    parts = [p for p in disk["partitions"] if int(p["number"]) in touched]
    parts += [r.part for r in resolved if r.part is not None]
    if plan.new_table:
        parts += disk["partitions"]
    names = dict.fromkeys(_part_name(p) for p in parts if p.get("mounted"))
    return [msg("{name} is in use. Unmount it before installing.", name=name) for name in names]


def _target_errors(item: _Resolved) -> list[str]:
    target, name, fstype = item.target, item.name, item.fstype
    mount = target.mountpoint
    errors: list[str] = []
    if target.format and fstype not in _FORMATTABLE:
        errors.append(
            msg(
                "{name} can't be formatted as {fstype}.",
                name=name,
                fstype=fstype or msg("nothing"),
            )
        )
    if not target.format:
        if fstype == "crypto_LUKS":
            errors.append(
                msg("{name} is encrypted (LUKS). Format or delete it to use it.", name=name)
            )
        elif fstype == "BitLocker":
            errors.append(
                msg(
                    "{name} has BitLocker turned on. Format it, or turn BitLocker off in "
                    "Windows first.",
                    name=name,
                )
            )
        elif not fstype and mount:
            errors.append(msg("{name} has no file system. Format it to use it.", name=name))
        if target.encrypt:
            errors.append(msg("{name} can only be encrypted when it is formatted.", name=name))
        reason = str((item.part or {}).get("resizeBlockedReason") or "")
        if mount and fscaps.fs_kind(fstype) == "ntfs" and reason in _NTFS_MOUNT_BLOCKERS:
            errors.append(msg("{name} can't be mounted: {reason}.", name=name, reason=reason))
    if mount not in ("", "swap") and not mount.startswith("/"):
        errors.append(
            msg('The mount point "{mount}" of {name} must start with /.', mount=mount, name=name)
        )
    if mount == "swap" and fstype != "swap":
        errors.append(msg("{name} is used as swap but isn't a swap partition.", name=name))
    if fstype == "swap" and mount not in ("", "swap"):
        errors.append(
            msg(
                "{name} is a swap partition and can't be mounted at {mount}.",
                name=name,
                mount=mount,
            )
        )
    if target.subvolumes:
        if not (target.format and fstype == "btrfs"):
            errors.append(
                msg(
                    "{name}: subvolumes need a Btrfs partition that is being formatted.",
                    name=name,
                )
            )
        elif mount != "/":
            errors.append(msg("Btrfs subvolumes are only supported on the root (/) partition."))
        ok, reason = validate_btrfs_subvolumes(target.subvolumes)
        if not ok:
            errors.append(msg("{name}: {reason}.", name=name, reason=reason))
        if any(m == "/boot/efi" or m.startswith("/boot/efi/") for m in item.mounts):
            errors.append(msg("Btrfs subvolumes can't be mounted at or under /boot/efi."))
    if item.is_esp and mount and mount != "/boot/efi":
        errors.append(
            msg(
                "The EFI system partition {name} must be mounted at /boot/efi, not {mount}.",
                name=name,
                mount=mount,
            )
        )
    if mount == "/boot/efi":
        if not item.is_esp:
            errors.append(
                msg("{name} is mounted at /boot/efi but isn't an EFI system partition.", name=name)
            )
        elif fstype not in _FAT:
            errors.append(
                msg(
                    "The EFI system partition must be FAT32, not {fstype}.",
                    fstype=fstype or msg("unformatted"),
                )
            )
        if item.size_bytes < _MIN_ESP_BYTES:
            errors.append(msg("The EFI system partition must be at least 32 MiB."))
    if target.encrypt and (item.is_esp or mount == "/boot/efi"):
        errors.append(msg("The EFI system partition can't be encrypted."))
    if target.encrypt and mount == "/boot":
        errors.append(
            msg(
                "A separate /boot can't be encrypted. Leave /boot unencrypted, "
                "or remove it so /boot lives on the encrypted root."
            )
        )
    return errors


def _root(resolved: list[_Resolved]) -> _Resolved | None:
    roots = [r for r in resolved if "/" in r.mounts]
    return roots[0] if len(roots) == 1 else None


def validate_plan(
    plan: PartitionPlan,
    disk: dict[str, Any],
    *,
    uefi: bool,
    has_internet: bool,
    min_install_gib: int,
    other_disks: list[dict[str, Any]] | None = None,
) -> list[str]:
    errors: list[str] = []
    if _base(plan.disk) != _base(disk["name"]):
        errors.append(
            msg(
                "The partition plan is for {plan_disk}, not {disk}.",
                plan_disk=plan.disk,
                disk=disk["name"],
            )
        )
    if plan.sector_size != int(disk["sectorSize"]):
        errors.append(msg("The disk's sector size changed since the plan was made."))
    if not plan.new_table and disk.get("ptType") != "gpt":
        return [
            *errors,
            msg(
                "This disk doesn't use a GPT partition table: use Erase or New Partition Table."
            ),
        ]
    errors += _geometry_errors(plan, disk)
    resolved, resolve_errors = _resolve(plan, disk, other_disks or [])
    errors += resolve_errors
    errors += _in_use_errors(plan, disk, resolved)

    resized = {r.number for r in plan.resizes}
    if any(t.format and t.ref in {f"part:{n}" for n in resized} for t in plan.targets):
        errors.append(
            msg("Resize or format, not both: delete it and create a new one instead.")
        )

    seen_refs: set[tuple[str, str]] = set()
    mounts: list[str] = []
    for item in resolved:
        key = (item.target.disk, item.target.ref)
        if key in seen_refs:
            errors.append(msg("{name} is used more than once.", name=item.name))
        seen_refs.add(key)
        errors += _target_errors(item)
        mounts += item.mounts

    for mount in dict.fromkeys(m for m in mounts if mounts.count(m) > 1):
        errors.append(msg("The mount point {mount} is used more than once.", mount=mount))

    root = _root(resolved)
    if "/" not in mounts:
        errors.append(msg("No partition is assigned to root (/)."))
    elif root is not None:
        if not root.target.format:
            errors.append(
                msg(
                    "The root (/) partition must be formatted: keeping its contents isn't "
                    "supported."
                )
            )
        if root.fstype not in _ROOT_FS:
            errors.append(
                msg(
                    "The root (/) partition must use ext4, XFS or Btrfs, not {fstype}.",
                    fstype=root.fstype or msg("no file system"),
                )
            )
        elif root.fstype == "btrfs" and not has_internet:
            errors.append(
                msg("A Btrfs root needs an internet connection to install its snapshot tools.")
            )
        if root.size_bytes < min_install_gib * GIB:
            errors.append(
                msg("The root (/) partition must be at least {gib} GiB.", gib=min_install_gib)
            )
        for item in resolved:
            encrypted_data = item.target.encrypt and item is not root and item.fstype != "swap"
            if encrypted_data and not item.is_esp and not root.target.encrypt:
                errors.append(
                    msg(
                        "{name} is encrypted but root (/) is not. Encrypt root too, "
                        "or its unlock key would be stored unencrypted.",
                        name=item.name,
                    )
                )

    has_esp = any(
        r.is_esp and r.fstype in _FAT and r.target.mountpoint == "/boot/efi" for r in resolved
    )
    if uefi and not has_esp:
        errors.append(msg("No EFI system partition is assigned to /boot/efi."))
    return list(dict.fromkeys(errors))


def plan_warnings(
    plan: PartitionPlan,
    disk: dict[str, Any],
    *,
    other_disks: list[dict[str, Any]] | None = None,
    expect_swap_partition: bool = True,
) -> list[str]:
    resolved, _ = _resolve(plan, disk, other_disks or [])
    warnings: list[str] = []
    if any(
        r.is_esp and not r.target.format and r.size_bytes < _SMALL_ESP_BYTES
        for r in resolved
        if r.target.mountpoint == "/boot/efi"
    ):
        warnings.append(
            msg(
                "The EFI system partition is smaller than 100 MiB. It works, "
                "but leaves little room for other boot loaders."
            )
        )
    if any(
        r.is_esp and r.target.ref.startswith("new:")
        and r.size_bytes < _RECOMMENDED_NEW_ESP_BYTES
        for r in resolved
        if r.target.mountpoint == "/boot/efi"
    ):
        warnings.append(msg("The EFI partition is smaller than the recommended 2000 MiB."))
    if expect_swap_partition and not any(t.mountpoint == "swap" for t in plan.targets):
        warnings.append(msg("There is no swap partition. Hibernation won't be available."))
    warnings += [
        msg(
            "{name} skipped: {mountpoint} is a separate partition.",
            name=sv["name"],
            mountpoint=sv["mountpoint"],
        )
        for sv in plan.skipped_subvolumes
    ]
    root = _root(resolved)
    if root is not None and root.target.encrypt and any(
        t.mountpoint == "swap" and not t.encrypt for t in plan.targets
    ):
        warnings.append(
            msg(
                "Swap is not encrypted while / is: hibernation would write the encryption key "
                "to disk unencrypted."
            )
        )
    return warnings


def plan_checklist(
    plan: PartitionPlan, disk: dict[str, Any], *, other_disks: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    resolved, _ = _resolve(plan, disk, other_disks or [])
    root = _root(resolved)
    esps = [r for r in resolved if "/boot/efi" in r.mounts]
    root_ok = root is not None and root.target.format and root.fstype in _ROOT_FS
    esp_ok = len(esps) == 1 and esps[0].is_esp and esps[0].fstype in _FAT
    return [
        {"text": msg("Root (/) assigned"), "ok": root_ok},
        {"text": msg("EFI partition at /boot/efi"), "ok": esp_ok},
    ]


def _target_fields(target: Target | None) -> dict[str, Any]:
    if target is None:
        return {"mountpoint": "", "format": False, "encrypt": False}
    return {"mountpoint": target.mountpoint, "format": target.format, "encrypt": target.encrypt}


def _part_type(type_guid: str, fstype: str) -> str:
    if type_guid == ESP_TYPE_GUID:
        return "efi"
    return "swap" if type_guid == SWAP_TYPE_GUID or fstype == "swap" else "linux"


def preview_segments(plan: PartitionPlan, disk: dict[str, Any]) -> list[dict[str, Any]]:
    sector_size = int(disk["sectorSize"])
    deleted = {d.number for d in plan.deletes}
    resized = {r.number: r.new_end for r in plan.resizes}
    targets = {t.ref: t for t in plan.targets if _target_disk(t, disk, []) is disk}
    live: list[dict[str, Any]] = []
    gone: list[dict[str, Any]] = []
    for part in disk["partitions"]:
        number = int(part["number"])
        seg = {**part, "pending": False, "pendingDelete": False, "newRef": ""}
        if plan.new_table or number in deleted:
            seg.update(_target_fields(None), kind="partition", pending=True, pendingDelete=True)
            gone.append(seg)
            continue
        target = targets.get(f"part:{number}")
        seg.update(_target_fields(target))
        if number in resized:
            end = resized[number]
            size = end - int(part["startSector"]) + 1
            seg.update(endSector=end, sizeSectors=size, sizeBytes=size * sector_size, pending=True)
        if target is not None and target.format:
            seg.update(fstype=target.fstype, label=target.label, pending=True)
        live.append(seg)

    for create in plan.creates:
        target = targets.get(f"new:{create.ref}")
        fstype = target.fstype if target else ""
        size = create.end - create.start + 1
        live.append(
            {
                "name": "",
                "path": "",
                "disk": disk["name"],
                "number": 0,
                "startSector": create.start,
                "endSector": create.end,
                "sizeSectors": size,
                "sizeBytes": size * sector_size,
                "fstype": fstype,
                "partType": _part_type(create.type_guid, fstype),
                "partTypeGuid": create.type_guid,
                "partLabel": create.name,
                "label": target.label if target else "",
                "isEsp": create.type_guid == ESP_TYPE_GUID,
                "osName": "",
                **_target_fields(target),
                "pending": True,
                "pendingDelete": False,
                "newRef": create.ref,
            }
        )

    segments = compute_segments(int(disk["sizeSectors"]), live, sector_size)
    for seg in segments:
        if seg["kind"] == "free":
            seg.update(pending=False, pendingDelete=False, newRef="")
    combined = segments + gone
    combined.sort(key=lambda s: (int(s["startSector"]), s["pendingDelete"]))
    return combined

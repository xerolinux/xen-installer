from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xen.partitioning import fscaps
from xen.partitioning.plan import (
    Delete,
    PartitionPlan,
    Resize,
    build_alongside_plan,
    build_erase_plan,
    build_manual_plan,
    plan_checklist,
    plan_warnings,
    preview_segments,
    validate_btrfs_subvolumes,
    validate_plan,
)
from xen.utils import disk_detector
from xen.utils.disk_detector import ESP_TYPE_GUID, LINUX_TYPE_GUID, SWAP_TYPE_GUID

MIB = 1024**2
GIB = 1024**3
FIXTURES = Path(__file__).parent.parent / "fixtures" / "fstools"
DEFAULT_SUBVOLS = [
    {"name": "@", "mountpoint": "/"},
    {"name": "@home", "mountpoint": "/home"},
    {"name": "@log", "mountpoint": "/var/log"},
]
OK = {"uefi": True, "has_internet": True, "min_install_gib": 5}


def _finish(disk: dict[str, Any]) -> dict[str, Any]:
    disk["segments"] = disk_detector.compute_segments(
        disk["sizeSectors"], disk["partitions"], disk["sectorSize"]
    )
    return disk


def _windows_like(**part3: Any) -> dict[str, Any]:
    device = json.loads((FIXTURES / "lsblk_windows_like.json").read_text())["blockdevices"][0]
    disk = disk_detector._build_disk(device)
    disk["partitions"][2].update({"minSizeBytes": 8 * GIB, "osName": "Windows 11", **part3})
    return _finish(disk)


def _part(number: int, start_mib: int, size_mib: int, fstype: str, ss: int, **extra: Any) -> dict:
    align = MIB // ss
    start = start_mib * align
    size = size_mib * align
    caps = fscaps.caps_for(fstype)
    part = {
        "name": f"sdx{number}",
        "path": f"/dev/sdx{number}",
        "disk": "sdx",
        "number": number,
        "startSector": start,
        "endSector": start + size - 1,
        "sizeSectors": size,
        "sizeBytes": size * ss,
        "fstype": fstype,
        "partType": "linux",
        "partTypeGuid": LINUX_TYPE_GUID,
        "partUuid": f"uuid-{number}",
        "partLabel": "",
        "label": "",
        "isEsp": False,
        "mounted": False,
        "bitlocker": fstype == "BitLocker",
        "osName": "",
        "canShrink": caps.can_shrink,
        "canGrow": caps.can_grow,
        "minSizeBytes": 0,
        "resizeBlockedReason": caps.blocked_reason,
    }
    part.update(extra)
    return part


def _disk(parts: list[tuple], size_gib: int = 100, ss: int = 512) -> dict[str, Any]:
    partitions = [
        _part(n, start, size, fs, ss, **(extra[0] if extra else {}))
        for n, start, size, fs, *extra in parts
    ]
    return _finish(
        {
            "name": "sdx",
            "sizeBytes": size_gib * GIB,
            "sizeSectors": size_gib * GIB // ss,
            "sectorSize": ss,
            "ptType": "gpt",
            "partitions": partitions,
        }
    )


ESP = {"isEsp": True, "partTypeGuid": ESP_TYPE_GUID, "partType": "efi"}


def _linux_disk(**part2: Any) -> dict[str, Any]:
    return _disk(
        [
            (1, 1, 512, "vfat", ESP),
            (2, 513, 40 * 1024, "ext4", part2),
            (3, 513 + 40 * 1024, 50 * 1024, "ext4"),
        ]
    )


def _existing(number: int, action: str, fstype: str = "", mountpoint: str = "", **kw: Any) -> dict:
    return {
        "kind": "existing",
        "number": number,
        "action": action,
        "newEnd": None,
        "fstype": fstype,
        "mountpoint": mountpoint,
        "label": "",
        "encrypt": False,
        "subvolumes": [],
        **kw,
    }


def _new(ref: str, start: int, end: int, fstype: str, mountpoint: str, **kw: Any) -> dict:
    return {
        "kind": "new",
        "ref": ref,
        "start": start,
        "end": end,
        "fstype": fstype,
        "mountpoint": mountpoint,
        "label": "",
        "encrypt": False,
        "subvolumes": [],
        **kw,
    }


def _valid_manual(**rows2: Any) -> list[dict]:
    return [
        _existing(1, "keep", mountpoint="/boot/efi"),
        _existing(2, "format", **{"fstype": "ext4", "mountpoint": "/", **rows2}),
        _existing(3, "keep", mountpoint="/home"),
    ]


def _errors(plan: PartitionPlan, disk: dict, **kw: Any) -> list[str]:
    return validate_plan(plan, disk, **{**OK, **kw})


def test_erase_plan_layout() -> None:
    disk = _disk([], size_gib=100)
    plan = build_erase_plan(disk, "btrfs", True, 2000, DEFAULT_SUBVOLS)
    assert plan.new_table and not plan.deletes and not plan.resizes
    esp, root = plan.creates
    assert (esp.start, esp.end, esp.type_guid) == (2048, 2048 + 2000 * 2048 - 1, ESP_TYPE_GUID)
    assert (root.start, root.type_guid) == (esp.end + 1, LINUX_TYPE_GUID)
    assert root.end == disk["sizeSectors"] - 34
    assert esp.name == "EFI system" and root.name == "XeroLinux root"
    esp_t, root_t = plan.targets
    assert (esp_t.ref, esp_t.fstype, esp_t.mountpoint, esp_t.is_esp) == (
        "new:" + esp.ref, "fat32", "/boot/efi", True
    )
    assert (root_t.ref, root_t.fstype, root_t.mountpoint, root_t.encrypt) == (
        "new:" + root.ref, "btrfs", "/", True
    )
    assert root_t.subvolumes == DEFAULT_SUBVOLS and root_t.format and esp_t.format
    assert _errors(plan, disk) == []


def test_erase_plan_4kn_geometry() -> None:
    disk = _disk([(1, 1, 300, "ntfs")], size_gib=100, ss=4096)
    plan = build_erase_plan(disk, "ext4", False, 2000, DEFAULT_SUBVOLS)
    esp, root = plan.creates
    assert plan.sector_size == 4096
    assert (esp.start, esp.end) == (256, 256 + 2000 * 256 - 1)
    # 4096-byte GPT backup: 1 header + 16 KiB of entries (4 sectors)
    assert root.end == disk["sizeSectors"] - 1 - 5
    assert plan.targets[1].subvolumes == []
    assert _errors(plan, disk) == []
    tail = [s for s in disk["segments"] if s["kind"] == "free"][-1]
    assert tail["endSector"] == root.end


def test_alongside_plan_windows_like() -> None:
    disk = _windows_like()
    ntfs = disk["partitions"][2]
    plan = build_alongside_plan(
        disk, 3, keep_size_bytes=10 * GIB + 12345, fstype="ext4", encrypt=False,
        esp_number=1, esp_disk="vda", subvolumes=[],
    )
    (resize,) = plan.resizes
    assert resize == Resize(3, ntfs["startSector"] + 10 * GIB // 512 - 1, "ntfs", True)
    (root,) = plan.creates
    assert root.start == resize.new_end + 1 and root.start % 2048 == 0
    assert root.end == disk["partitions"][3]["startSector"] - 1
    assert root.type_guid == LINUX_TYPE_GUID
    esp = next(t for t in plan.targets if t.mountpoint == "/boot/efi")
    assert (esp.ref, esp.format, esp.is_esp) == ("part:1", False, True)
    assert not plan.new_table and not plan.deletes
    assert _errors(plan, disk) == []


def test_reused_fat16_esp_refused() -> None:
    disk = _windows_like()
    disk["partitions"][0]["fstype"] = "fat16"
    plan = build_alongside_plan(disk, 3, 10 * GIB, "ext4", False, 1, "vda", [])
    assert "The EFI system partition must be FAT32, not fat16." in _errors(plan, disk)


def test_alongside_absorbs_trailing_free_space() -> None:
    disk = _disk([(1, 1, 512, "vfat", ESP), (2, 513, 60 * 1024, "ext4")], size_gib=100)
    plan = build_alongside_plan(disk, 2, 30 * GIB, "btrfs", True, 1, "sdx", DEFAULT_SUBVOLS)
    assert plan.creates[0].end == disk["sizeSectors"] - 34
    assert _errors(plan, disk) == []


def test_alongside_rejects_below_min() -> None:
    disk = _windows_like()
    below_fs_min = build_alongside_plan(disk, 3, 4 * GIB, "ext4", False, 1, "vda", [])
    errors = _errors(below_fs_min, disk)
    assert any("/dev/vda3" in e and "8.0 GiB" in e for e in errors), errors

    too_small_root = build_alongside_plan(disk, 3, 16 * GIB, "ext4", False, 1, "vda", [])
    errors = _errors(too_small_root, disk, min_install_gib=25)
    assert any("25 GiB" in e for e in errors), errors


@pytest.mark.parametrize("fstype", ["xfs", "BitLocker"])
def test_alongside_rejects_xfs_and_bitlocker(fstype: str) -> None:
    blocked = fscaps.caps_for(fstype).blocked_reason
    disk = _windows_like(
        fstype=fstype, canShrink=False, canGrow=fstype == "xfs", resizeBlockedReason=blocked
    )
    plan = build_alongside_plan(disk, 3, 10 * GIB, "ext4", False, 1, "vda", [])
    errors = _errors(plan, disk)
    assert any(blocked in e for e in errors), errors


def test_resize_refused_when_scan_blocked_it_even_for_growable_fs() -> None:
    reason = fscaps.REASON_HIBERNATED
    disk = _windows_like(resizeBlockedReason=reason, canShrink=False, canGrow=True)
    plan = build_alongside_plan(disk, 3, 10 * GIB, "ext4", False, 1, "vda", [])
    assert any(reason in e for e in _errors(plan, disk))


def test_alongside_esp_on_another_disk() -> None:
    disk = _disk([(1, 1, 60 * 1024, "ntfs")], size_gib=100)
    other = _disk([(1, 1, 300, "vfat", ESP)], size_gib=50)
    other["name"] = "nvme0n1"
    plan = build_alongside_plan(disk, 1, 20 * GIB, "ext4", False, 1, "nvme0n1", [])
    esp = next(t for t in plan.targets if t.is_esp)
    assert esp.disk == "nvme0n1"
    assert _errors(plan, disk, other_disks=[other]) == []
    assert _errors(plan, disk)


def test_manual_new_table_plan() -> None:
    disk = _linux_disk()
    rows = [
        _new("r1", 2048, 2048 + 1024 * 2048 - 1, "fat32", "/boot/efi"),
        _new("r2", 1025 * 2048, 61 * 1024 * 2048 - 1, "btrfs", "/", subvolumes=DEFAULT_SUBVOLS),
        _new("r3", 61 * 1024 * 2048, 69 * 1024 * 2048 - 1, "linuxswap", ""),
        _new("r4", 69 * 1024 * 2048, 90 * 1024 * 2048 - 1, "ext4", "/data", label="DATA"),
    ]
    plan = build_manual_plan(disk, rows, new_table=True)
    assert plan.new_table and not plan.deletes
    assert [c.type_guid for c in plan.creates] == [ESP_TYPE_GUID, LINUX_TYPE_GUID, SWAP_TYPE_GUID, LINUX_TYPE_GUID]
    assert [c.name for c in plan.creates] == [
        "EFI system", "XeroLinux root", "XeroLinux swap", "XeroLinux data"
    ]
    swap = plan.targets[2]
    assert (swap.ref, swap.fstype, swap.mountpoint) == ("new:r3", "swap", "swap")
    assert plan.targets[3].label == "DATA"
    assert _errors(plan, disk) == []


def test_manual_new_table_rejects_existing_targets() -> None:
    disk = _linux_disk()
    rows = [
        _existing(3, "keep", mountpoint="/home"),
        _new("r1", 2048, 2048 + 1024 * 2048 - 1, "fat32", "/boot/efi"),
        _new("r2", 1025 * 2048, 61 * 1024 * 2048 - 1, "ext4", "/"),
    ]
    errors = _errors(build_manual_plan(disk, rows, new_table=True), disk)
    assert any("/dev/sdx3" in e for e in errors), errors


def test_manual_keep_home_format_root() -> None:
    disk = _linux_disk()
    plan = build_manual_plan(disk, _valid_manual(), new_table=False)
    by_mount = {t.mountpoint: t for t in plan.targets}
    assert by_mount["/"].format and by_mount["/"].ref == "part:2"
    assert not by_mount["/home"].format and by_mount["/home"].fstype == "ext4"
    assert by_mount["/boot/efi"].is_esp and not by_mount["/boot/efi"].format
    assert not plan.creates and not plan.resizes
    assert _errors(plan, disk) == []


def test_manual_delete_and_create_in_freed_space() -> None:
    disk = _linux_disk()
    p3 = disk["partitions"][2]
    rows = [
        _existing(1, "keep", mountpoint="/boot/efi"),
        _existing(2, "format", "xfs", "/"),
        _existing(3, "delete"),
        _new("r1", p3["startSector"], p3["endSector"], "ext4", "/home"),
    ]
    plan = build_manual_plan(disk, rows, new_table=False)
    assert plan.deletes == [Delete(3)]
    assert _errors(plan, disk) == []


def test_manual_shrink_and_grow_rows() -> None:
    disk = _linux_disk()
    p3 = disk["partitions"][2]
    p3["minSizeBytes"] = 10 * GIB

    def home(new_end: int) -> list[dict]:
        rows = _valid_manual()
        rows[2] = _existing(3, "keep", mountpoint="/home", newEnd=new_end)
        return rows

    rows = home(p3["startSector"] + 20 * GIB // 512 - 1)
    plan = build_manual_plan(disk, rows, new_table=False)
    assert plan.resizes == [Resize(3, rows[2]["newEnd"], "ext4", True)]
    assert _errors(plan, disk) == []

    rows = home(p3["startSector"] + 5 * GIB // 512 - 1)
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert errors == ["/dev/sdx3 can't be shrunk below 10.0 GiB."], errors

    plan = build_manual_plan(disk, home(disk["sizeSectors"] - 34), new_table=False)
    assert plan.resizes == [Resize(3, disk["sizeSectors"] - 34, "ext4", False)]
    assert _errors(plan, disk) == []


def test_resize_fstype_must_match_disk() -> None:
    disk = _linux_disk()
    plan = build_manual_plan(disk, _valid_manual(), new_table=False)
    plan.resizes.append(Resize(3, disk["partitions"][2]["endSector"] - 2048, "ntfs", True))
    assert any("ntfs" in e for e in _errors(plan, disk))


def test_root_keep_rejected() -> None:
    disk = _linux_disk()
    rows = _valid_manual()
    rows[1] = _existing(2, "keep", mountpoint="/")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("root" in e.lower() and "format" in e.lower() for e in errors), errors


@pytest.mark.parametrize("fstype", ["crypto_LUKS", "BitLocker"])
def test_luks_keep_rejected(fstype: str) -> None:
    disk = _disk(
        [
            (1, 1, 512, "vfat", ESP),
            (2, 513, 40 * 1024, "ext4"),
            (3, 513 + 40 * 1024, 50 * 1024, fstype),
        ]
    )
    errors = _errors(build_manual_plan(disk, _valid_manual(), new_table=False), disk)
    assert any("/dev/sdx3" in e for e in errors), errors

    rows = _valid_manual()
    rows[2] = _existing(3, "format", "ext4", "/home")
    assert _errors(build_manual_plan(disk, rows, new_table=False), disk) == []


def test_overlap_rejected() -> None:
    disk = _linux_disk()
    p3 = disk["partitions"][2]
    rows = _valid_manual() + [_new("r1", p3["startSector"] + 2048, p3["endSector"], "ext4", "")]
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("overlap" in e for e in errors), errors

    rows = _valid_manual(newEnd=disk["partitions"][2]["startSector"] + 2048)
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("overlap" in e for e in errors), errors


def test_geometry_bounds_and_alignment() -> None:
    disk = _disk([])
    esp = _new("r1", 2048, 2048 + 1024 * 2048 - 1, "fat32", "/boot/efi")
    tail = disk["sizeSectors"] - 34
    cases = {
        "aligned": _new("r2", 1025 * 2048 + 7, 40 * 1024 * 2048, "ext4", "/"),
        "end of the disk": _new("r2", 1025 * 2048, tail + 1, "ext4", "/"),
        "first usable": _new("r2", 0, 1000, "ext4", "/"),
    }
    for needle, row in cases.items():
        errors = _errors(build_manual_plan(disk, [esp, row], new_table=True), disk)
        assert any(needle in e for e in errors), (needle, errors)
    fill = _new("r2", 1025 * 2048, tail, "ext4", "/")
    assert _errors(build_manual_plan(disk, [esp, fill], new_table=True), disk) == []


def test_btrfs_needs_internet() -> None:
    disk = _disk([])
    plan = build_erase_plan(disk, "btrfs", False, 2000, DEFAULT_SUBVOLS)
    errors = _errors(plan, disk, has_internet=False)
    assert any("internet" in e for e in errors), errors
    assert _errors(build_erase_plan(disk, "ext4", False, 2000, []), disk, has_internet=False) == []


def test_root_rules() -> None:
    disk = _linux_disk()
    rows = [_existing(1, "keep", mountpoint="/boot/efi"), _existing(3, "keep", mountpoint="/home")]
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("root" in e.lower() for e in errors), errors

    rows = _valid_manual()
    rows[2] = _existing(3, "format", "ext4", "/")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("/ is used more than once" in e for e in errors), errors

    rows = _valid_manual()
    rows[1] = _existing(2, "format", "fat32", "/")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("ext4, XFS or Btrfs" in e for e in errors), errors


def test_mountpoint_rules() -> None:
    disk = _linux_disk()
    rows = _valid_manual()
    rows[2] = _existing(3, "keep", mountpoint="home")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert errors == ['The mount point "home" of /dev/sdx3 must start with /.'], errors

    rows = _valid_manual()
    rows[2] = _existing(3, "keep", mountpoint="/boot/efi")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert "/dev/sdx3 is mounted at /boot/efi but isn't an EFI system partition." in errors
    assert "The mount point /boot/efi is used more than once." in errors

    rows = _valid_manual()
    rows[2] = _existing(3, "keep", mountpoint="/boot")
    assert _errors(build_manual_plan(disk, rows, new_table=False), disk) == []


def test_esp_rules() -> None:
    disk = _linux_disk()
    rows = _valid_manual()
    rows[0] = _existing(1, "keep", mountpoint="/mnt/esp")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("/boot/efi" in e for e in errors), errors
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk, uefi=False)
    assert errors == [
        "The EFI system partition /dev/sdx1 must be mounted at /boot/efi, not /mnt/esp."
    ], errors

    rows = _valid_manual()
    rows[0] = _existing(1, "keep")
    rows[2] = _existing(3, "format", "fat32", "/boot/efi")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("EFI" in e for e in errors), errors

    tiny = _disk([(1, 1, 16, "vfat", ESP), (2, 17, 40 * 1024, "ext4")])
    rows = [_existing(1, "keep", mountpoint="/boot/efi"), _existing(2, "format", "ext4", "/")]
    errors = _errors(build_manual_plan(tiny, rows, new_table=False), tiny)
    assert any("32 MiB" in e for e in errors), errors

    rows = _valid_manual()
    rows[0] = _existing(1, "format", "fat32", "/boot/efi", encrypt=True)
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("EFI" in e and "encrypt" in e for e in errors), errors


def test_btrfs_subvolume_never_counts_as_esp() -> None:
    disk = _disk([])
    plan = build_erase_plan(disk, "btrfs", False, 2000, DEFAULT_SUBVOLS)
    plan.creates = plan.creates[1:]
    plan.targets = plan.targets[1:]
    plan.targets[0].subvolumes = [
        {"name": "@", "mountpoint": "/"},
        {"name": "@efi", "mountpoint": "/boot/efi"},
    ]
    errors = _errors(plan, disk)
    assert "No EFI system partition is assigned to /boot/efi." in errors, errors
    assert "Btrfs subvolumes can't be mounted at or under /boot/efi." in errors, errors

    plan = build_erase_plan(disk, "btrfs", False, 2000, DEFAULT_SUBVOLS)
    plan.targets[1].subvolumes.append({"name": "@fw", "mountpoint": "/boot/efi/EFI"})
    errors = _errors(plan, disk)
    assert errors == ["Btrfs subvolumes can't be mounted at or under /boot/efi."], errors


def test_resize_and_format_same_partition_rejected() -> None:
    disk = _linux_disk()
    p2 = disk["partitions"][1]
    rows = _valid_manual(newEnd=p2["startSector"] + 20 * GIB // 512 - 1)
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert errors == [
        "Resize or format, not both: delete it and create a new one instead."
    ], errors


@pytest.mark.parametrize("pt_type", ["dos", ""])
def test_non_gpt_disk_needs_new_table(pt_type: str) -> None:
    disk = _linux_disk()
    disk["ptType"] = pt_type
    errors = _errors(build_manual_plan(disk, _valid_manual(), new_table=False), disk)
    assert errors == [
        "This disk doesn't use a GPT partition table: use Erase or New Partition Table."
    ], errors
    assert _errors(build_erase_plan(disk, "ext4", False, 2000, []), disk) == []


def test_mounted_partition_rejected() -> None:
    disk = _linux_disk(mounted=True)
    errors = _errors(build_manual_plan(disk, _valid_manual(), new_table=False), disk)
    assert any("/dev/sdx2" in e and "in use" in e for e in errors), errors


def test_encryption_rules() -> None:
    disk = _linux_disk()
    rows = _valid_manual()
    rows[2] = _existing(3, "format", "ext4", "/home", encrypt=True)
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("root" in e and "encrypt" in e for e in errors), errors

    rows = _valid_manual(encrypt=True)
    rows[2] = _existing(3, "format", "ext4", "/home", encrypt=True)
    assert _errors(build_manual_plan(disk, rows, new_table=False), disk) == []

    rows = _valid_manual()
    rows[2] = _existing(3, "format", "swap", "swap", encrypt=True)
    assert _errors(build_manual_plan(disk, rows, new_table=False), disk) == []

    rows = _valid_manual()
    rows[2] = _existing(3, "keep", mountpoint="/home", encrypt=True)
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("format" in e for e in errors), errors


def test_subvolume_rules() -> None:
    disk = _linux_disk()
    rows = _valid_manual(subvolumes=DEFAULT_SUBVOLS)
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("Btrfs" in e for e in errors), errors

    rows = _valid_manual(fstype="btrfs", subvolumes=[{"name": "@home", "mountpoint": "/home"}])
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("root" in e.lower() for e in errors), errors

    rows = _valid_manual(fstype="btrfs", subvolumes=DEFAULT_SUBVOLS)
    rows[2] = _existing(3, "keep", mountpoint="/data")
    assert _errors(build_manual_plan(disk, rows, new_table=False), disk) == []


def test_unsupported_format_fstype_rejected() -> None:
    disk = _linux_disk()
    rows = _valid_manual()
    rows[2] = _existing(3, "format", "ntfs", "/data")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert any("ntfs" in e for e in errors), errors


def test_checklist_and_warnings() -> None:
    disk = _disk([(1, 1, 64, "vfat", ESP), (2, 65, 40 * 1024, "ext4")])
    rows = [_existing(1, "keep", mountpoint="/boot/efi"), _existing(2, "format", "ext4", "/")]
    plan = build_manual_plan(disk, rows, new_table=False)
    assert _errors(plan, disk) == []
    assert plan_checklist(plan, disk) == [
        {"text": "Root (/) assigned", "ok": True},
        {"text": "EFI partition at /boot/efi", "ok": True},
    ]
    warnings = plan_warnings(plan, disk)
    assert len(warnings) == 2
    assert any("100 MiB" in w for w in warnings) and any("swap" in w for w in warnings)

    empty = build_manual_plan(disk, [], new_table=False)
    assert [c["ok"] for c in plan_checklist(empty, disk)] == [False, False]

    big = _linux_disk()
    rows = _valid_manual()
    rows[2] = _existing(3, "format", "swap", "swap")
    assert plan_warnings(build_manual_plan(big, rows, new_table=False), big) == []


def test_preview_segments_alongside() -> None:
    disk = _windows_like()
    plan = build_alongside_plan(disk, 3, 10 * GIB, "ext4", True, 1, "vda", [])
    segs = preview_segments(plan, disk)
    kinds = [(s["kind"], s.get("number"), s["newRef"], s["pending"]) for s in segs]
    assert kinds == [
        ("partition", 1, "", False),
        ("partition", 2, "", False),
        ("partition", 3, "", True),
        ("partition", 0, "root", True),
        ("partition", 4, "", False),
        ("free", None, "", False),
    ]
    ntfs, root = segs[2], segs[3]
    assert ntfs["endSector"] == plan.resizes[0].new_end
    assert ntfs["sizeBytes"] == 10 * GIB
    assert root["sizeBytes"] == root["sizeSectors"] * 512
    assert (root["fstype"], root["mountpoint"], root["encrypt"]) == ("ext4", "/", True)
    assert segs[0]["mountpoint"] == "/boot/efi" and not segs[0]["format"]


def test_preview_segments_manual_delete_and_new_table() -> None:
    disk = _linux_disk()
    rows = [
        _existing(1, "keep", mountpoint="/boot/efi"),
        _existing(2, "format", "btrfs", "/"),
        _existing(3, "delete"),
    ]
    segs = preview_segments(build_manual_plan(disk, rows, new_table=False), disk)
    deleted = [s for s in segs if s.get("pendingDelete")]
    assert [s["number"] for s in deleted] == [3]
    assert segs[1]["fstype"] == "btrfs" and segs[1]["pending"]
    live = [s for s in segs if not s.get("pendingDelete")]
    assert live[-1]["kind"] == "free"
    assert live[-1]["startSector"] == disk["partitions"][2]["startSector"]

    segs = preview_segments(build_manual_plan(disk, [], new_table=True), disk)
    assert [s["kind"] for s in segs if not s.get("pendingDelete")] == ["free"]


def test_plan_dict_is_json_serializable() -> None:
    disk = _windows_like()
    plan = build_alongside_plan(disk, 3, 10 * GIB, "btrfs", True, 1, "vda", DEFAULT_SUBVOLS)
    plan.deletes.append(Delete(9))
    data = plan.to_dict()
    assert json.loads(json.dumps(data)) == data


class TestValidateBtrfsSubvolumes:
    def test_accepts_well_formed_plan(self) -> None:
        assert validate_btrfs_subvolumes(DEFAULT_SUBVOLS) == (True, "")

    def test_accepts_root_mountpoint_as_empty_string(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": ""}, {"name": "@home", "mountpoint": "/home"}]
        )
        assert ok is True, err

    def test_rejects_empty_list(self) -> None:
        ok, err = validate_btrfs_subvolumes([])
        assert ok is False
        assert "subvolume" in err.lower()

    def test_rejects_missing_root_subvolume(self) -> None:
        ok, err = validate_btrfs_subvolumes([{"name": "@home", "mountpoint": "/home"}])
        assert ok is False
        assert "root" in err.lower()

    def test_rejects_two_root_subvolumes(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@root2", "mountpoint": "/"}]
        )
        assert ok is False
        assert "root" in err.lower()

    def test_rejects_duplicate_names(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@", "mountpoint": "/home"}]
        )
        assert ok is False
        assert "duplicate" in err.lower()

    def test_rejects_duplicate_mountpoints(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [
                {"name": "@", "mountpoint": "/"},
                {"name": "@home", "mountpoint": "/data"},
                {"name": "@home2", "mountpoint": "/data"},
            ]
        )
        assert ok is False
        assert "duplicate" in err.lower()

    def test_rejects_name_without_at_prefix(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "root", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}]
        )
        assert ok is False
        assert "@" in err

    def test_rejects_name_with_illegal_characters(self) -> None:
        ok, _ = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@ho/me", "mountpoint": "/home"}]
        )
        assert ok is False

    def test_rejects_non_root_mountpoint_without_leading_slash(self) -> None:
        ok, err = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "home"}]
        )
        assert ok is False
        assert "/" in err

    def test_rejects_empty_name(self) -> None:
        ok, _ = validate_btrfs_subvolumes(
            [{"name": "@", "mountpoint": "/"}, {"name": "", "mountpoint": "/home"}]
        )
        assert ok is False


SMALL_NEW_ESP = "The EFI partition is smaller than the recommended 2000 MiB."


@pytest.mark.parametrize(("esp_mib", "warned"), [(512, True), (1999, True), (2000, False)])
def test_new_esp_below_recommended_size_warns(esp_mib: int, warned: bool) -> None:
    disk = _disk([], size_gib=100)
    plan = build_erase_plan(disk, "ext4", False, esp_mib, [])
    assert (SMALL_NEW_ESP in plan_warnings(plan, disk)) is warned


def test_reused_small_esp_keeps_only_the_reuse_warning() -> None:
    disk = _disk([(1, 1, 64, "vfat", ESP), (2, 65, 40 * 1024, "ext4")])
    rows = [_existing(1, "keep", mountpoint="/boot/efi"), _existing(2, "format", "ext4", "/")]
    warnings = plan_warnings(build_manual_plan(disk, rows, new_table=False), disk)
    assert SMALL_NEW_ESP not in warnings
    assert any("100 MiB" in w for w in warnings)


UNENCRYPTED_SWAP = (
    "Swap is not encrypted while / is: hibernation would write the encryption key "
    "to disk unencrypted."
)


@pytest.mark.parametrize(
    ("root_encrypted", "swap_encrypted", "warned"),
    [(True, False, True), (True, True, False), (False, False, False)],
)
def test_unencrypted_swap_with_encrypted_root_warns(
    root_encrypted: bool, swap_encrypted: bool, warned: bool
) -> None:
    disk = _disk([], size_gib=100)
    rows = [
        {"kind": "new", "ref": "esp", "start": 2048, "end": 2048 + 2000 * 2048 - 1,
         "fstype": "fat32", "mountpoint": "/boot/efi"},
        {"kind": "new", "ref": "root", "start": 2002 * 2048, "end": 52 * 1024 * 2048 - 1,
         "fstype": "ext4", "mountpoint": "/", "encrypt": root_encrypted},
        {"kind": "new", "ref": "swap", "start": 52 * 1024 * 2048, "end": 54 * 1024 * 2048 - 1,
         "fstype": "swap", "mountpoint": "swap", "encrypt": swap_encrypted},
    ]  # fmt: skip
    plan = build_manual_plan(disk, rows, new_table=True)
    assert _errors(plan, disk) == []
    assert (UNENCRYPTED_SWAP in plan_warnings(plan, disk)) is warned


NO_SWAP = "There is no swap partition. Hibernation won't be available."


def test_missing_swap_partition_warning_is_optional() -> None:
    disk = _disk([], size_gib=100)
    plan = build_erase_plan(disk, "ext4", False, 2000, [])
    assert plan_warnings(plan, disk) == [NO_SWAP]
    assert plan_warnings(plan, disk, expect_swap_partition=False) == []


def _home_shrunk_to(disk: dict, size_bytes: int) -> list[dict]:
    p3 = disk["partitions"][2]
    rows = _valid_manual()
    new_end = p3["startSector"] + size_bytes // 512 - 1
    rows[2] = _existing(3, "keep", mountpoint="/home", newEnd=new_end)
    return rows


@pytest.mark.parametrize("min_bytes", [10 * GIB, 10 * GIB + 12345, 3 * MIB + 1])
def test_manual_shrink_minimum_includes_the_preflight_margin(min_bytes: int) -> None:
    disk = _linux_disk()
    disk["partitions"][2]["minSizeBytes"] = min_bytes
    floor = fscaps.min_shrink_bytes(min_bytes)
    assert floor >= min_bytes + fscaps.SHRINK_MARGIN and floor % MIB == 0
    at_floor = build_manual_plan(disk, _home_shrunk_to(disk, floor), new_table=False)
    assert _errors(at_floor, disk) == []
    below = build_manual_plan(disk, _home_shrunk_to(disk, floor - MIB), new_table=False)
    assert any("can't be shrunk below" in e for e in _errors(below, disk))


ENCRYPTED_BOOT = (
    "A separate /boot can't be encrypted. Leave /boot unencrypted, "
    "or remove it so /boot lives on the encrypted root."
)


def test_encrypted_separate_boot_rejected() -> None:
    disk = _linux_disk()
    rows = _valid_manual(encrypt=True)
    rows[2] = _existing(3, "format", "ext4", "/boot", encrypt=True)
    assert ENCRYPTED_BOOT in _errors(build_manual_plan(disk, rows, new_table=False), disk)
    rows[2] = _existing(3, "format", "ext4", "/boot")
    assert _errors(build_manual_plan(disk, rows, new_table=False), disk) == []


def test_btrfs_subvolume_dropped_when_its_mount_point_is_a_separate_partition() -> None:
    disk = _linux_disk()
    rows = _valid_manual(fstype="btrfs", subvolumes=DEFAULT_SUBVOLS)
    plan = build_manual_plan(disk, rows, new_table=False)
    root = next(t for t in plan.targets if t.mountpoint == "/")
    assert [sv["name"] for sv in root.subvolumes] == ["@", "@log"]
    assert _errors(plan, disk) == []
    assert "@home skipped: /home is a separate partition." in plan_warnings(plan, disk)


@pytest.mark.parametrize(
    "reason",
    [fscaps.REASON_HIBERNATED, fscaps.REASON_UNCLEAN],
)
def test_kept_ntfs_with_mount_point_refused_when_windows_is_not_shut_down(reason: str) -> None:
    disk = _linux_disk()
    disk["partitions"][2].update(
        fstype="ntfs", canShrink=False, resizeBlockedReason=reason
    )
    rows = _valid_manual()
    rows[2] = _existing(3, "keep", mountpoint="/data")
    errors = _errors(build_manual_plan(disk, rows, new_table=False), disk)
    assert f"/dev/sdx3 can't be mounted: {reason}." in errors, errors
    rows[2] = _existing(3, "keep")
    assert _errors(build_manual_plan(disk, rows, new_table=False), disk) == []

"""Deterministic fake disks in the scanner's UI contract, for tests only."""

from __future__ import annotations

from typing import Any

from xen.utils import disk_detector
from xen.utils.disk_detector import ESP_TYPE_GUID, LINUX_TYPE_GUID

MSR_TYPE_GUID = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
BASIC_DATA_TYPE_GUID = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
RECOVERY_TYPE_GUID = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"
SECTOR_SIZE = 512
ALIGN = 2048


def finalize_mock_disk(disk: dict[str, Any]) -> dict[str, Any]:
    """Lay mock partitions out sequentially and attach geometry + segments."""
    disk_sectors = int(disk["sizeBytes"]) // SECTOR_SIZE
    cursor = ALIGN
    parts = []
    for number, mock in enumerate(disk["partitions"], start=1):
        part = {
            "path": disk_detector._fallback_partition_path(disk["name"], number),
            "disk": disk["name"],
            "number": number,
            "partTypeGuid": LINUX_TYPE_GUID,
            "partUuid": "",
            "partLabel": "",
            "label": "",
            "mounted": False,
            "bitlocker": False,
            "osName": "",
            **disk_detector._resize_fields(mock["fstype"], False),
            **mock,
        }
        part["isEsp"] = part["partTypeGuid"] == ESP_TYPE_GUID
        size_sectors = int(part["sizeBytes"]) // SECTOR_SIZE
        part["startSector"] = cursor
        part["sizeSectors"] = size_sectors
        part["endSector"] = cursor + size_sectors - 1
        cursor += size_sectors
        parts.append(part)
    disk["partitions"] = parts
    disk["sizeSectors"] = disk_sectors
    disk["sectorSize"] = SECTOR_SIZE
    disk["ptType"] = "gpt"
    disk["segments"] = disk_detector.compute_segments(disk_sectors, disk["partitions"])
    return disk


def mock_disks() -> list[dict[str, Any]]:
    """Return a deterministic mock disk list using the shared UI contract."""
    _disks = [
        {
            "name": "sda",
            "model": "Mock SSD 500G",
            "size": disk_detector._format_size(500 * 1024**3),
            "sizeBytes": 500 * 1024**3,
            "type": "SSD",
            "transport": "sata",
            "removable": False,
            "partitions": [
                {
                    "name": "sda1",
                    "sizeBytes": 512 * 1024**2,
                    "fstype": "vfat",
                    "partType": "efi",
                    "partTypeGuid": ESP_TYPE_GUID,
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
            "size": disk_detector._format_size(1024**4),
            "sizeBytes": 1024**4,
            "type": "SSD",
            "transport": "nvme",
            "removable": False,
            "partitions": [],
        },
        {
            "name": "nvme1n1",
            "model": "Mock NVMe Windows 512G",
            "size": disk_detector._format_size(512 * 1024**3),
            "sizeBytes": 512 * 1024**3,
            "type": "SSD",
            "transport": "nvme",
            "removable": False,
            "partitions": [
                {
                    "name": "nvme1n1p1",
                    "sizeBytes": 100 * 1024**2,
                    "fstype": "vfat",
                    "partType": "efi",
                    "partTypeGuid": ESP_TYPE_GUID,
                    "partLabel": "EFI system partition",
                    "label": "SYSTEM",
                },
                {
                    "name": "nvme1n1p2",
                    "sizeBytes": 16 * 1024**2,
                    "fstype": "",
                    "partType": "windows",
                    "partTypeGuid": MSR_TYPE_GUID,
                    "partLabel": "Microsoft reserved partition",
                },
                {
                    "name": "nvme1n1p3",
                    "sizeBytes": 510 * 1024**3,
                    "fstype": "ntfs",
                    "partType": "windows",
                    "partTypeGuid": BASIC_DATA_TYPE_GUID,
                    "partLabel": "Basic data partition",
                    "label": "Windows",
                    "osName": "Windows 11",
                    "minSizeBytes": 96 * 1024**3,
                },
                {
                    "name": "nvme1n1p4",
                    "sizeBytes": 800 * 1024**2,
                    "fstype": "ntfs",
                    "partType": "windows",
                    "partTypeGuid": RECOVERY_TYPE_GUID,
                    "label": "Recovery",
                    "osName": "Windows 11",
                    "minSizeBytes": 610 * 1024**2,
                },
            ],
        },
    ]
    return [finalize_mock_disk(d) for d in _disks]

"""Unit tests for the unified disk_detector module."""

import copy
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from tests.mock_disks import ALIGN, mock_disks
    from xen.utils import disk_detector

    HAS_DETECTOR = True
except ImportError:
    HAS_DETECTOR = False

pytestmark = pytest.mark.skipif(not HAS_DETECTOR, reason="disk_detector not available")


def _lsblk_result(payload: dict[str, object]) -> MagicMock:
    return MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")


def _findmnt_result(source: str, returncode: int = 0) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=source + "\n", stderr="")


def _make_run(lsblk_payload: dict[str, object], live_source: str) -> MagicMock:
    """Build a subprocess.run side_effect dispatching on the binary name."""

    def _side_effect(cmd: list[str], **_kwargs: object) -> MagicMock:
        if cmd[0] == "lsblk":
            return _lsblk_result(lsblk_payload)
        if cmd[0] == "findmnt":
            return _findmnt_result(live_source)
        return MagicMock(returncode=0, stdout="", stderr="")

    return MagicMock(side_effect=_side_effect)


class TestCoerceBool:
    """Tests for the robust bool coercion helper."""

    def test_truthy_forms(self) -> None:
        assert disk_detector._coerce_bool(1) is True
        assert disk_detector._coerce_bool("1") is True
        assert disk_detector._coerce_bool(True) is True

    def test_falsy_forms(self) -> None:
        assert disk_detector._coerce_bool(0) is False
        assert disk_detector._coerce_bool("0") is False
        assert disk_detector._coerce_bool(False) is False
        assert disk_detector._coerce_bool(None) is False


class TestClassifyPartType:
    """Tests for partType deduction rules."""

    def test_efi_from_vfat_and_parttypename(self) -> None:
        assert disk_detector._classify_part_type("vfat", "EFI System") == "efi"

    def test_vfat_without_efi_is_other(self) -> None:
        assert disk_detector._classify_part_type("vfat", "") == "other"

    def test_windows_from_ntfs(self) -> None:
        assert disk_detector._classify_part_type("ntfs", "") == "windows"

    def test_windows_from_parttypename(self) -> None:
        assert disk_detector._classify_part_type("", "Microsoft basic data") == "windows"

    def test_swap(self) -> None:
        assert disk_detector._classify_part_type("swap", "") == "swap"
        assert disk_detector._classify_part_type("", "Linux swap") == "swap"

    def test_linux_filesystems(self) -> None:
        assert disk_detector._classify_part_type("ext4", "") == "linux"
        assert disk_detector._classify_part_type("btrfs", "") == "linux"
        assert disk_detector._classify_part_type("xfs", "") == "linux"

    def test_other(self) -> None:
        assert disk_detector._classify_part_type("", "") == "other"


class TestListDisks:
    """Tests for list_disks() parsing and contract."""

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_parses_disk_and_partitions(self, mock_run: MagicMock) -> None:
        payload = {
            "blockdevices": [
                {
                    "name": "nvme0n1",
                    "size": 500 * 1024**3,
                    "type": "disk",
                    "model": "  WD Black  ",
                    "rm": False,
                    "hotplug": False,
                    "rota": False,
                    "children": [
                        {
                            "name": "nvme0n1p1",
                            "size": 512 * 1024**2,
                            "type": "part",
                            "fstype": "vfat",
                            "parttypename": "EFI System",
                        },
                        {
                            "name": "nvme0n1p2",
                            "size": 499 * 1024**3,
                            "type": "part",
                            "fstype": "ext4",
                            "parttypename": "Linux filesystem",
                        },
                    ],
                }
            ]
        }
        # Live root is an overlay -> exclude nothing.
        mock_run.side_effect = _make_run(payload, "overlay").side_effect

        disks = disk_detector.list_disks()

        assert len(disks) == 1
        disk = disks[0]
        assert disk["name"] == "nvme0n1"
        assert disk["model"] == "WD Black"  # stripped
        assert disk["sizeBytes"] == 500 * 1024**3
        assert disk["size"] == "500.0 GB"
        assert disk["type"] == "SSD"  # rota False
        assert disk["removable"] is False
        assert len(disk["partitions"]) == 2
        assert disk["partitions"][0]["partType"] == "efi"
        assert disk["partitions"][1]["partType"] == "linux"

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_hdd_and_removable_flags(self, mock_run: MagicMock) -> None:
        payload = {
            "blockdevices": [
                {
                    "name": "sdb",
                    "size": 64 * 1024**3,
                    "type": "disk",
                    "model": "USB Stick",
                    "rm": True,
                    "hotplug": True,
                    "rota": True,
                    "tran": "USB",
                }
            ]
        }
        mock_run.side_effect = _make_run(payload, "overlay").side_effect

        disks = disk_detector.list_disks()
        assert len(disks) == 1
        assert disks[0]["type"] == "HDD"  # rota True
        assert disks[0]["removable"] is True
        assert disks[0]["transport"] == "usb"

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_filters_non_disk_devices(self, mock_run: MagicMock) -> None:
        payload = {
            "blockdevices": [
                {"name": "loop0", "size": 1024, "type": "loop"},
                {"name": "sr0", "size": 1024, "type": "rom"},
                {
                    "name": "sda",
                    "size": 256 * 1024**3,
                    "type": "disk",
                    "rota": False,
                },
            ]
        }
        mock_run.side_effect = _make_run(payload, "overlay").side_effect

        disks = disk_detector.list_disks()
        assert [d["name"] for d in disks] == ["sda"]

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_excludes_virtual_disks_by_name(self, mock_run: MagicMock) -> None:
        # zram/loop/ram/etc. can be reported by lsblk as type="disk"; they must
        # never be offered as installation targets.
        payload = {
            "blockdevices": [
                {"name": "zram0", "size": 8 * 1024**3, "type": "disk", "rota": False},
                {"name": "loop1", "size": 1024, "type": "disk", "rota": False},
                {"name": "sda", "size": 256 * 1024**3, "type": "disk", "rota": False},
            ]
        }
        mock_run.side_effect = _make_run(payload, "overlay").side_effect

        disks = disk_detector.list_disks()
        assert [d["name"] for d in disks] == ["sda"]

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_excludes_live_disk(self, mock_run: MagicMock) -> None:
        payload = {
            "blockdevices": [
                {
                    "name": "sda",
                    "size": 256 * 1024**3,
                    "type": "disk",
                    "rota": False,
                    "children": [
                        {"name": "sda1", "size": 512 * 1024**2, "type": "part"},
                        {"name": "sda2", "size": 255 * 1024**3, "type": "part"},
                    ],
                },
                {
                    "name": "nvme0n1",
                    "size": 1024**4,
                    "type": "disk",
                    "rota": False,
                    "children": [],
                },
            ]
        }
        # Live root sits on /dev/sda2 -> sda excluded, nvme0n1 kept.
        mock_run.side_effect = _make_run(payload, "/dev/sda2").side_effect

        disks = disk_detector.list_disks()
        names = [d["name"] for d in disks]
        assert "sda" not in names
        assert "nvme0n1" in names

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_single_disk_that_is_live_disk_is_not_excluded(self, mock_run: MagicMock) -> None:
        """Excluding the only disk would leave an empty, dead-end list - show it instead."""
        payload = {
            "blockdevices": [
                {
                    "name": "nvme0n1",
                    "size": 512 * 1024**3,
                    "type": "disk",
                    "rota": False,
                    "children": [{"name": "nvme0n1p2", "size": 500 * 1024**3, "type": "part"}],
                }
            ]
        }
        mock_run.side_effect = _make_run(payload, "/dev/nvme0n1p2").side_effect

        disks = disk_detector.list_disks()
        names = [d["name"] for d in disks]
        assert "nvme0n1" in names

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_non_block_live_source_excludes_nothing(self, mock_run: MagicMock) -> None:
        payload = {
            "blockdevices": [
                {
                    "name": "sda",
                    "size": 256 * 1024**3,
                    "type": "disk",
                    "rota": False,
                    "children": [{"name": "sda2", "size": 1024, "type": "part"}],
                }
            ]
        }
        # airootfs (live ISO) is not a block device.
        mock_run.side_effect = _make_run(payload, "airootfs").side_effect

        disks = disk_detector.list_disks()
        assert [d["name"] for d in disks] == ["sda"]

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_lsblk_missing_returns_empty_list(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError()
        assert disk_detector.list_disks() == []

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_bad_json_returns_empty_list(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        assert disk_detector.list_disks() == []

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_nonzero_exit_returns_empty_list(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        assert disk_detector.list_disks() == []

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_lsblk_timeout_returns_empty_list(self, mock_run: MagicMock) -> None:
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(["lsblk"], 10)
        assert disk_detector.list_disks() == []

    def test_scan_never_offers_fake_disks(self) -> None:
        assert "mock_on_failure" not in inspect.signature(disk_detector.list_disks).parameters
        assert not hasattr(disk_detector, "_mock_disks")

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_subprocess_calls_have_timeouts(self, mock_run: MagicMock) -> None:
        payload = {"blockdevices": [{"name": "sda", "type": "disk", "size": 10 * 1024**3}]}
        mock_run.side_effect = _make_run(payload, "overlay").side_effect
        disk_detector.list_disks()
        assert mock_run.call_count == 2
        for call in mock_run.call_args_list:
            assert call.kwargs.get("timeout")

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_requests_logical_sector_size(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _make_run({"blockdevices": []}, "overlay").side_effect
        disk_detector.list_disks()
        lsblk_cmd = mock_run.call_args_list[0].args[0]
        assert "LOG-SEC" in lsblk_cmd[lsblk_cmd.index("-o") + 1].split(",")

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_4kn_disk_geometry_uses_logical_sectors(self, mock_run: MagicMock) -> None:
        # lsblk START is always in 512-byte units; parted's "s" unit is the
        # disk's logical sector, so a 4Kn disk must be converted.
        size = 100 * 1024**3
        part_size = 512 * 1024**2
        payload = {
            "blockdevices": [
                {
                    "name": "sda",
                    "type": "disk",
                    "size": size,
                    "log-sec": 4096,
                    "children": [
                        {
                            "name": "sda1",
                            "type": "part",
                            "size": part_size,
                            "start": 2048,
                            "fstype": "vfat",
                            "parttypename": "EFI System",
                        }
                    ],
                }
            ]
        }
        mock_run.side_effect = _make_run(payload, "overlay").side_effect

        disk = disk_detector.list_disks()[0]
        assert disk["sectorSize"] == 4096
        assert disk["sizeSectors"] == size // 4096
        part = disk["partitions"][0]
        assert part["startSector"] == 256
        assert part["sizeSectors"] == part_size // 4096
        assert part["endSector"] == 256 + part_size // 4096 - 1
        free = [s for s in disk["segments"] if s["kind"] == "free"]
        assert free and free[-1]["sizeBytes"] == free[-1]["sizeSectors"] * 4096


class TestComputeSegments:
    """Tests for free-space interleaving across the whole disk."""

    @staticmethod
    def _part(name: str, start: int, sectors: int) -> dict[str, object]:
        return {
            "name": name,
            "partType": "linux",
            "fstype": "ext4",
            "startSector": start,
            "sizeSectors": sectors,
            "endSector": start + sectors - 1,
            "sizeBytes": sectors * disk_detector._SECTOR_SIZE,
        }

    def test_empty_disk_is_single_free_segment(self) -> None:
        disk_sectors = 40 * 1024**3 // disk_detector._SECTOR_SIZE
        segs = disk_detector.compute_segments(disk_sectors, [])
        assert [s["kind"] for s in segs] == ["free"]
        assert segs[0]["startSector"] == ALIGN
        assert segs[0]["endSector"] == disk_sectors - disk_detector._GPT_TAIL

    def test_free_between_and_after_partitions(self) -> None:
        disk_sectors = 1_000_000
        p1 = self._part("p1", ALIGN, 100_000)  # ends 102047
        p2 = self._part("p2", 300_000, 100_000)  # ends 399999
        segs = disk_detector.compute_segments(disk_sectors, [p1, p2])

        assert [s["kind"] for s in segs] == ["partition", "free", "partition", "free"]
        gap = segs[1]
        assert (gap["startSector"], gap["endSector"]) == (102_048, 299_999)
        tail = segs[3]
        assert tail["startSector"] == 400_000
        assert tail["endSector"] == disk_sectors - disk_detector._GPT_TAIL

    def test_leading_free_before_first_partition(self) -> None:
        disk_sectors = 1_000_000
        p1 = self._part("p1", 500_000, 100_000)
        segs = disk_detector.compute_segments(disk_sectors, [p1])

        assert [s["kind"] for s in segs] == ["free", "partition", "free"]
        assert segs[0]["startSector"] == ALIGN
        assert segs[0]["endSector"] == 499_999

    def test_subalignment_gap_is_dropped(self) -> None:
        disk_sectors = 1_000_000
        p1 = self._part("p1", ALIGN, 100_000)  # ends 102047
        # Next partition sits only 100 sectors later: a sub-1MiB gap, unusable.
        p2 = self._part("p2", 102_148, 100_000)
        segs = disk_detector.compute_segments(disk_sectors, [p1, p2])

        # The tiny gap between p1 and p2 is omitted; only the trailing free remains.
        assert [s["kind"] for s in segs] == ["partition", "partition", "free"]

    def test_ordering_is_by_start_sector(self) -> None:
        disk_sectors = 1_000_000
        # Provide partitions out of order; segments must come back sorted.
        p_late = self._part("p2", 400_000, 50_000)
        p_early = self._part("p1", ALIGN, 50_000)
        segs = disk_detector.compute_segments(disk_sectors, [p_late, p_early])

        part_names = [s["name"] for s in segs if s["kind"] == "partition"]
        assert part_names == ["p1", "p2"]


FIXTURES = Path(__file__).parent.parent / "fixtures" / "fstools"
ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"


def _windows_like() -> dict[str, object]:
    return json.loads((FIXTURES / "lsblk_windows_like.json").read_text())


class TestPartitionPath:
    _ROWS = {
        "blockdevices": [
            {"path": "/dev/nvme0n1", "pkname": None, "partn": None},
            {"path": "/dev/nvme0n1p1", "pkname": "nvme0n1", "partn": 1},
            {"path": "/dev/nvme0n1p11", "pkname": "nvme0n1", "partn": 11},
            {"path": "/dev/sda", "pkname": None, "partn": None},
            {"path": "/dev/sda11", "pkname": "sda", "partn": 11},
            {"path": "/dev/sda1", "pkname": "sda", "partn": 1},
            {"path": "/dev/mapper/odd", "pkname": "vdz", "partn": 3},
        ]
    }

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_partition_path_nvme_and_sda(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _lsblk_result(self._ROWS)
        assert disk_detector.partition_path("nvme0n1", 11) == "/dev/nvme0n1p11"
        assert disk_detector.partition_path("/dev/nvme0n1", 1) == "/dev/nvme0n1p1"
        assert disk_detector.partition_path("/dev/sda", 1) == "/dev/sda1"
        assert disk_detector.partition_path("sda", 11) == "/dev/sda11"
        # only PKNAME + PARTN can produce this path, never a name rule
        assert disk_detector.partition_path("/dev/vdz", 3) == "/dev/mapper/odd"
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "lsblk" and "PKNAME" in cmd[cmd.index("-o") + 1]
        assert mock_run.call_args.kwargs.get("timeout")

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_partition_path_fallback_when_lsblk_unknown(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError()
        assert disk_detector.partition_path("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
        assert disk_detector.partition_path("/dev/sda", 2) == "/dev/sda2"
        assert disk_detector.partition_path("mmcblk0", 1) == "/dev/mmcblk0p1"


class TestScanContract:
    @patch("xen.utils.disk_detector.subprocess.run")
    def test_lsblk_requests_identity_columns(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _make_run({"blockdevices": []}, "overlay").side_effect
        disk_detector.list_disks()
        cmd = mock_run.call_args_list[0].args[0]
        columns = set(cmd[cmd.index("-o") + 1].split(","))
        expected = "PATH,PKNAME,PARTN,PARTTYPE,PARTUUID,PARTLABEL,LABEL,UUID,MOUNTPOINTS"
        assert set(expected.split(",")) <= columns

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_esp_detection_by_type_guid(self, mock_run: MagicMock) -> None:
        payload = _windows_like()
        # a vfat ESP whose type name lacks "EFI" must still be found by its GUID
        payload["blockdevices"][0]["children"][0]["parttypename"] = "something"
        payload["blockdevices"][0]["children"][1]["parttypename"] = "EFI-looking name"
        mock_run.side_effect = _make_run(payload, "overlay").side_effect
        parts = disk_detector.list_disks()[0]["partitions"]
        assert [p["isEsp"] for p in parts] == [True, False, False, False]
        esp, _msr, win, _rec = parts
        assert esp["partTypeGuid"] == ESP_GUID
        assert (win["path"], win["number"], win["label"], win["partLabel"]) == (
            "/dev/vda3",
            3,
            "Windows",
            "Windows",
        )
        assert win["partUuid"] == "f4913707-1776-4220-83d7-a5e3627810bd"
        assert win["disk"] == "vda"
        assert win["mounted"] is False
        assert win["osName"] == ""
        assert (win["canShrink"], win["canGrow"], win["minSizeBytes"]) == (True, True, 0)
        assert win["resizeBlockedReason"] == ""
        assert esp["canShrink"] is False
        assert esp["resizeBlockedReason"] == "FAT partitions can't be resized here"

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_partition_table_type(self, mock_run: MagicMock) -> None:
        payload = _windows_like()
        blank = {"name": "sdb", "type": "disk", "size": 10 * 1024**3, "pttype": None}
        payload["blockdevices"].append(blank)
        mock_run.side_effect = _make_run(payload, "overlay").side_effect
        disks = disk_detector.list_disks()
        assert [d["ptType"] for d in disks] == ["gpt", ""]
        cmd = mock_run.call_args_list[0].args[0]
        assert "PTTYPE" in cmd[cmd.index("-o") + 1].split(",")
        assert {d["ptType"] for d in mock_disks()} == {"gpt"}

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_bitlocker_reason(self, mock_run: MagicMock) -> None:
        payload = _windows_like()
        payload["blockdevices"][0]["children"][2]["fstype"] = "BitLocker"
        mock_run.side_effect = _make_run(payload, "overlay").side_effect
        win = disk_detector.list_disks()[0]["partitions"][2]
        assert win["bitlocker"] is True
        assert (win["canShrink"], win["canGrow"]) == (False, False)
        assert win["resizeBlockedReason"] == "BitLocker is on: turn it off in Windows first"

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_scan_without_probe_runs_no_probe(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _make_run(_windows_like(), "overlay").side_effect
        with patch("xen.utils.disk_detector.probe") as mock_probe:
            disk_detector.list_disks()
        assert not mock_probe.method_calls
        assert [c.args[0][0] for c in mock_run.call_args_list] == ["lsblk", "findmnt"]

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_mounted_partition_not_probed(self, mock_run: MagicMock) -> None:
        payload = _windows_like()
        children = payload["blockdevices"][0]["children"]
        children[2]["mountpoints"] = ["/run/media/win"]
        children[3]["children"] = [{"name": "holder", "type": "crypt", "mountpoints": [None]}]
        mock_run.side_effect = _make_run(payload, "overlay").side_effect
        probed: list[str] = []

        def fake_probe(part: dict, _scratch: str) -> dict:
            probed.append(part["path"])
            return {"minSizeBytes": 5, "resizeBlockedReason": "", "bitlocker": False}

        with (
            patch("xen.utils.disk_detector.probe.probe_partition", side_effect=fake_probe),
            patch("xen.utils.disk_detector.probe.detect_os", return_value={}) as detect,
        ):
            parts = disk_detector.list_disks(probe=True)[0]["partitions"]
        assert probed == ["/dev/vda1", "/dev/vda2"]
        for part in parts[2:]:
            assert part["mounted"] is True
            assert part["resizeBlockedReason"] == "In use"
            assert (part["canShrink"], part["canGrow"], part["minSizeBytes"]) == (False, False, 0)
        assert detect.call_args.args[0] == parts

    @patch("xen.utils.disk_detector.subprocess.run")
    def test_probe_results_reach_partitions_and_segments(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _make_run(_windows_like(), "overlay").side_effect
        results = {
            "/dev/vda3": {
                "minSizeBytes": 7 * 1024**3,
                "resizeBlockedReason": "",
                "bitlocker": False,
            },
            "/dev/vda4": {
                "minSizeBytes": 0,
                "resizeBlockedReason": "Couldn't check this partition: boom",
                "bitlocker": False,
            },
        }

        def fake_probe(part: dict, _scratch: str) -> dict:
            default = {
                "minSizeBytes": 0,
                "resizeBlockedReason": part["resizeBlockedReason"],
                "bitlocker": False,
            }
            return copy.deepcopy(results.get(part["path"], default))

        names = {"/dev/vda3": "Windows", "/dev/vda4": "Windows"}
        with (
            patch("xen.utils.disk_detector.probe.probe_partition", side_effect=fake_probe),
            patch("xen.utils.disk_detector.probe.detect_os", return_value=names),
        ):
            disk = disk_detector.list_disks(probe=True)[0]
        win, rec = disk["partitions"][2:]
        assert win["minSizeBytes"] == 7 * 1024**3
        assert (win["canShrink"], win["osName"]) == (True, "Windows")
        assert (rec["canShrink"], rec["resizeBlockedReason"]) == (
            False,
            "Couldn't check this partition: boom",
        )
        seg = next(s for s in disk["segments"] if s.get("path") == "/dev/vda3")
        assert seg["minSizeBytes"] == 7 * 1024**3
        assert seg["osName"] == "Windows"


class TestMockDisks:
    def test_mock_disks_include_alongside_candidate(self) -> None:
        disks = mock_disks()
        win_disk = next(d for d in disks if any(p["osName"] for p in d["partitions"]))
        parts = win_disk["partitions"]
        assert sum(p["isEsp"] for p in parts) == 1
        win = next(p for p in parts if p["osName"] == "Windows 11")
        assert win["fstype"] == "ntfs"
        assert win["canShrink"] is True
        assert 0 < win["minSizeBytes"] < win["sizeBytes"]
        assert win["resizeBlockedReason"] == ""
        assert win["path"] == f"/dev/{win['name']}"
        expected = disk_detector._fallback_partition_path(win_disk["name"], win["number"])
        assert win["path"] == expected
        assert win["startSector"] > 0 and win["endSector"] > win["startSector"]
        seg = next(s for s in win_disk["segments"] if s.get("path") == win["path"])
        assert seg["minSizeBytes"] == win["minSizeBytes"]
        keys = set(win)
        for disk in disks:
            for part in disk["partitions"]:
                assert set(part) == keys

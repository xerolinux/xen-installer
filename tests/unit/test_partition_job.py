from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from xen.jobs.base import JobContext
from xen.jobs.partition import PartitionJob, resolve_root_encryption
from xen.partitioning import fscaps
from xen.partitioning.executor import ExecResult
from xen.partitioning.plan import PartitionPlan
from xen.utils.disk_detector import ESP_TYPE_GUID, LINUX_TYPE_GUID

MIB = 1024**2
GIB = 1024**3
SS = 512
OUTPUT_KEYS = {
    "partition_assignments",
    "luks_passphrases",
    "root_partition",
    "efi_partition",
    "esp_formatted",
    "target_swap_devices",
    "opened_mappers",
    "encryption",
    "filesystem",
    "swap_strategy",
}


def _part(number: int, start_mib: int, size_mib: int, fstype: str, **extra: Any) -> dict:
    align = MIB // SS
    start, size = start_mib * align, size_mib * align
    caps = fscaps.caps_for(fstype)
    part = {
        "name": f"sdx{number}",
        "path": f"/dev/sdx{number}",
        "disk": "sdx",
        "number": number,
        "startSector": start,
        "endSector": start + size - 1,
        "sizeSectors": size,
        "sizeBytes": size * SS,
        "fstype": fstype,
        "partTypeGuid": LINUX_TYPE_GUID,
        "isEsp": False,
        "mounted": False,
        "canShrink": caps.can_shrink,
        "canGrow": caps.can_grow,
        "minSizeBytes": 0,
        "resizeBlockedReason": caps.blocked_reason,
    }
    part.update(extra)
    return part


def _disk(parts: list[dict] | None = None) -> dict[str, Any]:
    return {
        "name": "sdx",
        "sizeBytes": 100 * GIB,
        "sizeSectors": 100 * GIB // SS,
        "sectorSize": SS,
        "ptType": "gpt",
        "partitions": parts or [],
    }


def _dual_boot_disk() -> dict[str, Any]:
    return _disk(
        [
            _part(1, 1, 512, "vfat", isEsp=True, partTypeGuid=ESP_TYPE_GUID),
            _part(2, 513, 60 * 1024, "ext4", minSizeBytes=4 * GIB),
        ]
    )


P2_START = 513 * 2048
MANUAL_ROWS = [
    {"kind": "existing", "number": 1, "action": "keep", "mountpoint": "/boot/efi"},
    {"kind": "existing", "number": 2, "action": "keep", "newEnd": P2_START + 30 * GIB // SS - 1},
    {
        "kind": "new",
        "ref": "r1",
        "start": P2_START + 30 * GIB // SS,
        "end": P2_START + 60 * GIB // SS - 1,
        "fstype": "ext4",
        "mountpoint": "/",
        "encrypt": True,
    },
]


def _selections(mode: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "disk": "/dev/sdx",
        "partition_mode": mode,
        "filesystem": "ext4",
        "encryption": False,
        "encryption_passphrase": "",
        "swap_strategy": "file",
        "efi_size_mb": 2000,
        "has_internet": True,
        "dry_run": False,
        "confirmed": True,
        "bootloader": "grub",
    }
    if mode == "alongside":
        base.update(
            alongside_partition=2,
            alongside_keep_size_bytes=30 * GIB,
            alongside_esp={"disk": "sdx", "number": 1},
        )
    if mode == "manual":
        base.update(manual_rows=MANUAL_ROWS, manual_new_table=False, manual_root_passphrase="pw")
    base.update(extra)
    return base


def _exec_result(**extra: Any) -> ExecResult:
    result = ExecResult(
        ok=True,
        assignments=[
            {"name": "sdx3", "path": "/dev/sdx3", "raw_path": "/dev/sdx3", "mountpoint": "/",
             "fstype": "ext4", "subvolumes": [], "format": True},
            {"name": "sdx1", "path": "/dev/sdx1", "raw_path": "/dev/sdx1",
             "mountpoint": "/boot/efi", "fstype": "vfat", "subvolumes": [], "format": False},
        ],
        root_partition="/dev/sdx3",
        efi_partition="/dev/sdx1",
        swap_devices=["/mnt/target/swapfile"],
    )  # fmt: skip
    for key, value in extra.items():
        setattr(result, key, value)
    return result


class Harness:
    def __init__(self, disks: list[dict[str, Any]], result: ExecResult | None = None) -> None:
        self.disks = disks
        self.result = result or _exec_result()
        self.executor = MagicMock()
        self.executor.return_value.execute.return_value = self.result
        self.executor.return_value.release_previous.return_value = ""
        self.list_disks = MagicMock(return_value=disks)
        self.patches = [
            patch("xen.jobs.partition.disk_detector.list_disks", self.list_disks),
            patch("xen.jobs.partition.PlanExecutor", self.executor),
            patch("xen.jobs.partition.os.path.isdir", return_value=True),
            patch("xen.jobs.partition.os.path.exists", return_value=True),
            patch("xen.jobs.partition.disk_release.holds_running_system", return_value=False),
        ]

    def __enter__(self) -> Harness:
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        for p in reversed(self.patches):
            p.stop()

    @property
    def plan(self) -> PartitionPlan:
        return self.executor.return_value.execute.call_args.args[0]

    @property
    def execute_args(self) -> tuple:
        return self.executor.return_value.execute.call_args.args


def _run(selections: dict[str, Any], disks: list[dict] | None = None, **kw: Any):
    job = PartitionJob(kw.pop("config", None))
    context = JobContext(selections=selections, target_root="/mnt/target")
    with Harness(disks or [_dual_boot_disk()], kw.pop("result", None)) as harness:
        result = job.run(context)
    return result, context, harness


def test_job_builds_erase_alongside_manual_plans() -> None:
    result, _, h = _run(_selections("auto", encryption=True, encryption_passphrase="secret"))
    assert result.success, result.message
    assert h.plan.new_table and [c.ref for c in h.plan.creates] == ["esp", "root"]
    assert h.execute_args[1:] == ("secret", "file")
    assert not h.list_disks.call_args.kwargs.get("probe")

    result, _, h = _run(_selections("alongside"))
    assert result.success, result.message
    (resize,) = h.plan.resizes
    assert (resize.number, resize.shrink) == (2, True)
    assert not h.plan.new_table
    assert h.list_disks.call_args.kwargs.get("probe") is True

    result, _, h = _run(_selections("manual"))
    assert result.success, result.message
    root = next(t for t in h.plan.targets if t.mountpoint == "/")
    assert root.ref == "new:r1" and root.encrypt
    assert h.execute_args[1] == "pw"


def test_validate_reports_plan_errors() -> None:
    rows = [{"kind": "existing", "number": 2, "action": "keep", "mountpoint": "/home"}]
    result, _, h = _run(_selections("manual", manual_rows=rows))
    assert not result.success
    assert "No partition is assigned to root (/)." in result.message
    assert "No EFI system partition is assigned to /boot/efi." in result.message
    h.executor.return_value.execute.assert_not_called()


def test_outputs_same_keys_all_modes() -> None:
    for mode in ("auto", "alongside", "manual"):
        selections = _selections(mode)
        added = OUTPUT_KEYS - set(selections)
        result, context, _ = _run(selections)
        assert result.success, result.message
        assert set(context.selections) == set(_selections(mode)) | added
        assert OUTPUT_KEYS <= set(context.selections)


def test_outputs_carry_the_executor_layout() -> None:
    exec_result = _exec_result(
        assignments=[
            {"name": "sdx3", "path": "/dev/mapper/cryptroot", "raw_path": "/dev/sdx3",
             "mountpoint": "/", "fstype": "btrfs", "format": True,
             "subvolumes": [{"name": "@", "mountpoint": "/"}]},
        ],
        luks_passphrases={"sdx3": "pw"},
        opened_mappers=["cryptroot", "cryptswap"],
        esp_formatted=True,
    )  # fmt: skip
    result, context, _ = _run(_selections("manual"), result=exec_result)
    assert result.success, result.message
    s = context.selections
    assert s["partition_assignments"] == exec_result.assignments
    assert s["luks_passphrases"] == {"sdx3": "pw"}
    assert (s["root_partition"], s["efi_partition"]) == ("/dev/sdx3", "/dev/sdx1")
    assert s["esp_formatted"] is True
    assert s["target_swap_devices"] == ["/mnt/target/swapfile"]
    assert s["opened_mappers"] == ["cryptroot", "cryptswap"]
    assert s["encryption"] is True and s["filesystem"] == "btrfs"


def test_manual_mode_forces_swap_strategy_none() -> None:
    result, context, h = _run(_selections("manual", swap_strategy="hibernate"))
    assert result.success, result.message
    assert h.execute_args[2] == "none"
    assert context.selections["swap_strategy"] == "none"


def test_executor_refusal_is_the_job_failure_message() -> None:
    refusal = "/dev/sdx2 is in use (mounted at /run/media/x): close it first."
    result, context, _ = _run(_selections("alongside"), result=ExecResult(ok=False, error=refusal))
    assert not result.success
    assert result.message == refusal
    assert "partition_assignments" not in context.selections


def test_btrfs_subvolumes_from_selection_then_config_then_default() -> None:
    custom = [{"name": "@", "mountpoint": "/"}, {"name": "@data", "mountpoint": "/data"}]
    sel = _selections("auto", filesystem="btrfs", btrfs_subvolumes=custom)
    _, _, h = _run(sel)
    assert h.plan.targets[1].subvolumes == custom

    _, _, h = _run(_selections("auto", filesystem="btrfs"), config={"btrfs_subvolumes": custom})
    assert h.plan.targets[1].subvolumes == custom

    _, _, h = _run(_selections("auto", filesystem="btrfs"))
    names = [sv["name"] for sv in h.plan.targets[1].subvolumes]
    assert names == ["@", "@home", "@root", "@srv", "@cache", "@tmp", "@log", "@swap"]


def test_min_install_gib_from_config() -> None:
    result, _, _ = _run(_selections("alongside"), config={"min_install_gib": 80})
    assert not result.success
    assert "The root (/) partition must be at least 80 GiB." in result.message


def test_btrfs_root_refused_offline() -> None:
    result, _, _ = _run(_selections("auto", filesystem="btrfs", has_internet=False))
    assert not result.success
    assert "internet connection" in result.message


def test_missing_disk_and_invalid_mode() -> None:
    result, _, _ = _run(_selections("auto", disk=""))
    assert not result.success and result.error_code == 30
    result, _, _ = _run(_selections("bogus"))
    assert not result.success and result.error_code == 31


def test_unreadable_alongside_selection_fails_cleanly() -> None:
    result, _, _ = _run(_selections("alongside", alongside_esp=None))
    assert not result.success
    assert "Invalid partition selection" in result.message


def test_encryption_requires_passphrase() -> None:
    result, _, h = _run(_selections("auto", encryption=True))
    assert not result.success and result.error_code == 46
    result, _, h = _run(_selections("manual", manual_root_passphrase=""))
    assert not result.success and result.error_code == 46
    h.executor.return_value.execute.assert_not_called()


def test_real_run_requires_uefi() -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()]) as h, patch(
        "xen.jobs.partition.os.path.isdir", return_value=False
    ):
        result = job.run(JobContext(selections=_selections("auto")))
    assert not result.success and result.error_code == 65
    h.executor.return_value.execute.assert_not_called()


def test_real_run_requires_confirmation() -> None:
    result, _, h = _run(_selections("auto", confirmed=False))
    assert not result.success and result.error_code == 38
    h.executor.return_value.execute.assert_not_called()


def test_dry_run_is_the_default_and_executes_nothing() -> None:
    sel = _selections("alongside")
    del sel["dry_run"]
    result, context, h = _run(sel)
    assert result.success, result.message
    assert result.data["dry_run"] is True
    h.executor.return_value.execute.assert_not_called()
    assert not h.list_disks.call_args.kwargs.get("probe")
    assert "partition_assignments" not in context.selections


def test_refuses_disk_backing_the_running_system() -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()]) as h, patch(
        "xen.jobs.partition.disk_release.holds_running_system", return_value=True
    ):
        result = job.run(JobContext(selections=_selections("auto")))
    assert not result.success and result.error_code == 56
    h.executor.return_value.execute.assert_not_called()


def test_retry_reuses_the_executor_so_it_releases_the_previous_run() -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()]) as h:
        job.run(JobContext(selections=_selections("auto")))
        job.run(JobContext(selections=_selections("auto")))
    assert h.executor.call_count == 1
    assert h.executor.return_value.execute.call_count == 2


@pytest.mark.parametrize(
    ("assignments", "expected"),
    [
        ([{"path": "/dev/mapper/cryptroot", "raw_path": "/dev/sdx3", "mountpoint": "/"}],
         (True, "/dev/sdx3")),
        ([{"path": "/dev/mapper/cryptroot", "raw_path": "/dev/sdx3", "mountpoint": "",
           "subvolumes": [{"name": "@", "mountpoint": "/"}]}], (True, "/dev/sdx3")),
        ([{"path": "/dev/sdx3", "raw_path": "/dev/sdx3", "mountpoint": "/"}], (False, "")),
        ([{"path": "/dev/mapper/cryptroot", "raw_path": "", "mountpoint": "/"}], (True, "")),
        ([], (False, "")),
    ],
)  # fmt: skip
def test_root_encryption_from_assignments(assignments: list, expected: tuple) -> None:
    for mode in ("auto", "alongside", "manual"):
        selections = {
            "partition_mode": mode,
            "partition_assignments": assignments,
            "encryption": not expected[0],
            "root_partition": "/dev/stale",
        }
        assert resolve_root_encryption(selections) == expected


def test_disk_missing_from_the_scan_fails_clearly() -> None:
    result, _, h = _run(_selections("auto", disk="/dev/sdz"))
    assert not result.success and result.error_code == 33
    assert "/dev/sdz" in result.message
    h.executor.return_value.execute.assert_not_called()


def test_manual_mode_logs_the_discarded_swap_choice(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="xen.jobs.partition"):
        result, _, _ = _run(_selections("manual", swap_strategy="hibernate"))
    assert result.success, result.message
    assert "ignoring swap strategy 'hibernate'" in caplog.text


DISK_CHANGED = (
    "The disk was already changed by a previous attempt. "
    "Go back to the Partition step so the disk is scanned again."
)


def _mounted(disk: dict[str, Any]) -> dict[str, Any]:
    for part in disk["partitions"]:
        part.update(mounted=True, resizeBlockedReason="In use")
    return disk


def test_retry_after_a_later_failure_releases_the_target_before_scanning() -> None:
    job = PartitionJob()
    state = {"left_mounted": False}
    with Harness([_dual_boot_disk()]) as h:
        executor = h.executor.return_value
        executor.release_previous.side_effect = lambda: state.update(left_mounted=False) or ""
        h.list_disks.side_effect = lambda **_kw: [
            _mounted(_dual_boot_disk()) if state["left_mounted"] else _dual_boot_disk()
        ]
        first = job.run(JobContext(selections=_selections("auto"), target_root="/mnt/target"))
        assert first.success, first.message
        state["left_mounted"] = True
        again = job.run(JobContext(selections=_selections("auto"), target_root="/mnt/target"))
    assert again.success, again.message
    assert executor.execute.call_count == 2


def test_release_failure_fails_the_retry_before_scanning() -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()]) as h:
        job.run(JobContext(selections=_selections("auto")))
        h.executor.return_value.release_previous.return_value = "Closing cryptroot failed: busy"
        h.list_disks.reset_mock()
        result = job.run(JobContext(selections=_selections("auto")))
    assert not result.success
    assert "Closing cryptroot failed: busy" in result.message
    h.list_disks.assert_not_called()


@pytest.mark.parametrize("mode", ["alongside", "manual"])
def test_retry_after_the_disk_changed_is_refused_for_alongside_and_manual(mode: str) -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()], _exec_result(disk_changed=True)) as h:
        first = job.run(JobContext(selections=_selections(mode)))
        assert first.success, first.message
        again = job.run(JobContext(selections=_selections(mode)))
    assert not again.success and again.message == DISK_CHANGED
    assert h.executor.return_value.execute.call_count == 1


def test_retry_after_a_failed_write_is_refused_for_manual() -> None:
    failed = ExecResult(ok=False, error="Create failed: boom", disk_changed=True)
    job = PartitionJob()
    with Harness([_dual_boot_disk()], failed):
        assert not job.run(JobContext(selections=_selections("manual"))).success
        again = job.run(JobContext(selections=_selections("manual")))
    assert again.message == DISK_CHANGED


def test_new_manual_choice_after_the_disk_changed_is_accepted() -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()], _exec_result(disk_changed=True)) as h:
        assert job.run(JobContext(selections=_selections("manual"))).success
        rows = [dict(r) for r in MANUAL_ROWS]
        rows[2]["fstype"] = "xfs"
        again = job.run(JobContext(selections=_selections("manual", manual_rows=rows)))
    assert again.success, again.message
    assert h.executor.return_value.execute.call_count == 2


def test_same_manual_choice_is_accepted_after_a_rescan() -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()], _exec_result(disk_changed=True)) as h:
        assert job.run(JobContext(selections=_selections("manual"))).success
        job.disk_rescanned()
        again = job.run(JobContext(selections=_selections("manual")))
    assert again.success, again.message
    assert h.executor.return_value.execute.call_count == 2


def test_erase_retry_after_the_disk_changed_replays() -> None:
    job = PartitionJob()
    with Harness([_dual_boot_disk()], _exec_result(disk_changed=True)) as h:
        assert job.run(JobContext(selections=_selections("auto"))).success
        assert job.run(JobContext(selections=_selections("auto"))).success
    assert h.executor.return_value.execute.call_count == 2


def test_run_reuses_the_engine_validation_and_scans_once() -> None:
    job = PartitionJob()
    context = JobContext(selections=_selections("alongside"))
    with Harness([_dual_boot_disk()]) as h:
        assert job.validate(context).success
        result = job.run(context)
        assert result.success, result.message
        assert h.list_disks.call_count == 1
        assert job.run(context).success
        assert h.list_disks.call_count == 2

from __future__ import annotations

import json
from typing import Any

import pytest

from xen.partitioning import executor as executor_module
from xen.partitioning.executor import CmdResult, PlanExecutor, mapper_name
from xen.partitioning.plan import (
    Create,
    PartitionPlan,
    Resize,
    Target,
    build_alongside_plan,
    build_erase_plan,
)
from xen.utils.disk_detector import ESP_TYPE_GUID, LINUX_TYPE_GUID, SWAP_TYPE_GUID

MIB = 1024**2
GIB = 1024**3
DISK_SECTORS = 41943040
LAST_USABLE = DISK_SECTORS - 34
SUBVOLS = [{"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}]


@pytest.fixture(autouse=True)
def _fallback_paths(monkeypatch):
    def path(disk: str, number: int) -> str:
        base = disk.rsplit("/", 1)[-1]
        return f"/dev/{base}{'p' if base[-1].isdigit() else ''}{number}"

    monkeypatch.setattr(executor_module, "partition_path", path)


DEFAULTS: dict[str, Any] = {
    "blockdev --getsz": f"{DISK_SECTORS}\n",
    "lsblk -J -l -p": '{"blockdevices": []}',
    "cryptsetup status": CmdResult(4, "", "inactive"),
}


class FakeRun:
    def __init__(self, responses: dict[str, Any] | None = None, fail: tuple[str, ...] = ()):
        self.calls: list[tuple[list[str], str, str | None]] = []
        self.responses = {**DEFAULTS, **(responses or {})}
        self.fail = fail

    def __call__(self, cmd: list[str], description: str, stdin: str | None = None) -> CmdResult:
        self.calls.append((list(cmd), description, stdin))
        joined = " ".join(cmd)
        if any(joined.startswith(prefix) for prefix in self.fail):
            return CmdResult(1, "", "boom")
        matches = [p for p in self.responses if joined.startswith(p)]
        out = self.responses[max(matches, key=len)] if matches else ""
        if isinstance(out, CmdResult):
            return out
        return CmdResult(0, out(self) if callable(out) else out, "")

    @property
    def cmds(self) -> list[list[str]]:
        return [c[0] for c in self.calls]

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.cmds]

    def index(self, prefix: str) -> int:
        return next(i for i, c in enumerate(self.joined()) if c.startswith(prefix))

    def has(self, prefix: str) -> bool:
        return any(c.startswith(prefix) for c in self.joined())


def proc_swaps(*devices: str) -> str:
    rows = [f"{d}  partition\t1048572\t0\t-2" for d in devices]
    return "\n".join(["Filename\tType\tSize\tUsed\tPriority", *rows]) + "\n"


def sgdisk_table(parts: list[tuple[int, int, int]], last: int = LAST_USABLE) -> str:
    lines = [
        f"Disk /dev/vda: {DISK_SECTORS} sectors, 20.0 GiB",
        "Sector size (logical/physical): 512/512 bytes",
        f"First usable sector is 34, last usable sector is {last}",
        "Partitions will be aligned on 2048-sector boundaries",
        "",
        "Number  Start (sector)    End (sector)  Size       Code  Name",
    ]
    lines += [f"   {n}          {s}        {e}   1.0 GiB     8300  x" for n, s, e in parts]
    return "\n".join(lines) + "\n"


def sgdisk_info(start: int, end: int, name: str = "Windows", attrs: str = "0" * 16) -> str:
    return (
        "Partition GUID code: EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 (Microsoft basic data)\n"
        "Partition unique GUID: 6F2A1C1E-1111-2222-3333-444455556666\n"
        f"First sector: {start} (at 1.0 MiB)\n"
        f"Last sector: {end} (at 20.0 GiB)\n"
        "Partition size: 1 sectors (1.0 GiB)\n"
        f"Attribute flags: {attrs}\n"
        f"Partition name: '{name}'\n"
    )


def lsblk_parts(disk: str, parts: list[tuple[int, int]], sector_size: int = 512) -> str:
    rows: list[dict[str, Any]] = [
        {"path": f"/dev/{disk}", "pkname": None, "partn": None, "start": None}
    ]
    rows += [
        {"path": f"/dev/{disk}{n}", "pkname": disk, "partn": n, "start": s * sector_size // 512}
        for n, s in parts
    ]
    return json.dumps({"blockdevices": rows})


def erase_disk(sector_size: int = 512) -> dict[str, Any]:
    return {
        "name": "vda",
        "sectorSize": sector_size,
        "sizeSectors": DISK_SECTORS * 512 // sector_size,
        "ptType": "gpt",
        "partitions": [],
    }


def erase_responses(plan: PartitionPlan, numbers: list[int] | None = None) -> dict[str, Any]:
    numbers = numbers or list(range(1, len(plan.creates) + 1))
    starts = [(n, c.start) for n, c in zip(numbers, plan.creates, strict=True)]
    return {
        "sgdisk -p": sgdisk_table([]),
        "lsblk -J -l -b": lsblk_parts("vda", starts, plan.sector_size),
    }


def alongside_disk() -> dict[str, Any]:
    return {
        "name": "vda",
        "sectorSize": 512,
        "sizeSectors": DISK_SECTORS,
        "ptType": "gpt",
        "partitions": [
            {"number": 1, "startSector": 2048, "endSector": 206847, "fstype": "vfat",
             "isEsp": True},
            {"number": 2, "startSector": 206848, "endSector": LAST_USABLE, "fstype": "ext4"},
        ],
    }


def alongside_setup(min_blocks: int = 262144) -> tuple[PartitionPlan, FakeRun]:
    plan = build_alongside_plan(alongside_disk(), 2, 10 * GIB, "ext4", False, 1, "vda", [])
    run = FakeRun(
        {
            "sgdisk -p": sgdisk_table([(1, 2048, 206847), (2, 206848, LAST_USABLE)]),
            "sgdisk -i 2": sgdisk_info(206848, LAST_USABLE),
            "resize2fs -P": f"Estimated minimum size of the filesystem: {min_blocks}\n",
            "dumpe2fs -h": "Block size:               4096\n",
            "lsblk -J -l -b": lsblk_parts(
                "vda", [(1, 2048), (2, 206848), (3, plan.creates[0].start)]
            ),
        }
    )
    return plan, run


def execute(plan: PartitionPlan, run: FakeRun, bootloader: str = "grub", **kw: Any):
    ex = PlanExecutor(run, "/mnt", bootloader)
    return ex, ex.execute(plan, kw.get("passphrase", "secret-pass"), kw.get("swap", "none"))


def _is_destructive(cmd: list[str]) -> bool:
    joined = " ".join(cmd)
    if cmd[0] == "sgdisk":
        return not (joined.startswith("sgdisk -p") or joined.startswith("sgdisk -i"))
    if cmd[0] == "resize2fs":
        return "-P" not in cmd
    if cmd[0] == "ntfsresize":
        return "--info" not in cmd and "--no-action" not in cmd
    if cmd[0] == "cryptsetup":
        return "luksFormat" in cmd
    if cmd[:3] == ["btrfs", "filesystem", "resize"]:
        return True
    return cmd[0] in ("wipefs", "partprobe", "mkswap") or cmd[0].startswith("mkfs")


def test_phase_order_alongside():
    plan, run = alongside_setup()
    _, result = execute(plan, run)
    assert result.ok, result.error

    first_table_write = next(
        i for i, c in enumerate(run.cmds) if c[0] == "sgdisk" and c[1] not in ("-p", "-i")
    )
    assert run.index("e2fsck -f -y /dev/vda2") < first_table_write
    assert run.index("resize2fs -P /dev/vda2") < first_table_write

    new_bytes = (plan.resizes[0].new_end - 206848 + 1) * 512
    fs_k = (new_bytes - 8 * MIB) // 4096 * 4096 // 1024
    shrink = run.index(f"resize2fs /dev/vda2 {fs_k}K")
    recreate = run.index("sgdisk -a 1 -d 2 ")
    grow = run.joined().index("resize2fs /dev/vda2")
    assert shrink < recreate < grow
    assert run.index("partprobe /dev/vda") > recreate
    assert run.cmds[recreate] == [
        "sgdisk", "-a", "1", "-d", "2",
        "-n", f"2:206848:{plan.resizes[0].new_end}",
        "-t", "2:EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
        "-u", "2:6F2A1C1E-1111-2222-3333-444455556666",
        "-c", "2:Windows",
        "/dev/vda",
    ]  # fmt: skip
    create = run.index("sgdisk -a 1 -n 3:")
    assert grow < create
    assert run.cmds[create][-3:] == ["-c", "3:XeroLinux root", "/dev/vda"]
    assert not run.has("parted")
    assert not any(c[0] == "wipefs" and c[-1] == "/dev/vda1" for c in run.cmds)
    assert result.root_partition == "/dev/vda3"
    assert result.efi_partition == "/dev/vda1"
    assert not result.esp_formatted


def test_new_table_then_creates():
    plan = build_erase_plan(erase_disk(), "ext4", False, 2000, [])
    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run)
    assert result.ok, result.error
    joined = run.joined()
    order = [
        run.index("wipefs -a /dev/vda"),
        run.index("sgdisk --zap-all /dev/vda"),
        run.index("sgdisk -o /dev/vda"),
        run.index("sgdisk -a 1 -n 1:2048:"),
        run.index("sgdisk -a 1 -n 2:"),
        run.index("partprobe /dev/vda"),
    ]
    assert order == sorted(order)
    esp, root = plan.creates
    assert run.cmds[order[3]] == [
        "sgdisk", "-a", "1", "-n", f"1:{esp.start}:{esp.end}",
        "-t", f"1:{ESP_TYPE_GUID}", "-c", "1:EFI system", "/dev/vda",
    ]  # fmt: skip
    assert run.cmds[order[4]][-3:] == ["-c", "2:XeroLinux root", "/dev/vda"]
    assert "mkfs.fat -F32 /dev/vda1" in joined
    assert "mkfs.ext4 -F /dev/vda2" in joined
    assert run.index("wipefs -af /dev/vda2") < run.index("mkfs.ext4")
    assert result.esp_formatted
    assert result.assignments == [
        {
            "name": "vda1",
            "path": "/dev/vda1",
            "raw_path": "/dev/vda1",
            "mountpoint": "/boot/efi",
            "fstype": "fat32",
            "subvolumes": [],
            "format": True,
        },
        {
            "name": "vda2",
            "path": "/dev/vda2",
            "raw_path": "/dev/vda2",
            "mountpoint": "/",
            "fstype": "ext4",
            "subvolumes": [],
            "format": True,
        },
    ]
    assert "mount -t ext4 /dev/vda2 /mnt" in joined
    assert "mount -t vfat /dev/vda1 /mnt/boot/efi" in joined
    assert joined.index("mount -t ext4 /dev/vda2 /mnt") < joined.index(
        "mount -t vfat /dev/vda1 /mnt/boot/efi"
    )


def test_preflight_failure_runs_no_destructive_command():
    plan, run = alongside_setup(min_blocks=10 * GIB // 4096)
    _, result = execute(plan, run)
    assert not result.ok
    assert "/dev/vda2 can't be shrunk to 10.0 GiB: it needs at least" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_preflight_gpt_bounds_refused_before_any_write():
    plan, run = alongside_setup()
    run.responses["sgdisk -p"] = sgdisk_table(
        [(1, 2048, 206847), (2, 206848, LAST_USABLE - 100)], last=LAST_USABLE - 100
    )
    _, result = execute(plan, run)
    assert not result.ok
    assert "usable" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_btrfs_preflight_mount_is_unmounted_when_probe_fails():
    disk = alongside_disk()
    disk["partitions"][1]["fstype"] = "btrfs"
    plan = build_alongside_plan(disk, 2, 10 * GIB, "ext4", False, 1, "vda", [])
    run = FakeRun(
        {"sgdisk -p": sgdisk_table([(1, 2048, 206847), (2, 206848, LAST_USABLE)]),
         "sgdisk -i 2": sgdisk_info(206848, LAST_USABLE)},
        fail=("btrfs inspect-internal",),
    )  # fmt: skip
    _, result = execute(plan, run)
    assert not result.ok
    mount = run.index("mount -t btrfs /dev/vda2 /run/xen-btrfs-scratch")
    assert run.index("umount /run/xen-btrfs-scratch") > mount
    assert "Preflight" in result.error and "btrfs inspect-internal" in result.error


def test_failure_after_table_change_names_phase_and_command_and_releases():
    plan = build_erase_plan(erase_disk(), "ext4", True, 2000, [])
    run = FakeRun(erase_responses(plan), fail=("mkfs.ext4",))
    ex, result = execute(plan, run)
    assert not result.ok
    assert result.error.startswith("Format failed")
    assert "mkfs.ext4 -F /dev/mapper/cryptroot" in result.error
    tail = run.joined()[run.index("mkfs.ext4") :]
    assert any(c.startswith("umount -R /mnt") for c in tail)
    assert "cryptsetup close cryptroot" in tail
    assert result.opened_mappers == []


def test_grub_uses_pbkdf2_for_every_luks():
    plan = PartitionPlan(
        disk="vda",
        sector_size=512,
        new_table=True,
        creates=[
            Create("esp", 2048, 4196351, ESP_TYPE_GUID, "EFI system"),
            Create("root", 4196352, 30000000, LINUX_TYPE_GUID, "XeroLinux root"),
            Create("home", 30001152, 40000000, LINUX_TYPE_GUID, "XeroLinux home"),
            Create("swap", 40001536, LAST_USABLE, SWAP_TYPE_GUID, "XeroLinux swap"),
        ],
        targets=[
            Target("new:esp", "fat32", True, "/boot/efi", is_esp=True),
            Target("new:root", "ext4", True, "/", encrypt=True),
            Target("new:home", "xfs", True, "/home", encrypt=True),
            Target("new:swap", "swap", True, "swap", encrypt=True),
        ],
    )
    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run, "grub")
    assert result.ok, result.error
    formats = [c for c in run.cmds if "luksFormat" in c]
    assert len(formats) == 2
    assert all(c[c.index("--pbkdf") + 1] == "pbkdf2" for c in formats)
    assert all(c[2:5] == ["--type", "luks2", "--batch-mode"] for c in formats)
    secrets_used = {c[0][-1]: c[2] for c in run.calls if "luksFormat" in c[0]}
    assert secrets_used["/dev/vda2"] == "secret-pass"
    assert secrets_used["/dev/vda3"] != "secret-pass"
    assert "/dev/vda4" not in secrets_used
    assert result.opened_mappers == ["cryptroot", "crypthome", "cryptswap"]
    assert result.luks_passphrases == {
        "vda2": "secret-pass",
        "vda3": secrets_used["/dev/vda3"],
    }
    assert result.root_partition == "/dev/vda2"
    root = next(a for a in result.assignments if a["mountpoint"] == "/")
    assert root["path"] == "/dev/mapper/cryptroot" and root["raw_path"] == "/dev/vda2"

    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run, "systemd-boot")
    assert result.ok, result.error
    assert not any("--pbkdf" in c for c in run.cmds)


def test_empty_root_passphrase_fails_before_luks_format():
    plan = build_erase_plan(erase_disk(), "ext4", True, 2000, [])
    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run, passphrase="")
    assert not result.ok
    assert not run.has("cryptsetup luksFormat")


def test_mount_always_has_type():
    plan = build_erase_plan(erase_disk(), "btrfs", False, 2000, SUBVOLS)
    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run)
    assert result.ok, result.error
    mounts = [c for c in run.cmds if c[0] == "mount"]
    assert mounts and all(c[1] == "-t" for c in mounts)
    joined = run.joined()
    opts = "noatime,compress=zstd,space_cache=v2"
    assert f"mount -t btrfs -o {opts},subvol=@ /dev/vda2 /mnt" in joined
    assert f"mount -t btrfs -o {opts},subvol=@home /dev/vda2 /mnt/home" in joined
    assert joined.index(f"mount -t btrfs -o {opts},subvol=@ /dev/vda2 /mnt") < joined.index(
        f"mount -t btrfs -o {opts},subvol=@home /dev/vda2 /mnt/home"
    )
    scratch = run.index("mount -t btrfs -o subvolid=5 /dev/vda2 /run/xen-btrfs-scratch")
    assert run.index("btrfs subvolume create /run/xen-btrfs-scratch/@home") > scratch
    assert run.index("umount /run/xen-btrfs-scratch") > scratch
    assert "mkfs.btrfs -f -L arch_root /dev/vda2" in joined


def test_resolve_new_partition_by_start_sector():
    plan = build_erase_plan(erase_disk(4096), "xfs", False, 2000, [])
    run = FakeRun(erase_responses(plan, numbers=[7, 5]))
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert plan.creates[0].start == 256
    assert result.efi_partition == "/dev/vda7"
    assert result.root_partition == "/dev/vda5"
    assert "mkfs.xfs -f /dev/vda5" in run.joined()


def test_unresolvable_new_partition_fails():
    plan = build_erase_plan(erase_disk(), "xfs", False, 2000, [])
    lsblk = lsblk_parts("vda", [(1, 2048)])
    run = FakeRun({"sgdisk -p": sgdisk_table([]), "lsblk -J -l -b": lsblk})
    _, result = execute(plan, run)
    assert not result.ok
    assert result.error.startswith("Resolve failed")
    assert not run.has("mkfs")


def test_rerun_resets_state():
    encrypted = build_erase_plan(erase_disk(), "ext4", True, 2000, [])
    run = FakeRun(erase_responses(encrypted))
    ex = PlanExecutor(run, "/mnt", "grub")
    first = ex.execute(encrypted, "secret-pass", "none")
    assert first.ok and first.opened_mappers == ["cryptroot"] and first.luks_passphrases

    plain = build_erase_plan(erase_disk(), "ext4", False, 2000, [])
    run.responses["cryptsetup status cryptroot"] = "type: LUKS2"
    run.calls.clear()
    second = ex.execute(plain, "", "none")
    assert second.ok, second.error
    assert run.index("cryptsetup close cryptroot") < run.index("wipefs -a /dev/vda")
    assert second.opened_mappers == []
    assert second.luks_passphrases == {}
    assert second.root_partition == "/dev/vda2"
    assert all(a["path"] == a["raw_path"] for a in second.assignments)


def test_release_only_touches_previous_run_and_target_root():
    plan = build_erase_plan(erase_disk(), "ext4", False, 2000, [])
    run = FakeRun(erase_responses(plan))
    ex = PlanExecutor(run, "/mnt", "grub")
    ex._result.swap_devices = ["/dev/vdb3"]
    run.responses["cat /proc/swaps"] = proc_swaps("/dev/vdb3", "/mnt/swapfile", "/dev/zram0")
    result = ex.execute(plan, "", "none")
    assert result.ok, result.error
    wipe = run.index("wipefs -a /dev/vda")
    assert run.index("swapoff /dev/vdb3") < wipe
    assert run.index("swapoff /mnt/swapfile") < wipe
    assert run.index("umount -R /mnt") < wipe
    assert not run.has("swapoff /dev/zram0")


def test_active_swap_on_affected_partition_released_not_refused():
    plan, run = alongside_setup()
    run.responses["cat /proc/swaps"] = proc_swaps("/dev/vda2")
    run.responses["lsblk -J -l -p"] = lambda r: (
        _tree([{"n": 1}, {"n": 2}]) if r.has("swapoff /dev/vda2") else
        _tree([{"n": 1}, {"n": 2, "mounts": ["[SWAP]"]}])
    )  # fmt: skip
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert run.index("swapoff /dev/vda2") < run.index("e2fsck")


def test_active_swap_mapper_on_affected_partition_swapped_off_and_closed():
    plan = build_erase_plan(erase_disk(), "ext4", False, 2000, [])
    run = FakeRun(erase_responses(plan))
    run.responses["cat /proc/swaps"] = proc_swaps("/dev/mapper/cryptswap")
    run.responses["lsblk -J -l -p"] = lambda r: (
        _tree([{"n": 3}]) if r.has("cryptsetup close cryptswap") else
        _tree([{"n": 3, "holders": [_crypt("cryptswap", ["[SWAP]"])]}])
    )  # fmt: skip
    _, result = execute(plan, run)
    assert result.ok, result.error
    wipe = run.index("wipefs -a /dev/vda")
    assert run.index("swapoff /dev/mapper/cryptswap") < run.index("cryptsetup close cryptswap")
    assert run.index("cryptsetup close cryptswap") < wipe


def test_active_swap_on_kept_partition_untouched():
    plan, run = alongside_setup()
    run.responses["cat /proc/swaps"] = proc_swaps("/dev/vda4")
    run.responses["lsblk -J -l -p"] = _tree([{"n": 1}, {"n": 2}, {"n": 4, "mounts": ["[SWAP]"]}])
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert not run.has("swapoff")


def test_previous_run_mapper_on_other_disk_closed():
    encrypted = build_erase_plan(erase_disk(), "ext4", True, 2000, [])
    run = FakeRun(erase_responses(encrypted))
    ex = PlanExecutor(run, "/mnt", "grub")
    assert ex.execute(encrypted, "secret-pass", "none").ok
    other = build_erase_plan({**erase_disk(), "name": "vdb"}, "ext4", False, 2000, [])
    run.responses["cryptsetup status cryptroot"] = "type: LUKS2"
    run.responses["lsblk -J -l -b"] = lsblk_parts("vdb", [(1, 2048), (2, other.creates[1].start)])
    run.calls.clear()
    result = ex.execute(other, "", "none")
    assert result.ok, result.error
    assert run.index("cryptsetup close cryptroot") < run.index("wipefs -a /dev/vdb")


def test_mapper_name_already_open_refused_in_preflight():
    plan = build_erase_plan(erase_disk(), "ext4", True, 2000, [])
    run = FakeRun({**erase_responses(plan), "cryptsetup status cryptroot": "type: LUKS2"})
    _, result = execute(plan, run)
    assert not result.ok
    assert "cryptroot is already open" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]
    assert not run.has("cryptsetup close")


def _tree(parts: list[dict[str, Any]], disk_mounts: list[str] | None = None) -> str:
    rows: list[dict[str, Any]] = [
        {"path": "/dev/vda", "pkname": None, "type": "disk", "partn": None,
         "mountpoints": disk_mounts or []}
    ]  # fmt: skip
    for p in parts:
        path = f"/dev/vda{p['n']}"
        rows.append({"path": path, "pkname": "/dev/vda", "type": "part", "partn": p["n"],
                     "mountpoints": p.get("mounts", [])})  # fmt: skip
        rows += [{**h, "pkname": path} for h in p.get("holders", [])]
    return json.dumps({"blockdevices": rows})


def _crypt(name: str, mounts: list[str]) -> dict[str, Any]:
    return {"path": f"/dev/mapper/{name}", "type": "crypt", "partn": None, "mountpoints": mounts}


def test_unrelated_mapper_on_kept_partition_untouched():
    plan, run = alongside_setup()
    run.responses["lsblk -J -l -p"] = _tree(
        [{"n": 1}, {"n": 2}, {"n": 4, "holders": [_crypt("crypthome", ["/home/user"])]}]
    )
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert not run.has("cryptsetup close") and not run.has("umount /home")


@pytest.mark.parametrize(
    ("part", "message"),
    [
        ({"n": 2, "mounts": ["/media/x"]}, "/dev/vda2 is in use (mounted at /media/x)"),
        ({"n": 2, "mounts": ["[SWAP]"]}, "/dev/vda2 is in use (used as swap)"),
        (
            {"n": 2, "holders": [_crypt("luks-1234", ["/run/media/u/data"])]},
            "/dev/vda2 is in use (opened as luks-1234, mounted at /run/media/u/data)",
        ),
    ],
)
def test_busy_affected_partition_refused_in_preflight(part, message):
    plan, run = alongside_setup()
    run.responses["lsblk -J -l -p"] = _tree([{"n": 1, "mounts": ["/boot/efi"]}, part])
    _, result = execute(plan, run)
    assert not result.ok
    assert result.error.startswith(message), result.error
    assert "close it first" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]
    assert not run.has("e2fsck")


def test_nested_holder_counts_as_busy():
    plan, run = alongside_setup()
    tree = json.loads(_tree([{"n": 1}, {"n": 2, "holders": [_crypt("luks-9", [])]}]))
    tree["blockdevices"].append(
        {"path": "/dev/mapper/vg-data", "pkname": "/dev/mapper/luks-9", "type": "lvm",
         "partn": None, "mountpoints": ["/srv"]}
    )  # fmt: skip
    run.responses["lsblk -J -l -p"] = json.dumps(tree)
    _, result = execute(plan, run)
    assert not result.ok
    assert result.error.startswith("/dev/vda2 is in use (opened as luks-9; opened as vg-data")
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_new_table_refuses_busy_partition_anywhere_on_disk():
    plan = build_erase_plan(erase_disk(), "ext4", False, 2000, [])
    run = FakeRun({**erase_responses(plan), "lsblk -J -l -p": _tree([{"n": 7, "mounts": ["/x"]}])})
    _, result = execute(plan, run)
    assert not result.ok and "/dev/vda7 is in use" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_new_table_bounds_checked_before_wipe():
    plan = build_erase_plan(erase_disk(), "ext4", False, 2000, [])
    run = FakeRun({**erase_responses(plan), "blockdev --getsz": f"{DISK_SECTORS - 2048}\n"})
    _, result = execute(plan, run)
    assert not result.ok
    assert "lies outside the usable sectors" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_new_table_bounds_4kn():
    plan = build_erase_plan(erase_disk(4096), "ext4", False, 2000, [])
    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert plan.creates[-1].end == DISK_SECTORS // 8 - 6


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: setattr(p.resizes[0], "new_end", 206848), "would end before it starts"),
        (lambda p: setattr(p.creates[0], "start", p.resizes[0].new_end), "would overlap"),
        (lambda p: setattr(p.resizes[0], "number", 9), "partition 9 to resize doesn't exist"),
    ],
)
def test_preflight_geometry_refusals(change, message):
    plan, run = alongside_setup()
    change(plan)
    _, result = execute(plan, run)
    assert not result.ok
    assert message in result.error, result.error
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_grow_overlapping_next_partition_refused():
    plan = PartitionPlan(
        disk="vda",
        sector_size=512,
        new_table=False,
        resizes=[Resize(1, 30000000, "ext4", False)],
        targets=[Target("part:3", "ext4", True, "/"), Target("part:4", "vfat", False, "/boot/efi")],
    )
    run = FakeRun(
        {"sgdisk -p": sgdisk_table([(1, 2048, 20000000), (2, 20000768, 40000000)]),
         "sgdisk -i 1": sgdisk_info(2048, 20000000)}
    )  # fmt: skip
    _, result = execute(plan, run)
    assert not result.ok
    assert "partition 1 and partition 2 would overlap" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_resize_keeps_gpt_attribute_flags():
    plan, run = alongside_setup()
    run.responses["sgdisk -i 2"] = sgdisk_info(206848, LAST_USABLE, attrs="8000000000000001")
    _, result = execute(plan, run)
    assert result.ok, result.error
    recreate = run.cmds[run.index("sgdisk -a 1 -d 2 ")]
    assert recreate[recreate.index("-A") + 1] == "2:=:8000000000000001"
    assert recreate[-1] == "/dev/vda"


def test_scratch_mount_released_when_run_cmd_raises():
    disk = alongside_disk()
    disk["partitions"][1]["fstype"] = "btrfs"
    plan = build_alongside_plan(disk, 2, 10 * GIB, "ext4", False, 1, "vda", [])
    run = FakeRun(
        {"sgdisk -p": sgdisk_table([(1, 2048, 206847), (2, 206848, LAST_USABLE)]),
         "sgdisk -i 2": sgdisk_info(206848, LAST_USABLE)},
    )  # fmt: skip

    def raising(cmd: list[str], description: str, stdin: str | None = None) -> CmdResult:
        result = run(cmd, description, stdin)
        if cmd[:2] == ["btrfs", "inspect-internal"]:
            raise OSError("probe crashed")
        return result

    result = PlanExecutor(raising, "/mnt", "grub").execute(plan, "", "none")
    assert not result.ok
    assert result.error == "Preflight failed: probe crashed"
    assert run.index("umount /run/xen-btrfs-scratch") > run.index("btrfs inspect-internal")


def test_exception_in_release_after_failure_keeps_original_error():
    plan = build_erase_plan(erase_disk(), "ext4", True, 2000, [])
    run = FakeRun(erase_responses(plan), fail=("mkfs.ext4",))

    def raising(cmd: list[str], description: str, stdin: str | None = None) -> CmdResult:
        if cmd[:2] == ["cryptsetup", "close"]:
            raise OSError("close crashed")
        return run(cmd, description, stdin)

    result = PlanExecutor(raising, "/mnt", "grub").execute(plan, "secret-pass", "none")
    assert not result.ok
    assert result.error.startswith("Format failed")


def test_unexpected_exception_is_reported_and_released():
    plan = build_erase_plan(erase_disk(), "ext4", True, 2000, [])
    run = FakeRun(erase_responses(plan))

    def raising(cmd: list[str], description: str, stdin: str | None = None) -> CmdResult:
        if cmd[0].startswith("mkfs"):
            raise OSError("device vanished")
        return run(cmd, description, stdin)

    ex = PlanExecutor(raising, "/mnt", "grub")
    result = ex.execute(plan, "secret-pass", "none")
    assert not result.ok
    assert result.error == "Format failed: device vanished"
    tail = run.joined()[run.index("wipefs -af /dev/vda1") :]
    assert "umount -R /mnt" in tail and "cryptsetup close cryptroot" in tail
    assert result.opened_mappers == []


def test_encrypted_swap_opened_before_swapon():
    plan = PartitionPlan(
        disk="vda",
        sector_size=512,
        new_table=True,
        creates=[
            Create("esp", 2048, 4196351, ESP_TYPE_GUID, "EFI system"),
            Create("root", 4196352, 30000000, LINUX_TYPE_GUID, "XeroLinux root"),
            Create("swap", 30001152, LAST_USABLE, SWAP_TYPE_GUID, "XeroLinux swap"),
        ],
        targets=[
            Target("new:esp", "fat32", True, "/boot/efi", is_esp=True),
            Target("new:root", "ext4", True, "/", encrypt=True),
            Target("new:swap", "swap", True, "swap", encrypt=True),
        ],
    )
    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run, swap="file")
    assert result.ok, result.error
    assert not run.has("cryptsetup luksFormat --type luks2 --batch-mode --pbkdf pbkdf2 /dev/vda3")
    plain = (
        "cryptsetup open --type plain --key-file /dev/urandom --cipher aes-xts-plain64 "
        "--key-size 256 /dev/vda3 cryptswap"
    )
    opened = run.index(plain)
    mkswap = run.index("mkswap /dev/mapper/cryptswap")
    swapon = run.index("swapon /dev/mapper/cryptswap")
    assert run.index("wipefs -a /dev/vda3") < opened < mkswap < swapon
    assert run.calls[opened][2] is None
    assert result.opened_mappers == ["cryptroot", "cryptswap"]
    assert result.swap_devices == ["/dev/mapper/cryptswap"]
    assert not run.has("dd ")
    assert "vda3" not in result.luks_passphrases


def test_swapfile_when_no_swap_target():
    plan = build_erase_plan(erase_disk(), "ext4", False, 2000, [])
    run = FakeRun(erase_responses(plan))
    _, result = execute(plan, run, swap="file")
    assert result.ok, result.error
    assert run.index("swapon /mnt/swapfile") > run.index("mkswap /mnt/swapfile")
    assert result.swap_devices == ["/mnt/swapfile"]


def test_kept_btrfs_home_uses_at_home_when_present():
    disk = alongside_disk()
    disk["partitions"].append(
        {"number": 3, "startSector": 50000000, "endSector": 60000000, "fstype": "btrfs"}
    )
    opts = "noatime,compress=zstd,space_cache=v2"

    def run_with(listing: str) -> FakeRun:
        plan = PartitionPlan(
            disk="vda",
            sector_size=512,
            new_table=False,
            targets=[
                Target("part:2", "ext4", True, "/"),
                Target("part:1", "vfat", False, "/boot/efi", is_esp=True),
                Target("part:3", "btrfs", False, "/home"),
            ],
        )
        run = FakeRun(
            {
                "sgdisk -p": sgdisk_table([(1, 2048, 206847), (2, 206848, 40000000)]),
                "btrfs subvolume list": listing,
            }
        )
        _, result = execute(plan, run)
        assert result.ok, result.error
        assert not run.has("mkfs.btrfs") and not run.has("wipefs -af /dev/vda3")
        return run

    run = run_with("ID 256 gen 9 top level 5 path @\nID 257 gen 9 top level 5 path @home\n")
    assert f"mount -t btrfs -o {opts},subvol=@home /dev/vda3 /mnt/home" in run.joined()

    run = run_with("ID 256 gen 9 top level 5 path @homework\n")
    assert f"mount -t btrfs -o {opts} /dev/vda3 /mnt/home" in run.joined()


def test_reused_esp_on_other_disk_resolves_against_its_disk():
    plan = build_alongside_plan(alongside_disk(), 2, 10 * GIB, "ext4", False, 1, "nvme0n1", [])
    _, run = alongside_setup()
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert result.efi_partition == "/dev/nvme0n1p1"
    assert "mount -t vfat /dev/nvme0n1p1 /mnt/boot/efi" in run.joined()


@pytest.mark.parametrize(
    ("mountpoint", "name"),
    [
        ("/", "cryptroot"),
        ("/home", "crypthome"),
        ("swap", "cryptswap"),
        ("/var/lib/data", "cryptdata-var-lib-data"),
        ("", "cryptdata"),
    ],
)
def test_mapper_name(mountpoint, name):
    assert mapper_name(mountpoint) == name


def _ntfs_setup(hiber: str) -> tuple[PartitionPlan, FakeRun]:
    disk = alongside_disk()
    disk["partitions"][1]["fstype"] = "ntfs"
    plan = build_alongside_plan(disk, 2, 10 * GIB, "ext4", False, 1, "vda", [])
    run = FakeRun(
        {
            "sgdisk -p": sgdisk_table([(1, 2048, 206847), (2, 206848, LAST_USABLE)]),
            "sgdisk -i 2": sgdisk_info(206848, LAST_USABLE),
            "sh -c": hiber,
            "ntfsresize --info": "You might resize at 4000000000 bytes or 4000 MB\n",
            "lsblk -J -l -b": lsblk_parts(
                "vda", [(1, 2048), (2, 206848), (3, plan.creates[0].start)]
            ),
        }
    )
    return plan, run


def test_hibernated_ntfs_refused_in_preflight():
    plan, run = _ntfs_setup("hibr")
    _, result = execute(plan, run)
    assert not result.ok
    assert result.error == (
        "/dev/vda2 can't be shrunk: Windows is hibernated (Fast Startup): "
        "shut Windows down fully first."
    )
    check = run.cmds[run.index("sh -c")]
    assert check[-1] == "/dev/vda2" and "ntfscat" in check[2]
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_ntfs_without_hibernation_shrinks():
    plan, run = _ntfs_setup("")
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert run.index("sh -c") < run.index("ntfsresize --force --force")


@pytest.mark.parametrize("min_bytes", [6 * GIB, 6 * GIB + 4096, 9 * GIB + 1 * MIB - 4096])
def test_preflight_accepts_the_shrink_minimum_the_plan_allows(min_bytes):
    from xen.partitioning import fscaps

    keep = fscaps.min_shrink_bytes(min_bytes)
    plan = build_alongside_plan(alongside_disk(), 2, keep, "ext4", False, 1, "vda", [])
    _, run = alongside_setup(min_blocks=-(-min_bytes // 4096))
    run.responses["lsblk -J -l -b"] = lsblk_parts(
        "vda", [(1, 2048), (2, 206848), (3, plan.creates[0].start)]
    )
    _, result = execute(plan, run)
    assert result.ok, result.error


def _grow_plan(fstype: str) -> tuple[PartitionPlan, FakeRun]:
    plan = PartitionPlan(
        disk="vda",
        sector_size=512,
        new_table=False,
        resizes=[Resize(2, 30000000, fstype, False)],
        targets=[Target("part:2", fstype, False, "/home")],
    )
    run = FakeRun(
        {"sgdisk -p": sgdisk_table([(1, 2048, 206847), (2, 206848, 20000000)]),
         "sgdisk -i 2": sgdisk_info(206848, 20000000)}
    )  # fmt: skip
    return plan, run


def test_ext4_grow_is_checked_with_e2fsck_before_any_write():
    plan, run = _grow_plan("ext4")
    _, result = execute(plan, run)
    assert result.ok, result.error
    first_write = next(i for i, c in enumerate(run.cmds) if _is_destructive(c))
    assert run.index("e2fsck -f -y /dev/vda2") < first_write
    assert run.index("e2fsck -f -y /dev/vda2") < run.index("resize2fs /dev/vda2")


def test_ext4_grow_refused_when_e2fsck_fails():
    plan, run = _grow_plan("ext4")
    run.responses["e2fsck -f -y"] = CmdResult(4, "", "errors left uncorrected")
    _, result = execute(plan, run)
    assert not result.ok
    assert "e2fsck -f -y /dev/vda2" in result.error
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_xfs_grow_runs_no_e2fsck():
    plan, run = _grow_plan("xfs")
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert not run.has("e2fsck")


def _kept_ntfs_plan(hiber: str, info: str = "You might resize at 4000000000 bytes\n"):
    plan = PartitionPlan(
        disk="vda",
        sector_size=512,
        new_table=False,
        targets=[Target("part:2", "ntfs", False, "/data")],
    )
    run = FakeRun(
        {"sgdisk -p": sgdisk_table([(1, 2048, 206847), (2, 206848, 20000000)]),
         "sh -c": hiber, "ntfsresize --info": info}
    )  # fmt: skip
    return plan, run


def test_kept_hibernated_ntfs_mount_refused_in_preflight():
    plan, run = _kept_ntfs_plan("hibr")
    _, result = execute(plan, run)
    assert not result.ok
    assert result.error == (
        "/dev/vda2 can't be mounted: Windows is hibernated (Fast Startup): "
        "shut Windows down fully first."
    )
    assert not [c for c in run.cmds if _is_destructive(c)]
    assert not run.has("mount ")


def test_kept_unclean_ntfs_mount_refused_in_preflight():
    plan, run = _kept_ntfs_plan("", "ERROR: NTFS is inconsistent. Run chkdsk /f on Windows\n")
    _, result = execute(plan, run)
    assert not result.ok
    assert "can't be mounted: Windows didn't shut down cleanly" in result.error
    assert not run.has("mount ")


def test_kept_clean_ntfs_mounted_with_the_kernel_ntfs3_driver():
    plan, run = _kept_ntfs_plan("")
    _, result = execute(plan, run)
    assert result.ok, result.error
    assert run.has("mount -t ntfs3 /dev/vda2 /mnt/data")


def test_release_previous_frees_what_the_last_run_left_open():
    plan, run = alongside_setup()
    ex, result = execute(plan, run, swap="file")
    assert result.ok, result.error
    run.calls.clear()
    run.responses["cat /proc/swaps"] = proc_swaps("/mnt/swapfile")
    run.responses["cryptsetup status"] = CmdResult(0, "active", "")
    ex._result.opened_mappers = ["cryptroot"]
    assert ex.release_previous() == ""
    joined = run.joined()
    assert "swapoff /mnt/swapfile" in joined
    assert "umount -R /mnt" in joined
    assert "cryptsetup close cryptroot" in joined
    assert not [c for c in run.cmds if _is_destructive(c)]


def test_release_previous_reports_a_failure():
    plan, run = alongside_setup()
    ex, _ = execute(plan, run)
    ex._result.opened_mappers = ["cryptroot"]
    run.responses["cryptsetup status"] = CmdResult(0, "active", "")
    run.fail = ("cryptsetup close",)
    assert "cryptsetup close cryptroot" in ex.release_previous()


def test_disk_changed_only_once_the_first_write_ran():
    plan, run = alongside_setup()
    run.responses["e2fsck -f -y"] = CmdResult(8, "", "bad")
    _, refused = execute(plan, run)
    assert not refused.ok and refused.disk_changed is False
    plan, run = alongside_setup()
    run.fail = ("sgdisk -a 1 -n",)
    _, failed = execute(plan, run)
    assert not failed.ok and failed.disk_changed is True

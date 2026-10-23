from fixtures import (
    TARGET,
    check_erase_layout,
    check_subvolumes,
    erase_install,
    part,
    sh,
    teardown_and_fsck,
)

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False
SUBVOLUMES = [{"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}]


def _check(disk, result):
    root = part(disk, 2)
    found = check_erase_layout(disk, result, "btrfs")
    check_subvolumes(found, SUBVOLUMES)
    assert found[TARGET]["source"].startswith("/dev/mapper/cryptroot"), found[TARGET]
    sh(f"cryptsetup isLuks {root}")
    dump = sh(f"cryptsetup luksDump {root}")
    pbkdfs = [line.split(":", 1)[1].strip() for line in dump.splitlines() if "PBKDF:" in line]
    assert pbkdfs and all(p == "pbkdf2" for p in pbkdfs), dump
    assert result.opened_mappers == ["cryptroot"], result
    assert result.luks_passphrases == {root.rsplit("/", 1)[-1]: "vmtest-passphrase"}
    root_entry = next(a for a in result.assignments if a["raw_path"] == root)
    assert root_entry["path"] == "/dev/mapper/cryptroot", root_entry


def run(ctx):
    from xen.partitioning.executor import PlanExecutor, run_subprocess

    disk = ctx.disk(0)
    executor = PlanExecutor(run_subprocess, TARGET, "grub")
    _plan, first = erase_install(disk, "btrfs", True, SUBVOLUMES, executor=executor)
    _check(disk, first)
    sh(f"touch {TARGET}/home/from-first-run")

    # a retry on the same executor must release the mounted target and the open mapper itself
    _plan, result = erase_install(disk, "btrfs", True, SUBVOLUMES, executor=executor)
    _check(disk, result)
    assert not sh(f"ls -A {TARGET}/home").strip(), "the retry did not reformat"
    teardown_and_fsck(disk, result, "btrfs")
    assert not sh("ls /dev/mapper").replace("control", "").strip()

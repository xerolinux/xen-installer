from fixtures import (
    check_erase_layout,
    check_subvolumes,
    erase_install,
    part,
    sh,
    teardown_and_fsck,
)

DISKS = [{"size_gib": 30, "logical_block_size": 4096}]
BOOT = False
SUBVOLUMES = [
    {"name": "@", "mountpoint": "/"},
    {"name": "@home", "mountpoint": "/home"},
    {"name": "@swap", "mountpoint": "/swap"},
]
MIB = 1024 * 1024


def run(ctx):
    disk = ctx.disk(0)
    assert sh(f"blockdev --getss {disk}").strip() == "4096"
    plan, result = erase_install(disk, "btrfs", subvolumes=SUBVOLUMES, swap="file")
    assert plan.sector_size == 4096
    found = check_erase_layout(disk, result, "btrfs")
    check_subvolumes(found, SUBVOLUMES)
    for n, create in enumerate(plan.creates, start=1):
        name = part(disk, n).rsplit("/", 1)[-1]
        start = int(sh(f"cat /sys/class/block/{name}/start")) * 512
        size = int(sh(f"cat /sys/class/block/{name}/size")) * 512
        assert start % MIB == 0, (name, start)
        assert start == create.start * 4096, (name, start, create)
        assert size == (create.end - create.start + 1) * 4096, (name, size, create)
    assert result.swap_devices == ["/mnt/swap/swapfile"], result
    assert "/mnt/swap/swapfile" in sh("swapon --show=NAME --noheadings --raw")
    teardown_and_fsck(disk, result, "btrfs")

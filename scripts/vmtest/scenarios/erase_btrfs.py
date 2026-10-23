from fixtures import (
    blkid,
    check_erase_layout,
    check_subvolumes,
    erase_install,
    part,
    sh,
    teardown_and_fsck,
)

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False
DEFAULT_SUBVOLUMES = [
    {"name": "@", "mountpoint": "/"},
    {"name": "@home", "mountpoint": "/home"},
    {"name": "@root", "mountpoint": "/root"},
    {"name": "@srv", "mountpoint": "/srv"},
    {"name": "@cache", "mountpoint": "/var/cache"},
    {"name": "@tmp", "mountpoint": "/var/tmp"},
    {"name": "@log", "mountpoint": "/var/log"},
    {"name": "@swap", "mountpoint": "/swap"},
]


def run(ctx):
    disk = ctx.disk(0)
    _plan, result = erase_install(disk, "btrfs", subvolumes=DEFAULT_SUBVOLUMES)
    found = check_erase_layout(disk, result, "btrfs")
    check_subvolumes(found, DEFAULT_SUBVOLUMES)
    assert blkid(part(disk, 2))["LABEL"] == "arch_root"
    listed = sh("btrfs subvolume list /mnt")
    names = sorted(line.split()[-1] for line in listed.splitlines())
    assert names == sorted(sv["name"] for sv in DEFAULT_SUBVOLUMES), names
    teardown_and_fsck(disk, result, "btrfs")

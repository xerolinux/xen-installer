from fixtures import check_erase_layout, check_subvolumes, erase_install, sh, teardown_and_fsck

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False
SUBVOLUMES = [
    {"name": "@", "mountpoint": "/"},
    {"name": "@home", "mountpoint": "/home"},
    {"name": "@var", "mountpoint": "/var"},
    {"name": "@varlog", "mountpoint": "/var/log"},
    {"name": "@opt", "mountpoint": "/opt"},
    {"name": "@snapshots", "mountpoint": "/.snapshots"},
]


def run(ctx):
    disk = ctx.disk(0)
    _plan, result = erase_install(disk, "btrfs", subvolumes=SUBVOLUMES)
    found = check_erase_layout(disk, result, "btrfs")
    check_subvolumes(found, SUBVOLUMES)
    assert not any(t.endswith(("/root", "/srv", "/swap")) for t in found), sorted(found)
    names = sorted(line.split()[-1] for line in sh("btrfs subvolume list /mnt").splitlines())
    assert names == sorted(sv["name"] for sv in SUBVOLUMES), names
    teardown_and_fsck(disk, result, "btrfs")

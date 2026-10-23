from fixtures import (
    GIB,
    TARGET,
    alongside_selections,
    check_subvolumes,
    esp_and_linux,
    fsck,
    install,
    mounts,
    part,
    raw_sha,
    release,
    tree_of,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False
SUBVOLUMES = [{"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}]


def run(ctx):
    disk = ctx.disk(0)
    esp_and_linux(disk, "btrfs", 400)
    esp, old = part(disk, 1), part(disk, 2)
    tree, esp_sha = tree_of(old, "btrfs"), raw_sha(esp)

    selections = install(
        alongside_selections(disk, 2, 12 * GIB, filesystem="btrfs", btrfs_subvolumes=SUBVOLUMES)
    )
    root = part(disk, 3)
    found = mounts()
    assert found[TARGET]["source"].startswith(root), found
    check_subvolumes(found, SUBVOLUMES)
    assert found[f"{TARGET}/boot/efi"]["source"] == esp, found
    assert selections["filesystem"] == "btrfs"
    release(selections)

    fsck(old, "btrfs")
    assert tree_of(old, "btrfs") == tree, "old btrfs data changed"
    assert raw_sha(esp) == esp_sha
    fsck(root, "btrfs")

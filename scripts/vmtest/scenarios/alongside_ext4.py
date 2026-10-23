from fixtures import (
    GIB,
    TARGET,
    alongside_selections,
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


def run(ctx):
    disk = ctx.disk(0)
    esp_and_linux(disk, "ext4", 400)
    esp, old = part(disk, 1), part(disk, 2)
    tree, esp_sha = tree_of(old, "ext4"), raw_sha(esp)

    selections = install(alongside_selections(disk, 2, 12 * GIB))
    root = part(disk, 3)
    found = mounts()
    assert found[TARGET]["source"] == root and found[TARGET]["fstype"] == "ext4", found
    assert found[f"{TARGET}/boot/efi"]["source"] == esp, found
    release(selections)

    fsck(old, "ext4")
    assert tree_of(old, "ext4") == tree, "old ext4 data changed"
    assert raw_sha(esp) == esp_sha
    fsck(root, "ext4")

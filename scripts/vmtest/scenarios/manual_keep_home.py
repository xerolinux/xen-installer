from fixtures import (
    TARGET,
    checksum_tree,
    esp_and_linux,
    existing_row,
    fsck,
    install,
    job_selections,
    largest_free_mib,
    mounts,
    new_row,
    part,
    release,
    tree_of,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    esp_and_linux(disk, "ext4", 300, linux_mib=5 * 1024)
    home = part(disk, 2)
    tree = tree_of(home, "ext4")
    start, size = largest_free_mib(disk)

    rows = [
        existing_row(1, mountpoint="/boot/efi"),
        existing_row(2, mountpoint="/home"),
        new_row("root", start, size, "ext4", "/"),
    ]
    selections = install(job_selections(disk, "manual", manual_rows=rows))
    found = mounts()
    assert found[TARGET]["source"] == part(disk, 3), found
    assert found[f"{TARGET}/home"]["source"] == home, found
    assert checksum_tree(f"{TARGET}/home") == tree, "/home was not mounted with its data"
    entry = next(a for a in selections["partition_assignments"] if a["mountpoint"] == "/home")
    assert entry["format"] is False, entry
    release(selections)

    fsck(home, "ext4")
    assert tree_of(home, "ext4") == tree
    fsck(part(disk, 3), "ext4")

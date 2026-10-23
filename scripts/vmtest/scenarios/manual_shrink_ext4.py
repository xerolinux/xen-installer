from fixtures import (
    GIB,
    MIB_SECTORS,
    TARGET,
    esp_and_linux,
    existing_row,
    fsck,
    install,
    job_selections,
    mounts,
    new_row,
    part,
    release,
    sh,
    tree_of,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    from xen.utils.disk_detector import list_disks

    disk = ctx.disk(0)
    esp_and_linux(disk, "ext4", 400)
    esp, old = part(disk, 1), part(disk, 2)
    tree = tree_of(old, "ext4")
    (target,) = [d for d in list_disks() if d["name"] == "vda"]
    start = int(target["partitions"][1]["startSector"])
    new_end = start + 12 * GIB // 512 - 1
    root_start_mib = (new_end + 1) // MIB_SECTORS
    root_mib = (int(target["partitions"][1]["endSector"]) + 1) // MIB_SECTORS - root_start_mib

    rows = [
        existing_row(1, mountpoint="/boot/efi"),
        existing_row(2, newEnd=new_end),
        new_row("root", root_start_mib, root_mib, "ext4", "/"),
    ]
    selections = install(job_selections(disk, "manual", manual_rows=rows))
    root = part(disk, 3)
    assert mounts()[TARGET]["source"] == root
    release(selections)

    assert int(sh(f"blockdev --getsize64 {old}")) == 12 * GIB
    fsck(old, "ext4")
    assert tree_of(old, "ext4") == tree, "shrunk ext4 data changed"
    fsck(root, "ext4")
    fsck(esp, "vfat")

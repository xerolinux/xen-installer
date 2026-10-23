from fixtures import TARGET, fsck, install, job_selections, mounts, new_row, part, release

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    rows = [
        new_row("esp", 1, 512, "fat32", "/boot/efi"),
        new_row("root", 513, 20 * 1024, "xfs", "/"),
    ]
    selections = install(job_selections(disk, "manual", manual_rows=rows, manual_new_table=True))
    found = mounts()
    assert found[TARGET]["source"] == part(disk, 2) and found[TARGET]["fstype"] == "xfs", found
    assert found[f"{TARGET}/boot/efi"]["fstype"] == "vfat", found
    assert selections["filesystem"] == "xfs"
    release(selections)
    fsck(part(disk, 2), "xfs")
    fsck(part(disk, 1), "vfat")

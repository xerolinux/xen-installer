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
GROWN = 10 * GIB


def run(ctx):
    from xen.utils.disk_detector import list_disks

    disk = ctx.disk(0)
    esp_and_linux(disk, "ext4", 300, linux_mib=5 * 1024)
    home = part(disk, 2)
    # mounted after its last full check: offline resize2fs refuses this without e2fsck -f
    sh(f"sleep 2; mount {home} /mnt && touch /mnt/mounted-again && umount /mnt")
    tree = tree_of(home, "ext4")
    (target,) = [d for d in list_disks() if d["name"] == "vda"]
    start = int(target["partitions"][1]["startSector"])
    new_end = start + GROWN // 512 - 1
    root_start_mib = (new_end + 1) // MIB_SECTORS
    root_mib = (int(target["sizeSectors"]) - 34) // MIB_SECTORS - root_start_mib

    rows = [
        existing_row(1, mountpoint="/boot/efi"),
        existing_row(2, mountpoint="/home", newEnd=new_end),
        new_row("root", root_start_mib, root_mib, "ext4", "/"),
    ]
    selections = install(job_selections(disk, "manual", manual_rows=rows))
    assert mounts()[f"{TARGET}/home"]["source"] == home
    release(selections)

    assert int(sh(f"blockdev --getsize64 {home}")) == GROWN
    header = sh(f"dumpe2fs -h {home}")
    fields = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
    fs_bytes = int(fields["Block count"]) * int(fields["Block size"])
    assert fs_bytes == GROWN, f"the file system was not grown: {fs_bytes} bytes"
    fsck(home, "ext4")
    assert tree_of(home, "ext4") == tree, "grown ext4 data changed"
    fsck(part(disk, 3), "ext4")

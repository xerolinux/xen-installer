from fixtures import (
    TARGET,
    check_subvolumes,
    fsck,
    install,
    job_selections,
    linux_ext4,
    mounts,
    new_row,
    part,
    release,
    sh,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False
SUBVOLUMES = [{"name": "@", "mountpoint": "/"}, {"name": "@log", "mountpoint": "/var/log"}]


def run(ctx):
    disk = ctx.disk(0)
    linux_ext4(disk, 50)
    rows = [
        new_row("esp", 1, 512, "fat32", "/boot/efi"),
        new_row("root", 513, 20 * 1024, "btrfs", "/", subvolumes=SUBVOLUMES),
        new_row("home", 513 + 20 * 1024, 10 * 1024, "ext4", "/home"),
        new_row("swap", 513 + 30 * 1024, 2048, "swap", "swap"),
    ]
    selections = install(
        job_selections(disk, "manual", manual_rows=rows, manual_new_table=True,
                       swap_strategy="file")
    )  # fmt: skip
    esp, root, home, swap = (part(disk, n) for n in (1, 2, 3, 4))
    found = mounts()
    check_subvolumes(found, SUBVOLUMES)
    assert found[TARGET]["source"].startswith(root), found
    assert found[f"{TARGET}/boot/efi"]["source"] == esp, found
    assert found[f"{TARGET}/home"]["source"] == home, found
    assert found[f"{TARGET}/home"]["fstype"] == "ext4", found
    assert swap in sh("cat /proc/swaps"), "swap partition not active"
    assert sh(f"blkid -p -o value -s PART_ENTRY_TYPE {swap}").strip() == (
        "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"
    )
    assert selections["swap_strategy"] == "none"
    assert selections["target_swap_devices"] == [swap]
    assert selections["esp_formatted"] is True
    assert not sh(f"ls {TARGET}/swapfile", check=False).strip()
    release(selections)
    fsck(root, "btrfs")
    fsck(home, "ext4")
    fsck(esp, "vfat")

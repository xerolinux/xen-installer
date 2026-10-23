from fixtures import check_erase_layout, erase_install, sh, teardown_and_fsck

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    _plan, result = erase_install(disk, "ext4", swap="file")
    check_erase_layout(disk, result, "ext4")
    assert result.swap_devices == ["/mnt/swapfile"], result
    active = sh("swapon --show=NAME,TYPE --noheadings --raw")
    assert "/mnt/swapfile file" in active, active
    assert sh("stat -c %a /mnt/swapfile").strip() == "600"
    teardown_and_fsck(disk, result, "ext4")

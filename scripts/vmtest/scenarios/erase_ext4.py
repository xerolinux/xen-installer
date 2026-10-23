from fixtures import check_erase_layout, erase_install, teardown_and_fsck

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    _plan, result = erase_install(disk, "ext4")
    check_erase_layout(disk, result, "ext4")
    teardown_and_fsck(disk, result, "ext4")

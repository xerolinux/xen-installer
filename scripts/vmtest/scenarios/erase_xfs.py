from fixtures import check_erase_layout, erase_install, teardown_and_fsck

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    _plan, result = erase_install(disk, "xfs")
    check_erase_layout(disk, result, "xfs")
    teardown_and_fsck(disk, result, "xfs")

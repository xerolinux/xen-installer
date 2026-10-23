from fixtures import GIB, alongside_selections, gpt_backup_sha, part, raw_sha, run_job, xfs_only

DISKS = [{"size_gib": 20, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    from xen.utils.disk_detector import list_disks

    disk = ctx.disk(0)
    xfs_only(disk)
    xfs = part(disk, 1)
    table, data = gpt_backup_sha(disk), raw_sha(xfs)

    (target,) = [d for d in list_disks(probe=True) if d["name"] == "vda"]
    (only,) = target["partitions"]
    assert only["fstype"] == "xfs" and only["resizeBlockedReason"] == "XFS can't be shrunk", only
    assert not [p for p in target["partitions"] if p["canShrink"]], "an XFS-only disk has a candidate"

    result, context = run_job(alongside_selections(disk, 1, 5 * GIB))
    assert not result.success
    assert f"{xfs} can't be resized: XFS can't be shrunk." in result.message, result.message
    assert "partition_assignments" not in context.selections
    assert gpt_backup_sha(disk) == table and raw_sha(xfs) == data, "the disk changed"

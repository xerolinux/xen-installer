from fixtures import (
    GIB,
    TARGET,
    alongside_selections,
    gpt_backup_sha,
    hibernated_ntfs,
    part,
    raw_sha,
    run_job,
    sh,
)

DISKS = [{"size_gib": 4, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    from xen.partitioning import fscaps
    from xen.partitioning.executor import PlanExecutor, run_subprocess
    from xen.partitioning.plan import Create, PartitionPlan, Resize, Target
    from xen.utils.disk_detector import LINUX_TYPE_GUID, list_disks

    disk = ctx.disk(0)
    hibernated_ntfs(disk)
    ntfs = part(disk, 1)
    table, data = gpt_backup_sha(disk), raw_sha(ntfs)

    (target,) = [d for d in list_disks(probe=True) if d["name"] == "vda"]
    (scanned,) = target["partitions"]
    assert scanned["canShrink"] is False, scanned
    assert scanned["resizeBlockedReason"] == fscaps.REASON_HIBERNATED, scanned

    result, _ = run_job(alongside_selections(disk, 1, 1 * GIB))
    assert not result.success
    assert f"{ntfs} can't be resized: {fscaps.REASON_HIBERNATED}." in result.message, result.message

    # Forced past validate_plan: the executor's own preflight must still refuse.
    start = int(scanned["startSector"])
    new_end = start + GIB // 512 - 1
    root_end = int(target["sizeSectors"]) - 34 - 2048
    root = Create("root", new_end + 1, root_end, LINUX_TYPE_GUID, "XeroLinux root")
    plan = PartitionPlan(
        disk="vda",
        sector_size=512,
        new_table=False,
        resizes=[Resize(1, new_end, "ntfs", True)],
        creates=[root],
        targets=[Target("new:root", "ext4", True, "/")],
    )
    forced = PlanExecutor(run_subprocess, TARGET, "grub").execute(plan, "", "none")
    assert not forced.ok
    assert forced.error == f"{ntfs} can't be shrunk: {fscaps.REASON_HIBERNATED}.", forced.error
    assert gpt_backup_sha(disk) == table, "the partition table changed"
    assert raw_sha(ntfs) == data, "the NTFS partition changed"
    assert not sh("findmnt -rn /run/xen-btrfs-scratch", check=False).strip()

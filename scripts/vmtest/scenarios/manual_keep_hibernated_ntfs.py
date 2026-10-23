from fixtures import (
    TARGET,
    esp_and_hibernated_ntfs,
    existing_row,
    gpt_backup_sha,
    job_selections,
    largest_free_mib,
    new_row,
    part,
    raw_sha,
    run_job,
    sh,
)

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    from xen.partitioning import fscaps
    from xen.partitioning.executor import PlanExecutor, run_subprocess
    from xen.partitioning.plan import build_manual_plan
    from xen.utils.disk_detector import list_disks

    disk = ctx.disk(0)
    esp_and_hibernated_ntfs(disk)
    ntfs = part(disk, 2)
    table, data = gpt_backup_sha(disk), raw_sha(ntfs)

    (target,) = [d for d in list_disks(probe=True) if d["name"] == "vda"]
    scanned = target["partitions"][1]
    assert scanned["resizeBlockedReason"] == fscaps.REASON_HIBERNATED, scanned

    start, size = largest_free_mib(disk)
    rows = [
        existing_row(1, mountpoint="/boot/efi"),
        existing_row(2, mountpoint="/data"),
        new_row("root", start, size, "ext4", "/"),
    ]
    refusal = f"{ntfs} can't be mounted: {fscaps.REASON_HIBERNATED}."
    result, _ = run_job(job_selections(disk, "manual", manual_rows=rows))
    assert not result.success and refusal in result.message, result.message

    # Forced past validate_plan: the executor's own preflight must still refuse.
    plan = build_manual_plan(target, rows, new_table=False)
    forced = PlanExecutor(run_subprocess, TARGET, "grub").execute(plan, "", "none")
    assert not forced.ok and forced.error == refusal, forced.error
    assert not forced.disk_changed
    assert gpt_backup_sha(disk) == table, "the partition table changed"
    assert raw_sha(ntfs) == data, "the NTFS partition changed"
    assert not sh(f"findmnt -rn {TARGET}", check=False).strip()

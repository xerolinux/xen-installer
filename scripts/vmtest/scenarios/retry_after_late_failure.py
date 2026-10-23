from fixtures import JOB_CONFIG, PASSPHRASE, TARGET, job_selections, mounts, part, release, sh

DISKS = [{"size_gib": 30, "logical_block_size": 512}]
BOOT = False


def _partition(job, selections):
    from xen.jobs.base import JobContext

    context = JobContext(selections=dict(selections), target_root=TARGET)
    validation = job.validate(context)
    assert validation.success, validation.message
    result = job.run(context)
    assert result.success, result.message
    return context.selections


def run(ctx):
    from xen.jobs.partition import PartitionJob

    disk = ctx.disk(0)
    selections = job_selections(
        disk, "auto", encryption=True, encryption_passphrase=PASSPHRASE, swap_strategy="file"
    )
    job = PartitionJob(dict(JOB_CONFIG))
    first = _partition(job, selections)
    assert mounts()[TARGET]["source"].startswith("/dev/mapper/cryptroot")

    # what the install job leaves behind when a later job fails: chroot mounts and a file
    for api in ("proc", "sys", "dev"):
        sh(f"mkdir -p {TARGET}/{api} && mount --bind /{api} {TARGET}/{api}")
    sh(f"touch {TARGET}/from-first-attempt")
    assert first["target_swap_devices"], first

    again = _partition(job, selections)
    found = mounts()
    assert found[TARGET]["source"].startswith("/dev/mapper/cryptroot"), found
    assert found[f"{TARGET}/boot/efi"]["source"] == part(disk, 1), found
    assert not any(m.startswith(f"{TARGET}/proc") for m in found), found
    assert not sh(f"ls {TARGET}/from-first-attempt", check=False).strip()
    assert sh("cryptsetup status cryptroot", check=False).strip()
    release(again)
    assert not sh("ls /dev/mapper").replace("control", "").strip()

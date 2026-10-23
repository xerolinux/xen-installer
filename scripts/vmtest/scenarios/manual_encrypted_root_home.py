from fixtures import (
    PASSPHRASE,
    TARGET,
    fsck,
    install,
    job_selections,
    mounts,
    new_row,
    part,
    pbkdfs,
    sh,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    rows = [
        new_row("esp", 1, 512, "fat32", "/boot/efi"),
        new_row("root", 513, 20 * 1024, "ext4", "/", encrypt=True),
        new_row("home", 513 + 20 * 1024, 10 * 1024, "ext4", "/home", encrypt=True),
        new_row("swap", 513 + 30 * 1024, 1024, "swap", "swap", encrypt=True),
    ]
    selections = install(
        job_selections(disk, "manual", manual_rows=rows, manual_new_table=True,
                       manual_root_passphrase=PASSPHRASE)
    )  # fmt: skip
    root, home = part(disk, 2), part(disk, 3)
    found = mounts()
    assert found[TARGET]["source"] == "/dev/mapper/cryptroot", found
    assert found[f"{TARGET}/home"]["source"] == "/dev/mapper/crypthome", found
    for raw in (root, home):
        sh(f"cryptsetup isLuks {raw}")
        assert pbkdfs(raw) and set(pbkdfs(raw)) == {"pbkdf2"}, raw
    keys = selections["luks_passphrases"]
    assert keys["vda2"] == PASSPHRASE and keys["vda3"] and keys["vda3"] != PASSPHRASE
    assert selections["encryption"] is True and selections["root_partition"] == root
    assert selections["opened_mappers"] == ["cryptroot", "crypthome", "cryptswap"]
    status = sh("cryptsetup status cryptswap")
    assert "PLAIN" in status and "aes-xts-plain64" in status and "keysize: 256 " in status, status
    swap_dm = sh("readlink -f /dev/mapper/cryptswap").strip()
    assert swap_dm in sh("cat /proc/swaps").split(), "the encrypted swap is not active"
    assert "vda4" not in keys

    from xen.jobs.base import JobContext
    from xen.jobs.finished import FinishedJob
    from xen.jobs.systemconfig import SystemConfigJob

    # No squashfs install here, so only the fstab/crypttab steps run, against the mounted target.
    sh(f"mkdir -p {TARGET}/etc")
    sysconfig = SystemConfigJob()
    assert sysconfig._generate_fstab(TARGET, selections).success
    crypttab_result = sysconfig._generate_crypttab(TARGET, selections)
    assert crypttab_result.success, crypttab_result.message
    fstab = sh(f"cat {TARGET}/etc/fstab")
    entries = [l.split() for l in fstab.splitlines() if l.strip() and not l.startswith("#")]
    assert [e[0] for e in entries if e[2] == "swap"] == ["/dev/mapper/cryptswap"], fstab
    assert "/dev/mapper/cryptswap none swap defaults 0 0" in fstab, fstab
    crypttab = {
        l.split()[0]: l.split()[1:] for l in sh(f"cat {TARGET}/etc/crypttab").splitlines()
        if l.strip() and not l.startswith("#")
    }  # fmt: skip
    uuid = {d: sh(f"blkid -s UUID -o value {d}").strip() for d in (root, home)}
    swap_partuuid = sh(f"blkid -s PARTUUID -o value {part(disk, 4)}").strip()
    keyfile = "/etc/cryptsetup-keys.d/crypthome.key"
    assert crypttab == {
        "cryptroot": [f"UUID={uuid[root]}", "none", "luks"],
        "crypthome": [f"UUID={uuid[home]}", keyfile, "luks"],
        "cryptswap": [f"PARTUUID={swap_partuuid}", "/dev/urandom",
                      "swap,cipher=aes-xts-plain64,size=256"],
    }, crypttab  # fmt: skip
    sh(f"cryptsetup open --test-passphrase --key-file {TARGET}{keyfile} {home}")

    cleanup = FinishedJob()._cleanup_mounts(JobContext(selections=selections, target_root=TARGET))
    assert cleanup.success, cleanup.message
    assert not sh(f"findmnt -rn {TARGET}", check=False).strip(), "target still mounted"
    assert swap_dm not in sh("cat /proc/swaps").split(), "swap still active"
    assert not sh("ls /dev/mapper").replace("control", "").strip(), "a mapper is still open"

    sh(f"printf %s {PASSPHRASE} | cryptsetup open --test-passphrase {root} --key-file -")
    sh(f"printf %s '{keys['vda3']}' | cryptsetup open --test-passphrase {home} --key-file -")
    sh(f"printf %s {PASSPHRASE} | cryptsetup open {root} checkroot --key-file -")
    try:
        fsck("/dev/mapper/checkroot", "ext4")
    finally:
        sh("cryptsetup close checkroot")

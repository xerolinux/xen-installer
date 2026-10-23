from fixtures import check_installed, part, pbkdfs, sh

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
GUI = True
BOOT_PASSPHRASE = "vmtest-luks-passphrase"
MAPPER = "xencheck"


def drive(g):
    from gui import choose, click_next, fill_passphrase, pick_disk

    pick_disk(g, "vda")
    choose(g, "xfs", "btrfs")
    g.click_left("Enable encryption (LUKS)", 19)
    fill_passphrase(g, BOOT_PASSPHRASE)
    g.wait("Passphrases match", 10)
    g.save("partition")
    click_next(g)


def verify(ctx, state):
    root = part(ctx.disk(0), 2)
    kdfs = pbkdfs(root)
    assert len(kdfs) == 2 and "pbkdf2" in kdfs, kdfs
    sh(f"printf '%s' '{BOOT_PASSPHRASE}' | cryptsetup open --key-file=- {root} {MAPPER}")
    try:
        grub, fstab = check_installed(f"/dev/mapper/{MAPPER}", "btrfs", "ro,subvol=@")
    finally:
        sh(f"cryptsetup close {MAPPER}")
    assert "cryptdevice=UUID=" in grub and "cryptkey=rootfs:" in grub, grub
    assert fstab["/"] == "btrfs", fstab

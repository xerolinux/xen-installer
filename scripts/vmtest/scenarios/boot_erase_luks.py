from fixtures import boot_selections, check_installed, part, pbkdfs, run_engine, sh

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = True
BOOT_PASSPHRASE = "vmtest-luks-passphrase"
MAPPER = "xencheck"


def run(ctx):
    disk = ctx.disk(0)
    selections = run_engine(
        boot_selections(
            disk, "auto", "btrfs", True, encryption=True, encryption_passphrase=BOOT_PASSPHRASE
        )
    )
    root = part(disk, 2)
    assert selections["root_partition"] == root
    assert selections["luks_keyfile_path"] == "/etc/cryptsetup-keys.d/root.key"
    kdfs = pbkdfs(root)
    assert len(kdfs) == 2 and "pbkdf2" in kdfs, kdfs
    sh(f"printf '%s' '{BOOT_PASSPHRASE}' | cryptsetup open --key-file=- {root} {MAPPER}")
    try:
        grub, fstab = check_installed(f"/dev/mapper/{MAPPER}", "btrfs", "ro,subvol=@")
    finally:
        sh(f"cryptsetup close {MAPPER}")
    assert "cryptdevice=UUID=" in grub and "cryptkey=rootfs:" in grub, grub
    assert fstab["/"] == "btrfs", fstab
    assert "XeroLinux" in sh("efibootmgr")

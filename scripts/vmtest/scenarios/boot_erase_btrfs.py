from fixtures import boot_selections, check_installed, part, run_engine, sh, tree_of

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = True


def run(ctx):
    disk = ctx.disk(0)
    # validate_plan refuses a Btrfs root offline: the packages job installs snapper and grub-btrfs
    selections = run_engine(boot_selections(disk, "auto", "btrfs", True))
    esp, root = part(disk, 1), part(disk, 2)
    assert selections["efi_partition"] == esp and selections["root_partition"] == root
    assert selections["esp_formatted"] is True
    grub, _fstab = check_installed(root, "btrfs", "ro,subvol=@")
    assert "rootflags=subvol=@ " in grub, "grub.cfg does not boot the @ subvolume"
    files = tree_of(esp, "vfat")
    assert "EFI/XeroLinux/grubx64.efi" in files, sorted(files)
    assert "EFI/BOOT/BOOTX64.EFI" in files, sorted(files)
    assert "XeroLinux" in sh("efibootmgr")

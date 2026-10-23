from fixtures import (
    boot_selections,
    check_installed,
    linux_ext4,
    new_row,
    part,
    run_engine,
    sh,
    tree_of,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = True
ROOT_MIB = 28 * 1024
HOME_MIB = 8 * 1024


def run(ctx):
    disk = ctx.disk(0)
    linux_ext4(disk, 50)
    rows = [
        new_row("esp", 1, 2000, "fat32", "/boot/efi"),
        new_row("root", 2001, ROOT_MIB, "xfs", "/"),
        new_row("home", 2001 + ROOT_MIB, HOME_MIB, "ext4", "/home"),
        new_row("swap", 2001 + ROOT_MIB + HOME_MIB, 1024, "swap", "swap"),
    ]
    selections = run_engine(
        boot_selections(disk, "manual", "xfs", False, manual_rows=rows, manual_new_table=True)
    )
    esp, root = part(disk, 1), part(disk, 2)
    assert selections["efi_partition"] == esp and selections["root_partition"] == root
    assert selections["esp_formatted"] is True
    grub, fstab = check_installed(root, "xfs")
    assert "resume=" in grub, "the swap partition is not the resume device"
    assert fstab["/"] == "xfs" and fstab["/home"] == "ext4" and fstab["none"] == "swap", fstab
    files = tree_of(esp, "vfat")
    assert "EFI/XeroLinux/grubx64.efi" in files and "EFI/BOOT/BOOTX64.EFI" in files, files
    assert "XeroLinux" in sh("efibootmgr")

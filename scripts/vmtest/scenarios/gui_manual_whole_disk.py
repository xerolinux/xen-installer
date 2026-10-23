from fixtures import check_installed, part, sh, tree_of

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
GUI = True


def drive(g):
    from gui import MANUAL_TABLE, click_next, create_partition, dialog_ok, pick_disk

    pick_disk(g, "vda")
    g.click_left("Manual partitioning", 70)
    click_next(g)
    g.wait("Revert All Changes", 30)
    g.save("manual-editor")
    g.click_text("New Partition Table", MANUAL_TABLE)
    g.wait("This removes every partition", 10)
    g.save("manual-new-table")
    dialog_ok(g, None)
    g.wait("This removes every partition", 10, gone=True)
    g.wait("doesn't use a GPT partition table", 10, gone=True)
    create_partition(g, 2000, "fat32", "/boot/efi")
    create_partition(g, None, "btrfs", "/")
    g.wait("38.0 GiB", 10, MANUAL_TABLE)
    g.save("manual-layout")
    click_next(g)


def verify(ctx, state):
    disk = ctx.disk(0)
    esp, root = part(disk, 1), part(disk, 2)
    assert int(sh(f"blockdev --getsize64 {esp}")) == 2000 * 1024 * 1024
    grub, fstab = check_installed(root, "btrfs", "ro,subvol=@")
    assert fstab["/"] == "btrfs", fstab
    files = tree_of(esp, "vfat")
    assert "EFI/XeroLinux/grubx64.efi" in files, sorted(files)

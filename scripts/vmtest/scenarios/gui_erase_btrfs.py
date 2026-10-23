from fixtures import check_installed, part

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
GUI = True


def drive(g):
    from gui import choose, click_next, pick_disk

    pick_disk(g, "vda")
    g.wait("Erase disk")
    choose(g, "xfs", "btrfs")
    g.wait("Subvolumes")
    g.click_text("Edit")
    g.wait("Btrfs subvolumes", 10)
    g.save("subvolumes")
    g.click_text("Cancel")
    g.wait("Btrfs subvolumes", 10, gone=True)
    g.save("partition")
    click_next(g)


def verify(ctx, state):
    disk = ctx.disk(0)
    grub, fstab = check_installed(part(disk, 2), "btrfs", "ro,subvol=@")
    assert "rootflags=subvol=@ " in grub, "grub.cfg does not boot the @ subvolume"
    assert fstab["/"] == "btrfs" and fstab["/home"] == "btrfs", fstab

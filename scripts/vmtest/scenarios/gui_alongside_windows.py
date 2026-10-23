from fixtures import check_installed, part, sh, tree_of, windows_like

DISKS = [{"size_gib": 60, "logical_block_size": 512}]
GUI = True


def prepare(ctx):
    disk = ctx.disk(0)
    windows_like(disk, 500)
    return {"esp": tree_of(part(disk, 1), "vfat"), "ntfs": tree_of(part(disk, 3), "ntfs3")}


def drive(g):
    from gui import click_next, pick_disk

    pick_disk(g, "vda")
    g.click_left("Install alongside", 70)
    g.wait("will be shrunk to", 30)
    g.wait("EFI system partition", 10)
    g.save("partition")
    click_next(g)


def verify(ctx, state):
    disk = ctx.disk(0)
    esp, data = part(disk, 1), part(disk, 3)
    grub, _fstab = check_installed(part(disk, 5), "xfs")
    assert "Windows" in grub, "os-prober did not add Windows to grub.cfg"
    after = tree_of(esp, "vfat")
    assert all(after.get(f) == digest for f, digest in state["esp"].items()), "Windows ESP files changed"
    added = sorted(set(after) - set(state["esp"]))
    assert added and all(f.startswith("EFI/XeroLinux/") for f in added), added
    sh(f"ntfsresize --check --no-progress-bar {data}")
    assert tree_of(data, "ntfs3") == state["ntfs"], "NTFS data changed"

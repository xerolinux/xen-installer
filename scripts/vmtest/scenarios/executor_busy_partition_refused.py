from fixtures import alongside_plan, checksum_tree, part, run_plan, sh, windows_like

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False
GIB = 1024**3


def _edges(disk):
    head = sh(f"head -c 1M {disk} | sha256sum").split()[0]
    tail = sh(f"tail -c 1M {disk} | sha256sum").split()[0]
    return head, tail


def run(ctx):
    disk = ctx.disk(0)
    windows_like(disk, 300)
    data = part(disk, 3)
    plan, errors = alongside_plan(disk, 3, 12 * GIB)
    assert not errors, errors

    sh(f"mkdir -p /mnt/busy && mount -t ntfs3 -o ro {data} /mnt/busy")
    tree = checksum_tree("/mnt/busy")
    sh(f"sgdisk --backup=/root/before.gpt {disk}")
    edges = _edges(disk)

    result = run_plan(plan)
    assert not result.ok, "the executor shrank a mounted partition"
    expected = f"{data} is in use (mounted at /mnt/busy): close it first."
    assert result.error == expected, result.error

    sh(f"sgdisk --backup=/root/after.gpt {disk}")
    sh("cmp /root/before.gpt /root/after.gpt")
    assert _edges(disk) == edges
    assert sh("findmnt -rno SOURCE /mnt/busy").strip() == data
    assert checksum_tree("/mnt/busy") == tree
    sh("umount /mnt/busy")
    assert not sh("findmnt -rn /run/xen-btrfs-scratch", check=False).strip()
    print("refused:", result.error)

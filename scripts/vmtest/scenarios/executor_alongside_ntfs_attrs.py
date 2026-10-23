from fixtures import (
    TARGET,
    alongside_plan,
    checksum_tree,
    gpt_identity,
    part,
    raw_sha,
    run_plan,
    sh,
    windows_like,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False
GIB = 1024**3


def _ntfs_tree(dev):
    sh(f"mkdir -p /tmp/ntfs && mount -t ntfs3 -o ro {dev} /tmp/ntfs")
    try:
        return checksum_tree("/tmp/ntfs")
    finally:
        sh("umount /tmp/ntfs")


def run(ctx):
    disk = ctx.disk(0)
    windows_like(disk, 500)
    sh(f"sgdisk -A 3:set:0 -A 3:set:63 {disk} && partprobe {disk} && udevadm settle")
    data, esp = part(disk, 3), part(disk, 1)
    before = gpt_identity(disk, 3)
    assert before["Attribute flags"] == "8000000000000001", before
    tree, esp_sha = _ntfs_tree(data), raw_sha(esp)

    plan, errors = alongside_plan(disk, 3, 12 * GIB)
    assert not errors, errors
    result = run_plan(plan)
    assert result.ok, result.error
    assert result.root_partition == part(disk, 5) and result.efi_partition == esp, result
    assert not result.esp_formatted
    sh(f"umount -R {TARGET}")

    assert gpt_identity(disk, 3) == before, (gpt_identity(disk, 3), before)
    assert int(sh(f"blockdev --getsize64 {data}")) == 12 * GIB
    assert _ntfs_tree(data) == tree
    assert raw_sha(esp) == esp_sha, "the reused ESP changed"
    sh(f"fsck.ext4 -n -f {result.root_partition}")

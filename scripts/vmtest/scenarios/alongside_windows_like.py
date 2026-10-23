from fixtures import (
    GIB,
    TARGET,
    alongside_selections,
    fsck,
    gpt_identity,
    install,
    mounts,
    part,
    raw_sha,
    release,
    sh,
    tree_of,
    windows_like,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    windows_like(disk, 500)
    esp, data = part(disk, 1), part(disk, 3)
    tree, esp_sha = tree_of(data, "ntfs3"), raw_sha(esp)
    kept = {n: gpt_identity(disk, n) for n in (1, 2, 4)}

    selections = install(alongside_selections(disk, 3, 12 * GIB))
    root = part(disk, 5)
    found = mounts()
    assert found[TARGET]["source"] == root and found[TARGET]["fstype"] == "ext4", found
    assert found[f"{TARGET}/boot/efi"]["source"] == esp, found
    assert selections["root_partition"] == root and selections["efi_partition"] == esp
    assert selections["esp_formatted"] is False and selections["encryption"] is False
    release(selections)

    assert int(sh(f"blockdev --getsize64 {data}")) == 12 * GIB
    sh(f"ntfsresize --check --no-progress-bar {data}")
    assert tree_of(data, "ntfs3") == tree, "NTFS data changed"
    assert raw_sha(esp) == esp_sha, "the reused ESP changed"
    assert {n: gpt_identity(disk, n) for n in (1, 2, 4)} == kept
    fsck(root, "ext4")

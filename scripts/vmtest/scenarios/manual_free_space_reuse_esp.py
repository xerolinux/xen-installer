from fixtures import (
    TARGET,
    existing_row,
    fsck,
    gpt_identity,
    install,
    job_selections,
    largest_free_mib,
    mounts,
    new_row,
    part,
    raw_sha,
    release,
    tree_of,
    windows_like,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    windows_like(disk, 300, free_mib=16 * 1024)
    esp, data = part(disk, 1), part(disk, 3)
    tree, esp_sha = tree_of(data, "ntfs3"), raw_sha(esp)
    kept = {n: gpt_identity(disk, n) for n in (1, 2, 3, 4)}
    start, size = largest_free_mib(disk)
    assert size >= 15 * 1024, (start, size)

    rows = [existing_row(1, mountpoint="/boot/efi"), new_row("root", start, size, "ext4", "/")]
    selections = install(job_selections(disk, "manual", manual_rows=rows))
    root = part(disk, 5)
    found = mounts()
    assert found[TARGET]["source"] == root, found
    assert found[f"{TARGET}/boot/efi"]["source"] == esp, found
    assert selections["esp_formatted"] is False
    release(selections)

    assert raw_sha(esp) == esp_sha, "the kept ESP changed"
    assert {n: gpt_identity(disk, n) for n in (1, 2, 3, 4)} == kept
    assert tree_of(data, "ntfs3") == tree
    fsck(root, "ext4")

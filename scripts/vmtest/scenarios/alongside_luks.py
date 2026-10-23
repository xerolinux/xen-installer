from fixtures import (
    GIB,
    PASSPHRASE,
    TARGET,
    alongside_selections,
    esp_and_linux,
    fsck,
    gpt_identity,
    install,
    mounts,
    part,
    pbkdfs,
    raw_sha,
    release,
    sh,
    tree_of,
)

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    disk = ctx.disk(0)
    esp_and_linux(disk, "ext4", 300)
    esp, old = part(disk, 1), part(disk, 2)
    tree, esp_sha, esp_id = tree_of(old, "ext4"), raw_sha(esp), gpt_identity(disk, 1)

    selections = install(
        alongside_selections(
            disk, 2, 12 * GIB, encryption=True, encryption_passphrase=PASSPHRASE
        )
    )
    root = part(disk, 3)
    found = mounts()
    assert found[TARGET]["source"] == "/dev/mapper/cryptroot", found
    assert found[f"{TARGET}/boot/efi"]["source"] == esp, found
    sh(f"cryptsetup isLuks {root}")
    assert pbkdfs(root) and set(pbkdfs(root)) == {"pbkdf2"}
    assert selections["encryption"] is True and selections["root_partition"] == root
    assert selections["luks_passphrases"] == {root.rsplit("/", 1)[-1]: PASSPHRASE}
    assert selections["opened_mappers"] == ["cryptroot"]
    release(selections)

    sh(f"printf %s {PASSPHRASE} | cryptsetup open --test-passphrase {root} --key-file -")
    assert sh(f"blkid -p -o value -s TYPE {old}").strip() == "ext4"
    fsck(old, "ext4")
    assert tree_of(old, "ext4") == tree
    assert raw_sha(esp) == esp_sha and gpt_identity(disk, 1) == esp_id

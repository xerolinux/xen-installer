from fixtures import (
    GIB,
    boot_selections,
    check_installed,
    part,
    run_engine,
    sh,
    tree_of,
    windows_like,
)

DISKS = [{"size_gib": 60, "logical_block_size": 512}]
BOOT = True
KEEP_BYTES = 12 * GIB


def _check_esp(esp, before):
    after = tree_of(esp, "vfat")
    changed = [f for f, digest in before.items() if after.get(f) != digest]
    assert not changed, f"pre-existing ESP files changed or removed: {changed}"
    added = sorted(set(after) - set(before))
    assert added and all(f.startswith("EFI/XeroLinux/") for f in added), added
    assert "EFI/BOOT/BOOTX64.EFI" not in after, "the shared ESP got a fallback loader"


def run(ctx):
    disk = ctx.disk(0)
    windows_like(disk, 500)
    esp, data = part(disk, 1), part(disk, 3)
    state = {"esp": tree_of(esp, "vfat"), "ntfs": tree_of(data, "ntfs3")}

    selections = run_engine(
        boot_selections(
            disk,
            "alongside",
            "ext4",
            False,
            alongside_partition=3,
            alongside_keep_size_bytes=KEEP_BYTES,
            alongside_esp={"disk": disk.rsplit("/", 1)[-1], "number": 1},
        )
    )
    root = part(disk, 5)
    assert selections["efi_partition"] == esp and selections["root_partition"] == root
    assert selections["esp_formatted"] is False
    grub, _fstab = check_installed(root, "ext4")
    assert "Windows" in grub, "os-prober did not add Windows to grub.cfg"
    assert "XeroLinux" in sh("efibootmgr")
    _check_esp(esp, state["esp"])
    return state


def verify(ctx, state):
    disk = ctx.disk(0)
    esp, data = part(disk, 1), part(disk, 3)
    _check_esp(esp, state["esp"])
    assert int(sh(f"blockdev --getsize64 {data}")) == KEEP_BYTES
    sh(f"ntfsresize --check --no-progress-bar {data}")
    assert tree_of(data, "ntfs3") == state["ntfs"], "NTFS data changed"

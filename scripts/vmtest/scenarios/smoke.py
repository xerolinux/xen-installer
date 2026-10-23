import os

from fixtures import part, windows_like

DISKS = [{"size_gib": 20, "logical_block_size": 512}]
BOOT = False


def run(ctx):
    assert os.path.isdir("/sys/firmware/efi"), "guest is not booted in UEFI mode"
    disk = ctx.disk(0)
    windows_like(disk, 300)
    fstypes = [ctx.sh(f"lsblk -no FSTYPE {part(disk, n)}").strip() for n in (1, 3, 4)]
    assert fstypes == ["vfat", "ntfs", "ntfs"], fstypes
    uuids = [ctx.sh(f"lsblk -no UUID {part(disk, n)}").strip() for n in (3, 4)]
    assert uuids[0] and uuids[1] and uuids[0] != uuids[1], uuids
    probed = ctx.sh("os-prober", check=False)
    assert "Windows" in probed, f"os-prober found no Windows: {probed!r}"

import glob
import os
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

from fixtures import checksum_tree, hibernated_ntfs, linux_btrfs, linux_ext4, part, sh, windows_like

DISKS = [
    {"size_gib": 20, "logical_block_size": 512},
    {"size_gib": 4, "logical_block_size": 512},
    {"size_gib": 4, "logical_block_size": 512},
    {"size_gib": 4, "logical_block_size": 512},
]
BOOT = False

RO_OPTS = {"vfat": "ro", "ntfs": "ro", "ext4": "ro,noload", "btrfs": "ro,rescue=nologreplay"}
MOUNT_TYPE = {"ntfs": "ntfs3"}
FULL_HASH_LIMIT = 5 * 1024**3


@contextmanager
def _mount(dev, fstype, opts):
    mnt = tempfile.mkdtemp(prefix="xenscan-")
    sh(f"mount -t {fstype} -o {opts} {dev} {mnt}")
    try:
        yield mnt
    finally:
        sh(f"umount {mnt}")
        os.rmdir(mnt)


def _fake_linux(root, pretty):
    os.makedirs(f"{root}/etc", exist_ok=True)
    os.makedirs(f"{root}/usr/lib", exist_ok=True)
    with open(f"{root}/etc/os-release", "w") as fh:
        fh.write(f'NAME="Old"\nPRETTY_NAME="{pretty}"\n')
    # os-prober's linux test only reports a root that has a dynamic loader
    open(f"{root}/usr/lib/ld-linux-x86-64.so.2", "w").close()
    sh("sync")


def _devices(disks):
    devs = {}
    for disk in disks:
        devs[disk] = None
        for line in sh(f"lsblk -lnpo NAME,TYPE,FSTYPE {disk}").splitlines():
            fields = line.split()
            if fields[1] == "part":
                devs[fields[0]] = fields[2] if len(fields) > 2 else ""
    return devs


def _raw_hashes(devs):
    hashes = {}
    for dev in devs:
        size = int(sh(f"blockdev --getsize64 {dev}"))
        reader = "cat" if size <= FULL_HASH_LIMIT else "head -c 64M"
        hashes[dev] = sh(f"{reader} {dev} | sha256sum").split()[0]
    return hashes


def _trees(devs):
    trees = {}
    for dev, fstype in devs.items():
        if fstype in RO_OPTS:
            with _mount(dev, MOUNT_TYPE.get(fstype, fstype), RO_OPTS[fstype]) as mnt:
                trees[dev] = checksum_tree(mnt)
    return trees


def _assert_hibernated_fixture_is_realistic(dev):
    mount = f"mount -t ntfs-3g -o rw {dev} /mnt/hib 2>&1"
    out = sh(f"mkdir -p /mnt/hib; {mount}; findmnt -no OPTIONS /mnt/hib")
    sh("umount /mnt/hib")
    options = out.strip().splitlines()[-1].split(",")
    # ntfs-3g never grants rw on a hibernated volume; it falls back to a read-only mount
    assert options[0] == "ro", f"ntfs-3g mounted the hibernated fixture rw: {out!r}"
    assert "hibernated" in out, f"ntfs-3g did not report hibernation: {out!r}"


def _by_path(disks):
    return {p["path"]: p for d in disks for p in d["partitions"]}


def run(ctx):
    # the host imports scenarios only for DISKS/BOOT; xen exists in the guest
    from xen.partitioning import fscaps, probe
    from xen.utils.disk_detector import list_disks

    win, ext, btr, hib = (ctx.disk(i) for i in range(4))
    windows_like(win, 300)
    linux_ext4(ext, 50)
    linux_btrfs(btr, 50)
    hibernated_ntfs(hib)
    with _mount(part(ext, 1), "ext4", "rw") as mnt:
        _fake_linux(mnt, "Old Linux 1.0")
    with _mount(part(btr, 1), "btrfs", "rw") as mnt:
        sh(f"btrfs subvolume create {mnt}/@")
        _fake_linux(f"{mnt}/@", "Btrfs Linux")
    _assert_hibernated_fixture_is_realistic(part(hib, 1))

    devs = _devices([win, ext, btr, hib])
    trees_before = _trees(devs)
    raw_before = _raw_hashes(devs)

    parts = _by_path(list_disks(probe=True))

    with patch("xen.partitioning.probe.shutil.which", return_value=None):
        fallback = probe.detect_os(list(parts.values()))

    assert _raw_hashes(devs) == raw_before, "the scan changed a disk"
    assert _trees(devs) == trees_before, "the scan changed a filesystem"
    leftover = [line for line in sh("findmnt -rno SOURCE").split() if line.startswith("/dev/vd")]
    assert not leftover, f"scan left mounts behind: {leftover}"
    assert not glob.glob("/tmp/xen-scan-*") and not glob.glob("/tmp/xen-osprobe-*")

    esp, msr, data, recovery = (parts[part(win, n)] for n in (1, 2, 3, 4))
    assert esp["isEsp"] and not any(p["isEsp"] for p in (msr, data, recovery)), parts
    expected_min = fscaps.parse_ntfsresize_min(
        sh(f"ntfsresize --info --no-progress-bar {data['path']}")
    )
    assert data["canShrink"], data
    assert abs(data["minSizeBytes"] - expected_min) <= expected_min * 0.1, (data, expected_min)
    assert data["osName"].startswith("Windows"), data
    assert data["number"] == 3 and data["disk"] == "vda", data
    assert msr["resizeBlockedReason"] == "Unknown or empty partition", msr

    old = parts[part(ext, 1)]
    assert old["osName"], old
    assert old["canShrink"] and old["minSizeBytes"] > 0, old
    btrfs_part = parts[part(btr, 1)]
    assert btrfs_part["canShrink"] and btrfs_part["minSizeBytes"] > 0, btrfs_part

    hib_part = parts[part(hib, 1)]
    assert hib_part["resizeBlockedReason"] == fscaps.REASON_HIBERNATED, hib_part
    assert not hib_part["canShrink"] and hib_part["minSizeBytes"] == 0, hib_part

    assert fallback.get(data["path"]) == "Windows", fallback
    assert fallback.get(recovery["path"]) == "Windows", fallback
    assert fallback.get(old["path"]) == "Old Linux 1.0", fallback
    assert fallback.get(btrfs_part["path"]) == "Btrfs Linux", fallback
    print(f"scan ok: ntfs min {data['minSizeBytes']} vs ntfsresize {expected_min}; os {fallback}")

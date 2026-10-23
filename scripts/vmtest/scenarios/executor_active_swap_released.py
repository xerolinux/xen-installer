import os

from fixtures import check_erase_layout, erase_install, part, sh, teardown_and_fsck, windows_like

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = False


def _active():
    return [line.split()[0] for line in sh("cat /proc/swaps").splitlines()[1:] if line.strip()]


def run(ctx):
    disk = ctx.disk(0)
    windows_like(disk, 50)
    # the 600 MiB recovery slot becomes a plain swap partition plus a live-session style cryptswap
    sh(f"sgdisk -d 4 -n 4:0:+300MiB -t 4:8200 -n 5:0:0 -t 5:8200 {disk}")
    sh(f"partprobe {disk} && udevadm settle")
    plain, crypt = part(disk, 4), part(disk, 5)
    sh(f"mkswap {plain} && swapon {plain}")
    sh(f"cryptsetup open --type plain --cipher aes-xts-plain64 --key-size 256 "
       f"--key-file /dev/urandom {crypt} cryptswap")  # fmt: skip
    sh("mkswap /dev/mapper/cryptswap && swapon /dev/mapper/cryptswap")
    mapper = os.path.realpath("/dev/mapper/cryptswap")
    assert {plain, mapper} <= set(_active()), _active()

    _plan, result = erase_install(disk, "ext4")
    check_erase_layout(disk, result, "ext4")
    assert not {plain, mapper} & set(_active()), _active()
    assert not os.path.exists("/dev/mapper/cryptswap"), "cryptswap is still open"
    teardown_and_fsck(disk, result, "ext4")

import os
import tempfile
from xml.etree import ElementTree

from fixtures import boot_selections, check_installed, part, run_engine, sh, tree_of

DISKS = [{"size_gib": 40, "logical_block_size": 512}]
BOOT = True
# A pacman transaction on the booted system: snap-pac takes a pre/post pair, grub-btrfs lists it.
# `command` skips the XeroLinux shell aliases (ls is eza, grep adds colors).
BOOT_SESSION = (
    "pkg=$(command ls /var/cache/pacman/pkg/snap-pac-*.pkg.tar.zst | head -n1); "
    'pacman -U --noconfirm "$pkg" >/dev/null; '
    "for i in $(seq 60); do command grep -q @snapshots/3/snapshot /boot/grub/grub-btrfs.cfg && break; sleep 2; done; "
    "echo '== snapper'; snapper --csvout -c root list --columns number,type,description; "
    "echo '== grub'; command grep -o '@snapshots/[0-9]*/snapshot' /boot/grub/grub-btrfs.cfg | sort -u; "
    "echo '== assistant'; btrfs-assistant --list; "
    "echo '== units'; systemctl is-enabled grub-btrfs-snapper.path snapper-cleanup.timer"
)


def run(ctx):
    disk = ctx.disk(0)
    # validate_plan refuses a Btrfs root offline: the packages job installs snapper and grub-btrfs
    selections = run_engine(boot_selections(disk, "auto", "btrfs", True))
    esp, root = part(disk, 1), part(disk, 2)
    assert selections["efi_partition"] == esp and selections["root_partition"] == root
    assert selections["esp_formatted"] is True
    grub, _fstab = check_installed(root, "btrfs", "ro,subvol=@")
    assert "rootflags=subvol=@ " in grub, "grub.cfg does not boot the @ subvolume"
    check_snapper_layout(root)
    files = tree_of(esp, "vfat")
    assert "EFI/XeroLinux/grubx64.efi" in files, sorted(files)
    assert "EFI/BOOT/BOOTX64.EFI" in files, sorted(files)
    assert "XeroLinux" in sh("efibootmgr")


def check_snapper_layout(root):
    # ArchWiki Snapper "Suggested filesystem layout": snapshots live in @snapshots, not inside @
    top = tempfile.mkdtemp(prefix="xentop-")
    sh(f"mount -t btrfs -o ro,subvolid=5 {root} {top}")
    try:
        subvols = [line.split(" path ", 1)[1] for line in sh(f"btrfs subvolume list {top}").splitlines()]
        assert "@snapshots" in subvols, subvols
        assert "@/.snapshots" not in subvols, subvols
        assert "@snapshots/1/snapshot" in subvols, subvols
        assert os.stat(f"{top}/@snapshots").st_mode & 0o777 == 0o750
        fstab = [line.split() for line in open(f"{top}/@/etc/fstab") if line.strip() and not line.startswith("#")]
        snap = [f for f in fstab if f[1] == "/.snapshots"]
        assert snap and "subvol=/@snapshots" in snap[0][3].split(","), fstab
        # btrfs-assistant restore and snapper rollback swap subvolumes by name
        assert not [f for f in fstab if "subvolid=" in f[3]], fstab
        assert 'SUBVOLUME="/"' in open(f"{top}/@/etc/snapper/configs/root").read()
        snapshots = snapshot_descriptions(f"{top}/@snapshots")
        assert snapshots == {1: "Fresh installation"}, snapshots
        menu = open(f"{top}/@/boot/grub/grub-btrfs.cfg").read()
        assert "@snapshots/1/snapshot" in menu, menu[:2000]
    finally:
        sh(f"umount {top}")
        os.rmdir(top)


def snapshot_descriptions(snapshots_dir):
    found = {}
    for entry in os.listdir(snapshots_dir):
        if entry.isdigit():
            info = ElementTree.parse(f"{snapshots_dir}/{entry}/info.xml").getroot()
            found[int(entry)] = info.findtext("description")
    return found


def check_session(output):
    sections = {}
    for block in output.split("== ")[1:]:
        name, _, body = block.partition("\n")
        sections[name.strip()] = [line for line in body.splitlines() if line.strip()]
    rows = [line.split(",", 2) for line in sections["snapper"][1:]]
    kinds = {int(number): (kind, description) for number, kind, description in rows if number != "0"}
    assert kinds.get(1) == ("single", "Fresh installation"), output
    assert kinds.get(2, ("",))[0] == "pre" and kinds.get(3, ("",))[0] == "post", output
    menu = sections["grub"]
    assert all(f"@snapshots/{n}/snapshot" in menu for n in (1, 2, 3)), output
    listed = "\n".join(sections["assistant"])
    assert all(f"@snapshots/{n}/snapshot" in listed for n in (1, 2, 3)), output
    assert sections["units"] == ["enabled", "enabled"], output

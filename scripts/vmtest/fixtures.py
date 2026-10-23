import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager

ESP_MIB = 100
MSR_MIB = 16
RECOVERY_MIB = 600


def sh(cmd, check=True):
    proc = subprocess.run(
        ["bash", "-c", "set -o pipefail; " + cmd], capture_output=True, text=True
    )
    if check and proc.returncode:
        raise AssertionError(
            f"command failed (rc={proc.returncode}): {cmd}\n{proc.stdout}{proc.stderr}"
        )
    return proc.stdout


def part(disk, n):
    return f"{disk}p{n}" if disk[-1].isdigit() else f"{disk}{n}"


def _disk_mib(disk):
    return int(sh(f"blockdev --getsize64 {disk}")) // (1024 * 1024)


def _settle(disk):
    sh(f"partprobe {disk}; udevadm settle")


@contextmanager
def _mounted(device, fstype=None):
    mnt = tempfile.mkdtemp(prefix="xenfx-")
    opt = f"-t {fstype}" if fstype else ""
    sh(f"mount {opt} {device} {mnt}")
    try:
        yield mnt
    finally:
        sh(f"umount {mnt}")
        os.rmdir(mnt)


def _mkntfs(device, label):
    # mkntfs derives the serial from the clock, so back-to-back runs collide
    sh(f"mkfs.ntfs -f -Q -L {label} {device} && ntfslabel --new-serial {device}")


def _random_file(path, mib):
    sh(f"head -c {mib}M /dev/urandom > '{path}'")


def _fill(mnt, data_mib):
    os.makedirs(f"{mnt}/data/sub")
    i = 0
    while data_mib > 0:
        i += 1
        chunk = min(data_mib, 64)
        _random_file(f"{mnt}/data/{'sub/' if i % 2 else ''}file{i}.bin", chunk)
        data_mib -= chunk
    sh("sync")


def _whole_disk_partition(disk, typecode):
    sh(f"sgdisk -Z {disk}; sgdisk -n1:1MiB:0 -t1:{typecode} {disk}")
    _settle(disk)
    return part(disk, 1)


def windows_like(disk, data_mib, free_mib=0):
    data_part_mib = _disk_mib(disk) - 1 - ESP_MIB - MSR_MIB - RECOVERY_MIB - 2 - free_mib
    sh(
        f"sgdisk -Z {disk}; sgdisk"
        f" -n1:1MiB:+{ESP_MIB}MiB -t1:ef00 -c1:EFI"
        f" -n2:0:+{MSR_MIB}MiB -t2:0c01 -c2:MSR"
        f" -n3:0:+{data_part_mib}MiB -t3:0700 -c3:Windows"
        f" -n4:0:+{RECOVERY_MIB}MiB -t4:2700 -c4:Recovery {disk}"
    )
    _settle(disk)
    sh(f"mkfs.fat -F32 -n SYSTEM {part(disk, 1)}")
    _mkntfs(part(disk, 3), "Windows")
    _mkntfs(part(disk, 4), "Recovery")
    with _mounted(part(disk, 1)) as mnt:
        os.makedirs(f"{mnt}/EFI/Microsoft/Boot")
        _random_file(f"{mnt}/EFI/Microsoft/Boot/bootmgfw.efi", 1)
        # os-prober's efi/20microsoft only reports Windows when BCD sits next to bootmgfw.efi
        _random_file(f"{mnt}/EFI/Microsoft/Boot/BCD", 1)
    with _mounted(part(disk, 3), "ntfs3") as mnt:
        _fill(mnt, data_mib)


def linux_ext4(disk, data_mib):
    dev = _whole_disk_partition(disk, "8300")
    sh(f"mkfs.ext4 -F -q -L oldlinux {dev}")
    with _mounted(dev) as mnt:
        _fill(mnt, data_mib)


def linux_btrfs(disk, data_mib):
    dev = _whole_disk_partition(disk, "8300")
    sh(f"mkfs.btrfs -f -q -L oldbtrfs {dev}")
    with _mounted(dev) as mnt:
        _fill(mnt, data_mib)


def esp_and_linux(disk, fstype, data_mib, linux_mib=0):
    size = f"+{linux_mib}MiB" if linux_mib else "0"
    sh(
        f"sgdisk -Z {disk}; sgdisk -n1:1MiB:+{ESP_MIB}MiB -t1:ef00 -c1:EFI"
        f" -n2:0:{size} -t2:8300 -c2:Linux {disk}"
    )
    _settle(disk)
    sh(f"mkfs.fat -F32 -n SYSTEM {part(disk, 1)}")
    force = "-F" if fstype == "ext4" else "-f"
    sh(f"mkfs.{fstype} {force} -q -L oldlinux {part(disk, 2)}")
    with _mounted(part(disk, 1)) as mnt:
        os.makedirs(f"{mnt}/EFI/oldlinux")
        _random_file(f"{mnt}/EFI/oldlinux/grubx64.efi", 1)
    with _mounted(part(disk, 2)) as mnt:
        _fill(mnt, data_mib)


def xfs_only(disk):
    dev = _whole_disk_partition(disk, "8300")
    sh(f"mkfs.xfs -f -q {dev}")


def _hibernate(dev, magic="hibr"):
    with _mounted(dev, "ntfs-3g") as mnt:
        # ntfs-3g refuses a normal rw mount when hiberfil.sys starts with this magic
        sh(f"printf '{magic}' > {mnt}/hiberfil.sys; head -c 1M /dev/zero >> {mnt}/hiberfil.sys")


def hibernated_ntfs(disk):
    dev = _whole_disk_partition(disk, "0700")
    _mkntfs(dev, "Windows")
    _hibernate(dev)


def esp_and_hibernated_ntfs(disk):
    sh(
        f"sgdisk -Z {disk}; sgdisk -n1:1MiB:+{ESP_MIB}MiB -t1:ef00 -c1:EFI"
        f" -n2:0:+2GiB -t2:0700 -c2:Windows {disk}"
    )
    _settle(disk)
    sh(f"mkfs.fat -F32 -n SYSTEM {part(disk, 1)}")
    _mkntfs(part(disk, 2), "Windows")
    _hibernate(part(disk, 2), "HIBR")


def checksum_tree(mountpoint):
    sums = {}
    for root, _dirs, files in os.walk(mountpoint):
        for name in files:
            path = os.path.join(root, name)
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
            sums[os.path.relpath(path, mountpoint)] = digest.hexdigest()
    return sums


TARGET = "/mnt"
PASSPHRASE = "vmtest-passphrase"
ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
LINUX_GUID = "0fc63daf-8483-4772-8e79-3d69d8477de4"
_FSCK = {
    "ext4": "fsck.ext4 -n -f {}",
    "xfs": "xfs_repair -n {}",
    "btrfs": "btrfs check --readonly {}",
    "vfat": "fsck.fat -n {}",
}


def erase_install(disk, fstype, encrypt=False, subvolumes=None, swap="none", executor=None):
    from xen.partitioning.executor import PlanExecutor, run_subprocess
    from xen.partitioning.plan import build_erase_plan
    from xen.utils.disk_detector import list_disks

    name = disk.rsplit("/", 1)[-1]
    target = next(d for d in list_disks() if d["name"] == name)
    plan = build_erase_plan(target, fstype, encrypt, 2000, subvolumes or [])
    executor = executor or PlanExecutor(run_subprocess, TARGET, "grub")
    result = executor.execute(plan, PASSPHRASE, swap)
    assert result.ok, result.error
    return plan, result


def mounts(root=TARGET):
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get("children", []))

    data = json.loads(sh(f"findmnt -R -J -o TARGET,SOURCE,FSTYPE,OPTIONS {root}"))
    return {m["target"]: m for m in walk(data["filesystems"])}


def blkid(dev):
    keys = ("PART_ENTRY_TYPE", "PART_ENTRY_NAME", "TYPE", "LABEL")
    return {k: sh(f"blkid -p -o value -s {k} {dev}", check=False).strip() for k in keys}


def check_erase_layout(disk, result, fstype):
    esp, root = part(disk, 1), part(disk, 2)
    found = mounts()
    assert found[TARGET]["fstype"] == fstype, found
    assert found[f"{TARGET}/boot/efi"]["fstype"] == "vfat", found
    assert found[f"{TARGET}/boot/efi"]["source"] == esp, found
    esp_id, root_id = blkid(esp), blkid(root)
    assert esp_id["PART_ENTRY_TYPE"] == ESP_GUID, esp_id
    assert esp_id["PART_ENTRY_NAME"] == "EFI system", esp_id
    assert root_id["PART_ENTRY_TYPE"] == LINUX_GUID, root_id
    assert root_id["PART_ENTRY_NAME"] == "XeroLinux root", root_id
    assert result.efi_partition == esp and result.root_partition == root, result
    assert int(sh(f"blockdev --getsize64 {esp}")) == 2000 * 1024 * 1024
    assert result.esp_formatted
    return found


def check_subvolumes(found, subvolumes):
    for sv in subvolumes:
        where = TARGET + ("" if sv["mountpoint"] == "/" else sv["mountpoint"])
        opts = found[where]["options"].split(",")
        assert found[where]["fstype"] == "btrfs", found[where]
        assert f"subvol=/{sv['name']}" in opts, found[where]
        assert any(o.startswith("compress=zstd") for o in opts), found[where]
        assert "noatime" in opts, found[where]


def teardown_and_fsck(disk, result, fstype):
    for device in result.swap_devices:
        sh(f"swapoff {device}")
    sh(f"umount -R {TARGET}")
    assert not sh(f"findmnt -rn {TARGET}", check=False).strip(), "target still mounted"
    root = next(a["path"] for a in result.assignments if a["raw_path"] == result.root_partition)
    sh(_FSCK[fstype].format(root))
    sh(_FSCK["vfat"].format(part(disk, 1)))
    for name in result.opened_mappers:
        sh(f"cryptsetup close {name}")


def alongside_plan(disk, number, keep_bytes):
    from xen.partitioning.plan import build_alongside_plan, validate_plan
    from xen.utils.disk_detector import list_disks

    disks = list_disks(probe=True)
    target = next(d for d in disks if d["name"] == disk.rsplit("/", 1)[-1])
    plan = build_alongside_plan(target, number, keep_bytes, "ext4", False, 1, target["name"], [])
    others = [d for d in disks if d is not target]
    errors = validate_plan(
        plan, target, uefi=True, has_internet=True, min_install_gib=20, other_disks=others
    )
    return plan, errors


def run_plan(plan):
    from xen.partitioning.executor import PlanExecutor, run_subprocess

    return PlanExecutor(run_subprocess, TARGET, "grub").execute(plan, "", "none")


def gpt_identity(disk, number):
    keys = ("Partition GUID code", "Partition unique GUID", "First sector", "Partition name",
            "Attribute flags")  # fmt: skip
    out = sh(f"sgdisk -i {number} {disk}")
    return {k: v.strip() for k, _, v in (l.partition(":") for l in out.splitlines()) if k in keys}


def raw_sha(dev):
    return sh(f"sha256sum {dev}").split()[0]


GIB = 1024**3
MIB_SECTORS = 2048
JOB_CONFIG = {"min_install_gib": 10}


def job_selections(disk, mode, **extra):
    return {
        "disk": disk,
        "partition_mode": mode,
        "filesystem": "ext4",
        "encryption": False,
        "encryption_passphrase": "",
        "swap_strategy": "none",
        "efi_size_mb": 2000,
        "has_internet": True,
        "dry_run": False,
        "confirmed": True,
        "bootloader": "grub",
        **extra,
    }


def alongside_selections(disk, number, keep_bytes, esp=1, **extra):
    return job_selections(
        disk,
        "alongside",
        alongside_partition=number,
        alongside_keep_size_bytes=keep_bytes,
        alongside_esp={"disk": disk.rsplit("/", 1)[-1], "number": esp},
        **extra,
    )


def new_row(ref, start_mib, size_mib, fstype, mountpoint, **extra):
    start = start_mib * MIB_SECTORS
    return {
        "kind": "new",
        "ref": ref,
        "start": start,
        "end": start + size_mib * MIB_SECTORS - 1,
        "fstype": fstype,
        "mountpoint": mountpoint,
        **extra,
    }


def existing_row(number, action="keep", mountpoint="", **extra):
    return {"kind": "existing", "number": number, "action": action, "mountpoint": mountpoint,
            **extra}  # fmt: skip


def run_job(selections):
    from xen.jobs.base import JobContext
    from xen.jobs.partition import PartitionJob

    job = PartitionJob(dict(JOB_CONFIG))
    context = JobContext(selections=dict(selections), target_root=TARGET)
    validation = job.validate(context)
    if not validation.success:
        return validation, context
    return job.run(context), context


def install(selections):
    result, context = run_job(selections)
    assert result.success, result.message
    return context.selections


def largest_free_mib(disk):
    first = int(sh(f"sgdisk -F {disk}").split()[-1])
    last = int(sh(f"sgdisk -E {disk}").split()[-1])
    start = -(-first // MIB_SECTORS)
    return start, (last + 1) // MIB_SECTORS - start


def release(selections):
    for device in selections.get("target_swap_devices", []):
        sh(f"swapoff {device}", check=False)
    sh(f"umount -R {TARGET}")
    assert not sh(f"findmnt -rn {TARGET}", check=False).strip(), "target still mounted"
    for name in reversed(selections.get("opened_mappers", [])):
        sh(f"cryptsetup close {name}")


def fsck(dev, fstype):
    sh(_FSCK[fstype].format(dev))


def tree_of(dev, fstype, opts="ro"):
    mnt = tempfile.mkdtemp(prefix="xentree-")
    sh(f"mount -t {fstype} -o {opts} {dev} {mnt}")
    try:
        return checksum_tree(mnt)
    finally:
        sh(f"umount {mnt}")
        os.rmdir(mnt)


def gpt_backup_sha(disk):
    sh(f"sgdisk --backup=/root/gpt.bak {disk}")
    return sh("sha256sum /root/gpt.bak").split()[0]


def pbkdfs(raw):
    dump = sh(f"cryptsetup luksDump {raw}")
    return [line.split(":", 1)[1].strip() for line in dump.splitlines() if "PBKDF:" in line]


BOOT_HOSTNAME = "xenboot"
BOOT_PASSWORD = "vmtest-password"
BOOT_TARGET = "/mnt/target"
BOOT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boot_config.yaml")
XERO_MNT = "/run/xen-xeroiso"


def xero_squashfs():
    roms = [line.split() for line in sh("lsblk -rndo PATH,LABEL -I 11").splitlines()]
    dev = next((r[0] for r in roms if len(r) == 2 and r[1].startswith("XERO_")), None)
    assert dev, f"no XeroLinux ISO among the CD-ROMs: {roms}"
    os.makedirs(XERO_MNT, exist_ok=True)
    sh(f"findmnt {XERO_MNT} || mount -o ro {dev} {XERO_MNT}")
    return f"{XERO_MNT}/arch/x86_64/airootfs.sfs"


def _boot_subvolumes():
    import yaml

    from xen.jobs.partition import default_btrfs_subvolumes

    with open(BOOT_CONFIG) as fh:
        jobs = yaml.safe_load(fh)["jobs"]
    config = next(j.get("config") or {} for j in jobs if j["name"] == "partition")
    return default_btrfs_subvolumes(config)


def boot_selections(disk, mode, filesystem, has_internet, **extra):
    manual = {"manual_rows": [], "manual_new_table": False, "manual_root_passphrase": ""}
    return {
        "disk": disk,
        "partition_mode": mode,
        "filesystem": filesystem,
        "encryption": False,
        "encryption_passphrase": "",
        "swap_strategy": "none",
        "efi_size_mb": 2000,
        "locale": "en_US.UTF-8",
        "timezone": "UTC",
        "keymap": "us",
        "keyboard_variant": "",
        "username": "xen",
        "fullname": "Xen Boot",
        "password": BOOT_PASSWORD,
        "hostname": BOOT_HOSTNAME,
        "root_same_as_user": True,
        "root_password": "",
        "btrfs_subvolumes": _boot_subvolumes(),
        "has_internet": has_internet,
        "squashfs_path": xero_squashfs(),
        "dry_run": False,
        "confirmed": True,
        **(manual if mode == "manual" else {}),
        **extra,
    }


def run_engine(selections):
    from xen.core.engine import Engine
    from xen.jobs.base import JobContext

    logging.basicConfig(level=logging.INFO, format="XEN-LOG %(name)s: %(message)s", force=True)
    engine = Engine.from_config_file(BOOT_CONFIG)
    start = time.monotonic()
    engine.on_job_start = lambda name: print(
        f"XEN-JOB {name} t={time.monotonic() - start:.0f}s", flush=True
    )
    failures = []
    engine.on_error = lambda name, error: failures.append(f"{name}: {error}")
    engine.set_selections(selections)
    context = JobContext(target_root=BOOT_TARGET)
    ok = engine.run_all(context)
    assert ok, f"install failed: {failures or engine.state.last_error}"
    assert not sh(f"findmnt -rn {BOOT_TARGET}", check=False).strip(), "target still mounted"
    return context.selections


def check_installed(root_dev, fstype, opts="ro"):
    mnt = tempfile.mkdtemp(prefix="xenroot-")
    sh(f"mount -t {fstype} -o {opts} {root_dev} {mnt}")
    try:
        print(f"XEN-INFO /boot: {sorted(os.listdir(f'{mnt}/boot'))}", flush=True)
        grub = open(f"{mnt}/boot/grub/grub.cfg").read()
        assert "console=ttyS0" in grub, "console=ttyS0 missing from grub.cfg"
        fstab = {
            fields[1]: fields[2]
            for fields in (line.split() for line in open(f"{mnt}/etc/fstab"))
            if len(fields) >= 3 and not fields[0].startswith("#")
        }
        assert fstab.get("/boot/efi") == "vfat", fstab
        assert open(f"{mnt}/etc/hostname").read().strip() == BOOT_HOSTNAME
        assert "\nxen:" in "\n" + open(f"{mnt}/etc/passwd").read()
        return grub, fstab
    finally:
        sh(f"umount {mnt}")
        os.rmdir(mnt)

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol

from xen.i18n.messages import Msg, msg
from xen.partitioning import fscaps
from xen.partitioning.plan import PartitionPlan, Resize, Target, size_text
from xen.utils.disk_detector import partition_path

logger = logging.getLogger(__name__)

MIB = 1024**2
SCRATCH = "/run/xen-btrfs-scratch"
BTRFS_MOUNT_OPTIONS = "noatime,compress=zstd,space_cache=v2"
_CMD_TIMEOUT = 3600
_LSBLK_SECTOR = 512
_GPT_ENTRIES_BYTES = 128 * 128
_FAT = ("vfat", "fat", "fat32")
# Same cipher and key size as the crypttab swap entry systemconfig writes.
_PLAIN_SWAP_OPEN = [
    "cryptsetup", "open", "--type", "plain", "--key-file", "/dev/urandom",
    "--cipher", "aes-xts-plain64", "--key-size", "256",
]  # fmt: skip
# e2fsck exits 1 after repairing the filesystem, which is what the preflight asks of it.
_OK_CODES = {"e2fsck": (0, 1)}


@dataclass
class CmdResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class RunCmd(Protocol):
    def __call__(
        self, cmd: list[str], description: str, stdin: str | None = None
    ) -> CmdResult: ...


def run_subprocess(cmd: list[str], description: str, stdin: str | None = None) -> CmdResult:
    logger.info("%s: %s", description, " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            stdin=subprocess.DEVNULL if stdin is None else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=_CMD_TIMEOUT,
        )
    except FileNotFoundError:
        return CmdResult(127, "", msg("{command}: command not found", command=cmd[0]))
    except subprocess.TimeoutExpired:
        return CmdResult(124, "", msg("timed out after {seconds}s", seconds=_CMD_TIMEOUT))
    return CmdResult(proc.returncode, proc.stdout, proc.stderr)


@dataclass
class ExecResult:
    ok: bool = False
    error: str = ""
    assignments: list[dict[str, Any]] = field(default_factory=list)
    root_partition: str = ""
    efi_partition: str = ""
    luks_passphrases: dict[str, str] = field(default_factory=dict)
    opened_mappers: list[str] = field(default_factory=list)
    swap_devices: list[str] = field(default_factory=list)
    esp_formatted: bool = False
    disk_changed: bool = False


def mapper_name(mountpoint: str) -> str:
    mp = mountpoint.strip().strip("/")
    if mountpoint.strip() == "/":
        return "cryptroot"
    if mountpoint.strip() == "swap":
        return "cryptswap"
    if mp == "home":
        return "crypthome"
    if not mp:
        return "cryptdata"
    safe = "".join(c if c.isalnum() else "-" for c in mp)
    return f"cryptdata-{safe}"


def mkfs_command(fstype: str, path: str, label: str = "") -> list[str] | None:
    fs = fstype.lower()
    label = label.strip()
    if fs in _FAT:
        return ["mkfs.fat", "-F32", *(["-n", label[:11]] if label else []), path]
    base = {
        "ext4": ["mkfs.ext4", "-F"],
        "xfs": ["mkfs.xfs", "-f"],
        "btrfs": ["mkfs.btrfs", "-f"],
        "swap": ["mkswap"],
    }.get(fs)
    if base is None:
        return None
    return [*base, *(["-L", label] if label else []), path]


def _detect_ram_mb() -> int:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        logger.debug("RAM detection failed, defaulting to 8192 MB")
    return 8192


def swapfile_commands(
    target_root: str, strategy: str, fstype: str
) -> list[tuple[list[str], str]]:
    ram_mb = _detect_ram_mb()
    size_mb = max(ram_mb, 8192) if strategy == "hibernate" else (min(ram_mb, 8192) or 4096)
    if fstype == "btrfs":
        # A plain dd swapfile doesn't work on Btrfs (needs NOCOW + preallocation), and the
        # @swap subvolume keeps it out of snapshots.
        swapfile = f"{target_root}/swap/swapfile"
        return [
            (
                ["btrfs", "filesystem", "mkswapfile", "--size", f"{size_mb}m",
                 "--uuid", "clear", swapfile],
                msg("Allocating Btrfs swapfile"),
            ),
            (["swapon", swapfile], msg("Activating swapfile")),
        ]  # fmt: skip
    swapfile = f"{target_root}/swapfile"
    return [
        (
            ["dd", "if=/dev/zero", f"of={swapfile}", "bs=1M", f"count={size_mb}"],
            msg("Allocating swapfile"),
        ),
        (["chmod", "600", swapfile], msg("Securing swapfile permissions")),
        (["mkswap", swapfile], msg("Formatting swapfile")),
        (["swapon", swapfile], msg("Activating swapfile")),
    ]


class _Failed(Exception):
    pass


@dataclass
class _PartInfo:
    start: int
    type_guid: str
    uuid: str
    name: str
    attributes: int


def _dev(disk: str) -> str:
    return f"/dev/{disk.rsplit('/', 1)[-1]}"


def _base(path: Any) -> str:
    return str(path or "").rsplit("/", 1)[-1]


def _detail(result: CmdResult) -> str:
    for text in (result.stderr, result.stdout):
        if isinstance(text, Msg):
            return text
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return msg("exit status {status}", status=result.returncode)


def _is_root(target: Target) -> bool:
    if target.subvolumes:
        return any(str(sv.get("mountpoint") or "/") == "/" for sv in target.subvolumes)
    return target.mountpoint == "/"


def _mount_type(fstype: str) -> str:
    if fscaps.fs_kind(fstype) == "ntfs":
        # a bare "ntfs" may resolve to the kernel's read-only legacy alias
        return "ntfs3"
    return "vfat" if fstype in _FAT else fstype


def _depth(mountpoint: str) -> int:
    return 0 if mountpoint == "/" else mountpoint.rstrip("/").count("/")


class PlanExecutor:
    def __init__(self, run_cmd: RunCmd, target_root: str, bootloader: str) -> None:
        self._run_cmd = run_cmd
        self._target_root = target_root.rstrip("/") or "/"
        self._bootloader = bootloader
        self._result = ExecResult()
        self._phase = ""
        self._parts: dict[int, _PartInfo] = {}
        self._rows: dict[int, tuple[int, int]] = {}
        self._shrink_to: dict[int, int] = {}
        self._used_numbers: set[int] = set()
        self._raw: list[str] = []
        self._paths: list[str] = []

    def execute(self, plan: PartitionPlan, passphrase: str, swap_strategy: str) -> ExecResult:
        previous = self._result
        self._result = ExecResult()
        self._parts, self._rows, self._shrink_to, self._used_numbers = {}, {}, {}, set()
        self._raw, self._paths = [], []
        result = self._result
        try:
            self._phase = msg("Release")
            self._release(plan, previous)
            self._preflight(plan)
            self._write_table(plan)
            self._resolve(plan)
            self._encrypt(plan, passphrase)
            self._format(plan)
            self._mount(plan)
            self._swap(plan, swap_strategy)
        except Exception as exc:
            result.error = (
                exc.args[0]
                if isinstance(exc, _Failed)
                else msg("{phase} failed: {error}", phase=self._phase, error=str(exc))
            )
            logger.error("Partitioning failed: %s", result.error)
            try:
                self._release_after_failure()
            except Exception:
                logger.exception("Releasing the disk after the failure failed")
            return result
        result.ok = True
        return result

    def release_previous(self) -> str:
        self._phase = msg("Release")
        try:
            self._release(None, self._result)
        except _Failed as exc:
            return exc.args[0]
        return ""

    def _run(
        self, cmd: list[str], description: str, check: bool = True, stdin: str | None = None
    ) -> CmdResult:
        if stdin is None:
            result = self._run_cmd(cmd, description)
        else:
            result = self._run_cmd(cmd, description, stdin=stdin)
        ok_codes = _OK_CODES.get(cmd[0], (0,))
        if check and result.returncode not in ok_codes:
            raise _Failed(
                msg(
                    "{phase} failed: {description} ({command}): {detail}",
                    phase=self._phase,
                    description=description,
                    command=" ".join(cmd),
                    detail=_detail(result),
                )
            )
        return result

    def _run_seq(self, cmds: list[list[str]], description: str) -> list[str]:
        if cmds[0][0] != "mount":
            return [self._run(cmd, description).stdout for cmd in cmds]
        mount, *body, umount = cmds
        self._run(["mkdir", "-p", mount[-1]], msg("Creating {path}", path=mount[-1]))
        self._run(mount, msg("{description}: mounting", description=description))
        done = False
        try:
            outputs = [self._run(cmd, description).stdout for cmd in body]
            done = True
        finally:
            self._run(
                umount, msg("{description}: unmounting", description=description), check=done
            )
        return outputs

    def _release(self, plan: PartitionPlan | None, previous: ExecResult) -> None:
        swaps_file = self._run(["cat", "/proc/swaps"], msg("Listing active swap"), False).stdout
        active = [
            line.split()[0].replace("\\040", " ")
            for line in swaps_file.splitlines()[1:]
            if line.strip()
        ]
        ours = {os.path.realpath(d) for d in previous.swap_devices}
        prefix = self._target_root.rstrip("/") + "/"
        # swap holds no user data and a live session may auto-activate it, so it is released
        swap_holders = self._affected_swap_holders(plan, active) if plan else {}
        for entry in active:
            real = os.path.realpath(entry)
            if entry.startswith(prefix) or real in ours or real in swap_holders:
                self._run(["swapoff", entry], msg("Deactivating swap {path}", path=entry))
                if swap_holders.get(real):
                    name = swap_holders[real]
                    self._run(["cryptsetup", "close", name], msg("Closing {name}", name=name))
        self._unmount_target()
        for name in previous.opened_mappers:
            if self._mapper_open(name):
                self._run(["cryptsetup", "close", name], msg("Closing {name}", name=name))

    def _unmount_target(self) -> None:
        self._run(
            ["umount", "-R", self._target_root],
            msg("Unmounting {path}", path=self._target_root),
            False,
        )

    def _affected_swap_holders(self, plan: PartitionPlan, active: list[str]) -> dict[str, str]:
        if not active:
            return {}
        found: dict[str, str] = {}
        for disk, numbers in self._affected(plan).items():
            if numbers is not None and not numbers:
                continue
            rows = self._lsblk_rows(disk)
            for row in rows:
                top = self._owner(row, rows)
                if top is None or top.get("type") != "part":
                    continue
                if numbers is not None and top.get("partn") not in numbers:
                    continue
                if "[SWAP]" in (row.get("mountpoints") or []):
                    path = os.path.realpath(str(row.get("path")))
                    found[path] = _base(row.get("path")) if row.get("type") == "crypt" else ""
        return found

    def _lsblk_rows(self, disk: str) -> list[dict[str, Any]]:
        out = self._run(
            ["lsblk", "-J", "-l", "-p", "-o", "PATH,PKNAME,TYPE,PARTN,MOUNTPOINTS", _dev(disk)],
            msg("Listing the devices on {disk}", disk=disk),
        ).stdout
        try:
            return list(json.loads(out).get("blockdevices", []))
        except ValueError as exc:
            raise _Failed(
                msg(
                    "{phase} failed: can't parse lsblk output: {error}.",
                    phase=self._phase,
                    error=exc,
                )
            ) from None

    @staticmethod
    def _owner(row: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        by_name = {_base(r.get("path")): r for r in rows}
        seen: set[str] = set()
        current: dict[str, Any] | None = row
        while current is not None and current.get("type") not in ("part", "disk"):
            name = _base(current.get("pkname"))
            if name in seen:
                return None
            seen.add(name)
            current = by_name.get(name)
        return current

    def _mapper_open(self, name: str) -> bool:
        description = msg("Checking {name}", name=name)
        return self._run(["cryptsetup", "status", name], description, False).ok

    def _release_after_failure(self) -> None:
        result = self._result
        for device in reversed(result.swap_devices):
            self._run(["swapoff", device], msg("Deactivating swap {path}", path=device), False)
        self._unmount_target()
        still_open = []
        for name in reversed(result.opened_mappers):
            description = msg("Closing {name}", name=name)
            if not self._run(["cryptsetup", "close", name], description, False).ok:
                still_open.insert(0, name)
        result.opened_mappers = still_open

    def _read_table(self, plan: PartitionPlan) -> tuple[int, int]:
        disk = _dev(plan.disk)
        description = msg("Reading the partition table of {disk}", disk=disk)
        out = self._run(["sgdisk", "-p", disk], description).stdout
        bounds = re.search(r"First usable sector is (\d+), last usable sector is (\d+)", out)
        if not bounds:
            raise _Failed(
                msg(
                    "{phase} failed: can't read the usable sectors of {disk}.",
                    phase=self._phase,
                    disk=disk,
                )
            )
        table = out.split("Number", 1)[1] if "Number" in out else ""
        rows = re.findall(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s", table, re.M)
        self._rows = {int(n): (int(start), int(end)) for n, start, end in rows}
        return int(bounds.group(1)), int(bounds.group(2))

    def _blank_bounds(self, plan: PartitionPlan) -> tuple[int, int]:
        disk = _dev(plan.disk)
        description = msg("Reading the size of {disk}", disk=disk)
        out = self._run(["blockdev", "--getsz", disk], description).stdout
        try:
            sectors = int(out.strip()) * _LSBLK_SECTOR // plan.sector_size
        except ValueError:
            raise _Failed(
                msg(
                    "{phase} failed: can't read the size of {disk}.",
                    phase=self._phase,
                    disk=disk,
                )
            ) from None
        entries = -(-_GPT_ENTRIES_BYTES // plan.sector_size)
        return 2 + entries, sectors - 2 - entries

    def _check_geometry(self, plan: PartitionPlan, first: int, last: int) -> None:
        def fail(reason: str) -> None:
            raise _Failed(msg("{phase} failed: {reason}", phase=self._phase, reason=reason))

        rows = dict(self._rows)
        for delete in plan.deletes:
            if rows.pop(delete.number, None) is None:
                fail(msg("partition {number} to delete doesn't exist.", number=delete.number))
        for resize in plan.resizes:
            if resize.number not in rows:
                fail(msg("partition {number} to resize doesn't exist.", number=resize.number))
            start, end = rows[resize.number]
            if resize.new_end <= start:
                fail(msg("partition {number} would end before it starts.", number=resize.number))
            if resize.shrink != (resize.new_end < end):
                fail(
                    msg(
                        "the resize of partition {number} doesn't match the disk.",
                        number=resize.number,
                    )
                )
            rows[resize.number] = (start, resize.new_end)
        spans = [
            (msg("partition {number}", number=n), start, end) for n, (start, end) in rows.items()
        ]
        for create in plan.creates:
            name = msg("the new partition {name}", name=repr(create.name))
            if create.end < create.start:
                fail(msg("{name} ends before it starts.", name=name))
            spans.append((name, create.start, create.end))
        for name, start, end in spans:
            if start < first or end > last:
                fail(
                    msg(
                        "{name} lies outside the usable sectors {first}-{last} of {disk}.",
                        name=name,
                        first=first,
                        last=last,
                        disk=plan.disk,
                    )
                )
        spans.sort(key=lambda span: span[1])
        for (name, _, end), (other, start, _) in zip(spans, spans[1:], strict=False):
            if start <= end:
                fail(msg("{name} and {other} would overlap.", name=name, other=other))
        self._used_numbers = set(rows)

    def _read_part(self, disk: str, number: int) -> _PartInfo:
        description = msg("Reading partition {number} of {disk}", number=number, disk=disk)
        out = self._run(["sgdisk", "-i", str(number), disk], description).stdout
        fields = {
            "type_guid": r"Partition GUID code: (\S+)",
            "uuid": r"Partition unique GUID: (\S+)",
            "start": r"First sector: (\d+)",
            "name": r"Partition name: '(.*)'",
            "attributes": r"Attribute flags: ([0-9A-Fa-f]+)",
        }
        found = {key: re.search(pattern, out) for key, pattern in fields.items()}
        missing = [key for key, match in found.items() if match is None]
        if missing:
            raise _Failed(
                msg(
                    "{phase} failed: can't read partition {number} of {disk}.",
                    phase=self._phase,
                    number=number,
                    disk=disk,
                )
            )
        values = {key: match.group(1) for key, match in found.items() if match}
        return _PartInfo(
            int(values["start"]),
            values["type_guid"],
            values["uuid"],
            values["name"],
            int(values["attributes"], 16),
        )

    def _preflight(self, plan: PartitionPlan) -> None:
        self._phase = msg("Preflight")
        bounds = self._blank_bounds(plan) if plan.new_table else self._read_table(plan)
        self._check_geometry(plan, *bounds)
        self._check_not_busy(plan)
        for name in self._mappers_to_open(plan):
            if self._mapper_open(name):
                raise _Failed(
                    msg("A device named {name} is already open: close it first.", name=name)
                )
        for resize in plan.resizes:
            self._parts[resize.number] = self._read_part(_dev(plan.disk), resize.number)
        for resize in plan.resizes:
            if resize.shrink:
                self._preflight_shrink(plan, resize)
            elif fscaps.fs_kind(resize.fstype) == "ext":
                # offline resize2fs refuses to grow a file system mounted since its last full check
                dev = partition_path(plan.disk, resize.number)
                self._run(
                    ["e2fsck", "-f", "-y", dev],
                    msg("Checking {path} before growing it", path=dev),
                )
        for target in plan.targets:
            if target.format or not target.ref.startswith("part:"):
                continue
            if target.mountpoint.startswith("/") and fscaps.fs_kind(target.fstype) == "ntfs":
                number = int(target.ref.removeprefix("part:"))
                dev = partition_path(target.disk or plan.disk, number)
                description = msg("Checking {path} before mounting it", path=dev)
                blocker, _ = self._ntfs_blocker(dev, description)
                if blocker:
                    raise _Failed(
                        msg("{path} can't be mounted: {reason}.", path=dev, reason=blocker)
                    )

    def _affected(self, plan: PartitionPlan) -> dict[str, set[int] | None]:
        affected: dict[str, set[int] | None] = {plan.disk: None if plan.new_table else set()}
        numbers = affected[plan.disk]
        if numbers is not None:
            numbers.update(d.number for d in plan.deletes)
            numbers.update(r.number for r in plan.resizes)
        for target in plan.targets:
            if target.ref.startswith("part:") and (target.format or target.encrypt):
                disk = target.disk or plan.disk
                bucket = affected.setdefault(disk, set())
                if bucket is not None:
                    bucket.add(int(target.ref.removeprefix("part:")))
        return affected

    def _check_not_busy(self, plan: PartitionPlan) -> None:
        for disk, numbers in self._affected(plan).items():
            if numbers is not None and not numbers:
                continue
            rows = self._lsblk_rows(disk)
            for row in rows:
                kind = row.get("type")
                if kind not in ("part", "disk"):
                    continue
                if numbers is not None and (kind == "disk" or row.get("partn") not in numbers):
                    continue
                holders = [r for r in rows if r is not row and self._owner(r, rows) is row]
                self._refuse_if_busy(row, holders)

    def _refuse_if_busy(self, node: dict[str, Any], holders: list[dict[str, Any]]) -> None:
        uses = []
        for mount in node.get("mountpoints") or []:
            if mount == "[SWAP]":
                uses.append(msg("used as swap"))
            elif mount:
                uses.append(msg("mounted at {path}", path=mount))
        for holder in holders:
            name = _base(holder.get("path"))
            mounts = [m for m in holder.get("mountpoints") or [] if m]
            if mounts:
                uses.append(
                    msg(
                        "opened as {name}, mounted at {paths}",
                        name=name,
                        paths=", ".join(mounts),
                    )
                )
            else:
                uses.append(msg("opened as {name}", name=name))
        if uses:
            raise _Failed(
                msg(
                    "{path} is in use ({uses}): close it first.",
                    path=node.get("path"),
                    uses="; ".join(uses),
                )
            )

    def _mappers_to_open(self, plan: PartitionPlan) -> list[str]:
        return [self._mapper_for(t) for t in plan.targets if t.encrypt]

    @staticmethod
    def _mapper_for(target: Target) -> str:
        if _is_root(target):
            return mapper_name("/")
        return mapper_name("swap" if target.fstype == "swap" else target.mountpoint)

    def _preflight_shrink(self, plan: PartitionPlan, resize: Resize) -> None:
        dev = partition_path(plan.disk, resize.number)
        kind = fscaps.fs_kind(resize.fstype)
        new_bytes = (resize.new_end - self._parts[resize.number].start + 1) * plan.sector_size
        fs_bytes = (new_bytes - fscaps.SHRINK_MARGIN) // MIB * MIB
        cmds = fscaps.probe_min_size_commands(resize.fstype, dev, SCRATCH, read_only=False)
        description = msg("Checking {path} before shrinking it", path=dev)
        if kind == "ntfs":
            blocker, info = self._ntfs_blocker(dev, description)
            if blocker:
                raise _Failed(msg("{path} can't be shrunk: {reason}.", path=dev, reason=blocker))
            if not info.ok:
                self._run(cmds[0], description)
            out = info.stdout
        else:
            out = self._run_seq(cmds, description)[-1]
        try:
            if kind == "ext":
                dumpe2fs = ["dumpe2fs", "-h", dev]
                block = fscaps.parse_dumpe2fs_block_size(self._run(dumpe2fs, description).stdout)
                min_bytes = fscaps.parse_resize2fs_min_blocks(out) * block
                fs_bytes = fs_bytes // block * block
            elif kind == "ntfs":
                min_bytes = fscaps.parse_ntfsresize_min(out)
            else:
                min_bytes = fscaps.parse_btrfs_min_dev_size(out)
        except ValueError as exc:
            raise _Failed(
                msg(
                    "{phase} failed: can't read the minimum size of {path}: {error}.",
                    phase=self._phase,
                    path=dev,
                    error=exc.args[0],
                )
            )
        if fs_bytes < min_bytes:
            raise _Failed(
                msg(
                    "{path} can't be shrunk to {size}: it needs at least {minimum}.",
                    path=dev,
                    size=size_text(new_bytes),
                    minimum=size_text(fscaps.min_shrink_bytes(min_bytes)),
                )
            )
        if kind == "ntfs":
            check = fscaps.preflight_shrink_check_commands(resize.fstype, dev, fs_bytes, SCRATCH)
            self._run_seq(check, description)
        self._shrink_to[resize.number] = fs_bytes

    def _ntfs_blocker(self, dev: str, description: str) -> tuple[str, CmdResult]:
        # ntfsresize opens the volume read-only here, so it never sees a hibernated Windows
        hiber = self._run(fscaps.ntfs_hibernation_command(dev), description, check=False)
        if fscaps.parse_ntfs_hibernated(hiber.stdout):
            return fscaps.REASON_HIBERNATED, hiber
        info_cmd = fscaps.probe_min_size_commands("ntfs", dev, SCRATCH, read_only=False)[0]
        info = self._run(info_cmd, description, check=False)
        return fscaps.parse_ntfsresize_blocker(info.stdout + info.stderr), info

    def _settle(self, disk: str) -> None:
        self._run(
            ["partprobe", disk], msg("Re-reading the partition table of {disk}", disk=disk)
        )
        self._run(["udevadm", "settle"], msg("Waiting for udev"))

    def _recreate(self, plan: PartitionPlan, resize: Resize) -> None:
        info, n, disk = self._parts[resize.number], resize.number, _dev(plan.disk)
        # -a 1: sgdisk must keep the existing start even when it isn't 1 MiB aligned.
        cmd = [
            "sgdisk", "-a", "1", "-d", str(n),
            "-n", f"{n}:{info.start}:{resize.new_end}",
            "-t", f"{n}:{info.type_guid}",
            "-u", f"{n}:{info.uuid}",
            "-c", f"{n}:{info.name}",
            *(["-A", f"{n}:=:{info.attributes:016x}"] if info.attributes else []),
            disk,
        ]  # fmt: skip
        self._run(cmd, msg("Resizing partition {number} of {disk}", number=n, disk=disk))
        self._settle(disk)

    def _write_table(self, plan: PartitionPlan) -> None:
        disk = _dev(plan.disk)
        self._result.disk_changed = True
        if plan.new_table:
            self._phase = msg("New partition table")
            self._run(["wipefs", "-a", disk], msg("Wiping signatures on {disk}", disk=disk))
            self._run(
                ["sgdisk", "--zap-all", disk],
                msg("Removing the old partition table of {disk}", disk=disk),
            )
            self._run(
                ["sgdisk", "-o", disk], msg("Creating a GPT partition table on {disk}", disk=disk)
            )

        self._phase = msg("Delete")
        for delete in plan.deletes:
            number = str(delete.number)
            self._run(
                ["sgdisk", "-d", number, disk], msg("Deleting partition {number}", number=number)
            )

        for resize in plan.resizes:
            if not resize.shrink:
                continue
            self._phase = msg("Shrink")
            dev = partition_path(plan.disk, resize.number)
            shrink = fscaps.shrink_fs_commands(
                resize.fstype, dev, self._shrink_to[resize.number], SCRATCH
            )
            self._run_seq(shrink, msg("Shrinking the file system on {path}", path=dev))
            self._recreate(plan, resize)
            grow = fscaps.grow_fs_commands(resize.fstype, dev, SCRATCH)
            self._run_seq(
                grow, msg("Growing the file system on {path} to fill its partition", path=dev)
            )

        for resize in plan.resizes:
            if resize.shrink:
                continue
            self._phase = msg("Grow")
            dev = partition_path(plan.disk, resize.number)
            self._recreate(plan, resize)
            grow = fscaps.grow_fs_commands(resize.fstype, dev, SCRATCH)
            self._run_seq(grow, msg("Growing the file system on {path}", path=dev))

        self._phase = msg("Create")
        for create in plan.creates:
            used = self._used_numbers
            n = next(i for i in range(1, len(used) + 2) if i not in used)
            self._used_numbers.add(n)
            cmd = [
                "sgdisk", "-a", "1",
                "-n", f"{n}:{create.start}:{create.end}",
                "-t", f"{n}:{create.type_guid}",
                "-c", f"{n}:{create.name}",
                disk,
            ]  # fmt: skip
            self._run(
                cmd,
                msg(
                    "Creating partition {number} ({name}) on {disk}",
                    number=n,
                    name=create.name,
                    disk=disk,
                ),
            )

        self._phase = msg("Settle")
        self._settle(disk)

    def _new_partitions(self, plan: PartitionPlan) -> dict[int, str]:
        disk = _dev(plan.disk)
        out = self._run(
            ["lsblk", "-J", "-l", "-b", "-o", "PATH,PKNAME,PARTN,START", disk],
            msg("Listing the partitions of {disk}", disk=disk),
        ).stdout
        try:
            rows = json.loads(out).get("blockdevices", [])
        except ValueError as exc:
            raise _Failed(
                msg(
                    "{phase} failed: can't parse lsblk output: {error}.",
                    phase=self._phase,
                    error=exc,
                )
            ) from None
        base = plan.disk.rsplit("/", 1)[-1]
        return {
            int(row["start"]) * _LSBLK_SECTOR // plan.sector_size: str(row["path"])
            for row in rows
            if row.get("pkname") == base and row.get("start") is not None
        }

    def _resolve(self, plan: PartitionPlan) -> None:
        self._phase = msg("Resolve")
        creates = {c.ref: c for c in plan.creates}
        by_start = self._new_partitions(plan) if plan.creates else {}
        for target in plan.targets:
            if target.ref.startswith("new:"):
                create = creates.get(target.ref[4:])
                path = by_start.get(create.start) if create else None
                if path is None:
                    where = (
                        msg("sector {sector}", sector=create.start) if create else target.ref
                    )
                    raise _Failed(
                        msg(
                            "{phase} failed: no new partition starts at {where} on {disk}.",
                            phase=self._phase,
                            where=where,
                            disk=_dev(plan.disk),
                        )
                    )
            else:
                number = int(target.ref.removeprefix("part:"))
                path = partition_path(target.disk or plan.disk, number)
            self._raw.append(path)
            self._paths.append(path)

    def _encrypt(self, plan: PartitionPlan, passphrase: str) -> None:
        self._phase = msg("Encryption")
        kdf = ["--pbkdf", "pbkdf2"] if self._bootloader == "grub" else []
        result = self._result
        if not passphrase and any(t.encrypt and _is_root(t) for t in plan.targets):
            raise _Failed(
                msg("{phase} failed: the root passphrase is empty.", phase=self._phase)
            )
        for i, target in enumerate(plan.targets):
            if not target.encrypt:
                continue
            raw, root = self._raw[i], _is_root(target)
            name = self._mapper_for(target)
            if name in result.opened_mappers:
                raise _Failed(
                    msg(
                        "{phase} failed: two encrypted partitions would use {name}.",
                        phase=self._phase,
                        name=name,
                    )
                )
            self._paths[i] = f"/dev/mapper/{name}"
            if target.fstype == "swap":
                # plain dm-crypt writes no header, so stale signatures (a LUKS2 backup header)
                # would otherwise survive on the raw partition
                if target.format:
                    self._run(
                        ["wipefs", "-a", raw], msg("Wiping old signatures on {path}", path=raw)
                    )
                self._run([*_PLAIN_SWAP_OPEN, raw, name], msg("Opening {name}", name=name))
                result.opened_mappers.append(name)
                continue
            key = passphrase if root else secrets.token_urlsafe(32)
            fmt = ["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode", *kdf, raw]
            self._run(fmt, msg("Encrypting {path}", path=raw), stdin=key)
            self._run(
                ["cryptsetup", "open", raw, name], msg("Opening {name}", name=name), stdin=key
            )
            result.opened_mappers.append(name)
            result.luks_passphrases[raw.rsplit("/", 1)[-1]] = key

    def _format(self, plan: PartitionPlan) -> None:
        formatted = [(i, t) for i, t in enumerate(plan.targets) if t.format]
        self._phase = msg("Format")
        for i, target in formatted:
            path = self._paths[i]
            cmd = mkfs_command(target.fstype, path, target.label)
            if cmd is None:
                raise _Failed(
                    msg(
                        "{phase} failed: {path} can't be formatted as {fstype}.",
                        phase=self._phase,
                        path=path,
                        fstype=target.fstype,
                    )
                )
            self._run(["wipefs", "-af", path], msg("Wiping old signatures on {path}", path=path))
            self._run(
                cmd, msg("Formatting {path} as {fstype}", path=path, fstype=target.fstype)
            )
            # by-uuid links (fstab, grub-btrfs) exist only once udev re-probed the new filesystem
            self._run(
                ["udevadm", "trigger", "--settle", f"--name-match={path}"],
                msg("Refreshing {path}", path=path),
            )
            if target.is_esp:
                self._result.esp_formatted = True

        self._phase = msg("Btrfs subvolumes")
        for i, target in formatted:
            if target.fstype != "btrfs" or not target.subvolumes:
                continue
            path = self._paths[i]
            cmds = [
                ["mount", "-t", "btrfs", "-o", "subvolid=5", path, SCRATCH],
                *(
                    ["btrfs", "subvolume", "create", f"{SCRATCH}/{sv['name']}"]
                    for sv in target.subvolumes
                ),
                ["umount", SCRATCH],
            ]
            self._run_seq(cmds, msg("Creating Btrfs subvolumes on {path}", path=path))

    def _home_subvolume(self, path: str) -> str:
        listing = self._run_seq(
            [
                ["mount", "-t", "btrfs", "-o", "subvolid=5", path, SCRATCH],
                ["btrfs", "subvolume", "list", SCRATCH],
                ["umount", SCRATCH],
            ],
            msg("Looking for @home on {path}", path=path),
        )[0]
        return "@home" if re.search(r"\spath @home$", listing, re.M) else ""

    def _mount(self, plan: PartitionPlan) -> None:
        self._phase = msg("Mount")
        result = self._result
        tasks: list[tuple[str, list[str]]] = []
        for i, target in enumerate(plan.targets):
            path, raw = self._paths[i], self._raw[i]
            result.assignments.append(
                {
                    "name": raw.rsplit("/", 1)[-1],
                    "path": path,
                    "raw_path": raw,
                    "mountpoint": target.mountpoint,
                    "fstype": target.fstype,
                    "subvolumes": [dict(sv) for sv in target.subvolumes],
                    "format": target.format,
                }
            )
            if _is_root(target):
                result.root_partition = raw
            if target.mountpoint == "/boot/efi":
                result.efi_partition = path
            if target.subvolumes:
                for sv in target.subvolumes:
                    opts = f"{BTRFS_MOUNT_OPTIONS},subvol={sv['name']}"
                    mountpoint = str(sv.get("mountpoint") or "/")
                    tasks.append((mountpoint, ["-t", "btrfs", "-o", opts, path]))
            elif target.mountpoint.startswith("/"):
                fstype = _mount_type(target.fstype)
                args = ["-t", fstype, path]
                if fstype == "btrfs":
                    opts = BTRFS_MOUNT_OPTIONS
                    if target.mountpoint == "/home" and not target.format:
                        subvol = self._home_subvolume(path)
                        opts += f",subvol={subvol}" if subvol else ""
                    args = ["-t", fstype, "-o", opts, path]
                tasks.append((target.mountpoint, args))

        for mountpoint, args in sorted(tasks, key=lambda task: _depth(task[0])):
            dest = self._target_root if mountpoint == "/" else self._dest(mountpoint)
            self._run(["mkdir", "-p", dest], msg("Creating {path}", path=dest))
            self._run(
                ["mount", *args, dest],
                msg("Mounting {device} at {path}", device=args[-1], path=dest),
            )

    def _dest(self, mountpoint: str) -> str:
        return f"{self._target_root.rstrip('/')}/{mountpoint.strip('/')}"

    def _swap(self, plan: PartitionPlan, swap_strategy: str) -> None:
        self._phase = msg("Swap")
        result = self._result
        swaps = [self._paths[i] for i, t in enumerate(plan.targets) if t.fstype == "swap"]
        for path in swaps:
            self._run(["swapon", path], msg("Enabling swap on {path}", path=path))
            result.swap_devices.append(path)
        if swaps or swap_strategy not in ("file", "hibernate"):
            return
        root = next((t for t in plan.targets if _is_root(t)), None)
        steps = swapfile_commands(self._target_root, swap_strategy, root.fstype if root else "")
        swapfile = steps[-1][0][-1]
        self._run(
            ["mkdir", "-p", os.path.dirname(swapfile)], msg("Creating the swapfile directory")
        )
        for cmd, description in steps:
            self._run(cmd, description)
        result.swap_devices.append(swapfile)

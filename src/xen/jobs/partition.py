"""
Partition Job for Xen Installer.

CRITICAL SECURITY WARNING:
This job performs DESTRUCTIVE DISK OPERATIONS that can result in IRREVERSIBLE DATA LOSS.
All operations require explicit confirmation and use dry-run mode by default.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from xen.i18n.messages import Msg, msg
from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.partitioning.executor import PlanExecutor, run_subprocess
from xen.partitioning.plan import (
    PartitionPlan,
    build_alongside_plan,
    build_erase_plan,
    build_manual_plan,
    validate_plan,
)
from xen.utils import disk_detector, disk_release
from xen.utils.network_helper import NetworkHelper

logger = logging.getLogger(__name__)

_MODES = ("auto", "alongside", "manual")
_CHOICE_KEYS = (
    "disk",
    "partition_mode",
    "alongside_partition",
    "alongside_keep_size_bytes",
    "alongside_esp",
    "manual_rows",
    "manual_new_table",
)
DISK_CHANGED = msg(
    "The disk was already changed by a previous attempt. "
    "Go back to the Partition step so the disk is scanned again."
)
DEFAULT_MIN_INSTALL_GIB = 25
_DEFAULT_BTRFS_SUBVOLUMES = (
    ("@", "/"),
    ("@home", "/home"),
    ("@root", "/root"),
    ("@srv", "/srv"),
    ("@cache", "/var/cache"),
    ("@tmp", "/var/tmp"),
    ("@log", "/var/log"),
    ("@swap", "/swap"),
    # Snapper snapshots outside @ (ArchWiki Snapper "Suggested filesystem layout")
    ("@snapshots", "/.snapshots"),
)


def _exc_text(exc: Exception) -> str:
    first = exc.args[0] if exc.args else None
    return first if isinstance(first, Msg) else str(exc)


def _join_lines(lines: list[str]) -> str:
    text = lines[0]
    for line in lines[1:]:
        text = msg("{head}\n{tail}", head=text, tail=line)
    return text


def default_btrfs_subvolumes(config: dict[str, Any]) -> list[dict[str, Any]]:
    configured = config.get("btrfs_subvolumes")
    if configured:
        return [dict(sv) for sv in configured]
    return [{"name": n, "mountpoint": m} for n, m in _DEFAULT_BTRFS_SUBVOLUMES]


def _is_root_entry(entry: dict[str, Any]) -> bool:
    """A Btrfs subvolume layout replaces the entry's own mountpoint; its root is at "" or "/"."""
    subvolumes = entry.get("subvolumes")
    if subvolumes:
        return any(str(sv.get("mountpoint", "")) in ("", "/") for sv in subvolumes)
    return str(entry.get("mountpoint", "")) == "/"


def is_swap_entry(entry: dict[str, Any]) -> bool:
    return (
        str(entry.get("mountpoint", "")) == "swap"
        or str(entry.get("fstype", "")).lower() == "swap"
    )


def _is_mapper_path(path: Any) -> bool:
    return str(path or "").startswith("/dev/mapper/")


def root_assignment(assignments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The single source of truth for which assignment is root."""
    return next((a for a in assignments if _is_root_entry(a)), None)


def manual_has_separate_boot(assignments: list[dict[str, Any]]) -> bool:
    """True if a plain (unencrypted) partition is mounted at ``/boot``."""
    return any(
        str(a.get("mountpoint", "")) == "/boot" and not _is_mapper_path(a.get("path"))
        for a in assignments
    )


def resolve_root_encryption(selections: dict[str, Any]) -> tuple[bool, str]:
    """(is_root_encrypted, raw_root_partition_path) from the partition job's assignments.

    A root on a mapper with no ``raw_path`` returns ``(True, "")`` so callers
    fail loudly instead of treating it as unencrypted.
    """
    root = root_assignment(selections.get("partition_assignments", []))
    if root is None or not _is_mapper_path(root.get("path")):
        return False, ""
    return True, str(root.get("raw_path", ""))


def resume_swap_partition(selections: dict[str, Any]) -> str:
    """The unencrypted swap partition hibernation resumes from, or ""."""
    return next(
        (
            str(a["path"])
            for a in selections.get("partition_assignments", [])
            if is_swap_entry(a) and a.get("path") and not _is_mapper_path(a.get("path"))
        ),
        "",
    )


class PartitionJob(BaseJob):
    """
    Disk partitioning and formatting job.

    SECURITY MODEL:
    - dry_run=True by default (the plan is validated and logged, nothing is executed)
    - confirmed=False by default (explicit user confirmation required)
    """

    name = "partition"
    description = "Disk partitioning and formatting"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._plan: PartitionPlan | None = None
        # Reused across retries so the executor can release what its previous run left open.
        self._executor: PlanExecutor | None = None
        self._changed_by: str | None = None

    def validate(self, context: JobContext) -> JobResult:
        context.report_progress(0, msg("Validating partition configuration..."))
        self._plan = None
        selections = context.selections

        disk = str(selections.get("disk") or "")
        if not disk:
            return JobResult.fail(msg("Disk selection is required"), error_code=30)

        mode = selections.get("partition_mode", "auto")
        if mode not in _MODES:
            return JobResult.fail(
                msg("Invalid partition mode: {mode}", mode=mode), error_code=31
            )

        dry_run = selections.get("dry_run", True)
        if not dry_run and not os.path.isdir("/sys/firmware/efi"):
            return JobResult.fail(
                msg(
                    "This system was not booted in UEFI mode. XeroLinux only installs in "
                    "UEFI mode: reboot the live media using its UEFI boot entry."
                ),
                error_code=65,
            )

        if not os.path.exists(disk):
            return JobResult.fail(msg("Disk not found: {disk}", disk=disk), error_code=32)

        swap_strategy = selections.get("swap_strategy")
        if swap_strategy is not None and swap_strategy not in ("file", "none", "hibernate"):
            return JobResult.fail(
                msg("Invalid swap strategy: {strategy}", strategy=swap_strategy),
                error_code=47,
            )

        # A plan built from the pre-install layout no longer matches a disk it already changed.
        if mode != "auto" and self._changed_by == self._choice(selections):
            return JobResult.fail(DISK_CHANGED, error_code=26)

        if not dry_run:
            # A later job's failure leaves the target mounted and its mappers open.
            error = self._executor_for(context).release_previous()
            if error:
                return JobResult.fail(error, error_code=27)

        # Root-only probes (min sizes, hibernation) are skipped in a dry run.
        disks = disk_detector.list_disks(probe=not dry_run and mode != "auto")
        name = disk.rsplit("/", 1)[-1]
        target = next((d for d in disks if d.get("name") == name), None)
        if target is None:
            return JobResult.fail(
                msg("Cannot read disk info for {disk} (lsblk did not report it)", disk=disk),
                error_code=33,
            )

        try:
            plan = self._build_plan(selections, mode, target)
        except (KeyError, TypeError, ValueError) as exc:
            return JobResult.fail(
                msg("Invalid partition selection: {error}", error=_exc_text(exc)),
                error_code=53,
            )

        if any(t.encrypt for t in plan.targets) and not self._passphrase(selections, mode):
            return JobResult.fail(
                msg("Encryption enabled but no passphrase provided"), error_code=46
            )

        has_internet = selections.get("has_internet")
        if has_internet is None:
            has_internet = NetworkHelper.check_internet_connectivity()
        errors = validate_plan(
            plan,
            target,
            uefi=True,
            has_internet=bool(has_internet),
            min_install_gib=int(self.config.get("min_install_gib", DEFAULT_MIN_INSTALL_GIB)),
            other_disks=[d for d in disks if d is not target],
        )
        if errors:
            return JobResult.fail(_join_lines(errors), error_code=49, data={"errors": errors})

        self._plan = plan
        context.report_progress(50, msg("Validation complete"))
        return JobResult.ok(msg("Partition configuration valid"), data={"plan": plan.to_dict()})

    def _build_plan(
        self, selections: dict[str, Any], mode: str, disk: dict[str, Any]
    ) -> PartitionPlan:
        if mode == "manual":
            return build_manual_plan(
                disk,
                list(selections.get("manual_rows") or []),
                bool(selections.get("manual_new_table")),
            )
        fstype = str(selections.get("filesystem", "xfs"))
        encrypt = bool(selections.get("encryption"))
        subvolumes = self._btrfs_subvolumes(selections) if fstype == "btrfs" else []
        if mode == "auto":
            efi_size_mb = int(selections.get("efi_size_mb", 2000))
            return build_erase_plan(disk, fstype, encrypt, efi_size_mb, subvolumes)
        esp = selections["alongside_esp"]
        return build_alongside_plan(
            disk,
            int(selections["alongside_partition"]),
            int(selections["alongside_keep_size_bytes"]),
            fstype,
            encrypt,
            int(esp["number"]),
            str(esp.get("disk") or disk["name"]),
            subvolumes,
        )

    def _btrfs_subvolumes(self, selections: dict[str, Any]) -> list[dict[str, Any]]:
        configured = selections.get("btrfs_subvolumes")
        if configured:
            return [dict(sv) for sv in configured]
        return default_btrfs_subvolumes(self.config)

    @staticmethod
    def _choice(selections: dict[str, Any]) -> str:
        return json.dumps({k: selections.get(k) for k in _CHOICE_KEYS}, sort_keys=True)

    def disk_rescanned(self) -> None:
        self._changed_by = None

    def release_previous(self) -> str:
        return self._executor.release_previous() if self._executor else ""

    def _executor_for(self, context: JobContext) -> PlanExecutor:
        if self._executor is None:
            bootloader = str(context.selections.get("bootloader", "grub"))
            self._executor = PlanExecutor(run_subprocess, context.target_root, bootloader)
        return self._executor

    @staticmethod
    def _passphrase(selections: dict[str, Any], mode: str) -> str:
        key = "manual_root_passphrase" if mode == "manual" else "encryption_passphrase"
        return str(selections.get(key) or "")

    def run(self, context: JobContext) -> JobResult:
        context.report_progress(0, msg("Starting partitioning job..."))
        if self._plan is None:
            validation = self.validate(context)
            if not validation.success:
                return validation
        plan, self._plan = self._plan, None
        assert plan is not None

        selections = context.selections
        disk = str(selections["disk"])
        mode = selections.get("partition_mode", "auto")
        dry_run = selections.get("dry_run", True)
        if not dry_run and not selections.get("confirmed", False):
            return JobResult.fail(
                msg(
                    "SECURITY: Cannot execute without explicit confirmation. "
                    "Set confirmed=True to proceed with destructive operations."
                ),
                error_code=38,
            )

        summary = {"disk": disk, "mode": mode, "dry_run": dry_run}
        if dry_run:
            logger.info("[DRY-RUN] Partition plan, not executed: %s", plan.to_dict())
            context.report_progress(100, msg("Partitioning simulated (dry run)"))
            return JobResult.ok(
                msg("[DRY-RUN] Disk {disk} not modified", disk=disk), data=summary
            )

        if disk_release.holds_running_system(disk):
            return JobResult.fail(
                msg(
                    "Refusing to touch {disk}: it backs the system Xen is running from.",
                    disk=disk,
                ),
                error_code=56,
            )

        # Manual mode has no swap choice: a stale auto-mode swapfile choice must not leak in.
        swap_strategy = str(selections.get("swap_strategy", "none"))
        if mode == "manual":
            if swap_strategy != "none" and not any(t.mountpoint == "swap" for t in plan.targets):
                logger.info(
                    "Manual mode has no swap choice: ignoring swap strategy %r", swap_strategy
                )
            swap_strategy = "none"
        logger.warning("Executing the partition plan on %s", disk)
        context.report_progress(10, msg("Partitioning..."))
        executor = self._executor_for(context)
        result = executor.execute(plan, self._passphrase(selections, mode), swap_strategy)
        if result.disk_changed:
            self._changed_by = self._choice(selections)
        if not result.ok:
            return JobResult.fail(result.error, error_code=44)

        root = root_assignment(result.assignments) or {}
        selections.update(
            {
                "partition_assignments": result.assignments,
                "luks_passphrases": dict(result.luks_passphrases),
                "root_partition": result.root_partition,
                "efi_partition": result.efi_partition,
                "esp_formatted": result.esp_formatted,
                "target_swap_devices": list(result.swap_devices),
                "opened_mappers": list(result.opened_mappers),
                "encryption": _is_mapper_path(root.get("path")),
                "filesystem": str(root.get("fstype", "")),
                "swap_strategy": swap_strategy,
            }
        )
        context.report_progress(100, msg("Partitioning complete"))
        return JobResult.ok(
            msg("Disk {disk} partitioned successfully", disk=disk), data=summary
        )

    def estimate_duration(self) -> int:
        return 60

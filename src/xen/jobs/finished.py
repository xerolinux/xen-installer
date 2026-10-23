"""
Finished Job - Installation completion, summary generation, and cleanup.

Handles the final phase of installation, including:
- Summary generation from all previous jobs
- Clean unmounting of filesystems
- Log saving
- Reboot/shutdown preparation
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from xen.i18n.messages import msg
from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.utils.log_capture import resolve_log_path

logger = logging.getLogger(__name__)


def _active_swaps() -> list[str]:
    try:
        lines = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return []
    # /proc/swaps escapes spaces in file paths as octal \040.
    return [line.split()[0].replace("\\040", " ") for line in lines if line.strip()]


class FinishedJob(BaseJob):
    """
    Installation completion job.

    Responsibilities:
    - Generate installation summary from previous job results
    - Clean unmounting of target filesystems
    - Save installation logs to target system
    - Prepare for reboot/shutdown/continue
    """

    name = "finished"
    description = "Installation completion and cleanup"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the finished job."""
        super().__init__(config)
        self._summary: dict[str, Any] = {}

    def _generate_summary(self, context: JobContext) -> dict[str, Any]:
        """
        Generate installation summary from context selections.

        Args:
            context: Execution context with all job selections

        Returns:
            Dictionary containing installation summary
        """
        selections = context.selections
        summary: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "target_root": context.target_root,
            "system": {},
            "partitions": {},
            "user": {},
            "locale": {},
            "status": "completed",
        }

        # System information
        system_info: dict[str, Any] = {}
        if "hostname" in selections:
            system_info["hostname"] = selections["hostname"]
        summary["system"] = system_info

        # Partition information
        partition_info: dict[str, Any] = {}
        if "disk" in selections:
            partition_info["disk"] = selections["disk"]
            partition_info["filesystem"] = selections.get("filesystem", "ext4")
            partition_info["mode"] = selections.get("partition_mode", "auto")
            partition_info["swap"] = selections.get("swap_strategy", "none")
        summary["partitions"] = partition_info

        # User information (without sensitive data)
        user_info: dict[str, Any] = {}
        if "username" in selections:
            user_info["username"] = selections["username"]
            user_info["fullname"] = selections.get("fullname", "")
        summary["user"] = user_info

        # Locale information
        locale_info: dict[str, Any] = {}
        if "locale" in selections:
            locale_info["locale"] = selections["locale"]
        if "timezone" in selections:
            locale_info["timezone"] = selections["timezone"]
        if "keymap" in selections:
            locale_info["keymap"] = selections["keymap"]
        summary["locale"] = locale_info

        return summary

    def _save_logs(self, context: JobContext) -> JobResult:
        """
        Save installation logs to target system.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        save_logs = context.selections.get("save_logs", True)
        if not save_logs:
            logger.info("Log saving disabled, skipping")
            return JobResult.ok(msg("Log saving skipped"))

        target_root = Path(context.target_root)
        log_dir = target_root / "var" / "log" / "xen-installer"

        try:
            # Create log directory
            log_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created log directory: {log_dir}")

            # Save summary as JSON
            import json

            summary_file = log_dir / "install-summary.json"
            summary_file.write_text(
                json.dumps(self._summary, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(f"Saved installation summary to {summary_file}")

            log_source = resolve_log_path()
            if log_source.exists():
                log_dest = log_dir / f"xen-{datetime.now():%Y%m%d-%H%M%S}.log"
                shutil.copy2(log_source, log_dest)
                logger.info(f"Copied log file: {log_source} -> {log_dest}")

            return JobResult.ok(msg("Logs saved to {path}", path=log_dir))

        except OSError as e:
            logger.warning(f"Failed to save logs: {e}")
            # Non-critical: continue even if log saving fails
            return JobResult.ok(msg("Log saving failed (non-critical): {error}", error=e))

    def _safe_unmount(self, mount_point: Path, lazy: bool = False) -> bool:
        """
        Safely unmount a filesystem.

        Args:
            mount_point: Path to unmount
            lazy: If True, use lazy unmount (-l) for busy filesystems

        Returns:
            True if unmounted successfully, False otherwise
        """
        if not os.path.ismount(mount_point):
            logger.debug(f"Mount point {mount_point} not mounted, skipping")
            return True

        # Try normal unmount first
        try:
            cmd = ["umount", str(mount_point)]
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.info(f"Unmounted {mount_point}")
            return True

        except subprocess.CalledProcessError as e:
            if not lazy:
                logger.warning(f"Failed to unmount {mount_point}: {e.stderr}")
                logger.info("Retrying with lazy unmount...")
                return self._safe_unmount(mount_point, lazy=True)

            # Try lazy unmount
            try:
                cmd = ["umount", "-l", str(mount_point)]
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                logger.info(f"Lazy unmounted {mount_point}")
                return True

            except subprocess.CalledProcessError as lazy_error:
                logger.error(f"Failed to lazy unmount {mount_point}: {lazy_error.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Unmount timeout for {mount_point}")
            return False

        except FileNotFoundError:
            logger.error("umount command not found")
            return False

    def _safe_unmount_recursive(self, target_root: Path) -> bool:
        """umount -R target_root - manual mode can mount extra partitions
        (e.g. /home) under it, so a fixed set of paths isn't enough."""
        if not os.path.ismount(target_root):
            logger.debug(f"{target_root} not mounted, skipping recursive unmount")
            return True

        try:
            subprocess.run(
                ["umount", "-R", str(target_root)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            logger.info(f"Recursively unmounted {target_root}")
            return True
        except subprocess.CalledProcessError as e:
            logger.warning(
                f"Recursive unmount of {target_root} failed: {e.stderr}; retrying lazy"
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Recursive unmount timeout for {target_root}")
            return False
        except FileNotFoundError:
            logger.error("umount command not found")
            return False

        try:
            subprocess.run(
                ["umount", "-R", "-l", str(target_root)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            logger.info(f"Recursively lazy-unmounted {target_root}")
            return True
        except subprocess.CalledProcessError as lazy_error:
            logger.error(f"Failed to recursively unmount {target_root}: {lazy_error.stderr}")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"Recursive lazy unmount timeout for {target_root}")
            return False
        except FileNotFoundError:
            logger.error("umount command not found")
            return False

    @staticmethod
    def _swapoff_target(context: JobContext) -> list[str]:
        """swapoff every swap under the target and every swap the partition job enabled.

        An active swap keeps its filesystem or mapper busy, so this runs before
        the unmount and the mapper close. Returns the entries still active.
        """
        prefix = context.target_root.rstrip("/") + "/"
        ours = {os.path.realpath(d) for d in context.selections.get("target_swap_devices", [])}
        failures: list[str] = []
        for entry in _active_swaps():
            if not (entry.startswith(prefix) or os.path.realpath(entry) in ours):
                continue
            try:
                result = subprocess.run(
                    ["swapoff", entry], check=False, capture_output=True, text=True, timeout=300
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning(f"swapoff {entry} failed: {e}")
                failures.append(entry)
                continue
            if result.returncode != 0:
                logger.warning(f"swapoff {entry} failed: {result.stderr}")
                failures.append(entry)
        return failures

    def _cleanup_mounts(self, context: JobContext) -> JobResult:
        """
        Clean up all mounted filesystems.

        Order matters (each step frees what the next one needs):
        1. swapoff every swap under the target and every swap the
           partition job enabled (an active swap keeps its filesystem busy)
        2. /mnt/target/boot/efi (belt-and-suspenders)
        3. /mnt/target and every submount still under it, recursively
           (umount -R) - covers root itself plus any manual-mode extra
           mountpoint (e.g. /home), not just the two hardcoded paths
        4. Every mapper the partition job opened, swap included

        Args:
            context: Execution context

        Returns:
            JobResult indicating cleanup status
        """
        target_root = Path(context.target_root)
        errors = []

        errors += [f"Failed to deactivate swap: {s}" for s in self._swapoff_target(context)]

        # Unmount EFI partition first (belt-and-suspenders - the recursive
        # unmount below covers it too, but doing it explicitly surfaces an
        # EFI-specific unmount failure distinctly).
        efi_mount = target_root / "boot" / "efi"
        if os.path.ismount(efi_mount) and not self._safe_unmount(efi_mount):
            errors.append(msg("Failed to unmount EFI partition: {path}", path=efi_mount))

        # Must happen before any mapper close below, or the close can race
        # a still-busy mount.
        if not self._safe_unmount_recursive(target_root):
            errors.append(
                msg(
                    "Failed to unmount target root and its submounts: {path}",
                    path=target_root,
                )
            )

        for mapper_name in reversed(context.selections.get("opened_mappers", [])):
            try:
                close_result = subprocess.run(
                    ["cryptsetup", "close", mapper_name],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.warning(f"Failed to close mapper {mapper_name}: {e}")
                continue
            if close_result.returncode == 0:
                logger.info(f"Closed mapper {mapper_name}")
            else:
                logger.warning(f"Failed to close mapper {mapper_name}: {close_result.stderr}")

        if errors:
            return JobResult.fail(
                message=msg(
                    "Cleanup completed with {count} error(s): {errors}",
                    count=len(errors),
                    errors="; ".join(errors),
                ),
                error_code=50,
                data={"errors": errors},
            )

        return JobResult.ok(msg("All filesystems unmounted successfully"))

    def _prepare_action(self, context: JobContext) -> JobResult:
        """
        Prepare for post-installation action (reboot/shutdown/continue).

        Args:
            context: Execution context

        Returns:
            JobResult with action preparation status
        """
        action = context.selections.get("action", "continue")

        if action == "reboot":
            logger.info("System ready for reboot")
            return JobResult.ok(
                msg("Ready to reboot"),
                data={
                    "action": "reboot",
                    "command": "systemctl reboot",
                },
            )

        elif action == "shutdown":
            logger.info("System ready for shutdown")
            return JobResult.ok(
                msg("Ready to shutdown"),
                data={
                    "action": "shutdown",
                    "command": "systemctl poweroff",
                },
            )

        elif action == "continue":
            logger.info("Installation complete, system ready for inspection")
            return JobResult.ok(
                msg("Installation complete"),
                data={"action": "continue"},
            )

        else:
            logger.warning(f"Unknown action: {action}, defaulting to 'continue'")
            return JobResult.ok(
                msg("Installation complete (unknown action)"),
                data={"action": "continue"},
            )

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate that finished job can proceed.

        Args:
            context: Execution context

        Returns:
            JobResult indicating validation status
        """
        # Validate action if specified
        action = context.selections.get("action", "continue")
        valid_actions = ["reboot", "shutdown", "continue"]

        if action not in valid_actions:
            return JobResult.fail(
                msg(
                    "Invalid action: {action}. Must be one of: {valid}",
                    action=action,
                    valid=", ".join(valid_actions),
                ),
                error_code=49,
            )

        # Validate save_logs if specified
        save_logs = context.selections.get("save_logs", True)
        if not isinstance(save_logs, bool):
            return JobResult.fail(
                msg("Invalid save_logs value: must be boolean"),
                error_code=49,
            )

        return JobResult.ok(msg("Finished job configuration valid"))

    def run(self, context: JobContext) -> JobResult:
        """
        Execute the finished job.

        Steps:
        1. Generate installation summary
        2. Save logs to target system
        3. Clean up mounted filesystems
        4. Prepare for next action (reboot/shutdown/continue)

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(0, msg("Finishing installation..."))

        # Validate first
        validation = self.validate(context)
        if not validation.success:
            return validation

        # Step 1: Generate summary
        context.report_progress(10, msg("Generating installation summary..."))
        self._summary = self._generate_summary(context)
        logger.info("Installation summary generated")

        # Step 2: Save logs
        context.report_progress(30, msg("Saving installation logs..."))
        log_result = self._save_logs(context)
        if not log_result.success:
            logger.warning(f"Log saving failed: {log_result.message}")
            # Continue anyway (non-critical)

        # Step 3: Cleanup mounts
        context.report_progress(60, msg("Cleaning up filesystems..."))
        cleanup_result = self._cleanup_mounts(context)
        if not cleanup_result.success:
            # Cleanup failure is critical
            return JobResult.fail(
                msg("Cleanup failed: {error}", error=cleanup_result.message),
                error_code=cleanup_result.error_code,
                data={
                    "summary": self._summary,
                    "cleanup_errors": cleanup_result.data.get("errors", []),
                },
            )

        # Step 4: Prepare action
        context.report_progress(90, msg("Preparing for completion..."))
        action_result = self._prepare_action(context)

        context.report_progress(100, msg("Installation complete!"))

        # Build final result data
        result_data = {
            "summary": self._summary,
            "action": action_result.data.get("action", "continue"),
            "reboot_ready": action_result.data.get("action") in ["reboot", "shutdown"],
        }

        if "command" in action_result.data:
            result_data["command"] = action_result.data["command"]

        return JobResult.ok(
            msg("Installation completed successfully"),
            data=result_data,
        )

    def cleanup(self, context: JobContext) -> None:
        """
        Cleanup after job execution failure.

        Ensures unmounting even on failure to prevent system corruption.

        Args:
            context: Execution context
        """
        logger.info("Running emergency cleanup...")
        cleanup_result = self._cleanup_mounts(context)

        if not cleanup_result.success:
            logger.error(f"Emergency cleanup failed: {cleanup_result.message}")
            logger.error("Manual unmounting may be required!")
        else:
            logger.info("Emergency cleanup completed successfully")

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Returns:
            Estimated duration (finished job is quick)
        """
        return 10

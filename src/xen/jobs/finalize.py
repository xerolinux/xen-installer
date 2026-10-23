"""
Finalize Job - run XeroLinux's live-ISO finalization scripts on the target.

These scripts ship under /usr/local/bin/ on the target (extracted from the
live squashfs) - this job just invokes them, in order, via arch-chroot.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from xen.i18n.messages import msg
from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)

_GRUB_CMDLINE_RE = re.compile(r'^(GRUB_CMDLINE_LINUX_DEFAULT)="(.*)"$', re.MULTILINE)

# Matches snapshots.py's own naming - see that file for why this script
# gets disabled before bootloader.py's grub-mkconfig runs.
_SNAPSHOT_DETECTION_SCRIPT = "41_snapshots-btrfs"
_SNAPSHOT_DETECTION_SCRIPT_DISABLED = "41_snapshots-btrfs.xen-disabled"


class FinalizeJob(BaseJob):
    """
    Runs a config-driven, ordered list of already-installed scripts via
    arch-chroot. A failing script is logged as a warning and does not fail
    the job: these are XeroLinux polish/finalization steps, not required
    for a bootable/usable target system.

    Configuration (from xen.yaml):
        commands: list[str]  # absolute paths, run in order
        timeout: int = 300   # per-command timeout in seconds
    """

    name = "finalize"
    description = "Run XeroLinux finalization scripts"
    _label = "finalization"

    def run(self, context: JobContext) -> JobResult:
        if context.selections.get("dry_run"):
            context.report_progress(100, msg("Skipped (dry run)"))
            return JobResult.ok(self._skipped_text())

        commands: list[str] = self._config.get("commands", [])
        target_root = context.target_root
        timeout = self._config.get("timeout", 300)
        total = len(commands)
        failed: list[str] = []
        # Scripts that run pacman would otherwise leave snap-pac pre/post pairs ahead of
        # the "Fresh installation" snapshot. arch-chroot passes the environment through.
        env = {**os.environ, "SNAP_PAC_SKIP": "y"}

        for index, command in enumerate(commands):
            context.report_progress(
                int((index / total) * 100), msg("Running {command}...", command=command)
            )
            cmd = ["arch-chroot", target_root, command]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout, env=env
                )
                if result.returncode != 0:
                    logger.warning(
                        f"{self._label} script {command} exited "
                        f"{result.returncode}: {result.stderr}"
                    )
                    failed.append(command)
                else:
                    logger.debug(f"{command}: {result.stdout}")
            except subprocess.TimeoutExpired:
                logger.warning(f"{self._label} script {command} timed out after {timeout}s")
                failed.append(command)
            except FileNotFoundError:
                return JobResult.fail(
                    msg("arch-chroot not found. Cannot access target system."),
                    error_code=63,
                )

        self._after_commands(target_root, context)

        context.report_progress(100, self._complete_text())
        if not commands:
            return JobResult.ok(self._none_text())
        return JobResult.ok(
            self._ran_text(total, len(failed)),
            data={"failed": failed},
        )

    def _skipped_text(self) -> str:
        if self._label == "pre-configuration":
            return msg("Skipped: pre-configuration scripts (dry run)")
        return msg("Skipped: finalization scripts (dry run)")

    def _complete_text(self) -> str:
        if self._label == "pre-configuration":
            return msg("Pre-configuration complete")
        return msg("Finalization complete")

    def _none_text(self) -> str:
        if self._label == "pre-configuration":
            return msg("No pre-configuration scripts configured")
        return msg("No finalization scripts configured")

    def _ran_text(self, total: int, failed: int) -> str:
        if self._label == "pre-configuration":
            return msg(
                "Ran {total} pre-configuration scripts ({failed} failures, non-fatal)",
                total=total,
                failed=failed,
            )
        return msg(
            "Ran {total} finalization scripts ({failed} failures, non-fatal)",
            total=total,
            failed=failed,
        )

    def _after_commands(self, target_root: str, context: JobContext) -> None:
        self._dedupe_grub_cmdline(target_root)
        self._take_fresh_install_snapshot(target_root, context)

    def _dedupe_grub_cmdline(self, target_root: str) -> None:
        # Some finalize scripts (e.g. nouveau) sed-append kernel params
        # without checking for existing tokens, producing duplicates.
        # No-op for systemd-boot targets (file won't exist).
        grub_default = Path(target_root) / "etc" / "default" / "grub"
        try:
            content = grub_default.read_text(encoding="utf-8")
        except OSError:
            return

        match = _GRUB_CMDLINE_RE.search(content)
        if not match:
            return

        tokens = match.group(2).split()
        seen: set[str] = set()
        deduped: list[str] = []
        for token in tokens:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(token)

        if deduped == tokens:
            return

        new_line = f'GRUB_CMDLINE_LINUX_DEFAULT="{" ".join(deduped)}"'
        new_content = content[: match.start()] + new_line + content[match.end() :]

        try:
            grub_default.write_text(new_content, encoding="utf-8")
        except OSError as e:
            logger.warning(f"Failed to write deduped {grub_default}: {e}")
            return

        logger.info("Deduplicated GRUB_CMDLINE_LINUX_DEFAULT tokens")

        try:
            subprocess.run(
                ["arch-chroot", target_root, "grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Could not regenerate grub.cfg after dedup: {e}")

    def _take_fresh_install_snapshot(self, target_root: str, context: JobContext) -> None:
        """One permanent "fresh install" snapshot, taken last (matches
        CachyOS's shellprocess@btrfs_snapshot timing) so it captures the
        fully-configured system - never auto-removed (no cleanup
        algorithm). Restores grub-btrfs's snapshot-detection script (see
        snapshots.py) and regenerates grub.cfg so it shows up in the boot
        menu immediately. Non-fatal."""
        if context.selections.get("filesystem") != "btrfs":
            return

        self._restore_snapshot_detection_script(target_root)

        try:
            result = subprocess.run(
                [
                    "arch-chroot",
                    target_root,
                    "snapper",
                    "--no-dbus",
                    "-c",
                    "root",
                    "create",
                    "--description",
                    "Fresh installation",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Could not take fresh-install snapshot: {e}")
            return

        if result.returncode != 0:
            logger.warning(f"Fresh-install snapshot failed: {result.stderr}")
            return

        logger.info("Took fresh-install Btrfs snapshot")

        try:
            subprocess.run(
                ["arch-chroot", target_root, "grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Could not regenerate grub.cfg after fresh-install snapshot: {e}")

    def _restore_snapshot_detection_script(self, target_root: str) -> None:
        """Reverses snapshots.py's disable: renames back and restores the
        executable bit (the part grub-mkconfig actually checks). Non-fatal:
        this is defensive cleanup, not something that should fail the
        install if it doesn't work."""
        disabled = Path(target_root) / "etc" / "grub.d" / _SNAPSHOT_DETECTION_SCRIPT_DISABLED
        if not disabled.exists():
            return
        script = disabled.with_name(_SNAPSHOT_DETECTION_SCRIPT)
        try:
            disabled.rename(script)
            script.chmod(0o755)
        except OSError as e:
            logger.warning(f"Failed to restore {script}: {e}")
            return
        logger.info(f"Restored {script}")

    def estimate_duration(self) -> int:
        return max(30, len(self._config.get("commands", [])) * 15)

"""
System Update Job - full pacman system update before installing new packages.

Ranks mirrors, strips a pacman DB key copied from the live ISO that newer
pacman doesn't understand, then runs a full sync + keyring refresh + upgrade
so the target has zero pending updates before anything new gets installed.
"""

from __future__ import annotations

import logging
import subprocess

from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.utils.network_helper import NetworkHelper

logger = logging.getLogger(__name__)


class SystemUpdateJob(BaseJob):
    """
    Full system update job (pacman only), run before installing new packages.

    Configuration (from xen.yaml):
        timeout_reflector: int = 60   # mirror ranking, before the first sync
        timeout_strip_db: int = 60    # strip stale %INSTALLED_DB% entries
        timeout_syncdb: int = 600     # pacman -Syy
        timeout_keyring: int = 300    # archlinux-keyring refresh
        timeout_upgrade: int = 5400   # pacman -Su
    """

    name = "systemupdate"
    description = "Full system update"

    STRIP_INSTALLED_DB_SCRIPT = (
        "find /var/lib/pacman/local/ -type f -name 'desc' "
        "-exec sed -i '/^%INSTALLED_DB%$/,+2d' {} +"
    )

    REFLECTOR_COMMAND = [
        "reflector",
        "--ipv4",
        "--ipv6",
        "--protocol",
        "https",
        "--age",
        "12",
        "--latest",
        "20",
        "--sort",
        "rate",
        "--download-timeout",
        "5",
        "--save",
        "/etc/pacman.d/mirrorlist",
    ]

    def _run_chroot(
        self,
        command: list[str],
        target_root: str,
        timeout: int,
        error_code: int,
    ) -> JobResult:
        cmd = ["arch-chroot", target_root, *command]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            logger.debug("systemupdate: %s", result.stdout)
            return JobResult.ok()
        except subprocess.CalledProcessError as e:
            return JobResult.fail(
                f"Command failed: {' '.join(command)}: {e.stderr}",
                error_code=error_code,
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail(
                f"Command timed out after {timeout}s: {' '.join(command)}",
                error_code=error_code,
            )
        except FileNotFoundError:
            return JobResult.fail(
                "arch-chroot not found. Cannot access target system.",
                error_code=error_code,
            )

    def run(self, context: JobContext) -> JobResult:
        # Connectivity is probed once at startup (bridge.py) so the UI's
        # "offline mode" notice matches what the job pipeline actually does.
        # Fall back to a live check when that wasn't set (e.g. direct job
        # invocation in tests, or a config that skips the GUI bridge).
        has_internet = context.selections.get("has_internet")
        if has_internet is None:
            has_internet = NetworkHelper.check_internet_connectivity()

        if not has_internet:
            return JobResult.ok("Skipped (offline)")

        if context.selections.get("dry_run"):
            context.report_progress(100, "Skipped (dry run)")
            return JobResult.ok("Skipped: system update (dry run)")

        target_root = context.target_root

        context.report_progress(5, "Ranking mirrors for fastest sync speed...")
        reflector_result = self._run_chroot(
            self.REFLECTOR_COMMAND,
            target_root,
            self._config.get("timeout_reflector", 60),
            error_code=55,
        )
        if not reflector_result.success:
            logger.warning(f"Mirror ranking failed (non-fatal): {reflector_result.message}")

        context.report_progress(10, "Cleaning up live-ISO package database...")
        result = self._run_chroot(
            ["sh", "-c", self.STRIP_INSTALLED_DB_SCRIPT],
            target_root,
            self._config.get("timeout_strip_db", 60),
            error_code=51,
        )
        if not result.success:
            return result

        context.report_progress(30, "Synchronizing package databases...")
        result = self._run_chroot(
            ["pacman", "-Syy", "--noconfirm"],
            target_root,
            self._config.get("timeout_syncdb", 600),
            error_code=52,
        )
        if not result.success:
            return result

        context.selections["repos_synced"] = True

        context.report_progress(50, "Refreshing package keyring...")
        result = self._run_chroot(
            ["pacman", "-S", "--noconfirm", "archlinux-keyring"],
            target_root,
            self._config.get("timeout_keyring", 300),
            error_code=53,
        )
        if not result.success:
            return result

        context.report_progress(60, "Upgrading system packages...")
        result = self._run_chroot(
            ["pacman", "-Su", "--noconfirm", "--disable-download-timeout"],
            target_root,
            self._config.get("timeout_upgrade", 5400),
            error_code=54,
        )
        if not result.success:
            return result

        context.report_progress(100, "System update complete")
        return JobResult.ok("System fully updated")

    def estimate_duration(self) -> int:
        return (
            self._config.get("timeout_reflector", 60)
            + self._config.get("timeout_strip_db", 60)
            + self._config.get("timeout_syncdb", 600)
            + self._config.get("timeout_keyring", 300)
            + 900
        )

"""
Services Job - enable/disable systemd units on the installed target.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)


class ServicesJob(BaseJob):
    """
    Enables or disables a config-driven list of systemd units on the target.

    Configuration (from xen.yaml):
        units: list[dict] — each: {name: str, action: "enable"|"disable",
                                    mandatory: bool = False}
    """

    name = "services"
    description = "Configure systemd services"

    def _run_systemctl(self, target_root: str, action: str, unit: str) -> JobResult:
        cmd = ["arch-chroot", target_root, "systemctl", action, unit]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
            logger.debug(f"systemctl {action} {unit}: {result.stdout}")
            return JobResult.ok()
        except subprocess.CalledProcessError as e:
            return JobResult.fail(f"systemctl {action} {unit} failed: {e.stderr}", error_code=60)
        except subprocess.TimeoutExpired:
            return JobResult.fail(f"systemctl {action} {unit} timed out", error_code=61)
        except FileNotFoundError:
            return JobResult.fail(
                "arch-chroot not found. Cannot access target system.", error_code=62
            )

    def run(self, context: JobContext) -> JobResult:
        if context.selections.get("dry_run"):
            context.report_progress(100, "Skipped (dry run)")
            return JobResult.ok("Skipped: service configuration (dry run)")

        units: list[dict[str, Any]] = self._config.get("units", [])
        if not units:
            return JobResult.ok("No services configured")

        target_root = context.target_root
        total = len(units)
        failed: list[str] = []

        for index, unit_config in enumerate(units):
            name = str(unit_config.get("name", ""))
            action = str(unit_config.get("action", "enable"))
            mandatory = bool(unit_config.get("mandatory", False))
            if not name:
                continue

            context.report_progress(int((index / total) * 100), f"{action.title()} {name}...")
            result = self._run_systemctl(target_root, action, name)
            if not result.success:
                if mandatory:
                    return JobResult.fail(
                        f"Failed to {action} required service {name}: {result.message}",
                        error_code=result.error_code,
                    )
                logger.warning(f"Failed to {action} optional service {name}: {result.message}")
                failed.append(name)

        context.report_progress(100, "Services configured")
        return JobResult.ok(
            f"Configured {total} services ({len(failed)} non-mandatory failures)",
            data={"failed": failed},
        )

    def estimate_duration(self) -> int:
        return max(10, len(self._config.get("units", [])) * 2)

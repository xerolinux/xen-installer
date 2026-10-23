"""
RemoveUser Job - delete the live ISO's default user account from the target.

The live squashfs image bakes in a default "liveuser" account - if left in
place, it keeps whatever UID useradd would otherwise assign the new user
(typically 1000), pushing the real user to a different UID and breaking
anything that assumes "the primary user is UID 1000". Must run before the
users job creates the real account.
"""

from __future__ import annotations

import logging
import subprocess

from xen.i18n.messages import msg
from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)

# userdel's exit code when the named account doesn't exist - not an error
# for us, just means there was nothing to remove (e.g. a non-archiso source).
_USERDEL_NO_SUCH_USER = 6


class RemoveUserJob(BaseJob):
    """
    Deletes a config-driven live-ISO user account (and its home directory)
    from the target via arch-chroot.

    Configuration (from xen.yaml):
        username: str = "liveuser"
    """

    name = "removeuser"
    description = "Remove the live ISO's default user account"

    def run(self, context: JobContext) -> JobResult:
        if context.selections.get("dry_run"):
            context.report_progress(100, msg("Skipped (dry run)"))
            return JobResult.ok(msg("Skipped: remove live user (dry run)"))

        username = self._config.get("username", "liveuser")
        target_root = context.target_root

        context.report_progress(
            10, msg("Removing live user '{username}'...", username=username)
        )

        try:
            result = subprocess.run(
                ["arch-chroot", target_root, "userdel", "-r", username],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return JobResult.fail(
                msg("arch-chroot not found. Cannot access target system."), error_code=110
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail(
                msg("userdel timed out for '{username}'", username=username), error_code=111
            )

        if result.returncode not in (0, _USERDEL_NO_SUCH_USER):
            logger.warning(f"userdel {username} exited {result.returncode}: {result.stderr}")
            return JobResult.fail(
                msg(
                    "Failed to remove live user '{username}': {error}",
                    username=username,
                    error=result.stderr,
                ),
                error_code=112,
            )

        if result.returncode == _USERDEL_NO_SUCH_USER:
            logger.info(f"No live user '{username}' present, nothing to remove")
        else:
            logger.info(f"Removed live user '{username}'")

        context.report_progress(100, msg("Live user removed"))
        return JobResult.ok(msg("Live user '{username}' removed", username=username))

    def estimate_duration(self) -> int:
        return 5

"""
LUKS Boot Keyfile Job.

Adds a random keyfile as an extra LUKS keyslot for the encrypted root and
records its path in context.selections["luks_keyfile_path"], so
systemconfig.py embeds it into the initramfs (FILES=) and bootloader.py
adds the matching cryptkey= kernel parameter. Without this, GRUB unlocks
the volume once to read the kernel/initramfs, then mkinitcpio's "encrypt"
hook prompts again to mount root - one volume, two prompts.

Must run before systemconfig's mkinitcpio rebuild.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)


class LuksBootKeyfileJob(BaseJob):
    """Adds a random keyfile as an extra LUKS keyslot for the encrypted root."""

    name = "luksbootkeyfile"
    description = "Add LUKS keyfile for single-prompt boot"

    KEYFILE_PATH = "/etc/cryptsetup-keys.d/root.key"
    KEYFILE_SIZE = 4096  # bytes of random key material

    def run(self, context: JobContext) -> JobResult:
        if context.selections.get("dry_run"):
            context.report_progress(100, "Skipped (dry run)")
            return JobResult.ok("Skipped: LUKS keyfile (dry run)")

        if not context.selections.get("encryption"):
            context.report_progress(100, "Encryption not enabled")
            return JobResult.ok("Encryption not enabled, skipping LUKS keyfile")

        target_root = context.target_root
        raw_partition = context.selections.get("root_partition")
        if not raw_partition:
            return JobResult.fail(
                "encryption selected but no root_partition recorded", error_code=110
            )

        # SECURITY: read the passphrase locally so we can wipe it in finally.
        passphrase = str(context.selections.get("encryption_passphrase", ""))
        if not passphrase:
            return JobResult.fail("LUKS passphrase missing", error_code=111)

        try:
            context.report_progress(20, "Generating LUKS keyfile...")
            write_result = self._write_keyfile(target_root)
            if not write_result.success:
                return write_result

            context.report_progress(60, "Adding keyfile to LUKS keyslots...")
            add_result = self._add_keyfile_to_luks(target_root, raw_partition, passphrase)
            if not add_result.success:
                return add_result

            context.selections["luks_keyfile_path"] = self.KEYFILE_PATH
            context.report_progress(100, "LUKS keyfile configured")
            return JobResult.ok("LUKS keyfile added", data={"keyfile": self.KEYFILE_PATH})
        finally:
            # SECURITY: clear the passphrase from memory in all cases.
            passphrase = ""  # noqa: F841

    def _write_keyfile(self, target_root: str) -> JobResult:
        keyfile_path = Path(target_root) / self.KEYFILE_PATH.lstrip("/")
        try:
            keyfile_path.parent.mkdir(parents=True, exist_ok=True)
            keyfile_path.write_bytes(os.urandom(self.KEYFILE_SIZE))
            os.chmod(keyfile_path, 0o000)
        except OSError as e:
            return JobResult.fail(f"Failed to write LUKS keyfile: {e}", error_code=112)
        return JobResult.ok("Keyfile written")

    def _add_keyfile_to_luks(
        self, target_root: str, raw_partition: str, passphrase: str
    ) -> JobResult:
        # SECURITY: passphrase via stdin (--key-file=-), never argv or logged.
        keyfile_path = Path(target_root) / self.KEYFILE_PATH.lstrip("/")
        try:
            result = subprocess.run(
                [
                    "cryptsetup",
                    "luksAddKey",
                    raw_partition,
                    str(keyfile_path),
                    "--key-file=-",
                ],
                input=passphrase,
                text=True,
                capture_output=True,
                # Verifying the existing passphrase's slot plus deriving
                # the new one both run LUKS2's Argon2id KDF - on
                # RAM/CPU-constrained hardware this can genuinely take
                # well past 30s (observed timing out at exactly 30s on a
                # VM already flagged for low CPU/RAM/storage).
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail("cryptsetup luksAddKey timed out", error_code=113)
        except FileNotFoundError:
            return JobResult.fail("cryptsetup not found", error_code=114)

        if result.returncode != 0:
            logger.error(f"cryptsetup luksAddKey failed (exit code: {result.returncode})")
            return JobResult.fail("Failed to add LUKS keyfile keyslot", error_code=115)

        logger.info("Added LUKS keyfile keyslot")
        return JobResult.ok("Keyfile added to LUKS keyslots")

    def estimate_duration(self) -> int:
        return 10

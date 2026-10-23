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

from xen.i18n.messages import msg
from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.jobs.partition import manual_has_separate_boot, resolve_root_encryption

logger = logging.getLogger(__name__)


class LuksBootKeyfileJob(BaseJob):
    """Adds a random keyfile as an extra LUKS keyslot for the encrypted root."""

    name = "luksbootkeyfile"
    description = "Add LUKS keyfile for single-prompt boot"

    KEYFILE_PATH = "/etc/cryptsetup-keys.d/root.key"
    KEYFILE_SIZE = 4096  # bytes of random key material

    def run(self, context: JobContext) -> JobResult:
        if context.selections.get("dry_run"):
            context.report_progress(100, msg("Skipped (dry run)"))
            return JobResult.ok(msg("Skipped: LUKS keyfile (dry run)"))

        selections = context.selections
        is_encrypted, raw_partition = resolve_root_encryption(selections)
        # Only GRUB decrypting root directly prompts twice. Anywhere else
        # (systemd-boot, or GRUB with a plain /boot) the initramfs sits
        # unencrypted, so embedding root's key there would expose it.
        grub_decrypts_root = selections.get("bootloader") == "grub" and not (
            manual_has_separate_boot(selections.get("partition_assignments", []))
        )
        if not is_encrypted or not grub_decrypts_root:
            context.report_progress(100, msg("No LUKS boot keyfile needed"))
            return JobResult.ok(msg("No LUKS boot keyfile needed"))
        if not raw_partition:
            return JobResult.fail(
                msg("encrypted root but no raw root partition recorded"), error_code=110
            )
        root_name = raw_partition.rsplit("/", 1)[-1]
        passphrase = str(selections.get("luks_passphrases", {}).get(root_name, ""))

        target_root = context.target_root
        if not passphrase:
            return JobResult.fail(msg("LUKS passphrase missing"), error_code=111)

        try:
            context.report_progress(20, msg("Generating LUKS keyfile..."))
            write_result = self._write_keyfile(target_root)
            if not write_result.success:
                return write_result

            context.report_progress(60, msg("Adding keyfile to LUKS keyslots..."))
            add_result = self._add_keyfile_to_luks(target_root, raw_partition, passphrase)
            if not add_result.success:
                return add_result

            context.selections["luks_keyfile_path"] = self.KEYFILE_PATH
            context.report_progress(100, msg("LUKS keyfile configured"))
            return JobResult.ok(
                msg("LUKS keyfile added"), data={"keyfile": self.KEYFILE_PATH}
            )
        finally:
            # SECURITY: clear the passphrase from memory in all cases.
            passphrase = ""  # noqa: F841

    def _write_keyfile(self, target_root: str) -> JobResult:
        keyfile_path = Path(target_root) / self.KEYFILE_PATH.lstrip("/")
        try:
            keyfile_path.parent.mkdir(parents=True, exist_ok=True)
            # Create with mode 000 from the very first write (no separate
            # chmod) - a write-then-chmod sequence leaves the file briefly
            # world-readable at the default umask in between.
            fd = os.open(str(keyfile_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o000)
            try:
                os.write(fd, os.urandom(self.KEYFILE_SIZE))
            finally:
                os.close(fd)
        except OSError as e:
            return JobResult.fail(
                msg("Failed to write LUKS keyfile: {error}", error=e), error_code=112
            )
        return JobResult.ok(msg("Keyfile written"))

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
            return JobResult.fail(msg("cryptsetup luksAddKey timed out"), error_code=113)
        except FileNotFoundError:
            return JobResult.fail(msg("cryptsetup not found"), error_code=114)

        if result.returncode != 0:
            logger.error(f"cryptsetup luksAddKey failed (exit code: {result.returncode})")
            return JobResult.fail(msg("Failed to add LUKS keyfile keyslot"), error_code=115)

        logger.info("Added LUKS keyfile keyslot")
        return JobResult.ok(msg("Keyfile added to LUKS keyslots"))

    def estimate_duration(self) -> int:
        return 10

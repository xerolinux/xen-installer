"""
SystemConfig Job - target-specific system identity and boot readiness.

install.py copies the *live* system's root filesystem verbatim. Several
files under /etc identify the running live instance rather than describe
the target disk, and install.py excludes them from that copy so they can be
regenerated here instead: /etc/fstab (mount table), /etc/machine-id (unique
instance id), and mkinitcpio's HOOKS (encrypt/resume, based on the user's
partitioning choices) before the initramfs is rebuilt for the target.

Without this job, the installed system boots with the live ISO's fstab
(wrong devices), a machine-id shared with every other machine booted from
the same media, and - critically for an encrypted install - an initramfs
with no "encrypt" hook, which cannot prompt for the LUKS passphrase at all.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.jobs.partition import is_swap_entry, resolve_root_encryption

logger = logging.getLogger(__name__)


class SystemConfigJob(BaseJob):
    """
    Regenerates target-specific system files that install.py's rsync copy
    excludes, then rebuilds the initramfs so it matches the target's actual
    disk layout and encryption/hibernate choices.
    """

    name = "systemconfig"
    description = "Generate target fstab, machine-id, and initramfs"

    _HOOKS_LINE = re.compile(r"^HOOKS=\((?P<hooks>.*)\)\s*$", re.MULTILINE)
    _FILES_LINE = re.compile(r"^FILES=\((?P<files>.*)\)\s*$", re.MULTILINE)
    _MODULES_LINE = re.compile(r"^MODULES=\((?P<modules>.*)\)\s*$", re.MULTILINE)

    # install.py's rsync copy carries over the *live ISO's own*
    # /etc/mkinitcpio.conf verbatim - these hooks (from mkinitcpio-archiso)
    # build the initramfs that boots the live squashfs/PXE session. They are
    # meaningless on a real installed disk and can break mkinitcpio -P on
    # the target (its presets/scripts assume the live environment's own
    # layout, not a normal installed root).
    _LIVE_ONLY_HOOKS = frozenset(
        {
            "archiso",
            "archiso_loop_mnt",
            "archiso_pxe_common",
            "archiso_pxe_nbd",
            "archiso_pxe_http",
            "archiso_pxe_nfs",
            "memdisk",
        }
    )

    def run(self, context: JobContext) -> JobResult:
        if context.selections.get("dry_run"):
            context.report_progress(100, "Skipped (dry run)")
            return JobResult.ok("Skipped: system configuration (dry run)")

        target_root = context.target_root

        context.report_progress(10, "Generating /etc/fstab...")
        result = self._generate_fstab(target_root)
        if not result.success:
            return result

        if context.selections.get("encryption") or context.selections.get(
            "partition_assignments"
        ):
            context.report_progress(15, "Generating /etc/crypttab...")
            result = self._generate_crypttab(target_root, context.selections)
            if not result.success:
                return result

        context.report_progress(35, "Generating machine-id...")
        result = self._generate_machine_id(target_root)
        if not result.success:
            return result

        context.report_progress(55, "Configuring mkinitcpio hooks...")
        result = self._configure_mkinitcpio_hooks(target_root, context.selections)
        if not result.success:
            return result

        context.report_progress(65, "Removing live-ISO-only mkinitcpio presets...")
        result = self._remove_live_only_presets(target_root)
        if not result.success:
            return result

        context.report_progress(70, "Rebuilding initramfs...")
        result = self._rebuild_initramfs(target_root)
        if not result.success:
            return result

        context.report_progress(100, "System configuration complete")
        return JobResult.ok("Target fstab, machine-id, and initramfs regenerated")

    def _generate_fstab(self, target_root: str) -> JobResult:
        try:
            result = subprocess.run(
                ["genfstab", "-U", target_root],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            return JobResult.fail(f"genfstab failed: {e.stderr}", error_code=90)
        except FileNotFoundError:
            return JobResult.fail(
                "genfstab not found. Install arch-install-scripts.", error_code=91
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail("genfstab timed out", error_code=92)

        fstab_path = Path(target_root) / "etc" / "fstab"
        try:
            fstab_path.write_text(result.stdout, encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {fstab_path}: {e}", error_code=93)

        logger.info(f"Wrote {fstab_path}")
        return JobResult.ok("fstab generated")

    def _blkid_uuid(self, device: str) -> str | None:
        """Get the filesystem/LUKS UUID of a raw device, or None if unavailable."""
        try:
            result = subprocess.run(
                ["blkid", "-s", "UUID", "-o", "value", device],
                check=True,
                capture_output=True,
                text=True,
            )
            uuid = result.stdout.strip()
            return uuid or None
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"blkid failed for {device}: {e}")
            return None

    def _blkid_partuuid(self, device: str) -> str | None:
        """Get the GPT partition's own PARTUUID, or None if unavailable.

        Unlike a filesystem/LUKS UUID, PARTUUID is stable across reboots
        for a device that gets reformatted every boot - exactly the case
        for encrypted swap, which is fresh-mkswap'd with a random key on
        every boot (ArchWiki: dm-crypt/Swap encryption)."""
        try:
            result = subprocess.run(
                ["blkid", "-s", "PARTUUID", "-o", "value", device],
                check=True,
                capture_output=True,
                text=True,
            )
            partuuid = result.stdout.strip()
            return partuuid or None
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"blkid failed for {device}: {e}")
            return None

    @staticmethod
    def _crypttab_mapper_names(content: str) -> set[str]:
        """Parse each non-comment line's first (mapper name) field, so an
        existing-entry check can't false-positive on a substring match
        (e.g. "cryptdata" matching inside an existing "cryptdata-srv" line)."""
        names: set[str] = set()
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if parts:
                names.add(parts[0])
        return names

    def _ensure_swap_fstab_entry(self, target_root: str, mapper_name: str) -> JobResult:
        """Add the /etc/fstab line that actually activates an encrypted
        swap mapper at boot. genfstab (run before this job, against the
        live Apply's filesystems) has no idea about a mapper that doesn't
        exist until crypttab's "swap" option mkswaps it fresh at boot -
        crypttab alone only creates the mapper, something else (fstab)
        has to swapon it."""
        fstab_path = Path(target_root) / "etc" / "fstab"
        try:
            existing = fstab_path.read_text(encoding="utf-8") if fstab_path.exists() else ""
        except OSError as e:
            return JobResult.fail(f"Failed to read {fstab_path}: {e}", error_code=118)

        mapper_device = f"/dev/mapper/{mapper_name}"
        for line in existing.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.split()[0] == mapper_device:
                return JobResult.ok("fstab already has swap mapper entry")

        line = f"{mapper_device} none swap defaults 0 0\n"
        try:
            fstab_path.parent.mkdir(parents=True, exist_ok=True)
            fstab_path.write_text(existing + line, encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {fstab_path}: {e}", error_code=118)

        logger.info(f"Added swap fstab entry for {mapper_device}")
        return JobResult.ok("fstab swap entry added")

    def _generate_crypttab(self, target_root: str, selections: dict[str, Any]) -> JobResult:
        """Write /etc/crypttab: the encrypted root (unlocked at boot via
        GRUB/mkinitcpio - bootloader.py's cryptdevice= kernel param), any
        other manually-encrypted partition (unlocked automatically via a
        per-partition keyfile added as a second LUKS keyslot, written
        here), and any encrypted swap (plain dm-crypt, a fresh random key
        every boot - never LUKS, never a remembered secret).

        Root encryption is resolved via partition.resolve_root_encryption
        (AUTO mode's selections["encryption"]/["root_partition"], or manual
        mode's partition_assignments) rather than trusting
        selections["root_partition"] directly: in manual mode that key is
        always set to whatever the real root partition is, encrypted or
        not, and - when it IS encrypted - set to the mapper path, not the
        raw LUKS partition needed here."""
        is_root_encrypted, raw_partition = resolve_root_encryption(selections)
        if is_root_encrypted and not raw_partition:
            return JobResult.fail(
                "encryption selected but no root_partition recorded", error_code=107
            )

        partition_assignments = selections.get("partition_assignments", [])

        crypttab_path = Path(target_root) / "etc" / "crypttab"
        try:
            existing = crypttab_path.read_text(encoding="utf-8") if crypttab_path.exists() else ""
        except OSError as e:
            return JobResult.fail(f"Failed to read {crypttab_path}: {e}", error_code=109)

        existing_mappers = self._crypttab_mapper_names(existing)
        lines: list[str] = []
        pending_swap_fstab_mappers: list[str] = []

        if is_root_encrypted and "cryptroot" not in existing_mappers:
            luks_uuid = self._blkid_uuid(raw_partition)
            if not luks_uuid:
                return JobResult.fail(
                    f"Could not determine LUKS UUID for {raw_partition}", error_code=108
                )
            lines.append(f"cryptroot UUID={luks_uuid} none luks\n")

        passphrases = selections.get("manual_luks_passphrases", {})
        for assignment in partition_assignments:
            path = str(assignment.get("path", ""))
            if not path.startswith("/dev/mapper/"):
                continue
            mapper_name = path.rsplit("/", 1)[-1]
            if mapper_name == "cryptroot":
                continue
            already_in_crypttab = mapper_name in existing_mappers

            if is_swap_entry(assignment):
                if not already_in_crypttab:
                    raw_swap_path = str(assignment.get("raw_path", ""))
                    partuuid = self._blkid_partuuid(raw_swap_path) if raw_swap_path else None
                    if not partuuid:
                        return JobResult.fail(
                            f"Could not determine PARTUUID for swap partition {raw_swap_path}",
                            error_code=117,
                        )
                    lines.append(
                        f"{mapper_name} PARTUUID={partuuid} /dev/urandom "
                        "swap,cipher=aes-xts-plain64,size=256\n"
                    )
                # Queued regardless of already_in_crypttab - see the loop
                # below for why.
                pending_swap_fstab_mappers.append(mapper_name)
                continue

            if already_in_crypttab:
                continue

            raw_path = str(assignment.get("raw_path", ""))
            partition_name = raw_path.rsplit("/", 1)[-1] if raw_path else ""
            passphrase = passphrases.get(partition_name, "")
            keyfile_path = f"/etc/cryptsetup-keys.d/{mapper_name}.key"
            keyfile_result = self._write_manual_keyfile(
                target_root, keyfile_path, raw_path, passphrase
            )
            if not keyfile_result.success:
                return keyfile_result
            luks_uuid = self._blkid_uuid(raw_path)
            if not luks_uuid:
                return JobResult.fail(
                    f"Could not determine LUKS UUID for {raw_path}", error_code=113
                )
            lines.append(f"{mapper_name} UUID={luks_uuid} {keyfile_path} luks\n")

        if lines:
            try:
                crypttab_path.parent.mkdir(parents=True, exist_ok=True)
                crypttab_path.write_text(existing + "".join(lines), encoding="utf-8")
            except OSError as e:
                return JobResult.fail(f"Failed to write {crypttab_path}: {e}", error_code=109)

        # Runs even when crypttab needed no new lines - a retry that wrote
        # crypttab but not fstab last time still needs the fstab line, and
        # this only runs after crypttab's own write above succeeds, so a
        # later partition's failure can't leave a dangling fstab entry for
        # a mapper crypttab never ends up creating.
        for mapper_name in pending_swap_fstab_mappers:
            fstab_result = self._ensure_swap_fstab_entry(target_root, mapper_name)
            if not fstab_result.success:
                return fstab_result

        if not lines:
            return JobResult.ok("No crypttab entries needed")

        logger.info(f"Wrote {crypttab_path}")
        return JobResult.ok("crypttab generated")

    def _write_manual_keyfile(
        self, target_root: str, keyfile_path: str, raw_path: str, passphrase: str
    ) -> JobResult:
        """Generate a random keyfile, write it (mode 000) under
        target_root, and add it as a second LUKS keyslot on raw_path -
        authenticated with the passphrase generated during live-Apply."""
        if not passphrase:
            return JobResult.fail(
                f"No carried-over passphrase for {raw_path} - cannot add keyfile",
                error_code=114,
            )
        full_path = Path(target_root) / keyfile_path.lstrip("/")
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            # Create with mode 000 from the very first write (no separate
            # chmod) - a write-then-chmod sequence leaves the file briefly
            # world-readable at the default umask in between.
            fd = os.open(str(full_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o000)
            try:
                os.write(fd, os.urandom(4096))
            finally:
                os.close(fd)
        except OSError as e:
            return JobResult.fail(f"Failed to write keyfile {keyfile_path}: {e}", error_code=115)

        try:
            subprocess.run(
                ["cryptsetup", "luksAddKey", raw_path, str(full_path), "--key-file=-"],
                input=passphrase,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,  # Argon2id on weak hardware can genuinely take this long.
            )
        except subprocess.CalledProcessError as e:
            return JobResult.fail(f"luksAddKey failed for {raw_path}: {e.stderr}", error_code=116)
        except subprocess.TimeoutExpired:
            return JobResult.fail(f"luksAddKey timed out for {raw_path}", error_code=119)
        finally:
            passphrase = ""  # noqa: F841 - SECURITY: clear from this frame

        return JobResult.ok(f"Keyfile added for {raw_path}")

    def _generate_machine_id(self, target_root: str) -> JobResult:
        machine_id_path = Path(target_root) / "etc" / "machine-id"
        try:
            machine_id_path.unlink(missing_ok=True)
        except OSError as e:
            return JobResult.fail(f"Failed to remove stale machine-id: {e}", error_code=94)

        try:
            subprocess.run(
                ["arch-chroot", target_root, "systemd-machine-id-setup"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.CalledProcessError as e:
            return JobResult.fail(f"systemd-machine-id-setup failed: {e.stderr}", error_code=95)
        except FileNotFoundError:
            return JobResult.fail("arch-chroot not found", error_code=96)
        except subprocess.TimeoutExpired:
            return JobResult.fail("systemd-machine-id-setup timed out", error_code=97)

        logger.info("Generated machine-id")

        # Symlink, not a copy, so it can't drift after a later regeneration.
        dbus_machine_id = Path(target_root) / "var" / "lib" / "dbus" / "machine-id"
        try:
            dbus_machine_id.parent.mkdir(parents=True, exist_ok=True)
            if dbus_machine_id.is_symlink() or dbus_machine_id.exists():
                dbus_machine_id.unlink()
            dbus_machine_id.symlink_to("/etc/machine-id")
        except OSError as e:
            logger.warning(f"Failed to symlink {dbus_machine_id} to /etc/machine-id: {e}")

        return JobResult.ok("machine-id generated")

    def _configure_mkinitcpio_hooks(
        self, target_root: str, selections: dict[str, Any]
    ) -> JobResult:
        conf_path = Path(target_root) / "etc" / "mkinitcpio.conf"
        try:
            content = conf_path.read_text(encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to read {conf_path}: {e}", error_code=98)

        match = self._HOOKS_LINE.search(content)
        if not match:
            return JobResult.fail(f"No HOOKS=(...) line found in {conf_path}", error_code=99)

        hooks = match.group("hooks").split()
        encryption, _ = resolve_root_encryption(selections)
        hibernate = selections.get("swap_strategy") == "hibernate"
        changed = False

        live_only = [h for h in hooks if h in self._LIVE_ONLY_HOOKS]
        if live_only:
            hooks = [h for h in hooks if h not in self._LIVE_ONLY_HOOKS]
            changed = True
            logger.info(f"Stripped live-ISO-only mkinitcpio hooks: {' '.join(live_only)}")

        if encryption and "encrypt" not in hooks:
            insert_at = hooks.index("filesystems") if "filesystems" in hooks else len(hooks)
            hooks.insert(insert_at, "encrypt")
            changed = True

        if hibernate and "resume" not in hooks:
            insert_at = hooks.index("filesystems") + 1 if "filesystems" in hooks else len(hooks)
            hooks.insert(insert_at, "resume")
            changed = True

        if changed:
            new_line = f"HOOKS=({' '.join(hooks)})"
            content = content[: match.start()] + new_line + content[match.end() :]

        files_changed, content = self._add_luks_keyfile_to_files(content, selections)
        changed = changed or files_changed

        modules_changed, content = self._add_btrfs_module(content, selections)
        changed = changed or modules_changed

        if not changed:
            return JobResult.ok("mkinitcpio HOOKS already correct")

        try:
            conf_path.write_text(content, encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {conf_path}: {e}", error_code=100)

        logger.info(f"Updated mkinitcpio HOOKS: {' '.join(hooks)}")
        return JobResult.ok("mkinitcpio HOOKS updated")

    def _add_luks_keyfile_to_files(
        self, content: str, selections: dict[str, Any]
    ) -> tuple[bool, str]:
        """Embed the LUKS boot keyfile into the initramfs via FILES=, so
        the "encrypt" hook can auto-unlock with it instead of prompting
        for the passphrase again."""
        keyfile_path = selections.get("luks_keyfile_path")
        if not keyfile_path:
            return False, content

        match = self._FILES_LINE.search(content)
        if not match:
            logger.warning("No FILES=(...) line found in mkinitcpio.conf; skipping keyfile embed")
            return False, content

        files = match.group("files").split()
        if keyfile_path in files:
            return False, content

        files.append(keyfile_path)
        new_line = f"FILES=({' '.join(files)})"
        new_content = content[: match.start()] + new_line + content[match.end() :]
        logger.info(f"Added LUKS keyfile to mkinitcpio FILES: {keyfile_path}")
        return True, new_content

    def _add_btrfs_module(self, content: str, selections: dict[str, Any]) -> tuple[bool, str]:
        """Btrfs is a kernel module, not built-in - add it to MODULES= so
        the initramfs can mount a Btrfs root. Only for a single-device
        Btrfs root (Xen's only case); multi-device pools would also need
        the "btrfs" HOOKS entry, which doesn't apply here."""
        if selections.get("filesystem") != "btrfs":
            return False, content

        match = self._MODULES_LINE.search(content)
        if not match:
            logger.warning("No MODULES=(...) line found in mkinitcpio.conf; skipping btrfs module")
            return False, content

        modules = match.group("modules").split()
        if "btrfs" in modules:
            return False, content

        modules.append("btrfs")
        new_line = f"MODULES=({' '.join(modules)})"
        new_content = content[: match.start()] + new_line + content[match.end() :]
        logger.info("Added btrfs to mkinitcpio MODULES")
        return True, new_content

    def _remove_live_only_presets(self, target_root: str) -> JobResult:
        """
        Delete mkinitcpio presets that only make sense on the live ISO
        (PRESETS=('archiso'), e.g. archiso's own linux.preset).

        install.py's rsync copy carries over every file under
        /etc/mkinitcpio.d/ verbatim, including the live ISO's own kernel
        preset built for mkarchiso's initramfs (referencing
        archiso_config/archiso_image). `mkinitcpio -P` processes every
        preset file in that directory, so this live-only preset gets
        rebuilt alongside the target's real one (e.g. XeroLinux's own
        xero.preset) for no reason - and since it targets the exact same
        kernel image, it's pure duplicate work at best.
        """
        preset_dir = Path(target_root) / "etc" / "mkinitcpio.d"
        if not preset_dir.is_dir():
            return JobResult.ok("No mkinitcpio.d directory to clean up")

        removed = []
        try:
            for preset_file in sorted(preset_dir.glob("*.preset")):
                content = preset_file.read_text(encoding="utf-8")
                if "archiso" in content:
                    preset_file.unlink()
                    removed.append(preset_file.name)
        except OSError as e:
            return JobResult.fail(f"Failed to clean up live-only presets: {e}", error_code=104)

        if removed:
            logger.info(f"Removed live-ISO-only mkinitcpio presets: {', '.join(removed)}")
        return JobResult.ok("Live-ISO-only mkinitcpio presets removed")

    def _check_kernel_readable(self, target_root: str) -> JobResult:
        """
        Fail fast, with an actionable message, if no readable kernel image
        exists in the target's /boot - after trying to recover one from
        the live ISO's own boot media first.

        archiso's airootfs.sfs (extracted by install.py) doesn't carry a
        copy of vmlinuz-linux in /boot: the live kernel that's actually
        used to boot lives only on the ISO's own boot medium, referenced
        directly by the bootloader, kept out of the squashfs to avoid
        shipping it twice. _find_live_kernel_image() recovers it from
        there.

        mkinitcpio's own error for a missing kernel ("Invalid option -k --
        '<path>' must be readable") gives no indication of what's actually
        in /boot, making it look like a preset/config bug rather than a
        missing kernel file - this surfaces the real state of /boot
        directly if the recovery copy also comes up empty.
        """
        boot_dir = Path(target_root) / "boot"
        if not boot_dir.is_dir():
            return JobResult.fail(f"{boot_dir} does not exist", error_code=105)

        def _has_kernel() -> bool:
            return any(
                p.is_file() and os.access(p, os.R_OK) for p in boot_dir.glob("vmlinuz-*")
            )

        if not _has_kernel():
            live_kernel = self._find_live_kernel_image()
            if live_kernel:
                try:
                    shutil.copy2(live_kernel, boot_dir / live_kernel.name)
                    logger.info(f"Recovered kernel image from live boot media: {live_kernel}")
                except OSError as e:
                    logger.warning(f"Failed to copy live kernel image {live_kernel}: {e}")

        if _has_kernel():
            return JobResult.ok("Kernel image found")

        try:
            contents = ", ".join(sorted(p.name for p in boot_dir.iterdir())) or "(empty)"
        except OSError as e:
            contents = f"(could not list: {e})"
        return JobResult.fail(
            f"No readable vmlinuz-* kernel image in {boot_dir}, and none could "
            f"be recovered from the live boot media. Contents of {boot_dir}: {contents}.",
            error_code=106,
        )

    # Standard archiso ISO layout: the boot medium itself (distinct from
    # the squashfs) always keeps the live kernel at
    # <bootmnt>/<install_dir>/boot/<arch>/vmlinuz-*, since the bootloader
    # needs it there to boot the live session in the first place.
    _LIVE_KERNEL_GLOB_PATTERNS = (
        "/run/archiso/bootmnt/*/boot/x86_64/vmlinuz-*",
        "/run/archiso/bootmnt/*/*/boot/x86_64/vmlinuz-*",
    )

    @classmethod
    def _find_live_kernel_image(cls) -> Path | None:
        """Locate the live ISO's own kernel image on its boot medium."""
        for pattern in cls._LIVE_KERNEL_GLOB_PATTERNS:
            matches = sorted(Path("/").glob(pattern.lstrip("/")))
            if matches:
                return matches[0]
        return None

    def _rebuild_initramfs(self, target_root: str) -> JobResult:
        kernel_check = self._check_kernel_readable(target_root)
        if not kernel_check.success:
            return kernel_check

        try:
            result = subprocess.run(
                ["arch-chroot", target_root, "mkinitcpio", "-P"],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.CalledProcessError as e:
            return JobResult.fail(f"mkinitcpio -P failed: {e.stderr}", error_code=101)
        except FileNotFoundError:
            return JobResult.fail("arch-chroot not found", error_code=102)
        except subprocess.TimeoutExpired:
            return JobResult.fail("mkinitcpio -P timed out", error_code=103)

        logger.info(f"mkinitcpio -P: {result.stdout}")
        return JobResult.ok("initramfs rebuilt")

    def estimate_duration(self) -> int:
        return 45

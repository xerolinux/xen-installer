"""
Bootloader Job - System bootloader installation and configuration.

Supports systemd-boot (default for UEFI) and GRUB (fallback/legacy).
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.jobs.partition import resolve_root_encryption

logger = logging.getLogger(__name__)


class BootloaderJob(BaseJob):
    """
    System bootloader installation job.

    Responsibilities:
    - Detect and validate EFI system partition
    - Install systemd-boot (default) or GRUB bootloader
    - Generate boot loader configuration files
    - Create boot entries with kernel parameters
    - Handle kernel and initramfs detection
    """

    name = "bootloader"
    description = "System bootloader installation"

    # Supported bootloaders
    SYSTEMD_BOOT = "systemd-boot"
    GRUB = "grub"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the bootloader job."""
        super().__init__(config)
        self._efi_mount: Path | None = None
        self._kernels: list[str] = []

    def _validate_efi_system(self, context: JobContext) -> JobResult:
        """
        Validate EFI system requirements.

        Args:
            context: Execution context

        Returns:
            JobResult indicating if EFI system is valid
        """
        target_root = Path(context.target_root)

        # Check for efivars (indicates UEFI mode)
        efivars_path = Path("/sys/firmware/efi/efivars")
        if not efivars_path.exists():
            logger.warning("EFI variables not available - system may not be booted in UEFI mode")
            return JobResult.fail(
                "System not booted in UEFI mode (efivars not available)",
                error_code=50,
            )

        # Find EFI partition mount point
        possible_efi_mounts = [
            target_root / "boot" / "efi",
            target_root / "efi",
            target_root / "boot",
        ]

        efi_partition = context.selections.get("efi_partition")
        for mount_path in possible_efi_mounts:
            if mount_path.exists():
                self._efi_mount = mount_path
                logger.info(f"Found EFI mount point: {mount_path}")
                break

        if not self._efi_mount:
            return JobResult.fail(
                "EFI partition not mounted. Expected at /boot/efi or /efi",
                error_code=51,
                data={"efi_partition": efi_partition},
            )

        # Validate EFI partition is writable
        if not self._efi_mount.exists():
            return JobResult.fail(
                f"EFI mount point does not exist: {self._efi_mount}",
                error_code=52,
            )

        return JobResult.ok(
            "EFI system validated",
            data={"efi_mount": str(self._efi_mount)},
        )

    def _detect_kernels(self, context: JobContext) -> JobResult:
        """
        Detect installed kernels in target system.

        Args:
            context: Execution context

        Returns:
            JobResult with detected kernel list
        """
        target_root = Path(context.target_root)
        boot_dir = target_root / "boot"

        if not boot_dir.exists():
            return JobResult.fail(
                f"Boot directory not found: {boot_dir}",
                error_code=53,
            )

        # Search for vmlinuz-* kernel files
        kernels = list(boot_dir.glob("vmlinuz-*"))
        if not kernels:
            return JobResult.fail(
                "No kernel images found in /boot",
                error_code=54,
                data={"boot_dir": str(boot_dir)},
            )

        self._kernels = [k.name for k in sorted(kernels)]
        logger.info(f"Detected kernels: {self._kernels}")

        # Verify initramfs exists for each kernel
        missing_initramfs = []
        for kernel_name in self._kernels:
            # Extract kernel version (e.g., vmlinuz-6.1.0 -> 6.1.0)
            kernel_version = kernel_name.replace("vmlinuz-", "")
            initramfs_name = f"initramfs-{kernel_version}.img"
            initramfs_path = boot_dir / initramfs_name

            if not initramfs_path.exists():
                # Try alternative naming (initrd-)
                initramfs_name = f"initrd.img-{kernel_version}"
                initramfs_path = boot_dir / initramfs_name

            if not initramfs_path.exists():
                missing_initramfs.append(kernel_version)

        if missing_initramfs:
            logger.warning(f"Missing initramfs for kernels: {missing_initramfs}")
            # This is a warning, not a failure - initramfs might be generated differently

        return JobResult.ok(
            f"Detected {len(self._kernels)} kernel(s)",
            data={"kernels": self._kernels},
        )

    def _install_systemd_boot(self, context: JobContext) -> JobResult:
        """
        Install systemd-boot bootloader.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        if not self._efi_mount:
            return JobResult.fail("EFI mount point not initialized", error_code=55)

        context.report_progress(30, "Installing systemd-boot...")

        # Run bootctl install via arch-chroot
        try:
            result = subprocess.run(
                [
                    "arch-chroot",
                    context.target_root,
                    "bootctl",
                    "install",
                    "--esp-path=/boot/efi",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("systemd-boot installed successfully")
            if result.stdout:
                logger.debug(f"bootctl output: {result.stdout}")

        except subprocess.CalledProcessError as e:
            logger.error(f"bootctl install failed: {e.stderr}")
            return JobResult.fail(
                f"Failed to install systemd-boot: {e.stderr}",
                error_code=56,
            )
        except FileNotFoundError:
            return JobResult.fail(
                "Required tool not found: arch-chroot or bootctl",
                error_code=57,
            )

        # Configure loader
        loader_conf_result = self._configure_systemd_boot_loader(context)
        if not loader_conf_result.success:
            return loader_conf_result

        # Create boot entries
        entries_result = self._create_systemd_boot_entries(context)
        if not entries_result.success:
            return entries_result

        return JobResult.ok("systemd-boot installed and configured")

    def _configure_systemd_boot_loader(self, _context: JobContext) -> JobResult:
        """
        Configure systemd-boot loader.conf.

        Args:
            _context: Execution context (unused)

        Returns:
            JobResult indicating success or failure
        """
        if not self._efi_mount:
            return JobResult.fail("EFI mount point not initialized", error_code=58)

        loader_dir = self._efi_mount / "loader"
        loader_conf = loader_dir / "loader.conf"

        try:
            loader_dir.mkdir(parents=True, exist_ok=True)

            # Get timeout from config (default 3 seconds)
            timeout = self._config.get("timeout", 3)

            loader_config = f"""default arch.conf
timeout {timeout}
console-mode max
editor no
"""

            loader_conf.write_text(loader_config, encoding="utf-8")
            logger.info(f"Created loader configuration: {loader_conf}")

            return JobResult.ok("Loader configuration created")

        except OSError as e:
            return JobResult.fail(
                f"Failed to create loader configuration: {e}",
                error_code=59,
            )

    def _create_systemd_boot_entries(self, context: JobContext) -> JobResult:
        """
        Create systemd-boot boot entries.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        if not self._efi_mount:
            return JobResult.fail("EFI mount point not initialized", error_code=60)

        entries_dir = self._efi_mount / "loader" / "entries"

        try:
            entries_dir.mkdir(parents=True, exist_ok=True)

            # Get kernel parameters from selections
            kernel_params = context.selections.get("kernel_params", "quiet splash")

            # Get root partition UUID
            root_partition = self._get_root_partition_uuid(context)
            if not root_partition:
                return JobResult.fail("Could not determine root partition UUID", error_code=61)

            # cryptdevice= tells mkinitcpio's "encrypt" hook which raw LUKS
            # partition to unlock before root=UUID=<mapper-fs-uuid> can mount.
            crypt_param = ""
            is_encrypted, raw_partition = resolve_root_encryption(context.selections)
            if is_encrypted:
                if raw_partition:
                    luks_uuid = self._blkid_uuid(raw_partition)
                    if luks_uuid:
                        crypt_param = f"cryptdevice=UUID={luks_uuid}:cryptroot "
                    else:
                        logger.warning(
                            f"Could not determine LUKS UUID for {raw_partition}; "
                            "boot entry will be missing cryptdevice="
                        )
                keyfile_path = context.selections.get("luks_keyfile_path")
                if keyfile_path:
                    crypt_param += f"cryptkey=rootfs:{keyfile_path} "

            # Create entry for each kernel
            for kernel_name in self._kernels:
                kernel_version = kernel_name.replace("vmlinuz-", "")
                entry_name = f"arch-{kernel_version}.conf"
                entry_path = entries_dir / entry_name

                # Determine initramfs filename
                initramfs_candidates = [
                    f"initramfs-{kernel_version}.img",
                    f"initrd.img-{kernel_version}",
                ]

                initramfs_name = None
                boot_dir = Path(context.target_root) / "boot"
                for candidate in initramfs_candidates:
                    if (boot_dir / candidate).exists():
                        initramfs_name = candidate
                        break

                if not initramfs_name:
                    logger.warning(f"No initramfs found for kernel {kernel_version}")
                    initramfs_name = f"initramfs-{kernel_version}.img"

                entry_config = f"""title   Arch Linux
linux   /{kernel_name}
initrd  /{initramfs_name}
options {crypt_param}root=UUID={root_partition} rw {kernel_params}
"""

                entry_path.write_text(entry_config, encoding="utf-8")
                logger.info(f"Created boot entry: {entry_path}")

            # Create default symlink
            default_entry = entries_dir / "arch.conf"
            if self._kernels:
                latest_kernel = self._kernels[-1]
                kernel_version = latest_kernel.replace("vmlinuz-", "")
                default_target = f"arch-{kernel_version}.conf"

                if default_entry.exists() or default_entry.is_symlink():
                    default_entry.unlink()

                default_entry.symlink_to(default_target)
                logger.info(f"Created default entry symlink: {default_entry}")

            return JobResult.ok(f"Created {len(self._kernels)} boot entry(ies)")

        except OSError as e:
            return JobResult.fail(
                f"Failed to create boot entries: {e}",
                error_code=62,
            )

    _GRUB_DEFAULTS: dict[str, str] = {
        "GRUB_TIMEOUT": "5",
        "GRUB_DEFAULT": "saved",
        "GRUB_DISABLE_SUBMENU": "false",
        "GRUB_DISABLE_RECOVERY": "true",
        "GRUB_EARLY_INITRD_LINUX_STOCK": "",
    }
    _GRUB_KERNEL_PARAMS = ("quiet", "nowatchdog", "nvme_load=YES", "zswap.enabled=0")

    @staticmethod
    def _set_grub_default_key(content: str, key: str, value: str) -> str:
        """Set (or replace, commented-or-not) a single KEY="value" line."""
        pattern = re.compile(rf'^#?\s*{re.escape(key)}=.*$', re.MULTILINE)
        line = f'{key}="{value}"'
        if pattern.search(content):
            return pattern.sub(line, content)
        separator = "\n" if content and not content.endswith("\n") else ""
        return f"{content}{separator}{line}\n"

    def _write_grub_defaults(self, target_root: str, context: JobContext) -> JobResult:
        """Write /etc/default/grub, run before grub-mkconfig so it picks
        these up. For an encrypted root, mkinitcpio's "encrypt" hook needs
        cryptdevice= to find and unlock the raw LUKS partition -
        GRUB_ENABLE_CRYPTODISK and /etc/crypttab alone aren't enough."""
        grub_default = Path(target_root) / "etc" / "default" / "grub"
        try:
            content = grub_default.read_text(encoding="utf-8") if grub_default.exists() else ""
        except OSError as e:
            return JobResult.fail(f"Failed to read {grub_default}: {e}", error_code=70)

        for key, value in self._GRUB_DEFAULTS.items():
            # GRUB_DEFAULT=saved needs to write grubenv, which GRUB cannot
            # do on Btrfs (no separate /boot partition here) - produces
            # "error: sparse file not allowed" at every boot, cosmetic but
            # alarming. Numeric default only for a Btrfs root.
            if key == "GRUB_DEFAULT" and context.selections.get("filesystem") == "btrfs":
                value = "0"
            content = self._set_grub_default_key(content, key, value)

        # The inherited live-ISO default has GRUB_SAVEDEFAULT="true", so
        # every boot entry writes to grubenv on every boot - same failure
        # mode as GRUB_DEFAULT=saved above. Matches Calamares's own
        # grubcfg module (forces false for btrfs/f2fs/zfs).
        if context.selections.get("filesystem") == "btrfs":
            content = self._set_grub_default_key(content, "GRUB_SAVEDEFAULT", "false")

        kernel_params = list(self._GRUB_KERNEL_PARAMS)
        is_encrypted, raw_partition = resolve_root_encryption(context.selections)
        if is_encrypted:
            if raw_partition:
                luks_uuid = self._blkid_uuid(raw_partition)
                if luks_uuid:
                    kernel_params.append(f"cryptdevice=UUID={luks_uuid}:cryptroot")
                else:
                    logger.warning(
                        f"Could not determine LUKS UUID for {raw_partition}; "
                        "kernel cmdline will be missing cryptdevice="
                    )
            else:
                logger.warning(
                    "encryption selected but no root_partition recorded; "
                    "kernel cmdline will be missing cryptdevice="
                )

            # cryptkey=: auto-unlock with the embedded keyfile instead of
            # prompting for the passphrase a second time.
            keyfile_path = context.selections.get("luks_keyfile_path")
            if keyfile_path:
                kernel_params.append(f"cryptkey=rootfs:{keyfile_path}")

        content = self._set_grub_default_key(
            content, "GRUB_CMDLINE_LINUX_DEFAULT", " ".join(kernel_params)
        )

        try:
            grub_default.parent.mkdir(parents=True, exist_ok=True)
            grub_default.write_text(content, encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {grub_default}: {e}", error_code=71)

        logger.info("Wrote /etc/default/grub (timeout, kernel params, recovery entries)")
        return JobResult.ok("GRUB defaults written")

    def _enable_grub_cryptodisk(self, target_root: str) -> JobResult:
        """
        Set GRUB_ENABLE_CRYPTODISK=y in /etc/default/grub.

        Required for an encrypted root: without it, grub-install omits the
        cryptodisk/luks2 modules from GRUB's core image, and grub-mkconfig
        won't detect the LUKS device to add the right kernel cmdline either.
        """
        grub_default = Path(target_root) / "etc" / "default" / "grub"
        try:
            content = grub_default.read_text(encoding="utf-8") if grub_default.exists() else ""
        except OSError as e:
            return JobResult.fail(f"Failed to read {grub_default}: {e}", error_code=70)

        new_content = self._set_grub_default_key(content, "GRUB_ENABLE_CRYPTODISK", "y")
        # GRUB_ENABLE_CRYPTODISK is conventionally unquoted (y, not "y") -
        # match grub-mkconfig's own expectations.
        new_content = new_content.replace('GRUB_ENABLE_CRYPTODISK="y"', "GRUB_ENABLE_CRYPTODISK=y")

        try:
            grub_default.parent.mkdir(parents=True, exist_ok=True)
            grub_default.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return JobResult.fail(f"Failed to write {grub_default}: {e}", error_code=71)

        logger.info("Set GRUB_ENABLE_CRYPTODISK=y for encrypted root")
        return JobResult.ok("GRUB_ENABLE_CRYPTODISK enabled")

    def _install_grub(self, context: JobContext) -> JobResult:
        """
        Install GRUB bootloader.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        if not self._efi_mount:
            return JobResult.fail("EFI mount point not initialized", error_code=63)

        defaults_result = self._write_grub_defaults(context.target_root, context)
        if not defaults_result.success:
            return defaults_result

        is_encrypted, _ = resolve_root_encryption(context.selections)
        if is_encrypted:
            cryptodisk_result = self._enable_grub_cryptodisk(context.target_root)
            if not cryptodisk_result.success:
                return cryptodisk_result

        context.report_progress(30, "Installing GRUB...")

        try:
            result = subprocess.run(
                [
                    "arch-chroot",
                    context.target_root,
                    "grub-install",
                    "--target=x86_64-efi",
                    "--efi-directory=/boot/efi",
                    "--bootloader-id=XeroLinux",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("GRUB installed successfully")
            if result.stdout:
                logger.debug(f"grub-install output: {result.stdout}")

        except subprocess.CalledProcessError as e:
            logger.error(f"grub-install failed: {e.stderr}")
            return JobResult.fail(
                f"Failed to install GRUB: {e.stderr}",
                error_code=64,
            )
        except FileNotFoundError:
            return JobResult.fail(
                "Required tool not found: arch-chroot or grub-install",
                error_code=65,
            )

        # Fallback EFI path (/EFI/BOOT/BOOTX64.EFI) for firmware that
        # ignores NVRAM boot entries. Non-fatal: the named entry above
        # already covers firmware that does honor them.
        try:
            fallback = subprocess.run(
                [
                    "arch-chroot",
                    context.target_root,
                    "grub-install",
                    "--target=x86_64-efi",
                    "--efi-directory=/boot/efi",
                    "--bootloader-id=XeroLinux",
                    "--removable",
                ],
                capture_output=True,
                text=True,
            )
            if fallback.returncode != 0:
                logger.warning(f"EFI fallback grub-install failed: {fallback.stderr}")
        except FileNotFoundError:
            logger.warning("grub-install not found for EFI fallback install")

        if context.selections.get("filesystem") == "btrfs":
            self._disable_grubenv_loading(context.target_root)

        self._disable_efi_bootnext_script(context.target_root)

        # Generate GRUB configuration
        context.report_progress(60, "Generating GRUB configuration...")

        try:
            result = subprocess.run(
                [
                    "arch-chroot",
                    context.target_root,
                    "grub-mkconfig",
                    "-o",
                    "/boot/grub/grub.cfg",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("GRUB configuration generated")
            if result.stdout:
                logger.debug(f"grub-mkconfig output: {result.stdout}")

        except subprocess.CalledProcessError as e:
            logger.error(f"grub-mkconfig failed: {e.stderr}")
            return JobResult.fail(
                f"Failed to generate GRUB configuration: {e.stderr}",
                error_code=66,
            )

        return JobResult.ok("GRUB installed and configured")

    # The exact heredoc block /etc/grub.d/00_header emits unconditionally
    # (not gated by GRUB_DEFAULT/GRUB_SAVEDEFAULT) into every grub.cfg:
    #   cat << EOF
    #   if [ -s $prefix/grubenv ]; then
    #     load_env
    #   fi
    #   ...
    #   EOF
    # Anchored on the distinctive "if [ -s $prefix/grubenv ]" line so this
    # only ever matches this specific block, not any other heredoc in the
    # file.
    _GRUBENV_LOAD_BLOCK_RE = re.compile(
        r"^cat << EOF\nif \[ -s \\\$prefix/grubenv \].*?\nEOF\n",
        re.MULTILINE | re.DOTALL,
    )

    def _disable_grubenv_loading(self, target_root: str) -> None:
        """Strips 00_header's unconditional `load_env` call - Btrfs's CoW
        layout breaks GRUB's raw-blocklist env reads, producing a cosmetic
        boot error every boot (non-fatal, boot continues). Editing the
        source script (not the generated grub.cfg) makes this durable
        across future grub-mkconfig runs. Non-fatal: cosmetic cleanup only.

        Not the only grubenv access point - see _write_grub_defaults's
        GRUB_SAVEDEFAULT handling too.
        """
        header_path = Path(target_root) / "etc" / "grub.d" / "00_header"
        try:
            content = header_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"Failed to read {header_path}: {e}")
            return

        new_content = self._GRUBENV_LOAD_BLOCK_RE.sub("", content, count=1)
        if new_content == content:
            logger.warning(f"grubenv load block not found in {header_path}, leaving as-is")
            return

        try:
            header_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            logger.warning(f"Failed to write {header_path}: {e}")
            return

        logger.info(f"Removed unconditional grubenv load from {header_path}")

    def _disable_efi_bootnext_script(self, target_root: str) -> None:
        """Strip the execute bit from /etc/grub.d/31_efi_bootnext so
        grub-mkconfig skips it (grub-mkconfig only sources executable
        scripts in /etc/grub.d/). Run before grub-mkconfig, every GRUB
        install - not gated on any filesystem. Non-fatal: the script isn't
        shipped by every grub version, so a missing file is a normal no-op.
        """
        script_path = Path(target_root) / "etc" / "grub.d" / "31_efi_bootnext"
        try:
            if not script_path.exists():
                return
            mode = script_path.stat().st_mode
            script_path.chmod(mode & ~0o111)
        except OSError as e:
            logger.warning(f"Failed to clear execute bit on {script_path}: {e}")
            return

        logger.info(f"Disabled {script_path} (chmod -x) ahead of grub-mkconfig")

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

    def _get_root_partition_uuid(self, context: JobContext) -> str | None:
        """
        Get UUID of root partition.

        Args:
            context: Execution context

        Returns:
            UUID string or None if not found
        """
        target_root = Path(context.target_root)

        # Try to read from /etc/fstab in target
        fstab_path = target_root / "etc" / "fstab"
        if fstab_path.exists():
            try:
                fstab_content = fstab_path.read_text(encoding="utf-8")
                for line in fstab_content.splitlines():
                    if "/" in line and "UUID=" in line:
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "/":
                            uuid_part = parts[0]
                            if uuid_part.startswith("UUID="):
                                uuid = uuid_part.replace("UUID=", "")
                                logger.info(f"Found root UUID from fstab: {uuid}")
                                return uuid
            except OSError as e:
                logger.warning(f"Failed to read fstab: {e}")

        # Fallback: try to get UUID from partition info
        # This would require parsing lsblk or similar
        # For now, try to use mountpoint
        try:
            result = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", target_root],
                check=True,
                capture_output=True,
                text=True,
            )
            root_device = result.stdout.strip()

            # Get UUID of device
            result = subprocess.run(
                ["blkid", "-s", "UUID", "-o", "value", root_device],
                check=True,
                capture_output=True,
                text=True,
            )
            uuid = result.stdout.strip()
            logger.info(f"Found root UUID from blkid: {uuid}")
            return uuid

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to get root UUID: {e}")

        return None

    def _get_bootloader_type(self, context: JobContext) -> str:
        """Resolve which bootloader to install: the yaml profile's own
        `type:` is authoritative; context.selections["bootloader"] is a
        fallback for a profile that decides this at runtime instead."""
        configured = self._config.get("type")
        if configured:
            return str(configured)
        return str(context.selections.get("bootloader", self.SYSTEMD_BOOT))

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate bootloader configuration.

        Args:
            context: Execution context

        Returns:
            JobResult indicating validation status
        """
        context.report_progress(0, "Validating bootloader configuration...")

        # Validate bootloader selection
        bootloader = self._get_bootloader_type(context)
        if bootloader not in [self.SYSTEMD_BOOT, self.GRUB]:
            return JobResult.fail(
                f"Invalid bootloader: {bootloader}. Must be '{self.SYSTEMD_BOOT}' or '{self.GRUB}'",
                error_code=67,
            )

        # Validate EFI system
        efi_result = self._validate_efi_system(context)
        if not efi_result.success:
            return efi_result

        # Validate kernel presence
        kernel_result = self._detect_kernels(context)
        if not kernel_result.success:
            return kernel_result

        return JobResult.ok(
            "Bootloader configuration validated",
            data={
                "bootloader": bootloader,
                "efi_mount": str(self._efi_mount) if self._efi_mount else "",
                "kernels": self._kernels,
            },
        )

    def run(self, context: JobContext) -> JobResult:
        """
        Execute bootloader installation.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(0, "Starting bootloader installation...")

        if context.selections.get("dry_run"):
            context.report_progress(100, "Skipped (dry run)")
            return JobResult.ok("Skipped: bootloader installation (dry run)")

        # Validate first
        validation = self.validate(context)
        if not validation.success:
            return validation

        bootloader = self._get_bootloader_type(context)

        logger.info(f"Installing bootloader: {bootloader}")

        # Install selected bootloader
        if bootloader == self.SYSTEMD_BOOT:
            result = self._install_systemd_boot(context)
        elif bootloader == self.GRUB:
            result = self._install_grub(context)
        else:
            return JobResult.fail(
                f"Unsupported bootloader: {bootloader}",
                error_code=68,
            )

        if not result.success:
            return result

        context.report_progress(100, "Bootloader installation complete")

        return JobResult.ok(
            f"{bootloader} installed successfully",
            data={
                "bootloader": bootloader,
                "efi_mount": str(self._efi_mount) if self._efi_mount else "",
                "kernels": self._kernels,
            },
        )

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Returns:
            Estimated duration for bootloader installation
        """
        return 60

"""
Packages Job - System package installation.

Handles package installation for the target system (pacman only - this is an
Arch/XeroLinux-only installer) with support for multiple installation modes
and error recovery.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from xen.i18n.messages import msg
from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.utils.network_helper import NetworkHelper

logger = logging.getLogger(__name__)


def _join_messages(messages: list[str]) -> str:
    joined = messages[0]
    for message in messages[1:]:
        joined = msg("{head}; {tail}", head=joined, tail=message)
    return joined


_PACMAN_FAILED = 36


class PackagesJob(BaseJob):
    """
    System package installation job.

    Responsibilities:
    - Install essential system packages
    - Configure package repositories
    - Support multiple installation modes (essential, desktop, custom)
    - Handle installation errors with retry logic
    - Track installation progress
    """

    name = "packages"
    description = "System package installation"

    # Package installation modes
    MODE_ESSENTIAL = "essential"
    MODE_DESKTOP = "desktop"
    MODE_CUSTOM = "custom"

    # Essential packages for minimal system
    ESSENTIAL_PACKAGES = [
        "base",
        "linux",
        "linux-firmware",
        "networkmanager",
        "sudo",
        "nano",
        "vim",
    ]

    # Desktop environment packages
    DESKTOP_PACKAGES = [
        "xorg",
        "plasma",
        "kde-applications",
        "firefox",
        "konsole",
        "dolphin",
        "sddm",
    ]

    MAX_RETRIES = 6
    RETRY_DELAY = 5  # seconds

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the packages job."""
        super().__init__(config)
        self._packages_installed: list[str] = []
        self._packages_failed: list[str] = []

    def _get_installation_mode(self, context: JobContext) -> str:
        """
        Get installation mode from context.

        Args:
            context: Execution context with selections

        Returns:
            Installation mode (essential, desktop, custom)
        """
        return context.selections.get("mode", self._config.get("mode", self.MODE_ESSENTIAL))

    def _get_package_list(self, context: JobContext) -> list[str]:
        """
        Build package list based on installation mode.

        Args:
            context: Execution context with selections

        Returns:
            List of package names to install
        """
        mode = self._get_installation_mode(context)

        if mode == self.MODE_ESSENTIAL:
            packages = self.ESSENTIAL_PACKAGES.copy()
        elif mode == self.MODE_DESKTOP:
            packages = self.ESSENTIAL_PACKAGES.copy()
            packages.extend(self.DESKTOP_PACKAGES)
        elif mode == self.MODE_CUSTOM:
            custom_packages = context.selections.get("packages", [])
            if not custom_packages:
                logger.warning("Custom mode selected but no packages specified")
                packages = self.ESSENTIAL_PACKAGES.copy()
            else:
                packages = list(custom_packages)
        else:
            logger.warning(f"Unknown mode '{mode}', using essential packages")
            packages = self.ESSENTIAL_PACKAGES.copy()

        for extra_pkg in self._config.get("extra_packages", []):
            if extra_pkg not in packages:
                packages.append(extra_pkg)

        for optional_pkg in context.selections.get("optional_packages", []):
            if optional_pkg not in packages:
                packages.append(optional_pkg)

        if context.selections.get("filesystem") == "btrfs":
            for btrfs_pkg in self._config.get("btrfs_packages", []):
                if btrfs_pkg not in packages:
                    packages.append(btrfs_pkg)

        return packages

    def _validate_package_names(self, packages: list[str]) -> JobResult:
        """
        Validate package names format.

        Args:
            packages: List of package names

        Returns:
            JobResult indicating validation success or failure
        """
        if not packages:
            return JobResult.fail(msg("Package list is empty"), error_code=30)

        invalid_packages = []
        for pkg in packages:
            # Basic validation: alphanumeric, dash, underscore
            if not pkg or not all(c.isalnum() or c in "-_." for c in pkg):
                invalid_packages.append(pkg)

        if invalid_packages:
            return JobResult.fail(
                msg("Invalid package names: {names}", names=invalid_packages),
                error_code=31,
                data={"invalid_packages": invalid_packages},
            )

        return JobResult.ok(msg("Package names validated"))

    def _update_repositories(self, context: JobContext) -> JobResult:
        """
        Update package repositories/mirrors before installation.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        target_root = context.target_root

        context.report_progress(5, msg("Updating package repositories..."))
        logger.info("Updating pacman repositories")

        try:
            cmd = [
                "arch-chroot",
                target_root,
                "pacman",
                "-Syy",
                "--noconfirm",
                "--disable-download-timeout",
            ]

            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes timeout
            )

            logger.info("Repository update successful")
            logger.debug(f"Update output: {result.stdout}")
            return JobResult.ok(msg("Repositories updated"))

        except subprocess.CalledProcessError as e:
            logger.error(f"Repository update failed: {e.stderr}")
            return JobResult.fail(
                msg("Failed to update repositories: {error}", error=e.stderr),
                error_code=33,
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail(
                msg("Repository update timed out"),
                error_code=34,
            )
        except FileNotFoundError:
            return JobResult.fail(
                msg("arch-chroot not found. Cannot access target system."),
                error_code=35,
            )

    def _install_packages_pacman(
        self,
        packages: list[str],
        target_root: str,
        context: JobContext,
    ) -> JobResult:
        """
        Install packages using pacman.

        Args:
            packages: List of package names
            target_root: Target root directory
            context: Execution context for progress reporting

        Returns:
            JobResult indicating success or failure
        """
        logger.info(f"Installing {len(packages)} packages with pacman")

        cmd = [
            "arch-chroot",
            target_root,
            "pacman",
            "-S",
            "--noconfirm",
            "--needed",  # Skip already installed packages
            "--disable-download-timeout",
        ]
        cmd.extend(packages)

        try:
            # Run pacman with real-time output parsing
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            installed_count = 0
            total_packages = len(packages)

            for line in process.stdout:  # type: ignore
                line = line.strip()
                logger.debug(f"pacman: {line}")

                # Track installation progress
                if "installing" in line.lower():
                    installed_count += 1
                    percent = 20 + int((installed_count / total_packages) * 70)
                    context.report_progress(
                        percent,
                        msg(
                            "Installing packages... ({installed}/{total})",
                            installed=installed_count,
                            total=total_packages,
                        ),
                    )

            return_code = process.wait()

            if return_code != 0:
                return JobResult.fail(
                    msg("pacman failed with exit code {code}", code=return_code),
                    error_code=_PACMAN_FAILED,
                    data={"return_code": return_code},
                )

            self._packages_installed.extend(packages)
            return JobResult.ok(
                msg("Installed {count} packages", count=len(packages)),
                data={"packages": packages},
            )

        except FileNotFoundError:
            return JobResult.fail(
                msg("pacman or arch-chroot not found"),
                error_code=35,
            )
        except Exception as e:
            logger.exception("Unexpected error during package installation")
            return JobResult.fail(
                msg("Package installation failed: {error}", error=e),
                error_code=37,
                data={"exception": str(e)},
            )

    def _installed_packages(self, target_root: str) -> set[str] | None:
        """Set of package names installed on target, or None if the query
        itself failed (as opposed to legitimately returning none)."""
        try:
            result = subprocess.run(
                ["arch-chroot", target_root, "pacman", "-Qq"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None
        if result.returncode != 0:
            return None
        return set(result.stdout.split())

    def _remove_packages(
        self,
        packages: list[str],
        target_root: str,
        context: JobContext,
    ) -> JobResult:
        """
        Remove packages left over from the live ISO.

        falkon stays installed offline (fallback browser: packagechooser's
        options aren't reachable without a connection), removed online.

        pacman -R is all-or-nothing: one configured package that isn't
        actually installed fails removal of everything else in the list
        too. Only pass pacman the subset that's actually present.

        Failure here is non-fatal: cosmetic live-session leftovers, not
        required for a bootable/usable target system.
        """
        if "falkon" in packages:
            has_internet = context.selections.get("has_internet")
            if has_internet is None:
                has_internet = NetworkHelper.check_internet_connectivity()
            if not has_internet:
                packages = [p for p in packages if p != "falkon"]

        installed = self._installed_packages(target_root)
        if installed is None:
            to_remove = packages
        else:
            to_remove = [p for p in packages if p in installed]
            if not to_remove:
                return JobResult.ok(
                    msg("Nothing to remove (none of the configured packages present)")
                )

        logger.info(f"Removing {len(to_remove)} live-only packages")
        cmd = ["arch-chroot", target_root, "pacman", "-R", "--noconfirm", *to_remove]

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            logger.debug(f"Remove output: {result.stdout}")
            return JobResult.ok(
                msg("Removed {count} packages", count=len(to_remove)),
                data={"packages": to_remove},
            )
        except subprocess.CalledProcessError as e:
            return JobResult.fail(
                msg("Failed to remove packages: {error}", error=e.stderr), error_code=41
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail(msg("Package removal timed out"), error_code=42)
        except FileNotFoundError:
            return JobResult.fail(
                msg("arch-chroot not found. Cannot access target system."),
                error_code=35,
            )

    def _install_packages_with_retry(
        self,
        packages: list[str],
        context: JobContext,
    ) -> JobResult:
        """
        Install packages with retry logic for network failures.

        Args:
            packages: List of package names
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        target_root = context.target_root

        last_result = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            logger.info(f"Installation attempt {attempt}/{self.MAX_RETRIES}")

            result = self._install_packages_pacman(packages, target_root, context)

            if result.success:
                return result

            last_result = result

            # pacman exits 1 for every failure, transient download errors
            # included; a missing tool or an internal error won't heal on retry.
            if result.error_code != _PACMAN_FAILED:
                return result

            if attempt < self.MAX_RETRIES:
                logger.warning(
                    f"Installation failed (attempt {attempt}), retrying in {self.RETRY_DELAY}s..."
                )
                context.report_progress(
                    15,
                    msg(
                        "Installation failed, retrying... (attempt {attempt}/{total})",
                        attempt=attempt + 1,
                        total=self.MAX_RETRIES,
                    ),
                )
                time.sleep(self.RETRY_DELAY)

        # Max retries reached
        return JobResult.fail(
            msg("Package installation failed after {count} attempts", count=self.MAX_RETRIES),
            error_code=39,
            data={
                "attempts": self.MAX_RETRIES,
                "packages_failed": packages,
                "last_error": last_result.message if last_result else "Unknown",
            },
        )

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate package installation prerequisites.

        Checks:
        - Installation mode is valid
        - Package list is valid
        - Target is accessible
        - Required tools are available

        Args:
            context: Execution context

        Returns:
            JobResult indicating validation success or failure
        """
        errors = []

        # Validate installation mode
        mode = self._get_installation_mode(context)
        valid_modes = [self.MODE_ESSENTIAL, self.MODE_DESKTOP, self.MODE_CUSTOM]
        if mode not in valid_modes:
            errors.append(
                msg("Invalid mode: {mode}. Valid modes: {modes}", mode=mode, modes=valid_modes)
            )

        # Validate custom packages if in custom mode
        if mode == self.MODE_CUSTOM:
            packages = context.selections.get("packages", [])
            if not packages:
                errors.append(msg("Custom mode requires 'packages' list in selections"))
            else:
                validation = self._validate_package_names(packages)
                if not validation.success:
                    errors.append(validation.message)

        # Validate target root exists
        target_path = Path(context.target_root)
        if not target_path.exists():
            errors.append(msg("Target directory not found: {path}", path=context.target_root))
        elif not target_path.is_dir():
            errors.append(msg("Target path is not a directory: {path}", path=context.target_root))

        if errors:
            return JobResult.fail(
                message=msg("Validation errors: {errors}", errors=_join_messages(errors)),
                error_code=40,
                data={"errors": errors},
            )

        logger.info("Package installation prerequisites validated")
        return JobResult.ok(msg("Package installation validated"))

    def run(self, context: JobContext) -> JobResult:
        """
        Execute package installation job.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(0, msg("Starting package installation..."))

        if context.selections.get("dry_run"):
            context.report_progress(100, msg("Skipped (dry run)"))
            return JobResult.ok(msg("Skipped: package installation (dry run)"))

        # Validate first
        validation = self.validate(context)
        if not validation.success:
            return validation

        # Get package list
        packages = self._get_package_list(context)
        mode = self._get_installation_mode(context)

        logger.info(f"Installing packages in {mode} mode: {packages}")
        context.report_progress(2, msg("Installing {count} packages...", count=len(packages)))

        has_internet = context.selections.get("has_internet")
        if has_internet is None:
            has_internet = NetworkHelper.check_internet_connectivity()

        if not has_internet:
            # The live ISO ships no pacman sync databases and no cached
            # package archives (both are stripped from the squashfs at
            # build time) - there is no local source pacman could install
            # anything new from. base/linux/etc. are already on disk from
            # install.py's squashfs extraction, so skip straight to the
            # remove step, matching how a purely offline install has
            # nothing new to add, only leftovers to strip.
            context.report_progress(10, msg("Offline, nothing new to install"))
        else:
            if context.selections.get("repos_synced"):
                context.report_progress(10, msg("Repositories already synced, skipping"))
            else:
                update_result = self._update_repositories(context)
                if not update_result.success:
                    logger.warning("Repository update failed, continuing with installation...")
                    # Non-critical: continue with installation
                    context.report_progress(10, msg("Repository update failed"))
                else:
                    context.report_progress(10, msg("Repositories updated"))

            install_result = self._install_packages_with_retry(packages, context)
            if not install_result.success:
                return install_result

            context.report_progress(95, msg("Package installation complete"))
            logger.info(f"Successfully installed {len(self._packages_installed)} packages")

        remove_list = self._config.get("remove", [])
        if remove_list:
            context.report_progress(96, msg("Removing live-only packages..."))
            remove_result = self._remove_packages(remove_list, context.target_root, context)
            if not remove_result.success:
                logger.warning(f"Package removal failed (non-fatal): {remove_result.message}")

        context.report_progress(100, msg("Package installation finished"))

        return JobResult.ok(
            msg("Packages installed successfully"),
            data={
                "mode": mode,
                "packages_installed": self._packages_installed,
                "packages_failed": self._packages_failed,
                "total_packages": len(self._packages_installed),
            },
        )

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Estimation varies based on:
        - Number of packages
        - Network speed
        - Package size
        - System resources

        Returns:
            Estimated duration in seconds
        """
        # Base estimate: 5 minutes for essential packages
        # Desktop mode: 15 minutes (more packages)
        base_estimate = 300

        if hasattr(self, "_config") and self._config:
            mode = self._config.get("mode", self.MODE_ESSENTIAL)
            if mode == self.MODE_DESKTOP:
                return 900  # 15 minutes
            if mode == self.MODE_CUSTOM:
                packages = self._config.get("packages", [])
                # Estimate ~10 seconds per package
                return max(base_estimate, len(packages) * 10)

        return base_estimate

    def cleanup(self, _context: JobContext) -> None:
        """
        Clean up after package installation.

        Clears package cache if needed.

        Args:
            _context: Execution context (unused)
        """
        logger.debug("PackagesJob cleanup - package cache retained")

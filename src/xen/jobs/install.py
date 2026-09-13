"""
Install Job - System files copy to target disk.

This job copies the live system or squashfs image to the target partition
using rsync with progress tracking.
"""

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)


class InstallJob(BaseJob):
    """
    Installation job - copies system files to target disk.

    Supports two source types:
    - live: Copy from running live system (default)
    - squashfs: Extract from compressed squashfs image

    Configuration (context.selections):
        source: str = "/"                  # Source path to copy from (source_type=live only)
        source_type: str = "squashfs"      # "live" or "squashfs" - squashfs extracts the
                                            # ISO's build-time image, avoiding session-only
                                            # content (live user credentials, browser data)
        squashfs_path: str | None = None   # Path to .sfs file; auto-detected from the
                                            # standard archiso runtime mount if omitted
        verify_install: bool = False       # Run post-install verification
    """

    name = "install"
    description = "System installation - copy files to target"

    # Directories to exclude from rsync copy
    EXCLUDE_DIRS = [
        "/proc",
        "/sys",
        "/dev",
        "/run",
        "/tmp",
        "/mnt",
        "/media",
        "/lost+found",
        "/var/cache",
        "/var/tmp",
        "/var/log",
        "/home/*/.cache",
        "/root/.cache",
        # These identify the *live* environment, not the target disk - the
        # systemconfig job regenerates them for the target instead of
        # carrying over the live session's copies.
        "/etc/fstab",
        "/etc/machine-id",
    ]

    # Critical files that must exist after installation
    CRITICAL_FILES = [
        "/etc/fstab",
        "/etc/passwd",
        "/etc/group",
        "/etc/shadow",
        "/etc/hostname",
        "/boot",
        "/usr/bin",
        "/usr/lib",
    ]

    # Minimum required free space (bytes) - 5 GB
    MIN_FREE_SPACE = 5 * 1024 * 1024 * 1024

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the install job."""
        super().__init__(config)
        self._source_size_bytes: int = 0
        self._bytes_copied: int = 0

    def run(self, context: JobContext) -> JobResult:
        """
        Execute the installation job.

        Args:
            context: Execution context with selections and target_root

        Returns:
            JobResult indicating success or failure
        """
        # Get configuration
        source = context.selections.get("source", "/")
        source_type = context.selections.get("source_type", "squashfs")
        verify = context.selections.get("verify_install", False)
        target = context.target_root

        if source_type == "squashfs":
            squashfs_path = self._resolve_squashfs_path(context)
        else:
            squashfs_path = context.selections.get("squashfs_path")

        context.report_progress(0, "Preparing installation...")
        logger.info(f"Starting installation from {source} to {target}")

        if context.selections.get("dry_run"):
            context.report_progress(100, "Skipped (dry run)")
            return JobResult.ok("Skipped: system installation (dry run)")

        # Handle squashfs extraction
        if source_type == "squashfs":
            if not squashfs_path:
                return JobResult.fail(
                    "No squashfs_path given and none could be auto-detected. "
                    "Expected a live ISO image under /run/archiso/bootmnt/.",
                    error_code=50,
                )

            result = self._extract_squashfs(squashfs_path, target, context)
            if not result.success:
                return result

        else:
            # Standard rsync copy from live system
            result = self._run_rsync(source, target, context)
            if not result.success:
                return result

        result = self._ensure_pseudo_mountpoints(target)
        if not result.success:
            return result

        # Verify installation if requested
        if verify:
            context.report_progress(95, "Verifying installation...")
            result = self._verify_installation(target)
            if not result.success:
                return result

        context.report_progress(100, "Installation complete")
        logger.info("Installation completed successfully")

        return JobResult.ok(
            "System files copied successfully",
            data={
                "source": source,
                "target": target,
                "source_type": source_type,
                "bytes_copied": self._bytes_copied,
                "verified": verify,
            },
        )

    # Top-level dirs excluded from the rsync copy above (pseudo-filesystems
    # and other live-only content) that still need to exist as empty
    # mountpoints/directories on the target, with their standard FHS mode.
    _PSEUDO_MOUNTPOINTS = {
        "proc": 0o555,
        "sys": 0o555,
        "dev": 0o755,
        "run": 0o755,
        "tmp": 0o1777,
        "mnt": 0o755,
        "media": 0o755,
    }

    def _ensure_pseudo_mountpoints(self, target: str) -> JobResult:
        """
        Recreate the pseudo-filesystem/live-only directories excluded from
        the rsync copy (EXCLUDE_DIRS) as empty mountpoints.

        Without this, the target has no /proc, /sys, /dev, or /run
        directories at all, so the very first `arch-chroot` call afterward
        (systemconfig's machine-id generation) fails to bind-mount them:
        "mount: /mnt/target/proc: mount point does not exist."
        """
        target_root = Path(target)
        try:
            for name, mode in self._PSEUDO_MOUNTPOINTS.items():
                path = target_root / name
                path.mkdir(mode=mode, exist_ok=True)
                os.chmod(path, mode)
        except OSError as e:
            return JobResult.fail(
                f"Failed to create pseudo-filesystem mountpoints: {e}", error_code=70
            )
        return JobResult.ok("Pseudo-filesystem mountpoints created")

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate installation prerequisites.

        Checks:
        - Source exists and is readable
        - Target is mounted and writable
        - Sufficient disk space available
        - Required tools are available (rsync, du)

        Args:
            context: Execution context

        Returns:
            JobResult.ok() if valid, JobResult.fail() with error code otherwise
        """
        source = context.selections.get("source", "/")
        source_type = context.selections.get("source_type", "squashfs")
        target = context.target_root

        if source_type == "squashfs":
            squashfs_path = self._resolve_squashfs_path(context)
        else:
            squashfs_path = context.selections.get("squashfs_path")

        # Validate source type
        if source_type not in ("live", "squashfs"):
            return JobResult.fail(
                f"Invalid source_type: {source_type}. Must be 'live' or 'squashfs'",
                error_code=51,
            )

        # Validate squashfs path if needed
        if source_type == "squashfs":
            if not squashfs_path:
                return JobResult.fail(
                    "No squashfs_path given and none could be auto-detected. "
                    "Expected a live ISO image under /run/archiso/bootmnt/.",
                    error_code=50,
                )

            sfs_path = Path(squashfs_path)
            if not sfs_path.exists():
                return JobResult.fail(
                    f"Squashfs file not found: {squashfs_path}",
                    error_code=52,
                )

            if not sfs_path.is_file():
                return JobResult.fail(
                    f"Squashfs path is not a file: {squashfs_path}",
                    error_code=53,
                )

            # Check if unsquashfs is available
            if not shutil.which("unsquashfs"):
                return JobResult.fail(
                    "unsquashfs tool not found. Install squashfs-tools package.",
                    error_code=54,
                )

        else:
            # Validate live source
            source_path = Path(source)
            if not source_path.exists():
                return JobResult.fail(
                    f"Source directory not found: {source}",
                    error_code=55,
                )

            if not source_path.is_dir():
                return JobResult.fail(
                    f"Source path is not a directory: {source}",
                    error_code=56,
                )

        # Validate target
        target_path = Path(target)
        if not target_path.exists():
            return JobResult.fail(
                f"Target directory not found: {target}. Mount the target partition first.",
                error_code=57,
            )

        if not target_path.is_dir():
            return JobResult.fail(
                f"Target path is not a directory: {target}",
                error_code=58,
            )

        # Check if target directory is writable for the current user
        if not os.access(target_path, os.W_OK):
            return JobResult.fail(
                f"Target directory is not writable: {target}",
                error_code=59,
            )

        # Check required tools
        if source_type == "live" and not shutil.which("rsync"):
            return JobResult.fail(
                "rsync tool not found. Install rsync package.",
                error_code=60,
            )

        if not shutil.which("du"):
            return JobResult.fail(
                "du tool not found. Required for disk space calculation.",
                error_code=61,
            )

        # Check available disk space
        target_stat = shutil.disk_usage(target)
        if target_stat.free < self.MIN_FREE_SPACE:
            free_gb = target_stat.free / (1024**3)
            min_gb = self.MIN_FREE_SPACE / (1024**3)
            return JobResult.fail(
                f"Insufficient disk space on target. "
                f"Available: {free_gb:.2f} GB, Required: {min_gb:.2f} GB minimum",
                error_code=62,
            )

        # Calculate source size for space validation
        if source_type == "live":
            try:
                source_size = self._get_source_size(source)
                if source_size > target_stat.free:
                    needed_gb = source_size / (1024**3)
                    free_gb = target_stat.free / (1024**3)
                    return JobResult.fail(
                        f"Insufficient disk space. "
                        f"Source size: {needed_gb:.2f} GB, Available: {free_gb:.2f} GB",
                        error_code=63,
                    )
            except subprocess.CalledProcessError as e:
                logger.warning(f"Could not calculate source size: {e}")
                # Non-critical - continue with installation

        logger.info("Installation prerequisites validated successfully")
        return JobResult.ok(
            "Installation prerequisites validated",
            data={
                "source": source,
                "target": target,
                "available_space_gb": target_stat.free / (1024**3),
            },
        )

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Estimation based on typical system size (5-10 GB) and disk I/O speed.
        Actual duration varies significantly based on:
        - Source size
        - Disk speed (HDD vs SSD vs NVMe)
        - System load

        Returns:
            Estimated duration in seconds (5 minutes default)
        """
        # Base estimate: 5 minutes for typical installation
        base_estimate = 300

        # If we have source size, adjust estimate
        if self._source_size_bytes > 0:
            # Assume 100 MB/s average copy speed
            estimated_seconds = self._source_size_bytes / (100 * 1024 * 1024)
            return max(60, int(estimated_seconds * 1.5))  # Add 50% safety margin

        return base_estimate

    def _get_source_size(self, source: str) -> int:
        """
        Calculate source directory size in bytes.

        Uses 'du' command with same exclusions as rsync to get accurate size.

        Args:
            source: Source directory path

        Returns:
            Size in bytes

        Raises:
            subprocess.CalledProcessError: If du command fails
        """
        logger.info(f"Calculating source size for {source}")

        # Build du command with exclusions
        cmd = ["du", "-sb"]

        # Add exclusions
        for exclude_dir in self.EXCLUDE_DIRS:
            cmd.extend(["--exclude", exclude_dir])

        cmd.append(source)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )

        # Parse output: "12345678\t/source/path"
        size_str = result.stdout.split("\t")[0].strip()
        size_bytes = int(size_str)

        self._source_size_bytes = size_bytes
        size_gb = size_bytes / (1024**3)
        logger.info(f"Source size: {size_gb:.2f} GB ({size_bytes} bytes)")

        return size_bytes

    @staticmethod
    def _estimate_eta(start_time: float, percent: int) -> str:
        """
        Estimate remaining time from average throughput so far, formatted
        as rsync's own H:MM:SS.

        Args:
            start_time: time.monotonic() value captured when the copy began
            percent: Raw rsync completion percentage (0-100)

        Returns:
            Remaining time as "H:MM:SS"
        """
        if percent <= 0:
            return "0:00:00"
        elapsed = time.monotonic() - start_time
        remaining_seconds = int(elapsed / percent * (100 - percent))
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    def _run_rsync(self, source: str, target: str, context: JobContext) -> JobResult:
        """
        Execute rsync to copy files from source to target.

        Uses rsync with:
        - Archive mode (-a): recursive, preserve permissions, timestamps, etc.
        - Extended attributes (-A): preserve ACLs
        - Extended attributes (-X): preserve extended attributes
        - Hard links (-H): preserve hard links
        - Progress info (--info=progress2): for progress tracking
        - Exclusions: virtual filesystems and cache directories

        Args:
            source: Source directory path
            target: Target directory path
            context: Job context for progress reporting

        Returns:
            JobResult indicating success or failure
        """
        logger.info(f"Starting rsync from {source} to {target}")

        # Calculate source size first for progress tracking
        # Build rsync command
        cmd = [
            "rsync",
            "-aAXHv",
            "--info=progress2",
        ]

        # Add exclusions
        for exclude_dir in self.EXCLUDE_DIRS:
            cmd.extend(["--exclude", exclude_dir])

        # Exclude target directory itself (when target is a subdirectory of source)
        source_path = Path(source).resolve()
        target_path = Path(target).resolve()
        if target_path.is_relative_to(source_path):
            relative_target = str(target_path.relative_to(source_path))
            if relative_target:
                cmd.extend(["--exclude", f"/{relative_target}"])

        # Add source and target
        # Important: source must end with / to copy contents, not directory itself
        if not source.endswith("/"):
            source = source + "/"

        cmd.extend([source, target])

        logger.debug(f"Rsync command: {' '.join(cmd)}")

        context.report_progress(5, "Starting file copy...")

        # Execute rsync with progress parsing
        try:
            rsync_start = time.monotonic()
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Parse progress output
            progress_pattern = re.compile(
                r"^\s*([0-9,]+)\s+(\d+)%\s+([\d.]+[KMGT]B/s)\s+(\d+:\d+:\d+)"
            )

            last_percent = 5
            for line in process.stdout:  # type: ignore
                line = line.strip()

                # Parse progress line: "123,456,789  45%  12.34MB/s    0:01:23"
                match = progress_pattern.match(line)
                if match:
                    try:
                        bytes_str = match.group(1).replace(",", "")
                        percent = int(match.group(2))
                        speed = match.group(3)

                        self._bytes_copied = int(bytes_str)

                        # rsync's own ETA is instantaneous-throughput-based:
                        # bytes-remaining / current-speed. That collapses at
                        # the tail of a transfer with many tiny files (e.g.
                        # the pacman local db) where per-file syscall
                        # overhead dwarfs the few bytes actually left,
                        # producing a wildly-too-large ETA at 99%+. An
                        # average-throughput ETA, based on elapsed time and
                        # percent done so far, stays realistic throughout.
                        eta = self._estimate_eta(rsync_start, percent)

                        # Scale progress to 5-90% range (reserve 90-95 for verification)
                        scaled_percent = 5 + int(percent * 0.85)

                        # Only report if progress increased
                        if scaled_percent > last_percent:
                            context.report_progress(
                                scaled_percent,
                                f"Copying files... {scaled_percent}% ({speed}, ETA {eta})",
                            )
                            last_percent = scaled_percent

                    except (ValueError, IndexError) as e:
                        logger.debug(f"Could not parse progress line: {line} - {e}")

                # Log other rsync output at debug level
                elif line:
                    logger.debug(f"rsync: {line}")

            # Wait for process to complete
            return_code = process.wait()

            if return_code != 0:
                return JobResult.fail(
                    f"rsync failed with exit code {return_code}",
                    error_code=64,
                    data={"return_code": return_code},
                )

            context.report_progress(90, "File copy completed")
            logger.info(f"rsync completed successfully. Bytes copied: {self._bytes_copied}")

            return JobResult.ok(
                "Files copied successfully",
                data={"bytes_copied": self._bytes_copied},
            )

        except FileNotFoundError:
            return JobResult.fail(
                "rsync command not found. Install rsync package.",
                error_code=60,
            )
        except subprocess.TimeoutExpired:
            return JobResult.fail(
                "rsync operation timed out",
                error_code=65,
            )
        except Exception as e:
            logger.exception("Unexpected error during rsync")
            return JobResult.fail(
                f"rsync failed: {e}",
                error_code=66,
                data={"exception": str(e)},
            )

    # Standard archiso runtime layout: the boot medium is mounted read-only
    # at /run/archiso/bootmnt, with the squashfs image at
    # <bootmnt>/<install_dir>/<arch>/<image>.sfs (install_dir is "arch" for
    # XeroLinux's profiles, per ~/XeroBuild/*/profiledef.sh).
    _SQUASHFS_GLOB_PATTERNS = (
        "/run/archiso/bootmnt/*/x86_64/*.sfs",
        "/run/archiso/bootmnt/*/*/x86_64/*.sfs",
    )

    @classmethod
    def _find_live_squashfs(cls) -> str | None:
        """
        Locate the live ISO's own squashfs image at its standard archiso
        runtime mount path.

        Returns:
            Path to the first matching .sfs file found, or None
        """
        for pattern in cls._SQUASHFS_GLOB_PATTERNS:
            matches = sorted(Path("/").glob(pattern.lstrip("/")))
            if matches:
                return str(matches[0])
        return None

    def _resolve_squashfs_path(self, context: JobContext) -> str | None:
        """
        Resolve the squashfs image path: an explicit selection wins, else
        auto-detect the live ISO's own image. Caches the result back onto
        context.selections so validate() and run() agree on the same path
        without re-scanning.
        """
        explicit = context.selections.get("squashfs_path")
        if explicit:
            return str(explicit)

        detected = self._find_live_squashfs()
        if detected:
            context.selections["squashfs_path"] = detected
            logger.info(f"Auto-detected live squashfs image: {detected}")
        return detected

    def _extract_squashfs(self, squashfs_path: str, target: str, context: JobContext) -> JobResult:
        """
        Extract squashfs image to target directory.

        Args:
            squashfs_path: Path to .sfs file
            target: Target directory path
            context: Job context for progress reporting

        Returns:
            JobResult indicating success or failure
        """
        logger.info(f"Extracting squashfs from {squashfs_path} to {target}")

        context.report_progress(5, "Extracting squashfs image...")

        cmd = [
            "unsquashfs",
            "-f",  # Force overwrite
            "-d",
            target,  # Destination
            squashfs_path,
        ]

        try:
            # unsquashfs doesn't provide good progress info, so we'll just show stages
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # Report progress in stages
            progress_stages = [
                (10, "Reading image header..."),
                (30, "Extracting files..."),
                (60, "Processing directories..."),
                (80, "Finalizing extraction..."),
            ]

            stage_idx = 0

            for line in process.stdout:  # type: ignore
                line = line.strip()
                logger.debug(f"unsquashfs: {line}")

                # Update progress through stages as we see output
                if stage_idx < len(progress_stages):
                    percent, message = progress_stages[stage_idx]
                    context.report_progress(percent, message)
                    stage_idx += 1

            return_code = process.wait()

            if return_code != 0:
                return JobResult.fail(
                    f"unsquashfs failed with exit code {return_code}",
                    error_code=67,
                    data={"return_code": return_code},
                )

            context.report_progress(90, "Squashfs extraction completed")
            logger.info("Squashfs extraction completed successfully")

            return JobResult.ok("Squashfs extracted successfully")

        except FileNotFoundError:
            return JobResult.fail(
                "unsquashfs command not found. Install squashfs-tools package.",
                error_code=54,
            )
        except Exception as e:
            logger.exception("Unexpected error during squashfs extraction")
            return JobResult.fail(
                f"Squashfs extraction failed: {e}",
                error_code=68,
                data={"exception": str(e)},
            )

    def _verify_installation(self, target: str) -> JobResult:
        """
        Verify installation integrity.

        Checks:
        - Critical system files exist
        - Directory structure is correct
        - Basic filesystem sanity

        Args:
            target: Target directory path

        Returns:
            JobResult indicating verification success or failure
        """
        logger.info(f"Verifying installation at {target}")

        target_path = Path(target)
        missing_files = []

        # Check critical files
        for critical_file in self.CRITICAL_FILES:
            file_path = target_path / critical_file.lstrip("/")
            if not file_path.exists():
                missing_files.append(critical_file)
                logger.error(f"Critical file missing: {critical_file}")

        if missing_files:
            return JobResult.fail(
                f"Installation verification failed. Missing critical files: {missing_files}",
                error_code=69,
                data={"missing_files": missing_files},
            )

        # Basic sanity checks
        checks_passed = 0
        checks_total = len(self.CRITICAL_FILES)

        for critical_file in self.CRITICAL_FILES:
            file_path = target_path / critical_file.lstrip("/")
            if file_path.exists():
                checks_passed += 1

        logger.info(f"Verification: {checks_passed}/{checks_total} critical files present")

        return JobResult.ok(
            f"Installation verified successfully ({checks_passed}/{checks_total} checks passed)",
            data={
                "checks_passed": checks_passed,
                "checks_total": checks_total,
            },
        )

    def cleanup(self, _context: JobContext) -> None:
        """
        Clean up after installation.

        Currently no cleanup needed - partitions remain mounted for next jobs.

        Args:
            _context: Execution context (unused)
        """
        logger.debug("InstallJob cleanup - no action needed")

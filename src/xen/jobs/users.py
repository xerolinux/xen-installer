"""
Users Job for Xen Installer.

Creates user accounts, sets passwords securely, and configures hostname.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from xen.i18n.messages import msg
from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)

_CHROOT_TIMEOUT = 60


class UsersJob(BaseJob):
    """
    User account and hostname configuration job.

    Responsibilities:
    - Create primary user account with appropriate groups
    - Set user password securely (using chpasswd, never logging passwords)
    - Configure user shell and home directory
    - Set system hostname
    - Grant the primary account sudo access (wheel/sudo group)

    SECURITY: This job handles sensitive data (passwords). All password operations
    are implemented with security best practices:
    - Never log passwords (not even in debug mode)
    - Use secure methods (chpasswd) for password setting
    - Clear password from memory as soon as possible
    """

    name = "users"
    description = "User account and hostname configuration"

    # Every user gets these (unless the job config's default_groups
    # overrides them), filtered against /etc/group so a group missing on the
    # target profile doesn't fail useradd.
    DEFAULT_GROUPS = [
        "wheel",
        "lp",
        "realtime",
        "sambashare",
        "power",
        "video",
        "network",
        "storage",
        "cups",
        "adm",
        "audio",
        "optical",
        "rfkill",
        "scanner",
        "sys",
    ]

    # Valid username pattern (lowercase letters, digits, underscore, hyphen)
    USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")

    # Usernames already claimed by system accounts on a stock Arch install.
    # Reusing one makes useradd fail (UID/GID already taken) or silently
    # takes over a system account's home directory and shell. "liveuser" is
    # matched by name in the live ISO's passwordless polkit rule.
    RESERVED_USERNAMES = frozenset(
        {
            "root",
            "liveuser",
            "nobody",
            "daemon",
            "bin",
            "sys",
            "messagebus",
            "sshd",
            "nscd",
            "polkituser",
            "rtkit",
            "avahi",
            "systemd-network",
            "systemd-resolve",
            "systemd-timesync",
            "systemd-coredump",
            "nm-openvpn",
            "dbus",
        }
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the users job."""
        super().__init__(config)

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate user configuration before execution.

        Args:
            context: Execution context with user selections

        Returns:
            JobResult indicating if configuration is valid
        """
        username = context.selections.get("username", "").strip()
        password = context.selections.get("password", "")
        hostname = context.selections.get("hostname", "").strip()

        # Validate username
        if not username:
            return JobResult.fail(msg("Username is required"), error_code=10)

        if not self._validate_username(username):
            return JobResult.fail(
                msg(
                    "Invalid username '{username}'. Must start with lowercase letter or underscore, "
                    "and contain only lowercase letters, digits, underscores, or hyphens.",
                    username=username,
                ),
                error_code=11,
            )

        if username in self.RESERVED_USERNAMES:
            return JobResult.fail(
                msg(
                    "Username '{username}' is reserved by the system. Please choose another name.",
                    username=username,
                ),
                error_code=14,
            )

        # Validate password (must not be empty, but we don't log it)
        if not password:
            return JobResult.fail(msg("Password is required"), error_code=12)

        # chpasswd reads "name:password" lines: a line break would inject a
        # second entry. Messages must never echo the secret itself.
        root_password = str(context.selections.get("root_password") or "")
        if any(c in pw for pw in (password, root_password) for c in "\r\n"):
            return JobResult.fail(msg("Passwords cannot contain line breaks"), error_code=15)

        # GECOS is a ":"-separated /etc/passwd field.
        fullname = context.selections.get("fullname", "")
        if any(c in fullname for c in ":\r\n"):
            return JobResult.fail(
                msg("Full name cannot contain ':' or line breaks"), error_code=16
            )

        # Validate hostname (optional but if provided must be valid)
        if hostname and not self._validate_hostname(hostname):
            return JobResult.fail(
                msg(
                    "Invalid hostname '{hostname}'. Must contain only lowercase letters, digits, "
                    "and hyphens, and cannot start/end with hyphen.",
                    hostname=hostname,
                ),
                error_code=13,
            )

        return JobResult.ok(msg("User configuration is valid"))

    def run(self, context: JobContext) -> JobResult:
        """
        Execute user account creation and configuration.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(0, msg("Starting user configuration..."))

        if context.selections.get("dry_run"):
            context.report_progress(100, msg("Skipped (dry run)"))
            return JobResult.ok(msg("Skipped: user account creation (dry run)"))

        # Validate first
        validation = self.validate(context)
        if not validation.success:
            return validation

        # Extract configuration
        username = context.selections.get("username", "").strip()
        password = context.selections.get("password", "")  # NEVER LOG THIS
        fullname = context.selections.get("fullname", "").strip()
        hostname = context.selections.get("hostname", "").strip()
        shell = context.selections.get("shell") or self._config.get("default_shell", "/bin/bash")
        target_root = context.target_root

        try:
            # Step 1: Create user account
            context.report_progress(
                20, msg("Creating user account '{username}'...", username=username)
            )
            create_result = self._create_user(
                target_root=target_root,
                username=username,
                fullname=fullname,
                shell=shell,
            )
            if not create_result.success:
                return create_result

            # Step 2: Set user password (SECURE - never log password)
            context.report_progress(50, msg("Setting user password..."))
            password_result = self._set_password(
                target_root=target_root,
                username=username,
                password=password,
            )
            if not password_result.success:
                return password_result

            context.report_progress(60, msg("Configuring root account..."))
            root_result = self._configure_root_password(target_root, context.selections, password)
            if not root_result.success:
                return root_result

            # Step 2.5: Guarantee sudo actually works for the new account.
            # Non-fatal by design - never blocks user creation.
            context.report_progress(65, msg("Ensuring sudo access..."))
            self._ensure_sudo_access(target_root)

            # Step 3: Configure hostname (if provided)
            if hostname:
                context.report_progress(
                    80, msg("Setting hostname '{hostname}'...", hostname=hostname)
                )
                hostname_result = self._set_hostname(target_root, hostname)
                if not hostname_result.success:
                    return hostname_result

            context.report_progress(100, msg("User configuration complete"))

            return JobResult.ok(
                msg("User '{username}' created successfully", username=username),
                data={
                    "username": username,
                    "hostname": hostname or "not_set",
                },
            )

        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {e.cmd} (exit code: {e.returncode})")
            return JobResult.fail(
                msg("User configuration failed: {error}", error=e),
                error_code=20,
            )
        except Exception as e:
            logger.exception("Unexpected error during user configuration")
            return JobResult.fail(
                msg("Unexpected error: {error}", error=e),
                error_code=21,
            )
        finally:
            # SECURITY: Clear password from memory in all cases (success, failure,
            # or unexpected exception), never leaving it referenced.
            password = ""  # noqa: F841

    def _create_user(
        self,
        target_root: str,
        username: str,
        fullname: str,
        shell: str,
    ) -> JobResult:
        """
        Create user account with appropriate groups.

        Args:
            target_root: Target root directory
            username: Username to create
            fullname: Full name (GECOS field)
            shell: Login shell path

        Returns:
            JobResult indicating success or failure
        """
        # Filtered against /etc/group so a group absent from this profile
        # (e.g. no CUPS/scanner packages) never fails useradd outright.
        group_file = Path(target_root) / "etc" / "group"
        group_content = group_file.read_text() if group_file.exists() else ""
        wanted = self._config.get("default_groups") or self.DEFAULT_GROUPS
        groups = [g for g in wanted if f"{g}:" in group_content]

        # wheel/sudo isn't filtered by existence like DEFAULT_GROUPS - one
        # of the two must be present on any Arch/Debian base.
        if "wheel:" not in group_content and "sudo:" in group_content:
            if "sudo" not in groups:
                groups.append("sudo")
        elif "wheel" not in groups:
            groups.insert(0, "wheel")

        groups_str = ",".join(groups)

        # Build useradd command
        cmd = [
            "arch-chroot",
            target_root,
            "useradd",
            "-m",  # Create home directory
            "-G",
            groups_str,  # Supplementary groups
            "-s",
            shell,  # Login shell
        ]

        # Add GECOS field if fullname provided
        if fullname:
            cmd.extend(["-c", fullname])

        cmd.append(username)

        try:
            logger.info(f"Creating user '{username}' with groups: {groups_str}")
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=_CHROOT_TIMEOUT,
            )

            # useradd's default home mode depends on the target's
            # /etc/login.defs UMASK, so set it explicitly.
            subprocess.run(
                ["arch-chroot", target_root, "chmod", "700", f"/home/{username}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=_CHROOT_TIMEOUT,
            )

            return JobResult.ok(msg("User '{username}' created", username=username))

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create user: {e.stderr}")
            return JobResult.fail(
                msg(
                    "Failed to create user '{username}': {error}",
                    username=username,
                    error=e.stderr,
                ),
                error_code=22,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Creating user '{username}' timed out")
            return JobResult.fail(
                msg(
                    "Creating user '{username}' timed out after {seconds}s",
                    username=username,
                    seconds=_CHROOT_TIMEOUT,
                ),
                error_code=22,
            )

    def _configure_root_password(
        self, target_root: str, selections: dict[str, Any], user_password: str
    ) -> JobResult:
        """Set root's password, or lock root when none was chosen.

        The squashfs copy carries the live ISO's /etc/shadow, so leaving root
        untouched would keep whatever root password the live session had.
        """
        if selections.get("root_same_as_user"):
            root_password = user_password
        else:
            root_password = str(selections.get("root_password") or "")
        try:
            if root_password:
                return self._set_password(
                    target_root=target_root, username="root", password=root_password
                )
            return self._lock_root(target_root)
        finally:
            root_password = ""  # noqa: F841

    def _lock_root(self, target_root: str) -> JobResult:
        try:
            subprocess.run(
                ["arch-chroot", target_root, "passwd", "-l", "root"],
                check=True,
                capture_output=True,
                text=True,
                timeout=_CHROOT_TIMEOUT,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            logger.error(f"Failed to lock the root account: {e}")
            return JobResult.fail(msg("Failed to lock the root account"), error_code=25)
        logger.info("Root account locked (no root password chosen)")
        return JobResult.ok(msg("Root account locked"))

    def _ensure_sudo_access(self, target_root: str) -> JobResult:
        """Guarantee wheel has working sudo access, regardless of whether
        the base image's own /etc/sudoers.d rule survived intact. Password
        required - the live ISO's own NOPASSWD rule (g_wheel) is deleted by
        xerolinux-final on purpose and must not be reintroduced here. Always
        returns ok() - a failure here must never block user creation.
        Validates the drop-in with visudo, removing it if invalid rather
        than risking a broken /etc/sudoers.d that locks sudo out entirely.
        """
        sudoers_d = Path(target_root) / "etc" / "sudoers.d"
        drop_in = sudoers_d / "99-wheel"

        try:
            sudoers_d.mkdir(parents=True, exist_ok=True)
            drop_in.write_text("%wheel ALL=(ALL:ALL) ALL\n", encoding="utf-8")
            os.chmod(drop_in, 0o440)
        except OSError as e:
            logger.warning(f"Failed to write {drop_in}: {e}")
            return JobResult.ok(msg("Sudo drop-in write failed (non-fatal)"))

        try:
            result = subprocess.run(
                ["arch-chroot", target_root, "visudo", "-cf", "/etc/sudoers.d/99-wheel"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"Could not validate sudoers drop-in: {e}")
            return JobResult.ok(msg("Sudo drop-in validation skipped (non-fatal)"))

        if result.returncode != 0:
            logger.warning(f"Sudoers drop-in failed validation, removing it: {result.stderr}")
            drop_in.unlink(missing_ok=True)
            return JobResult.ok(msg("Sudo drop-in invalid, removed (non-fatal)"))

        logger.info(f"Ensured wheel sudo access via {drop_in}")
        return JobResult.ok(msg("Sudo access ensured"))

    def _set_password(
        self,
        target_root: str,
        username: str,
        password: str,
    ) -> JobResult:
        """
        Set user password securely using chpasswd.

        SECURITY CRITICAL:
        - Password is passed via stdin to chpasswd (not command line)
        - Password is never logged (not even in debug mode)
        - Uses subprocess.PIPE to avoid shell exposure

        Args:
            target_root: Target root directory
            username: Username
            password: Password (NEVER LOGGED)

        Returns:
            JobResult indicating success or failure
        """
        # Format: username:password
        # SECURITY: Do NOT log this string
        password_input = f"{username}:{password}"

        cmd = ["arch-chroot", target_root, "chpasswd"]

        try:
            # SECURITY: Pass password via stdin, never via command line
            subprocess.run(
                cmd,
                input=password_input,
                text=True,
                capture_output=True,
                check=True,
                timeout=_CHROOT_TIMEOUT,
            )

            # SECURITY: Do NOT log that password was set successfully with any details
            logger.info(f"Password configured for user '{username}'")
            return JobResult.ok(msg("Password set"))

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # SECURITY: chpasswd can echo the "username:password" line back in
            # e.stderr, and TimeoutExpired carries the stdin input. Log a static
            # string so nothing leaks at any logger verbosity.
            logger.error("password change failed")
            return JobResult.fail(
                msg("Failed to set user password"),
                error_code=23,
            )
        finally:
            # SECURITY: Clear password material from memory in all cases.
            password_input = ""  # noqa: F841

    def _set_hostname(self, target_root: str, hostname: str) -> JobResult:
        """
        Configure system hostname.

        Args:
            target_root: Target root directory
            hostname: Hostname to set

        Returns:
            JobResult indicating success or failure
        """
        try:
            # Write /etc/hostname
            hostname_file = Path(target_root) / "etc" / "hostname"
            hostname_file.write_text(f"{hostname}\n")

            # Update /etc/hosts
            hosts_file = Path(target_root) / "etc" / "hosts"
            hosts_content = f"""# Generated by Xen installer
127.0.0.1   localhost
::1         localhost
127.0.1.1   {hostname}.localdomain {hostname}
"""
            hosts_file.write_text(hosts_content)

            logger.info(f"Hostname set to '{hostname}'")
            return JobResult.ok(msg("Hostname set to '{hostname}'", hostname=hostname))

        except OSError as e:
            logger.error(f"Failed to set hostname: {e}")
            return JobResult.fail(
                msg("Failed to set hostname: {error}", error=e),
                error_code=24,
            )

    def _validate_username(self, username: str) -> bool:
        """
        Validate username format.

        Rules:
        - Must start with lowercase letter or underscore
        - Can contain lowercase letters, digits, underscores, hyphens
        - Typical Linux username restrictions

        Args:
            username: Username to validate

        Returns:
            True if valid, False otherwise
        """
        if not username or len(username) > 32:
            return False
        return bool(self.USERNAME_PATTERN.match(username))

    def _validate_hostname(self, hostname: str) -> bool:
        """
        Validate hostname format.

        Rules:
        - Can contain lowercase letters, digits, hyphens
        - Cannot start or end with hyphen
        - Maximum 63 characters per label

        Args:
            hostname: Hostname to validate

        Returns:
            True if valid, False otherwise
        """
        if not hostname or len(hostname) > 253:
            return False

        if hostname == "localhost":
            return False

        # Check each label (parts separated by dots)
        labels = hostname.split(".")
        for label in labels:
            if not label or len(label) > 63:
                return False
            if label.startswith("-") or label.endswith("-"):
                return False
            if not re.match(r"^[a-z0-9-]+$", label):
                return False

        return True

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Returns:
            Estimated duration (user creation is fast)
        """
        return 10

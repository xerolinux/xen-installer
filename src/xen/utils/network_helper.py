"""Network helper utilities for Xen installer.

Provides desktop environment detection and native network configurator launching.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from enum import Enum
from typing import ClassVar

logger = logging.getLogger(__name__)


class DesktopEnvironment(Enum):
    """Supported desktop environments."""

    GNOME = "gnome"
    KDE = "kde"
    XFCE = "xfce"
    CINNAMON = "cinnamon"
    MATE = "mate"
    LXDE = "lxde"
    LXQT = "lxqt"
    UNKNOWN = "unknown"


class NetworkHelper:
    """Helper class for network-related operations.

    Detects the current desktop environment and launches the appropriate
    native network configuration tool.
    """

    # Mapping of DE identifiers to DesktopEnvironment enum
    DE_IDENTIFIERS: ClassVar[dict[str, DesktopEnvironment]] = {
        # GNOME variants
        "gnome": DesktopEnvironment.GNOME,
        "gnome-shell": DesktopEnvironment.GNOME,
        "gnome-classic": DesktopEnvironment.GNOME,
        "gnome-xorg": DesktopEnvironment.GNOME,
        "ubuntu": DesktopEnvironment.GNOME,
        "ubuntu:gnome": DesktopEnvironment.GNOME,
        "pop": DesktopEnvironment.GNOME,
        "zorin": DesktopEnvironment.GNOME,
        "endless": DesktopEnvironment.GNOME,
        # KDE variants
        "kde": DesktopEnvironment.KDE,
        "kde-plasma": DesktopEnvironment.KDE,
        "plasma": DesktopEnvironment.KDE,
        "kde5": DesktopEnvironment.KDE,
        "kde6": DesktopEnvironment.KDE,
        "neon": DesktopEnvironment.KDE,
        # XFCE
        "xfce": DesktopEnvironment.XFCE,
        "xfce4": DesktopEnvironment.XFCE,
        "xubuntu": DesktopEnvironment.XFCE,
        # Cinnamon
        "cinnamon": DesktopEnvironment.CINNAMON,
        "x-cinnamon": DesktopEnvironment.CINNAMON,
        "linuxmint": DesktopEnvironment.CINNAMON,
        # MATE
        "mate": DesktopEnvironment.MATE,
        "ubuntu-mate": DesktopEnvironment.MATE,
        # LXDE
        "lxde": DesktopEnvironment.LXDE,
        "lubuntu": DesktopEnvironment.LXDE,
        # LXQt
        "lxqt": DesktopEnvironment.LXQT,
    }

    # Network settings commands per desktop environment
    # Each entry is a list of command arguments
    NETWORK_COMMANDS: ClassVar[dict[DesktopEnvironment, list[str]]] = {
        DesktopEnvironment.GNOME: ["gnome-control-center", "wifi"],
        DesktopEnvironment.KDE: ["systemsettings", "kcm_networkmanagement"],
        DesktopEnvironment.XFCE: ["xfce4-settings-manager", "network"],
        DesktopEnvironment.CINNAMON: ["cinnamon-settings", "network"],
        DesktopEnvironment.MATE: ["mate-network-properties"],
        DesktopEnvironment.LXDE: ["lxappearance", "--prefs"],
        DesktopEnvironment.LXQT: ["lxqt-config", "network"],
    }

    # Fallback command if DE-specific command not available
    FALLBACK_COMMAND: ClassVar[list[str]] = ["nm-connection-editor"]

    @classmethod
    def detect_desktop_environment(cls) -> DesktopEnvironment:
        """Detect the current desktop environment.

        Checks XDG_CURRENT_DESKTOP and DESKTOP_SESSION environment variables.

        Returns:
            DesktopEnvironment enum value for the detected DE.
        """
        # Check XDG_CURRENT_DESKTOP first (more reliable)
        xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if xdg_desktop:
            # XDG_CURRENT_DESKTOP can contain multiple values separated by ':'
            for part in xdg_desktop.split(":"):
                part = part.strip()
                if part in cls.DE_IDENTIFIERS:
                    de = cls.DE_IDENTIFIERS[part]
                    logger.debug(f"Detected DE from XDG_CURRENT_DESKTOP: {de.value}")
                    return de

        # Fallback to DESKTOP_SESSION
        desktop_session = os.environ.get("DESKTOP_SESSION", "").lower()
        if desktop_session and desktop_session in cls.DE_IDENTIFIERS:
            de = cls.DE_IDENTIFIERS[desktop_session]
            logger.debug(f"Detected DE from DESKTOP_SESSION: {de.value}")
            return de

        # Check XDG_SESSION_DESKTOP as last resort
        xdg_session = os.environ.get("XDG_SESSION_DESKTOP", "").lower()
        if xdg_session and xdg_session in cls.DE_IDENTIFIERS:
            de = cls.DE_IDENTIFIERS[xdg_session]
            logger.debug(f"Detected DE from XDG_SESSION_DESKTOP: {de.value}")
            return de

        logger.debug("Could not detect desktop environment, using UNKNOWN")
        return DesktopEnvironment.UNKNOWN

    @classmethod
    def get_network_settings_command(cls, de: DesktopEnvironment | None = None) -> list[str] | None:
        """Get the network settings command for the given desktop environment.

        Args:
            de: Desktop environment. If None, auto-detect.

        Returns:
            List of command arguments, or None if no suitable command found.
        """
        if de is None:
            de = cls.detect_desktop_environment()

        # Try DE-specific command first
        if de in cls.NETWORK_COMMANDS:
            cmd = cls.NETWORK_COMMANDS[de]
            # Check if the command exists
            if shutil.which(cmd[0]):
                logger.debug(f"Using DE-specific command: {cmd}")
                return cmd
            logger.debug(f"DE command {cmd[0]} not found, trying fallback")

        # Try fallback command
        if shutil.which(cls.FALLBACK_COMMAND[0]):
            logger.debug(f"Using fallback command: {cls.FALLBACK_COMMAND}")
            return cls.FALLBACK_COMMAND

        logger.warning("No network settings command available")
        return None

    @classmethod
    def launch_network_settings(cls) -> tuple[bool, str]:
        """Launch the native network configuration tool.

        Detects the desktop environment and launches the appropriate
        network settings application.

        Returns:
            Tuple of (success: bool, message: str).
            - On success: (True, "Launched <command>")
            - On failure: (False, "<error message>")
        """
        de = cls.detect_desktop_environment()
        cmd = cls.get_network_settings_command(de)

        if cmd is None:
            msg = "No network configuration tool available"
            logger.error(msg)
            return (False, msg)

        try:
            # Launch the command in the background (non-blocking)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info(f"Launched network settings: {' '.join(cmd)} (PID: {process.pid})")
            return (True, f"Launched {cmd[0]}")

        except FileNotFoundError:
            msg = f"Command not found: {cmd[0]}"
            logger.error(msg)
            return (False, msg)

        except PermissionError:
            msg = f"Permission denied: {cmd[0]}"
            logger.error(msg)
            return (False, msg)

        except OSError as e:
            msg = f"Failed to launch network settings: {e}"
            logger.error(msg)
            return (False, msg)

    @classmethod
    def check_internet_connectivity(cls, timeout: float = 2.0) -> bool:
        """Check if internet connectivity is available.

        Attempts to connect to well-known hosts to verify connectivity.

        Args:
            timeout: Connection timeout in seconds.

        Returns:
            True if internet is available, False otherwise.
        """
        import socket

        # List of hosts to try (in order of preference)
        test_hosts = [
            ("1.1.1.1", 53),  # Cloudflare DNS
            ("8.8.8.8", 53),  # Google DNS
            ("9.9.9.9", 53),  # Quad9 DNS
        ]

        for host, port in test_hosts:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    pass
                logger.debug(f"Internet connectivity confirmed via {host}:{port}")
                return True
            except (OSError, TimeoutError):
                continue

        logger.debug("No internet connectivity detected")
        return False

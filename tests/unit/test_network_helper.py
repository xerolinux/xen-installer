"""Unit tests for NetworkHelper utility class."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from xen.utils.network_helper import DesktopEnvironment, NetworkHelper


class TestDesktopEnvironmentDetection:
    """Tests for detect_desktop_environment method."""

    def test_detect_gnome_from_xdg_current_desktop(self) -> None:
        """Test detection of GNOME from XDG_CURRENT_DESKTOP."""
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.GNOME

    def test_detect_kde_from_xdg_current_desktop(self) -> None:
        """Test detection of KDE from XDG_CURRENT_DESKTOP."""
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE"}, clear=True):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.KDE

    def test_detect_gnome_ubuntu_variant(self) -> None:
        """Test detection of GNOME from Ubuntu:GNOME format."""
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, clear=True):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.GNOME

    def test_detect_plasma_variant(self) -> None:
        """Test detection of KDE from plasma identifier."""
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "plasma"}, clear=True):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.KDE

    def test_detect_xfce(self) -> None:
        """Test detection of XFCE."""
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}, clear=True):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.XFCE

    def test_detect_cinnamon(self) -> None:
        """Test detection of Cinnamon."""
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "X-Cinnamon"}, clear=True):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.CINNAMON

    def test_fallback_to_desktop_session(self) -> None:
        """Test fallback to DESKTOP_SESSION when XDG_CURRENT_DESKTOP is empty."""
        with patch.dict(
            os.environ, {"XDG_CURRENT_DESKTOP": "", "DESKTOP_SESSION": "gnome"}, clear=True
        ):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.GNOME

    def test_fallback_to_xdg_session_desktop(self) -> None:
        """Test fallback to XDG_SESSION_DESKTOP."""
        with patch.dict(
            os.environ,
            {
                "XDG_CURRENT_DESKTOP": "",
                "DESKTOP_SESSION": "",
                "XDG_SESSION_DESKTOP": "kde",
            },
            clear=True,
        ):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.KDE

    def test_unknown_desktop_environment(self) -> None:
        """Test unknown desktop environment returns UNKNOWN."""
        with patch.dict(
            os.environ,
            {
                "XDG_CURRENT_DESKTOP": "some-unknown-de",
                "DESKTOP_SESSION": "unknown",
                "XDG_SESSION_DESKTOP": "unknown",
            },
            clear=True,
        ):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.UNKNOWN

    def test_empty_environment_returns_unknown(self) -> None:
        """Test empty environment variables return UNKNOWN."""
        with patch.dict(os.environ, {}, clear=True):
            result = NetworkHelper.detect_desktop_environment()
            assert result == DesktopEnvironment.UNKNOWN


class TestGetNetworkSettingsCommand:
    """Tests for get_network_settings_command method."""

    def test_gnome_command_when_available(self) -> None:
        """Test GNOME network settings command when available."""
        with patch("shutil.which", return_value="/usr/bin/gnome-control-center"):
            result = NetworkHelper.get_network_settings_command(DesktopEnvironment.GNOME)
            assert result == ["gnome-control-center", "wifi"]

    def test_kde_command_when_available(self) -> None:
        """Test KDE network settings command when available."""
        with patch("shutil.which", return_value="/usr/bin/systemsettings"):
            result = NetworkHelper.get_network_settings_command(DesktopEnvironment.KDE)
            assert result == ["systemsettings", "kcm_networkmanagement"]

    def test_fallback_to_nm_connection_editor(self) -> None:
        """Test fallback to nm-connection-editor when DE command not found."""

        def mock_which(cmd: str) -> str | None:
            if cmd == "nm-connection-editor":
                return "/usr/bin/nm-connection-editor"
            return None

        with patch("shutil.which", side_effect=mock_which):
            result = NetworkHelper.get_network_settings_command(DesktopEnvironment.GNOME)
            assert result == ["nm-connection-editor"]

    def test_no_command_available(self) -> None:
        """Test returns None when no network command is available."""
        with patch("shutil.which", return_value=None):
            result = NetworkHelper.get_network_settings_command(DesktopEnvironment.GNOME)
            assert result is None

    def test_auto_detect_desktop_when_none_provided(self) -> None:
        """Test auto-detection when DE is not provided."""
        with (
            patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True),
            patch("shutil.which", return_value="/usr/bin/gnome-control-center"),
        ):
            result = NetworkHelper.get_network_settings_command()
            assert result == ["gnome-control-center", "wifi"]


class TestLaunchNetworkSettings:
    """Tests for launch_network_settings method."""

    def test_successful_launch(self) -> None:
        """Test successful launch of network settings."""
        mock_process = MagicMock()
        mock_process.pid = 12345

        with (
            patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True),
            patch("shutil.which", return_value="/usr/bin/gnome-control-center"),
            patch("subprocess.Popen", return_value=mock_process) as mock_popen,
        ):
            success, message = NetworkHelper.launch_network_settings()

            assert success is True
            assert "gnome-control-center" in message
            mock_popen.assert_called_once()

    def test_no_command_available_error(self) -> None:
        """Test error when no network command is available."""
        with (
            patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True),
            patch("shutil.which", return_value=None),
        ):
            success, message = NetworkHelper.launch_network_settings()

            assert success is False
            assert "No network configuration tool" in message

    def test_file_not_found_error(self) -> None:
        """Test error handling when command is not found at runtime."""
        with (
            patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True),
            patch("shutil.which", return_value="/usr/bin/gnome-control-center"),
            patch("subprocess.Popen", side_effect=FileNotFoundError()),
        ):
            success, message = NetworkHelper.launch_network_settings()

            assert success is False
            assert "not found" in message

    def test_permission_error(self) -> None:
        """Test error handling when permission is denied."""
        with (
            patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True),
            patch("shutil.which", return_value="/usr/bin/gnome-control-center"),
            patch("subprocess.Popen", side_effect=PermissionError()),
        ):
            success, message = NetworkHelper.launch_network_settings()

            assert success is False
            assert "Permission denied" in message

    def test_os_error_handling(self) -> None:
        """Test generic OS error handling."""
        with (
            patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True),
            patch("shutil.which", return_value="/usr/bin/gnome-control-center"),
            patch("subprocess.Popen", side_effect=OSError("Some error")),
        ):
            success, message = NetworkHelper.launch_network_settings()

            assert success is False
            assert "Failed to launch" in message


class TestCheckInternetConnectivity:
    """Tests for check_internet_connectivity method."""

    def _create_socket_mock(self, connect_side_effect: object = None) -> MagicMock:
        """Create a properly configured socket mock for context manager usage."""
        mock_socket_instance = MagicMock()
        if connect_side_effect is not None:
            mock_socket_instance.connect.side_effect = connect_side_effect
        # Configure context manager to return the mock instance
        mock_socket_instance.__enter__ = MagicMock(return_value=mock_socket_instance)
        mock_socket_instance.__exit__ = MagicMock(return_value=False)
        return mock_socket_instance

    def test_internet_available(self) -> None:
        """Test returns True when internet is available."""
        mock_socket = self._create_socket_mock()
        with patch("socket.socket", return_value=mock_socket):
            result = NetworkHelper.check_internet_connectivity(timeout=1.0)
            assert result is True
            mock_socket.connect.assert_called_once()

    def test_no_internet_connection(self) -> None:
        """Test returns False when no internet connection."""
        # All three hosts fail with OSError
        mock_socket = self._create_socket_mock(connect_side_effect=OSError("No route to host"))
        with patch("socket.socket", return_value=mock_socket):
            result = NetworkHelper.check_internet_connectivity(timeout=1.0)
            assert result is False

    def test_timeout_error(self) -> None:
        """Test returns False on timeout."""
        mock_socket = self._create_socket_mock(connect_side_effect=TimeoutError())
        with patch("socket.socket", return_value=mock_socket):
            result = NetworkHelper.check_internet_connectivity(timeout=1.0)
            assert result is False

    def test_tries_multiple_hosts(self) -> None:
        """Test that multiple hosts are tried before giving up."""
        mock_socket = self._create_socket_mock(
            # First two hosts fail, third succeeds
            connect_side_effect=[OSError(), OSError(), None]
        )
        with patch("socket.socket", return_value=mock_socket):
            result = NetworkHelper.check_internet_connectivity(timeout=1.0)
            assert result is True
            assert mock_socket.connect.call_count == 3


class TestDesktopEnvironmentEnum:
    """Tests for DesktopEnvironment enum values."""

    def test_all_desktop_environments_defined(self) -> None:
        """Test that all expected desktop environments are defined."""
        expected = ["GNOME", "KDE", "XFCE", "CINNAMON", "MATE", "LXDE", "LXQT", "UNKNOWN"]
        actual = [de.name for de in DesktopEnvironment]
        assert set(expected) == set(actual)

    def test_enum_values(self) -> None:
        """Test enum values are lowercase strings."""
        assert DesktopEnvironment.GNOME.value == "gnome"
        assert DesktopEnvironment.KDE.value == "kde"
        assert DesktopEnvironment.XFCE.value == "xfce"
        assert DesktopEnvironment.UNKNOWN.value == "unknown"

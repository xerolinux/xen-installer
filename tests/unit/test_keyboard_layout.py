"""Unit tests for the live keyboard layout application helper.

Covers `xen.utils.keyboard_layout.apply_keyboard_layout_live`: gsettings on
GNOME Wayland/X11, setxkbmap fallback, and best-effort failure handling.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from xen.utils.keyboard_layout import (
    apply_keyboard_layout_live,
    build_xkb_layout_string,
)


class TestBuildXkbLayoutString:
    def test_layout_only(self) -> None:
        assert build_xkb_layout_string("fr", "") == "fr"

    def test_layout_with_variant(self) -> None:
        assert build_xkb_layout_string("fr", "azerty") == "fr+azerty"


class TestApplyViaGsettings:
    def test_wayland_session_uses_gsettings(self) -> None:
        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", return_value="/usr/bin/gsettings"),
            patch("xen.utils.keyboard_layout.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = apply_keyboard_layout_live("fr", "azerty")

        assert result is True
        assert mock_run.call_count == 2
        sources_call = mock_run.call_args_list[0].args[0]
        assert sources_call == [
            "gsettings",
            "set",
            "org.gnome.desktop.input-sources",
            "sources",
            "[('xkb', 'fr+azerty')]",
        ]
        current_call = mock_run.call_args_list[1].args[0]
        assert current_call == [
            "gsettings",
            "set",
            "org.gnome.desktop.input-sources",
            "current",
            "0",
        ]

    def test_root_wraps_gsettings_in_user_session(self) -> None:
        pw = MagicMock(pw_name="nixos")
        with (
            patch.dict(
                "os.environ",
                {"XDG_SESSION_TYPE": "wayland", "XDG_RUNTIME_DIR": "/run/user/1000"},
                clear=True,
            ),
            patch("xen.utils.keyboard_layout.os.geteuid", return_value=0),
            patch("xen.utils.keyboard_layout.pwd.getpwuid", return_value=pw),
            patch("xen.utils.keyboard_layout.shutil.which", return_value="/run/wrappers/bin/su"),
            patch("xen.utils.keyboard_layout.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = apply_keyboard_layout_live("de", "")

        assert result is True
        sources_call = mock_run.call_args_list[0].args[0]
        assert sources_call[:5] == ["runuser", "-u", "nixos", "--", "env"]
        assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in sources_call
        assert sources_call[-5:] == [
            "gsettings",
            "set",
            "org.gnome.desktop.input-sources",
            "sources",
            "[('xkb', 'de')]",
        ]

    def test_layout_without_variant_omits_plus(self) -> None:
        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", return_value="/usr/bin/gsettings"),
            patch("xen.utils.keyboard_layout.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            apply_keyboard_layout_live("us")

        sources_call = mock_run.call_args_list[0].args[0]
        assert sources_call[-1] == "[('xkb', 'us')]"

    def test_gsettings_failure_is_best_effort(self) -> None:
        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", return_value="/usr/bin/gsettings"),
            patch(
                "xen.utils.keyboard_layout.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "gsettings"),
            ),
        ):
            result = apply_keyboard_layout_live("fr")

        assert result is False


class TestApplyViaSetxkbmapFallback:
    def test_x11_session_uses_setxkbmap(self) -> None:
        def which_side_effect(cmd: str) -> str | None:
            return "/usr/bin/setxkbmap" if cmd == "setxkbmap" else None

        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", side_effect=which_side_effect),
            patch("xen.utils.keyboard_layout.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = apply_keyboard_layout_live("de", "nodeadkeys")

        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert cmd == ["setxkbmap", "de", "-variant", "nodeadkeys"]

    def test_x11_without_variant_skips_variant_flag(self) -> None:
        def which_side_effect(cmd: str) -> str | None:
            return "/usr/bin/setxkbmap" if cmd == "setxkbmap" else None

        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", side_effect=which_side_effect),
            patch("xen.utils.keyboard_layout.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            apply_keyboard_layout_live("us")

        cmd = mock_run.call_args.args[0]
        assert cmd == ["setxkbmap", "us"]

    def test_wayland_without_gsettings_falls_back_to_setxkbmap(self) -> None:
        def which_side_effect(cmd: str) -> str | None:
            return "/usr/bin/setxkbmap" if cmd == "setxkbmap" else None

        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", side_effect=which_side_effect),
            patch("xen.utils.keyboard_layout.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = apply_keyboard_layout_live("fr", "azerty")

        assert result is True
        cmd = mock_run.call_args.args[0]
        assert cmd == ["setxkbmap", "fr", "-variant", "azerty"]


class TestApplyKeyboardLayoutNoTooling:
    def test_returns_false_when_nothing_available(self) -> None:
        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", return_value=None),
        ):
            result = apply_keyboard_layout_live("fr")

        assert result is False

    def test_empty_layout_returns_false(self) -> None:
        assert apply_keyboard_layout_live("") is False

    def test_setxkbmap_failure_is_best_effort(self) -> None:
        def which_side_effect(cmd: str) -> str | None:
            return "/usr/bin/setxkbmap" if cmd == "setxkbmap" else None

        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=True),
            patch("xen.utils.keyboard_layout.shutil.which", side_effect=which_side_effect),
            patch(
                "xen.utils.keyboard_layout.subprocess.run",
                side_effect=OSError("command not found"),
            ),
        ):
            result = apply_keyboard_layout_live("fr")

        assert result is False

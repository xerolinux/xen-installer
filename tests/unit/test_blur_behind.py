"""
Unit tests for enable_blur_behind.

Covers:
- No-op (returns False) on any platform other than xcb (X11/XWayland).
- No-op (returns False) when libX11 can't be loaded.
- No-op (returns False) when XOpenDisplay fails.
- On xcb with a working libX11, sets the _KDE_NET_WM_BLUR_BEHIND_REGION
  property with a zero-length CARDINAL value and returns True.

Runs offscreen; libX11 calls are mocked, no real X server is contacted.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from xen.gui.blur_behind import enable_blur_behind  # noqa: E402


def _fake_window(win_id: int = 12345) -> MagicMock:
    window = MagicMock()
    window.winId.return_value = win_id
    return window


@patch("xen.gui.blur_behind.QGuiApplication")
def test_noop_on_non_xcb_platform(mock_qapp: MagicMock) -> None:
    mock_qapp.platformName.return_value = "wayland"
    assert enable_blur_behind(_fake_window()) is False


@patch("xen.gui.blur_behind.ctypes.CDLL", side_effect=OSError("not found"))
@patch("xen.gui.blur_behind.QGuiApplication")
def test_noop_when_libx11_missing(mock_qapp: MagicMock, _mock_cdll: MagicMock) -> None:
    mock_qapp.platformName.return_value = "xcb"
    assert enable_blur_behind(_fake_window()) is False


@patch("xen.gui.blur_behind.ctypes.CDLL")
@patch("xen.gui.blur_behind.QGuiApplication")
def test_noop_when_display_open_fails(mock_qapp: MagicMock, mock_cdll: MagicMock) -> None:
    mock_qapp.platformName.return_value = "xcb"
    mock_libx11 = MagicMock()
    mock_libx11.XOpenDisplay.return_value = None
    mock_cdll.return_value = mock_libx11
    assert enable_blur_behind(_fake_window()) is False


@patch("xen.gui.blur_behind.ctypes.CDLL")
@patch("xen.gui.blur_behind.QGuiApplication")
def test_sets_blur_property_on_xcb(mock_qapp: MagicMock, mock_cdll: MagicMock) -> None:
    mock_qapp.platformName.return_value = "xcb"
    mock_libx11 = MagicMock()
    mock_libx11.XOpenDisplay.return_value = 0xDEADBEEF
    mock_libx11.XInternAtom.return_value = 999
    mock_cdll.return_value = mock_libx11

    window = _fake_window(win_id=555)
    assert enable_blur_behind(window) is True

    mock_libx11.XInternAtom.assert_called_once_with(
        0xDEADBEEF, b"_KDE_NET_WM_BLUR_BEHIND_REGION", 0
    )
    mock_libx11.XChangeProperty.assert_called_once_with(
        0xDEADBEEF, 555, 999, 6, 32, 0, None, 0
    )
    mock_libx11.XFlush.assert_called_once_with(0xDEADBEEF)
    mock_libx11.XCloseDisplay.assert_called_once_with(0xDEADBEEF)

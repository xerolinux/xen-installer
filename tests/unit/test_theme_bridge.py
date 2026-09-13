"""
Unit tests for SystemThemeBridge.

Covers:
- ``available`` reflects whether a QGuiApplication instance exists.
- Palette-derived properties return valid hex colors sourced from the
  active QGuiApplication palette.
- ``paletteChanged`` on the application re-emits ``paletteUpdated``.

Runs offscreen; no real display required.
"""

from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QGuiApplication, QPalette  # noqa: E402

from xen.gui.theme_bridge import SystemThemeBridge  # noqa: E402

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@pytest.fixture
def qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def test_available_true_with_running_application(qapp: QGuiApplication) -> None:
    assert qapp is not None
    bridge = SystemThemeBridge()
    assert bridge.available is True


def test_palette_properties_return_hex_colors(qapp: QGuiApplication) -> None:
    assert qapp is not None
    bridge = SystemThemeBridge()
    for value in (
        bridge.windowColor,
        bridge.windowTextColor,
        bridge.baseColor,
        bridge.buttonColor,
        bridge.accentColor,
        bridge.disabledTextColor,
    ):
        assert _HEX_COLOR_RE.match(value), f"Expected hex color, got {value!r}"


def test_palette_change_emits_signal(qapp: QGuiApplication) -> None:
    bridge = SystemThemeBridge()
    received = []
    bridge.paletteUpdated.connect(lambda: received.append(True))

    new_palette = QPalette(qapp.palette())
    new_palette.setColor(QPalette.ColorRole.Window, "#123456")
    qapp.setPalette(new_palette)

    assert received == [True]
    assert bridge.windowColor.lower() == "#123456"

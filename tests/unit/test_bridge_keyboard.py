"""Unit tests for EngineBridge keyboard layout wiring.

Covers:
- `applyKeyboardLayout` delegates to the live-apply util (mocked here; the
  util itself is exercised in test_keyboard_layout.py).
- `setSelectedKeymap` / `setSelectedKeyboardVariant` keep driving the install
  config (selections dict) exactly as before; the live-apply affordance is
  additive, not a replacement.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CONFIG = PROJECT_ROOT / "config" / "examples" / "minimal.yaml"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def bridge(qapp: QApplication) -> EngineBridge:
    assert qapp is not None
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    return EngineBridge(
        engine, MINIMAL_CONFIG.parent, debug=False, dry_run=True, skip_requirements=True
    )


@pytest.fixture
def live_bridge(qapp: QApplication) -> EngineBridge:
    assert qapp is not None
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    return EngineBridge(
        engine, MINIMAL_CONFIG.parent, debug=False, dry_run=False, skip_requirements=True
    )


class TestApplyKeyboardLayoutSlot:
    def test_delegates_to_live_apply_util(self, live_bridge: EngineBridge) -> None:
        with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
            result = live_bridge.applyKeyboardLayout("fr", "azerty")

        mocked.assert_called_once_with("fr", "azerty")
        assert result is True

    def test_dry_run_leaves_the_desktop_layout_alone(self, bridge: EngineBridge) -> None:
        with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
            result = bridge.applyKeyboardLayout("fr", "azerty")

        mocked.assert_not_called()
        assert result is False

    def test_propagates_best_effort_failure(self, live_bridge: EngineBridge) -> None:
        with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=False):
            result = live_bridge.applyKeyboardLayout("xx", "")

        assert result is False

    def test_never_touches_install_selections(self, bridge: EngineBridge) -> None:
        """Live-apply is cosmetic only: it must not mutate the install config."""
        bridge.setSelectedKeymap("us")
        before = dict(bridge.selections)

        with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True):
            bridge.applyKeyboardLayout("fr", "azerty")

        assert bridge.selections == before


class TestKeymapSelectionStillDrivesInstallConfig:
    """Regression guard: the live-apply addition must not break the existing
    install-config wiring (setSelectedKeymap/setSelectedKeyboardVariant)."""

    def test_set_selected_keymap_reflects(self, bridge: EngineBridge) -> None:
        bridge.setSelectedKeymap("de")
        assert bridge.selectedKeymap == "de"
        assert bridge.selections["keymap"] == "de"

    def test_set_selected_keyboard_variant_reflects(self, bridge: EngineBridge) -> None:
        bridge.setSelectedKeymap("fr")
        bridge.setSelectedKeyboardVariant("azerty")
        assert bridge.selectedKeyboardVariant == "azerty"
        assert bridge.selections["keyboardVariant"] == "azerty"

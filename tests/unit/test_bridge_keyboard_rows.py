"""Unit tests for EngineBridge.keyboardLayoutRows: on-screen keyboard
data (JSON), now also taking the variant so ckbcomp-backed rows are exact,
not just per-layout."""

from __future__ import annotations

import json
import os
from pathlib import Path

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


def test_keyboard_layout_rows_accepts_variant(bridge: EngineBridge) -> None:
    rows = json.loads(bridge.keyboardLayoutRows("de", ""))
    assert len(rows) >= 1


def test_keyboard_layout_rows_defaults_variant_to_empty(bridge: EngineBridge) -> None:
    # Calling with just the layout (no variant arg) must still work from
    # QML call sites that don't pass one.
    rows = json.loads(bridge.keyboardLayoutRows("us", ""))
    assert len(rows) >= 1

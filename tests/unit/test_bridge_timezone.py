"""Unit tests for EngineBridge.timezoneForCoordinate/coordinateForTimezone:
the offline timezone lookups exposed to the interactive timezone map."""

from __future__ import annotations

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


def test_timezone_for_coordinate_returns_containing_zone(bridge: EngineBridge) -> None:
    assert bridge.timezoneForCoordinate(48.85, 2.35) == "Europe/Paris"


def test_timezone_for_coordinate_kentucky(bridge: EngineBridge) -> None:
    assert bridge.timezoneForCoordinate(37.08, -88.60) == "America/Chicago"


def test_timezone_for_coordinate_tokyo(bridge: EngineBridge) -> None:
    assert bridge.timezoneForCoordinate(35.68, 139.76) == "Asia/Tokyo"


def test_coordinate_for_timezone_returns_json_pair(bridge: EngineBridge) -> None:
    import json

    lat, lon = json.loads(bridge.coordinateForTimezone("Europe/Paris"))
    assert lat == pytest.approx(48.85, abs=0.1)
    assert lon == pytest.approx(2.35, abs=0.1)


def test_coordinate_for_timezone_unknown_returns_json_null(bridge: EngineBridge) -> None:
    import json

    assert json.loads(bridge.coordinateForTimezone("Not/AZone")) is None

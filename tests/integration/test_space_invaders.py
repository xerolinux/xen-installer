"""Space Invaders waves: new geeky invaders every level, scarier as it gets harder."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QUrl  # noqa: E402
from PySide6.QtQml import QJSValue, QQmlComponent, QQmlEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

GAME = Path(__file__).resolve().parents[2] / "src/xen/gui/qml/components/SpaceInvadersGame.qml"


@pytest.fixture(scope="module")
def game():
    app = QApplication.instance() or QApplication([])
    assert app is not None
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(GAME)))
    obj = component.create()
    assert obj is not None, component.errors()
    yield obj
    del obj, engine


def _plain(value):
    return value.toVariant() if isinstance(value, QJSValue) else value


def _call(obj, name: str, level: int):
    return _plain(QMetaObject.invokeMethod(obj, name, Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", level)))


def test_every_level_brings_a_new_set_of_invaders(game) -> None:
    roster = set(_plain(game.property("invaderTypes")))
    assert len(roster) >= 16
    waves = [tuple(_call(game, "waveTypes", level)) for level in range(1, 6)]
    for wave in waves:
        assert len(wave) == 4 and set(wave) <= roster
    assert len(set(waves)) == len(waves)
    assert len({t for wave in waves[:4] for t in wave}) == 16


def test_invaders_look_scarier_as_levels_rise(game) -> None:
    menace = [_call(game, "menaceFor", level) for level in range(1, 8)]
    assert menace[0] == 0
    assert menace == sorted(menace)
    assert menace[-1] > menace[2] > 0

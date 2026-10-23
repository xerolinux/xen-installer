"""
Offscreen integration test: ProgressView (install progress screen).

Loads the real Main.qml, jumps to step 6 (Progress), and drives the real
bridge progress machinery so the combined progress bar and matrix-rain
canvas actually render without QML errors.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def _make_bridge() -> EngineBridge:
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    return EngineBridge(
        engine,
        XEROLINUX_CONFIG.parent,
        debug=False,
        dry_run=True,
        skip_requirements=True,
    )


def _load(bridge: EngineBridge) -> tuple[QQmlApplicationEngine, QObject]:
    qml_engine = QQmlApplicationEngine()
    translator_proxy = TranslatorProxy(engine=qml_engine)
    qml_engine.rootContext().setContextProperty("engine", bridge)
    qml_engine.rootContext().setContextProperty("branding", bridge.branding_proxy)
    qml_engine.rootContext().setContextProperty("translator", translator_proxy)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    roots = qml_engine.rootObjects()
    assert roots, "Main.qml n'a pas pu être chargé"
    return qml_engine, roots[0]


def _collect_texts(root: QObject) -> list[str]:
    texts: list[str] = []
    stack: list[QObject] = [root]
    while stack:
        obj = stack.pop()
        for child in obj.children():
            stack.append(child)
            meta = child.metaObject()
            if meta.className().startswith("QQuickText"):
                value = child.property("text")
                if value:
                    texts.append(str(value))
    return texts


class TestProgressView:
    def test_overall_progress_percent_renders(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root = _load(bridge)
        try:
            root.setProperty("currentStep", 7)
            qapp.processEvents()

            texts = _collect_texts(root)
            assert any("%" in t for t in texts)
        finally:
            del qml_engine

    def test_current_job_message_renders(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root = _load(bridge)
        try:
            bridge._on_job_start(bridge._engine.get_job_names()[0])
            root.setProperty("currentStep", 7)
            qapp.processEvents()
            qapp.processEvents()

            texts = _collect_texts(root)
            assert any(bridge.currentJobMessage in t for t in texts)
        finally:
            del qml_engine

    def test_no_second_progress_bar_property_left_bound(self, qapp: QApplication) -> None:
        """Regression: the old redundant per-job progress bar/property is
        gone - the combined bar plus the current job's live message is the
        only progress indicator now."""
        bridge = _make_bridge()
        qml_engine, root = _load(bridge)
        try:
            root.setProperty("currentStep", 7)
            qapp.processEvents()

            assert not hasattr(bridge, "currentJobProgress")
        finally:
            del qml_engine

    def test_animations_only_run_while_shown_and_game_closed(self, qapp: QApplication) -> None:
        from PySide6.QtCore import QMetaObject
        from PySide6.QtQuick import QQuickItem

        def find(obj: QObject, name: str) -> QObject | None:
            stack, seen, refs = [obj], set(), []
            while stack:
                o = stack.pop()
                if id(o) in seen:
                    continue
                seen.add(id(o))
                refs.append(o)
                if o.objectName() == name:
                    return o
                stack.extend(o.children())
                if isinstance(o, QQuickItem):
                    stack.extend(o.childItems())
            return None

        bridge = _make_bridge()
        qml_engine, root = _load(bridge)
        try:
            qapp.processEvents()
            view = find(root, "progressView")
            assert view.property("animating") is False
            root.setProperty("currentStep", 7)
            qapp.processEvents()
            assert view.property("animating") is True
            game = find(view, "gamePopup")
            QMetaObject.invokeMethod(game, "open")
            qapp.processEvents()
            assert view.property("animating") is False
            QMetaObject.invokeMethod(game, "close")
            qapp.processEvents()
        finally:
            del qml_engine

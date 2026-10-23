"""
Offscreen integration tests: OnScreenKeyboard.qml.

Covers:
- The component loads with zero QML errors for a representative layout
  from every XML layout group (plus one unmapped layout, which must fall
  back to empty.xml rather than erroring).
- flashKeyForChar() highlights the on-screen key matching a typed
  character.

Runs offscreen; no real keyboard layout is applied to any session.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import (  # noqa: E402
    QMetaObject,
    Q_ARG,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
COMPONENTS_DIR = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "components"

_HARNESS_QML = """
import QtQuick
import QtQuick.Controls
import "file:{components_dir}"

ApplicationWindow {{
    width: 820
    height: 320
    visible: true
    color: "#1a1a1a"

    TextField {{ id: tf; objectName: "testField"; width: parent.width }}

    OnScreenKeyboard {{
        id: osk
        objectName: "osk"
        anchors.top: tf.bottom
        width: parent.width
        xkbLayout: "us"
        target: tf
    }}
}}
"""


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def bridge(qapp: QApplication) -> EngineBridge:
    assert qapp is not None
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    return EngineBridge(
        engine, XEROLINUX_CONFIG.parent, debug=False, dry_run=True, skip_requirements=True
    )


class _QmlMessageCollector:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def handler(self, msg_type: QtMsgType, _context: object, message: str) -> None:
        if msg_type == QtMsgType.QtWarningMsg:
            self.warnings.append(message)
        elif msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            self.errors.append(message)
        # QML's own console.error/console.warn route through QtWarningMsg;
        # a genuine JS TypeError/ReferenceError string is what we actually
        # care about failing the test on, not every qWarning.
        low = message.lower()
        if "typeerror" in low or "referenceerror" in low:
            self.errors.append(message)


def _load_harness(bridge: EngineBridge) -> tuple[QQmlApplicationEngine, Any, _QmlMessageCollector]:
    collector = _QmlMessageCollector()
    qInstallMessageHandler(collector.handler)

    qml_text = _HARNESS_QML.format(components_dir=str(COMPONENTS_DIR))
    harness_fd, harness_name = tempfile.mkstemp(suffix=".qml")
    harness_path = Path(harness_name)
    with os.fdopen(harness_fd, "w") as f:
        f.write(qml_text)

    qml_engine = QQmlApplicationEngine()
    qml_engine.rootContext().setContextProperty("engine", bridge)
    qml_engine.load(QUrl.fromLocalFile(str(harness_path)))
    harness_path.unlink(missing_ok=True)
    roots = qml_engine.rootObjects()
    assert roots, "OnScreenKeyboard harness failed to load"
    return qml_engine, roots[0], collector


def _walk(root: Any):
    """Walk both the QObject ownership tree and the QQuickItem visual
    tree (Repeater-generated delegates, like every OnScreenKey here, are
    only reachable via childItems() - see
    feedback-qtquick-gotchas / test_partition_view_manual.py's own
    _find_by_object_name for why the dedup guard is required)."""
    stack: list[Any] = [root]
    visited_ids: set[int] = set()
    visited_refs: list[Any] = []
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in visited_ids:
            continue
        visited_ids.add(oid)
        visited_refs.append(obj)
        yield obj
        for child in obj.children():
            stack.append(child)
        if isinstance(obj, QQuickItem):
            for child in obj.childItems():
                stack.append(child)


def _find_by_object_name(root: Any, name: str) -> Any | None:
    for obj in _walk(root):
        if obj.objectName() == name:
            return obj
    return None


@pytest.mark.parametrize(
    "xkb_layout", ["us", "de", "fr", "ru", "ara", "fa", "se", "pl", "jp"]
)
def test_onscreen_keyboard_loads_for_every_group_with_no_qml_errors(
    xkb_layout: str, bridge: EngineBridge, qapp: QApplication
) -> None:
    qml_engine, root, collector = _load_harness(bridge)
    try:
        osk = _find_by_object_name(root, "osk")
        assert osk is not None
        osk.setProperty("xkbLayout", xkb_layout)
        qapp.processEvents()

        rows = osk.property("_rows")
        rows = rows.toVariant() if hasattr(rows, "toVariant") else rows
        assert len(rows) >= 1, f"{xkb_layout}: no rows rendered"

        assert not collector.errors, f"QML errors for {xkb_layout}: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_flash_key_for_char_highlights_matching_key(bridge: EngineBridge, qapp: QApplication) -> None:
    qml_engine, root, collector = _load_harness(bridge)
    try:
        osk = _find_by_object_name(root, "osk")
        assert osk is not None
        osk.setProperty("xkbLayout", "us")
        qapp.processEvents()

        QMetaObject.invokeMethod(osk, "flashKeyForChar", Q_ARG("QVariant", "a"))
        qapp.processEvents()

        all_keys = osk.property("allKeys")
        all_keys = all_keys.toVariant() if hasattr(all_keys, "toVariant") else all_keys
        assert len(all_keys) > 0

        flashed_labels = [
            k.property("mainLabel")
            for k in _walk(osk)
            if k.property("flashed") is True
        ]
        assert "a" in flashed_labels, f"expected key 'a' to be flashed, got {flashed_labels}"

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_alternate_symbol_is_typed_into_target(bridge: EngineBridge, qapp: QApplication) -> None:
    qml_engine, root, collector = _load_harness(bridge)
    try:
        osk = _find_by_object_name(root, "osk")
        field = _find_by_object_name(root, "testField")
        qapp.processEvents()

        key = next(k for k in _walk(osk) if k.property("mainLabel") == "a")
        assert QMetaObject.invokeMethod(key, "alternatesClicked", Q_ARG(str, "á"))
        qapp.processEvents()

        assert field.property("text") == "á"
        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine

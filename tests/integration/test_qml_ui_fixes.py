"""
Offscreen regression tests for QML UI fixes:

- PackageChooserView package checkboxes reflect engine.packageSelections,
  so a choice survives switching to another group and back.
- SearchableComboBox never overwrites its parent's currentValue binding
  when the user picks an item.
- Return/Escape typed into the keyboard test field stay in the field
  instead of triggering Main.qml's wizard Next/Back shortcuts.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QMetaObject, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
COMPONENTS_DIR = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "components"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"


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


def _walk(root: Any):
    stack: list[Any] = [root]
    seen: set[int] = set()
    keep: list[Any] = []
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        keep.append(obj)
        yield obj
        stack.extend(obj.children())
        if isinstance(obj, QQuickItem):
            stack.extend(obj.childItems())


def _find(root: Any, name: str) -> Any | None:
    for obj in _walk(root):
        if obj.objectName() == name:
            return obj
    return None


def _load_harness(bridge: EngineBridge, body: str) -> tuple[QQmlApplicationEngine, Any]:
    qml = (
        "import QtQuick\nimport QtQuick.Controls\n"
        f'import "file:{COMPONENTS_DIR}"\n'
        "ApplicationWindow {\n width: 900\n height: 640\n visible: true\n" + body + "\n}\n"
    )
    fd, name = tempfile.mkstemp(suffix=".qml")
    path = Path(name)
    with os.fdopen(fd, "w") as f:
        f.write(qml)
    qml_engine = QQmlApplicationEngine()
    qml_engine.rootContext().setContextProperty("engine", bridge)
    qml_engine.rootContext().setContextProperty("branding", bridge.branding_proxy)
    qml_engine.load(QUrl.fromLocalFile(str(path)))
    path.unlink(missing_ok=True)
    roots = qml_engine.rootObjects()
    assert roots, "harness failed to load"
    return qml_engine, roots[0]


def _click_item(window: Any, item: Any, qapp: QApplication) -> None:
    centre = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(centre.x(), centre.y()))
    qapp.processEvents()


def _settle(qapp: QApplication, window: Any) -> None:
    window.requestActivate()
    for _ in range(5):
        qapp.processEvents()
    QTest.qWaitForWindowExposed(window)


class TestPackageChooserSelections:
    def test_package_checkbox_follows_engine_and_survives_group_switch(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        qml_engine, window = _load_harness(
            bridge,
            'PackageChooserView { id: view; objectName: "view"; anchors.fill: parent;'
            " groups: engine.packageGroupsModel }",
        )
        try:
            _settle(qapp, window)
            view = _find(window, "view")
            view.setProperty("activeGroupId", "internet-browsers")
            qapp.processEvents()

            firefox = _find(window, "pkgCheck_firefox")
            assert firefox is not None
            assert firefox.property("checked") is False

            bridge.setPackageSelected("internet-browsers", "firefox", True)
            qapp.processEvents()
            assert firefox.property("checked") is True

            view.setProperty("activeGroupId", "audio-tools")
            qapp.processEvents()
            view.setProperty("activeGroupId", "internet-browsers")
            qapp.processEvents()

            firefox = _find(window, "pkgCheck_firefox")
            assert firefox.property("checked") is True

            QTest.qWait(100)
            floorp_row = _find(window, "pkgRow_floorp")
            assert floorp_row is not None
            _click_item(window, floorp_row, qapp)
            assert bridge.packageSelections.get("internet-browsers:floorp") is True
            assert _find(window, "pkgCheck_floorp").property("checked") is True
        finally:
            del qml_engine


class TestSearchableComboBinding:
    def test_pick_keeps_parent_binding_alive(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        qml_engine, window = _load_harness(
            bridge,
            'property string chosen: "alpha"\n'
            'property string lastPicked: ""\n'
            'SearchableComboBox { id: combo; objectName: "combo"; x: 20; y: 20;'
            ' width: 300; height: 40; model: ["alpha", "beta", "gamma"];'
            " currentValue: chosen; onValueSelected: function(v) { lastPicked = v } }",
        )
        try:
            _settle(qapp, window)
            combo = _find(window, "combo")
            assert combo.property("currentValue") == "alpha"

            _click_item(window, combo, qapp)
            for ch in "beta":
                QTest.keyClick(window, ch)
            QTest.keyClick(window, Qt.Key_Return)
            qapp.processEvents()

            assert window.property("lastPicked") == "beta"
            assert combo.property("currentValue") == "alpha"

            window.setProperty("chosen", "gamma")
            qapp.processEvents()
            assert combo.property("currentValue") == "gamma"
        finally:
            del qml_engine


class TestKeyboardTestFieldKeys:
    def test_return_and_escape_in_test_field_do_not_navigate(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        qml_engine = QQmlApplicationEngine()
        translator_proxy = TranslatorProxy(engine=qml_engine)
        qml_engine.rootContext().setContextProperty("engine", bridge)
        qml_engine.rootContext().setContextProperty("branding", bridge.branding_proxy)
        qml_engine.rootContext().setContextProperty("translator", translator_proxy)
        qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
        roots = qml_engine.rootObjects()
        assert roots
        window = roots[0]
        try:
            window.setProperty("currentStep", 2)
            _settle(qapp, window)

            field = _find(window, "keyboardTestField")
            assert field is not None
            QMetaObject.invokeMethod(field, "forceActiveFocus")
            qapp.processEvents()
            assert field.property("activeFocus") is True

            QTest.keyClick(window, Qt.Key_Return)
            qapp.processEvents()
            assert window.property("currentStep") == 2

            QTest.keyClick(window, Qt.Key_Enter)
            qapp.processEvents()
            assert window.property("currentStep") == 2

            QTest.keyClick(window, Qt.Key_Escape)
            qapp.processEvents()
            assert window.property("currentStep") == 2
        finally:
            del qml_engine

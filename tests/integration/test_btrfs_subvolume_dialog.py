"""Btrfs subvolume summary line and BtrfsSubvolumeDialog on the Partition step (offscreen)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import (  # noqa: E402
    QMetaObject,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtQml import QQmlApplicationEngine, qmlEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.mock_disks import mock_disks  # noqa: E402
from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        QQuickStyle.setStyle("Fusion")
        app = QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def ui(qapp: QApplication) -> Iterator[tuple[Any, EngineBridge, list[str]]]:
    messages: list[str] = []

    def handler(msg_type: QtMsgType, _context: object, message: str) -> None:
        if msg_type != QtMsgType.QtDebugMsg:
            messages.append(message)

    qInstallMessageHandler(handler)
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    bridge = EngineBridge(
        engine, XEROLINUX_CONFIG.parent, debug=False, dry_run=True, skip_requirements=True
    )
    bridge._on_disk_scan_finished(mock_disks())
    bridge._apply_internet_status(True)
    bridge.setSelectedDisk("nvme1n1")
    bridge.setFilesystem("btrfs")
    qml_engine = QQmlApplicationEngine()
    translator = TranslatorProxy(engine=qml_engine)
    ctx = qml_engine.rootContext()
    ctx.setContextProperty("engine", bridge)
    ctx.setContextProperty("branding", bridge.branding_proxy)
    ctx.setContextProperty("translator", translator)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    roots = qml_engine.rootObjects()
    assert roots, "Main.qml failed to load"
    window = roots[0]
    window.setWidth(1582)
    window.setHeight(870)
    window.setProperty("currentStep", 5)
    _settle(qapp)
    try:
        yield window, bridge, messages
    finally:
        qInstallMessageHandler(None)
        del translator, qml_engine


def _settle(qapp: QApplication, seconds: float = 0.2) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()


def _walk(item: QQuickItem) -> Iterator[QQuickItem]:
    yield item
    for child in item.childItems():
        yield from _walk(child)


def _visible(window: Any, name: str) -> QQuickItem:
    found = [it for it in _walk(window.contentItem()) if it.objectName() == name and it.isVisible()]
    assert found, f"no visible {name}"
    return found[0]


def _visible_names(window: Any, prefix: str) -> list[QQuickItem]:
    return [
        it
        for it in _walk(window.contentItem())
        if it.objectName().startswith(prefix) and it.isVisible()
    ]


def _click(qapp: QApplication, window: Any, item: QQuickItem) -> None:
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(p.x()), round(p.y())))
    _settle(qapp)


def _dialog(window: Any) -> QObject:
    dialog = window.findChild(QObject, "btrfsSubvolumeDialog")
    assert dialog is not None
    return dialog


def _open(qapp: QApplication, window: Any) -> QObject:
    _click(qapp, window, _visible(window, "subvolumesEditButton"))
    dialog = _dialog(window)
    assert dialog.property("visible") is True
    return dialog


def _rows(window: Any) -> list[tuple[str, str]]:
    def by_index(prefix: str) -> list[QQuickItem]:
        items = _visible_names(window, prefix)
        return sorted(items, key=lambda it: int(it.objectName().split("_")[1]))

    names, mounts = by_index("subvolumeName_"), by_index("subvolumeMount_")
    return [(n.property("text"), m.property("text")) for n, m in zip(names, mounts, strict=True)]


def _qml_problems(messages: list[str]) -> list[str]:
    return [m for m in messages if "BtrfsSubvolumeDialog" in m or "PartitionView" in m]


DEFAULT_ROWS = [
    ("@", "/"),
    ("@home", "/home"),
    ("@root", "/root"),
    ("@srv", "/srv"),
    ("@cache", "/var/cache"),
    ("@tmp", "/var/tmp"),
    ("@log", "/var/log"),
    ("@swap", "/swap"),
]


def test_defaults_label(qapp, ui) -> None:
    window, bridge, messages = ui
    for mode in ("auto", "alongside"):
        bridge.setPartitionMode(mode)
        _settle(qapp)
        assert _visible(window, "subvolumesSummary").property("text") == "Default (8)"
        assert _visible(window, "subvolumesEditButton")

    custom = [{"name": "@", "mountpoint": "/"}, {"name": "@data", "mountpoint": "/data"}]
    assert bridge.setBtrfsSubvolumes(json.dumps(custom))
    _settle(qapp)
    assert _visible(window, "subvolumesSummary").property("text") == "Custom (2)"

    bridge.setFilesystem("ext4")
    _settle(qapp)
    assert not _visible_names(window, "subvolumesSummary")
    assert not _visible_names(window, "subvolumesEditButton")
    assert not _qml_problems(messages), _qml_problems(messages)


def test_edit_and_save_updates_engine(qapp, ui) -> None:
    window, bridge, messages = ui
    dialog = _open(qapp, window)
    assert _rows(window) == DEFAULT_ROWS

    _click(qapp, window, _visible(window, "subvolumeMount_1"))
    QTest.keyClick(window, Qt.Key_End)
    QTest.keyClick(window, Qt.Key_2)
    _settle(qapp)
    assert _visible(window, "subvolumeMount_1").property("text") == "/home2"
    assert json.loads(bridge.btrfsSubvolumes)[1]["mountpoint"] == "/home"

    _click(qapp, window, _visible(window, "subvolumesSaveButton"))
    assert dialog.property("visible") is False
    assert json.loads(bridge.btrfsSubvolumes)[1] == {"name": "@home", "mountpoint": "/home2"}
    assert _visible(window, "subvolumesSummary").property("text") == "Custom (8)"

    _open(qapp, window)
    _click(qapp, window, _visible(window, "subvolumeRemove_7"))
    _click(qapp, window, _visible(window, "subvolumesCancelButton"))
    assert len(json.loads(bridge.btrfsSubvolumes)) == 8
    assert not _qml_problems(messages), _qml_problems(messages)


def test_cannot_remove_root_subvolume(qapp, ui) -> None:
    window, _bridge, _messages = ui
    _open(qapp, window)
    removable = {it.objectName() for it in _visible_names(window, "subvolumeRemove_")}
    assert "subvolumeRemove_0" not in removable
    assert removable == {f"subvolumeRemove_{i}" for i in range(1, 8)}

    _click(qapp, window, _visible(window, "subvolumeRemove_1"))
    assert _rows(window) == [DEFAULT_ROWS[0], *DEFAULT_ROWS[2:]]


def test_invalid_disables_save(qapp, ui) -> None:
    window, bridge, _messages = ui
    _open(qapp, window)
    save = _visible(window, "subvolumesSaveButton")
    assert save.property("enabled") is True
    assert not _visible_names(window, "subvolumesError")

    _click(qapp, window, _visible(window, "subvolumesAddButton"))
    assert len(_rows(window)) == 9
    assert save.property("enabled") is False
    assert not _visible_names(window, "subvolumesError")

    _visible(window, "subvolumeName_8").setProperty("text", "@data")
    _settle(qapp)
    assert save.property("enabled") is False
    assert _visible(window, "subvolumesError").property("text") == bridge.validateBtrfsSubvolumes(
        json.dumps([{"name": n, "mountpoint": m} for n, m in [*DEFAULT_ROWS, ("@data", "")]])
    )

    _visible(window, "subvolumeMount_8").setProperty("text", "/home")
    _settle(qapp)
    assert save.property("enabled") is False
    assert "Duplicate subvolume mountpoint" in _visible(window, "subvolumesError").property("text")

    _visible(window, "subvolumeMount_8").setProperty("text", "/data")
    _settle(qapp)
    assert save.property("enabled") is True
    assert not _visible_names(window, "subvolumesError")

    _click(qapp, window, save)
    assert json.loads(bridge.btrfsSubvolumes)[-1] == {"name": "@data", "mountpoint": "/data"}


def test_restore_defaults(qapp, ui) -> None:
    window, bridge, _messages = ui
    custom = [{"name": "@", "mountpoint": "/"}, {"name": "@data", "mountpoint": "/data"}]
    assert bridge.setBtrfsSubvolumes(json.dumps(custom))
    _settle(qapp)
    _open(qapp, window)
    assert _rows(window) == [("@", "/"), ("@data", "/data")]

    _click(qapp, window, _visible(window, "subvolumesRestoreButton"))
    assert _rows(window) == DEFAULT_ROWS
    _click(qapp, window, _visible(window, "subvolumesSaveButton"))
    assert json.loads(bridge.btrfsSubvolumes) == json.loads(bridge.defaultBtrfsSubvolumes)
    assert _visible(window, "subvolumesSummary").property("text") == "Default (8)"


def test_blank_row_error_shows_once_save_is_attempted(qapp, ui) -> None:
    window, _bridge, _messages = ui
    dialog = _open(qapp, window)
    _click(qapp, window, _visible(window, "subvolumesAddButton"))
    assert not _visible_names(window, "subvolumesError")

    QMetaObject.invokeMethod(dialog, "save")
    _settle(qapp)
    assert dialog.property("visible") is True
    assert "Invalid subvolume name" in _visible(window, "subvolumesError").property("text")


def test_failed_commit_keeps_dialog_open(qapp, ui) -> None:
    window, bridge, _messages = ui
    dialog = _open(qapp, window)
    dialog.setProperty("commit", qmlEngine(dialog).evaluate("(function(json) { return false })"))
    _click(qapp, window, _visible(window, "subvolumeRemove_7"))
    _click(qapp, window, _visible(window, "subvolumesSaveButton"))
    assert dialog.property("visible") is True
    assert len(json.loads(bridge.btrfsSubvolumes)) == 8


def test_root_row_follows_identity_not_text(qapp, ui) -> None:
    window, _bridge, _messages = ui
    _open(qapp, window)
    _click(qapp, window, _visible(window, "subvolumesAddButton"))
    _visible(window, "subvolumeName_8").setProperty("text", "@")
    _visible(window, "subvolumeMount_8").setProperty("text", "/")
    _visible(window, "subvolumeMount_0").setProperty("text", "/old")
    _settle(qapp)
    removable = {it.objectName() for it in _visible_names(window, "subvolumeRemove_")}
    assert "subvolumeRemove_8" in removable
    assert "subvolumeRemove_0" not in removable


def test_added_row_scrolls_into_view(qapp, ui) -> None:
    window, _bridge, _messages = ui
    window.setWidth(1024)
    window.setHeight(640)
    _settle(qapp)
    _open(qapp, window)
    for _ in range(12):
        _click(qapp, window, _visible(window, "subvolumesAddButton"))
    last = _visible(window, "subvolumeName_19")
    scroll = next(
        it for it in _walk(window.contentItem())
        if it.inherits("QQuickFlickable") and it.isVisible() and _is_ancestor(it, last)
    )
    top = scroll.mapToScene(QPointF(0, 0)).y()
    y = last.mapToScene(QPointF(0, last.height())).y()
    assert scroll.property("contentHeight") > scroll.height()
    assert top <= y <= top + scroll.height() + 1


def _is_ancestor(ancestor: QQuickItem, item: QQuickItem) -> bool:
    while item is not None:
        if item == ancestor:
            return True
        item = item.parentItem()
    return False

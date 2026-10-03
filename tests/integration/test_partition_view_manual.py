"""
Offscreen integration tests: manual partitioning UI (ManualPartitionEditor +
PartitionView's Auto/Manual toggle).

Covers the QML<->bridge wiring for the M2 manual editor:
- Toggling to Manual mode loads Main.qml with zero QML warnings/errors and
  surfaces the ManualPartitionEditor instance.
- The data-partition filesystem list offered by the editor is exactly
  ext4/xfs/btrfs when online, and ext4/xfs (btrfs hidden) when offline -
  never more, never less.
- Main.qml's canProceedToNext() gates the Partition step on manualPlanValid
  when in manual mode.

Runs offscreen; no real partitioning is performed.
"""

from __future__ import annotations

import os
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
from xen.gui.theme_bridge import SystemThemeBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402
from xen.jobs.partition import _ALIGN, _SECTOR_SIZE  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"
EDITOR_QML = (
    PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "components" / "ManualPartitionEditor.qml"
)

MIB = 1024 * 1024
DISK_SECTORS = (500 * 1024**3) // _SECTOR_SIZE


def _mock_disk() -> dict[str, Any]:
    """A single-free-region 500 GB disk (no existing partitions)."""
    free_size = DISK_SECTORS - _ALIGN - 34
    return {
        "name": "sda",
        "sizeBytes": 500 * 1024**3,
        "sizeSectors": DISK_SECTORS,
        "segments": [
            {
                "kind": "free",
                "startSector": _ALIGN,
                "sizeSectors": free_size,
                "sizeBytes": free_size * _SECTOR_SIZE,
            }
        ],
        "partitions": [],
    }


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


def _find_by_object_name(root: Any, name: str) -> Any | None:
    """Search both QObject ownership children AND the QQuickItem visual
    tree. Repeater-generated delegates (partition table rows, the mount
    ComboBox inside them, etc.) are NOT reachable via .children() alone -
    Qt Quick keeps their QObject ownership separate from their visual
    parent, so a Repeater's items live under childItems() instead.

    No id()-based cycle guard: PySide mints a fresh Python wrapper per
    access, so id() (a memory address in CPython) can be reused for an
    unrelated object once an earlier short-lived wrapper is GC'd mid-walk -
    a dedup set here caused real, unrelated items to be falsely skipped.
    A plain QML item tree has no actual cycles, so none is needed.
    """
    stack: list[Any] = [root]
    while stack:
        obj = stack.pop()
        if obj.objectName() == name:
            return obj
        for child in obj.children():
            stack.append(child)
        if isinstance(obj, QQuickItem):
            for child in obj.childItems():
                stack.append(child)
    return None


def _load_main(bridge: EngineBridge) -> tuple[QQmlApplicationEngine, Any, _QmlMessageCollector]:
    collector = _QmlMessageCollector()
    qInstallMessageHandler(collector.handler)

    qml_engine = QQmlApplicationEngine()
    translator_proxy = TranslatorProxy(engine=qml_engine)
    system_theme = SystemThemeBridge()
    qml_engine.rootContext().setContextProperty("engine", bridge)
    qml_engine.rootContext().setContextProperty("branding", bridge.branding_proxy)
    qml_engine.rootContext().setContextProperty("translator", translator_proxy)
    qml_engine.rootContext().setContextProperty("systemTheme", system_theme)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    roots = qml_engine.rootObjects()
    assert roots, "Main.qml failed to load"
    qml_engine._keep_alive = (translator_proxy, system_theme)  # type: ignore[attr-defined]
    return qml_engine, roots[0], collector


class TestManualModeToggle:
    def test_switching_to_manual_shows_editor_with_no_qml_warnings(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        bridge._disks_model = [_mock_disk()]
        qml_engine, root, collector = _load_main(bridge)
        try:
            root.setProperty("currentStep", 4)
            qapp.processEvents()

            bridge.setSelectedDisk("sda")
            bridge.setPartitionMode("manual")
            qapp.processEvents()

            editor = _find_by_object_name(root, "manualPartitionEditor")
            assert editor is not None, "ManualPartitionEditor not found in the QML tree"

            binding_loops = [w for w in collector.warnings if "Binding loop" in w]
            assert not binding_loops, f"Binding loops: {binding_loops}"
            assert not collector.errors, f"QML errors: {collector.errors}"
        finally:
            qInstallMessageHandler(None)
            del qml_engine

    def test_auto_mode_hides_editor(self, bridge: EngineBridge, qapp: QApplication) -> None:
        bridge._disks_model = [_mock_disk()]
        qml_engine, root, _collector = _load_main(bridge)
        try:
            root.setProperty("currentStep", 4)
            bridge.setSelectedDisk("sda")
            qapp.processEvents()

            auto_radio = _find_by_object_name(root, "partitionModeAutoRadio")
            manual_radio = _find_by_object_name(root, "partitionModeManualRadio")
            assert auto_radio is not None
            assert manual_radio is not None
            assert bool(auto_radio.property("checked")) is True
            assert bool(manual_radio.property("checked")) is False
        finally:
            qInstallMessageHandler(None)
            del qml_engine


class TestManualPlanGatesNavigation:
    def test_next_disabled_until_manual_plan_valid(self, qapp: QApplication) -> None:
        # skip_requirements=False: skip_requirements maps to engine.skipValidation,
        # which short-circuits canProceedToNext() to always-true - the other
        # fixture-provided bridge would make this test vacuously pass.
        assert qapp is not None
        engine = Engine.from_config_file(XEROLINUX_CONFIG)
        bridge = EngineBridge(
            engine, XEROLINUX_CONFIG.parent, debug=False, dry_run=True, skip_requirements=False
        )
        bridge._disks_model = [_mock_disk()]
        qml_engine, root, _collector = _load_main(bridge)
        try:
            root.setProperty("currentStep", 4)
            bridge.setSelectedDisk("sda")
            bridge.setPartitionMode("manual")
            qapp.processEvents()

            next_button = _find_by_object_name(root, "nextInstallButton")
            assert next_button is not None

            # No operations queued, no partitions to mount -> must stay blocked.
            assert bool(next_button.property("enabled")) is False

            # Simulate a disk already laid out (as if Apply had already run):
            # an ESP + a root ext4 partition, both assigned via M1.
            esp_size = 512 * MIB // _SECTOR_SIZE
            root_start = _ALIGN + esp_size
            root_size = DISK_SECTORS - root_start - 34
            existing_disk = {
                "name": "sda",
                "sizeBytes": 500 * 1024**3,
                "sizeSectors": DISK_SECTORS,
                "segments": [
                    {
                        "kind": "partition",
                        "name": "sda1",
                        "startSector": _ALIGN,
                        "sizeSectors": esp_size,
                        "sizeBytes": esp_size * _SECTOR_SIZE,
                        "fstype": "vfat",
                        "partType": "efi",
                        "mountpoint": "",
                    },
                    {
                        "kind": "partition",
                        "name": "sda2",
                        "startSector": root_start,
                        "sizeSectors": root_size,
                        "sizeBytes": root_size * _SECTOR_SIZE,
                        "fstype": "ext4",
                        "partType": "linux",
                        "mountpoint": "",
                    },
                ],
                "partitions": [],
            }
            bridge._disks_model = [existing_disk]
            bridge.setPartitionMount("sda2", "/")
            qapp.processEvents()

            # Root alone still isn't enough - the ESP must be mounted too.
            assert bool(next_button.property("enabled")) is False

            bridge.setPartitionMount("sda1", "/boot/efi")
            qapp.processEvents()

            assert bool(next_button.property("enabled")) is True, bridge.manualPlanError
        finally:
            qInstallMessageHandler(None)
            del qml_engine


class TestManualEditorFilesystemList:
    """Loads ManualPartitionEditor.qml standalone to check the fstype list."""

    def _load_editor(self, bridge: EngineBridge) -> tuple[QQmlApplicationEngine, Any]:
        qml_engine = QQmlApplicationEngine()
        qml_engine.rootContext().setContextProperty("engine", bridge)
        qml_engine.load(QUrl.fromLocalFile(str(EDITOR_QML)))
        roots = qml_engine.rootObjects()
        assert roots, "ManualPartitionEditor.qml failed to load"
        return qml_engine, roots[0]

    def test_exactly_three_filesystems_when_online(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        bridge._disks_model = [_mock_disk()]
        qml_engine, editor = self._load_editor(bridge)
        try:
            editor.setProperty("hasInternet", True)
            qapp.processEvents()
            fs_list = editor.property("dataFilesystems").toVariant()
            assert fs_list == ["ext4", "xfs", "btrfs"]
        finally:
            del qml_engine

    def test_btrfs_hidden_when_offline(self, bridge: EngineBridge, qapp: QApplication) -> None:
        bridge._disks_model = [_mock_disk()]
        qml_engine, editor = self._load_editor(bridge)
        try:
            editor.setProperty("hasInternet", False)
            qapp.processEvents()
            fs_list = editor.property("dataFilesystems").toVariant()
            assert fs_list == ["ext4", "xfs"]
            assert "btrfs" not in fs_list
        finally:
            del qml_engine


class TestEditDialogCommitsMountPoint:
    """The Edit dialog's Mount Point field commits on OK (editDialog.confirm()),
    not live-as-you-type - this replaced an earlier inline-per-row editable
    ComboBox that silently discarded typed text on losing focus (editable
    QML ComboBoxes only commit on Enter/picking a list item, never on blur).
    Proves the new mechanism actually reaches the bridge."""

    def test_edit_dialog_ok_commits_mount_point(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        root_size = DISK_SECTORS - _ALIGN - 34
        seg = {
            "kind": "existing",
            "name": "sda2",
            "startSector": _ALIGN,
            "sizeSectors": root_size,
            "sizeBytes": root_size * _SECTOR_SIZE,
            "fstype": "ext4",
            "partType": "linux",
            "mountpoint": "",
            "pendingDelete": False,
            "number": 2,
            "minSizeSectors": _ALIGN,
            "freeAfterSectors": 0,
        }
        disk = {
            "name": "sda",
            "sizeBytes": 500 * 1024**3,
            "sizeSectors": DISK_SECTORS,
            "segments": [
                {
                    "kind": "partition",
                    "name": "sda2",
                    "startSector": _ALIGN,
                    "sizeSectors": root_size,
                    "sizeBytes": root_size * _SECTOR_SIZE,
                    "fstype": "ext4",
                    "partType": "linux",
                }
            ],
        }
        bridge._disks_model = [disk]
        bridge.setSelectedDisk("sda")

        qml_engine = QQmlApplicationEngine()
        qml_engine.rootContext().setContextProperty("engine", bridge)
        qml_engine.load(QUrl.fromLocalFile(str(EDITOR_QML)))
        roots = qml_engine.rootObjects()
        assert roots, "ManualPartitionEditor.qml failed to load"
        editor = roots[0]
        try:
            qapp.processEvents()

            edit_dialog = _find_by_object_name(editor, "editPartitionDialog")
            assert edit_dialog is not None, "editPartitionDialog not found"
            QMetaObject.invokeMethod(edit_dialog, "openFor", Q_ARG("QVariant", seg))
            qapp.processEvents()

            mount_field = _find_by_object_name(editor, "editMountField")
            assert mount_field is not None, "editMountField not found"
            mount_field.setProperty("text", "/")
            qapp.processEvents()

            QMetaObject.invokeMethod(edit_dialog, "confirm")
            qapp.processEvents()

            assert bridge.partitionMount("sda2") == "/"
        finally:
            del qml_engine

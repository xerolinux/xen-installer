"""Manual partitioning page: Calamares layout, Create/Edit dialog, guide and checklist (offscreen)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import (  # noqa: E402
    Q_ARG,
    QMetaObject,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.mock_disks import finalize_mock_disk, mock_disks  # noqa: E402
from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402
from xen.utils import disk_detector  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"

MIB = 1024**2
GIB = 1024**3
GUIDE_TITLES = [
    "Use the whole disk, my own layout",
    "Install in free space next to another OS",
    "Shrink a partition to make room",
    "Replace an old Linux install",
    "Keep my existing /home",
    "Add swap",
    "Which file system should I pick?",
]


def _mixed_disk() -> dict[str, Any]:
    disk = finalize_mock_disk(
        {
            "name": "sdb",
            "model": "Test",
            "size": "400 GB",
            "sizeBytes": 400 * GIB,
            "type": "SSD",
            "removable": False,
            "partitions": [
                {
                    "name": "sdb1",
                    "sizeBytes": 300 * MIB,
                    "fstype": "vfat",
                    "partType": "efi",
                    "partTypeGuid": disk_detector.ESP_TYPE_GUID,
                },
                {
                    "name": "sdb2",
                    "sizeBytes": 150 * GIB,
                    "fstype": "ntfs",
                    "partType": "windows",
                    "label": "Windows",
                    "osName": "Windows 11",
                    "minSizeBytes": 40 * GIB,
                },
                {"name": "gap", "sizeBytes": 50 * GIB, "fstype": "", "partType": "linux"},
                {"name": "sdb4", "sizeBytes": 60 * GIB, "fstype": "xfs", "partType": "linux"},
                {"name": "sdb5", "sizeBytes": 100 * GIB, "fstype": "ext4", "partType": "linux"},
                {"name": "sdb6", "sizeBytes": 30 * GIB, "fstype": "crypto_LUKS", "partType": "linux"},
            ],
        }
    )
    disk["partitions"] = [p for p in disk["partitions"] if p["name"] != "gap"]
    disk["segments"] = disk_detector.compute_segments(disk["sizeSectors"], disk["partitions"])
    return disk


def _sliver_disk() -> dict[str, Any]:
    disk = finalize_mock_disk(
        {
            "name": "sdd",
            "model": "Sliver",
            "size": "20 GB",
            "sizeBytes": 20 * GIB,
            "type": "SSD",
            "removable": False,
            "partitions": [
                {
                    "name": "sdd1",
                    "sizeBytes": 300 * MIB + 512,
                    "fstype": "vfat",
                    "partType": "efi",
                    "partTypeGuid": disk_detector.ESP_TYPE_GUID,
                },
                {"name": "gap", "sizeBytes": 2049 * 512, "fstype": "", "partType": "linux"},
                {"name": "sdd3", "sizeBytes": 19 * GIB, "fstype": "ext4", "partType": "linux"},
            ],
        }
    )
    disk["partitions"] = [p for p in disk["partitions"] if p["name"] != "gap"]
    disk["segments"] = disk_detector.compute_segments(disk["sizeSectors"], disk["partitions"])
    return disk


def _disk_4kn() -> dict[str, Any]:
    sectors = 100 * GIB // 4096
    return {
        "name": "sdc",
        "model": "4Kn",
        "size": "100 GB",
        "sizeBytes": 100 * GIB,
        "type": "SSD",
        "removable": False,
        "sectorSize": 4096,
        "sizeSectors": sectors,
        "ptType": "gpt",
        "partitions": [],
        "segments": disk_detector.compute_segments(sectors, [], 4096),
    }


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        QQuickStyle.setStyle("Fusion")
        app = QApplication([])
    return app  # type: ignore[return-value]


def _make_ui(qapp: QApplication, skip_requirements: bool) -> Iterator[tuple[Any, EngineBridge, list[str]]]:
    messages: list[str] = []

    def handler(msg_type: QtMsgType, _context: object, message: str) -> None:
        if msg_type != QtMsgType.QtDebugMsg:
            messages.append(message)

    qInstallMessageHandler(handler)
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    bridge = EngineBridge(
        engine,
        XEROLINUX_CONFIG.parent,
        debug=False,
        dry_run=True,
        skip_requirements=skip_requirements,
    )
    bridge._on_disk_scan_finished(mock_disks() + [_mixed_disk(), _disk_4kn(), _sliver_disk()])
    bridge._apply_internet_status(True)
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


@pytest.fixture
def ui(qapp: QApplication) -> Iterator[tuple[Any, EngineBridge, list[str]]]:
    yield from _make_ui(qapp, skip_requirements=True)


@pytest.fixture
def gated_ui(qapp: QApplication) -> Iterator[tuple[Any, EngineBridge, list[str]]]:
    yield from _make_ui(qapp, skip_requirements=False)


def _settle(qapp: QApplication, seconds: float = 0.15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()


def _walk(item: QQuickItem) -> Iterator[QQuickItem]:
    yield item
    for child in item.childItems():
        yield from _walk(child)


def _find(window: Any, name: str) -> QQuickItem:
    found = [it for it in _walk(window.contentItem()) if it.objectName() == name]
    assert found, f"no {name}"
    visible = [it for it in found if it.isVisible()]
    return (visible or found)[0]


def _visible(window: Any, name: str) -> QQuickItem:
    item = _find(window, name)
    assert item.isVisible(), f"{name} is hidden"
    return item


def _is_shown(window: Any, name: str) -> bool:
    return any(it.isVisible() for it in _walk(window.contentItem()) if it.objectName() == name)


def _click(qapp: QApplication, window: Any, item: QQuickItem, double: bool = False) -> None:
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    pos = QPoint(round(p.x()), round(p.y()))
    if double:
        QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, pos)
    else:
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)
    _settle(qapp)


def _enter_manual(qapp: QApplication, window: Any, bridge: EngineBridge, disk: str = "sdb") -> None:
    bridge.setSelectedDisk(disk)
    bridge.setPartitionMode("manual")
    _settle(qapp)
    QMetaObject.invokeMethod(window, "navigateNext")
    _settle(qapp)
    assert _find(window, "partitionView").property("manualEditorActive") is True


def _table_rows(window: Any) -> list[QQuickItem]:
    rows = [
        it
        for it in _walk(window.contentItem())
        if it.objectName().startswith("manualRow_") and it.isVisible()
    ]
    return sorted(rows, key=lambda it: int(it.objectName().split("_")[1]))


def _row(window: Any, key: str) -> QQuickItem:
    for row in _table_rows(window):
        if row.property("rowKey") == key:
            return row
    raise AssertionError(f"no table row {key}: {[r.property('rowKey') for r in _table_rows(window)]}")


def _select(qapp: QApplication, window: Any, key: str) -> None:
    _click(qapp, window, _row(window, key))


def _free_key(window: Any) -> str:
    keys = [r.property("rowKey") for r in _table_rows(window)]
    return next(k for k in keys if k.startswith("free:"))


def _dialog(window: Any) -> QObject:
    dialog = window.findChild(QObject, "manualPartitionDialog")
    assert dialog is not None
    return dialog


def _open_dialog(qapp: QApplication, window: Any, button: str) -> QObject:
    _click(qapp, window, _visible(window, button))
    dialog = _dialog(window)
    assert dialog.property("visible") is True
    return dialog


def _pick(qapp: QApplication, window: Any, combo_name: str, value: str) -> None:
    combo = _visible(window, combo_name)
    model = list(combo.property("model"))
    index = model.index(value)
    combo.setProperty("currentIndex", index)
    QMetaObject.invokeMethod(combo, "activated", Q_ARG("int", index))
    _settle(qapp)


def _set_mount(qapp: QApplication, window: Any, mount: str) -> None:
    _visible(window, "dialogMountCombo").setProperty("editText", mount)
    _settle(qapp)


def _set_size(qapp: QApplication, window: Any, mib: int) -> None:
    _visible(window, "dialogSizeSpin").setProperty("value", mib)
    _settle(qapp)


def _part(bridge: EngineBridge, disk: str, number: int) -> dict[str, Any]:
    d = next(d for d in bridge._disks_model if d["name"] == disk)
    return next(p for p in d["partitions"] if p["number"] == number)


def _clean(messages: list[str]) -> list[str]:
    return [m for m in messages if "qml" in m.lower() or "Error" in m]


def test_entering_manual_shows_page_and_back_returns(ui, qapp) -> None:
    window, bridge, messages = ui
    _enter_manual(qapp, window, bridge)
    assert _is_shown(window, "manualPartitionPage")
    assert window.property("currentStep") == 5
    QMetaObject.invokeMethod(window, "navigateBack")
    _settle(qapp)
    assert window.property("currentStep") == 5
    assert not _is_shown(window, "manualPartitionPage")
    assert not _clean(messages), messages


def test_table_lists_partitions_and_free_space(ui, qapp) -> None:
    window, bridge, messages = ui
    _enter_manual(qapp, window, bridge)
    keys = [r.property("rowKey") for r in _table_rows(window)]
    assert keys[:3] == ["1", "2", keys[2]] and keys[2].startswith("free:")
    assert keys[3:6] == ["4", "5", "6"]
    assert keys[-1].startswith("free:")
    assert not _clean(messages), messages


def test_buttons_enable_rules(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)

    def enabled(name: str) -> bool:
        return bool(_visible(window, name).property("enabled"))

    assert not enabled("manualCreateButton")
    assert not enabled("manualEditButton")
    assert not enabled("manualDeleteButton")
    assert enabled("manualNewTableButton")

    _select(qapp, window, _free_key(window))
    assert enabled("manualCreateButton")
    assert not enabled("manualEditButton")
    assert not enabled("manualDeleteButton")

    _select(qapp, window, "2")
    assert not enabled("manualCreateButton")
    assert enabled("manualEditButton")
    assert enabled("manualDeleteButton")


def test_double_click_opens_create_or_edit(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    _click(qapp, window, _row(window, _free_key(window)), double=True)
    dialog = _dialog(window)
    assert dialog.property("visible") is True
    assert dialog.property("mode") == "create"
    _click(qapp, window, _visible(window, "dialogCancelButton"))
    assert dialog.property("visible") is False

    _click(qapp, window, _row(window, "5"), double=True)
    assert dialog.property("visible") is True
    assert dialog.property("mode") == "edit"


def test_new_table_confirmation_sets_flag(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    _click(qapp, window, _visible(window, "manualNewTableButton"))
    confirm = window.findChild(QObject, "newTableConfirm")
    assert confirm.property("visible") is True
    assert _visible(window, "newTableConfirmText").property("text") == (
        "This removes every partition on this disk when you click Install."
    )
    _click(qapp, window, _visible(window, "newTableCancelButton"))
    assert confirm.property("visible") is False
    assert bridge.manualNewTable is False

    _click(qapp, window, _visible(window, "manualNewTableButton"))
    _click(qapp, window, _visible(window, "newTableConfirmButton"))
    assert bridge.manualNewTable is True
    keys = [r.property("rowKey") for r in _table_rows(window)]
    assert len(keys) == 1 and keys[0].startswith("free:")


def test_create_root_row(ui, qapp) -> None:
    window, bridge, messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    spin = _visible(window, "dialogSizeSpin")
    free = next(s for s in bridge.planPreview if s["kind"] == "free")
    assert spin.property("to") == (free["endSector"] - 2048 + 1) // 2048
    assert spin.property("value") == spin.property("to")

    _pick(qapp, window, "dialogFsCombo", "ext4")
    _set_mount(qapp, window, "/")
    _set_size(qapp, window, 40960)
    _click(qapp, window, _visible(window, "dialogOkButton"))

    assert _dialog(window).property("visible") is False
    [row] = bridge.manualRows
    assert row["kind"] == "new" and not row["ref"].isdigit()
    assert row["start"] == 2048
    assert row["end"] == 2048 + 40960 * 2048 - 1
    assert row["fstype"] == "ext4"
    assert row["mountpoint"] == "/"
    assert row["encrypt"] is False
    assert row["subvolumes"] == []
    assert not _clean(messages), messages


def test_mount_point_list_labels_root(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    combo = _visible(window, "dialogMountCombo")
    QQmlExpression(QQmlEngine.contextForObject(combo), combo, "popup.open()").evaluate()
    _settle(qapp)
    popup = window.findChild(QObject, "dialogMountPopup")
    assert popup is not None and popup.property("visible") is True
    view = next(o for o in popup.findChildren(QObject) if o.metaObject().className().startswith("QQuickListView"))
    texts = [
        it.property("text")
        for it in _walk(view)
        if it.property("text") and it.isVisible() and it.mapToItem(view, 0, 0).y() >= 0
    ]
    # A lone "/" reads as an empty row, so root carries a hint
    assert texts[:2] == ["/", "(root)"], texts
    # Every choice fits, so hovering the last one never scrolls "/" out of view
    assert view.property("contentHeight") <= view.property("height")
    assert view.property("interactive") is False
    QQmlExpression(QQmlEngine.contextForObject(combo), combo, "popup.close()").evaluate()


def test_picking_a_mount_point_row_sets_it(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    combo = _visible(window, "dialogMountCombo")
    QQmlExpression(QQmlEngine.contextForObject(combo), combo, "popup.open()").evaluate()
    _settle(qapp)
    popup = window.findChild(QObject, "dialogMountPopup")
    view = next(o for o in popup.findChildren(QObject) if o.metaObject().className().startswith("QQuickListView"))
    # The popup owns its rows: the combo's delegate model destroys rows it highlights
    assert view.property("model") == ["/", "/boot", "/boot/efi", "/home", "/opt", "/srv", "/var"]
    row = next(it for it in _walk(view) if it.objectName() == "mountChoice_3")
    _click(qapp, window, row)
    assert combo.property("editText") == "/home"
    assert _dialog(window).property("mount") == "/home"
    assert popup.property("visible") is False


def test_option_combo_rows_belong_to_the_popup(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    combo = _visible(window, "dialogFsCombo")
    QQmlExpression(QQmlEngine.contextForObject(combo), combo, "popup.open()").evaluate()
    _settle(qapp)
    popup = next(o for o in window.findChildren(QObject) if o.objectName() == "optionComboPopup" and o.property("visible"))
    view = next(o for o in popup.findChildren(QObject) if o.metaObject().className().startswith("QQuickListView"))
    choices = list(combo.property("model"))
    assert view.property("model") == choices
    target = choices.index("xfs")
    _click(qapp, window, next(it for it in _walk(view) if it.objectName() == f"optionChoice_{target}"))
    assert _dialog(window).property("fsName") == "xfs"
    assert combo.property("displayText") == "xfs"
    assert popup.property("visible") is False


def test_create_btrfs_root_gets_default_subvolumes(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    _pick(qapp, window, "dialogFsCombo", "btrfs")
    assert not _is_shown(window, "dialogSubvolumesRow")
    _set_mount(qapp, window, "/")
    assert _is_shown(window, "dialogSubvolumesRow")
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [row] = bridge.manualRows
    names = [sv["name"] for sv in row["subvolumes"]]
    assert names[0] == "@" and "@home" in names and "@snapshots" in names


def test_create_esp_defaults_to_2000_mib(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    _pick(qapp, window, "dialogFsCombo", "fat32")
    assert not _is_shown(window, "dialogEncryptCheck")
    _set_mount(qapp, window, "/boot/efi")
    assert _visible(window, "dialogSizeSpin").property("value") == 2000
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [row] = bridge.manualRows
    assert row["end"] - row["start"] + 1 == 2000 * 2048
    assert row["fstype"] == "fat32" and row["mountpoint"] == "/boot/efi"


def test_create_swap_hides_mount_point(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    _set_mount(qapp, window, "/home")
    _pick(qapp, window, "dialogFsCombo", "linuxswap")
    assert not _is_shown(window, "dialogMountCombo")
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [row] = bridge.manualRows
    assert row["fstype"] == "linuxswap" and row["mountpoint"] == ""


def test_btrfs_offered_only_online(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    model = list(_visible(window, "dialogFsCombo").property("model"))
    assert model == ["btrfs", "ext4", "xfs", "fat32", "linuxswap"]
    bridge._apply_internet_status(False)
    _settle(qapp)
    model = list(_visible(window, "dialogFsCombo").property("model"))
    assert model == ["ext4", "xfs", "fat32", "linuxswap"]


def test_encrypted_root_asks_for_passphrase(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    _pick(qapp, window, "dialogFsCombo", "ext4")
    _visible(window, "dialogEncryptCheck").setProperty("checked", True)
    _settle(qapp)
    assert not _is_shown(window, "dialogPassField")
    _set_mount(qapp, window, "/")
    ok = _visible(window, "dialogOkButton")
    assert ok.property("enabled") is False
    _visible(window, "dialogPassField").setProperty("text", "correct horse")
    _visible(window, "dialogPassConfirmField").setProperty("text", "correct horse")
    _settle(qapp)
    assert ok.property("enabled") is True
    _click(qapp, window, ok)
    [row] = bridge.manualRows
    assert row["encrypt"] is True
    assert bridge._manual_root_passphrase == "correct horse"


def test_create_on_4kn_disk_uses_logical_sectors(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="sdc")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    _set_size(qapp, window, 512)
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [row] = bridge.manualRows
    assert row["start"] == 256
    assert row["end"] == 256 + 512 * 256 - 1


def test_edit_shrink_bounds_and_xfs_disabled(ui, qapp) -> None:
    window, bridge, messages = ui
    _enter_manual(qapp, window, bridge)
    _select(qapp, window, "2")
    _open_dialog(qapp, window, "manualEditButton")
    spin = _visible(window, "dialogSizeSpin")
    assert spin.property("enabled") is True
    # the preflight shrinks the file system to the partition size minus an 8 MiB margin
    assert spin.property("from") == 40 * 1024 + 8
    assert spin.property("to") == 200 * 1024
    assert spin.property("value") == 150 * 1024
    _set_size(qapp, window, 100 * 1024)
    _click(qapp, window, _visible(window, "dialogOkButton"))
    ntfs = _part(bridge, "sdb", 2)
    [row] = bridge.manualRows
    assert row["kind"] == "existing" and row["number"] == 2 and row["action"] == "keep"
    assert row["newEnd"] == ntfs["startSector"] + 100 * 1024 * 2048 - 1

    _select(qapp, window, "4")
    _open_dialog(qapp, window, "manualEditButton")
    assert _visible(window, "dialogSizeSpin").property("enabled") is False
    assert "XFS can't be shrunk" in _visible(window, "dialogResizeReason").property("text")
    _click(qapp, window, _visible(window, "dialogOkButton"))
    xfs_row = next(r for r in bridge.manualRows if r["number"] == 4)
    assert xfs_row.get("newEnd") is None
    assert not _clean(messages), messages


def test_edit_format_sets_fs_and_warns(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    _select(qapp, window, "5")
    _open_dialog(qapp, window, "manualEditButton")
    fs = _visible(window, "dialogFsCombo")
    assert fs.property("enabled") is False
    assert not _is_shown(window, "dialogFormatWarning")
    _click(qapp, window, _visible(window, "dialogFormatRadio"))
    assert fs.property("enabled") is True
    assert _is_shown(window, "dialogFormatWarning")
    assert _visible(window, "dialogSizeSpin").property("enabled") is False
    _pick(qapp, window, "dialogFsCombo", "xfs")
    _set_mount(qapp, window, "/home")
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [row] = bridge.manualRows
    assert row["action"] == "format" and row["fstype"] == "xfs" and row["mountpoint"] == "/home"


def test_keep_disabled_for_root(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    _select(qapp, window, "5")
    _open_dialog(qapp, window, "manualEditButton")
    keep = _visible(window, "dialogKeepRadio")
    assert keep.property("enabled") is True and keep.property("checked") is True
    _set_mount(qapp, window, "/")
    assert keep.property("enabled") is False
    assert _visible(window, "dialogFormatRadio").property("checked") is True
    assert _is_shown(window, "dialogKeepReason")
    _click(qapp, window, _visible(window, "dialogCancelButton"))

    _select(qapp, window, "6")
    _open_dialog(qapp, window, "manualEditButton")
    assert _visible(window, "dialogKeepRadio").property("enabled") is False
    assert _visible(window, "dialogKeepReason").property("text") == (
        "Encrypted (LUKS): Format or Delete it to use it"
    )
    assert _visible(window, "dialogSizeSpin").property("enabled") is False


def test_delete_existing_and_new_rows(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    _select(qapp, window, "5")
    _click(qapp, window, _visible(window, "manualDeleteButton"))
    assert bridge.manualRows == [{"kind": "existing", "number": 5, "action": "delete"}]
    assert "5" not in [r.property("rowKey") for r in _table_rows(window)]

    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    _set_size(qapp, window, 1024)
    _click(qapp, window, _visible(window, "dialogOkButton"))
    ref = next(r["ref"] for r in bridge.manualRows if r["kind"] == "new")
    _select(qapp, window, "new:" + ref)
    _click(qapp, window, _visible(window, "manualDeleteButton"))
    assert all(r["kind"] != "new" for r in bridge.manualRows)

    _click(qapp, window, _visible(window, "manualRevertButton"))
    assert bridge.manualRows == []


def test_guide_all_collapsed_and_single_open(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    assert _visible(window, "guidePrompt").property("text") == (
        "Select what you want to do to see the guide."
    )
    titles = [_visible(window, f"guideHeader_{i}").property("title") for i in range(7)]
    assert titles == GUIDE_TITLES

    def open_bodies() -> list[int]:
        return [i for i in range(7) if _is_shown(window, f"guideBody_{i}")]

    assert open_bodies() == []
    _click(qapp, window, _visible(window, "guideHeader_0"))
    assert open_bodies() == [0]
    _click(qapp, window, _visible(window, "guideHeader_3"))
    assert open_bodies() == [3]
    _click(qapp, window, _visible(window, "guideHeader_3"))
    assert open_bodies() == []


def test_guide_text_uses_2000_mib_esp(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    _click(qapp, window, _visible(window, "guideHeader_0"))
    texts = [it.property("text") for it in _walk(_visible(window, "guideBody_0")) if it.inherits("QQuickText")]
    assert any("2000 MiB" in t and "/boot/efi" in t for t in texts), texts


def test_checklist_updates(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)

    def checklist() -> list[tuple[str, bool]]:
        items = [
            _visible(window, f"checklistItem_{i}") for i in range(len(bridge.planChecklist))
        ]
        return [(it.property("text"), it.property("ok")) for it in items]

    assert checklist() == [("Root (/) assigned", False), ("EFI partition at /boot/efi", False)]
    error_lines = [
        it.property("text") for it in _walk(window.contentItem())
        if it.objectName().startswith("planErrorLine_") and it.isVisible()
    ]
    assert not any("root (/)" in t or "/boot/efi" in t for t in error_lines), error_lines

    gap = next(s for s in bridge.planPreview if s["kind"] == "free")
    bridge.setManualRow('{"kind": "existing", "number": 1, "action": "keep", "mountpoint": "/boot/efi"}')
    bridge.setManualRow(
        '{"kind": "new", "ref": "r1", "start": %d, "end": %d, "fstype": "ext4", "mountpoint": "/"}'
        % (gap["startSector"], gap["endSector"])
    )
    _settle(qapp)
    assert checklist() == [("Root (/) assigned", True), ("EFI partition at /boot/efi", True)]
    assert bridge.planValid


def test_next_blocked_until_plan_valid(gated_ui, qapp) -> None:
    window, bridge, _messages = gated_ui
    _enter_manual(qapp, window, bridge)
    next_button = _find(window, "nextInstallButton")
    assert next_button.property("enabled") is False
    gap = next(s for s in bridge.planPreview if s["kind"] == "free")
    bridge.setManualRow('{"kind": "existing", "number": 1, "action": "keep", "mountpoint": "/boot/efi"}')
    _settle(qapp)
    assert next_button.property("enabled") is False
    bridge.setManualRow(
        '{"kind": "new", "ref": "r1", "start": %d, "end": %d, "fstype": "ext4", "mountpoint": "/"}'
        % (gap["startSector"], gap["endSector"])
    )
    _settle(qapp)
    assert next_button.property("enabled") is True


def test_edit_new_partition_updates_same_row(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    _set_size(qapp, window, 4096)
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [created] = bridge.manualRows

    _select(qapp, window, "new:" + created["ref"])
    _open_dialog(qapp, window, "manualEditButton")
    spin = _visible(window, "dialogSizeSpin")
    assert spin.property("value") == 4096
    assert spin.property("to") > 4096
    _set_size(qapp, window, 8192)
    _set_mount(qapp, window, "/home")
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [row] = bridge.manualRows
    assert row["ref"] == created["ref"] and row["start"] == created["start"]
    assert row["end"] == created["start"] + 8192 * 2048 - 1
    assert row["mountpoint"] == "/home"


def test_passphrase_fields_cleared_on_close(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="nvme0n1")
    for closer in ("dialogCancelButton", "dialogOkButton"):
        _select(qapp, window, _free_key(window))
        _open_dialog(qapp, window, "manualCreateButton")
        _pick(qapp, window, "dialogFsCombo", "ext4")
        _visible(window, "dialogEncryptCheck").setProperty("checked", True)
        _set_mount(qapp, window, "/")
        _visible(window, "dialogPassField").setProperty("text", "correct horse")
        _visible(window, "dialogPassConfirmField").setProperty("text", "correct horse")
        _settle(qapp)
        _click(qapp, window, _visible(window, closer))
        assert _dialog(window).property("visible") is False
        assert window.findChild(QObject, "dialogPassField").property("text") == ""
        assert window.findChild(QObject, "dialogPassConfirmField").property("text") == ""
        bridge.revertManual()
        _settle(qapp)


def test_create_refused_when_less_than_one_mib_fits(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge, disk="sdd")
    _select(qapp, window, _free_key(window))
    _open_dialog(qapp, window, "manualCreateButton")
    assert _visible(window, "dialogOkButton").property("enabled") is False
    assert _visible(window, "dialogFitReason").property("text")
    _click(qapp, window, _visible(window, "dialogOkButton"))
    assert bridge.manualRows == []


def test_format_resets_resized_size(ui, qapp) -> None:
    window, bridge, _messages = ui
    _enter_manual(qapp, window, bridge)
    _select(qapp, window, "2")
    _open_dialog(qapp, window, "manualEditButton")
    _set_size(qapp, window, 100 * 1024)
    _click(qapp, window, _visible(window, "dialogFormatRadio"))
    assert _visible(window, "dialogSizeSpin").property("value") == 150 * 1024
    _click(qapp, window, _visible(window, "dialogKeepRadio"))
    _click(qapp, window, _visible(window, "dialogOkButton"))
    [row] = bridge.manualRows
    assert row.get("newEnd") is None


@pytest.mark.parametrize("step", [5, 6])
def test_next_and_install_blocked_while_disks_are_scanned(ui, qapp, step) -> None:
    window, bridge, _messages = ui
    bridge.setSelectedDisk("sdb")
    bridge.setPartitionMode("auto")
    window.setProperty("currentStep", step)
    _settle(qapp)
    next_button = _find(window, "nextInstallButton")
    assert next_button.property("enabled") is True
    bridge._disks_scanning = True
    bridge.disksScanningChanged.emit()
    _settle(qapp)
    assert next_button.property("enabled") is False
    bridge._disks_scanning = False
    bridge.disksScanningChanged.emit()
    _settle(qapp)
    assert next_button.property("enabled") is True


@pytest.mark.parametrize(("mode", "step"), [("manual", 5), ("alongside", 5), ("auto", 7)])
def test_retry_returns_to_partitioning_unless_erase(ui, qapp, mode, step) -> None:
    window, bridge, _messages = ui
    bridge.setSelectedDisk("sdb")
    bridge.setPartitionMode(mode)
    window.setProperty("currentStep", 8)
    _settle(qapp)
    finished = window.findChild(QObject, "finishedView")
    assert finished is not None
    with (
        patch.object(bridge, "refreshDisks") as refresh,
        patch.object(bridge, "startInstallation") as start,
    ):
        QMetaObject.invokeMethod(finished, "retryClicked")
        _settle(qapp)
    assert window.property("currentStep") == step
    assert refresh.called is (mode != "auto")
    assert start.called is (mode == "auto")

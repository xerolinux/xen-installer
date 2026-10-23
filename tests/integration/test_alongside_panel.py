"""Partition step Options card and the Install alongside panel (offscreen)."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import (  # noqa: E402
    Q_ARG,
    QMetaObject,
    QPoint,
    QPointF,
    Qt,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.mock_disks import finalize_mock_disk, mock_disks  # noqa: E402
from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402
from xen.jobs.bootloader import BootloaderJob  # noqa: E402
from xen.utils import disk_detector  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"

MIB = 1024**2
GIB = 1024**3


def _two_os_disk() -> dict[str, Any]:
    return finalize_mock_disk(
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
                    "osName": "Windows 11",
                    "minSizeBytes": 40 * GIB,
                },
                {"name": "sdb3", "sizeBytes": 60 * GIB, "fstype": "xfs", "partType": "linux"},
                {
                    "name": "sdb4",
                    "sizeBytes": 180 * GIB,
                    "fstype": "ext4",
                    "partType": "linux",
                    "osName": "Fedora Linux 40",
                    "minSizeBytes": 20 * GIB,
                },
            ],
        }
    )


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        QQuickStyle.setStyle("Fusion")
        app = QApplication([])
    return app  # type: ignore[return-value]


def _load_ui(qapp: QApplication, disks: list[dict[str, Any]] | None) -> Iterator[tuple[Any, EngineBridge, list[str]]]:
    messages: list[str] = []

    def handler(msg_type: QtMsgType, _context: object, message: str) -> None:
        if msg_type != QtMsgType.QtDebugMsg:
            messages.append(message)

    qInstallMessageHandler(handler)
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    bridge = EngineBridge(
        engine, XEROLINUX_CONFIG.parent, debug=False, dry_run=True, skip_requirements=False
    )
    if disks is not None:
        bridge._on_disk_scan_finished(disks)
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
    yield from _load_ui(qapp, mock_disks() + [_two_os_disk()])


@pytest.fixture
def unscanned_ui(qapp: QApplication) -> Iterator[tuple[Any, EngineBridge, list[str]]]:
    yield from _load_ui(qapp, None)


def _settle(qapp: QApplication, seconds: float = 0.2) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()


def _walk(item: QQuickItem) -> Iterator[QQuickItem]:
    yield item
    for child in item.childItems():
        yield from _walk(child)


def _find(window: Any, name: str) -> QQuickItem:
    found = [it for it in _walk(window.contentItem()) if it.objectName() == name]
    assert found, f"{name} not found"
    return found[0]


def _center(item: QQuickItem) -> QPoint:
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(round(p.x()), round(p.y()))


def _scroll_into_view(qapp: QApplication, window: Any, item: QQuickItem) -> QPoint:
    flick = _find(window, "partitionPageScroll").property("contentItem")
    top = flick.mapToScene(QPointF(0, 0)).y()
    y = item.mapToScene(QPointF(0, item.height() / 2)).y()
    if not top + 20 <= y <= top + flick.height() - 20:
        flick.setProperty("contentY", flick.property("contentY") + y - top - flick.height() / 2)
        _settle(qapp)
    return _center(item)


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _human(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    i, v = 0, float(size)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:.0f} {units[i]}" if v >= 100 or i == 0 else f"{v:.1f} {units[i]}"


def _alongside(qapp: QApplication, bridge: EngineBridge, disk: str) -> None:
    bridge.setSelectedDisk(disk)
    bridge.setPartitionMode("alongside")
    _settle(qapp)


def _qml_problems(messages: list[str]) -> list[str]:
    return [m for m in messages if "PartitionView" in m or "AlongsidePanel" in m or "PartitionBar" in m]


def test_radio_order_is_erase_alongside_manual(qapp, ui) -> None:
    window, bridge, messages = ui
    _alongside(qapp, bridge, "nvme1n1")
    names = ["partitionModeAutoRadio", "partitionModeAlongsideRadio", "partitionModeManualRadio"]
    ys = [_find(window, n).mapToScene(QPointF(0, 0)).y() for n in names]
    assert ys == sorted(ys)
    assert not _qml_problems(messages), _qml_problems(messages)


def test_alongside_radio_hidden_without_candidates(qapp, ui) -> None:
    window, bridge, _messages = ui
    bridge.setSelectedDisk("nvme0n1")
    _settle(qapp)
    assert bridge.alongsideAvailable is False
    assert not _find(window, "partitionModeAlongsideRadio").isVisible()
    bridge.setSelectedDisk("nvme1n1")
    _settle(qapp)
    assert bridge.alongsideAvailable is True
    assert _find(window, "partitionModeAlongsideRadio").isVisible()


def test_alongside_radio_selects_mode(qapp, ui) -> None:
    window, bridge, _messages = ui
    bridge.setSelectedDisk("nvme1n1")
    _settle(qapp)
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, _scroll_into_view(qapp, window, _find(window, "partitionModeAlongsideRadio")))
    _settle(qapp)
    assert bridge.partitionMode == "alongside"
    assert _find(window, "alongsidePanel").isVisible()
    assert _find(window, "partitionModeAlongsideRadio").property("checked") is True
    assert _find(window, "partitionModeAutoRadio").property("checked") is False


def test_click_current_bar_selects_partition(qapp, ui) -> None:
    window, bridge, _messages = ui
    _alongside(qapp, bridge, "sdb")
    assert bridge.alongsidePartition == 4

    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, _scroll_into_view(qapp, window, _find(window, "currentSegment_3")))
    _settle(qapp)
    assert bridge.alongsidePartition == 4
    reason = _find(window, "alongsideReasonText")
    assert reason.isVisible()
    blocked = next(c["reason"] for c in bridge.alongsideCandidates if c["number"] == 3)
    assert "XFS" in blocked
    assert reason.property("text") == f"sdb3: {blocked}"

    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, _scroll_into_view(qapp, window, _find(window, "currentSegment_2")))
    _settle(qapp)
    assert bridge.alongsidePartition == 2
    assert not _find(window, "alongsideReasonText").isVisible()


def test_alongside_unavailable_after_disk_change_blocks_next(qapp, ui) -> None:
    window, bridge, _messages = ui
    _alongside(qapp, bridge, "nvme1n1")
    bridge.setSelectedDisk("nvme0n1")
    _settle(qapp)
    assert bridge.partitionMode == "alongside"
    assert bridge.alongsideAvailable is False
    assert _find(window, "partitionModeAlongsideRadio").isVisible()
    messages = _find(window, "planMessages")
    assert messages.isVisible()
    texts = [it.property("text") for it in _walk(messages) if it.property("text")]
    assert any("No partition on this disk can be shrunk" in t for t in texts), texts
    assert _find(window, "nextInstallButton").property("enabled") is False


def test_disk_combo_lists_every_disk_and_selects(qapp, ui) -> None:
    window, bridge, _messages = ui
    combo = _find(window, "diskCombo")
    names = [d["name"] for d in bridge.disksModel]
    assert combo.property("count") == len(names) == 4
    QMetaObject.invokeMethod(combo, "activated", Q_ARG(int, names.index("nvme1n1")))
    _settle(qapp)
    assert bridge.selectedDisk == "nvme1n1"
    assert combo.property("currentIndex") == names.index("nvme1n1")
    assert "Mock NVMe Windows 512G" in combo.property("displayText")


def test_disk_combo_shows_no_disk_when_scan_finishes_after_load(qapp, unscanned_ui) -> None:
    window, bridge, _messages = unscanned_ui
    bridge._on_disk_scan_finished(mock_disks())
    _settle(qapp)
    combo = _find(window, "diskCombo")
    assert bridge.selectedDisk == ""
    assert combo.property("displayText") == "Select a disk"


def test_filesystem_and_swap_combos_are_labelled(qapp, ui) -> None:
    window, bridge, _messages = ui
    for mode, disk in (("auto", "sda"), ("alongside", "nvme1n1")):
        bridge.setSelectedDisk(disk)
        bridge.setPartitionMode(mode)
        _settle(qapp)
        shown = {
            it.property("text")
            for it in _walk(window.contentItem())
            if it.isVisible() and it.inherits("QQuickText")
        }
        assert {"File system:", "Swap:"} <= shown, mode


def test_erase_efi_note_shows_exact_mib(qapp, ui) -> None:
    window, bridge, _messages = ui
    bridge.setSelectedDisk("sda")
    bridge.setPartitionMode("auto")
    _settle(qapp)
    note = _find(window, "eraseEfiNote")
    assert note.isVisible()
    assert note.property("text") == f"EFI: {bridge.efiSizeMb} MiB (/boot/efi)"
    assert "2000 MiB" in note.property("text")


def test_ineligible_segments_dimmed(qapp, ui) -> None:
    window, bridge, _messages = ui
    _alongside(qapp, bridge, "sdb")
    assert _find(window, "currentSegment_3").property("opacity") < 1
    assert _find(window, "currentSegment_4").property("opacity") == 1


def test_drag_handle_updates_size_within_bounds(qapp, ui) -> None:
    window, bridge, _messages = ui
    _alongside(qapp, bridge, "nvme1n1")
    low, high = bridge.alongsideMinNewMb, bridge.alongsideMaxNewMb
    start = bridge.alongsideNewSizeMb
    handle = _find(window, "alongsideHandle")

    pos = _scroll_into_view(qapp, window, handle)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, pos)
    for step in range(1, 11):
        QTest.mouseMove(window, QPoint(pos.x() - step * 15, pos.y()))
        _settle(qapp, 0.02)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(pos.x() - 150, pos.y()))
    _settle(qapp)
    grown = bridge.alongsideNewSizeMb
    assert grown > start
    assert low <= grown <= high

    pos = _scroll_into_view(qapp, window, _find(window, "alongsideHandle"))
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, pos)
    for step in range(1, 21):
        QTest.mouseMove(window, QPoint(pos.x() + step * 200, pos.y()))
        _settle(qapp, 0.02)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(pos.x() + 4000, pos.y()))
    _settle(qapp)
    assert bridge.alongsideNewSizeMb == low


def test_handle_keyboard_steps_one_gib(qapp, ui) -> None:
    window, bridge, _messages = ui
    _alongside(qapp, bridge, "nvme1n1")
    start = bridge.alongsideNewSizeMb
    handle = _find(window, "alongsideHandle")
    handle.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Left)
    _settle(qapp)
    assert bridge.alongsideNewSizeMb == start + 1024
    QTest.keyClick(window, Qt.Key_Right)
    QTest.keyClick(window, Qt.Key_Right)
    _settle(qapp)
    assert bridge.alongsideNewSizeMb == start - 1024


def test_size_sentence_text(qapp, ui) -> None:
    window, bridge, _messages = ui
    _alongside(qapp, bridge, "nvme1n1")
    preview = bridge.planPreview
    kept = next(s for s in preview if s.get("number") == bridge.alongsidePartition)
    new = next(s for s in preview if s.get("newRef"))
    text = _plain(_find(window, "alongsideSizeText").property("text"))
    assert text == (
        f"Windows 11 (/dev/nvme1n1p3, ntfs) will be shrunk to {_human(kept['sizeBytes'])} "
        f"and a new {_human(new['sizeBytes'])} partition will be created for XeroLinux."
    )

    bridge.setAlongsideNewSizeMb(bridge.alongsideMinNewMb)
    _settle(qapp)
    text = _plain(_find(window, "alongsideSizeText").property("text"))
    assert "a new 25.0 GiB partition" in text


def test_esp_sentence_and_combo(qapp, ui) -> None:
    window, bridge, _messages = ui
    _alongside(qapp, bridge, "nvme1n1")
    assert len(bridge.espCandidates) > 1
    combo = _find(window, "alongsideEspCombo")
    assert combo.isVisible()
    assert combo.property("currentIndex") == 0
    assert bridge.alongsideEsp["disk"] == "nvme1n1"

    only_one = [d for d in bridge.disksModel if d["name"] == "nvme1n1"]
    bridge._on_disk_scan_finished(only_one)
    _settle(qapp)
    assert not _find(window, "alongsideEspCombo").isVisible()
    text = _plain(_find(window, "alongsideEspText").property("text"))
    assert text == "The EFI system partition at /dev/nvme1n1p1 will be reused (not formatted)."


def test_scanning_indicator_replaces_empty_state(qapp, ui) -> None:
    window, bridge, _messages = ui
    bridge._disks_model = []
    bridge._disks_scanning = True
    bridge.disksChanged.emit()
    bridge.disksScanningChanged.emit()
    _settle(qapp)
    assert _find(window, "disksScanningIndicator").isVisible()
    assert not _find(window, "noDisksState").isVisible()

    bridge._disks_scanning = False
    bridge.disksScanningChanged.emit()
    _settle(qapp)
    assert not _find(window, "disksScanningIndicator").isVisible()
    assert _find(window, "noDisksState").isVisible()


def test_alongside_mode_loads_without_qml_errors(qapp, ui) -> None:
    window, bridge, messages = ui
    _alongside(qapp, bridge, "nvme1n1")
    bridge.setEncryption(True)
    _settle(qapp)
    bridge.setPartitionMode("auto")
    _settle(qapp)
    problems = [m for m in messages if ".qml" in m and "SummaryView" not in m]
    assert not problems, problems


@pytest.mark.parametrize(("bootloader", "shown"), [("grub", True), ("systemd-boot", False)])
def test_grub_kdf_note_only_for_encrypted_grub(qapp, bootloader: str, shown: bool) -> None:
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    for job in engine.jobs:
        if isinstance(job, BootloaderJob):
            job._config["type"] = bootloader
    bridge = EngineBridge(
        engine, XEROLINUX_CONFIG.parent, debug=False, dry_run=True, skip_requirements=True
    )
    bridge._on_disk_scan_finished(mock_disks())
    bridge.setSelectedDisk("nvme0n1")
    qml_engine = QQmlApplicationEngine()
    translator = TranslatorProxy(engine=qml_engine)
    ctx = qml_engine.rootContext()
    ctx.setContextProperty("engine", bridge)
    ctx.setContextProperty("branding", bridge.branding_proxy)
    ctx.setContextProperty("translator", translator)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    window = qml_engine.rootObjects()[0]
    try:
        window.setProperty("currentStep", 5)
        _settle(qapp)
        assert _find(window, "grubKdfNote").isVisible() is False
        bridge.setEncryption(True)
        _settle(qapp)
        assert _find(window, "grubKdfNote").isVisible() is shown
    finally:
        del translator, qml_engine


def _fact_chips(window: Any) -> list[str]:
    return [
        it.property("text")
        for it in _walk(window.contentItem())
        if it.objectName() == "diskFactChip" and it.isVisible()
    ]


def test_disk_hint_card_shows_until_a_disk_is_picked(qapp, unscanned_ui) -> None:
    window, bridge, _messages = unscanned_ui
    bridge._on_disk_scan_finished(mock_disks())
    _settle(qapp)
    assert _find(window, "diskHintCard").isVisible()
    assert _fact_chips(window) == []
    bridge.setSelectedDisk("nvme1n1")
    _settle(qapp)
    assert not _find(window, "diskHintCard").isVisible()


def test_disk_fact_chips_describe_the_picked_disk(qapp, ui) -> None:
    window, bridge, _messages = ui
    bridge.setSelectedDisk("nvme1n1")
    _settle(qapp)
    assert _fact_chips(window) == [
        "NVMe SSD", "512.0 GB", "GPT", "4 partitions", "Windows 11", "1.1 GiB free",
    ]  # fmt: skip
    bridge.setSelectedDisk("sda")
    _settle(qapp)
    assert _fact_chips(window) == ["SSD", "500.0 GB", "GPT", "2 partitions", "No free space"]

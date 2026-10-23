"""Every wizard page fits its window without scrolling, from the minimum
window size up to a 1440p screen (regression: Partition's Options card was
cut off at 1582x870)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.mock_disks import mock_disks  # noqa: E402
from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"

WINDOW_SIZES = [(1024, 640), (1366, 700), (1582, 870), (1920, 1040), (2560, 1400)]
# Packages (3) is checked separately: only its package list may scroll.
STEPS = [1, 2, 4, 5, 6, 7, 8]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture(scope="module")
def main_window(qapp: QApplication):
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    bridge = EngineBridge(engine, XEROLINUX_CONFIG.parent, debug=False, dry_run=True, skip_requirements=True)
    # Every mock disk, with the Windows one selected so the alongside choice shows too.
    bridge._on_disk_scan_finished(mock_disks())
    bridge.setSelectedDisk("nvme1n1")

    qml_engine = QQmlApplicationEngine()
    translator = TranslatorProxy(engine=qml_engine)
    ctx = qml_engine.rootContext()
    ctx.setContextProperty("engine", bridge)
    ctx.setContextProperty("branding", bridge.branding_proxy)
    ctx.setContextProperty("translator", translator)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    roots = qml_engine.rootObjects()
    assert roots
    assert isinstance(roots[0], QQuickWindow)
    yield qapp, roots[0], bridge
    del translator, qml_engine


def _settle(qapp: QApplication) -> None:
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        qapp.processEvents()


def _page_overflow(window) -> float:
    pages = [
        it for it in _walk(window.contentItem())
        if it.isVisible() and it.inherits("QQuickFlickable") and it.height() > 0
    ]
    page = max(pages, key=lambda it: it.height())
    return page.property("contentHeight") - page.height()


def _use_btrfs(bridge) -> None:
    # The tallest Erase/Alongside layout: btrfs adds the Subvolumes line.
    bridge._apply_internet_status(True)
    bridge.setFilesystem("btrfs")
    assert bridge.filesystem == "btrfs"


def _walk(item):
    yield item
    for child in item.childItems():
        yield from _walk(child)


@pytest.mark.parametrize(("width", "height"), WINDOW_SIZES, ids=lambda v: str(v))
def test_pages_fit_without_scrolling(main_window, width: int, height: int) -> None:
    qapp, window, _bridge = main_window
    window.setWidth(width)
    window.setHeight(height)
    overflowing = []
    for step in STEPS:
        window.setProperty("currentStep", step)
        _settle(qapp)
        pages = [
            it for it in _walk(window.contentItem())
            if it.isVisible() and it.inherits("QQuickFlickable") and it.height() > 0
        ]
        page = max(pages, key=lambda it: it.height())
        content = page.property("contentHeight")
        if content > page.height() + 1:
            overflowing.append(f"step {step}: {content:.0f}px of content in {page.height():.0f}px")
    window.setProperty("currentStep", 3)
    _settle(qapp)
    rail = next(it for it in _walk(window.contentItem()) if it.objectName() == "packageRail")
    rail_flick = next(it for it in _walk(rail) if it.inherits("QQuickFlickable"))
    if rail_flick.property("contentHeight") > rail_flick.height() + 1:
        overflowing.append(f"package categories: {rail_flick.property('contentHeight'):.0f}px in {rail_flick.height():.0f}px")
    assert not overflowing, f"{width}x{height} uiScale={window.property('uiScale'):.2f}: {overflowing}"


@pytest.mark.parametrize(("width", "height"), WINDOW_SIZES, ids=lambda v: str(v))
def test_partition_fits_with_encryption_fields_open(main_window, width: int, height: int) -> None:
    qapp, window, bridge = main_window
    window.setWidth(width)
    window.setHeight(height)
    bridge.setEncryption(True)
    _use_btrfs(bridge)
    try:
        window.setProperty("currentStep", 5)
        _settle(qapp)
        overflow = _page_overflow(window)
    finally:
        bridge.setEncryption(False)
        bridge.setFilesystem("xfs")
    assert overflow <= 1, f"{width}x{height}: Partition overflows by {overflow:.0f}px with encryption on"


@pytest.mark.parametrize(("width", "height"), WINDOW_SIZES, ids=lambda v: str(v))
def test_partition_fits_in_alongside_mode_with_encryption(main_window, width: int, height: int) -> None:
    qapp, window, bridge = main_window
    window.setWidth(width)
    window.setHeight(height)
    try:
        bridge.setPartitionMode("alongside")
        bridge.setEncryption(True)
        _use_btrfs(bridge)
        window.setProperty("currentStep", 5)
        _settle(qapp)
        assert bridge.partitionMode == "alongside"
        overflow = _page_overflow(window)
    finally:
        bridge.setEncryption(False)
        bridge.setFilesystem("xfs")
        bridge.setPartitionMode("auto")
    assert overflow <= 1, f"{width}x{height}: Partition overflows by {overflow:.0f}px in alongside mode"


@pytest.mark.parametrize(("width", "height"), WINDOW_SIZES, ids=lambda v: str(v))
def test_manual_page_fits_with_any_guide_section_open(main_window, width: int, height: int) -> None:
    qapp, window, bridge = main_window
    window.setWidth(width)
    window.setHeight(height)
    partition_view = next(
        it for it in _walk(window.contentItem()) if it.objectName() == "partitionView"
    )
    overflowing = []
    try:
        bridge.setPartitionMode("manual")
        window.setProperty("currentStep", 5)
        partition_view.setProperty("manualEditorActive", True)
        _settle(qapp)
        guide = next(it for it in _walk(window.contentItem()) if it.objectName() == "guideAccordion")
        assert guide.isVisible()
        for section in range(7):
            guide.setProperty("openIndex", section)
            _settle(qapp)
            # Only the partition table is a list that may scroll.
            for it in _walk(window.contentItem()):
                if (
                    it.isVisible()
                    and it.inherits("QQuickFlickable")
                    and it.objectName() != "manualTable"
                    and it.property("contentHeight") > it.height() + 1
                ):
                    overflowing.append(
                        f"section {section}: {it.objectName() or it.metaObject().className()} "
                        f"{it.property('contentHeight'):.0f}px in {it.height():.0f}px"
                    )
    finally:
        partition_view.setProperty("manualEditorActive", False)
        bridge.setPartitionMode("auto")
    assert not overflowing, f"{width}x{height}: {overflowing}"

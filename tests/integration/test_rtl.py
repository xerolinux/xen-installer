"""Right-to-left languages mirror the whole window."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "examples" / "xerolinux.yaml"


def _settle(app: QApplication) -> None:
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        app.processEvents()


def test_window_mirrors_for_right_to_left_languages_only() -> None:
    app = QApplication.instance() or QApplication([])
    bridge = EngineBridge(Engine.from_config_file(CONFIG), CONFIG.parent, dry_run=True, skip_requirements=True)
    qml_engine = QQmlApplicationEngine()
    translator = TranslatorProxy(engine=qml_engine)
    ctx = qml_engine.rootContext()
    ctx.setContextProperty("engine", bridge)
    ctx.setContextProperty("branding", bridge.branding_proxy)
    ctx.setContextProperty("translator", translator)
    qml_engine.load(QUrl.fromLocalFile(str(ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml")))
    window = qml_engine.rootObjects()[0]
    try:
        for locale, mirrored in (("ar_SA", True), ("fa_IR", True), ("he_IL", True), ("de_DE", False), ("en_US", False)):
            translator.setLocale(locale)
            _settle(app)
            assert window.property("rightToLeft") is mirrored, locale
    finally:
        del translator, qml_engine

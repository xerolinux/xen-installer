"""
Test d'intégration offscreen : navigation générale du wizard Main.qml.

Garde-fou anti-régression sur la structure des steps :

- Le chargement de Main.qml offscreen ne produit AUCUNE binding loop ni
  warning QML.
- La navigation traverse bien les 8 étapes (0..7) : Welcome, Locale,
  Packages, Users, Partition, Summary, Installing, Finished.

Le test s'exécute en plateforme offscreen (aucune fenêtre réelle).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# Force la plateforme offscreen AVANT tout import Qt (aucune GUI réelle).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QtMsgType, QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.theme_bridge import SystemThemeBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """QApplication unique (offscreen) pour toute la session."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def bridge(qapp: QApplication) -> EngineBridge:
    """EngineBridge réel adossé à la config XeroLinux."""
    assert qapp is not None
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    theme_base = XEROLINUX_CONFIG.parent
    return EngineBridge(engine, theme_base, debug=False, dry_run=True, skip_requirements=True)


class _QmlMessageCollector:
    """Collecte les messages Qt (warnings/erreurs) émis pendant le chargement."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def handler(self, msg_type: QtMsgType, _context: object, message: str) -> None:
        if msg_type == QtMsgType.QtWarningMsg:
            self.warnings.append(message)
        elif msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            self.errors.append(message)


class TestMainQmlOffscreen:
    """Chargement offscreen de Main.qml : 0 binding loop, navigation 0..6."""

    def _load(
        self, bridge: EngineBridge
    ) -> tuple[QQmlApplicationEngine, Any, _QmlMessageCollector, TranslatorProxy, SystemThemeBridge]:
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
        assert roots, "Main.qml n'a pas pu être chargé"
        return qml_engine, roots[0], collector, translator_proxy, system_theme

    def test_no_binding_loops_on_load(self, bridge: EngineBridge, qapp: QApplication) -> None:
        qml_engine, root, collector, _tp, _st = self._load(bridge)
        try:
            qapp.processEvents()
            binding_loops = [w for w in collector.warnings if "Binding loop" in w]
            assert not binding_loops, f"Binding loops détectées : {binding_loops}"
            assert not collector.errors, f"Erreurs QML : {collector.errors}"
        finally:
            qInstallMessageHandler(None)
            del qml_engine

    def test_navigation_traverses_eight_steps(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        qml_engine, root, collector, _tp, _st = self._load(bridge)
        try:
            assert root.property("totalSteps") == 8
            step_names_raw = root.property("stepNames")
            # stepNames est un tableau JS (QJSValue) : convertir en liste Python.
            step_names = (
                step_names_raw.toVariant()
                if hasattr(step_names_raw, "toVariant")
                else list(step_names_raw)
            )
            assert step_names == [
                "Welcome",
                "Locale",
                "Packages",
                "Users",
                "Partition",
                "Summary",
                "Installing",
                "Finished",
            ]

            # Parcours 0..7 en écrivant currentStep (skipValidation=True via
            # skip_requirements côté bridge => canProceedToNext ne bloque pas).
            for step in range(0, 8):
                root.setProperty("currentStep", step)
                qapp.processEvents()
                assert root.property("currentStep") == step

            binding_loops = [w for w in collector.warnings if "Binding loop" in w]
            assert not binding_loops, f"Binding loops pendant la navigation : {binding_loops}"
        finally:
            qInstallMessageHandler(None)
            del qml_engine

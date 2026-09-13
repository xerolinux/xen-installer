"""
Tests d'intégration offscreen : retraduction live + clavier live (LocaleView).

Couvre deux correctifs :

1. Changer la langue dans LocaleView doit retraduire l'UI EN DIRECT (pas
   seulement mettre à jour la config d'installation) : le handler
   `onLocaleSelected` de Main.qml doit appeler `translator.setLocale()` en
   plus de `engine.setSelectedLocale()`.
2. Changer le clavier (layout ou variante) doit appliquer le layout à la
   SESSION LIVE via `engine.applyKeyboardLayout(...)`, sans jamais régresser
   l'écriture de la config d'installation (setSelectedKeymap/
   setSelectedKeyboardVariant).

Exécution en plateforme offscreen (aucune fenêtre réelle).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

# Force la plateforme offscreen AVANT tout import Qt (aucune GUI réelle).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CONFIG = PROJECT_ROOT / "config" / "examples" / "minimal.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """QApplication unique (offscreen) pour toute la session."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def _make_bridge() -> EngineBridge:
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    return EngineBridge(
        engine, MINIMAL_CONFIG.parent, debug=False, dry_run=True, skip_requirements=True
    )


def _load(bridge: EngineBridge) -> tuple[QQmlApplicationEngine, QObject, TranslatorProxy]:
    qml_engine = QQmlApplicationEngine()
    translator_proxy = TranslatorProxy(engine=qml_engine)
    qml_engine.rootContext().setContextProperty("engine", bridge)
    qml_engine.rootContext().setContextProperty("branding", bridge.branding_proxy)
    qml_engine.rootContext().setContextProperty("translator", translator_proxy)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    roots = qml_engine.rootObjects()
    assert roots, "Main.qml n'a pas pu être chargé"
    return qml_engine, roots[0], translator_proxy


def _find_locale_view(root: QObject) -> QObject | None:
    """Retrouve l'instance LocaleView via sa signature (signal + propriété)."""
    stack: list[QObject] = [root]
    while stack:
        obj = stack.pop()
        for child in obj.children():
            stack.append(child)
            meta = child.metaObject()
            names = {meta.property(i).name() for i in range(meta.propertyCount())}
            if (
                "selectedKeyboardVariant" in names
                and meta.indexOfSignal("localeSelected(QString)") >= 0
            ):
                return child
    return None


class TestLiveLanguageSwitch:
    """Sélectionner une langue dans LocaleView retraduit l'UI en direct."""

    def test_locale_selected_triggers_translator_set_locale(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, translator_proxy = _load(bridge)
        try:
            locale_view = _find_locale_view(root)
            assert locale_view is not None, "LocaleView introuvable dans l'arbre QML"

            assert translator_proxy.currentLocale == "en_US"

            ok = QMetaObject.invokeMethod(
                locale_view,
                "localeSelected",
                Qt.DirectConnection,
                Q_ARG(str, "de_DE.UTF-8"),
            )
            assert ok
            qapp.processEvents()

            # (a) Retraduction live : le translator a bien changé de locale.
            assert translator_proxy.currentLocale == "de_DE"
            # (b) Config d'installation : toujours écrite (pas de régression).
            assert bridge.selectedLocale == "de_DE.UTF-8"
        finally:
            del qml_engine

    def test_locale_selection_syncs_live_keyboard(self, qapp: QApplication) -> None:
        """La dérivation auto du clavier depuis la langue déclenche aussi
        l'application live (via engine.applyKeyboardLayout)."""
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            locale_view = _find_locale_view(root)
            assert locale_view is not None

            with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
                QMetaObject.invokeMethod(
                    locale_view,
                    "localeSelected",
                    Qt.DirectConnection,
                    Q_ARG(str, "fr_FR.UTF-8"),
                )
                qapp.processEvents()

            assert mocked.called
        finally:
            del qml_engine


class TestLiveKeyboardApply:
    """Sélectionner un layout/une variante applique la session live."""

    def test_keymap_selected_applies_live_and_writes_config(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            locale_view = _find_locale_view(root)
            assert locale_view is not None

            with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
                ok = QMetaObject.invokeMethod(
                    locale_view,
                    "keymapSelected",
                    Qt.DirectConnection,
                    Q_ARG(str, "de"),
                )
                assert ok
                qapp.processEvents()

            # (a) Application live tentée avec le nouveau layout.
            mocked.assert_called_with("de", bridge.selectedKeyboardVariant)
            # (b) Config d'installation toujours mise à jour (pas de régression).
            assert bridge.selectedKeymap == "de"
            assert bridge.selections["keymap"] == "de"
        finally:
            del qml_engine

    def test_keyboard_variant_selected_applies_live_and_writes_config(
        self, qapp: QApplication
    ) -> None:
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            locale_view = _find_locale_view(root)
            assert locale_view is not None

            bridge.setSelectedKeymap("fr")

            with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
                ok = QMetaObject.invokeMethod(
                    locale_view,
                    "keyboardVariantSelected",
                    Qt.DirectConnection,
                    Q_ARG(str, "azerty"),
                )
                assert ok
                qapp.processEvents()

            mocked.assert_called_with("fr", "azerty")
            assert bridge.selectedKeyboardVariant == "azerty"
            assert bridge.selections["keyboardVariant"] == "azerty"
        finally:
            del qml_engine

"""
Tests d'intégration offscreen : retraduction live + clavier live (LocaleView).

Couvre deux correctifs :

1. Changer la langue (page d'accueil, WelcomeView) doit retraduire l'UI EN DIRECT (pas
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

from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, Qt, QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
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


def _make_bridge(dry_run: bool = True) -> EngineBridge:
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    return EngineBridge(
        engine, MINIMAL_CONFIG.parent, debug=False, dry_run=dry_run, skip_requirements=True
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
                "selectedTimezone" in names
                and meta.indexOfSignal("timezoneSelected(QString)") >= 0
            ):
                return child
    return None


def _find_welcome_view(root: QObject) -> QObject | None:
    """Retrouve l'instance WelcomeView, qui porte le choix de la langue
    système (comme la page d'accueil de Calamares)."""
    stack: list[QObject] = [root]
    while stack:
        obj = stack.pop()
        for child in obj.children():
            stack.append(child)
            meta = child.metaObject()
            names = {meta.property(i).name() for i in range(meta.propertyCount())}
            if (
                "selectedLocale" in names
                and meta.indexOfSignal("localeSelected(QString)") >= 0
            ):
                return child
    return None


def _find_keyboard_view(root: QObject) -> QObject | None:
    """Retrouve l'instance KeyboardView via sa signature (signal + propriété).
    Separate wizard step from LocaleView since Keyboard split out of it -
    see KeyboardView.qml."""
    stack: list[QObject] = [root]
    while stack:
        obj = stack.pop()
        for child in obj.children():
            stack.append(child)
            meta = child.metaObject()
            names = {meta.property(i).name() for i in range(meta.propertyCount())}
            if (
                "selectedKeyboardVariant" in names
                and meta.indexOfSignal("keymapSelected(QString)") >= 0
            ):
                return child
    return None


class TestLiveLanguageSwitch:
    """Sélectionner une langue dans WelcomeView retraduit l'UI en direct."""

    def test_locale_selected_triggers_translator_set_locale(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, translator_proxy = _load(bridge)
        try:
            welcome_view = _find_welcome_view(root)
            assert welcome_view is not None, "WelcomeView introuvable dans l'arbre QML"

            assert translator_proxy.currentLocale == "en_US"

            ok = QMetaObject.invokeMethod(
                welcome_view,
                "localeSelected",
                Qt.DirectConnection,
                Q_ARG(str, "de_DE.UTF-8"),
            )
            assert ok
            qapp.processEvents()

            assert translator_proxy.currentLocale == "de_DE"
            # The system locale is chosen on the Locale page, not here
            assert bridge.selectedLocale == "en_US.UTF-8"
        finally:
            del qml_engine

    def test_locale_selection_leaves_the_keyboard_alone(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            welcome_view = _find_welcome_view(root)
            assert welcome_view is not None

            with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
                QMetaObject.invokeMethod(
                    welcome_view,
                    "localeSelected",
                    Qt.DirectConnection,
                    Q_ARG(str, "fr_FR.UTF-8"),
                )
                qapp.processEvents()

            assert not mocked.called
            assert bridge.selectedKeymap == "us"
        finally:
            del qml_engine

    def test_welcome_explains_it_only_changes_the_installer_language(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            notes = [o for o in root.findChildren(QObject) if o.objectName() == "uiLanguageNote"]
            assert notes and notes[0].property("visible")
        finally:
            del qml_engine


class TestLiveKeyboardApply:
    """Sélectionner un layout/une variante applique la session live."""

    def test_keymap_selected_applies_live_and_writes_config(self, qapp: QApplication) -> None:
        bridge = _make_bridge(dry_run=False)
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            keyboard_view = _find_keyboard_view(root)
            assert keyboard_view is not None

            with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
                ok = QMetaObject.invokeMethod(
                    keyboard_view,
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

    def test_leaving_the_keyboard_page_applies_the_layout(self, qapp: QApplication) -> None:
        bridge = _make_bridge(dry_run=False)
        bridge.setSelectedKeymap("fr")
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            root.setProperty("currentStep", 2)
            qapp.processEvents()
            with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
                QMetaObject.invokeMethod(root, "navigateNext", Qt.DirectConnection)
                qapp.processEvents()
            mocked.assert_called_with("fr", bridge.selectedKeyboardVariant)
        finally:
            del qml_engine

    def test_keyboard_variant_selected_applies_live_and_writes_config(
        self, qapp: QApplication
    ) -> None:
        bridge = _make_bridge(dry_run=False)
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            keyboard_view = _find_keyboard_view(root)
            assert keyboard_view is not None

            bridge.setSelectedKeymap("fr")

            with patch("xen.gui.bridge.apply_keyboard_layout_live", return_value=True) as mocked:
                ok = QMetaObject.invokeMethod(
                    keyboard_view,
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


def _walk(root: QObject):
    """Dual QObject-tree/QQuickItem visual-tree walk - see
    test_partition_view_manual.py's _find_by_object_name for why both are
    needed to reach Repeater-generated delegates (the on-screen keyboard's
    keys)."""
    stack: list[QObject] = [root]
    visited_ids: set[int] = set()
    visited_refs: list[QObject] = []
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


def _find_by_object_name(root: QObject, name: str) -> QObject | None:
    for obj in _walk(root):
        if obj.objectName() == name:
            return obj
    return None


class TestTimezoneMap:
    """The interactive map and the existing timezone combo stay in sync,
    and clicking the map updates the selected timezone."""

    def test_map_reflects_combo_driven_selection(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            locale_view = _find_locale_view(root)
            assert locale_view is not None

            # The map's MapQuickItem pin only reports visible once an
            # ancestor chain is actually visible (QtQuick/QtLocation
            # effective-visibility semantics) - step to Locale first, as a
            # real user would have the page open before touching the map.
            root.setProperty("currentStep", 1)
            qapp.processEvents()

            QMetaObject.invokeMethod(
                locale_view,
                "timezoneSelected",
                Qt.DirectConnection,
                Q_ARG(str, "Europe/Paris"),
            )
            qapp.processEvents()

            tz_map = _find_by_object_name(root, "timezoneMap")
            assert tz_map is not None
            assert tz_map.property("selectedTimezone") == "Europe/Paris"
            assert bool(tz_map.property("_havePin")) is True
            assert tz_map.property("pinLat") == pytest.approx(48.85, abs=0.1)
        finally:
            del qml_engine

    def test_clicking_map_updates_selected_timezone(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            tz_map = _find_by_object_name(root, "timezoneMap")
            assert tz_map is not None

            QMetaObject.invokeMethod(
                tz_map,
                "_pickAt",
                Q_ARG("QVariant", 48.85),
                Q_ARG("QVariant", 2.35),
            )
            qapp.processEvents()

            assert bridge.selectedTimezone != ""
        finally:
            del qml_engine

    def test_mouse_click_selects_the_zone_under_the_cursor(self, qapp: QApplication) -> None:
        """A real click on western Kentucky (Central time) goes through the
        scaled map layer and must resolve to the zone actually there, with
        the pin dropped under the cursor."""
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            root.setWidth(1440)
            root.setHeight(864)
            root.setProperty("currentStep", 1)
            QTest.qWait(500)

            tz_map = _find_by_object_name(root, "timezoneMap")
            assert tz_map is not None
            point = QMetaObject.invokeMethod(
                tz_map,
                "_windowPointFor",
                Q_RETURN_ARG("QVariant"),
                Q_ARG("QVariant", 37.08),
                Q_ARG("QVariant", -88.60),
            )

            QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            qapp.processEvents()

            assert bridge.selectedTimezone == "America/Chicago"
            assert tz_map.property("pinLat") == pytest.approx(37.08, abs=0.6)
            assert tz_map.property("pinLon") == pytest.approx(-88.60, abs=0.6)
        finally:
            del qml_engine

    def test_pin_stays_at_clicked_point_not_snapped_to_zone_city(
        self, qapp: QApplication
    ) -> None:
        """The round trip (_pickAt -> timezonePicked -> timezoneSelected ->
        engine.setSelectedTimezone -> selectedTimezone binding ->
        onSelectedTimezoneChanged) must not overwrite the pin with the
        zone's canonical zone1970.tab coordinate - the user should see the
        pin where they actually clicked, not snapped to a representative
        city possibly hundreds of km away."""
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            root.setProperty("currentStep", 1)
            qapp.processEvents()

            tz_map = _find_by_object_name(root, "timezoneMap")
            assert tz_map is not None

            # A coordinate near, but not exactly on, Europe/Paris's own
            # zone1970.tab coordinate (48.85, 2.35) - a snap-back would be
            # detectable.
            expected_lat, expected_lon = 48.70, 2.50

            QMetaObject.invokeMethod(
                tz_map,
                "_pickAt",
                Q_ARG("QVariant", expected_lat),
                Q_ARG("QVariant", expected_lon),
            )
            qapp.processEvents()

            pin_lat = tz_map.property("pinLat")
            pin_lon = tz_map.property("pinLon")
            assert pin_lat == pytest.approx(expected_lat, abs=0.01)
            assert pin_lon == pytest.approx(expected_lon, abs=0.01)
        finally:
            del qml_engine


class TestKeyboardTestArea:
    """The on-screen keyboard test field highlights the key matching what
    was typed, confirming the live-applied layout end to end."""

    def test_keyboard_test_field_flashes_key_on_type(self, qapp: QApplication) -> None:
        bridge = _make_bridge()
        qml_engine, root, _translator_proxy = _load(bridge)
        try:
            test_field = _find_by_object_name(root, "keyboardTestField")
            osk = _find_by_object_name(root, "onScreenKeyboard")
            assert test_field is not None, "keyboardTestField not found"
            assert osk is not None, "onScreenKeyboard not found"

            test_field.setProperty("text", "a")
            qapp.processEvents()

            flashed = [
                k.property("mainLabel")
                for k in _walk(osk)
                if k.property("flashed") is True
            ]
            assert "a" in flashed, f"expected key 'a' to flash, got {flashed}"
        finally:
            del qml_engine

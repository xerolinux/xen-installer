"""
Tests d'intégration offscreen : écran SummaryView (résumé + confirmation).

Couvre deux correctifs de la Phase 2 :

ITEM 1: Persistance du résumé : les valeurs saisies dans les vues précédentes
    (username, hostname, locale, timezone, keymap, disque...) doivent
    RÉELLEMENT s'afficher dans SummaryView. On pilote les VRAIS setters du bridge,
    on force currentStep=5 (Summary) et on lit le `text` RÉEL des éléments Text de
    l'arbre QML (preuve runtime, pas une simple lecture de Property).

ITEM 2: Confirmation finale : une CheckBox arme le garde-fou destructif
    (engine.setConfirmed). Le bouton « Install » (canProceedToNext au step 5) ne
    doit autoriser l'installation que si la confirmation est cochée.

Exécution en plateforme offscreen (aucune fenêtre réelle).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Force la plateforme offscreen AVANT tout import Qt (aucune GUI réelle).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.mock_disks import finalize_mock_disk, mock_disks  # noqa: E402
from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402
from xen.utils import disk_detector  # noqa: E402

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


def _make_bridge(skip_requirements: bool) -> EngineBridge:
    """EngineBridge réel adossé à la config XeroLinux."""
    engine = Engine.from_config_file(XEROLINUX_CONFIG)
    return EngineBridge(
        engine,
        XEROLINUX_CONFIG.parent,
        debug=False,
        dry_run=True,
        skip_requirements=skip_requirements,
    )


def _load(
    bridge: EngineBridge,
) -> tuple[QQmlApplicationEngine, QObject, TranslatorProxy]:
    """Charge Main.qml offscreen avec le bridge fourni."""
    qml_engine = QQmlApplicationEngine()
    translator_proxy = TranslatorProxy(engine=qml_engine)
    qml_engine.rootContext().setContextProperty("engine", bridge)
    qml_engine.rootContext().setContextProperty("branding", bridge.branding_proxy)
    qml_engine.rootContext().setContextProperty("translator", translator_proxy)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    roots = qml_engine.rootObjects()
    assert roots, "Main.qml n'a pas pu être chargé"
    return qml_engine, roots[0], translator_proxy


def _find_by_object_name(root: QObject, name: str) -> QObject | None:
    """Retrouve un objet QML par son objectName."""
    stack: list[QObject] = [root]
    while stack:
        obj = stack.pop()
        for child in obj.children():
            stack.append(child)
            if child.objectName() == name:
                return child
    return None


def _find_summary(root: QObject) -> QObject | None:
    """Retrouve l'instance SummaryView dans l'arbre QML.

    Heuristique robuste : propriété scalaire `usernameValue` (propre à
    SummaryView) + signal `editPartition()`.
    """
    stack: list[QObject] = [root]
    while stack:
        obj = stack.pop()
        for child in obj.children():
            stack.append(child)
            meta = child.metaObject()
            names = {meta.property(i).name() for i in range(meta.propertyCount())}
            if "usernameValue" in names and meta.indexOfSignal("editPartition()") >= 0:
                return child
    return None


def _collect_texts(summary: QObject) -> list[str]:
    """Collecte les valeurs `text` de tous les Text sous SummaryView."""
    texts: list[str] = []
    stack: list[QObject] = [summary]
    while stack:
        obj = stack.pop()
        for child in obj.children():
            stack.append(child)
            meta = child.metaObject()
            if meta.className().startswith("QQuickText"):
                value = child.property("text")
                if value:
                    texts.append(str(value))
    return texts


class TestSummaryPersistence:
    """ITEM 1 : les setters du bridge se reflètent dans les Text RÉELS."""

    def test_summary_displays_all_selected_values(self, qapp: QApplication) -> None:
        bridge = _make_bridge(skip_requirements=True)
        qml_engine, root, _tp = _load(bridge)
        try:
            # Pilote les VRAIS setters (comme le feraient les vues du wizard).
            bridge.setHostname("my-pc")
            bridge.setUsername("alice")
            bridge.setFullName("Alice Doe")
            bridge.setSelectedLocale("fr_FR.UTF-8")
            bridge.setSelectedTimezone("Europe/Paris")
            bridge.setSelectedKeymap("fr")
            bridge.setSelectedDisk("/dev/sda")
            bridge.setPartitionMode("auto")
            bridge.setFilesystem("btrfs")

            root.setProperty("currentStep", 6)  # Summary
            qapp.processEvents()
            qapp.processEvents()

            summary = _find_summary(root)
            assert summary is not None, "SummaryView introuvable dans l'arbre QML"
            texts = _collect_texts(summary)

            # Preuve runtime : chaque valeur saisie apparaît dans un Text réel.
            for expected in (
                "my-pc",
                "alice",
                "Alice Doe",
                "fr_FR.UTF-8",
                "Europe/Paris",
                "fr",
                "/dev/sda > BTRFS",
            ):
                assert any(expected in t for t in texts), (
                    f"Valeur '{expected}' absente du résumé rendu : {texts}"
                )

            # Aucun champ ne doit rester sur le placeholder « Not set ».
            not_set = [t for t in texts if "Not set" in t or "Non défini" in t]
            assert not not_set, f"Champs non renseignés dans le résumé : {not_set}"
        finally:
            del qml_engine

    def test_summary_updates_after_navigation(self, qapp: QApplication) -> None:
        """Un changement de setter APRÈS affichage du résumé se propage."""
        bridge = _make_bridge(skip_requirements=True)
        qml_engine, root, _tp = _load(bridge)
        try:
            bridge.setUsername("bob")
            root.setProperty("currentStep", 6)
            qapp.processEvents()

            summary = _find_summary(root)
            assert summary is not None
            assert any("bob" in t for t in _collect_texts(summary))

            # Modifie la sélection alors que le résumé est déjà affiché.
            bridge.setUsername("charlie")
            qapp.processEvents()
            texts = _collect_texts(summary)
            assert any("charlie" in t for t in texts)
            assert not any(t == "bob" for t in texts)
        finally:
            del qml_engine


class TestSummaryConfirmationGate:
    """ITEM 2 : la CheckBox de confirmation arme l'installation."""

    def test_confirmed_defaults_false(self, qapp: QApplication) -> None:
        assert qapp is not None
        bridge = _make_bridge(skip_requirements=False)
        qml_engine, _root, _tp = _load(bridge)
        try:
            assert bridge.confirmed is False
        finally:
            del qml_engine

    def test_gate_blocks_until_confirmed(self, qapp: QApplication) -> None:
        """Le bouton Install (enabled: canProceedToNext) suit l'état confirmé.

        Sélections minimales complètes (locale/disque) pour que seule
        la confirmation manque ; on observe `enabled` du bouton footer.
        """
        bridge = _make_bridge(skip_requirements=False)
        qml_engine, root, _tp = _load(bridge)
        try:
            # Renseigne les prérequis des étapes 1-3 (hors confirmation).
            bridge.setSelectedLocale("fr_FR.UTF-8")
            bridge.setSelectedTimezone("Europe/Paris")
            bridge.setSelectedKeymap("fr")
            bridge.setSelectedDisk("/dev/sda")

            root.setProperty("currentStep", 6)  # Summary
            qapp.processEvents()

            button = _find_by_object_name(root, "nextInstallButton")
            assert button is not None, "Bouton Install introuvable"

            # Non confirmé -> bouton Install désactivé.
            assert bridge.confirmed is False
            assert button.property("enabled") is False

            # Confirmé -> bouton Install activé.
            bridge.setConfirmed(True)
            qapp.processEvents()
            assert button.property("enabled") is True

            # Désarmé -> re-désactivé.
            bridge.setConfirmed(False)
            qapp.processEvents()
            assert button.property("enabled") is False
        finally:
            del qml_engine

    def test_checkbox_toggle_arms_bridge(self, qapp: QApplication) -> None:
        """Cocher la CheckBox émet confirmedToggled -> engine.setConfirmed(true)."""
        bridge = _make_bridge(skip_requirements=False)
        qml_engine, root, _tp = _load(bridge)
        try:
            root.setProperty("currentStep", 6)
            qapp.processEvents()

            summary = _find_summary(root)
            assert summary is not None

            # Émet le signal de confirmation comme le ferait la CheckBox cochée.
            summary.confirmedToggled.emit(True)  # type: ignore[attr-defined]
            qapp.processEvents()
            assert bridge.confirmed is True

            summary.confirmedToggled.emit(False)  # type: ignore[attr-defined]
            qapp.processEvents()
            assert bridge.confirmed is False
        finally:
            del qml_engine


def _visible_texts(root: QObject) -> list[str]:
    from PySide6.QtQuick import QQuickItem

    texts: list[str] = []
    stack: list[QObject] = [root]
    seen: set[int] = set()
    refs: list[QObject] = []
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        refs.append(obj)
        if isinstance(obj, QQuickItem) and not obj.isVisible():
            continue
        if obj.metaObject().className().startswith("QQuickText"):
            value = obj.property("text")
            if value:
                texts.append(str(value))
        stack.extend(obj.children())
        if isinstance(obj, QQuickItem):
            stack.extend(obj.childItems())
    return texts


def _three_partition_disk() -> dict:
    return finalize_mock_disk({
        "name": "sda", "size": "500 GiB", "sizeBytes": 500 * 1024**3,
        "partitions": [
            {"name": "sda1", "fstype": "vfat", "partType": "efi", "sizeBytes": 512 * 1024**2,
             "partTypeGuid": disk_detector.ESP_TYPE_GUID},
            {"name": "sda2", "fstype": "ext4", "partType": "linux", "sizeBytes": 100 * 1024**3},
            {"name": "sda3", "fstype": "ext4", "partType": "linux", "sizeBytes": 100 * 1024**3},
        ],
    })


class TestSummaryPartitionModes:
    def test_manual_mode_shows_mount_plan_not_auto_options(self, qapp: QApplication) -> None:
        bridge = _make_bridge(skip_requirements=True)
        bridge._on_disk_scan_finished([_three_partition_disk()])
        qml_engine, root, _tp = _load(bridge)
        try:
            bridge.setSelectedDisk("sda")
            bridge.setPartitionMode("manual")
            bridge.setFilesystem("btrfs")
            bridge.setManualRow(json.dumps(
                {"kind": "existing", "number": 1, "action": "keep", "mountpoint": "/boot/efi"}))
            bridge.setManualRow(json.dumps(
                {"kind": "existing", "number": 2, "action": "format", "fstype": "ext4", "mountpoint": "/"}))
            root.setProperty("currentStep", 6)
            qapp.processEvents()

            summary = _find_summary(root)
            texts = _visible_texts(summary)
            assert "/dev/sda2" in texts and any(t.startswith("/ ") for t in texts), texts
            assert "/dev/sda1" in texts, texts
            assert "/dev/sda3" not in texts, texts
            assert "Manual" in texts, texts
            assert not any("BTRFS" in t for t in texts), texts
            assert not any(t == "Encryption:" for t in texts), texts
            assert not any("will be modified and erased" in t for t in texts), texts
        finally:
            del qml_engine

    def test_alongside_mode_shows_shrink_sentence(self, qapp: QApplication) -> None:
        bridge = _make_bridge(skip_requirements=True)
        bridge._on_disk_scan_finished(mock_disks())
        qml_engine, root, _tp = _load(bridge)
        try:
            bridge.setSelectedDisk("nvme1n1")
            bridge.setPartitionMode("alongside")
            root.setProperty("currentStep", 6)
            qapp.processEvents()

            texts = _visible_texts(_find_summary(root))
            assert "Install alongside" in texts, texts
            assert any("/dev/nvme1n1p3 will be shrunk to" in t for t in texts), texts
            assert any(t == "Encryption:" for t in texts), texts
        finally:
            del qml_engine

    def test_erase_mode_says_erase_disk(self, qapp: QApplication) -> None:
        bridge = _make_bridge(skip_requirements=True)
        bridge._on_disk_scan_finished(mock_disks())
        qml_engine, root, _tp = _load(bridge)
        try:
            bridge.setSelectedDisk("nvme0n1")
            root.setProperty("currentStep", 6)
            qapp.processEvents()

            texts = _visible_texts(_find_summary(root))
            assert "Erase disk" in texts, texts
            assert any("will be modified and erased" in t for t in texts), texts
        finally:
            del qml_engine

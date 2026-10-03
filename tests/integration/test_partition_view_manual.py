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
from xen.jobs.bootloader import BootloaderJob  # noqa: E402
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

    Dedup IS required: a plain item's children() and childItems() largely
    overlap (QObject parent == visual parent for ordinary items), so every
    shared node gets pushed from both paths. Without a visited guard this
    double-push compounds per level of depth, and controls that hold a
    reference to a single shared, page-wide object (e.g. a TextField's
    context menu living under the window's Overlay) turn that into a
    combinatorial blow-up - observed hanging for minutes/never finishing
    on a "not found" search that must exhaust the whole tree.

    This previously had no dedup: a bare id()-keyed set was tried and
    reverted, because PySide mints a fresh Python wrapper per attribute
    access, so id() (a memory address in CPython) can be reused for an
    unrelated object once an earlier short-lived wrapper is GC'd mid-walk,
    causing real, unrelated items to be falsely skipped. Keeping a strong
    reference to every visited wrapper in `visited_refs` keeps each one
    alive for the rest of the walk, so its id() cannot be recycled - the
    id()-reuse hazard is gone and the dedup is safe.
    """
    stack: list[Any] = [root]
    visited_ids: set[int] = set()
    visited_refs: list[Any] = []
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in visited_ids:
            continue
        visited_ids.add(oid)
        visited_refs.append(obj)
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

    def test_edit_dialog_format_op_includes_mountpoint(
        self, bridge: EngineBridge, qapp: QApplication
    ) -> None:
        """Regression: editDialog.confirm()'s format op never included
        mountpoint in its params at all (mount assignment for an existing
        partition goes through the separate, immediate
        engine.setPartitionMount() call instead) - leaving backend logic
        that reads op.params.get("mountpoint") on a format op (Task 2's
        non-root-passphrase rule, Task 8's root-required-for-non-root-
        encryption rule) always blind for any op the Edit dialog queues.
        Confirms encrypting the actual root via Edit (passphrase field
        shown, since editMountField.text.trim() === "/") now queues a
        format op whose params carry mountpoint == "/"."""
        root_size = DISK_SECTORS - _ALIGN - 34
        seg = {
            "kind": "existing",
            "name": "sda2",
            "startSector": _ALIGN,
            "sizeSectors": root_size,
            "sizeBytes": root_size * _SECTOR_SIZE,
            "fstype": "ext4",
            "partType": "linux",
            "mountpoint": "/",
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

            edit_dialog.setProperty("doFormat", True)
            qapp.processEvents()

            mount_field = _find_by_object_name(editor, "editMountField")
            mount_field.setProperty("text", "/")
            qapp.processEvents()

            encrypt_check = _find_by_object_name(editor, "editEncryptCheck")
            assert encrypt_check is not None, "editEncryptCheck not found"
            encrypt_check.setProperty("checked", True)
            qapp.processEvents()

            pass_field = _find_by_object_name(editor, "editEncryptPassField")
            assert pass_field is not None, "editEncryptPassField not found"
            pass_field.setProperty("text", "S3cretRootPass!")
            confirm_field = _find_by_object_name(editor, "editEncryptPassConfirmField")
            confirm_field.setProperty("text", "S3cretRootPass!")
            qapp.processEvents()

            QMetaObject.invokeMethod(edit_dialog, "confirm")
            qapp.processEvents()

            ops = [op for op in bridge._partition_operations if op.get("type") == "format"]
            assert len(ops) == 1, f"expected exactly one queued format op, got {ops}"
            params = ops[0]["params"]
            assert params.get("mountpoint") == "/"
            assert params.get("encrypt") is True
            assert params.get("encryption_passphrase") == "S3cretRootPass!"
        finally:
            del qml_engine


def test_grub_encryption_note_visibility(bridge: EngineBridge, qapp: QApplication) -> None:
    """The GRUB/Argon2id note shows only when both encryption is on AND
    bootloader is grub - hidden in every other combination.

    Two things the obvious version of this test gets wrong:

    - The encryption/swap/EFI details block (grubKdfNote's ancestor) is only
      shown once a disk is selected, so `setSelectedDisk("sda")` is required
      - without it, note.property("visible") reads False regardless of the
      encryption/bootloader state, since QQuickItem.visible reports
      *effective* (ancestor-combined) visibility, not just the item's own
      binding.
    - engine.bootloaderType is declared `constant=True` (Task 3): QML reads
      it once per binding and never re-polls it on its own, since it's fixed
      by the distro config and never changes at runtime. So the grub case
      below needs its own fresh engine/bridge load with the job config set
      to "grub" BEFORE the QML tree is built - mutating it after the first
      engine has already loaded has no effect on that engine's
      already-evaluated (and never-re-read) binding. (XEROLINUX_CONFIG's own
      bootloader default is already "grub", so the first load explicitly
      overrides it to "systemd-boot" to exercise the "encryption on, but not
      grub" case meaningfully.)
    """
    bridge._disks_model = [_mock_disk()]
    for job in bridge._engine.jobs:
        if isinstance(job, BootloaderJob):
            job._config["type"] = "systemd-boot"

    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        bridge.setSelectedDisk("sda")
        qapp.processEvents()

        note = _find_by_object_name(root, "grubKdfNote")
        assert note is not None, "grubKdfNote Text not found in the QML tree"

        # Neither encryption nor grub: hidden.
        assert bool(note.property("visible")) is False

        # Encryption on, but bootloader is systemd-boot: still hidden.
        bridge.setEncryption(True)
        qapp.processEvents()
        assert bool(note.property("visible")) is False

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine

    # Encryption on AND grub: visible. Fresh engine load, set up before
    # _load_main() builds the tree (see docstring above).
    for job in bridge._engine.jobs:
        if isinstance(job, BootloaderJob):
            job._config["type"] = "grub"

    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        qapp.processEvents()

        note = _find_by_object_name(root, "grubKdfNote")
        assert note is not None, "grubKdfNote Text not found in the QML tree"
        assert bool(note.property("visible")) is True

        # Bootloader is grub, but encryption off: hidden again.
        bridge.setEncryption(False)
        qapp.processEvents()
        assert bool(note.property("visible")) is False

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_encrypt_checkbox_passphrase_visibility(bridge: EngineBridge, qapp: QApplication) -> None:
    """Passphrase fields show only when Mount Point is exactly '/' and
    Encrypt is checked; the checkbox itself is hidden for the ESP."""
    bridge._disks_model = [_mock_disk()]
    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        qapp.processEvents()

        # ManualPartitionEditor itself only lives inside PartitionView's
        # own "manualEditorPopup" Popup - a Popup's content is detached
        # from the window (no window, no Overlay) until it is actually
        # open, so newDialog (nested inside it) can't open either until
        # this outer popup opens first.
        manual_popup = _find_by_object_name(root, "manualEditorPopup")
        assert manual_popup is not None
        QMetaObject.invokeMethod(manual_popup, "open")
        qapp.processEvents()

        new_dialog = _find_by_object_name(root, "newPartitionDialog")
        assert new_dialog is not None

        # A closed Popup's children report effective (ancestor-combined)
        # visibility, which is always False regardless of their own
        # bindings until the popup is actually open (same caveat as
        # test_grub_encryption_note_visibility above) - openFor() is the
        # real "New" button's own open path (see the toolbar's New
        # PartitionActionButton), invoked directly here the same way
        # test_edit_dialog_ok_commits_mount_point drives editDialog.
        free_seg = {
            "kind": "free",
            "startSector": _ALIGN,
            "sizeSectors": DISK_SECTORS - _ALIGN - 34,
            "sizeBytes": (DISK_SECTORS - _ALIGN - 34) * _SECTOR_SIZE,
        }
        QMetaObject.invokeMethod(new_dialog, "openFor", Q_ARG("QVariant", free_seg))
        qapp.processEvents()

        encrypt_check = _find_by_object_name(new_dialog, "newEncryptCheck")
        passphrase_field = _find_by_object_name(new_dialog, "newEncryptPassField")
        assert encrypt_check is not None
        assert passphrase_field is not None

        # Unchecked: passphrase hidden regardless of mount point.
        assert bool(passphrase_field.property("visible")) is False

        encrypt_check.setProperty("checked", True)
        qapp.processEvents()
        # Mount Point field defaults empty (not "/") - still hidden.
        assert bool(passphrase_field.property("visible")) is False

        mount_field = _find_by_object_name(new_dialog, "newMountField")
        mount_field.setProperty("text", "/")
        qapp.processEvents()
        assert bool(passphrase_field.property("visible")) is True

        mount_field.setProperty("text", "/home")
        qapp.processEvents()
        assert bool(passphrase_field.property("visible")) is False

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_encrypt_checkbox_hidden_for_esp(bridge: EngineBridge, qapp: QApplication) -> None:
    """The encrypt option must never render when formatting as vfat with
    the esp flag - GRUB/systemd-boot both need to read the ESP in the clear.

    newDialog is a Popup nested inside PartitionView's own
    "manualEditorPopup" Popup (same caveat as
    test_encrypt_checkbox_passphrase_visibility above): a closed Popup's
    children always report effective visibility False regardless of their
    own bindings, so both outer and inner popups must actually be opened
    for a `visible` assertion to mean anything. Likewise, setting
    newFsCombo.currentIndex via setProperty alone does NOT update
    newDialog.fsChoice - only the real `onActivated` handler does that -
    so the "activated" signal is invoked directly here to drive fsChoice
    the way a real click would.
    """
    bridge._disks_model = [_mock_disk()]
    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        qapp.processEvents()

        manual_popup = _find_by_object_name(root, "manualEditorPopup")
        assert manual_popup is not None
        QMetaObject.invokeMethod(manual_popup, "open")
        qapp.processEvents()

        new_dialog = _find_by_object_name(root, "newPartitionDialog")
        assert new_dialog is not None

        free_seg = {
            "kind": "free",
            "startSector": _ALIGN,
            "sizeSectors": DISK_SECTORS - _ALIGN - 34,
            "sizeBytes": (DISK_SECTORS - _ALIGN - 34) * _SECTOR_SIZE,
        }
        QMetaObject.invokeMethod(new_dialog, "openFor", Q_ARG("QVariant", free_seg))
        qapp.processEvents()

        fs_combo = _find_by_object_name(new_dialog, "newFsCombo")
        encrypt_row = _find_by_object_name(new_dialog, "newEncryptRow")
        assert fs_combo is not None
        assert encrypt_row is not None

        # Default fstype (not fat32): encrypt option visible.
        assert bool(encrypt_row.property("visible")) is True

        # Select "fat32" explicitly by value rather than index (index can
        # shift based on online/offline btrfs gating) - find it in the model.
        model = list(fs_combo.property("model"))
        fat32_index = model.index("fat32")
        fs_combo.setProperty("currentIndex", fat32_index)
        QMetaObject.invokeMethod(fs_combo, "activated", Q_ARG("int", fat32_index))
        qapp.processEvents()
        assert bool(encrypt_row.property("visible")) is False

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_encrypt_checkbox_hidden_for_esp_edit_dialog(bridge: EngineBridge, qapp: QApplication) -> None:
    """Same ESP exclusion as test_encrypt_checkbox_hidden_for_esp above, but
    for editDialog's mirrored editEncryptRow/editFsCombo - the Edit dialog's
    gating is structurally identical to the New dialog's (same fsChoice
    !== "fat32" binding) but was previously unverified by any test.

    editDialog.openFor() needs an *existing* partition segment (not a free
    region like newDialog.openFor()), following the same seg/disk shape
    used by test_edit_dialog_ok_commits_mount_point.
    """
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
    qml_engine, root, collector = _load_main(bridge)
    try:
        root.setProperty("currentStep", 4)
        bridge.setSelectedDisk("sda")
        bridge.setPartitionMode("manual")
        qapp.processEvents()

        manual_popup = _find_by_object_name(root, "manualEditorPopup")
        assert manual_popup is not None
        QMetaObject.invokeMethod(manual_popup, "open")
        qapp.processEvents()

        edit_dialog = _find_by_object_name(root, "editPartitionDialog")
        assert edit_dialog is not None
        QMetaObject.invokeMethod(edit_dialog, "openFor", Q_ARG("QVariant", seg))
        qapp.processEvents()

        # editFsCombo/editEncryptRow live inside the "Format" content
        # radio's ColumnLayout (visible: editDialog.doFormat) - "Keep" is
        # the default, so Format must be selected first or both read as
        # invisible regardless of fsChoice.
        edit_dialog.setProperty("doFormat", True)
        qapp.processEvents()

        fs_combo = _find_by_object_name(edit_dialog, "editFsCombo")
        encrypt_row = _find_by_object_name(edit_dialog, "editEncryptRow")
        assert fs_combo is not None
        assert encrypt_row is not None

        # Default fstype (ext4, not fat32): encrypt option visible.
        assert bool(encrypt_row.property("visible")) is True

        # Select "fat32" explicitly by value rather than index, and via the
        # real "activated" signal (not just setProperty(currentIndex)) -
        # same rationale as test_encrypt_checkbox_hidden_for_esp above:
        # only onActivated updates editDialog.fsChoice.
        model = list(fs_combo.property("model"))
        fat32_index = model.index("fat32")
        fs_combo.setProperty("currentIndex", fat32_index)
        QMetaObject.invokeMethod(fs_combo, "activated", Q_ARG("int", fat32_index))
        qapp.processEvents()
        assert bool(encrypt_row.property("visible")) is False

        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def _free_seg() -> dict[str, Any]:
    return {
        "kind": "free",
        "startSector": _ALIGN,
        "sizeSectors": DISK_SECTORS - _ALIGN - 34,
        "sizeBytes": (DISK_SECTORS - _ALIGN - 34) * _SECTOR_SIZE,
    }


def _existing_seg() -> dict[str, Any]:
    size = DISK_SECTORS - _ALIGN - 34
    return {
        "kind": "existing", "name": "sda2", "startSector": _ALIGN, "sizeSectors": size,
        "sizeBytes": size * _SECTOR_SIZE, "fstype": "ext4", "partType": "linux",
        "mountpoint": "", "pendingDelete": False, "number": 2,
        "minSizeSectors": _ALIGN, "freeAfterSectors": 0,
    }


def _existing_disk() -> dict[str, Any]:
    seg = _existing_seg()
    return {
        "name": "sda", "sizeBytes": 500 * 1024**3, "sizeSectors": DISK_SECTORS,
        "segments": [{
            "kind": "partition", "name": "sda2", "startSector": seg["startSector"],
            "sizeSectors": seg["sizeSectors"], "sizeBytes": seg["sizeBytes"],
            "fstype": "ext4", "partType": "linux",
        }],
    }


def _open_manual_editor(bridge: EngineBridge, qapp: QApplication) -> tuple[Any, Any, Any]:
    qml_engine, root, collector = _load_main(bridge)
    root.setProperty("currentStep", 4)
    bridge.setSelectedDisk("sda")
    bridge.setPartitionMode("manual")
    qapp.processEvents()
    manual_popup = _find_by_object_name(root, "manualEditorPopup")
    QMetaObject.invokeMethod(manual_popup, "open")
    qapp.processEvents()
    return qml_engine, root, collector


def _select_fs(dialog: Any, combo_name: str, fs: str, qapp: QApplication) -> None:
    combo = _find_by_object_name(dialog, combo_name)
    index = list(combo.property("model")).index(fs)
    combo.setProperty("currentIndex", index)
    QMetaObject.invokeMethod(combo, "activated", Q_ARG("int", index))
    qapp.processEvents()


def test_new_dialog_gates_create_on_root_passphrase(bridge: EngineBridge, qapp: QApplication) -> None:
    """Critical #2: length >= 8 and confirm must match before Create."""
    bridge._disks_model = [_mock_disk()]
    qml_engine, root, collector = _open_manual_editor(bridge, qapp)
    try:
        dialog = _find_by_object_name(root, "newPartitionDialog")
        QMetaObject.invokeMethod(dialog, "openFor", Q_ARG("QVariant", _free_seg()))
        qapp.processEvents()
        create = _find_by_object_name(dialog, "newCreateButton")
        _find_by_object_name(dialog, "newMountField").setProperty("text", "/")
        _find_by_object_name(dialog, "newEncryptCheck").setProperty("checked", True)
        pass_field = _find_by_object_name(dialog, "newEncryptPassField")
        confirm_field = _find_by_object_name(dialog, "newEncryptPassConfirmField")
        qapp.processEvents()
        assert bool(create.property("enabled")) is False

        pass_field.setProperty("text", "short")
        confirm_field.setProperty("text", "short")
        qapp.processEvents()
        assert bool(create.property("enabled")) is False

        pass_field.setProperty("text", "S3cretRootPass!")
        confirm_field.setProperty("text", "S3cretRootPass?")
        qapp.processEvents()
        assert bool(create.property("enabled")) is False
        QMetaObject.invokeMethod(dialog, "confirm")
        qapp.processEvents()
        assert bridge._partition_operations == []

        confirm_field.setProperty("text", "S3cretRootPass!")
        qapp.processEvents()
        assert bool(create.property("enabled")) is True
        QMetaObject.invokeMethod(dialog, "confirm")
        qapp.processEvents()
        assert bridge._partition_operations[0]["params"]["encryption_passphrase"] == "S3cretRootPass!"
        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_new_dialog_btrfs_subvolume_root_asks_for_passphrase(
    bridge: EngineBridge, qapp: QApplication
) -> None:
    """Critical #3: default btrfs layout (root at '@'), Mount Point blank."""
    bridge._disks_model = [_mock_disk()]
    qml_engine, root, collector = _open_manual_editor(bridge, qapp)
    try:
        dialog = _find_by_object_name(root, "newPartitionDialog")
        QMetaObject.invokeMethod(dialog, "openFor", Q_ARG("QVariant", _free_seg()))
        qapp.processEvents()
        _select_fs(dialog, "newFsCombo", "btrfs", qapp)
        _find_by_object_name(dialog, "newEncryptCheck").setProperty("checked", True)
        qapp.processEvents()

        pass_field = _find_by_object_name(dialog, "newEncryptPassField")
        assert bool(dialog.property("isRoot")) is True
        assert bool(pass_field.property("visible")) is True
        assert bool(_find_by_object_name(dialog, "newCreateButton").property("enabled")) is False

        pass_field.setProperty("text", "S3cretRootPass!")
        _find_by_object_name(dialog, "newEncryptPassConfirmField").setProperty("text", "S3cretRootPass!")
        qapp.processEvents()
        QMetaObject.invokeMethod(dialog, "confirm")
        qapp.processEvents()
        params = bridge._partition_operations[0]["params"]
        assert "mountpoint" not in params
        assert params["encryption_passphrase"] == "S3cretRootPass!"
        assert bridge.operationsApplicable is True, bridge.operationsApplicableError
        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_dialogs_reset_encryption_state_on_open(bridge: EngineBridge, qapp: QApplication) -> None:
    """Important #2: a checked Encrypt or typed passphrase never carries over."""
    bridge._disks_model = [_existing_disk()]
    qml_engine, root, collector = _open_manual_editor(bridge, qapp)
    try:
        new_dialog = _find_by_object_name(root, "newPartitionDialog")
        QMetaObject.invokeMethod(new_dialog, "openFor", Q_ARG("QVariant", _free_seg()))
        qapp.processEvents()
        new_check = _find_by_object_name(new_dialog, "newEncryptCheck")
        new_pass = _find_by_object_name(new_dialog, "newEncryptPassField")
        new_check.setProperty("checked", True)
        new_pass.setProperty("text", "S3cretRootPass!")
        QMetaObject.invokeMethod(new_dialog, "close")
        qapp.processEvents()
        assert new_pass.property("text") == "", "root passphrase must not linger after close"
        QMetaObject.invokeMethod(new_dialog, "openFor", Q_ARG("QVariant", _free_seg()))
        qapp.processEvents()
        assert bool(new_check.property("checked")) is False
        assert new_pass.property("text") == ""

        edit_dialog = _find_by_object_name(root, "editPartitionDialog")
        QMetaObject.invokeMethod(edit_dialog, "openFor", Q_ARG("QVariant", _existing_seg()))
        qapp.processEvents()
        edit_check = _find_by_object_name(edit_dialog, "editEncryptCheck")
        edit_confirm = _find_by_object_name(edit_dialog, "editEncryptPassConfirmField")
        edit_check.setProperty("checked", True)
        edit_confirm.setProperty("text", "S3cretRootPass!")
        QMetaObject.invokeMethod(edit_dialog, "close")
        QMetaObject.invokeMethod(edit_dialog, "openFor", Q_ARG("QVariant", _existing_seg()))
        qapp.processEvents()
        assert bool(edit_check.property("checked")) is False
        assert edit_confirm.property("text") == ""
        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine


def test_edit_dialog_replaces_queued_format_op(bridge: EngineBridge, qapp: QApplication) -> None:
    bridge._disks_model = [_existing_disk()]
    qml_engine, root, collector = _open_manual_editor(bridge, qapp)
    try:
        edit_dialog = _find_by_object_name(root, "editPartitionDialog")
        for fs in ("ext4", "xfs"):
            QMetaObject.invokeMethod(edit_dialog, "openFor", Q_ARG("QVariant", _existing_seg()))
            qapp.processEvents()
            edit_dialog.setProperty("doFormat", True)
            qapp.processEvents()
            _select_fs(edit_dialog, "editFsCombo", fs, qapp)
            QMetaObject.invokeMethod(edit_dialog, "confirm")
            qapp.processEvents()
        formats = [op for op in bridge._partition_operations if op["type"] == "format"]
        assert [op["params"]["fstype"] for op in formats] == ["xfs"]
        assert not collector.errors, f"QML errors: {collector.errors}"
    finally:
        qInstallMessageHandler(None)
        del qml_engine

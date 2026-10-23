"""
Unit tests for EngineBridge selection/state handling: internet status,
keyboard-variant defaults, the confirmation gate, manual-mode filesystem,
installation reset, config-driven defaults and log-handler lifetime.

Runs offscreen; connectivity checks and subprocesses are patched.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import shiboken6  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.mock_disks import mock_disks  # noqa: E402
from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CONFIG = PROJECT_ROOT / "config" / "examples" / "minimal.yaml"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def _make_bridge(config: Path, has_internet: bool = True) -> EngineBridge:
    engine = Engine.from_config_file(config)
    with patch(
        "xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=has_internet
    ):
        return EngineBridge(
            engine, config.parent, debug=False, dry_run=True, skip_requirements=True
        )


@pytest.fixture
def bridge(qapp: QApplication) -> Iterator[EngineBridge]:
    assert qapp is not None
    obj = _make_bridge(MINIMAL_CONFIG)
    yield obj
    logging.getLogger("xen").removeHandler(obj._log_handler)


def _wait_for(predicate, timeout_s: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _write_config(tmp_path: Path, partition: str = "", locale: str = "") -> Path:
    config = tmp_path / "xen.yaml"
    jobs = ["  - welcome"]
    jobs.append("  - name: partition\n    config:\n" + partition if partition else "  - partition")
    jobs.append("  - name: locale\n    config:\n" + locale if locale else "  - locale")
    config.write_text('version: "1.0"\nbranding:\n  name: "T"\njobs:\n' + "\n".join(jobs) + "\n")
    return config


class TestInternetStatus:
    def test_recheck_updates_property_selection_and_signal(self, bridge: EngineBridge) -> None:
        assert bridge.hasInternet is True
        received: list[bool] = []
        bridge.internetStatusChanged.connect(received.append)

        with patch(
            "xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=False
        ):
            bridge.recheckInternetStatus()
            assert _wait_for(lambda: received == [False])

        assert bridge.hasInternet is False
        assert bridge._selections["has_internet"] is False

    def test_recheck_does_not_block_the_caller(self, bridge: EngineBridge) -> None:
        import threading

        release = threading.Event()

        def _slow_check(*_a: object, **_k: object) -> bool:
            release.wait(5)
            return True

        with patch("xen.gui.bridge.NetworkHelper.check_internet_connectivity", _slow_check):
            bridge.recheckInternetStatus()
            # Returned while the probe is still blocked.
            assert not release.is_set()
            release.set()
            assert _wait_for(lambda: bridge._internet_check_thread is None)


class TestKeyboardVariantDefaults:
    def test_default_variant_is_empty(self, bridge: EngineBridge) -> None:
        assert bridge.selectedKeyboardVariant == ""

    def test_unknown_layout_falls_back_to_default_variant(self, bridge: EngineBridge) -> None:
        bridge.setSelectedKeymap("zz-unknown")
        assert bridge.keyboardVariantsModel == [""]
        assert bridge.selectedKeyboardVariant == ""

    def test_load_locale_data_populates_variants(self, bridge: EngineBridge) -> None:
        assert bridge.keyboardVariantsModel == []
        bridge.loadLocaleData()
        assert bridge.keyboardVariantsModel[0] == ""
        assert "dvorak" in bridge.keyboardVariantsModel

    def test_malay_locale_uses_us_layout(self, bridge: EngineBridge) -> None:
        bridge.setSelectedLocale("ms_MY.UTF-8")
        assert bridge.selectedKeymap == "us"


class TestConfirmationResets:
    @pytest.fixture
    def armed(self, bridge: EngineBridge) -> EngineBridge:
        bridge._disks_model = [
            {"name": "sda", "sizeSectors": 10**9, "segments": []},
            {"name": "sdb", "sizeSectors": 10**9, "segments": []},
        ]
        bridge.setSelectedDisk("sda")
        bridge.setConfirmed(True)
        assert bridge.confirmed is True
        return bridge

    def test_disk_change(self, armed: EngineBridge) -> None:
        armed.setSelectedDisk("sdb")
        assert armed.confirmed is False

    def test_same_disk_keeps_confirmation(self, armed: EngineBridge) -> None:
        armed.setSelectedDisk("sda")
        assert armed.confirmed is True

    def test_partition_mode_change(self, armed: EngineBridge) -> None:
        armed.setPartitionMode("manual")
        assert armed.confirmed is False

    def test_encryption_change(self, armed: EngineBridge) -> None:
        armed.setEncryption(True)
        assert armed.confirmed is False

    def test_filesystem_change(self, armed: EngineBridge) -> None:
        armed.setFilesystem("btrfs")
        assert armed.confirmed is False

    def test_swap_strategy_change(self, armed: EngineBridge) -> None:
        armed.setSwapStrategy("hibernate")
        assert armed.confirmed is False

    def test_esp_size_change(self, armed: EngineBridge) -> None:
        armed.setEfiSizeMb(1024)
        assert armed.confirmed is False

    def test_encryption_passphrase_change(self, armed: EngineBridge) -> None:
        armed.setEncryptionPassphrase("a new passphrase")
        assert armed.confirmed is False

    def test_manual_row_change(self, armed: EngineBridge) -> None:
        armed.setManualRow('{"kind": "existing", "number": 1, "action": "delete"}')
        assert armed.confirmed is False

    def test_reset_emits_selections_changed(self, armed: EngineBridge) -> None:
        received: list[int] = []
        armed.selectionsChanged.connect(lambda: received.append(1))
        armed.setManualRow('{"kind": "existing", "number": 1, "action": "delete"}')
        assert received


def _root_row(fstype: str, **extra: object) -> str:
    row = {"kind": "new", "ref": "root", "start": 2048, "end": 2048 + 30 * 1024 * 2048 - 1,
           "fstype": fstype, "mountpoint": "/", **extra}  # fmt: skip
    return json.dumps(row)


class TestManualModeFilesystem:
    def _manual(self, bridge: EngineBridge) -> EngineBridge:
        bridge._on_disk_scan_finished(mock_disks())
        bridge.setSelectedDisk("nvme0n1")
        bridge.setPartitionMode("manual")
        bridge.setFilesystem("xfs")
        return bridge

    def test_filesystem_follows_formatted_root(self, bridge: EngineBridge) -> None:
        self._manual(bridge)
        bridge.setManualRow(_root_row("ext4"))
        bridge.applySelectionsToContext()
        assert bridge._engine._selections["filesystem"] == "ext4"

    def test_filesystem_follows_subvolume_root(self, bridge: EngineBridge) -> None:
        self._manual(bridge)
        bridge.setManualRow(_root_row("btrfs", subvolumes=[{"name": "@", "mountpoint": "/"}]))
        bridge.applySelectionsToContext()
        assert bridge._engine._selections["filesystem"] == "btrfs"

    def test_filesystem_falls_back_without_root(self, bridge: EngineBridge) -> None:
        self._manual(bridge)
        bridge.applySelectionsToContext()
        assert bridge._engine._selections["filesystem"] == "xfs"

    def test_auto_mode_keeps_selected_filesystem(self, bridge: EngineBridge) -> None:
        bridge.setFilesystem("ext4")
        bridge.applySelectionsToContext()
        assert bridge._engine._selections["filesystem"] == "ext4"


class TestResetInstallation:
    def test_reset_clears_progress_state(self, bridge: EngineBridge) -> None:
        bridge._on_job_start(bridge._engine.get_job_names()[0])
        bridge._engine.state.current_job_index = 2
        bridge._overall_progress = 42
        bridge._current_job_message = "copying"
        bridge._install_start_time = 1.0
        bridge._install_end_time = 2.0
        bridge._error_message = "boom"

        bridge.resetInstallation()

        assert bridge._engine.state.current_job_index == -1
        assert bridge.overallProgress == 0
        assert bridge._current_job_name == ""
        assert bridge.currentJobMessage == ""
        assert bridge._install_start_time is None
        assert bridge._install_end_time is None
        assert bridge.errorMessage == ""


class TestFinishActions:
    def test_power_actions_use_a_timeout(self, qapp: QApplication) -> None:
        engine = Engine.from_config_file(MINIMAL_CONFIG)
        with patch("xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=True):
            live = EngineBridge(engine, MINIMAL_CONFIG.parent, dry_run=False)
        try:
            with patch("xen.gui.bridge.subprocess.run") as run:
                live.executeFinishAction("reboot")
                live.executeFinishAction("shutdown")
            assert run.call_count == 2
            for call in run.call_args_list:
                assert call.kwargs.get("timeout")
        finally:
            logging.getLogger("xen").removeHandler(live._log_handler)

    def test_power_action_timeout_does_not_raise(self, qapp: QApplication) -> None:
        engine = Engine.from_config_file(MINIMAL_CONFIG)
        with patch("xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=True):
            live = EngineBridge(engine, MINIMAL_CONFIG.parent, dry_run=False)
        try:
            with patch(
                "xen.gui.bridge.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["systemctl"], 30),
            ):
                live.executeFinishAction("reboot")
        finally:
            logging.getLogger("xen").removeHandler(live._log_handler)


class TestConfigDefaults:
    def test_partition_config_drives_filesystem_and_flags(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        config = _write_config(
            tmp_path,
            partition=(
                "      default_filesystem: ext4\n"
                "      allow_encryption: false\n"
                "      allow_manual_partitioning: false\n"
            ),
        )
        obj = _make_bridge(config)
        try:
            assert obj.filesystem == "ext4"
            assert obj.allowEncryption is False
            assert obj.allowManualPartitioning is False
        finally:
            logging.getLogger("xen").removeHandler(obj._log_handler)

    def test_partition_flags_default_true(self, bridge: EngineBridge) -> None:
        assert bridge.allowEncryption is True
        assert bridge.allowManualPartitioning is True

    def test_locale_config_drives_initial_locale_and_timezone(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        config = _write_config(
            tmp_path,
            locale='      default_language: "de_DE"\n      default_timezone: "Europe/Berlin"\n',
        )
        obj = _make_bridge(config)
        try:
            obj.loadLocaleData()
            assert obj.selectedLocale == "de_DE.UTF-8"
            assert obj.selectedTimezone == "Europe/Berlin"
            assert obj.selectedKeymap == "de"
        finally:
            logging.getLogger("xen").removeHandler(obj._log_handler)


class TestPackageSelections:
    def test_package_selection_is_exposed_and_notified(self, bridge: EngineBridge) -> None:
        received: list[int] = []
        bridge.packageSelectionChanged.connect(lambda: received.append(1))
        bridge.setPackageSelected("graphics", "gimp", True)
        assert bridge.packageSelections == {"graphics:gimp": True}
        assert received


class TestLogHandlerLifetime:
    def test_handler_detached_when_bridge_destroyed(self, qapp: QApplication) -> None:
        obj = _make_bridge(MINIMAL_CONFIG)
        handler = obj._log_handler
        assert handler in logging.getLogger("xen").handlers
        shiboken6.delete(obj)
        assert handler not in logging.getLogger("xen").handlers

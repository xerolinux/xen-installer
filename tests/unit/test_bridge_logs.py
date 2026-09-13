"""
Unit tests for the EngineBridge logs/diagnostics surface.

Covers:
- ``installationLog`` / ``logChanged`` / ``logMessageAppended`` reflecting
  what is captured by the ``BridgeLogHandler`` installed on the ``xen`` and
  root loggers.
- Secrets registered via ``setPassword`` / ``setRootPassword`` /
  ``setEncryptionPassphrase`` are never observable in ``installationLog``,
  even when logged verbatim by application code after being set.
- ``uploadInstallLog`` emits ``logUploadFinished(url, ok, error_message)`` on
  both success and failure, driven through a real (offscreen) QThread with
  ``upload_log`` monkeypatched so no network call is made.

Runs offscreen; no real network access is performed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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


@pytest.fixture
def bridge(qapp: QApplication) -> Iterator[EngineBridge]:
    assert qapp is not None
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    theme_base = MINIMAL_CONFIG.parent
    obj = EngineBridge(engine, theme_base, debug=False, dry_run=True, skip_requirements=True)
    yield obj
    # Detach the handler installed on shared loggers so subsequent tests /
    # bridge instances in the same process don't accumulate handlers.
    logging.getLogger("xen").removeHandler(obj._log_handler)
    logging.getLogger().removeHandler(obj._log_handler)


class TestInstallationLogProperty:
    """installationLog / logTail / throttled logChanged reflect captured logs."""

    def test_logged_message_appears_in_installation_log(self, bridge: EngineBridge) -> None:
        logger = logging.getLogger("xen.test_bridge_logs")
        logger.info("hello from a job")

        assert "hello from a job" in bridge.installationLog

    def test_logged_line_appears_in_log_tail(self, bridge: EngineBridge) -> None:
        logger = logging.getLogger("xen.test_bridge_logs")
        logger.info("a distinct live line")

        assert "a distinct live line" in bridge.logTail

    def test_flush_emits_log_changed_when_dirty(self, bridge: EngineBridge) -> None:
        # Live refresh is throttled: logging flags the buffer dirty; the
        # coalescing timer (here driven manually) emits a single logChanged.
        changed = []
        bridge.logChanged.connect(lambda: changed.append(True))

        logger = logging.getLogger("xen.test_bridge_logs")
        logger.info("triggers logChanged")
        bridge._flush_log()

        assert changed

    def test_flush_is_noop_when_no_new_lines(self, bridge: EngineBridge) -> None:
        bridge._flush_log()  # drain any pending dirty state from setup
        changed = []
        bridge.logChanged.connect(lambda: changed.append(True))

        bridge._flush_log()

        assert not changed


class TestJobFailureLogging:
    """Regression: a failing job reached the UI's error card but never the
    captured log (engine.logTail / the log sent to termbin), because
    _on_error/_on_installation_error only emitted Qt signals without ever
    calling logger.error - the one thing BridgeLogHandler actually captures."""

    def test_job_failure_appears_in_log_tail(self, bridge: EngineBridge) -> None:
        bridge._on_error("systemconfig", "mkinitcpio -P failed: something broke")

        assert "systemconfig" in bridge.logTail
        assert "mkinitcpio -P failed: something broke" in bridge.logTail

    def test_installation_error_appears_in_log_tail(self, bridge: EngineBridge) -> None:
        bridge._on_installation_error("worker thread crashed")

        assert "worker thread crashed" in bridge.logTail


class TestSecretsNeverLeakIntoLog:
    """Anti-regression: password/passphrase values must never reach the log."""

    def test_password_never_appears_in_installation_log(self, bridge: EngineBridge) -> None:
        secret = "CorrectHorseBatteryStaple"
        bridge.setPassword(secret)

        logger = logging.getLogger("xen.test_bridge_logs")
        logger.info("user password set to %s during setup", secret)

        assert secret not in bridge.installationLog
        assert "***" in bridge.installationLog

    def test_root_password_never_appears_in_installation_log(self, bridge: EngineBridge) -> None:
        secret = "R00tSecretValue"
        bridge.setRootPassword(secret)

        logger = logging.getLogger("xen.test_bridge_logs")
        logger.info("root password: %s", secret)

        assert secret not in bridge.installationLog

    def test_encryption_passphrase_never_appears_in_installation_log(
        self, bridge: EngineBridge
    ) -> None:
        secret = "LuksPassphrase!2024"
        bridge.setEncryptionPassphrase(secret)

        logger = logging.getLogger("xen.test_bridge_logs")
        logger.info("encryption passphrase in use: %s", secret)

        assert secret not in bridge.installationLog

    def test_apply_selections_registers_secrets_before_any_later_log(
        self, bridge: EngineBridge
    ) -> None:
        # Defense-in-depth path: a secret that only reached _selections (not
        # via the dedicated setters) is still registered by
        # applySelectionsToContext, before jobs run and could log it.
        secret = "DefenseInDepthSecret"
        bridge._selections["password"] = secret
        bridge.applySelectionsToContext()

        logger = logging.getLogger("xen.test_bridge_logs")
        logger.info("late log referencing %s", secret)

        assert secret not in bridge.installationLog


class TestUploadInstallLog:
    """uploadInstallLog runs the upload on a background QThread."""

    @staticmethod
    def _run_and_wait(bridge: EngineBridge, timeout_ms: int = 5000) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []
        loop = QEventLoop()

        def _on_finished(url: str, ok: bool, error_message: str) -> None:
            results.append((url, ok, error_message))
            loop.quit()

        bridge.logUploadFinished.connect(_on_finished)

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)

        bridge.uploadInstallLog()
        timer.start(timeout_ms)
        loop.exec()

        return results

    def test_upload_success_emits_finished_with_url(
        self, bridge: EngineBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "xen.gui.bridge.upload_log",
            lambda *_args, **_kwargs: "https://0x0.st/abcd",
        )

        results = self._run_and_wait(bridge)

        assert results == [("https://0x0.st/abcd", True, "")]

    def test_upload_failure_emits_finished_with_error(
        self, bridge: EngineBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("Log upload failed on all providers: 0x0.st: x; termbin.com: y")

        monkeypatch.setattr("xen.gui.bridge.upload_log", _fail)

        results = self._run_and_wait(bridge)

        assert len(results) == 1
        url, ok, error_message = results[0]
        assert url == ""
        assert ok is False
        assert "Log upload failed" in error_message


class TestInstallationSummary:
    """installationSummary exposes distribution, target and duration."""

    def test_summary_has_expected_keys(self, bridge: EngineBridge) -> None:
        summary = bridge.installationSummary
        for key in ("distribution", "targetDisk", "installationTime", "distroName"):
            assert key in summary

    def test_distribution_includes_distro_name(self, bridge: EngineBridge) -> None:
        name = bridge._engine.get_branding().name
        assert name in bridge.installationSummary["distribution"]

    def test_target_disk_reflects_selection(self, bridge: EngineBridge) -> None:
        bridge._selections["disk"] = "/dev/sdz"
        assert "/dev/sdz" in bridge.installationSummary["targetDisk"]

    def test_target_disk_includes_filesystem(self, bridge: EngineBridge) -> None:
        """Matches SummaryView's "<disk> > <FILESYSTEM>" format."""
        bridge._selections["disk"] = "/dev/sdz"
        bridge._selections["filesystem"] = "btrfs"
        assert "/dev/sdz > BTRFS" in bridge.installationSummary["targetDisk"]

    def test_target_disk_omits_separator_when_no_filesystem(
        self, bridge: EngineBridge
    ) -> None:
        bridge._selections["disk"] = "/dev/sdz"
        bridge._selections["filesystem"] = ""
        assert ">" not in bridge.installationSummary["targetDisk"]

    def test_duration_empty_before_install(self, bridge: EngineBridge) -> None:
        bridge._install_start_time = None
        assert bridge._format_install_duration() == ""

    def test_duration_formatted_after_install(self, bridge: EngineBridge) -> None:
        bridge._install_start_time = 100.0
        bridge._install_end_time = 100.0 + 372  # 6m 12s
        assert bridge._format_install_duration() == "6m 12s"

    def test_duration_seconds_only(self, bridge: EngineBridge) -> None:
        bridge._install_start_time = 10.0
        bridge._install_end_time = 55.0
        assert bridge._format_install_duration() == "45s"

"""Unit tests for the installation logs/diagnostics capture utilities.

Covers ``xen.utils.log_capture``:
- ``SecretRedactor``: value-based redaction + regex fallback for
  password-like patterns.
- ``BridgeLogHandler``: ring-buffer logging handler with file mirroring and
  a live-line callback, all best-effort.
- ``resolve_log_path``: picks a writable, non-world-readable log file path.
- ``upload_log``: uploads to 0x0.st with a termbin.com fallback. All network
  calls are mocked; no real network access is exercised.
"""

from __future__ import annotations

import logging
import os
import socket
import stat
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from xen.utils.log_capture import (
    BridgeLogHandler,
    SecretRedactor,
    resolve_log_path,
    upload_log,
)


class TestSecretRedactor:
    """Tests for value-based and pattern-based secret redaction."""

    def test_registered_secret_is_redacted(self) -> None:
        redactor = SecretRedactor()
        redactor.add_secret("hunter2")

        # The regex fallback also matches the bare "password is ..." phrasing
        # and masks the rest of the line (see
        # test_regex_fallback_masks_rest_of_line_for_unstructured_phrasing);
        # combined with the value-based substitution, the whole trailing
        # segment collapses to a single "***".
        assert redactor.redact("user password is hunter2 today") == "user password ***"

    def test_multiple_secrets_multiple_occurrences(self) -> None:
        redactor = SecretRedactor()
        redactor.add_secret("s3cret")
        redactor.add_secret("r00tpw")

        text = "s3cret used twice: s3cret, and r00tpw appears once"
        result = redactor.redact(text)

        assert "s3cret" not in result
        assert "r00tpw" not in result
        assert result.count("***") == 3

    @pytest.mark.parametrize(
        "text",
        [
            "password=hunter2",
            "passphrase: s3cret",
            "passwd foo",
            "PASSWORD=Sup3r",
            "Passphrase:topsecret",
        ],
    )
    def test_regex_fallback_masks_unregistered_password_like_patterns(self, text: str) -> None:
        redactor = SecretRedactor()
        result = redactor.redact(text)

        # The token/value portion must never survive redaction.
        assert "hunter2" not in result
        assert "s3cret" not in result
        assert "topsecret" not in result
        assert "Sup3r" not in result
        assert "***" in result

    def test_text_without_secret_is_unchanged(self) -> None:
        redactor = SecretRedactor()
        redactor.add_secret("hunter2")

        text = "installation proceeding, mounting /dev/sda2 at /mnt/target"
        assert redactor.redact(text) == text

    def test_empty_or_none_secret_is_ignored_without_crash(self) -> None:
        redactor = SecretRedactor()
        redactor.add_secret("")
        redactor.add_secret(None)  # type: ignore[arg-type]

        text = "nothing secret here"
        # Must not raise, and must not turn every character into "***".
        assert redactor.redact(text) == text

    def test_regex_fallback_masks_rest_of_line_for_unstructured_phrasing(self) -> None:
        """Anti-regression for a leak found while adding these tests.

        The bare-whitespace form of the fallback pattern (no ``=``/``:``)
        used to capture only a single token after the keyword. For natural
        log phrasing like "password is <secret>", that token was the filler
        word "is", not the secret — so an *unregistered* secret survived
        redaction in plain text right next to the masked filler word. The
        fallback now masks the remainder of the line instead, which cannot
        under-redact (it may only ever hide more than strictly necessary).
        """
        redactor = SecretRedactor()  # secret is NOT registered - only the fallback applies
        result = redactor.redact("debug: user password is hunter2SecretValue")

        assert "hunter2SecretValue" not in result
        assert result == "debug: user password ***"

    def test_regex_fallback_structured_form_masks_single_token(self) -> None:
        """Structured "key=value" / "key: value" logs still redact only the value."""
        redactor = SecretRedactor()

        assert redactor.redact("password=hunter2") == "password=***"
        assert redactor.redact("password: hunter2") == "password: ***"
        assert redactor.redact("passphrase hunter2") == "passphrase ***"

    def test_registered_secret_never_resurfaces(self) -> None:
        """Anti-regression: a concrete registered secret must never leak via redact()."""
        redactor = SecretRedactor()
        secret_value = "MyLuksPassphrase!42"
        redactor.add_secret(secret_value)

        texts = [
            f"applying encryption with passphrase {secret_value}",
            f"cryptsetup luksFormat --key-file - <<< '{secret_value}'",
            secret_value,
            f"{secret_value}{secret_value}",
        ]
        for text in texts:
            assert secret_value not in redactor.redact(text)


class TestBridgeLogHandler:
    """Tests for the logging.Handler that feeds the GUI log buffer."""

    @staticmethod
    def _make_logger(handler: BridgeLogHandler, name: str) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
        return logger

    def test_logged_messages_appear_in_order_and_redacted(self) -> None:
        redactor = SecretRedactor()
        redactor.add_secret("hunter2")
        handler = BridgeLogHandler(redactor)
        logger = self._make_logger(handler, "test.log_capture.order")

        logger.info("first message")
        logger.warning("second message with hunter2 in it")
        logger.error("third message")

        text = handler.get_text()
        lines = text.splitlines()
        assert len(lines) == 3
        assert "first message" in lines[0]
        assert "hunter2" not in lines[1]
        assert "***" in lines[1]
        assert "third message" in lines[2]
        # Order preserved (FIFO): "first" only in lines[0], "second" only in lines[1].
        assert "first" in lines[0] and "second" not in lines[0]
        assert "second" in lines[1] and "third" not in lines[1]

    def test_ring_buffer_respects_maxlen(self) -> None:
        redactor = SecretRedactor()
        handler = BridgeLogHandler(redactor, max_lines=5)
        logger = self._make_logger(handler, "test.log_capture.maxlen")

        for i in range(20):
            logger.info("line %d", i)

        lines = handler.get_text().splitlines()
        assert len(lines) == 5
        # Only the last 5 lines (15..19) should survive.
        for i, line in enumerate(lines):
            assert f"line {15 + i}" in line

    def test_on_line_callback_invoked_per_line(self) -> None:
        redactor = SecretRedactor()
        seen: list[str] = []
        handler = BridgeLogHandler(redactor, on_line=seen.append)
        logger = self._make_logger(handler, "test.log_capture.online")

        logger.info("hello")
        logger.info("world")

        assert len(seen) == 2
        assert "hello" in seen[0]
        assert "world" in seen[1]

    def test_on_line_exception_does_not_propagate(self) -> None:
        redactor = SecretRedactor()

        def _boom(_line: str) -> None:
            raise RuntimeError("callback exploded")

        handler = BridgeLogHandler(redactor, on_line=_boom)
        logger = self._make_logger(handler, "test.log_capture.online_error")

        # Must not raise despite the callback failing.
        logger.info("this should not crash the logger")

        # The line must still have been buffered even though the callback failed.
        assert "this should not crash the logger" in handler.get_text()

    def test_file_mirroring_writes_redacted_content(self, tmp_path: Path) -> None:
        redactor = SecretRedactor()
        redactor.add_secret("hunter2")
        log_file = tmp_path / "install.log"
        handler = BridgeLogHandler(redactor, file_path=log_file)
        logger = self._make_logger(handler, "test.log_capture.file")

        logger.info("connecting with password hunter2")
        logger.info("second line")

        content = log_file.read_text(encoding="utf-8")
        assert "hunter2" not in content
        assert "***" in content
        assert "second line" in content

    def test_clear_empties_buffer(self) -> None:
        redactor = SecretRedactor()
        handler = BridgeLogHandler(redactor)
        logger = self._make_logger(handler, "test.log_capture.clear")

        logger.info("something")
        assert handler.get_text() != ""

        handler.clear()
        assert handler.get_text() == ""


class TestResolveLogPath:
    """Tests for the writable-location resolution logic."""

    def test_falls_back_to_tmp_when_var_log_unwritable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # /var/log is typically not writable by an unprivileged test runner;
        # if XDG_RUNTIME_DIR is set and writable, resolve_log_path should use
        # it. We force a deterministic scenario by clearing XDG_RUNTIME_DIR
        # and pointing /tmp fallback validation only at behavior/permissions,
        # not the exact path (which depends on the environment's privileges).
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        path = resolve_log_path()

        assert path.exists()
        mode = stat.S_IMODE(path.stat().st_mode)
        # Must not be world-readable/writable: no bits set for "other".
        assert mode & stat.S_IRWXO == 0

    def test_prefers_xdg_runtime_dir_when_writable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        xdg_dir = tmp_path / "xdg-runtime"
        xdg_dir.mkdir()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_dir))

        path = resolve_log_path()

        # /var/log is not writable by the test runner, so XDG should win.
        if not (Path("/var/log").exists() and os.access("/var/log", os.W_OK)):
            assert path == xdg_dir / "xen" / "install.log"
        assert path.exists()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IRWXO == 0

    def test_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Even in a maximally hostile environment, resolve_log_path must not raise.
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        path = resolve_log_path()
        assert isinstance(path, Path)


class TestUploadLog:
    """Tests for upload_log(); all network I/O is mocked, no real network calls."""

    def test_primary_provider_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeResponse:
            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def read(self) -> bytes:
                return b"https://0x0.st/abcd.log\n"

        def _fake_urlopen(_req: Any, timeout: float) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        url = upload_log("some log text")
        assert url == "https://0x0.st/abcd.log"

    def test_fallback_to_termbin_when_0x0_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fake_urlopen_fail(_req: Any, timeout: float) -> Any:  # noqa: ARG001
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_fail)

        class _FakeSocket:
            def __init__(self) -> None:
                self._sent = False
                self._response = b"https://termbin.com/xyz\n\x00"

            def __enter__(self) -> _FakeSocket:
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def sendall(self, _data: bytes) -> None:
                self._sent = True

            def shutdown(self, _how: int) -> None:
                return None

            def recv(self, _bufsize: int) -> bytes:
                if self._response:
                    chunk, self._response = self._response, b""
                    return chunk
                return b""

        def _fake_create_connection(_addr: tuple[str, int], timeout: float) -> _FakeSocket:  # noqa: ARG001
            return _FakeSocket()

        monkeypatch.setattr(socket, "create_connection", _fake_create_connection)

        url = upload_log("some log text")
        assert url == "https://termbin.com/xyz"

    def test_both_providers_fail_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake_urlopen_fail(_req: Any, timeout: float) -> Any:  # noqa: ARG001
            raise urllib.error.URLError("connection refused")

        def _fake_create_connection_fail(_addr: tuple[str, int], timeout: float) -> Any:  # noqa: ARG001
            raise OSError("network unreachable")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_fail)
        monkeypatch.setattr(socket, "create_connection", _fake_create_connection_fail)

        with pytest.raises(RuntimeError, match="Log upload failed on all providers"):
            upload_log("some log text")

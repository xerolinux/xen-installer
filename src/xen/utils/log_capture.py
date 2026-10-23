"""
In-process log capture for the installation logs/diagnostics feature.

Installation jobs run inside the UI process (QThread worker). This module
provides:

- ``SecretRedactor``: strips known secret values (and common password-like
  patterns) from log text before it ever reaches a buffer, file, or upload.
- ``BridgeLogHandler``: a ``logging.Handler`` that feeds a bounded in-memory
  ring buffer, optionally mirrors to a log file, and optionally streams each
  redacted line to a callback (used by the bridge to emit a Qt signal).
- ``resolve_log_path``: picks a writable location for the install log file.
- ``upload_log``: uploads redacted log text to a public pastebin for support
  purposes (0x0.st primary, termbin.com fallback).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import TextIO

_SECRET_PATTERN_KEYWORDS = ("password", "passphrase", "passwd")

# Matches e.g. "password=xxx", "password: xxx", "passphrase xxx" (case-insensitive).
#
# Two branches:
# - Structured (`=` or `:`): the value is a single token, e.g. "password=hunter2".
# - Bare whitespace (natural-language log lines, e.g. "password is hunter2"): the
#   value greedily consumes the rest of the line. A single-token capture here
#   would only redact the first word after the keyword (often a filler word
#   like "is"/"was"), leaving the actual secret exposed later on the same
#   line. Since this pattern is a defense-in-depth fallback for secrets that
#   were never registered via SecretRedactor.add_secret(), it must fail safe:
#   over-redacting the rest of the line is preferable to a credential leak.
_SECRET_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(_SECRET_PATTERN_KEYWORDS) + r")\s*(?:([=:]\s*)(\S+)|(\s+)(.+))"
)

UPLOAD_USER_AGENT = "Xen-Installer/0.5"
UPLOAD_URL_0X0 = "https://0x0.st"
TERMBIN_HOST = "termbin.com"
TERMBIN_PORT = 9999


class SecretRedactor:
    """Redacts known secret values and common password-like patterns from text."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add_secret(self, value: str) -> None:
        """Register a concrete secret value to be redacted from future text.

        Empty or ``None`` values are ignored (nothing to redact).
        """
        if value:
            self._secrets.add(value)

    def redact(self, text: str) -> str:
        """Return ``text`` with registered secrets and password-like patterns hidden."""
        redacted = text
        for secret in self._secrets:
            redacted = redacted.replace(secret, "***")

        def _mask(match: re.Match[str]) -> str:
            keyword = match.group(1)
            separator = match.group(2) if match.group(2) is not None else match.group(4)
            return f"{keyword}{separator}***"

        redacted = _SECRET_PATTERN.sub(_mask, redacted)
        return redacted


class BridgeLogHandler(logging.Handler):
    """Logging handler feeding a bounded ring buffer, a file, and a live callback."""

    def __init__(
        self,
        redactor: SecretRedactor,
        file_path: Path | None = None,
        on_line: Callable[[str], None] | None = None,
        max_lines: int = 5000,
    ) -> None:
        super().__init__()
        self._redactor = redactor
        self._file_path = file_path
        self._on_line = on_line
        self._buffer: deque[str] = deque(maxlen=max_lines)
        self._lock = Lock()
        self._file: TextIO | None = None
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            msg = self._redactor.redact(msg)
        except Exception:
            self.handleError(record)
            return

        with self._lock:
            self._buffer.append(msg)
            if self._file_path is not None:
                # Best-effort file mirroring: I/O errors must never break logging.
                with contextlib.suppress(Exception):
                    if self._file is None:
                        self._file = self._file_path.open("a", encoding="utf-8")
                    self._file.write(msg + "\n")
                    self._file.flush()

        if self._on_line is not None:
            # Best-effort UI notification: callback errors must never crash logging.
            with contextlib.suppress(Exception):
                self._on_line(msg)

    def get_text(self) -> str:
        """Return the full buffered log text (redacted), newline-joined."""
        with self._lock:
            return "\n".join(self._buffer)

    def get_tail(self, max_lines: int) -> str:
        """Return the last ``max_lines`` buffered lines, newline-joined.

        Used for the live in-progress view: joining only the tail keeps the
        per-refresh cost bounded even when the full buffer holds thousands of
        lines (some install jobs are extremely verbose).
        """
        with self._lock:
            if max_lines >= len(self._buffer):
                return "\n".join(self._buffer)
            return "\n".join(list(self._buffer)[-max_lines:])

    def clear(self) -> None:
        """Clear the in-memory ring buffer."""
        with self._lock:
            self._buffer.clear()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                with contextlib.suppress(Exception):
                    self._file.close()
                self._file = None
        super().close()


def resolve_log_path() -> Path:
    """Resolve a writable path for the install log file.

    Tries, in order, a system-wide location (root install context), a
    per-user runtime location, then falls back to /tmp. Never raises.
    """
    candidates = [Path("/var/log/xen-install.log")]

    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        candidates.append(Path(xdg_runtime) / "xen" / "install.log")

    candidates.append(Path("/tmp/xen-install.log"))

    for candidate in candidates:
        parent = candidate.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue

        if os.access(parent, os.W_OK):
            if not candidate.exists():
                try:
                    candidate.touch(mode=0o600)
                except OSError:
                    continue
            return candidate

    # Should be unreachable (/tmp is always writable), but never raise.
    return Path("/tmp/xen-install.log")


def _upload_0x0(text: str, timeout: float) -> str:
    """Upload ``text`` to 0x0.st via a multipart/form-data POST. Returns the URL."""
    boundary = "----XenInstallLogBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="xen-install.log"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        f"{text}\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        UPLOAD_URL_0X0,
        data=body,
        method="POST",
        headers={
            "User-Agent": UPLOAD_USER_AGENT,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8").strip()


def _upload_termbin(text: str, timeout: float) -> str:
    """Upload ``text`` to termbin.com via a raw TCP socket. Returns the URL."""
    with socket.create_connection((TERMBIN_HOST, TERMBIN_PORT), timeout=timeout) as sock:
        sock.sendall(text.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)

        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)

    return b"".join(chunks).decode("utf-8", errors="replace").strip("\x00\r\n ")


def upload_log(text: str, timeout: float = 10.0) -> str:
    """Upload log text to a public pastebin and return its URL.

    Tries 0x0.st first, falls back to termbin.com. Raises ``RuntimeError`` if
    both uploads fail.
    """
    errors: list[str] = []

    try:
        return _upload_0x0(text, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        errors.append(f"0x0.st: {e}")

    try:
        return _upload_termbin(text, timeout)
    except (OSError, TimeoutError) as e:
        errors.append(f"termbin.com: {e}")

    raise RuntimeError(f"Log upload failed on all providers: {'; '.join(errors)}")

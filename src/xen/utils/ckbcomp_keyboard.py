"""Real, per-layout on-screen keyboard rows via `ckbcomp` (Debian's
console-setup, GPL-2.0-or-later, https://salsa.debian.org/installer-team/
console-setup) - the same tool the real Calamares keyboard module shells
out to for its own preview (see keyboard/keyboardwidget/keyboardpreview.cpp
upstream). Replaces the earlier hand-authored, per-language-GROUP XML
approximation (xen.utils.keyboard_data) with the exact key mapping the
selected XKB layout+variant will actually produce.

`ckbcomp -compact` emits one `keycode N = col0 col1 ...` line per physical
key, columns being the symbol at each shift/modifier level (plain, Shift,
AltGr, ...) - keyed by the standard Linux console scancode numbers, which
are the same across every layout (keycode 16 is always the key in the
"Q" position on a US keyboard, wherever a given layout actually puts a
different letter there). We only need the first two columns (plain,
Shift) for a compact test keyboard, mapped onto a fixed physical grid
covering the main alphanumeric block.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache

_COMMAND_TIMEOUT = 5

_UNICODE_TOKEN_RE = re.compile(r"^\+?U\+([0-9a-fA-F]+)$")
_KEYCODE_LINE_RE = re.compile(r"^keycode\s+(\d+)\s*=\s*(.+)$")

# keycode -> (display label, command name). Matches the "label!command"
# convention OnScreenKeyboard.qml already parses (see keyboard_data's XML
# files) - backspace/enter/tab/shift are handled there; everything else
# (Ctrl/Alt/CapsLock) renders inert, same as in the XML-group keyboards.
_COMMAND_KEYCODES: dict[int, tuple[str, str]] = {
    14: ("⌫", "backspace"),
    15: ("↹", "tab"),
    28: ("↵", "enter"),
    42: ("⇧", "shift"),
    54: ("⇧", "shift"),
    58: ("Caps", ""),
    29: ("Ctrl", ""),
    56: ("Alt", ""),
}

# Fixed physical grid (keycode, width ratio) covering the main
# alphanumeric block of a standard ANSI 104/105-key layout - see this
# module's docstring for why the same keycodes apply across every layout.
_ROWS: list[list[tuple[int, float]]] = [
    [(41, 1)] + [(k, 1) for k in range(2, 14)] + [(14, 2)],
    [(15, 1.5)] + [(k, 1) for k in range(16, 28)] + [(43, 1.5)],
    [(58, 1.8)] + [(k, 1) for k in range(30, 41)] + [(28, 1.8)],
    [(42, 2.3)] + [(k, 1) for k in range(44, 54)] + [(54, 2.3)],
    [(29, 1.3), (56, 1.3), (57, 6), (56, 1.3), (29, 1.3)],
]


def is_ckbcomp_available() -> bool:
    return shutil.which("ckbcomp") is not None


def decode_symbol(token: str) -> str:
    """Decode one ckbcomp -compact column token to a display character, or
    "" if it's a named keysym (not a printable Unicode codepoint)."""
    match = _UNICODE_TOKEN_RE.match(token)
    if not match:
        return ""
    try:
        return chr(int(match.group(1), 16))
    except ValueError:
        return ""


def parse_compact_keymap(text: str) -> dict[int, list[str]]:
    """Parse `ckbcomp -compact` output into {keycode: [token, ...]}."""
    rows: dict[int, list[str]] = {}
    for line in text.splitlines():
        match = _KEYCODE_LINE_RE.match(line.strip())
        if not match:
            continue
        rows[int(match.group(1))] = match.group(2).split()
    return rows


@lru_cache(maxsize=64)
def _ckbcomp_output(layout: str, variant: str, model: str = "pc105") -> str | None:
    cmd = ["ckbcomp", "-model", model, "-layout", layout]
    if variant:
        cmd += ["-variant", variant]
    cmd += ["-compact"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout


def keyboard_rows_via_ckbcomp(layout: str, variant: str) -> list[list[dict]] | None:
    """Real on-screen keyboard rows for an XKB layout+variant, or None if
    ckbcomp is unavailable or the layout is unrecognized - callers should
    fall back to the approximate XML-group keyboard in that case."""
    if not is_ckbcomp_available():
        return None
    raw = _ckbcomp_output(layout, variant)
    if raw is None:
        return None
    keymap = parse_compact_keymap(raw)
    if not keymap:
        return None

    rows: list[list[dict]] = []
    for physical_row in _ROWS:
        row: list[dict] = []
        for keycode, ratio in physical_row:
            if keycode in _COMMAND_KEYCODES:
                label, command = _COMMAND_KEYCODES[keycode]
                labels = f"{label}!{command}" if command else label
                row.append({"labels": labels, "ratio": ratio, "checkable": command == "shift"})
                continue
            tokens = keymap.get(keycode, [])
            plain = decode_symbol(tokens[0]) if tokens else ""
            shift = decode_symbol(tokens[1]) if len(tokens) > 1 else ""
            if not plain:
                plain = " "
            labels = f"{plain}|{shift}" if shift and shift != plain else plain
            row.append({"labels": labels, "ratio": ratio, "checkable": False})
        rows.append(row)
    return rows

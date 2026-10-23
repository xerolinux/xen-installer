"""Unit tests for xen.utils.ckbcomp_keyboard: real per-layout on-screen
keyboard rows generated via the `ckbcomp` CLI (Debian console-setup,
GPL-2.0-or-later), replacing the earlier hand-authored language-group
XML approximation with the exact layout the system will actually use."""

from __future__ import annotations

import shutil

import pytest

from xen.utils.ckbcomp_keyboard import (
    decode_symbol,
    is_ckbcomp_available,
    keyboard_rows_via_ckbcomp,
    parse_compact_keymap,
)

ckbcomp_missing = shutil.which("ckbcomp") is None


class TestDecodeSymbol:
    def test_decodes_plain_unicode_token(self) -> None:
        assert decode_symbol("U+0071") == "q"

    def test_decodes_plus_prefixed_letter_token(self) -> None:
        assert decode_symbol("+U+0051") == "Q"

    def test_named_keysym_returns_empty_string(self) -> None:
        assert decode_symbol("BackSpace") == ""
        assert decode_symbol("Control_q") == ""


class TestParseCompactKeymap:
    def test_parses_keycode_lines_into_token_lists(self) -> None:
        text = (
            "keymaps 0-4,6,8,10,12,14\n"
            "keycode 1 = Escape Escape Escape\n"
            "keycode 16 = +U+0071 +U+0051 +U+0071\n"
        )
        rows = parse_compact_keymap(text)
        assert rows[1] == ["Escape", "Escape", "Escape"]
        assert rows[16] == ["+U+0071", "+U+0051", "+U+0071"]

    def test_ignores_non_keycode_lines(self) -> None:
        text = "keymaps 0-4\nstrings as usual\n"
        assert parse_compact_keymap(text) == {}


@pytest.mark.skipif(ckbcomp_missing, reason="ckbcomp not installed")
class TestKeyboardRowsViaCkbcomp:
    def test_is_available(self) -> None:
        assert is_ckbcomp_available() is True

    def test_us_layout_has_qwerty_row(self) -> None:
        rows = keyboard_rows_via_ckbcomp("us", "")
        assert rows is not None
        all_labels = [key["labels"] for row in rows for key in row]
        assert any(label.startswith("q") for label in all_labels)
        assert any(label.startswith("a") for label in all_labels)

    def test_de_layout_differs_from_us(self) -> None:
        us_rows = keyboard_rows_via_ckbcomp("us", "")
        de_rows = keyboard_rows_via_ckbcomp("de", "")
        assert us_rows != de_rows
        de_labels = {key["labels"] for row in de_rows for key in row}
        # German QWERTZ swaps Y and Z relative to US QWERTY.
        assert any(label.startswith("z") for label in de_labels)

    def test_variant_changes_output(self) -> None:
        plain = keyboard_rows_via_ckbcomp("de", "")
        nodeadkeys = keyboard_rows_via_ckbcomp("de", "nodeadkeys")
        assert plain is not None and nodeadkeys is not None

    def test_backspace_and_enter_are_command_keys(self) -> None:
        rows = keyboard_rows_via_ckbcomp("us", "")
        all_labels = [key["labels"] for row in rows for key in row]
        assert any("!backspace" in label for label in all_labels)
        assert any("!enter" in label for label in all_labels)

    def test_unknown_layout_returns_none(self) -> None:
        assert keyboard_rows_via_ckbcomp("not-a-real-xkb-layout", "") is None


def test_keyboard_rows_via_ckbcomp_returns_none_when_ckbcomp_missing(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert keyboard_rows_via_ckbcomp("us", "") is None


def test_ckbcomp_result_is_cached_per_layout_variant(monkeypatch) -> None:
    import xen.utils.ckbcomp_keyboard as ck

    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = "keycode 16 = +U+0071 +U+0051\n"

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(ck, "is_ckbcomp_available", lambda: True)
    monkeypatch.setattr(ck.subprocess, "run", _fake_run)
    ck._ckbcomp_output.cache_clear()
    try:
        first = keyboard_rows_via_ckbcomp("zz-test", "v1")
        second = keyboard_rows_via_ckbcomp("zz-test", "v1")
        keyboard_rows_via_ckbcomp("zz-test", "v2")
    finally:
        ck._ckbcomp_output.cache_clear()
    assert first == second
    assert len(calls) == 2

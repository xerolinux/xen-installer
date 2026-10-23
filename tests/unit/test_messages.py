from __future__ import annotations

from unittest.mock import patch

from xen.i18n.messages import CONTEXT, Msg, localize, msg

FRENCH = {
    "The root (/) partition must be at least {gib} GiB.": "La partition racine (/) doit faire au moins {gib} Gio.",
    "In use": "Utilisée",
    "{name}: {reason}": "{name} : {reason}",
}


def _translate(context: str, text: str) -> str:
    assert context == CONTEXT
    return FRENCH.get(text, text)


def test_msg_reads_as_formatted_english() -> None:
    m = msg("The root (/) partition must be at least {gib} GiB.", gib=20)
    assert isinstance(m, str)
    assert m == "The root (/) partition must be at least 20 GiB."


def test_translated_uses_the_catalog_then_fills_values() -> None:
    m = msg("The root (/) partition must be at least {gib} GiB.", gib=20)
    with patch("xen.i18n.messages._translate", _translate):
        assert m.translated() == "La partition racine (/) doit faire au moins 20 Gio."


def test_nested_messages_are_translated_too() -> None:
    m = msg("{name}: {reason}", name="sda2", reason=msg("In use"))
    with patch("xen.i18n.messages._translate", _translate):
        assert m.translated() == "sda2 : Utilisée"


def test_broken_translation_falls_back_to_english() -> None:
    m = msg("In use")
    m2 = msg("The root (/) partition must be at least {gib} GiB.", gib=20)
    broken = {"The root (/) partition must be at least {gib} GiB.": "Au moins {taille} Gio."}
    with patch("xen.i18n.messages._translate", lambda _c, t: broken.get(t, t)):
        assert m.translated() == "In use"
        assert m2.translated() == "The root (/) partition must be at least 20 GiB."


def test_localize_walks_lists_and_dicts_and_keeps_other_values() -> None:
    data = {"items": [msg("In use"), "raw tool output", 3], "ok": True, "text": msg("In use")}
    with patch("xen.i18n.messages._translate", _translate):
        assert localize(data) == {"items": ["Utilisée", "raw tool output", 3], "ok": True, "text": "Utilisée"}


def test_msg_is_a_msg_and_braces_without_values_stay_literal() -> None:
    m = msg("Use {braces} literally")
    assert isinstance(m, Msg)
    assert m == "Use {braces} literally"
    with patch("xen.i18n.messages._translate", _translate):
        assert m.translated() == "Use {braces} literally"

"""Parses the bundled on-screen keyboard layout XML files and maps real XKB
layout codes to the layout-group file that best approximates them.

The XML files (``src/xen/gui/qml/components/keyboard_data/*.xml``) are
adapted from Calamares' own ``keyboardq/data/*.xml`` (GPL-3.0-or-later) -
each one is a visual approximation covering a *group* of related languages,
not an exact per-XKB-layout rendering. Parsed here in Python (not QML's
XMLHttpRequest, which this Qt6 install blocks for local files by default,
and not Qt6's QtQml.XmlListModel, whose XmlListModelRole dropped XPath
`query` support) and exposed to QML via EngineBridge.keyboardLayoutRows().
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from xen.utils.ckbcomp_keyboard import keyboard_rows_via_ckbcomp

_DATA_DIR = (
    Path(__file__).resolve().parents[1] / "gui" / "qml" / "components" / "keyboard_data"
)

# Real XKB layout code -> bundled layout-group XML file (without extension).
# Grouped by real codes (this project's own LocaleJob.COMMON_KEYMAPS),
# not Calamares' fuzzy label-prefix match.
GROUP_FOR_LAYOUT: dict[str, str] = {
    "us": "en",
    "gb": "en",
    "ca": "en",
    "ie": "en",
    "de": "de",
    "fr": "fr",
    "be": "fr",
    "es": "es",
    "latam": "es",
    "pt": "pt",
    "br": "pt",
    "ru": "ru",
    "ua": "ru",
    "bg": "ru",
    "ara": "ar",
    "ir": "afgani",
    "se": "scan",
    "no": "scan",
    "dk": "scan",
    "fi": "scan",
    "nl": "generic",
    "is": "generic",
    "it": "generic",
    "ro": "generic",
    "tr": "generic",
    "ee": "generic",
    "lv": "generic",
    "id": "generic",
    "in": "generic",
    "cz": "generic_qz",
    "sk": "generic_qz",
    "hu": "generic_qz",
    "pl": "generic_qz",
    "si": "generic_qz",
    "hr": "generic_qz",
    "rs": "generic_qz",
}

_DEFAULT_GROUP = "empty"


def group_file_for_layout(xkb_layout: str) -> str:
    """Return the bundled XML filename (with extension) for an XKB layout code."""
    return GROUP_FOR_LAYOUT.get(xkb_layout, _DEFAULT_GROUP) + ".xml"


def parse_keyboard_xml(path: Path) -> list[list[dict]]:
    """Parse one keyboard_data XML file into rows of key attribute dicts.

    Each returned key dict has ``labels`` (str), ``ratio`` (float, default
    1.0) and ``checkable`` (bool).
    """
    root = ET.parse(path).getroot()
    rows: list[list[dict]] = []
    for row_el in root.findall("Row"):
        keys = []
        for key_el in row_el.findall("Key"):
            ratio_raw = key_el.get("ratio", "1")
            try:
                ratio = float(ratio_raw)
            except ValueError:
                ratio = 1.0
            keys.append(
                {
                    "labels": key_el.get("labels", ""),
                    "ratio": ratio,
                    "checkable": key_el.get("checkable", "") == "true",
                }
            )
        rows.append(keys)
    return rows


@lru_cache(maxsize=16)
def _parse_group_file(group_file: str) -> tuple:
    """Cached parse, keyed by filename - tuples so the lru_cache result is hashable-safe to share."""
    rows = parse_keyboard_xml(_DATA_DIR / group_file)
    return tuple(tuple(row) for row in rows)


def keyboard_rows_for_layout(xkb_layout: str, xkb_variant: str = "") -> list[list[dict]]:
    """Return the on-screen keyboard rows for an XKB layout+variant.

    Prefers the real, exact mapping from `ckbcomp` (xen.utils.
    ckbcomp_keyboard) - the same tool real Calamares shells out to for its
    own keyboard preview. Falls back to the approximate, hand-authored
    per-language-GROUP XML data (this module) only if ckbcomp is missing
    or fails for the given layout/variant.
    """
    exact = keyboard_rows_via_ckbcomp(xkb_layout, xkb_variant)
    if exact is not None:
        return exact

    group_file = group_file_for_layout(xkb_layout)
    cached = _parse_group_file(group_file)
    return [list(row) for row in cached]

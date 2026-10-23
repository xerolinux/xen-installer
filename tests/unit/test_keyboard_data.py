"""Unit tests for xen.utils.keyboard_data: parsing the bundled on-screen
keyboard XML layout files and mapping XKB codes to layout groups."""

from __future__ import annotations

from pathlib import Path

import pytest

from xen.utils.keyboard_data import (
    group_file_for_layout,
    keyboard_rows_for_layout,
    parse_keyboard_xml,
)

DATA_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "xen"
    / "gui"
    / "qml"
    / "components"
    / "keyboard_data"
)


class TestParseKeyboardXml:
    def test_parses_rows_and_key_attributes(self, tmp_path) -> None:
        xml_path = tmp_path / "sample.xml"
        xml_path.write_text(
            """<?xml version="1.0"?>
            <Keyboard>
                <Row>
                    <Key labels = "a|A" ratio = "1"/>
                    <Key labels = "&#8593;!shift" ratio = "1.2" checkable = "true"/>
                </Row>
                <Row>
                    <Key labels = "z|Z" ratio = "1"/>
                </Row>
            </Keyboard>
            """
        )
        rows = parse_keyboard_xml(xml_path)
        assert len(rows) == 2
        assert rows[0][0] == {"labels": "a|A", "ratio": 1.0, "checkable": False}
        assert rows[0][1]["checkable"] is True
        assert rows[0][1]["ratio"] == pytest.approx(1.2)
        assert len(rows[1]) == 1

    def test_every_bundled_xml_file_parses_with_at_least_one_row(self) -> None:
        xml_files = sorted(DATA_DIR.glob("*.xml"))
        assert len(xml_files) == 12, "expected the 12 Calamares-derived layout group files"
        for path in xml_files:
            rows = parse_keyboard_xml(path)
            assert len(rows) >= 1, f"{path.name} produced no rows"
            assert any(len(row) >= 1 for row in rows), f"{path.name} produced no keys"


class TestGroupForLayout:
    @pytest.mark.parametrize(
        "layout,expected_group",
        [
            ("us", "en"),
            ("gb", "en"),
            ("de", "de"),
            ("fr", "fr"),
            ("ru", "ru"),
            ("ara", "ar"),
            ("ir", "afgani"),
            ("fa", "empty"),
            ("se", "scan"),
            ("pl", "generic_qz"),
            ("nl", "generic"),
            ("jp", "empty"),
            ("dvorak", "empty"),
        ],
    )
    def test_known_mappings(self, layout: str, expected_group: str) -> None:
        assert group_file_for_layout(layout) == expected_group + ".xml"

    def test_every_common_keymap_resolves_to_a_bundled_file(self) -> None:
        from xen.jobs.locale import LocaleJob

        for layout in LocaleJob.COMMON_KEYMAPS:
            group_file = group_file_for_layout(layout)
            assert (DATA_DIR / group_file).exists(), f"{layout} -> {group_file} missing"


class TestKeyboardRowsForLayout:
    def test_returns_parsed_rows_for_a_real_layout(self) -> None:
        rows = keyboard_rows_for_layout("us")
        assert len(rows) >= 1
        assert any(
            any(key["labels"].startswith("a") for key in row) for row in rows
        ), "expected an 'a' key somewhere in the US layout"

    def test_unmapped_layout_falls_back_to_empty(self) -> None:
        rows = keyboard_rows_for_layout("not-a-real-layout")
        assert len(rows) >= 1  # empty.xml still has rows, just blank labels

    def test_prefers_ckbcomp_when_available(self) -> None:
        """When ckbcomp is installed, its exact per-layout data is used
        instead of the approximate XML-group fallback - verified here by
        checking German-specific output only ckbcomp produces (the XML
        'de' group file's keys don't carry this exact labeling)."""
        import shutil

        if shutil.which("ckbcomp") is None:
            import pytest

            pytest.skip("ckbcomp not installed")
        from xen.utils.ckbcomp_keyboard import keyboard_rows_via_ckbcomp

        expected = keyboard_rows_via_ckbcomp("de", "")
        actual = keyboard_rows_for_layout("de", "")
        assert actual == expected

    def test_falls_back_to_xml_group_when_ckbcomp_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "xen.utils.keyboard_data.keyboard_rows_via_ckbcomp", lambda layout, variant: None
        )
        rows = keyboard_rows_for_layout("us")
        assert len(rows) >= 1
        assert any(
            any(key["labels"].startswith("a") for key in row) for row in rows
        )

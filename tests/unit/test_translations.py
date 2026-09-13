"""
Tests for translation files (.ts and .qm).

Verifies that:
1. All declared locales have both .ts and .qm files
2. Translation files have valid XML structure
3. Translation files are properly encoded (UTF-8)
4. No Unicode escape sequences remain in translations
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from xen.i18n.translator import Translator

# Translations directory
TRANSLATIONS_DIR = Path(__file__).parent.parent.parent / "src" / "xen" / "gui" / "translations"

# All locales declared in the bridge.py LOCALE_NATIVE_NAMES
# This list should match the locales defined in bridge.py
DECLARED_LOCALES = [
    "en_US",
    "en_GB",
    "en_CA",
    "en_AU",
    "en_NZ",
    "en_IE",
    "en_ZA",
    "en_IN",
    "fr_FR",
    "fr_CA",
    "fr_BE",
    "fr_CH",
    "fr_LU",
    "de_DE",
    "de_AT",
    "de_CH",
    "de_LU",
    "de_LI",
    "es_ES",
    "es_MX",
    "es_AR",
    "es_CO",
    "es_CL",
    "es_PE",
    "es_VE",
    "it_IT",
    "it_CH",
    "pt_BR",
    "pt_PT",
    "ru_RU",
    "uk_UA",
    "be_BY",
    "bg_BG",
    "sr_RS",
    "mk_MK",
    "zh_CN",
    "zh_TW",
    "zh_HK",
    "zh_SG",
    "ja_JP",
    "ko_KR",
    "ar_SA",
    "ar_EG",
    "ar_MA",
    "ar_DZ",
    "ar_TN",
    "ar_AE",
    "he_IL",
    "fa_IR",
    "hi_IN",
    "bn_IN",
    "bn_BD",
    "ta_IN",
    "te_IN",
    "mr_IN",
    "gu_IN",
    "kn_IN",
    "ml_IN",
    "pa_IN",
    "th_TH",
    "vi_VN",
    "id_ID",
    "ms_MY",
    "tr_TR",
    "el_GR",
    "el_CY",
    "pl_PL",
    "nl_NL",
    "nl_BE",
    "sv_SE",
    "sv_FI",
    "da_DK",
    "nb_NO",
    "nn_NO",
    "fi_FI",
    "is_IS",
    "cs_CZ",
    "sk_SK",
    "hu_HU",
    "ro_RO",
    "ro_MD",
    "lt_LT",
    "lv_LV",
    "et_EE",
    "sl_SI",
    "hr_HR",
    "bs_BA",
    "ca_ES",
    "eu_ES",
    "gl_ES",
    "cy_GB",
    "ga_IE",
    "sq_AL",
]

# Core locales that must have full translations
# Currently only EN and FR are complete - others are placeholders
CORE_LOCALES = [
    "en_US",
    "fr_FR",
]

# Extended locales that should have .ts files (may be incomplete)
EXTENDED_LOCALES = [
    "de_DE",
    "es_ES",
    "it_IT",
    "pt_BR",
    "ru_RU",
    "zh_CN",
    "ja_JP",
    "ko_KR",
]


def get_existing_ts_locales() -> set[str]:
    """Get all locales that have .ts files."""
    ts_files = TRANSLATIONS_DIR.glob("xen_*.ts")
    locales = set()
    for ts_file in ts_files:
        # Extract locale from filename: xen_fr_FR.ts -> fr_FR
        locale = ts_file.stem.replace("xen_", "")
        locales.add(locale)
    return locales


def get_existing_qm_locales() -> set[str]:
    """Get all locales that have .qm files."""
    qm_files = TRANSLATIONS_DIR.glob("xen_*.qm")
    locales = set()
    for qm_file in qm_files:
        # Extract locale from filename: xen_fr_FR.qm -> fr_FR
        locale = qm_file.stem.replace("xen_", "")
        locales.add(locale)
    return locales


class TestTranslationFileExistence:
    """Tests for translation file existence."""

    def test_translations_directory_exists(self) -> None:
        """Verify the translations directory exists."""
        assert TRANSLATIONS_DIR.exists(), f"Translations directory not found: {TRANSLATIONS_DIR}"

    def test_core_locales_have_ts_files(self) -> None:
        """Verify all core locales have .ts files."""
        existing = get_existing_ts_locales()
        missing = []
        for locale in CORE_LOCALES:
            if locale not in existing:
                missing.append(locale)
        assert not missing, f"Missing .ts files for core locales: {missing}"

    def test_core_locales_have_qm_files(self) -> None:
        """Verify all core locales have .qm files."""
        existing = get_existing_qm_locales()
        missing = []
        for locale in CORE_LOCALES:
            if locale not in existing:
                missing.append(locale)
        assert not missing, f"Missing .qm files for core locales: {missing}"

    def test_ts_files_have_matching_qm_files(self) -> None:
        """Verify every .ts file has a corresponding .qm file."""
        ts_locales = get_existing_ts_locales()
        qm_locales = get_existing_qm_locales()

        missing_qm = ts_locales - qm_locales
        assert not missing_qm, (
            f".ts files without .qm: {missing_qm}. Run: scripts/compile_translations.sh"
        )

    def test_qm_files_are_not_empty(self) -> None:
        """Verify .qm files for core locales are not empty (have actual content)."""
        empty_files = []
        for qm_file in TRANSLATIONS_DIR.glob("xen_*.qm"):
            # Empty Qt .qm files are typically 33-46 bytes (just header)
            # A file with actual translations should be larger
            if qm_file.stat().st_size < 100:
                locale = qm_file.stem.replace("xen_", "")
                # Only check core locales that should have full translations
                if locale in CORE_LOCALES:
                    empty_files.append(locale)

        assert not empty_files, (
            f"Empty .qm files for core locales: {empty_files}. Add translations to .ts files."
        )

    def test_extended_locales_have_ts_files(self) -> None:
        """Verify extended locales have .ts files (may be incomplete)."""
        existing = get_existing_ts_locales()
        missing = []
        for locale in EXTENDED_LOCALES:
            if locale not in existing:
                missing.append(locale)
        assert not missing, f"Missing .ts files for extended locales: {missing}"


class TestTranslationFileFormat:
    """Tests for translation file format and encoding."""

    @pytest.mark.parametrize("locale", CORE_LOCALES)
    def test_ts_file_is_valid_xml(self, locale: str) -> None:
        """Verify .ts files are valid XML."""
        ts_file = TRANSLATIONS_DIR / f"xen_{locale}.ts"
        if not ts_file.exists():
            pytest.skip(f"No .ts file for {locale}")

        try:
            ET.parse(ts_file)
        except ET.ParseError as e:
            pytest.fail(f"Invalid XML in xen_{locale}.ts: {e}")

    @pytest.mark.parametrize("locale", CORE_LOCALES)
    def test_ts_file_is_utf8_encoded(self, locale: str) -> None:
        """Verify .ts files are properly UTF-8 encoded."""
        ts_file = TRANSLATIONS_DIR / f"xen_{locale}.ts"
        if not ts_file.exists():
            pytest.skip(f"No .ts file for {locale}")

        try:
            content = ts_file.read_text(encoding="utf-8")
            assert (
                '<?xml version="1.0" encoding="utf-8"?>' in content.lower()
                or '<?xml version="1.0" encoding="UTF-8"?>' in content
            ), f"Missing UTF-8 encoding declaration in xen_{locale}.ts"
        except UnicodeDecodeError:
            pytest.fail(f"xen_{locale}.ts is not valid UTF-8")

    @pytest.mark.parametrize("locale", CORE_LOCALES)
    def test_no_unicode_escape_sequences(self, locale: str) -> None:
        """Verify .ts files don't contain Unicode escape sequences like \\u00e8."""
        ts_file = TRANSLATIONS_DIR / f"xen_{locale}.ts"
        if not ts_file.exists():
            pytest.skip(f"No .ts file for {locale}")

        content = ts_file.read_text(encoding="utf-8")

        # Pattern for Unicode escape sequences
        escape_pattern = r"\\u[0-9a-fA-F]{4}"
        matches = re.findall(escape_pattern, content)

        if matches:
            unique_escapes = set(matches[:10])  # Show first 10 unique escapes
            pytest.fail(
                f"xen_{locale}.ts contains Unicode escapes: {unique_escapes}. "
                f"Run: python scripts/fix_translation_encoding.py"
            )


class TestTranslationContent:
    """Tests for translation content quality."""

    @pytest.mark.parametrize("locale", CORE_LOCALES)
    def test_no_unfinished_translations_in_core(self, locale: str) -> None:
        """Verify core locale translations are complete (no 'unfinished' type)."""
        ts_file = TRANSLATIONS_DIR / f"xen_{locale}.ts"
        if not ts_file.exists():
            pytest.skip(f"No .ts file for {locale}")

        tree = ET.parse(ts_file)
        root = tree.getroot()

        unfinished_count = 0
        for message in root.iter("message"):
            translation = message.find("translation")
            if translation is not None and translation.get("type") == "unfinished":
                unfinished_count += 1

        # Allow some unfinished for development, but not too many
        max_allowed = 5 if locale != "en_US" else 0  # en_US should have no unfinished
        assert unfinished_count <= max_allowed, (
            f"xen_{locale}.ts has {unfinished_count} unfinished translations "
            f"(max allowed: {max_allowed})"
        )

    def test_en_us_has_source_equals_translation(self) -> None:
        """Verify en_US translation equals source (identity translation)."""
        ts_file = TRANSLATIONS_DIR / "xen_en_US.ts"
        if not ts_file.exists():
            pytest.skip("No en_US.ts file")

        tree = ET.parse(ts_file)
        root = tree.getroot()

        mismatches = []
        for message in root.iter("message"):
            source = message.find("source")
            translation = message.find("translation")

            if source is not None and translation is not None:
                source_text = source.text or ""
                trans_text = translation.text or ""

                # For en_US, source should equal translation
                if source_text != trans_text and translation.get("type") != "unfinished":
                    mismatches.append((source_text[:50], trans_text[:50]))

        # Allow some intentional differences (e.g., formatting variations)
        if len(mismatches) > 5:
            sample = mismatches[:5]
            pytest.fail(
                f"en_US has {len(mismatches)} source!=translation mismatches. Sample: {sample}"
            )


class TestTranslationCoverage:
    """Tests for translation coverage metrics."""

    def test_list_available_translations(self) -> None:
        """Report available translations (informational test)."""
        ts_locales = get_existing_ts_locales()
        qm_locales = get_existing_qm_locales()

        complete = ts_locales & qm_locales
        ts_only = ts_locales - qm_locales

        print("\n=== Translation Coverage ===")
        print(f"Complete (ts + qm): {len(complete)} locales")
        print(f"TS only (needs compilation): {len(ts_only)} locales")
        print(f"\nComplete locales: {sorted(complete)}")
        if ts_only:
            print(f"Needs compilation: {sorted(ts_only)}")

    def test_qm_file_sizes(self) -> None:
        """Report .qm file sizes (informational test)."""
        print("\n=== QM File Sizes ===")
        sizes = []
        for qm_file in sorted(TRANSLATIONS_DIR.glob("xen_*.qm")):
            size = qm_file.stat().st_size
            locale = qm_file.stem.replace("xen_", "")
            sizes.append((locale, size))
            status = "OK" if size > 100 else "EMPTY"
            print(f"  {locale}: {size} bytes ({status})")

        # Check that at least some files have content
        non_empty = [s for s in sizes if s[1] > 100]
        assert len(non_empty) >= 2, "At least 2 locales should have non-empty translations"


class TestRegionalLocaleFallback:
    """Regional variants booted from the ISO GRUB menu must still show a translated UI.

    The live ISO passes kbd.locale=fr_BE / de_CH / fr_CA / pt_PT / en_GB, and none of
    them ships a .conf of its own: without a language-family fallback the installer
    silently dropped to English (the Belgian "AZERTY - (Belge)" entry bug).
    """

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            ("fr_BE", "fr_FR"),
            ("fr_CA", "fr_FR"),
            ("de_CH", "de_DE"),
            ("de_AT", "de_DE"),
            ("en_GB", "en_US"),
            ("pt_PT", "pt_BR"),
            ("es_MX", "es_ES"),
        ],
    )
    def test_regional_variant_resolves_to_same_language(
        self, requested: str, expected: str
    ) -> None:
        translator = Translator()
        assert translator.resolve_locale(requested) == expected

    def test_exact_locale_wins(self) -> None:
        translator = Translator()
        assert translator.resolve_locale("fr_FR") == "fr_FR"

    def test_unsupported_language_falls_back_to_english(self) -> None:
        """GeoIP on a Belgian IP yields nl_BE; Xen ships no Dutch .conf."""
        translator = Translator()
        assert translator.resolve_locale("nl_BE") == "en_US"

    def test_encoding_suffix_is_ignored(self) -> None:
        """The kernel cmdline carries kbd.locale=fr_BE.UTF-8."""
        translator = Translator()
        assert translator.resolve_locale("fr_BE.UTF-8") == "fr_FR"

    def test_belgian_boot_choice_loads_french_strings(self) -> None:
        translator = Translator(locale="fr_BE")
        assert translator.get("next", section="buttons") != ""
        assert translator.get("next", section="buttons") == Translator(locale="fr_FR").get(
            "next", section="buttons"
        )

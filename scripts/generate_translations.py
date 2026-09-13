#!/usr/bin/env python3
"""
Generate Qt translation files (.ts) for all supported locales.

This script creates template .ts files that can be translated
and then compiled to .qm files using pyside6-lrelease.
"""

import subprocess
import sys
from pathlib import Path

# All supported locales (must match LOCALE_NATIVE_NAMES in bridge.py)
SUPPORTED_LOCALES = [
    # Major languages
    "en_US", "en_GB", "fr_FR", "de_DE", "es_ES", "it_IT", "pt_BR", "pt_PT",
    "ru_RU", "zh_CN", "zh_TW", "ja_JP", "ko_KR",
    # Arabic
    "ar_SA", "ar_EG",
    # European
    "pl_PL", "nl_NL", "sv_SE", "da_DK", "nb_NO", "fi_FI", "cs_CZ", "sk_SK",
    "hu_HU", "ro_RO", "el_GR", "tr_TR", "uk_UA", "bg_BG",
    # Other
    "he_IL", "hi_IN", "th_TH", "vi_VN", "id_ID", "ca_ES", "eu_ES", "gl_ES",
]

# Base template from en_US
TEMPLATE = '''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="{locale}">
<context>
    <name>LocaleView</name>
    <message>
        <source>Country &amp; Language</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Configure your system language, timezone, and keyboard layout</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>System Language</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Select your preferred system language and locale</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Select language...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Search languages...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Timezone</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Select your timezone for accurate time display</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Select timezone...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Search timezones...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Keyboard Configuration</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Select your keyboard layout and variant</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Layout</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Select...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Search layouts...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Variant</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Default</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Test:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Type to test keyboard...</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>SearchableComboBox</name>
    <message>
        <source>Select...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Type to search...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>%1 items</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>%1 of %2 items</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>No results found</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>SummaryView</name>
    <message>
        <source>Review Installation</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Please review your selections before starting the installation</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>System</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Computer Name:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Not set</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Edit</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Locale &amp; Keyboard</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Language:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Timezone:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Keyboard Layout:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>User Account</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Username:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Full Name:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Administrator:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Yes</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>No</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Auto Login:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Enabled</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Disabled</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Storage</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Installation Disk:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Size:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Unknown</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Partitioning:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Automatic</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Manual</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Ready to Install</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>The installation will begin once you click the Install button. This process will modify your disk and cannot be undone. Please ensure all data is backed up.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Previous</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Install Now</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>FinishedView</name>
    <message>
        <source>Installation Complete!</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Installation Failed</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>The system has been successfully installed on your computer</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>An error occurred during installation</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Installation Summary</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Distribution:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Installation Target:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Installation Time:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Packages Installed:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Error Details</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>An unknown error occurred during installation</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>View Full Logs</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Reboot Now</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Shutdown</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Continue</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Retry Installation</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Exit Installer</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Please remove the installation media before rebooting</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Check the logs for more details about the error</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>Main</name>
    <message>
        <source>Back</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Next</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Install</source>
        <translation type="unfinished"></translation>
    </message>
</context>
</TS>
'''


def main():
    """Generate translation files for all locales."""
    script_dir = Path(__file__).parent
    translations_dir = script_dir.parent / "src" / "xen" / "gui" / "translations"

    if not translations_dir.exists():
        translations_dir.mkdir(parents=True)

    print(f"Generating translation files in: {translations_dir}")

    generated = 0
    skipped = 0

    for locale in SUPPORTED_LOCALES:
        ts_file = translations_dir / f"xen_{locale}.ts"

        # Skip if file already exists (don't overwrite existing translations)
        if ts_file.exists():
            print(f"  [skip] {ts_file.name} (already exists)")
            skipped += 1
            continue

        # Generate template file
        content = TEMPLATE.format(locale=locale)
        ts_file.write_text(content, encoding="utf-8")
        print(f"  [new] {ts_file.name}")
        generated += 1

    print(f"\nGenerated: {generated}, Skipped: {skipped}")

    # Compile all .ts files to .qm
    print("\nCompiling .ts files to .qm...")

    # Find lrelease command
    lrelease_cmd = None
    for cmd in ["pyside6-lrelease", "lrelease"]:
        try:
            subprocess.run([cmd, "--version"], capture_output=True, check=True)
            lrelease_cmd = cmd
            break
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

    if not lrelease_cmd:
        print("Warning: Neither pyside6-lrelease nor lrelease found.")
        print("Install PySide6 or Qt6 linguist tools to compile translations.")
        return 1

    compiled = 0
    for ts_file in translations_dir.glob("*.ts"):
        qm_file = ts_file.with_suffix(".qm")
        try:
            result = subprocess.run(
                [lrelease_cmd, str(ts_file), "-qm", str(qm_file)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print(f"  [compiled] {qm_file.name}")
                compiled += 1
            else:
                print(f"  [error] {ts_file.name}: {result.stderr}")
        except Exception as e:
            print(f"  [error] {ts_file.name}: {e}")

    print(f"\nCompiled: {compiled} .qm files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

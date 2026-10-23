# Translation Management Guide

This guide covers the translation workflow for Xen, including file generation, editing, compilation, and troubleshooting.

---

## Quick Start

### Compile All Translations

```bash
# Standard compilation
./scripts/compile_translations.sh

# With encoding check
./scripts/compile_translations.sh --check-encoding
```

### Update Translation Sources

```bash
# Extract qsTr() strings from the QML into every xen_*.ts file
python scripts/update_translations.py
```

### Fix Encoding Issues

```bash
# Preview encoding issues
./scripts/fix_translation_encoding.py --dry-run

# Fix all files
./scripts/fix_translation_encoding.py

# Fix specific locale
./scripts/fix_translation_encoding.py --locale fr_FR
```

---

## Translation Workflow

### 1. Update Translation Sources

When you add or change translatable strings in QML files:

```bash
python scripts/update_translations.py
```

Messages built in Python (partition errors, scan reasons, system checks, install
progress) are wrapped in `msg("Template with {name}", name=value)` from
`xen.i18n.messages`. The bridge translates them when QML reads them, so they follow
the language picked on the Welcome page. The script collects every `msg("...")`
literal into `build/python_messages.js` (context `Messages`) for lupdate.

The script runs `lupdate` (`pyside6-lupdate`, `lupdate6` or Qt 6's `lupdate`,
whichever is found first) over `src/xen/gui/qml` and updates every
`src/xen/gui/translations/xen_*.ts` in place:
- New strings are added as unfinished
- Existing translations are preserved
- Strings that no longer exist are kept as vanished; pass `--no-obsolete` to drop them

To add a new locale, create `xen_<locale>.ts` with an empty `<TS version="2.1" language="<locale>">`,
add `config/i18n/<locale>.conf` (the Welcome/Finish texts), and run the script.

### 2. Edit Translations

Use Qt Linguist to edit translation files:

```bash
# Install Qt Linguist (if not already installed)
sudo pacman -S qt6-tools  # Arch Linux
sudo apt install qt6-linguist  # Debian/Ubuntu

# Open a translation file
linguist src/xen/gui/translations/xen_fr_FR.ts
```

**Important:** Configure Qt Linguist to use UTF-8 without escape sequences:
- File → Preferences → Translation File Format → Use UTF-8

### 3. Verify Encoding

Before compiling, check for Unicode escape sequences:

```bash
# Quick check (no changes made)
./scripts/fix_translation_encoding.py --dry-run

# Detailed report
./scripts/fix_translation_encoding.py --dry-run --verbose
```

If issues are found, fix them:

```bash
# Fix all files
./scripts/fix_translation_encoding.py

# Fix specific locale
./scripts/fix_translation_encoding.py --locale fr_FR
```

### 4. Compile to Binary Format

Compile `.ts` (XML) files to `.qm` (binary) format:

```bash
# Compile all translations
./scripts/compile_translations.sh

# Compile with encoding verification
./scripts/compile_translations.sh --check-encoding
```

### 5. Test in Application

```bash
# Run with specific locale
python -m xen.main --debug

# Switch locale in GUI to test translations
```

---

## Supported Locales

One locale per `src/xen/gui/translations/xen_<locale>.ts` file (67 locales):

`af_ZA`, `ar_SA`, `be_BY`, `bg_BG`, `bn_IN`, `bs_BA`, `ca_ES`, `cs_CZ`, `cy_GB`, `da_DK`, `de_DE`, `el_GR`, `en_US`, `eo`, `es_ES`, `et_EE`, `eu_ES`, `fa_IR`, `fi_FI`, `fil_PH`, `fr_FR`, `ga_IE`, `gl_ES`, `gu_IN`, `he_IL`, `hi_IN`, `hr_HR`, `hu_HU`, `hy_AM`, `id_ID`, `is_IS`, `it_IT`, `ja_JP`, `ka_GE`, `kk_KZ`, `kn_IN`, `ko_KR`, `lt_LT`, `lv_LV`, `mk_MK`, `ml_IN`, `mr_IN`, `ms_MY`, `nb_NO`, `nl_NL`, `nn_NO`, `pa_IN`, `pl_PL`, `pt_BR`, `pt_PT`, `ro_RO`, `ru_RU`, `sk_SK`, `sl_SI`, `sq_AL`, `sr_RS`, `sv_SE`, `sw_KE`, `ta_IN`, `te_IN`, `th_TH`, `tr_TR`, `uk_UA`, `uz_UZ`, `vi_VN`, `zh_CN`, `zh_TW`

Regional variants without their own file (`en_GB`, `fr_BE`, `ar_EG`, ...) fall back to
the base language's file.

---

## File Structure

```
src/xen/gui/translations/
├── xen_en_US.ts     # English (US) - Base translations
├── xen_fr_FR.ts     # French
├── xen_de_DE.ts     # German
├── ...
├── xen_en_US.qm     # Compiled English
├── xen_fr_FR.qm     # Compiled French
└── ...
```

### File Types

- **`.ts` files:** XML format, human-readable, used for editing
- **`.qm` files:** Binary format, optimized, used by Qt at runtime
- **Only `.qm` files** are loaded by the application

---

## Common Issues

### Unicode Escape Sequences

**Problem:** Characters display as `\u00e8` instead of `è`

**Cause:** Translation tools converting UTF-8 characters to escape sequences

**Solution:**

```bash
# Detect issues
./scripts/fix_translation_encoding.py --dry-run --verbose

# Fix issues
./scripts/fix_translation_encoding.py

# Recompile
./scripts/compile_translations.sh
```

### Missing Translations

**Problem:** Text appears in English despite selecting another locale

**Cause:** Translation not finished or .qm file not compiled

**Solution:**

1. Check `.ts` file for `<translation type="unfinished">`
2. Edit translation in Qt Linguist
3. Mark as finished
4. Recompile: `./scripts/compile_translations.sh`

### Compilation Errors

**Problem:** `lrelease` command not found

**Solution:**

```bash
# Install PySide6 (includes pyside6-lrelease)
pip install PySide6

# Or install Qt6 linguist tools
sudo pacman -S qt6-tools  # Arch Linux
sudo apt install qt6-linguist  # Debian/Ubuntu
```

### Special Characters Not Displaying

**Problem:** Accented characters or special symbols broken

**Checklist:**

1. Verify UTF-8 encoding in `.ts` file header: `encoding="utf-8"`
2. Check for escape sequences: `grep '\\u[0-9a-fA-F]' src/xen/gui/translations/*.ts`
3. Run encoding fix: `./scripts/fix_translation_encoding.py`
4. Verify Qt loads correct `.qm` file
5. Check system locale configuration

---

## Adding New Strings

### 1. Mark Strings in QML

Use `qsTr()` or `qsTranslate()`:

```qml
// Simple translation
Text {
    text: qsTr("Hello World")
}

// Translation with context
Text {
    text: qsTranslate("LocaleView", "System Language")
}

// Translation with placeholder
Text {
    text: qsTr("%1 items").arg(count)
}
```

### 2. Update Translation Files

```bash
python scripts/update_translations.py
```

### 3. Add Translations

Edit each locale file with Qt Linguist or directly in XML:

```xml
<message>
    <source>Hello World</source>
    <translation>Bonjour le monde</translation>
</message>
```

### 4. Compile and Test

```bash
./scripts/compile_translations.sh
python -m xen.main --debug
```

---

## Best Practices

### Development

1. **Always use `qsTr()` for user-facing strings**
   - Exception: Debug messages, logs
2. **Provide context with `qsTranslate()`**
   - Helps translators understand usage
3. **Use placeholders for dynamic content**
   - Example: `qsTr("%1 of %2 items").arg(current).arg(total)`
4. **Avoid string concatenation**
   - Bad: `qsTr("Hello") + " " + name`
   - Good: `qsTr("Hello %1").arg(name)`

### Translation

1. **Use Qt Linguist for editing**
   - Better context and validation
2. **Mark finished translations**
   - Unfinished ones show warnings
3. **Test in context**
   - Some strings have length constraints
4. **Use consistent terminology**
   - Maintain glossary for technical terms

### Maintenance

1. **Regular encoding checks**
   - Add to CI/CD pipeline
2. **Version control .ts files**
   - Track translation progress
3. **Commit .qm files together with their .ts changes**
   - The wheel ships them as-is; nothing recompiles them at package build time
4. **Document translation notes**
   - Add comments in .ts for context

---

## Scripting Reference

### fix_translation_encoding.py

```
Usage: ./scripts/fix_translation_encoding.py [OPTIONS]

Options:
  --dry-run          Preview changes without modifying files
  --locale LOCALE    Fix specific locale only (e.g., fr_FR)
  --no-compile       Skip .qm compilation after fixing
  --verbose          Show detailed changes for each file

Examples:
  ./scripts/fix_translation_encoding.py --dry-run
  ./scripts/fix_translation_encoding.py --locale fr_FR
  ./scripts/fix_translation_encoding.py --verbose
```

### compile_translations.sh

```
Usage: ./scripts/compile_translations.sh [OPTIONS]

Options:
  --check-encoding   Run encoding fix check before compilation

Examples:
  ./scripts/compile_translations.sh
  ./scripts/compile_translations.sh --check-encoding
```

### update_translations.py

```
Usage: python scripts/update_translations.py [OPTIONS]

Options:
  --ts-dir DIR       Directory holding the xen_*.ts files (default: src/xen/gui/translations)
  --no-obsolete      Drop strings that no longer appear in the QML

Notes:
  - Requires lupdate (qt6-tools on Arch)
  - Does not compile; run ./scripts/compile_translations.sh afterwards
```

---

## Automation

### Pre-commit Hook

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
# Check for encoding issues before commit

if ./scripts/fix_translation_encoding.py --dry-run | grep -q "escapes found"; then
    echo "❌ Unicode escape sequences detected in translation files"
    echo "Run: ./scripts/fix_translation_encoding.py"
    exit 1
fi
```

### CI/CD Pipeline

```yaml
# Example GitHub Actions workflow
- name: Check Translation Encoding
  run: |
    python scripts/fix_translation_encoding.py --dry-run
    if [ $? -ne 0 ]; then
      echo "Encoding issues detected"
      exit 1
    fi

- name: Compile Translations
  run: ./scripts/compile_translations.sh
```

---

## Troubleshooting

### Debug Translation Loading

Add logging to `src/xen/gui/translator_proxy.py`:

```python
def set_language(self, locale):
    print(f"Loading translator for: {locale}")
    qm_file = translations_dir / f"xen_{locale}.qm"
    print(f"QM file: {qm_file}")
    print(f"Exists: {qm_file.exists()}")
    # ... rest of code
```

### Verify Compiled Translations

```bash
# Check .qm file size (should not be 0)
ls -lh src/xen/gui/translations/*.qm

# Verify .qm contains translations (not just empty)
strings src/xen/gui/translations/xen_fr_FR.qm | grep -i "langue"
```

### Reset Translations

```bash
# Remove all compiled files
rm src/xen/gui/translations/*.qm

# Recompile from scratch
./scripts/compile_translations.sh
```

---

## Resources

### Documentation

- [Qt Linguist Manual](https://doc.qt.io/qt-6/linguist-manual.html)
- [PySide6 Translation](https://doc.qt.io/qtforpython-6/overviews/linguist-manual.html)
- [Qt Translation Format](https://doc.qt.io/qt-6/linguist-ts-file-format.html)

### Tools

- **Qt Linguist:** GUI editor for .ts files
- **lrelease:** Compiler for .ts to .qm
- **lupdate:** Extracts strings from the QML (`scripts/update_translations.py`)

### Related Files

- `src/xen/gui/translator_proxy.py` - Translation system implementation
- `src/xen/gui/bridge.py` - Language switching logic
- `scripts/update_translations.py` - Updates .ts files from the QML
- `scripts/compile_translations.sh` - Compilation script
- `scripts/fix_translation_encoding.py` - Encoding fixer

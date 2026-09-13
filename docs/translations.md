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

### Generate Translation Templates

```bash
# Generate .ts files for all supported locales
python scripts/generate_translations.py
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

### 1. Update Translation Templates

When you add new translatable strings in QML files:

```bash
# This script creates/updates .ts files for all supported locales
python scripts/generate_translations.py
```

The script:
- Creates new `.ts` files for locales that don't exist yet
- Skips existing files to preserve translations
- Uses UTF-8 encoding to prevent character issues

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

### Major Languages
- **English:** `en_US`, `en_GB`
- **French:** `fr_FR`
- **German:** `de_DE`
- **Spanish:** `es_ES`
- **Italian:** `it_IT`
- **Portuguese:** `pt_BR`, `pt_PT`
- **Russian:** `ru_RU`
- **Chinese:** `zh_CN`, `zh_TW`
- **Japanese:** `ja_JP`
- **Korean:** `ko_KR`

### Other Languages
- **Arabic:** `ar_SA`, `ar_EG`
- **European:** `pl_PL`, `nl_NL`, `sv_SE`, `da_DK`, `nb_NO`, `fi_FI`, `cs_CZ`, `sk_SK`, `hu_HU`, `ro_RO`, `el_GR`, `tr_TR`, `uk_UA`, `bg_BG`
- **Other:** `he_IL`, `hi_IN`, `th_TH`, `vi_VN`, `id_ID`, `ca_ES`, `eu_ES`, `gl_ES`

Total: **37 locales**

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
# Regenerate templates (preserves existing translations)
python scripts/generate_translations.py
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
3. **Don't commit .qm files to git**
   - Generated files, rebuild from .ts
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

### generate_translations.py

```
Usage: python scripts/generate_translations.py

Notes:
  - Skips existing files to preserve translations
  - Creates new .ts files for missing locales
  - Automatically compiles to .qm after generation
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
- **lrelease:** Compiler for .ts → .qm
- **lupdate:** Extracts strings from source code (not used in Xen)

### Related Files

- `src/xen/gui/translator_proxy.py` - Translation system implementation
- `src/xen/gui/bridge.py` - Language switching logic
- `scripts/generate_translations.py` - Template generator
- `scripts/compile_translations.sh` - Compilation script
- `scripts/fix_translation_encoding.py` - Encoding fixer

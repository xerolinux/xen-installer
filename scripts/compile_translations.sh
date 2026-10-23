#!/usr/bin/env bash
#
# Compile all Qt translation (.ts) files to binary (.qm) format
#
# Usage:
#   ./scripts/compile_translations.sh [--check-encoding]
#
# Options:
#   --check-encoding    Run encoding fix before compilation
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TRANSLATIONS_DIR="$PROJECT_ROOT/src/xen/gui/translations"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

LRELEASE=""
for candidate in pyside6-lrelease lrelease6 /usr/lib/qt6/bin/lrelease lrelease; do
    if command -v "$candidate" &> /dev/null; then
        LRELEASE="$candidate"
        break
    fi
done
if [ -z "$LRELEASE" ]; then
    echo -e "${RED}❌ Error: no Qt 6 lrelease found (pyside6-lrelease, lrelease6, lrelease)${NC}"
    echo "Install PySide6 or Qt6 linguist tools:"
    echo "  pip install PySide6"
    exit 1
fi

echo "🔨 Qt Translation Compiler"
echo "Using: $LRELEASE"
echo ""

# Run encoding fix if requested
if [[ "${1:-}" == "--check-encoding" ]]; then
    echo "🔍 Checking for Unicode escape sequences..."
    python "$SCRIPT_DIR/fix_translation_encoding.py" --dry-run
    echo ""
fi

# Compile all .ts files
COMPILED=0
FAILED=0
TOTAL=$(find "$TRANSLATIONS_DIR" -name "*.ts" | wc -l)

echo "📄 Compiling $TOTAL translation file(s)..."
echo ""

while IFS= read -r ts_file; do
    qm_file="${ts_file%.ts}.qm"
    filename=$(basename "$ts_file")

    if output=$("$LRELEASE" "$ts_file" -qm "$qm_file" 2>&1); then
        # Extract stats from output
        if echo "$output" | grep -q "Generated [0-9]\+ translation"; then
            stats=$(echo "$output" | grep "Generated" | sed 's/.*Generated //')
            echo -e "${GREEN}✅ $filename${NC}: $stats"
        else
            echo -e "${YELLOW}⚠️  $filename${NC}: Compiled (no translations)"
        fi
        COMPILED=$((COMPILED + 1))
    else
        echo -e "${RED}❌ $filename${NC}: Failed"
        echo "$output" | sed 's/^/     /'
        FAILED=$((FAILED + 1))
    fi
done < <(find "$TRANSLATIONS_DIR" -name "*.ts" | sort)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 Summary:"
echo "   Total:    $TOTAL"
echo "   Success:  $COMPILED"
if [[ $FAILED -gt 0 ]]; then
    echo -e "   ${RED}Failed:   $FAILED${NC}"
    exit 1
else
    echo "   Failed:   0"
    echo ""
    echo -e "${GREEN}✅ All translations compiled successfully!${NC}"
fi

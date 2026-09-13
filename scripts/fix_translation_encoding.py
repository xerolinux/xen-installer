#!/usr/bin/env python3
"""
Fix Unicode escape sequences in Qt translation (.ts) files.

This script converts escape sequences like \\u00e8 to native UTF-8 characters (è),
preserving XML structure and recompiling .qm files.

Usage:
    ./scripts/fix_translation_encoding.py [--dry-run] [--locale LOCALE]

Examples:
    ./scripts/fix_translation_encoding.py                    # Fix all locales
    ./scripts/fix_translation_encoding.py --dry-run          # Preview changes only
    ./scripts/fix_translation_encoding.py --locale fr_FR    # Fix specific locale
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def decode_unicode_escapes(text: str) -> str:
    """
    Decode Unicode escape sequences (\\uXXXX) to UTF-8 characters.

    Handles:
    - \\uXXXX (4-digit hex)
    - Multiple escape sequences in same string
    - Preserves XML entities (&amp;, &lt;, &gt;, etc.)

    Args:
        text: String potentially containing Unicode escapes

    Returns:
        String with escapes decoded to UTF-8 characters
    """
    # Pattern for \uXXXX escape sequences
    pattern = r'\\u([0-9a-fA-F]{4})'

    def replace_escape(match):
        # Convert hex to character
        code_point = int(match.group(1), 16)
        return chr(code_point)

    # Replace all Unicode escapes
    return re.sub(pattern, replace_escape, text)


def analyze_file(file_path: Path) -> Tuple[int, List[Tuple[int, str, str]]]:
    """
    Analyze a .ts file for Unicode escape sequences.

    Args:
        file_path: Path to .ts file

    Returns:
        Tuple of (escape_count, [(line_number, original, fixed)])
    """
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    escape_pattern = r'\\u[0-9a-fA-F]{4}'
    changes = []
    escape_count = 0

    for line_num, line in enumerate(lines, start=1):
        if re.search(escape_pattern, line):
            fixed_line = decode_unicode_escapes(line)
            if fixed_line != line:
                escape_count += len(re.findall(escape_pattern, line))
                changes.append((line_num, line.strip(), fixed_line.strip()))

    return escape_count, changes


def fix_file(file_path: Path, dry_run: bool = False) -> Dict[str, any]:
    """
    Fix Unicode escapes in a single .ts file.

    Args:
        file_path: Path to .ts file
        dry_run: If True, only analyze without modifying

    Returns:
        Dict with fix results
    """
    result = {
        'file': file_path.name,
        'escape_count': 0,
        'lines_affected': 0,
        'success': False,
        'changes': []
    }

    try:
        # Analyze file
        escape_count, changes = analyze_file(file_path)
        result['escape_count'] = escape_count
        result['lines_affected'] = len(changes)
        result['changes'] = changes

        if escape_count == 0:
            result['success'] = True
            return result

        if dry_run:
            result['success'] = True
            return result

        # Read and fix content
        content = file_path.read_text(encoding='utf-8')
        fixed_content = decode_unicode_escapes(content)

        # Verify XML structure is preserved
        if '<?xml' not in fixed_content or '<TS' not in fixed_content:
            raise ValueError("XML structure corrupted during fix")

        # Write fixed content
        file_path.write_text(fixed_content, encoding='utf-8')
        result['success'] = True

    except Exception as e:
        result['error'] = str(e)

    return result


def compile_qm_files(translations_dir: Path, lrelease_cmd: str = None) -> Dict[str, any]:
    """
    Compile all .ts files to .qm using pyside6-lrelease.

    Args:
        translations_dir: Directory containing .ts files
        lrelease_cmd: Override lrelease command

    Returns:
        Dict with compilation results
    """
    result = {
        'compiled': 0,
        'failed': 0,
        'errors': []
    }

    # Find lrelease command
    if not lrelease_cmd:
        for cmd in ["pyside6-lrelease", "lrelease"]:
            try:
                subprocess.run([cmd, "--version"], capture_output=True, check=True)
                lrelease_cmd = cmd
                break
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue

    if not lrelease_cmd:
        result['errors'].append("Neither pyside6-lrelease nor lrelease found")
        return result

    # Compile each .ts file
    for ts_file in sorted(translations_dir.glob("*.ts")):
        qm_file = ts_file.with_suffix(".qm")
        try:
            proc_result = subprocess.run(
                [lrelease_cmd, str(ts_file), "-qm", str(qm_file)],
                capture_output=True,
                text=True,
                check=False
            )

            if proc_result.returncode == 0:
                result['compiled'] += 1
            else:
                result['failed'] += 1
                result['errors'].append(f"{ts_file.name}: {proc_result.stderr.strip()}")

        except Exception as e:
            result['failed'] += 1
            result['errors'].append(f"{ts_file.name}: {e}")

    return result


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Fix Unicode escape sequences in Qt translation files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying files"
    )
    parser.add_argument(
        "--locale",
        help="Fix specific locale only (e.g., fr_FR)"
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip .qm compilation after fixing"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed changes for each file"
    )

    args = parser.parse_args()

    # Locate translations directory
    script_dir = Path(__file__).parent
    translations_dir = script_dir.parent / "src" / "xen" / "gui" / "translations"

    if not translations_dir.exists():
        print(f"❌ Translations directory not found: {translations_dir}")
        return 1

    print("🔍 Unicode Escape Sequence Fix Utility")
    print(f"📁 Directory: {translations_dir}")
    print(f"🔧 Mode: {'DRY RUN (preview only)' if args.dry_run else 'FIX MODE'}")
    print()

    # Determine files to process
    if args.locale:
        ts_files = list(translations_dir.glob(f"xen_{args.locale}.ts"))
        if not ts_files:
            print(f"❌ Locale file not found: xen_{args.locale}.ts")
            return 1
    else:
        ts_files = sorted(translations_dir.glob("xen_*.ts"))

    print(f"📄 Processing {len(ts_files)} file(s)...")
    print()

    # Process each file
    results = []
    total_escapes = 0
    total_lines = 0

    for ts_file in ts_files:
        result = fix_file(ts_file, dry_run=args.dry_run)
        results.append(result)

        if result['escape_count'] > 0:
            status = "🔍" if args.dry_run else ("✅" if result['success'] else "❌")
            print(f"{status} {result['file']}: {result['escape_count']} escapes on {result['lines_affected']} lines")

            total_escapes += result['escape_count']
            total_lines += result['lines_affected']

            # Show sample changes if verbose
            if args.verbose and result['changes']:
                for line_num, original, fixed in result['changes'][:3]:
                    print(f"    Line {line_num}:")
                    print(f"      Before: {original}")
                    print(f"      After:  {fixed}")
                if len(result['changes']) > 3:
                    print(f"    ... and {len(result['changes']) - 3} more changes")
                print()
        elif result['escape_count'] == 0:
            print(f"✓ {result['file']}: No escapes found (clean)")

    print()
    print("=" * 60)
    print(f"📊 Summary:")
    print(f"   Files processed: {len(results)}")
    print(f"   Total escapes: {total_escapes}")
    print(f"   Lines affected: {total_lines}")

    if args.dry_run:
        print()
        print("💡 Run without --dry-run to apply fixes")
        return 0

    # Compile .qm files if not skipped
    if not args.no_compile and total_escapes > 0:
        print()
        print("🔨 Compiling .qm files...")
        compile_result = compile_qm_files(translations_dir)

        if compile_result['compiled'] > 0:
            print(f"✅ Compiled {compile_result['compiled']} .qm file(s)")

        if compile_result['failed'] > 0:
            print(f"❌ Failed to compile {compile_result['failed']} file(s)")
            for error in compile_result['errors']:
                print(f"   - {error}")

        if compile_result['errors'] and 'lrelease' in compile_result['errors'][0]:
            print()
            print("⚠️  Install PySide6 or Qt6 linguist tools:")
            print("   pip install PySide6")

    print()
    print("✅ Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

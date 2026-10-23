#!/usr/bin/env python3
"""Refresh every src/xen/gui/translations/*.ts from the QML qsTr() strings and the Python msg() templates."""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "xen"
QML_DIR = SRC_DIR / "gui" / "qml"
MESSAGES_JS = PROJECT_ROOT / "build" / "python_messages.js"
TS_DIR = PROJECT_ROOT / "src" / "xen" / "gui" / "translations"
LUPDATE_CANDIDATES = ("pyside6-lupdate", "lupdate6", "/usr/lib/qt6/bin/lupdate", "lupdate")


def find_lupdate() -> str | None:
    for candidate in LUPDATE_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def python_messages(src_dir: Path) -> list[str]:
    found: set[str] = set()
    for path in sorted(src_dir.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            first = node.args[0]
            if name == "msg" and isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return sorted(found)


def write_messages_js(templates: list[str], out: Path) -> None:
    # lupdate has no Python parser, so the templates reach it as JavaScript
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = (f'QT_TRANSLATE_NOOP("Messages", {json.dumps(t, ensure_ascii=False)})' for t in templates)
    out.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ts-dir",
        type=Path,
        default=TS_DIR,
        help=f"directory holding the xen_*.ts files (default: {TS_DIR})",
    )
    parser.add_argument(
        "--no-obsolete",
        action="store_true",
        help="drop strings that no longer appear in the QML instead of keeping them as vanished",
    )
    args = parser.parse_args()

    lupdate = find_lupdate()
    if lupdate is None:
        print(
            "Error: lupdate not found (tried: " + ", ".join(LUPDATE_CANDIDATES) + "). "
            "Install qt6-tools or PySide6's tools.",
            file=sys.stderr,
        )
        return 1

    ts_files = sorted(args.ts_dir.glob("xen_*.ts"))
    if not ts_files:
        print(f"Error: no xen_*.ts files in {args.ts_dir}", file=sys.stderr)
        return 1

    write_messages_js(python_messages(SRC_DIR), MESSAGES_JS)
    cmd = [
        lupdate,
        "-extensions",
        "qml,js",
        str(QML_DIR),
        str(MESSAGES_JS),
        "-ts",
        *map(str, ts_files),
    ]
    if args.no_obsolete:
        cmd.insert(1, "-no-obsolete")

    print(f"Updating {len(ts_files)} file(s) with {lupdate}")
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())

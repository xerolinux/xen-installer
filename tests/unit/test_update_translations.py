from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "update_translations.py"
spec = importlib.util.spec_from_file_location("update_translations", SCRIPT)
update_translations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_translations)


def test_python_messages_collects_msg_literals_once(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "from xen.i18n.messages import msg\n"
        'A = msg("In use")\n'
        'B = msg("Root must be at least {gib} GiB.", gib=20)\n'
        "C = msg(dynamic)\n"
        'D = other("Not a message")\n'
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text('from xen.i18n import messages\nE = messages.msg("In use")\n')
    assert update_translations.python_messages(tmp_path) == [
        "In use",
        "Root must be at least {gib} GiB.",
    ]


def test_messages_js_marks_each_template_for_the_messages_context(tmp_path: Path) -> None:
    out = tmp_path / "python_messages.js"
    update_translations.write_messages_js(['Say "hi"', "≥ 20%"], out)
    assert out.read_text(encoding="utf-8") == (
        'QT_TRANSLATE_NOOP("Messages", "Say \\"hi\\"")\n'
        'QT_TRANSLATE_NOOP("Messages", "≥ 20%")\n'
    )

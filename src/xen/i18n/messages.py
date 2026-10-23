"""User-facing messages built in Python, translated when the UI reads them.

``msg(template, **values)`` reads as the formatted English text, so logs and
comparisons keep working, but remembers its template: the bridge calls
``localize`` at read time, after the user picked a language.
``scripts/update_translations.py`` collects every ``msg("...")`` literal.
"""

from __future__ import annotations

from typing import Any

CONTEXT = "Messages"


def _translate(context: str, text: str) -> str:
    # Partitioning code also runs in the VM test guest, which has no PySide6
    try:
        from PySide6.QtCore import QCoreApplication
    except ImportError:
        return text
    return QCoreApplication.translate(context, text)


class Msg(str):
    template: str
    values: dict[str, Any]

    def __new__(cls, template: str, **values: Any) -> Msg:
        obj = super().__new__(cls, template.format(**values) if values else template)
        obj.template = template
        obj.values = values
        return obj

    def translated(self) -> str:
        text = _translate(CONTEXT, self.template)
        if not self.values:
            return text
        try:
            return text.format(**{k: localize(v) for k, v in self.values.items()})
        except (KeyError, IndexError, ValueError):
            return self.template.format(**{k: localize(v) for k, v in self.values.items()})


msg = Msg


def localize(value: Any) -> Any:
    if isinstance(value, Msg):
        return value.translated()
    if isinstance(value, list):
        return [localize(v) for v in value]
    if isinstance(value, tuple):
        return tuple(localize(v) for v in value)
    if isinstance(value, dict):
        return {k: localize(v) for k, v in value.items()}
    return value

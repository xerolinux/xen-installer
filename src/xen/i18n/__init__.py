"""
Xen Internationalization (i18n) System.

Provides dynamic language support with configurable translation files.
"""

from xen.i18n.translator import Translator, get_translator, set_locale

__all__ = [
    "Translator",
    "get_translator",
    "set_locale",
]

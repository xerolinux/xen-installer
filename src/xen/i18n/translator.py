"""
Translation system for Xen Installer.

Loads translations from config/i18n/<locale>.conf files.
Supports dynamic language switching at runtime.
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default locale
DEFAULT_LOCALE = "en_US"

# Global translator instance
_translator: Translator | None = None


class Translator:
    """
    Handles translation loading and string retrieval.

    Translation files use INI format with sections for different UI areas:

    [welcome]
    title = Welcome to {distro_name}
    subtitle = {distro_tagline}
    install_button = Install {distro_name}

    [requirements]
    title = System Requirements
    ram_description = Memory (RAM)
    ...
    """

    def __init__(
        self,
        locale: str = DEFAULT_LOCALE,
        i18n_dir: Path | str | None = None,
    ) -> None:
        """
        Initialize the translator.

        Args:
            locale: Locale code (e.g., "en_US", "fr_FR")
            i18n_dir: Path to i18n directory containing .conf files
        """
        self._locale = locale
        self._fallback_locale = DEFAULT_LOCALE
        self._translations: dict[str, dict[str, str]] = {}
        self._fallback_translations: dict[str, dict[str, str]] = {}

        # Determine i18n directory
        if i18n_dir is None:
            self._i18n_dir = self._default_i18n_dir()
        else:
            self._i18n_dir = Path(i18n_dir)

        # Load translations
        self._load_translations()

    @staticmethod
    def _default_i18n_dir() -> Path:
        """Locate the ``config/i18n`` directory across dev and packaged layouts.

        In a source checkout the files live at ``<repo>/config/i18n``. Packaged
        builds (e.g. Nix ``buildPythonApplication``) ship them under
        ``<prefix>/share/xen/config/i18n`` — not next to the Python module —
        so probe several candidates and fall back to the dev path.
        """
        here = Path(__file__).resolve()
        candidates: list[Path] = [here.parents[3] / "config" / "i18n"]
        # Walk up from the module to find an installed ``share/xen`` tree.
        candidates += [p / "share" / "xen" / "config" / "i18n" for p in here.parents]
        candidates.append(Path("/etc/xen/config/i18n"))
        return next((c for c in candidates if c.exists()), candidates[0])

    @property
    def locale(self) -> str:
        """Current locale."""
        return self._locale

    @property
    def available_locales(self) -> list[str]:
        """List of available locales based on .conf files."""
        locales = []
        if self._i18n_dir.exists():
            for conf_file in self._i18n_dir.glob("*.conf"):
                locales.append(conf_file.stem)
        return sorted(locales)

    def set_locale(self, locale: str) -> bool:
        """
        Change the current locale.

        Args:
            locale: New locale code

        Returns:
            True if locale was changed successfully
        """
        if locale == self._locale:
            return True

        old_locale = self._locale
        self._locale = locale
        self._translations = {}

        try:
            self._load_translations()
            logger.info(f"Locale changed from {old_locale} to {locale}")
            return True
        except Exception as e:
            logger.error(f"Failed to load locale {locale}: {e}")
            self._locale = old_locale
            self._load_translations()
            return False

    def resolve_locale(self, locale: str) -> str:
        """
        Return the best translated locale for ``locale``.

        Regional variants have no translation of their own: the installer's live ISO
        can boot with ``kbd.locale=fr_BE``/``de_CH``/``fr_CA``, and the locale view offers them as
        system locales. They must still show a translated UI, so fall back to a
        locale of the same language (fr_BE -> fr_FR) before giving up on English.
        """
        locale = locale.split(".")[0]
        available = self.available_locales
        if locale in available:
            return locale

        language = locale.split("_")[0]
        same_language = [code for code in available if code.split("_")[0] == language]
        if not same_language:
            return self._fallback_locale

        canonical = f"{language}_{language.upper()}"
        return canonical if canonical in same_language else same_language[0]

    def _load_translations(self) -> None:
        """Load translation files for current and fallback locales."""
        # Load fallback first
        if self._fallback_locale != self._locale:
            self._fallback_translations = self._load_locale_file(self._fallback_locale)

        resolved = self.resolve_locale(self._locale)
        if resolved != self._locale:
            logger.info(f"No translations for {self._locale}, using {resolved}")

        self._translations = self._load_locale_file(resolved)

        if not self._translations:
            logger.warning(f"No translations found for locale {self._locale}, using fallback")
            self._translations = self._fallback_translations.copy()

    def _load_locale_file(self, locale: str) -> dict[str, dict[str, str]]:
        """
        Load a specific locale file.

        Args:
            locale: Locale code

        Returns:
            Nested dict of section -> key -> value
        """
        locale_file = self._i18n_dir / f"{locale}.conf"

        if not locale_file.exists():
            logger.warning(f"Locale file not found: {locale_file}")
            return {}

        try:
            config = configparser.ConfigParser(interpolation=None)
            config.read(locale_file, encoding="utf-8")

            translations: dict[str, dict[str, str]] = {}
            for section in config.sections():
                translations[section] = dict(config.items(section))

            logger.debug(f"Loaded {len(translations)} sections from {locale_file}")
            return translations

        except Exception as e:
            logger.error(f"Failed to parse locale file {locale_file}: {e}")
            return {}

    def get(
        self,
        key: str,
        section: str = "common",
        default: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Get a translated string.

        Args:
            key: Translation key
            section: Section name (default: "common")
            default: Default value if key not found
            **kwargs: Format arguments for string interpolation

        Returns:
            Translated string with format arguments applied
        """
        # Try current locale
        value = self._translations.get(section, {}).get(key)

        # Try fallback locale
        if value is None:
            value = self._fallback_translations.get(section, {}).get(key)

        # Use default or key as last resort
        if value is None:
            if default is not None:
                value = default
            else:
                logger.debug(f"Missing translation: [{section}].{key}")
                value = key

        # Apply format arguments
        if kwargs:
            try:
                value = value.format(**kwargs)
            except KeyError as e:
                logger.warning(f"Missing format key {e} in translation [{section}].{key}")

        return value

    def t(self, key: str, section: str = "common", **kwargs: Any) -> str:
        """Shorthand for get()."""
        return self.get(key, section, **kwargs)

    def section(self, section: str) -> dict[str, str]:
        """
        Get all translations for a section.

        Args:
            section: Section name

        Returns:
            Dict of key -> translated value
        """
        result = self._fallback_translations.get(section, {}).copy()
        result.update(self._translations.get(section, {}))
        return result

    def has_key(self, key: str, section: str = "common") -> bool:
        """Check if a translation key exists."""
        return key in self._translations.get(section, {}) or key in self._fallback_translations.get(
            section, {}
        )


def get_translator() -> Translator:
    """Get the global translator instance."""
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator


def set_locale(locale: str) -> bool:
    """Set the global locale."""
    return get_translator().set_locale(locale)


def tr(key: str, section: str = "common", **kwargs: Any) -> str:
    """Shorthand for global translation lookup."""
    return get_translator().get(key, section, **kwargs)

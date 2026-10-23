"""
TranslatorProxy - Bridge between Python translator and QML.

Enables live language switching in the QML interface by:
1. Loading Qt translation files (.qm)
2. Emitting signals to trigger QML retranslation
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QCoreApplication,
    QLocale,
    QObject,
    QTranslator,
    Signal,
    Slot,
)

if TYPE_CHECKING:
    from PySide6.QtQml import QQmlApplicationEngine

from xen.i18n.translator import get_translator

logger = logging.getLogger(__name__)


class TranslatorProxy(QObject):
    """
    Qt-based translator proxy for live language switching.

    This class:
    - Manages Qt QTranslator for QML qsTr() strings
    - Keeps the Python Translator's locale in sync
    - Emits languageChanged signal to trigger QML updates
    """

    # Signal emitted when language changes - QML should refresh
    languageChanged = Signal()

    def __init__(
        self,
        engine: QQmlApplicationEngine | None = None,
        translations_dir: Path | str | None = None,
        parent: QObject | None = None,
    ) -> None:
        """
        Initialize the translator proxy.

        Args:
            engine: QML engine for retranslation
            translations_dir: Directory containing .qm files
            parent: Qt parent object
        """
        super().__init__(parent)
        self._engine = engine
        self._current_locale = "en_US"
        self._qt_translator: QTranslator | None = None
        self._python_translator = get_translator()

        # Determine translations directory
        if translations_dir is None:
            self._translations_dir = Path(__file__).parent / "translations"
        else:
            self._translations_dir = Path(translations_dir)

        logger.debug(f"TranslatorProxy initialized, translations dir: {self._translations_dir}")

    @Property(str, notify=languageChanged)
    def currentLocale(self) -> str:
        """Get current locale code."""
        return self._current_locale

    @Slot(str, result=bool)
    def setLocale(self, locale: str) -> bool:
        """
        Change the current locale and trigger retranslation.

        Args:
            locale: Locale code (e.g., "fr_FR", "de_DE")

        Returns:
            True if locale was changed successfully
        """
        if locale == self._current_locale:
            logger.debug(f"Locale already set to {locale}")
            return True

        old_locale = self._current_locale
        logger.info(f"Changing locale from {old_locale} to {locale}")

        # Update Python translator
        if not self._python_translator.set_locale(locale):
            logger.warning(f"Failed to set Python translator locale to {locale}")

        # Load Qt translation
        self._load_qt_translation(locale)

        # Update current locale
        self._current_locale = locale

        # Trigger QML retranslation
        if self._engine is not None:
            try:
                self._engine.retranslate()
                logger.debug("QML retranslation triggered")
            except Exception as e:
                logger.error(f"Failed to retranslate QML: {e}")

        # Emit signals
        self.languageChanged.emit()

        logger.info(f"Locale changed to {locale}")
        return True

    def _load_qt_translation(self, locale: str) -> bool:
        """
        Load Qt translation file for the given locale.

        Args:
            locale: Locale code (e.g., "fr_FR.UTF-8" or "fr_FR")

        Returns:
            True if translation was loaded
        """
        app = QCoreApplication.instance()
        if app is None:
            logger.warning("No QCoreApplication instance, cannot load translation")
            return False

        # Remove previous translator
        if self._qt_translator is not None:
            app.removeTranslator(self._qt_translator)
            self._qt_translator = None

        # Create new translator
        self._qt_translator = QTranslator(self)

        # Normalize locale: remove encoding suffix (e.g., "fr_FR.UTF-8" -> "fr_FR")
        normalized_locale = locale.split(".")[0]

        # A regional variant (fr_BE, de_CH, fr_CA...) ships no .qm of its own: fall
        # back to a locale of the same language so the QML stays translated.
        for candidate in (
            normalized_locale,
            self._python_translator.resolve_locale(normalized_locale),
        ):
            qm_file = self._translations_dir / f"xen_{candidate}.qm"
            if not qm_file.exists():
                logger.debug(f"No Qt translation file found: {qm_file}")
                continue
            if self._qt_translator.load(str(qm_file)):
                app.installTranslator(self._qt_translator)
                logger.debug(f"Loaded Qt translation: {qm_file}")
                return True
            logger.warning(f"Failed to load Qt translation: {qm_file}")

        # Try loading from Qt's locale system as fallback
        qt_locale = QLocale(locale.replace("_", "-"))
        if self._qt_translator.load(qt_locale, "xen", "_", str(self._translations_dir)):
            app.installTranslator(self._qt_translator)
            logger.debug(f"Loaded Qt translation using QLocale: {locale}")
            return True

        logger.debug(f"No translation found for {locale}, using default strings")
        return False

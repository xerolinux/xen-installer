"""Live Qt/desktop palette exposed to QML for system-theme sync."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal
from PySide6.QtGui import QGuiApplication, QPalette


class SystemThemeBridge(QObject):
    """Exposes the active Qt platform palette to QML.

    Reflects whatever color scheme the desktop environment has configured
    (KDE color scheme, GTK theme via the Qt platform theme, etc.) and
    updates live if the user switches it during install.
    """

    paletteUpdated = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        app = self._app()
        if app is not None:
            app.paletteChanged.connect(self._on_palette_changed)

    def _on_palette_changed(self, _palette: QPalette) -> None:
        self.paletteUpdated.emit()

    @staticmethod
    def _app() -> QGuiApplication | None:
        app = QGuiApplication.instance()
        return app if isinstance(app, QGuiApplication) else None

    @classmethod
    def _palette(cls) -> QPalette:
        app = cls._app()
        return app.palette() if app is not None else QPalette()

    @Property(bool, notify=paletteUpdated)
    def available(self) -> bool:
        """False when no QGuiApplication exists yet (e.g. unit tests)."""
        return self._app() is not None

    @Property(str, notify=paletteUpdated)
    def windowColor(self) -> str:
        return self._palette().color(QPalette.ColorRole.Window).name()

    @Property(str, notify=paletteUpdated)
    def windowTextColor(self) -> str:
        return self._palette().color(QPalette.ColorRole.WindowText).name()

    @Property(str, notify=paletteUpdated)
    def baseColor(self) -> str:
        return self._palette().color(QPalette.ColorRole.Base).name()

    @Property(str, notify=paletteUpdated)
    def buttonColor(self) -> str:
        return self._palette().color(QPalette.ColorRole.Button).name()

    @Property(str, notify=paletteUpdated)
    def accentColor(self) -> str:
        # QPalette.ColorRole.Accent is unreliable in practice: several
        # platform themes (including KDE's, as tested) leave it unset
        # (black) even when a real accent color is configured. Highlight
        # is the role apps have used for "the accent color" since Qt4 and
        # is reliably populated.
        return self._palette().color(QPalette.ColorRole.Highlight).name()

    @Property(str, notify=paletteUpdated)
    def disabledTextColor(self) -> str:
        return self._palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText).name()

"""
Xen Installer - Entry point.

This module handles:
- Argument parsing
- Process separation (UI vs Engine)
- Application initialization
- IPC between UI (unprivileged) and Engine (root)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from xen import __version__
from xen.core.engine import ConfigurationError

if TYPE_CHECKING:
    from PySide6.QtGui import QGuiApplication, QIcon

    from xen.core.engine import BrandingConfig

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="xen",
        description="Xen Installer - Modern Linux installation system",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"Xen Installer {__version__}",
    )

    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to configuration file (default: auto-detect)",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate installation without making changes",
    )

    parser.add_argument(
        "--platform-info",
        action="store_true",
        help="Display Qt platform information and exit",
    )

    # IPC-related arguments
    parser.add_argument(
        "--engine",
        action="store_true",
        help="Run as engine server (requires root privileges)",
    )

    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="Path to IPC socket (default: /run/xen/ipc.sock)",
    )

    parser.add_argument(
        "--no-fork",
        action="store_true",
        help="Run without forking engine process (for development/testing)",
    )

    parser.add_argument(
        "--skip-requirements",
        action="store_true",
        help="Dev: bypass requirements and per-screen validation "
        "(navigate all screens freely for design testing)",
    )

    return parser.parse_args()


def print_platform_info() -> None:
    """Display Qt platform and environment information."""
    import os

    from PySide6.QtCore import QLibraryInfo, qVersion
    from PySide6.QtGui import QGuiApplication

    # Initialize minimal app to get platform info
    app = QGuiApplication([])

    print("=" * 60)
    print("Xen Installer - Platform Information")
    print("=" * 60)

    # Qt version
    print("\n[Qt]")
    print(f"  Version: {qVersion()}")
    print(f"  Plugins path: {QLibraryInfo.path(QLibraryInfo.PluginsPath)}")  # type: ignore[attr-defined]

    # Platform
    print("\n[Platform]")
    print(f"  Plugin: {app.platformName()}")
    print(f"  DISPLAY: {os.environ.get('DISPLAY', 'not set')}")
    print(f"  WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', 'not set')}")
    print(f"  XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', 'not set')}")
    print(f"  XDG_CURRENT_DESKTOP: {os.environ.get('XDG_CURRENT_DESKTOP', 'not set')}")

    # Theme
    print("\n[Theme]")
    print(f"  QT_QPA_PLATFORMTHEME: {os.environ.get('QT_QPA_PLATFORMTHEME', 'not set')}")
    print(f"  QT_STYLE_OVERRIDE: {os.environ.get('QT_STYLE_OVERRIDE', 'not set')}")

    # Screens
    print("\n[Screens]")
    for i, screen in enumerate(app.screens()):
        print(f"  Screen {i}: {screen.name()}")
        print(f"    Size: {screen.size().width()}x{screen.size().height()}")
        print(f"    DPI: {screen.logicalDotsPerInch():.0f}")
        print(f"    Scale: {screen.devicePixelRatio()}")

    # Available platform plugins
    plugins_path = Path(QLibraryInfo.path(QLibraryInfo.PluginsPath)) / "platforms"  # type: ignore[attr-defined]
    if plugins_path.exists():
        print("\n[Available Platform Plugins]")
        for plugin in sorted(plugins_path.glob("libq*.so")):
            name = plugin.stem.replace("libq", "")
            print(f"  - {name}")

    print("\n" + "=" * 60)
    app.quit()


def find_config_file(explicit_path: Path | None = None) -> Path:
    """
    Locate configuration file.

    Search order:
    1. Explicit path from --config
    2. xen.yaml in current directory
    3. config/examples/xerolinux.yaml (development fallback)
    """
    if explicit_path is not None:
        if explicit_path.exists():
            return explicit_path
        raise ConfigurationError(
            f"Configuration file not found: {explicit_path}\n"
            f"Create one with: cp xen.yaml.example xen.yaml"
        )

    # Search locations: CWD, /etc, puis la config embarquée avec le paquet
    # (paquet Arch : <prefix>/share/xen/config ; dépôt : <repo>/config),
    # résolue en remontant depuis ce module.
    candidates = [
        Path("xen.yaml"),
        Path("config/examples/xerolinux.yaml"),
        Path("/etc/xen/xerolinux.yaml"),
    ]
    here = Path(__file__).resolve()
    for base in list(here.parents)[:6]:
        candidates.append(base / "share" / "xen" / "config" / "examples" / "xerolinux.yaml")
        candidates.append(base / "config" / "examples" / "xerolinux.yaml")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise ConfigurationError(
        "No configuration file found.\n"
        "Create one with: cp xen.yaml.example xen.yaml\n"
        "Or specify: xen --config config/examples/xerolinux.yaml"
    )


def find_qml_file() -> Path:
    """Locate the main QML file."""
    # Check multiple possible locations
    candidates = [
        Path(__file__).parent / "gui" / "qml" / "Main.qml",
        Path("src/xen/gui/qml/Main.qml"),
        Path("/usr/share/xen/qml/Main.qml"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    raise FileNotFoundError("Main.qml not found. Check installation.")


def _resolve_app_icon() -> QIcon:
    """Locate the Xen app icon across dev and packaged layouts.

    Dev checkout: ``<repo>/data/icons/org.xerolinux.xen.svg``. Packaged
    builds ship it in the hicolor theme under ``<prefix>/share/icons/...``.
    Fall back to the icon theme name if no concrete file is found.
    """
    from PySide6.QtGui import QIcon

    here = Path(__file__).resolve()
    name = "org.xerolinux.xen.svg"
    candidates = [here.parents[2] / "data" / "icons" / name]
    candidates += [
        p / "share" / "icons" / "hicolor" / "scalable" / "apps" / name for p in here.parents
    ]
    for candidate in candidates:
        if candidate.exists():
            return QIcon(str(candidate))
    return QIcon.fromTheme("org.xerolinux.xen")


def create_application(branding: BrandingConfig) -> QGuiApplication:
    """Create and configure Qt application."""
    from PySide6.QtGui import QGuiApplication, QSurfaceFormat
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQuickControls2 import QQuickStyle

    # Force the Fusion Quick Controls style. Every view in this app overrides
    # `background:`/`contentItem:` on its controls (TextField, CheckBox,
    # RadioButton, ComboBox, ...) - Fusion is a neutral base that respects
    # those overrides cleanly. Without this, Qt auto-selects whatever style
    # is discoverable at runtime: on a real KDE session with qqc2-desktop-style
    # installed (the actual XeroLinux target - "KDE-first" per its own
    # branding) that's org.kde.desktop, which imposes its own native
    # decorations on top of our custom ones and visibly breaks control
    # sizing/padding (pill-shaped fields, misaligned placeholder text). Must
    # be set before the QGuiApplication/window is created.
    QQuickStyle.setStyle("Fusion")

    # Request an alpha-capable surface so the QML window's semi-transparent
    # background (see Main.qml's Window.color) actually composites with the
    # desktop instead of rendering as opaque. Both calls are needed: the
    # scenegraph (QQuickWindow) picks its own surface format independently
    # of QSurfaceFormat::defaultFormat() under Qt6's RHI backends. Must be
    # set before the QGuiApplication/window is created.
    surface_format = QSurfaceFormat()
    surface_format.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(surface_format)
    QQuickWindow.setDefaultAlphaBuffer(True)

    app = QGuiApplication(sys.argv)

    # Set application metadata from branding
    app.setApplicationName(branding.name)
    app.setApplicationVersion(branding.version)
    app.setOrganizationName("Xen")

    # Wayland associates the window with its .desktop file (app_id) to pick up
    # the taskbar/dock icon; setWindowIcon covers X11 and other compositors.
    app.setDesktopFileName("xen")
    app.setWindowIcon(_resolve_app_icon())

    return app


def _register_theme_fonts(theme_base: Path) -> None:
    fonts_dir = theme_base / "fonts"
    if not fonts_dir.is_dir():
        return

    from PySide6.QtGui import QFontDatabase

    for font_file in sorted(fonts_dir.iterdir()):
        if font_file.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            continue
        if QFontDatabase.addApplicationFont(str(font_file)) < 0:
            logger.warning("Theme font not loaded: %s", font_file.name)
        else:
            logger.debug("Theme font loaded: %s", font_file.name)


def run_ui_mode(
    config_path: Path,
    debug: bool,
    dry_run: bool,
    skip_requirements: bool = False,
) -> int:
    """
    Run the UI process.

    Runs as a single process: the whole app (GUI + job execution) is
    launched elevated via pkexec (see data/xen-polkit) - no separate
    engine process or IPC hop.

    Args:
        config_path: Path to configuration file
        debug: Enable debug mode
        dry_run: Enable dry-run mode

    Returns:
        Exit code
    """
    # Import Qt modules here to avoid loading in engine mode
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QWindow
    from PySide6.QtQml import QQmlApplicationEngine

    from xen.core.engine import Engine
    from xen.gui.blur_behind import enable_blur_behind
    from xen.gui.bridge import EngineBridge
    from xen.gui.theme_bridge import SystemThemeBridge
    from xen.gui.translator_proxy import TranslatorProxy

    # Load configuration for branding info
    engine = Engine.from_config_file(config_path)
    branding = engine.get_branding()

    # Resolve theme path
    theme_path = engine.get_theme_path()
    theme_base = (config_path.parent / theme_path).resolve() if theme_path else config_path.parent

    if debug:
        print(f"Theme base: {theme_base}")

    # Create Qt application
    app = create_application(branding)

    # Register fonts hand-placed in <theme>/fonts so the families named in
    # theme.yaml (primary/display/monospace) resolve even when the
    # system doesn't have them installed.
    _register_theme_fonts(theme_base)

    # Set up QML engine
    qml_engine = QQmlApplicationEngine()

    bridge = EngineBridge(
        engine,
        theme_base,
        debug=debug,
        dry_run=dry_run,
        skip_requirements=skip_requirements,
    )

    # Create translator proxy for live language switching
    translator_proxy = TranslatorProxy(engine=qml_engine)

    # Live system palette (KDE color scheme, etc.), synced to QML
    system_theme = SystemThemeBridge()

    # Expose bridge and translator to QML
    qml_engine.rootContext().setContextProperty("engine", bridge)
    qml_engine.rootContext().setContextProperty("branding", bridge.branding_proxy)
    qml_engine.rootContext().setContextProperty("translator", translator_proxy)
    qml_engine.rootContext().setContextProperty("systemTheme", system_theme)

    # Load QML
    try:
        qml_file = find_qml_file()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    qml_engine.load(QUrl.fromLocalFile(str(qml_file)))

    if not qml_engine.rootObjects():
        print("Error: Failed to load QML interface", file=sys.stderr)
        return 1

    window = qml_engine.rootObjects()[0]
    if isinstance(window, QWindow):
        enable_blur_behind(window)

    # Run application
    return app.exec()


def main() -> int:
    """
    Application entry point.

    Handles two modes:
    1. --engine: Run as a standalone engine server over IPC (legacy;
       nothing in the shipped app invokes this - see run_ui_mode's
       docstring for the actual, single-process elevation model)
    2. Normal: Run the GUI (elevated via pkexec/sudo, no forking)

    Returns:
        Exit code (0 = success)
    """
    args = parse_args()

    # Qt Quick software backend by default: the installer runs on arbitrary
    # live-boot hardware with no guaranteed working GL/EGL stack (QRhiGles2
    # fails, "EGL not available", window never renders) and needs no GPU
    # acceleration. Set before any Qt object is created; override still honored.
    os.environ.setdefault("QT_QUICK_BACKEND", "software")

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s]: %(message)s",
    )

    # Handle --platform-info
    if args.platform_info:
        print_platform_info()
        return 0

    # Find configuration file
    try:
        config_path = find_config_file(args.config)
        if args.debug:
            print(f"Using config: {config_path}")
    except ConfigurationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Engine mode: run as IPC server
    if args.engine:
        from xen.launcher import DEFAULT_SOCKET_PATH, run_engine_server

        socket_path = args.socket or DEFAULT_SOCKET_PATH
        return run_engine_server(
            config_path=config_path,
            socket_path=socket_path,
            debug=args.debug,
            dry_run=args.dry_run,
        )

    # UI mode: run graphical interface. The whole process runs elevated via
    # pkexec/sudo (see data/xen-polkit) - single process, no fork/IPC hop.
    # --no-fork is accepted but has no effect: that was always the only
    # correct way to run this (see run_ui_mode's docstring); kept as a
    # no-op flag so existing invocations (data/xen-polkit) don't break.
    return run_ui_mode(
        config_path=config_path,
        debug=args.debug,
        dry_run=args.dry_run,
        skip_requirements=args.skip_requirements,
    )


if __name__ == "__main__":
    sys.exit(main())

"""KWin blur-behind hint for the installer window (X11/XWayland only).

Sets the X11 window property ``_KDE_NET_WM_BLUR_BEHIND_REGION``, so it only
takes effect when Qt runs on the ``xcb`` platform plugin: a native X11
session, or XWayland when the app is forced onto xcb. On native Wayland
(Qt's ``wayland`` platform) it is a no-op; KWin's Wayland-side blur goes
through its own ``org_kde_kwin_blur`` protocol, which this module does not
implement. Non-KWin compositors ignore the property.
"""

from __future__ import annotations

import ctypes
import logging

from PySide6.QtGui import QGuiApplication, QWindow

logger = logging.getLogger(__name__)

_BLUR_ATOM_NAME = b"_KDE_NET_WM_BLUR_BEHIND_REGION"
_XA_CARDINAL = 6
_PROP_MODE_REPLACE = 0


def enable_blur_behind(window: QWindow) -> bool:
    """Ask KWin to blur the desktop behind ``window``.

    Returns True if the property was set, False if blur isn't available
    on this platform (never raises).
    """
    if QGuiApplication.platformName() != "xcb":
        logger.debug(
            "Blur-behind skipped: platform '%s' is not X11/XWayland",
            QGuiApplication.platformName(),
        )
        return False

    try:
        libx11 = ctypes.CDLL("libX11.so.6")
    except OSError:
        logger.debug("Blur-behind skipped: libX11.so.6 not available")
        return False

    libx11.XOpenDisplay.restype = ctypes.c_void_p
    libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    libx11.XInternAtom.restype = ctypes.c_ulong
    libx11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    libx11.XChangeProperty.restype = ctypes.c_int
    libx11.XChangeProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    libx11.XFlush.argtypes = [ctypes.c_void_p]
    libx11.XCloseDisplay.argtypes = [ctypes.c_void_p]

    display = libx11.XOpenDisplay(None)
    if not display:
        logger.debug("Blur-behind skipped: could not open X11 display")
        return False

    try:
        atom = libx11.XInternAtom(display, _BLUR_ATOM_NAME, 0)
        # Zero-length CARDINAL property = blur the whole window.
        libx11.XChangeProperty(
            display,
            int(window.winId()),
            atom,
            _XA_CARDINAL,
            32,
            _PROP_MODE_REPLACE,
            None,
            0,
        )
        libx11.XFlush(display)
    finally:
        libx11.XCloseDisplay(display)

    logger.info("Blur-behind enabled via XWayland/X11")
    return True

"""Live keyboard layout application for the running installer session.

The installer's target-system keyboard config is written by the ``locale``
job (see ``xen.jobs.locale``) and is unaffected by this module. This module
only handles the best-effort, cosmetic affordance of switching the *live*
session's keyboard layout so the on-screen keyboard test field (and every
other input in the installer) immediately reflects the user's choice.

On Wayland ``setxkbmap`` has no effect: the compositor owns the layout. KDE
Plasma (the XeroLinux live session) reloads ``kxkbrc`` when KConfig notifies
a change to it; GNOME uses
``org.gnome.desktop.input-sources``. ``setxkbmap`` is the X11 fallback.
"""

from __future__ import annotations

import logging
import os
import pwd
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 5  # seconds


def _session_user() -> tuple[str, dict[str, str]] | None:
    """Utilisateur de la session graphique + env DBus, quand on tourne en root.

    L'installeur s'exécute en root (pkexec ou sudo) ; ``gsettings`` doit viser le dconf de
    la session GNOME de l'utilisateur, pas celui de root. Retourne ``None`` si on
    n'est pas root (on est déjà l'utilisateur) ou si l'uid est introuvable.
    """
    if os.geteuid() != 0:
        return None
    uid: int | None = None
    for var in ("PKEXEC_UID", "SUDO_UID"):
        value = os.environ.get(var, "")
        if value.isdigit() and int(value) != 0:
            uid = int(value)
            break
    if uid is None:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        if runtime.startswith("/run/user/"):
            try:
                uid = int(runtime.rsplit("/", 1)[1])
            except ValueError:
                uid = None
    if uid is None:
        uid = 1000
    try:
        entry = pwd.getpwuid(uid)
    except KeyError:
        return None
    user = entry.pw_name
    env = {
        "HOME": entry.pw_dir,
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
    }
    return user, env


def _in_session(cmd: list[str]) -> list[str] | None:
    """Enrobe ``cmd`` pour l'exécuter dans la session de l'utilisateur si root."""
    su = _session_user()
    if su is None:
        return cmd
    user, env = su
    if not shutil.which("runuser"):
        return None
    env_args = [f"{k}={v}" for k, v in env.items()]
    return ["runuser", "-u", user, "--", "env", *env_args, *cmd]


def build_xkb_layout_string(layout: str, variant: str) -> str:
    """Build the XKB ``layout[+variant]`` string, e.g. ``fr`` or ``fr+azerty``."""
    if variant:
        return f"{layout}+{variant}"
    return layout


def apply_keyboard_layout_live(layout: str, variant: str = "") -> bool:
    """
    Apply a keyboard layout to the running desktop session (best-effort).

    Args:
        layout: XKB layout code (e.g. "fr", "us").
        variant: XKB variant code (e.g. "azerty"), or "" for the default.

    Returns:
        True if a live-apply command was executed successfully, False
        otherwise. Never raises: this is a cosmetic UX affordance and must
        not break the installer if the session tooling is unavailable.
    """
    if not layout:
        return False

    if _kwin_running() and shutil.which("kwriteconfig6"):
        return _apply_via_kwin(layout, variant)

    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()

    if session_type != "x11" and shutil.which("gsettings"):
        return _apply_via_gsettings(build_xkb_layout_string(layout, variant))

    if shutil.which("setxkbmap"):
        return _apply_via_setxkbmap(layout, variant)

    # Wayland without setxkbmap: gsettings is still worth a try even if the
    # session-type check above missed it (e.g. unset XDG_SESSION_TYPE).
    if shutil.which("gsettings"):
        return _apply_via_gsettings(build_xkb_layout_string(layout, variant))

    logger.warning("No supported keyboard layout tool found (gsettings/setxkbmap)")
    return False


def _kwin_running() -> bool:
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            if comm.read_text().strip() in ("kwin_wayland", "kwin_x11"):
                return True
        except OSError:
            continue
    return False


def _apply_via_kwin(layout: str, variant: str) -> bool:
    # KWin's KConfigWatcher reloads the layout only on --notify writes
    write = ["kwriteconfig6", "--notify", "--file", "kxkbrc", "--group", "Layout", "--key"]
    cmds = [[*write, "Use", "true"], [*write, "LayoutList", layout], [*write, "VariantList", variant]]
    wrapped = [_in_session(cmd) for cmd in cmds]
    if any(cmd is None for cmd in wrapped):
        logger.warning("Cannot reach the user session (runuser missing) for kwriteconfig6")
        return False
    try:
        for cmd in wrapped:
            subprocess.run(cmd, check=True, timeout=_COMMAND_TIMEOUT)
        logger.info(f"Applied live keyboard layout via KWin: {layout} {variant}")
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning(f"Failed to apply keyboard layout via KWin: {exc}")
        return False


def _apply_via_gsettings(xkb_layout: str) -> bool:
    """Apply the layout via GNOME's input-sources (GNOME Wayland/X11)."""
    sources_value = f"[('xkb', '{xkb_layout}')]"
    set_sources = _in_session(
        ["gsettings", "set", "org.gnome.desktop.input-sources", "sources", sources_value]
    )
    set_current = _in_session(
        ["gsettings", "set", "org.gnome.desktop.input-sources", "current", "0"]
    )
    if set_sources is None or set_current is None:
        logger.warning("Cannot reach the user session (runuser missing) for gsettings")
        return False
    try:
        subprocess.run(set_sources, check=True, timeout=_COMMAND_TIMEOUT)
        subprocess.run(set_current, check=True, timeout=_COMMAND_TIMEOUT)
        logger.info(f"Applied live keyboard layout via gsettings: {xkb_layout}")
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning(f"Failed to apply keyboard layout via gsettings: {exc}")
        return False


def _apply_via_setxkbmap(layout: str, variant: str) -> bool:
    """Apply the layout via setxkbmap (X11 sessions)."""
    cmd = ["setxkbmap", layout]
    if variant:
        cmd += ["-variant", variant]
    wrapped = _in_session(cmd)
    if wrapped is None:
        logger.warning("Cannot reach the user session (runuser missing) for setxkbmap")
        return False
    try:
        subprocess.run(wrapped, check=True, timeout=_COMMAND_TIMEOUT)
        logger.info(f"Applied live keyboard layout via setxkbmap: {layout} {variant}")
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning(f"Failed to apply keyboard layout via setxkbmap: {exc}")
        return False

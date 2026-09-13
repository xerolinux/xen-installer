"""Live keyboard layout application for the running installer session.

The installer's target-system keyboard config is written by the ``locale``
job (see ``xen.jobs.locale``) and is unaffected by this module. This module
only handles the best-effort, cosmetic affordance of switching the *live*
session's keyboard layout so the on-screen keyboard test field (and every
other input in the installer) immediately reflects the user's choice.

On GNOME Wayland — the live session used by this installer — ``setxkbmap`` has no
effect because layout switching is owned by the compositor via
``org.gnome.desktop.input-sources``. This module therefore prefers
``gsettings`` and falls back to ``setxkbmap`` for X11 sessions or when
``gsettings`` is unavailable.
"""

from __future__ import annotations

import logging
import os
import pwd
import shutil
import subprocess

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 5  # seconds


def _session_user() -> tuple[str, dict[str, str]] | None:
    """Utilisateur de la session graphique + env DBus, quand on tourne en root.

    L'installeur s'exécute en root (sudo) ; ``gsettings`` doit viser le dconf de
    la session GNOME de l'utilisateur, pas celui de root. Retourne ``None`` si on
    n'est pas root (on est déjà l'utilisateur) ou si l'uid est introuvable.
    """
    if os.geteuid() != 0:
        return None
    uid: int | None = None
    sudo_uid = os.environ.get("SUDO_UID", "")
    if sudo_uid.isdigit() and int(sudo_uid) != 0:
        uid = int(sudo_uid)
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
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        return None
    env = {
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

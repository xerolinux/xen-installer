"""
Offscreen integration test: UsersView's client-side username/hostname
validation must accept and reject exactly what UsersJob accepts and rejects,
so the wizard never lets through a value the install will later refuse.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QtMsgType, QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.gui.translator_proxy import TranslatorProxy  # noqa: E402
from xen.jobs.base import JobContext  # noqa: E402
from xen.jobs.users import UsersJob  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
MAIN_QML = PROJECT_ROOT / "src" / "xen" / "gui" / "qml" / "Main.qml"

USERNAMES = [
    "alice", "_svc", "a" * 32, "a" * 33, "Alice", "1abc", "ab-c_d", "ab c", "root", "liveuser", "",
]
HOSTNAMES = [
    "my-pc", "1box", "abc-", "-abc", "a" * 63, "a" * 64, "my-pc.lan", "my..pc",
    "my-pc.", "localhost", "My-PC", "pc_1", "",
]
FULL_NAMES = ["Alice Smith", "", "Alice: Admin", "Alice\nSmith", "Alice\rSmith"]
PASSWORDS = ["S3cret!pass", "abc\ndef", "abc\rdef"]


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def _find(root: Any, name: str) -> Any | None:
    stack = [root]
    seen: set[int] = set()
    refs: list[Any] = []
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        refs.append(obj)
        if obj.objectName() == name:
            return obj
        stack.extend(obj.children())
        if isinstance(obj, QQuickItem):
            stack.extend(obj.childItems())
    return None


def _job_username_ok(name: str) -> bool:
    return UsersJob._validate_username(UsersJob.__new__(UsersJob), name) and name not in (
        UsersJob.RESERVED_USERNAMES
    )


def _job_hostname_ok(name: str) -> bool:
    return UsersJob._validate_hostname(UsersJob.__new__(UsersJob), name)


def _job_accepts(fullname: str, password: str, root_password: str) -> bool:
    context = JobContext(selections={
        "username": "alice", "hostname": "my-pc", "fullname": fullname,
        "password": password, "root_password": root_password,
    })
    return UsersJob().validate(context).success


def test_users_view_validation_matches_users_job(qapp: QApplication) -> None:
    warnings: list[str] = []

    def handler(msg_type: QtMsgType, _ctx: object, message: str) -> None:
        if msg_type != QtMsgType.QtDebugMsg and msg_type != QtMsgType.QtInfoMsg:
            warnings.append(message)

    qInstallMessageHandler(handler)
    bridge = EngineBridge(
        Engine.from_config_file(XEROLINUX_CONFIG), XEROLINUX_CONFIG.parent,
        debug=False, dry_run=True, skip_requirements=True,
    )
    qml_engine = QQmlApplicationEngine()
    translator = TranslatorProxy(engine=qml_engine)
    ctx = qml_engine.rootContext()
    ctx.setContextProperty("engine", bridge)
    ctx.setContextProperty("branding", bridge.branding_proxy)
    ctx.setContextProperty("translator", translator)
    qml_engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    try:
        root = qml_engine.rootObjects()[0]
        root.setProperty("currentStep", 4)
        qapp.processEvents()
        view = _find(root, "usersView")
        assert view is not None
        user_field = _find(view, "usernameField")
        host_field = _find(view, "hostnameField")

        for name in USERNAMES:
            user_field.setProperty("text", name)
            qapp.processEvents()
            assert bool(view.property("usernameValid")) == _job_username_ok(name), name

        # The live ISO's passwordless polkit rule matches this account name.
        user_field.setProperty("text", "liveuser")
        qapp.processEvents()
        assert bool(view.property("usernameValid")) is False

        for name in HOSTNAMES:
            host_field.setProperty("text", name)
            qapp.processEvents()
            assert bool(view.property("hostnameValid")) == _job_hostname_ok(name), name

        user_field.setProperty("text", "Bad")
        qapp.processEvents()
        hint = _find(view, "usernameHint").property("text")
        assert "start with a letter" not in hint
        assert "underscore" in hint

        user_field.setProperty("text", "alice")
        host_field.setProperty("text", "my-pc")
        full_field = _find(view, "fullNameField")
        pass_field = _find(view, "passwordField")
        confirm_field = _find(view, "passwordConfirmField")
        good = PASSWORDS[0]
        pass_field.setProperty("text", good)
        confirm_field.setProperty("text", good)
        for name in FULL_NAMES:
            full_field.setProperty("text", name)
            qapp.processEvents()
            assert full_field.property("text") == name
            assert bool(view.property("isValid")) == _job_accepts(name, good, good), repr(name)
        full_hint = _find(view, "fullNameHint")
        assert "line breaks" in full_hint.property("text")

        full_field.setProperty("text", "Alice")
        for pw in PASSWORDS:
            pass_field.setProperty("text", pw)
            confirm_field.setProperty("text", pw)
            qapp.processEvents()
            assert pass_field.property("text") == pw
            assert bool(view.property("isValid")) == _job_accepts("Alice", pw, pw), repr(pw)
        assert bool(_find(view, "passwordLineBreakError").property("visible")) is True

        pass_field.setProperty("text", good)
        confirm_field.setProperty("text", good)
        bridge.setRootSameAsUser(False)
        root_field = _find(view, "rootPasswordField")
        root_confirm = _find(view, "rootPasswordConfirmField")
        for pw in PASSWORDS:
            root_field.setProperty("text", pw)
            root_confirm.setProperty("text", pw)
            qapp.processEvents()
            assert bool(view.property("isValid")) == _job_accepts("Alice", good, pw), repr(pw)
        assert bool(_find(view, "rootPasswordLineBreakError").property("visible")) is True
        assert not any("Users" in w and "qml" in w for w in warnings), warnings
    finally:
        qInstallMessageHandler(None)
        del qml_engine

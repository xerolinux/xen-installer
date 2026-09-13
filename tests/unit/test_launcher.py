"""
Engine-server root-privilege gate.

The engine process runs partitioning, mounting, arch-chroot, and bootloader
installation - all of which require root, same as Calamares. It must refuse
to start unprivileged rather than fail midway through a destructive step
with the disk already wiped.
"""

from __future__ import annotations

import pytest

from xen import launcher


def test_run_engine_server_refuses_non_root_without_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "check_root_privileges", lambda: False)

    def _fail_if_called(*_a, **_kw):
        raise AssertionError("Engine.from_config_file must not run unprivileged")

    monkeypatch.setattr(
        "xen.core.engine.Engine.from_config_file",
        staticmethod(_fail_if_called),
    )

    result = launcher.run_engine_server(
        config_path=tmp_path / "does-not-matter.yaml",
        socket_path=tmp_path / "ipc.sock",
        dry_run=False,
    )

    assert result == 1


def test_run_engine_server_allows_dry_run_without_root(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "check_root_privileges", lambda: False)

    called = False

    def _fake_from_config_file(_path):
        nonlocal called
        called = True
        raise SystemExit("stop before real server startup")

    monkeypatch.setattr(
        "xen.core.engine.Engine.from_config_file",
        staticmethod(_fake_from_config_file),
    )

    with pytest.raises(SystemExit):
        launcher.run_engine_server(
            config_path=tmp_path / "does-not-matter.yaml",
            socket_path=tmp_path / "ipc.sock",
            dry_run=True,
        )

    assert called is True


def test_check_root_privileges_matches_getuid(monkeypatch):
    monkeypatch.setattr(launcher.os, "getuid", lambda: 0)
    assert launcher.check_root_privileges() is True

    monkeypatch.setattr(launcher.os, "getuid", lambda: 1000)
    assert launcher.check_root_privileges() is False

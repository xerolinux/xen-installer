"""Config-file resolution in xen.main."""

from __future__ import annotations

from xen import main


def test_find_config_file_falls_back_to_xerolinux(monkeypatch, tmp_path):
    """Regression test: with no local xen.yaml and no explicit path, the
    real find_config_file() must fall back to the packaged
    config/examples/xerolinux.yaml, not a stale distro default."""
    monkeypatch.chdir(tmp_path)
    config_path = main.find_config_file()
    assert config_path.name == "xerolinux.yaml"

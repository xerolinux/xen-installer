from pathlib import Path

import yaml

from xen.core.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
BOOT_CONFIG = ROOT / "scripts" / "vmtest" / "boot_config.yaml"
PRODUCTION = ROOT / "config" / "examples" / "xerolinux.yaml"
DROPPED = {"welcome", "packagechooser", "systemupdate"}


def _jobs(path):
    jobs = yaml.safe_load(path.read_text())["jobs"]
    return [(j["name"], j.get("config") or {}) for j in jobs]


def test_boot_config_is_the_production_job_list_minus_gui_and_upgrade_jobs():
    production = [(n, c) for n, c in _jobs(PRODUCTION) if n not in DROPPED]
    boot = _jobs(BOOT_CONFIG)
    assert [n for n, _ in boot] == [n for n, _ in production]
    for (name, config), (_, expected) in zip(boot, production, strict=True):
        if name == "bootloader":
            config = {k: v for k, v in config.items() if k != "extra_kernel_params"}
        assert config == expected, name


def test_boot_config_puts_the_installed_kernel_on_the_serial_console():
    bootloader = dict(_jobs(BOOT_CONFIG))["bootloader"]
    assert bootloader["extra_kernel_params"] == ["console=ttyS0"]


def test_boot_config_loads():
    assert Engine.from_config_file(BOOT_CONFIG).get_job_names()[0] == "partition"

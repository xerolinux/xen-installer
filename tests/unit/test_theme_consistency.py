"""
Tests for theme and configuration consistency.

XeroLinux configs use inline `branding:` with no separate `theme:`
directory. These tests validate that pattern stays consistent: configs
load, branding resolves, and no `theme:` key is set.
"""

from pathlib import Path

import pytest

try:
    import yaml

    HAS_YAML = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    HAS_YAML = False

try:
    from xen.core.engine import Engine, XenConfig

    HAS_XEN = True
except ImportError:
    Engine = None  # type: ignore[assignment, misc]
    XenConfig = None  # type: ignore[assignment, misc]
    HAS_XEN = False

# Skip entire module if dependencies are missing
pytestmark = pytest.mark.skipif(
    not (HAS_YAML and HAS_XEN),
    reason="Required dependencies (yaml, xen) not available",
)

# Path to config examples directory
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "config" / "examples"


class TestInlineBrandingConfigs:
    """Tests for the inline-branding pattern (no theme: directory)."""

    @pytest.fixture
    def xerolinux_path(self) -> Path:
        """Path to the XeroLinux configuration (single profile, online+offline)."""
        return EXAMPLES_DIR / "xerolinux.yaml"

    def test_xerolinux_config_exists(self, xerolinux_path: Path) -> None:
        """XeroLinux config file should exist."""
        assert xerolinux_path.exists(), f"Config not found: {xerolinux_path}"

    def test_xerolinux_has_no_theme_key(self, xerolinux_path: Path) -> None:
        """XeroLinux config should use inline branding, not a theme directory."""
        with xerolinux_path.open("r") as f:
            raw = yaml.safe_load(f)
        config = XenConfig.model_validate(raw)
        assert not config.theme, "XeroLinux configs use inline branding, not a theme: key"


class TestConfigThemeIntegration:
    """Integration tests for config loading and branding resolution."""

    def test_engine_loads_xerolinux_config(self) -> None:
        """Engine should load the XeroLinux config without errors."""
        config_path = EXAMPLES_DIR / "xerolinux.yaml"
        engine = Engine.from_config_file(config_path)
        assert engine.config.branding.name == "XeroLinux"

    def test_xerolinux_theme_path_is_empty(self) -> None:
        """Engine should report an empty theme path for inline-branding configs."""
        config_path = EXAMPLES_DIR / "xerolinux.yaml"
        engine = Engine.from_config_file(config_path)
        assert engine.get_theme_path() == ""


class TestMultipleConfigs:
    """Tests for multiple configuration files."""

    @pytest.mark.parametrize(
        "config_name",
        ["archlinux.yaml", "xerolinux.yaml", "xerolinux-nvidia.yaml", "minimal.yaml"],
    )
    def test_example_config_is_valid(self, config_name: str) -> None:
        """All example configs should be valid."""
        config_path = EXAMPLES_DIR / config_name
        if not config_path.exists():
            pytest.skip(f"Config {config_name} not found")

        with config_path.open("r") as f:
            raw = yaml.safe_load(f)

        # Should not raise validation error
        config = XenConfig.model_validate(raw)
        assert config.branding.name, f"{config_name} missing branding name"

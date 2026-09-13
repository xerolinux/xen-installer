"""Unit tests for BrandingProxy's per-profile brand color/edition label.

Purely config-driven, no hardware detection: xerolinux.yaml (Intel/AMD,
one ISO for both) always shows a blue-to-red gradient; xerolinux-nvidia.yaml
always shows solid green. Each profile already declares its own identity
via its `edition` config value.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import BrandingProxy  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
XEROLINUX_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux.yaml"
NVIDIA_CONFIG = PROJECT_ROOT / "config" / "examples" / "xerolinux-nvidia.yaml"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def _make_proxy(qapp: QApplication, config_path: Path) -> BrandingProxy:
    assert qapp is not None
    engine = Engine.from_config_file(config_path)
    return BrandingProxy(engine, config_path.parent, debug=False)


class TestVendorColor:
    def test_intel_amd_profile_is_blue_to_red_gradient(self, qapp: QApplication) -> None:
        proxy = _make_proxy(qapp, XEROLINUX_CONFIG)
        assert proxy.primaryColor == "#5597e6"
        assert proxy.accentColor == "#5597e6"
        assert proxy.accentGradientEnd == "#ED1C24"

    def test_intel_amd_profile_has_no_edition_label(self, qapp: QApplication) -> None:
        proxy = _make_proxy(qapp, XEROLINUX_CONFIG)
        assert proxy.editionLabel == ""

    def test_nvidia_profile_is_solid_green(self, qapp: QApplication) -> None:
        proxy = _make_proxy(qapp, NVIDIA_CONFIG)
        assert proxy.primaryColor == "#76B900"
        assert proxy.accentColor == "#76B900"
        assert proxy.accentGradientEnd == "#76B900"

    def test_nvidia_profile_edition_label(self, qapp: QApplication) -> None:
        proxy = _make_proxy(qapp, NVIDIA_CONFIG)
        assert proxy.editionLabel == "nVidia Edition"

    def test_intel_amd_background_gradient_matches_accent(self, qapp: QApplication) -> None:
        proxy = _make_proxy(qapp, XEROLINUX_CONFIG)
        assert proxy.backgroundGradientStart == "#5597e6"
        assert proxy.backgroundGradientEnd == "#ED1C24"

    def test_nvidia_background_gradient_is_muted_not_brand_green(
        self, qapp: QApplication
    ) -> None:
        """Regression: the raw nvidia brand green washed across a whole
        page background read as sickly - background gets its own muted
        green-to-grey pair, distinct from the vivid button/badge green."""
        proxy = _make_proxy(qapp, NVIDIA_CONFIG)
        assert proxy.backgroundGradientStart != proxy.primaryColor
        assert proxy.backgroundGradientEnd != proxy.primaryColor
        assert proxy.backgroundGradientStart == "#4a5a3d"
        assert proxy.backgroundGradientEnd == "#43453f"

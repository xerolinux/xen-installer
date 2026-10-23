from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402
from xen.i18n.messages import msg  # noqa: E402

CONFIG = Path(__file__).resolve().parents[2] / "config" / "examples" / "minimal.yaml"
FRENCH = {"In use": "Utilisée", "Installing {count} packages...": "Installation de {count} paquets..."}


@pytest.fixture
def bridge() -> Iterator[EngineBridge]:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    with patch("xen.gui.bridge.NetworkHelper.check_internet_connectivity", return_value=True):
        yield EngineBridge(Engine.from_config_file(CONFIG), CONFIG.parent, dry_run=True, skip_requirements=True)


@pytest.fixture
def french() -> Iterator[None]:
    with patch("xen.i18n.messages._translate", lambda _c, t: FRENCH.get(t, t)):
        yield


def test_message_properties_are_read_in_the_current_language(bridge: EngineBridge, french: None) -> None:
    bridge._plan_errors = [msg("In use")]
    bridge._plan_warnings = [msg("In use")]
    bridge._plan_checklist = [{"text": msg("In use"), "ok": True}]
    bridge._requirements_model = [{"description": msg("In use"), "met": True}]
    bridge._alongside_candidates = [{"reason": msg("In use")}]
    bridge._disks_model = [{"name": "sda", "partitions": [{"resizeBlockedReason": msg("In use")}]}]
    bridge._current_job_message = msg("Installing {count} packages...", count=3)
    bridge._error_message = msg("In use")
    assert bridge.planErrors == ["Utilisée"]
    assert bridge.planWarnings == ["Utilisée"]
    assert bridge.planChecklist == [{"text": "Utilisée", "ok": True}]
    assert bridge.requirementsModel == [{"description": "Utilisée", "met": True}]
    assert bridge.alongsideCandidates == [{"reason": "Utilisée"}]
    assert bridge.disksModel[0]["partitions"][0]["resizeBlockedReason"] == "Utilisée"
    assert bridge.currentJobMessage == "Installation de 3 paquets..."
    assert bridge.errorMessage == "Utilisée"


def test_retranslate_tells_qml_to_read_the_messages_again(bridge: EngineBridge) -> None:
    fired: list[str] = []
    for name in ("partitioningChanged", "requirementsChanged", "progressChanged", "disksChanged"):
        getattr(bridge, name).connect(lambda n=name: fired.append(n))
    bridge.retranslate()
    assert sorted(fired) == ["disksChanged", "partitioningChanged", "progressChanged", "requirementsChanged"]


def test_ui_languages_without_a_font_are_not_offered(bridge: EngineBridge) -> None:
    from PySide6.QtGui import QFontDatabase

    systems = [QFontDatabase.WritingSystem.Latin, QFontDatabase.WritingSystem.Cyrillic]
    with patch("xen.gui.bridge.QFontDatabase.writingSystems", return_value=systems):
        bridge.loadLocaleData()
        offered = {entry["code"] for entry in bridge.localesModelNative}
        assert {"fr_FR.UTF-8", "ru_RU.UTF-8", "eo.UTF-8"} <= offered
        assert not {"ja_JP.UTF-8", "zh_TW.UTF-8", "ar_SA.UTF-8", "hi_IN.UTF-8"} & offered
        assert bridge.isUiLanguageAvailable("ru_RU") is True
        assert bridge.isUiLanguageAvailable("ko_KR") is False

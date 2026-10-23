"""
Unit tests for EngineBridge's overall-progress weighting.

Regression: overallProgress used to split the bar evenly across job count
(each job = 1/N regardless of how long it actually takes), so the one job
that dominates wall-clock time (install's rsync copy) barely moved the bar
while every other near-instant job "jumped" by the same amount. Progress is
now weighted by each job's own estimate_duration().

Runs offscreen; no real network access is performed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from xen.core.engine import Engine  # noqa: E402
from xen.gui.bridge import EngineBridge  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MINIMAL_CONFIG = PROJECT_ROOT / "config" / "examples" / "minimal.yaml"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def bridge(qapp: QApplication) -> Iterator[EngineBridge]:
    assert qapp is not None
    engine = Engine.from_config_file(MINIMAL_CONFIG)
    theme_base = MINIMAL_CONFIG.parent
    obj = EngineBridge(engine, theme_base, debug=False, dry_run=True, skip_requirements=True)
    yield obj
    logging.getLogger("xen").removeHandler(obj._log_handler)
    logging.getLogger().removeHandler(obj._log_handler)


class TestJobWeights:
    def test_one_weight_per_job(self, bridge: EngineBridge) -> None:
        assert len(bridge._job_weights) == len(bridge._engine.jobs)

    def test_total_weight_is_sum_of_weights(self, bridge: EngineBridge) -> None:
        assert bridge._total_job_weight == sum(bridge._job_weights)

    def test_weights_are_never_zero(self, bridge: EngineBridge) -> None:
        # A zero-weight job would divide the bar by zero for everything
        # after it; estimate_duration() values are clamped to at least 1.
        assert all(w >= 1 for w in bridge._job_weights)


class TestWeightedOverallProgress:
    def test_evenly_weighted_jobs_split_evenly(self, bridge: EngineBridge) -> None:
        bridge._job_weights = [10, 10, 10, 10]
        bridge._total_job_weight = 40

        assert bridge._weighted_overall_progress(0, 0) == 0
        assert bridge._weighted_overall_progress(0, 100) == 25
        assert bridge._weighted_overall_progress(1, 0) == 25
        assert bridge._weighted_overall_progress(2, 50) == 62
        assert bridge._weighted_overall_progress(3, 100) == 100

    def test_a_long_job_dominates_the_bar(self, bridge: EngineBridge) -> None:
        # 4 near-instant jobs (weight 1 each) + one long "install" (weight
        # 96): halfway through install should read ~50%, not the ~60% the
        # old equal-split formula would have given (4/5 jobs done * 20% +
        # half of the 5th's 20% share).
        bridge._job_weights = [1, 1, 1, 1, 96]
        bridge._total_job_weight = 100

        assert bridge._weighted_overall_progress(4, 0) == 4
        assert bridge._weighted_overall_progress(4, 50) == 52
        assert bridge._weighted_overall_progress(4, 100) == 100

    def test_out_of_range_index_returns_current_value_unchanged(
        self, bridge: EngineBridge
    ) -> None:
        bridge._overall_progress = 42

        assert bridge._weighted_overall_progress(-1, 50) == 42
        assert bridge._weighted_overall_progress(999, 50) == 42


class TestProgressCallbacksUseWeighting:
    def test_on_job_start_sets_weighted_overall_progress(self, bridge: EngineBridge) -> None:
        bridge._job_weights = [1, 1, 1, 1, 96]
        bridge._total_job_weight = 100

        bridge._on_job_start("install")

        assert bridge.overallProgress == bridge._weighted_overall_progress(
            bridge.getCurrentJobIndex(), 0
        )

    def test_on_job_progress_sets_weighted_overall_progress(self, bridge: EngineBridge) -> None:
        bridge._job_weights = [1, 1, 1, 1, 96]
        bridge._total_job_weight = 100

        bridge._on_job_progress("install", 75, "Copying files...")

        idx = bridge.getCurrentJobIndex()
        assert bridge.overallProgress == bridge._weighted_overall_progress(idx, 75)
        assert bridge.currentJobMessage == "Copying files..."

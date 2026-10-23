"""Unit tests for Xen Engine."""

from pathlib import Path
from typing import Any

import pytest

try:
    from xen.core.engine import ConfigurationError, Engine, XenConfig

    HAS_XEN = True
except ImportError:
    HAS_XEN = False

# Skip entire module if xen is not available
pytestmark = pytest.mark.skipif(not HAS_XEN, reason="xen package not available")


class TestXenConfig:
    """Tests for configuration loading."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = XenConfig()
        assert config.version == "1.0"
        assert config.branding.name == "Linux"
        assert config.jobs == []

    def test_normalize_jobs_strings(self) -> None:
        """Test job normalization from strings."""
        config = XenConfig(jobs=["welcome", "locale", "install"])
        normalized = config.normalize_jobs()

        assert len(normalized) == 3
        assert normalized[0].name == "welcome"
        assert normalized[1].name == "locale"
        assert normalized[2].name == "install"


class TestEngine:
    """Tests for Engine class."""

    def test_config_file_not_found(self) -> None:
        """Test error when config file doesn't exist."""
        with pytest.raises(ConfigurationError, match="not found"):
            Engine.from_config_file("/nonexistent/path.yaml")

    def test_get_job_names(self, tmp_path: Path) -> None:
        """Test retrieving job names."""
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
version: "1.0"
branding:
  name: "Test OS"
jobs:
  - welcome
  - install
""")

        engine = Engine.from_config_file(config_file)
        names = engine.get_job_names()

        assert names == ["welcome", "install"]

    def test_branding_loaded(self, tmp_path: Path) -> None:
        """Test branding configuration loading."""
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
version: "1.0"
branding:
  name: "Custom OS"
  version: "1.0.0"
  colors:
    primary: "#FF0000"
jobs: []
""")

        engine = Engine.from_config_file(config_file)
        branding = engine.get_branding()

        assert branding.name == "Custom OS"
        assert branding.version == "1.0.0"
        assert branding.colors.primary == "#FF0000"


class TestThemeOverlay:
    """Tests for the <theme_dir>/theme.yaml overlay onto inline branding."""

    @staticmethod
    def _write_theme(tmp_path: Path, theme_yaml: str) -> None:
        theme_dir = tmp_path / "themes" / "testtheme"
        theme_dir.mkdir(parents=True)
        (theme_dir / "theme.yaml").write_text(theme_yaml)

    def test_theme_colors_override_inline_branding(self, tmp_path: Path) -> None:
        self._write_theme(
            tmp_path,
            """
colors:
  primary: "#222222"
  secondary: "#333333"
""",
        )
        config_file = tmp_path / "xen.yaml"
        config_file.write_text("""
version: "1.0"
theme: "themes/testtheme"
branding:
  name: "Inline OS"
  colors:
    primary: "#111111"
    text: "#ABCDEF"
jobs: []
""")
        branding = Engine.from_config_file(config_file).get_branding()
        assert branding.colors.primary == "#222222"  # theme wins
        assert branding.colors.secondary == "#333333"  # theme provides
        assert branding.colors.text == "#ABCDEF"  # inline kept (theme silent)
        assert branding.name == "Inline OS"  # no theme metadata

    def test_theme_metadata_maps_to_identity(self, tmp_path: Path) -> None:
        self._write_theme(
            tmp_path,
            """
metadata:
  name: "Theme OS"
  version: "2025.1"
  codename: "Xenlash"
colors:
  primary: "#222222"
""",
        )
        config_file = tmp_path / "xen.yaml"
        config_file.write_text("""
version: "1.0"
theme: "themes/testtheme"
branding:
  name: "Inline OS"
jobs: []
""")
        branding = Engine.from_config_file(config_file).get_branding()
        assert branding.name == "Theme OS"
        assert branding.version == "2025.1"
        assert branding.edition == "Xenlash"

    def test_theme_extra_color_keys_are_ignored(self, tmp_path: Path) -> None:
        self._write_theme(
            tmp_path,
            """
colors:
  primary: "#222222"
  success: "#010203"
""",
        )
        config_file = tmp_path / "xen.yaml"
        config_file.write_text("""
version: "1.0"
theme: "themes/testtheme"
branding:
  name: "Inline OS"
jobs: []
""")
        # Must not raise despite `success` not being a BrandingColors field.
        branding = Engine.from_config_file(config_file).get_branding()
        assert branding.colors.primary == "#222222"

    def test_missing_theme_yaml_uses_inline_branding(self, tmp_path: Path) -> None:
        config_file = tmp_path / "xen.yaml"
        config_file.write_text("""
version: "1.0"
theme: "themes/absent"
branding:
  name: "Inline OS"
  colors:
    primary: "#111111"
jobs: []
""")
        branding = Engine.from_config_file(config_file).get_branding()
        assert branding.colors.primary == "#111111"
        assert branding.name == "Inline OS"

    def test_status_colors_from_theme(self, tmp_path: Path) -> None:
        # success/warning/error are first-class branding colors, sourced from
        # theme.yaml.
        self._write_theme(
            tmp_path,
            """
colors:
  success: "#010203"
  warning: "#040506"
  error: "#070809"
""",
        )
        config_file = tmp_path / "xen.yaml"
        config_file.write_text("""
version: "1.0"
theme: "themes/testtheme"
branding:
  name: "Inline OS"
jobs: []
""")
        colors = Engine.from_config_file(config_file).get_branding().colors
        assert colors.success == "#010203"
        assert colors.warning == "#040506"
        assert colors.error == "#070809"


class TestLayoutPropagation:
    """A job's selection writes reach the jobs after it."""

    def test_partition_layout_reaches_later_jobs(self, tmp_path: Path) -> None:
        from xen.jobs.base import BaseJob, JobContext, JobResult

        class _PartJob(BaseJob):
            name = "part"
            description = "part"

            def run(self, context: JobContext) -> JobResult:
                context.selections.update(root_partition="/dev/sda2", efi_partition="/dev/sda1")
                return JobResult.ok("ok", data={"layout": {"root": "/dev/stale"}})

            def estimate_duration(self) -> int:
                return 1

        seen: dict[str, str] = {}

        class _NixJob(BaseJob):
            name = "nix"
            description = "nix"

            def run(self, context: JobContext) -> JobResult:
                seen["root"] = str(context.selections.get("root_partition", ""))
                seen["efi"] = str(context.selections.get("efi_partition", ""))
                return JobResult.ok("ok")

            def estimate_duration(self) -> int:
                return 1

        config_file = tmp_path / "c.yaml"
        config_file.write_text('version: "1.0"\nbranding:\n  name: "T"\njobs: []\n')
        engine = Engine.from_config_file(config_file)
        engine.jobs = [_PartJob(), _NixJob()]

        assert engine.run_all() is True
        assert seen == {"root": "/dev/sda2", "efi": "/dev/sda1"}


class TestOnErrorCallback:
    """Regression tests for the P0 fix: on_error must fire for every failure mode.

    Before the fix, ``_run_single_job`` only invoked ``on_error`` when
    ``job.validate()`` failed or when ``job.run()`` raised an exception. A job
    that *returned* ``JobResult.fail(...)`` from ``run()`` (the normal,
    non-exceptional failure path) never triggered ``on_error``, so the GUI
    surfaced a generic "unknown error" instead of the job's real message.
    """

    @staticmethod
    def _make_engine(tmp_path: Path, job: Any) -> Engine:
        from xen.jobs.base import BaseJob

        assert isinstance(job, BaseJob)
        config_file = tmp_path / "c.yaml"
        config_file.write_text('version: "1.0"\nbranding:\n  name: "T"\njobs: []\n')
        engine = Engine.from_config_file(config_file)
        engine.jobs = [job]
        return engine

    def test_returned_failure_triggers_on_error(self, tmp_path: Path) -> None:
        """A job that returns JobResult.fail(...) must call on_error with its message."""
        from xen.jobs.base import BaseJob, JobContext, JobResult

        class _FailingJob(BaseJob):
            name = "failing_job"
            description = "fails via return value"

            def run(self, _context: JobContext) -> JobResult:
                return JobResult.fail("boom")

            def estimate_duration(self) -> int:
                return 1

        engine = self._make_engine(tmp_path, _FailingJob())

        errors: list[tuple[str, str]] = []
        engine.on_error = lambda name, message: errors.append((name, message))

        assert engine.run_all() is False
        assert errors == [("failing_job", "boom")]
        assert engine.state.last_error == "boom"

    def test_exception_still_triggers_on_error(self, tmp_path: Path) -> None:
        """Non-regression: a job that raises must still call on_error (pre-existing path)."""
        from xen.jobs.base import BaseJob, JobContext, JobResult

        class _RaisingJob(BaseJob):
            name = "raising_job"
            description = "raises an exception"

            def run(self, _context: JobContext) -> JobResult:
                raise RuntimeError("kaboom")

            def estimate_duration(self) -> int:
                return 1

        engine = self._make_engine(tmp_path, _RaisingJob())

        errors: list[tuple[str, str]] = []
        engine.on_error = lambda name, message: errors.append((name, message))

        assert engine.run_all() is False
        assert errors == [("raising_job", "kaboom")]
        assert engine.state.last_error == "kaboom"

    def test_successful_job_does_not_trigger_on_error(self, tmp_path: Path) -> None:
        """A successful job must never call on_error."""
        from xen.jobs.base import BaseJob, JobContext, JobResult

        class _OkJob(BaseJob):
            name = "ok_job"
            description = "succeeds"

            def run(self, _context: JobContext) -> JobResult:
                return JobResult.ok("all good")

            def estimate_duration(self) -> int:
                return 1

        engine = self._make_engine(tmp_path, _OkJob())

        errors: list[tuple[str, str]] = []
        engine.on_error = lambda name, message: errors.append((name, message))

        assert engine.run_all() is True
        assert errors == []
        assert engine.state.last_error is None


class TestBootloaderSelectionSeeding:
    """Regression: partition.py's AUTO-mode LUKS KDF choice reads
    context.selections["bootloader"], a key nothing in the app ever set, so
    on a `type: grub` profile it fell back to "systemd-boot", kept Argon2id,
    and produced a volume GRUB cannot unlock."""

    XEROLINUX_CONFIG = Path(__file__).parents[2] / "config" / "examples" / "xerolinux.yaml"

    def test_xerolinux_profile_seeds_grub_for_earlier_jobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from xen.jobs.base import BaseJob, JobContext, JobResult

        seen: dict[str, Any] = {}

        class _ProbeJob(BaseJob):
            name = "probe"
            description = "probe"

            def run(self, context: JobContext) -> JobResult:
                seen.update(context.selections)
                return JobResult.ok("ok")

            def estimate_duration(self) -> int:
                return 1

        engine = Engine.from_config_file(self.XEROLINUX_CONFIG)
        bootloader_job = next(j for j in engine.jobs if j.name == "bootloader")
        monkeypatch.setattr(bootloader_job, "run", lambda _context: JobResult.ok("ok"))
        engine.jobs = [_ProbeJob(), bootloader_job]
        engine.set_selections({"dry_run": True})

        assert engine.run_all() is True
        assert seen.get("bootloader") == "grub"

    def test_job_config_wins_over_selections(self) -> None:
        engine = Engine.from_config_file(self.XEROLINUX_CONFIG)
        bootloader_job = next(j for j in engine.jobs if j.name == "bootloader")
        engine.set_selections({"bootloader": "systemd-boot"})

        assert engine.bootloader_type() == "grub"
        assert bootloader_job.config["type"] == "grub"

    def test_falls_back_to_selections_then_grub(self) -> None:
        engine = Engine.from_config_file(self.XEROLINUX_CONFIG)
        bootloader_job = next(j for j in engine.jobs if j.name == "bootloader")
        bootloader_job.config.pop("type")

        assert engine.bootloader_type() == "grub"
        engine.set_selections({"bootloader": "systemd-boot"})
        assert engine.bootloader_type() == "systemd-boot"

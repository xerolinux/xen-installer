"""Unit tests for Xen Jobs."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from xen.jobs.base import BaseJob, JobContext, JobResult, JobStatus

    HAS_XEN = True
except ImportError:
    HAS_XEN = False

# Skip entire module if xen is not available
pytestmark = pytest.mark.skipif(not HAS_XEN, reason="xen package not available")


class TestJobResult:
    """Tests for JobResult class."""

    def test_ok_result(self) -> None:
        """Test successful result creation."""
        result = JobResult.ok("Success message")

        assert result.success is True
        assert result.message == "Success message"
        assert result.error_code == 0

    def test_fail_result(self) -> None:
        """Test failed result creation."""
        result = JobResult.fail("Error message", error_code=42)

        assert result.success is False
        assert result.message == "Error message"
        assert result.error_code == 42

    def test_result_with_data(self) -> None:
        """Test result with additional data."""
        result = JobResult.ok(data={"key": "value"})

        assert result.data == {"key": "value"}


class TestJobContext:
    """Tests for JobContext class."""

    def test_default_context(self) -> None:
        """Test default context values."""
        context = JobContext()

        assert context.target_root == "/mnt/target"
        assert context.selections == {}
        assert context.config == {}

    def test_progress_callback(self) -> None:
        """Test progress reporting."""
        progress_calls: list[tuple[int, str]] = []

        def on_progress(percent: int, message: str) -> None:
            progress_calls.append((percent, message))

        context = JobContext(on_progress=on_progress)
        context.report_progress(50, "Halfway there")

        assert progress_calls == [(50, "Halfway there")]

    def test_progress_clamped(self) -> None:
        """Test progress values are clamped to 0-100."""
        progress_calls: list[tuple[int, str]] = []

        def on_progress(percent: int, message: str) -> None:
            progress_calls.append((percent, message))

        context = JobContext(on_progress=on_progress)
        context.report_progress(-10, "")
        context.report_progress(150, "")

        assert progress_calls[0][0] == 0
        assert progress_calls[1][0] == 100


class ConcreteJob(BaseJob):
    """Concrete implementation for testing."""

    name = "test_job"
    description = "A test job"

    def run(self, context: JobContext) -> JobResult:
        context.report_progress(100, "Done")
        return JobResult.ok()

    def estimate_duration(self) -> int:
        return 5


class TestBaseJob:
    """Tests for BaseJob abstract class."""

    def test_job_initialization(self) -> None:
        """Test job initialization."""
        job = ConcreteJob(config={"key": "value"})

        assert job.name == "test_job"
        assert job.status == JobStatus.PENDING
        assert job._config == {"key": "value"}

    def test_job_run(self) -> None:
        """Test job execution."""
        job = ConcreteJob()
        context = JobContext()

        result = job.run(context)

        assert result.success is True

    def test_job_validation_default(self) -> None:
        """Test default validation passes."""
        job = ConcreteJob()
        context = JobContext()

        result = job.validate(context)

        assert result.success is True

    def test_job_repr(self) -> None:
        """Test job string representation."""
        job = ConcreteJob()

        assert "test_job" in repr(job)
        assert "PENDING" in repr(job)


# =============================================================================
# Import additional modules for welcome job tests
# =============================================================================

try:
    from xen.jobs import (
        RequirementCheck,
        RequirementsResult,
        RequirementStatus,
        SystemRequirementsChecker,
        WelcomeConfig,
        WelcomeJob,
        WelcomeState,
    )

    HAS_WELCOME_JOB = True
except ImportError:
    HAS_WELCOME_JOB = False


# =============================================================================
# RequirementCheck Tests
# =============================================================================


@pytest.mark.skipif(not HAS_WELCOME_JOB, reason="WelcomeJob not available")
class TestRequirementCheck:
    """Tests for RequirementCheck dataclass."""

    def test_passed_with_pass_status(self) -> None:
        """PASS status should be considered passed."""
        check = RequirementCheck(
            name="test",
            description="Test",
            status=RequirementStatus.PASS,
        )
        assert check.passed is True
        assert check.is_critical is False

    def test_passed_with_warn_status(self) -> None:
        """WARN status should be considered passed (can continue)."""
        check = RequirementCheck(
            name="test",
            description="Test",
            status=RequirementStatus.WARN,
        )
        assert check.passed is True
        assert check.is_critical is False

    def test_passed_with_skip_status(self) -> None:
        """SKIP status should be considered passed."""
        check = RequirementCheck(
            name="test",
            description="Test",
            status=RequirementStatus.SKIP,
        )
        assert check.passed is True
        assert check.is_critical is False

    def test_failed_with_fail_status(self) -> None:
        """FAIL status should not be passed and is critical."""
        check = RequirementCheck(
            name="test",
            description="Test",
            status=RequirementStatus.FAIL,
        )
        assert check.passed is False
        assert check.is_critical is True

    def test_check_with_all_values(self) -> None:
        """RequirementCheck should store all values."""
        check = RequirementCheck(
            name="ram",
            description="Memory",
            status=RequirementStatus.PASS,
            current_value="8 GB",
            required_value="4 GB",
            recommended_value="8 GB",
            details="Sufficient RAM",
        )
        assert check.name == "ram"
        assert check.description == "Memory"
        assert check.current_value == "8 GB"
        assert check.required_value == "4 GB"
        assert check.recommended_value == "8 GB"
        assert check.details == "Sufficient RAM"


# =============================================================================
# RequirementsResult Tests
# =============================================================================


@pytest.mark.skipif(not HAS_WELCOME_JOB, reason="WelcomeJob not available")
class TestRequirementsResult:
    """Tests for RequirementsResult aggregation."""

    def test_all_passed_with_only_pass(self) -> None:
        """All PASS checks should result in all_passed=True."""
        result = RequirementsResult(
            checks=[
                RequirementCheck("a", "A", RequirementStatus.PASS),
                RequirementCheck("b", "B", RequirementStatus.PASS),
            ]
        )
        assert result.all_passed is True
        assert result.can_continue is True

    def test_all_passed_with_warnings(self) -> None:
        """WARN checks should not prevent all_passed."""
        result = RequirementsResult(
            checks=[
                RequirementCheck("a", "A", RequirementStatus.PASS),
                RequirementCheck("b", "B", RequirementStatus.WARN),
            ]
        )
        assert result.all_passed is True
        assert result.can_continue is True

    def test_can_continue_with_failures(self) -> None:
        """FAIL checks should prevent can_continue."""
        result = RequirementsResult(
            checks=[
                RequirementCheck("a", "A", RequirementStatus.PASS),
                RequirementCheck("b", "B", RequirementStatus.FAIL),
            ]
        )
        assert result.all_passed is False
        assert result.can_continue is False

    def test_failures_list(self) -> None:
        """failures property should return only FAIL checks."""
        result = RequirementsResult(
            checks=[
                RequirementCheck("a", "A", RequirementStatus.PASS),
                RequirementCheck("b", "B", RequirementStatus.FAIL),
                RequirementCheck("c", "C", RequirementStatus.WARN),
            ]
        )
        assert len(result.failures) == 1
        assert result.failures[0].name == "b"

    def test_warnings_list(self) -> None:
        """warnings property should return only WARN checks."""
        result = RequirementsResult(
            checks=[
                RequirementCheck("a", "A", RequirementStatus.PASS),
                RequirementCheck("b", "B", RequirementStatus.FAIL),
                RequirementCheck("c", "C", RequirementStatus.WARN),
            ]
        )
        assert len(result.warnings) == 1
        assert result.warnings[0].name == "c"

    def test_passed_checks_list(self) -> None:
        """passed_checks property should return only PASS checks."""
        result = RequirementsResult(
            checks=[
                RequirementCheck("a", "A", RequirementStatus.PASS),
                RequirementCheck("b", "B", RequirementStatus.FAIL),
                RequirementCheck("c", "C", RequirementStatus.WARN),
            ]
        )
        assert len(result.passed_checks) == 1
        assert result.passed_checks[0].name == "a"


# =============================================================================
# SystemRequirementsChecker Tests
# =============================================================================


@pytest.mark.skipif(not HAS_WELCOME_JOB, reason="WelcomeJob not available")
class TestSystemRequirementsChecker:
    """Tests for SystemRequirementsChecker."""

    def test_init_with_empty_config(self) -> None:
        """Checker should work with empty config."""
        checker = SystemRequirementsChecker()
        assert checker.config == {}

    def test_init_with_config(self) -> None:
        """Checker should store provided config."""
        config = {"min_ram_gb": 8, "min_disk_gb": 100}
        checker = SystemRequirementsChecker(config)
        assert checker.config == config

    def test_check_cpu_architecture(self) -> None:
        """CPU architecture check should detect x86_64."""
        import os

        actual_arch = os.uname().machine

        checker = SystemRequirementsChecker({"require_x86_64": True})
        result = checker._check_cpu_architecture()

        assert result.current_value == actual_arch
        if actual_arch == "x86_64":
            assert result.status == RequirementStatus.PASS
        else:
            assert result.status == RequirementStatus.FAIL

    def test_check_all_returns_result(self) -> None:
        """check_all should return RequirementsResult with enabled checks."""
        # New config format requires explicit enabled: true for each check
        config = {
            "ram": {"enabled": True, "min_gb": 4, "warn_gb": 8, "recommended_gb": 8},
            "disk": {"enabled": True, "min_gb": 20, "recommended_gb": 60},
            "cpu_arch": {"enabled": True, "require_x86_64": True},
            "efi": {"enabled": True, "required": False},
        }
        checker = SystemRequirementsChecker(config)
        result = checker.check_all()

        assert isinstance(result, RequirementsResult)
        assert len(result.checks) > 0

        # Should have the enabled checks
        check_names = [c.name for c in result.checks]
        assert "ram" in check_names
        assert "disk" in check_names
        assert "cpu_arch" in check_names
        assert "efi" in check_names

    @patch("xen.jobs.requirements.GPUDetector")
    def test_gpu_incompatible_is_warn_by_default(self, mock_det: MagicMock) -> None:
        """No compatible GPU is advisory (WARN) and does not block by default."""
        mock_det.return_value.gpus = []
        mock_det.return_value.check_compatibility.return_value = (
            "fail",
            "No compatible GPU detected",
            [],
        )
        checker = SystemRequirementsChecker({"gpu": {"enabled": True}})
        check = checker._check_gpu()

        assert check.status == RequirementStatus.WARN
        assert RequirementsResult(checks=[check]).can_continue is True

    @patch("xen.jobs.requirements.GPUDetector")
    def test_gpu_incompatible_blocks_when_required(self, mock_det: MagicMock) -> None:
        """With gpu.required=true, no compatible GPU is a blocking FAIL."""
        mock_det.return_value.gpus = []
        mock_det.return_value.check_compatibility.return_value = (
            "fail",
            "No compatible GPU detected",
            [],
        )
        checker = SystemRequirementsChecker({"gpu": {"enabled": True, "required": True}})
        check = checker._check_gpu()

        assert check.status == RequirementStatus.FAIL
        assert RequirementsResult(checks=[check]).can_continue is False


# =============================================================================
# WelcomeConfig Tests
# =============================================================================


@pytest.mark.skipif(not HAS_WELCOME_JOB, reason="WelcomeJob not available")
class TestWelcomeConfig:
    """Tests for WelcomeConfig dataclass."""

    def test_from_dict_empty(self) -> None:
        """WelcomeConfig should handle empty config."""
        config = WelcomeConfig.from_dict({})
        assert config.show_release_notes is True
        assert config.wallpaper_dark == ""
        assert config.requirements == {}

    def test_from_dict_full(self) -> None:
        """WelcomeConfig should parse full config."""
        config = WelcomeConfig.from_dict(
            {
                "show_release_notes": False,
                "wallpapers": {
                    "dark": "wallpapers/welcome-dark.jpg",
                    "light": "wallpapers/welcome-light.jpg",
                    "fallback": "wallpapers/dark.jpg",
                },
                "requirements": {
                    "min_ram_gb": 4,
                    "min_disk_gb": 40,
                },
            }
        )
        assert config.show_release_notes is False
        assert config.wallpaper_dark == "wallpapers/welcome-dark.jpg"
        assert config.wallpaper_light == "wallpapers/welcome-light.jpg"
        assert config.wallpaper_fallback == "wallpapers/dark.jpg"
        assert config.requirements["min_ram_gb"] == 4


# =============================================================================
# WelcomeJob Tests
# =============================================================================


@pytest.mark.skipif(not HAS_WELCOME_JOB, reason="WelcomeJob not available")
class TestWelcomeJob:
    """Tests for WelcomeJob implementation."""

    def test_init_defaults(self) -> None:
        """WelcomeJob should have correct defaults."""
        job = WelcomeJob()
        assert job.name == "welcome"
        assert job.description == "Welcome screen and system requirements check"
        assert job.status == JobStatus.PENDING

    def test_init_with_config(self) -> None:
        """WelcomeJob should parse provided config."""
        job = WelcomeJob(
            {
                "show_release_notes": True,
                "wallpapers": {"dark": "test.jpg"},
                "requirements": {"min_ram_gb": 8},
            }
        )
        assert job.welcome_config.show_release_notes is True
        assert job.welcome_config.wallpaper_dark == "test.jpg"
        assert job.welcome_config.requirements["min_ram_gb"] == 8

    def test_initialize_resolves_wallpapers(self) -> None:
        """initialize should resolve wallpaper paths to URLs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_base = Path(tmpdir)
            wallpaper_dir = theme_base / "wallpapers"
            wallpaper_dir.mkdir()

            # Create test wallpaper
            dark_wallpaper = wallpaper_dir / "welcome-dark.jpg"
            dark_wallpaper.touch()

            job = WelcomeJob({"wallpapers": {"dark": "wallpapers/welcome-dark.jpg"}})
            job.initialize(theme_base)

            assert job.state.wallpaper_dark_url == f"file://{dark_wallpaper}"

    def test_set_dark_mode(self) -> None:
        """set_dark_mode should update current wallpaper."""
        job = WelcomeJob()
        job._state.wallpaper_dark_url = "file:///dark.jpg"
        job._state.wallpaper_light_url = "file:///light.jpg"

        job.set_dark_mode(True)
        assert job.state.current_wallpaper_url == "file:///dark.jpg"

        job.set_dark_mode(False)
        assert job.state.current_wallpaper_url == "file:///light.jpg"

    def test_check_requirements_updates_state(self) -> None:
        """check_requirements should update job state."""
        job = WelcomeJob({"requirements": {"min_ram_gb": 1}})
        result = job.check_requirements()

        assert job.state.requirements_result is not None
        assert isinstance(result, RequirementsResult)
        # State should reflect results
        assert job.state.can_proceed == result.can_continue

    def test_get_requirements_summary(self) -> None:
        """get_requirements_summary should return structured data."""
        job = WelcomeJob()
        summary = job.get_requirements_summary()

        assert "passed" in summary
        assert "warnings" in summary
        assert "failures" in summary
        assert "can_proceed" in summary
        assert "total_checks" in summary

    def test_validate_returns_result(self) -> None:
        """validate should return JobResult."""
        job = WelcomeJob({"requirements": {"min_ram_gb": 1, "min_disk_gb": 1}})
        context = JobContext()
        context.on_progress = MagicMock()

        result = job.validate(context)

        assert isinstance(result, JobResult)

    def test_run_returns_result(self) -> None:
        """run should return JobResult with summary data."""
        job = WelcomeJob({"requirements": {"min_ram_gb": 1, "min_disk_gb": 1}})
        context = JobContext()
        context.on_progress = MagicMock()

        result = job.run(context)

        assert isinstance(result, JobResult)
        if result.success:
            assert "requirements_summary" in result.data
            assert "wallpaper_url" in result.data
            assert "can_proceed" in result.data

    def test_estimate_duration(self) -> None:
        """estimate_duration should return reasonable value."""
        job = WelcomeJob()
        duration = job.estimate_duration()
        assert duration == 5


# =============================================================================
# WelcomeState Tests
# =============================================================================


@pytest.mark.skipif(not HAS_WELCOME_JOB, reason="WelcomeJob not available")
class TestWelcomeState:
    """Tests for WelcomeState dataclass."""

    def test_default_values(self) -> None:
        """WelcomeState should have sensible defaults."""
        state = WelcomeState()
        assert state.wallpaper_dark_url == ""
        assert state.wallpaper_light_url == ""
        assert state.current_wallpaper_url == ""
        assert state.requirements_result is None
        assert state.all_requirements_met is False
        assert state.can_proceed is False
        assert state.is_dark_mode is True
        assert state.show_details is False


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.skipif(not HAS_WELCOME_JOB, reason="WelcomeJob not available")
class TestWelcomeJobIntegration:
    """Integration tests for WelcomeJob with full workflow."""

    def test_full_workflow(self) -> None:
        """Test complete welcome job workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_base = Path(tmpdir)
            wallpaper_dir = theme_base / "wallpapers"
            wallpaper_dir.mkdir()

            # Create wallpapers
            (wallpaper_dir / "welcome-dark.jpg").touch()
            (wallpaper_dir / "welcome-light.jpg").touch()

            # Create job with full config
            job = WelcomeJob(
                {
                    "show_release_notes": True,
                    "wallpapers": {
                        "dark": "wallpapers/welcome-dark.jpg",
                        "light": "wallpapers/welcome-light.jpg",
                    },
                    "requirements": {
                        "ram": {"enabled": True, "min_gb": 1, "warn_gb": 4, "recommended_gb": 8},
                        "disk": {"enabled": True, "min_gb": 1, "recommended_gb": 20},
                        "efi": {"enabled": True, "required": False},
                    },
                }
            )

            # Initialize with theme
            job.initialize(theme_base)

            # Check wallpapers resolved
            assert "welcome-dark.jpg" in job.state.wallpaper_dark_url
            assert "welcome-light.jpg" in job.state.wallpaper_light_url

            # Run requirements check
            result = job.check_requirements()
            assert isinstance(result, RequirementsResult)

            # Get summary for UI
            summary = job.get_requirements_summary()
            assert summary["total_checks"] > 0

            # Run the job
            context = JobContext()
            context.on_progress = MagicMock()
            run_result = job.run(context)

            assert isinstance(run_result, JobResult)

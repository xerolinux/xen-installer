"""Unit tests for dynamic job loading system."""

import tempfile
from pathlib import Path
from typing import Any

import pytest

try:
    from xen.core.engine import Engine, JobLoadError, XenConfig

    HAS_XEN = True
except ImportError:
    HAS_XEN = False

pytestmark = pytest.mark.skipif(not HAS_XEN, reason="xen package not available")


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def minimal_config() -> dict[str, Any]:
    """Minimal valid configuration for testing."""
    return {
        "version": "1.0",
        "branding": {"name": "TestOS"},
        "jobs": [],
    }


@pytest.fixture
def temp_jobs_module(tmp_path: Path) -> Path:
    """Create a temporary jobs module directory for testing."""
    jobs_dir = tmp_path / "xen" / "jobs"
    jobs_dir.mkdir(parents=True)

    # Create __init__.py
    init_file = jobs_dir / "__init__.py"
    init_file.write_text('"""Test jobs package."""\n')

    return jobs_dir


# =============================================================================
# Job Loader Tests
# =============================================================================


class TestDynamicJobLoader:
    """Tests for dynamic job loading from xen.jobs.<name>."""

    def test_load_valid_job(self, minimal_config: dict[str, Any]) -> None:
        """Should successfully load the welcome job."""
        minimal_config["jobs"] = ["welcome"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(minimal_config, f)
            config_path = f.name

        try:
            engine = Engine.from_config_file(config_path)

            assert len(engine.jobs) == 1
            assert engine.jobs[0].name == "welcome"
            # WelcomeJob should be loaded
            assert engine.jobs[0].__class__.__name__ == "WelcomeJob"
        finally:
            Path(config_path).unlink()

    def test_load_nonexistent_job(self, minimal_config: dict[str, Any]) -> None:
        """Should raise JobLoadError for nonexistent job module."""
        minimal_config["jobs"] = ["nonexistent_job_module_that_does_not_exist"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(minimal_config, f)
            config_path = f.name

        try:
            with pytest.raises(JobLoadError) as exc_info:
                Engine.from_config_file(config_path)

            assert "Failed to import job module" in str(exc_info.value)
            assert "nonexistent_job_module_that_does_not_exist" in str(exc_info.value)
        finally:
            Path(config_path).unlink()

    def test_load_job_without_job_class(self) -> None:
        """Should raise JobLoadError when module has no valid Job class."""
        # We can't easily test this without modifying sys.path
        # Instead, test with a module that exists but has wrong structure
        # This test is skipped for now - use integration test instead
        pytest.skip("Requires dynamic module injection - covered by integration tests")

    def test_load_job_not_inheriting_basejob(self) -> None:
        """Should raise JobLoadError when Job class doesn't inherit BaseJob."""
        # We can't easily test this without modifying sys.path
        # This test is skipped for now - use integration test instead
        pytest.skip("Requires dynamic module injection - covered by integration tests")

    def test_load_job_with_config(self, minimal_config: dict[str, Any]) -> None:
        """Should pass job config to job constructor."""
        minimal_config["jobs"] = [
            {
                "name": "welcome",
                "config": {
                    "show_release_notes": False,
                    "requirements": {"min_ram_gb": 16},
                },
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(minimal_config, f)
            config_path = f.name

        try:
            engine = Engine.from_config_file(config_path)

            assert len(engine.jobs) == 1
            job = engine.jobs[0]

            # WelcomeJob should have received the config
            assert job._config["show_release_notes"] is False
            assert job._config["requirements"]["min_ram_gb"] == 16
        finally:
            Path(config_path).unlink()

    def test_load_multiple_jobs(self, minimal_config: dict[str, Any]) -> None:
        """Should load multiple jobs in sequence."""
        # Use welcome job multiple times with different configs
        minimal_config["jobs"] = [
            {"name": "welcome", "config": {"id": 1}},
            {"name": "welcome", "config": {"id": 2}},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(minimal_config, f)
            config_path = f.name

        try:
            engine = Engine.from_config_file(config_path)

            assert len(engine.jobs) == 2
            assert engine.jobs[0]._config["id"] == 1
            assert engine.jobs[1]._config["id"] == 2
        finally:
            Path(config_path).unlink()

    def test_find_job_class_with_custom_name(self) -> None:
        """Should find job class with custom name ending in 'Job'."""
        # Test with the actual WelcomeJob which has a custom name
        from xen.core.engine import JobDefinition

        engine = Engine(config=XenConfig(jobs=[]))
        job = engine._load_single_job(JobDefinition(name="welcome"))

        # WelcomeJob (not Job) should be found
        assert job.__class__.__name__ == "WelcomeJob"
        assert job.name == "welcome"

    def test_job_instantiation_failure(self) -> None:
        """Should raise JobLoadError when job instantiation fails."""
        # We can't easily test instantiation failure without a real failing job
        # This would require creating a temporary module in the Python path
        pytest.skip("Requires dynamic module injection - covered by integration tests")


# =============================================================================
# Integration Tests
# =============================================================================


class TestJobLoaderIntegration:
    """Integration tests for job loading within Engine workflow."""

    def test_engine_loads_and_runs_welcome_job(self, minimal_config: dict[str, Any]) -> None:
        """Engine should load and execute welcome job successfully."""
        minimal_config["jobs"] = [
            {
                "name": "welcome",
                "config": {
                    "requirements": {
                        "ram": {"enabled": True, "min_gb": 1},
                    }
                },
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(minimal_config, f)
            config_path = f.name

        try:
            engine = Engine.from_config_file(config_path)

            # Job should be loaded
            assert len(engine.jobs) == 1
            assert engine.jobs[0].name == "welcome"

            # Job should be runnable
            from xen.jobs.base import JobContext

            context = JobContext()
            result = engine.run_all(context)

            # May fail if requirements not met, but should not crash
            assert isinstance(result, bool)
        finally:
            Path(config_path).unlink()

    def test_job_names_accessible(self, minimal_config: dict[str, Any]) -> None:
        """Engine should provide list of loaded job names."""
        minimal_config["jobs"] = ["welcome"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(minimal_config, f)
            config_path = f.name

        try:
            engine = Engine.from_config_file(config_path)
            job_names = engine.get_job_names()

            assert job_names == ["welcome"]
        finally:
            Path(config_path).unlink()


class TestPackageChooserGroupsFileResolution:
    """Tests for resolving packagechooser's groups_file against config dir."""

    def test_resolves_relative_groups_file_against_config_dir(self, tmp_path: Path) -> None:
        import yaml

        groups_file = tmp_path / "packages-optional.yaml"
        groups_file.write_text("[]\n")

        config = {
            "version": "1.0",
            "branding": {"name": "TestOS"},
            "jobs": [
                {
                    "name": "packagechooser",
                    "config": {"groups_file": "packages-optional.yaml"},
                }
            ],
        }
        config_path = tmp_path / "xen.yaml"
        config_path.write_text(yaml.safe_dump(config))

        engine = Engine.from_config_file(config_path)

        job = engine.jobs[0]
        assert job._config["groups_file"] == str(groups_file.resolve())

    def test_leaves_absolute_groups_file_untouched(self, tmp_path: Path) -> None:
        import yaml

        groups_file = tmp_path / "elsewhere.yaml"
        groups_file.write_text("[]\n")

        config = {
            "version": "1.0",
            "branding": {"name": "TestOS"},
            "jobs": [
                {
                    "name": "packagechooser",
                    "config": {"groups_file": str(groups_file)},
                }
            ],
        }
        config_path = tmp_path / "xen.yaml"
        config_path.write_text(yaml.safe_dump(config))

        engine = Engine.from_config_file(config_path)

        assert engine.jobs[0]._config["groups_file"] == str(groups_file)

    def test_ignores_jobs_without_packagechooser(self, tmp_path: Path) -> None:
        import yaml

        config = {
            "version": "1.0",
            "branding": {"name": "TestOS"},
            "jobs": ["welcome"],
        }
        config_path = tmp_path / "xen.yaml"
        config_path.write_text(yaml.safe_dump(config))

        # Should not raise even though there's no packagechooser job at all.
        engine = Engine.from_config_file(config_path)
        assert engine.jobs[0].name == "welcome"

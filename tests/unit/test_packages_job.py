"""Unit tests for PackagesJob."""

import subprocess
import tempfile
from unittest.mock import MagicMock, Mock, patch

import pytest

try:
    from xen.jobs.base import JobContext, JobResult, JobStatus
    from xen.jobs.packages import PackagesJob

    HAS_PACKAGES_JOB = True
except ImportError:
    HAS_PACKAGES_JOB = False

# Skip entire module if xen packages job is not available
pytestmark = pytest.mark.skipif(not HAS_PACKAGES_JOB, reason="PackagesJob not available")


# =============================================================================
# PackagesJob Initialization Tests
# =============================================================================


class TestPackagesJobInit:
    """Tests for PackagesJob initialization."""

    def test_init_defaults(self) -> None:
        """PackagesJob should have correct defaults."""
        job = PackagesJob()
        assert job.name == "packages"
        assert job.description == "System package installation"
        assert job.status == JobStatus.PENDING
        assert job._packages_installed == []
        assert job._packages_failed == []

    def test_init_with_config(self) -> None:
        """PackagesJob should accept configuration."""
        config = {"mode": "desktop"}
        job = PackagesJob(config)
        assert job._config == config

    def test_constants_defined(self) -> None:
        """PackagesJob should define necessary constants."""
        assert PackagesJob.MODE_ESSENTIAL == "essential"
        assert PackagesJob.MODE_DESKTOP == "desktop"
        assert PackagesJob.MODE_CUSTOM == "custom"
        assert len(PackagesJob.ESSENTIAL_PACKAGES) > 0
        assert len(PackagesJob.DESKTOP_PACKAGES) > 0


# =============================================================================
# Package List Building Tests
# =============================================================================


class TestGetPackageList:
    """Tests for package list building."""

    def test_get_package_list_essential(self) -> None:
        """Should return essential packages for essential mode."""
        job = PackagesJob()
        context = JobContext(selections={"mode": "essential"})

        packages = job._get_package_list(context)

        assert packages == PackagesJob.ESSENTIAL_PACKAGES
        assert "base" in packages
        assert "linux" in packages

    def test_get_package_list_desktop(self) -> None:
        """Should return combined packages for desktop mode."""
        job = PackagesJob()
        context = JobContext(selections={"mode": "desktop"})

        packages = job._get_package_list(context)

        # Should contain both essential and desktop packages
        assert all(pkg in packages for pkg in PackagesJob.ESSENTIAL_PACKAGES)
        assert all(pkg in packages for pkg in PackagesJob.DESKTOP_PACKAGES)
        assert len(packages) > len(PackagesJob.ESSENTIAL_PACKAGES)

    def test_get_package_list_custom(self) -> None:
        """Should return custom packages for custom mode."""
        job = PackagesJob()
        custom_pkgs = ["vim", "git", "htop"]
        context = JobContext(
            selections={
                "mode": "custom",
                "packages": custom_pkgs,
            }
        )

        packages = job._get_package_list(context)

        assert packages == custom_pkgs

    def test_get_package_list_custom_empty(self) -> None:
        """Should fallback to essential if custom mode has no packages."""
        job = PackagesJob()
        context = JobContext(
            selections={
                "mode": "custom",
                "packages": [],
            }
        )

        packages = job._get_package_list(context)

        assert packages == PackagesJob.ESSENTIAL_PACKAGES

    def test_get_package_list_default(self) -> None:
        """Should default to essential mode if not specified."""
        job = PackagesJob()
        context = JobContext()

        packages = job._get_package_list(context)

        assert packages == PackagesJob.ESSENTIAL_PACKAGES

    def test_get_package_list_mode_from_job_config(self) -> None:
        """Should use job config mode when selections mode is not set."""
        job = PackagesJob({"mode": "desktop"})
        context = JobContext()

        packages = job._get_package_list(context)

        # Should include both essential and desktop packages from config mode
        assert all(pkg in packages for pkg in PackagesJob.ESSENTIAL_PACKAGES)
        assert all(pkg in packages for pkg in PackagesJob.DESKTOP_PACKAGES)
        assert len(packages) > len(PackagesJob.ESSENTIAL_PACKAGES)


# =============================================================================
# Package Name Validation Tests
# =============================================================================


class TestValidatePackageNames:
    """Tests for package name validation."""

    def test_validate_valid_packages(self) -> None:
        """Valid package names should pass validation."""
        job = PackagesJob()

        result = job._validate_package_names(["vim", "git", "htop"])
        assert result.success is True

        result = job._validate_package_names(["python-pip", "gcc-libs"])
        assert result.success is True

        result = job._validate_package_names(["lib32-gcc-libs"])
        assert result.success is True

    def test_validate_empty_list(self) -> None:
        """Empty package list should fail validation."""
        job = PackagesJob()

        result = job._validate_package_names([])

        assert result.success is False
        assert result.error_code == 30

    def test_validate_invalid_characters(self) -> None:
        """Package names with invalid characters should fail."""
        job = PackagesJob()

        result = job._validate_package_names(["vim!", "git@", "htop#"])

        assert result.success is False
        assert result.error_code == 31
        assert "invalid_packages" in result.data

    def test_validate_empty_package_name(self) -> None:
        """Empty package names should fail validation."""
        job = PackagesJob()

        result = job._validate_package_names(["vim", "", "git"])

        assert result.success is False
        assert result.error_code == 31


# =============================================================================
# Repository Update Tests
# =============================================================================


class TestUpdateRepositories:
    """Tests for repository update operations."""

    @patch("subprocess.run")
    def test_update_repositories_pacman_success(self, mock_run: Mock) -> None:
        """Should successfully update pacman repositories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(stdout="", stderr="")

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)

            result = job._update_repositories(context)

            assert result.success is True
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "pacman" in call_args
            assert "-Syy" in call_args
            assert "--noconfirm" in call_args

    @patch("subprocess.run")
    def test_update_repositories_failure(self, mock_run: Mock) -> None:
        """Should handle repository update failures."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "pacman", stderr="Repository error"
            )

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)

            result = job._update_repositories(context)

            assert result.success is False
            assert result.error_code == 33

    @patch("subprocess.run")
    def test_update_repositories_timeout(self, mock_run: Mock) -> None:
        """Should handle repository update timeout."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.TimeoutExpired("pacman", 300)

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)

            result = job._update_repositories(context)

            assert result.success is False
            assert result.error_code == 34


# =============================================================================
# Package Installation Tests
# =============================================================================


class TestInstallPackages:
    """Tests for package installation operations."""

    @patch("subprocess.Popen")
    def test_install_packages_pacman_success(self, mock_popen: Mock) -> None:
        """Should successfully install packages with pacman."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock process output
            mock_process = Mock()
            mock_process.stdout = iter(
                [
                    "installing base-1.0\n",
                    "installing linux-6.0\n",
                    "installing vim-9.0\n",
                ]
            )
            mock_process.wait.return_value = 0
            mock_popen.return_value = mock_process

            job = PackagesJob()
            packages = ["base", "linux", "vim"]
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()

            result = job._install_packages_pacman(packages, tmpdir, context)

            assert result.success is True
            assert job._packages_installed == packages
            mock_popen.assert_called_once()

    @patch("subprocess.Popen")
    def test_install_packages_pacman_failure(self, mock_popen: Mock) -> None:
        """Should handle pacman installation failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_process = Mock()
            mock_process.stdout = iter(["error: failed to install\n"])
            mock_process.wait.return_value = 1
            mock_popen.return_value = mock_process

            job = PackagesJob()
            packages = ["nonexistent"]
            context = JobContext(target_root=tmpdir)

            result = job._install_packages_pacman(packages, tmpdir, context)

            assert result.success is False
            assert result.error_code == 36


# =============================================================================
# Retry Logic Tests
# =============================================================================


class TestInstallWithRetry:
    """Tests for installation retry logic."""

    @patch.object(PackagesJob, "_install_packages_pacman")
    def test_install_retry_success_first_attempt(self, mock_install: Mock) -> None:
        """Should succeed on first attempt without retry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_install.return_value = JobResult.ok("Installed")

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)
            packages = ["vim", "git"]

            result = job._install_packages_with_retry(packages, context)

            assert result.success is True
            assert mock_install.call_count == 1

    @patch.object(PackagesJob, "_install_packages_pacman")
    @patch("time.sleep")
    def test_install_retry_success_after_retry(self, mock_sleep: Mock, mock_install: Mock) -> None:
        """Should succeed after retry on network failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First attempt fails, second succeeds
            mock_install.side_effect = [
                JobResult.fail("Network error", error_code=33),
                JobResult.ok("Installed"),
            ]

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()
            packages = ["vim"]

            result = job._install_packages_with_retry(packages, context)

            assert result.success is True
            assert mock_install.call_count == 2
            mock_sleep.assert_called_once_with(PackagesJob.RETRY_DELAY)

    @patch.object(PackagesJob, "_install_packages_pacman")
    @patch("time.sleep")
    def test_install_retry_max_attempts(self, _mock_sleep: Mock, mock_install: Mock) -> None:
        """Should fail after max retries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_install.return_value = JobResult.fail("Network error", error_code=33)

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()
            packages = ["vim"]

            result = job._install_packages_with_retry(packages, context)

            assert result.success is False
            assert result.error_code == 39
            assert mock_install.call_count == PackagesJob.MAX_RETRIES

    @patch.object(PackagesJob, "_install_packages_pacman")
    def test_install_retry_non_network_error(self, mock_install: Mock) -> None:
        """Should not retry for non-network errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_install.return_value = JobResult.fail("Invalid package", error_code=31)

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)
            packages = ["invalid!"]

            result = job._install_packages_with_retry(packages, context)

            assert result.success is False
            assert result.error_code == 31
            assert mock_install.call_count == 1  # No retry


# =============================================================================
# Validation Tests
# =============================================================================


class TestPackagesJobValidate:
    """Tests for JobContext validation."""

    def test_validate_all_valid(self) -> None:
        """Validate should pass with all valid selections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "package_manager": "pacman",
                    "mode": "essential",
                },
            )

            result = job.validate(context)

            assert result.success is True

    def test_validate_invalid_mode(self) -> None:
        """Should fail with invalid installation mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "invalid"},
            )

            result = job.validate(context)

            assert result.success is False
            assert "mode" in result.message.lower()

    def test_validate_custom_mode_no_packages(self) -> None:
        """Should fail if custom mode has no packages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "custom"},
            )

            result = job.validate(context)

            assert result.success is False
            assert "packages" in result.message.lower()

    def test_validate_custom_mode_invalid_packages(self) -> None:
        """Should fail if custom packages are invalid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "mode": "custom",
                    "packages": ["vim!", "git@"],
                },
            )

            result = job.validate(context)

            assert result.success is False

    def test_validate_target_not_exists(self) -> None:
        """Should fail if target directory does not exist."""
        job = PackagesJob()
        context = JobContext(target_root="/nonexistent/path")

        result = job.validate(context)

        assert result.success is False
        assert "target directory not found" in result.message.lower()

    def test_validate_multiple_errors(self) -> None:
        """Should report all validation errors."""
        job = PackagesJob()
        context = JobContext(
            target_root="/nonexistent",
            selections={
                "mode": "custom",
                "packages": [],
            },
        )

        result = job.validate(context)

        assert result.success is False
        assert "errors" in result.data
        assert len(result.data["errors"]) >= 2


# =============================================================================
# Full Job Execution Tests
# =============================================================================


class TestSkipsRedundantSyncWhenAlreadySynced:
    """Deliberate speed deviation from Calamares (which unconditionally
    re-syncs here even right after shellprocess@update's own -Syy):
    packages.py skips its own repo sync when systemupdate.py already did
    one this run, avoiding a second full repo re-download."""

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_skips_update_repositories_when_repos_synced(
        self, mock_update: Mock, mock_install: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_install.return_value = JobResult.ok("Installed")
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "essential", "repos_synced": True},
            )

            result = job.run(context)

            assert result.success is True
            mock_update.assert_not_called()

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_still_syncs_when_not_already_synced(
        self, mock_update: Mock, mock_install: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "essential", "has_internet": True},
            )

            result = job.run(context)

            assert result.success is True
            mock_update.assert_called_once()


class TestSkipsInstallWhenOffline:
    """The live ISO ships no pacman sync databases and no cached package
    archives (both stripped from the squashfs at build time) - there is no
    local source pacman could install anything new from offline. base/linux/
    etc. are already on disk from install.py's squashfs extraction, so both
    the repo sync AND the package install must be skipped offline, matching
    real Calamares's packages-offline.conf (remove-only, no install op)."""

    @patch.object(PackagesJob, "_remove_packages")
    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_skips_update_and_install_when_offline(
        self, mock_update: Mock, mock_install: Mock, mock_remove: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_remove.return_value = JobResult.ok("Removed")
            job = PackagesJob({"remove": ["live-launcher"]})
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "essential", "has_internet": False},
            )

            result = job.run(context)

            assert result.success is True
            mock_update.assert_not_called()
            mock_install.assert_not_called()
            mock_remove.assert_called_once()

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_offline_skips_even_when_repos_synced_flag_set(
        self, mock_update: Mock, mock_install: Mock
    ) -> None:
        """has_internet is checked before repos_synced - offline always wins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "mode": "essential",
                    "has_internet": False,
                    "repos_synced": True,
                },
            )

            result = job.run(context)

            assert result.success is True
            mock_update.assert_not_called()
            mock_install.assert_not_called()

    @patch("xen.jobs.packages.NetworkHelper.check_internet_connectivity")
    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_falls_back_to_live_check_when_has_internet_unset(
        self, mock_update: Mock, mock_install: Mock, mock_check: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_check.return_value = False
            job = PackagesJob()
            context = JobContext(target_root=tmpdir, selections={"mode": "essential"})

            result = job.run(context)

            assert result.success is True
            mock_check.assert_called_once()
            mock_update.assert_not_called()
            mock_install.assert_not_called()

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_online_still_installs(self, mock_update: Mock, mock_install: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")
            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "essential", "has_internet": True},
            )

            result = job.run(context)

            assert result.success is True
            mock_update.assert_called_once()
            mock_install.assert_called_once()


class TestPackagesJobRun:
    """Tests for full PackagesJob execution."""

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_essential_mode(self, mock_update: Mock, mock_install: Mock) -> None:
        """Should run successfully in essential mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")

            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "essential", "has_internet": True},
            )
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert result.data["mode"] == "essential"
            assert "packages_installed" in result.data
            mock_update.assert_called_once()
            mock_install.assert_called_once()

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_desktop_mode(self, mock_update: Mock, mock_install: Mock) -> None:
        """Should run successfully in desktop mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")

            job = PackagesJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"mode": "desktop", "has_internet": True},
            )
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert result.data["mode"] == "desktop"

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_custom_mode(self, mock_update: Mock, mock_install: Mock) -> None:
        """Should run successfully in custom mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")

            job = PackagesJob()
            custom_pkgs = ["vim", "git", "htop"]
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "mode": "custom",
                    "packages": custom_pkgs,
                    "has_internet": True,
                },
            )
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert result.data["mode"] == "custom"

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_update_failure_continues(self, mock_update: Mock, mock_install: Mock) -> None:
        """Should continue installation if update fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.fail("Update failed", error_code=33)
            mock_install.return_value = JobResult.ok("Installed")

            job = PackagesJob()
            context = JobContext(target_root=tmpdir, selections={"has_internet": True})
            context.on_progress = MagicMock()

            result = job.run(context)

            # Should succeed despite update failure
            assert result.success is True
            mock_install.assert_called_once()

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_validation_fails(self, mock_update: Mock, mock_install: Mock) -> None:
        """Should fail if validation fails."""
        job = PackagesJob()
        context = JobContext(
            target_root="/nonexistent",
            selections={"mode": "essential"},
        )

        result = job.run(context)

        assert result.success is False
        mock_update.assert_not_called()
        mock_install.assert_not_called()

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_installation_fails(self, mock_update: Mock, mock_install: Mock) -> None:
        """Should fail if installation fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.fail("Installation failed", error_code=36)

            job = PackagesJob()
            context = JobContext(target_root=tmpdir, selections={"has_internet": True})

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 36

    def test_estimate_duration_essential(self) -> None:
        """Should return reasonable duration estimate for essential mode."""
        job = PackagesJob({"mode": "essential"})
        duration = job.estimate_duration()
        assert duration == 300

    def test_estimate_duration_desktop(self) -> None:
        """Should return longer duration for desktop mode."""
        job = PackagesJob({"mode": "desktop"})
        duration = job.estimate_duration()
        assert duration == 900

    def test_estimate_duration_custom(self) -> None:
        """Should estimate based on package count for custom mode."""
        job = PackagesJob({"mode": "custom", "packages": ["vim", "git", "htop"]})
        duration = job.estimate_duration()
        # Should be at least base estimate
        assert duration >= 300


# =============================================================================
# Integration Tests
# =============================================================================


class TestPackagesJobIntegration:
    """Integration tests for complete PackagesJob workflow."""

    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_full_workflow_essential(self, mock_update: Mock, mock_install: Mock) -> None:
        """Test complete package installation workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")

            # Create job
            job = PackagesJob({"mode": "essential"})

            # Prepare context
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "package_manager": "pacman",
                    "mode": "essential",
                    "has_internet": True,
                },
            )
            context.on_progress = MagicMock()

            # Validate first
            validation = job.validate(context)
            assert validation.success is True

            # Run the job
            result = job.run(context)
            assert result.success is True

            # Verify results
            assert result.data["mode"] == "essential"
            assert "packages_installed" in result.data
            assert context.on_progress.called

    def test_cleanup(self) -> None:
        """Test cleanup operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = PackagesJob()
            context = JobContext(target_root=tmpdir)

            # Should not raise any exceptions
            job.cleanup(context)


# =============================================================================
# Optional Package Merge Tests
# =============================================================================


class TestGetPackageListOptionalMerge:
    """Tests for merging optional_packages into the install list."""

    def test_essential_mode_with_optional_packages(self) -> None:
        job = PackagesJob()
        context = JobContext(
            selections={"mode": "essential", "optional_packages": ["firefox", "gimp"]}
        )

        packages = job._get_package_list(context)

        assert "firefox" in packages
        assert "gimp" in packages
        assert all(pkg in packages for pkg in PackagesJob.ESSENTIAL_PACKAGES)

    def test_optional_packages_deduplicated(self) -> None:
        job = PackagesJob()
        context = JobContext(selections={"mode": "essential", "optional_packages": ["base"]})

        packages = job._get_package_list(context)

        assert packages.count("base") == 1

    def test_no_optional_packages_unchanged(self) -> None:
        job = PackagesJob()
        context = JobContext(selections={"mode": "essential"})

        packages = job._get_package_list(context)

        assert packages == PackagesJob.ESSENTIAL_PACKAGES

    def test_custom_mode_still_exact_match_without_optional(self) -> None:
        job = PackagesJob()
        custom_pkgs = ["vim", "git", "htop"]
        context = JobContext(selections={"mode": "custom", "packages": custom_pkgs})

        packages = job._get_package_list(context)

        assert packages == custom_pkgs


class TestGetPackageListBtrfsMerge:
    """Tests for merging btrfs_packages into the install list when
    filesystem == "btrfs" (Snapper/grub-btrfs/btrfs-assistant)."""

    def test_btrfs_packages_merged_when_filesystem_is_btrfs(self) -> None:
        job = PackagesJob(
            {"btrfs_packages": ["snapper", "snap-pac", "grub-btrfs", "btrfs-assistant"]}
        )
        context = JobContext(selections={"mode": "essential", "filesystem": "btrfs"})

        packages = job._get_package_list(context)

        for pkg in ("snapper", "snap-pac", "grub-btrfs", "btrfs-assistant"):
            assert pkg in packages

    def test_btrfs_packages_not_merged_for_other_filesystems(self) -> None:
        job = PackagesJob({"btrfs_packages": ["snapper", "snap-pac"]})
        context = JobContext(selections={"mode": "essential", "filesystem": "ext4"})

        packages = job._get_package_list(context)

        assert "snapper" not in packages
        assert "snap-pac" not in packages

    def test_btrfs_packages_deduplicated(self) -> None:
        job = PackagesJob({"btrfs_packages": ["base"]})
        context = JobContext(selections={"mode": "essential", "filesystem": "btrfs"})

        packages = job._get_package_list(context)

        assert packages.count("base") == 1

    def test_no_btrfs_packages_configured_is_noop(self) -> None:
        job = PackagesJob()
        context = JobContext(selections={"mode": "essential", "filesystem": "btrfs"})

        packages = job._get_package_list(context)

        assert packages == PackagesJob.ESSENTIAL_PACKAGES


# =============================================================================
# Remove Operation Tests
# =============================================================================


class TestRemovePackages:
    @patch("subprocess.run")
    def test_remove_packages_pacman_success(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(stdout="", stderr="")
            job = PackagesJob()
            context = JobContext()

            result = job._remove_packages(["live-launcher"], tmpdir, context)

            assert result.success is True
            call_args = mock_run.call_args[0][0]
            assert call_args == [
                "arch-chroot",
                tmpdir,
                "pacman",
                "-R",
                "--noconfirm",
                "live-launcher",
            ]

    @patch("subprocess.run")
    def test_remove_packages_failure(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.CalledProcessError(1, "pacman", stderr="not installed")
            job = PackagesJob()
            context = JobContext()

            result = job._remove_packages(["nonexistent"], tmpdir, context)

            assert result.success is False
            assert result.error_code == 41


class TestRemovePackagesFiltersToInstalled:
    """Regression: pacman -R is all-or-nothing - asking it to remove even
    one package name that isn't installed (e.g. the wrong GPU-variant cfg
    package) fails the whole command, silently skipping removal of
    everything else configured too."""

    @patch("subprocess.run")
    def test_only_removes_packages_actually_installed(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:

            def run_side_effect(cmd: list[str], **_kwargs: object) -> Mock:
                if cmd[2:4] == ["pacman", "-Qq"]:
                    return Mock(returncode=0, stdout="ckbcomp\nqt6-doc\n", stderr="")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = run_side_effect
            job = PackagesJob()
            context = JobContext()

            result = job._remove_packages(
                ["ckbcomp", "qt6-doc", "xen-cfg-xerolinux-nvidia"], tmpdir, context
            )

            assert result.success is True
            remove_call = mock_run.call_args_list[-1].args[0]
            assert remove_call == ["arch-chroot", tmpdir, "pacman", "-R", "--noconfirm",
                                    "ckbcomp", "qt6-doc"]

    @patch("subprocess.run")
    def test_skips_pacman_entirely_when_none_installed(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="other-package\n", stderr="")
            job = PackagesJob()
            context = JobContext()

            result = job._remove_packages(["not-installed"], tmpdir, context)

            assert result.success is True
            assert mock_run.call_count == 1  # only the -Qq query, no -R call

    @patch("subprocess.run")
    def test_falls_back_to_unfiltered_list_when_query_fails(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="")
            job = PackagesJob()
            context = JobContext()

            result = job._remove_packages(["ckbcomp"], tmpdir, context)

            assert result.success is True
            remove_call = mock_run.call_args_list[-1].args[0]
            assert remove_call[-1] == "ckbcomp"


class TestRemovePackagesFalkonOnlineOfflineSplit:
    """falkon: Calamares removes it only when online (packages.conf), keeps
    it offline (packages-offline.conf, fallback browser). packages.py
    applies that split at runtime from context.selections["has_internet"]
    since Xen shares one remove list across both cases."""

    @staticmethod
    def _run_side_effect(cmd: list[str], **_kwargs: object) -> Mock:
        if cmd[2:4] == ["pacman", "-Qq"]:
            return Mock(returncode=0, stdout="falkon\nckbcomp\n", stderr="")
        return Mock(returncode=0, stdout="", stderr="")

    @patch("subprocess.run")
    def test_falkon_removed_when_online(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = self._run_side_effect
            job = PackagesJob()
            context = JobContext(selections={"has_internet": True})

            result = job._remove_packages(["falkon", "ckbcomp"], tmpdir, context)

            assert result.success is True
            remove_call = mock_run.call_args_list[-1].args[0]
            assert "falkon" in remove_call

    @patch("subprocess.run")
    def test_falkon_kept_when_offline(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = self._run_side_effect
            job = PackagesJob()
            context = JobContext(selections={"has_internet": False})

            result = job._remove_packages(["falkon", "ckbcomp"], tmpdir, context)

            assert result.success is True
            remove_call = mock_run.call_args_list[-1].args[0]
            assert "falkon" not in remove_call
            assert "ckbcomp" in remove_call

    @patch("subprocess.run")
    def test_no_network_probe_when_falkon_not_configured(self, mock_run: Mock) -> None:
        """Regression: must not touch the network for removals that don't
        include falkon at all - only falkon's presence should trigger the
        connectivity check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = PackagesJob()
            context = JobContext()

            with patch(
                "xen.jobs.packages.NetworkHelper.check_internet_connectivity"
            ) as mock_check:
                result = job._remove_packages(["ckbcomp"], tmpdir, context)

            assert result.success is True
            mock_check.assert_not_called()


class TestRunRemovesConfiguredPackages:
    @patch.object(PackagesJob, "_remove_packages")
    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_calls_remove_when_configured(
        self, mock_update: Mock, mock_install: Mock, mock_remove: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")
            mock_remove.return_value = JobResult.ok("Removed")

            job = PackagesJob({"mode": "essential", "remove": ["live-launcher"]})
            context = JobContext(target_root=tmpdir, selections={"mode": "essential"})
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            mock_remove.assert_called_once_with(["live-launcher"], tmpdir, context)

    @patch.object(PackagesJob, "_remove_packages")
    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_skips_remove_when_not_configured(
        self, mock_update: Mock, mock_install: Mock, mock_remove: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")

            job = PackagesJob()
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()

            job.run(context)

            mock_remove.assert_not_called()

    @patch.object(PackagesJob, "_remove_packages")
    @patch.object(PackagesJob, "_install_packages_with_retry")
    @patch.object(PackagesJob, "_update_repositories")
    def test_run_succeeds_even_if_remove_fails(
        self, mock_update: Mock, mock_install: Mock, mock_remove: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_update.return_value = JobResult.ok("Updated")
            mock_install.return_value = JobResult.ok("Installed")
            mock_remove.return_value = JobResult.fail("boom", error_code=41)

            job = PackagesJob({"remove": ["live-launcher"]})
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True

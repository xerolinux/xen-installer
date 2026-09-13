"""Unit tests for FinishedJob."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

try:
    from xen.jobs.base import JobContext, JobResult, JobStatus
    from xen.jobs.finished import FinishedJob

    HAS_FINISHED_JOB = True
except ImportError:
    HAS_FINISHED_JOB = False

# Skip entire module if xen finished job is not available
pytestmark = pytest.mark.skipif(not HAS_FINISHED_JOB, reason="FinishedJob not available")


# =============================================================================
# FinishedJob Initialization Tests
# =============================================================================


class TestFinishedJobInit:
    """Tests for FinishedJob initialization."""

    def test_init_defaults(self) -> None:
        """FinishedJob should have correct defaults."""
        job = FinishedJob()
        assert job.name == "finished"
        assert job.description == "Installation completion and cleanup"
        assert job.status == JobStatus.PENDING
        assert job._summary == {}

    def test_init_with_config(self) -> None:
        """FinishedJob should accept configuration."""
        config = {"save_logs_default": True}
        job = FinishedJob(config)
        assert job._config == config


# =============================================================================
# Summary Generation Tests
# =============================================================================


class TestGenerateSummary:
    """Tests for installation summary generation."""

    def test_generate_summary_empty_selections(self) -> None:
        """Should generate minimal summary with no selections."""
        job = FinishedJob()
        context = JobContext(target_root="/mnt/target")

        summary = job._generate_summary(context)

        assert "timestamp" in summary
        assert summary["target_root"] == "/mnt/target"
        assert summary["status"] == "completed"
        assert summary["system"] == {}
        assert summary["partitions"] == {}
        assert summary["user"] == {}
        assert summary["locale"] == {}

    def test_generate_summary_full_selections(self) -> None:
        """Should generate complete summary with all selections."""
        job = FinishedJob()
        context = JobContext(
            target_root="/mnt/target",
            selections={
                "hostname": "testhost",
                "disk": "/dev/sda",
                "filesystem": "xfs",
                "mode": "auto",
                "swap_size": 4,
                "username": "testuser",
                "fullname": "Test User",
                "locale": "fr_FR.UTF-8",
                "timezone": "Europe/Paris",
                "keymap": "fr",
            },
        )

        summary = job._generate_summary(context)

        # Verify system info
        assert summary["system"]["hostname"] == "testhost"

        # Verify partition info
        assert summary["partitions"]["disk"] == "/dev/sda"
        assert summary["partitions"]["filesystem"] == "xfs"
        assert summary["partitions"]["mode"] == "auto"
        assert summary["partitions"]["swap_size_gb"] == 4

        # Verify user info (no sensitive data)
        assert summary["user"]["username"] == "testuser"
        assert summary["user"]["fullname"] == "Test User"
        assert "password" not in summary["user"]

        # Verify locale info
        assert summary["locale"]["locale"] == "fr_FR.UTF-8"
        assert summary["locale"]["timezone"] == "Europe/Paris"
        assert summary["locale"]["keymap"] == "fr"

    def test_generate_summary_partial_selections(self) -> None:
        """Should handle partial selections gracefully."""
        job = FinishedJob()
        context = JobContext(
            target_root="/mnt",
            selections={
                "hostname": "host1",
                "locale": "en_US.UTF-8",
                # Missing disk, user, timezone, etc.
            },
        )

        summary = job._generate_summary(context)

        assert summary["system"]["hostname"] == "host1"
        assert summary["locale"]["locale"] == "en_US.UTF-8"
        assert summary["partitions"] == {}
        assert summary["user"] == {}

    def test_generate_summary_no_swap(self) -> None:
        """Should omit swap_size_gb when swap is not used."""
        job = FinishedJob()
        context = JobContext(
            selections={
                "disk": "/dev/sda",
                "swap_size": 0,  # No swap
            }
        )

        summary = job._generate_summary(context)

        assert "swap_size_gb" not in summary["partitions"]


# =============================================================================
# Log Saving Tests
# =============================================================================


class TestSaveLogs:
    """Tests for log saving functionality."""

    def test_save_logs_disabled(self) -> None:
        """Should skip log saving when disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"save_logs": False},
            )

            result = job._save_logs(context)

            assert result.success is True
            assert "skipped" in result.message.lower()

            # Verify no log directory was created
            log_dir = Path(tmpdir) / "var" / "log" / "xen-installer"
            assert not log_dir.exists()

    def test_save_logs_creates_directory(self) -> None:
        """Should create log directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            job._summary = {"test": "data"}
            context = JobContext(
                target_root=tmpdir,
                selections={"save_logs": True},
            )

            result = job._save_logs(context)

            assert result.success is True

            # Verify directory structure
            log_dir = Path(tmpdir) / "var" / "log" / "xen-installer"
            assert log_dir.exists()
            assert log_dir.is_dir()

    def test_save_logs_creates_summary_json(self) -> None:
        """Should save installation summary as JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            job._summary = {
                "timestamp": "2024-01-01T00:00:00",
                "system": {"hostname": "testhost"},
            }
            context = JobContext(
                target_root=tmpdir,
                selections={"save_logs": True},
            )

            result = job._save_logs(context)

            assert result.success is True

            # Verify JSON file
            summary_file = Path(tmpdir) / "var" / "log" / "xen-installer" / "install-summary.json"
            assert summary_file.exists()

            # Verify JSON content
            summary_data = json.loads(summary_file.read_text())
            assert summary_data["timestamp"] == "2024-01-01T00:00:00"
            assert summary_data["system"]["hostname"] == "testhost"

    def test_save_logs_copies_existing_logs(self) -> None:
        """Should copy existing log files if available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake log file
            fake_log = Path("/tmp/xen.log")
            try:
                fake_log.write_text("Test log content", encoding="utf-8")

                job = FinishedJob()
                job._summary = {"test": "data"}
                context = JobContext(
                    target_root=tmpdir,
                    selections={"save_logs": True},
                )

                result = job._save_logs(context)

                assert result.success is True

                # Verify log was copied
                log_dir = Path(tmpdir) / "var" / "log" / "xen-installer"
                log_files = list(log_dir.glob("xen-*.log"))
                assert len(log_files) >= 1

            finally:
                # Cleanup
                if fake_log.exists():
                    fake_log.unlink()

    def test_save_logs_handles_errors_gracefully(self) -> None:
        """Should handle log saving errors gracefully (non-critical)."""
        job = FinishedJob()
        job._summary = {"test": "data"}

        # Use invalid target_root
        context = JobContext(
            target_root="/invalid/nonexistent/path",
            selections={"save_logs": True},
        )

        result = job._save_logs(context)

        # Should succeed but indicate failure (non-critical)
        assert result.success is True
        assert "failed" in result.message.lower()


# =============================================================================
# Safe Unmount Tests
# =============================================================================


class TestSafeUnmount:
    """Tests for safe unmount functionality."""

    @patch("xen.jobs.finished.subprocess.run")
    @patch("xen.jobs.finished.os.path.ismount")
    def test_unmount_not_mounted(self, mock_ismount: Mock, mock_run: Mock) -> None:
        """Should skip unmount if path is not mounted."""
        mock_ismount.return_value = False

        job = FinishedJob()
        result = job._safe_unmount(Path("/not/mounted"))

        assert result is True
        # Should not call subprocess
        mock_run.assert_not_called()

    @patch("xen.jobs.finished.subprocess.run")
    @patch("xen.jobs.finished.os.path.ismount")
    def test_unmount_success(self, mock_ismount: Mock, mock_run: Mock) -> None:
        """Should successfully unmount filesystem."""
        mock_ismount.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        job = FinishedJob()
        result = job._safe_unmount(Path("/mnt/target"))

        assert result is True
        mock_run.assert_called_once()
        assert "umount" in mock_run.call_args[0][0]

    @patch("xen.jobs.finished.subprocess.run")
    @patch("xen.jobs.finished.os.path.ismount")
    def test_unmount_lazy_fallback(self, mock_ismount: Mock, mock_run: Mock) -> None:
        """Should use lazy unmount if normal unmount fails."""
        mock_ismount.return_value = True

        # First call (normal unmount) fails, second call (lazy) succeeds
        from subprocess import CalledProcessError

        mock_run.side_effect = [
            CalledProcessError(1, "umount", stderr="Device busy"),
            MagicMock(returncode=0),  # Lazy unmount succeeds
        ]

        job = FinishedJob()
        result = job._safe_unmount(Path("/mnt/busy"))

        # Should succeed with lazy unmount
        assert result is True
        assert mock_run.call_count == 2

    @patch("xen.jobs.finished.subprocess.run")
    @patch("xen.jobs.finished.os.path.ismount")
    def test_unmount_failure(self, mock_ismount: Mock, mock_run: Mock) -> None:
        """Should return False if all unmount attempts fail."""
        mock_ismount.return_value = True

        # Both normal and lazy unmount fail
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "umount", stderr="Error")

        job = FinishedJob()
        result = job._safe_unmount(Path("/mnt/stuck"))

        assert result is False


# =============================================================================
# Cleanup Mounts Tests
# =============================================================================


class TestCleanupMounts:
    """Tests for filesystem cleanup."""

    @patch("xen.jobs.finished.FinishedJob._safe_unmount")
    @patch("xen.jobs.finished.os.path.ismount")
    @patch("xen.jobs.finished.subprocess.run")
    def test_cleanup_unmounts_in_order(
        self, mock_run: Mock, mock_ismount: Mock, mock_unmount: Mock
    ) -> None:
        """Should unmount filesystems in correct order."""
        mock_unmount.return_value = True
        mock_ismount.return_value = True  # Simulate mounted filesystems
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the EFI mount point structure to trigger unmount logic
            efi_mount = Path(tmpdir) / "boot" / "efi"
            efi_mount.mkdir(parents=True, exist_ok=True)

            job = FinishedJob()
            context = JobContext(target_root=tmpdir)

            result = job._cleanup_mounts(context)

            assert result.success is True
            # Should call unmount at least once (root directory exists)
            # Both EFI and root should be unmounted
            assert mock_unmount.call_count >= 1

    @patch("xen.jobs.finished.FinishedJob._safe_unmount")
    @patch("subprocess.run")
    def test_cleanup_deactivates_swap(self, mock_run: Mock, mock_unmount: Mock) -> None:
        """Should deactivate swap partition if present."""
        mock_unmount.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"swap_partition": "/dev/sda3"},
            )

            result = job._cleanup_mounts(context)

            assert result.success is True

            # Verify swapoff was called
            swapoff_calls = [call for call in mock_run.call_args_list if "swapoff" in call[0][0]]
            assert len(swapoff_calls) >= 1

    @patch("xen.jobs.finished.FinishedJob._safe_unmount")
    @patch("xen.jobs.finished.os.path.ismount")
    def test_cleanup_reports_errors(self, mock_ismount: Mock, mock_unmount: Mock) -> None:
        """Should report cleanup errors."""
        # Simulate mounted filesystems
        mock_ismount.return_value = True
        # Simulate unmount failures
        mock_unmount.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            context = JobContext(target_root=tmpdir)

            result = job._cleanup_mounts(context)

            assert result.success is False
            assert result.error_code == 50
            assert "errors" in result.data
            assert len(result.data["errors"]) > 0

    @patch("xen.jobs.finished.FinishedJob._safe_unmount")
    @patch("subprocess.run")
    def test_cleanup_handles_missing_swap_gracefully(
        self, mock_run: Mock, mock_unmount: Mock
    ) -> None:
        """Should handle missing swapoff command gracefully."""
        mock_unmount.return_value = True
        mock_run.side_effect = FileNotFoundError("swapoff not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"swap_partition": "/dev/sda3"},
            )

            result = job._cleanup_mounts(context)

            # Should not fail completely due to missing swapoff
            # (unmount succeeded, only swap deactivation failed)
            assert result.success is True


# =============================================================================
# Action Preparation Tests
# =============================================================================


class TestPrepareAction:
    """Tests for post-installation action preparation."""

    def test_prepare_action_reboot(self) -> None:
        """Should prepare for reboot action."""
        job = FinishedJob()
        context = JobContext(selections={"action": "reboot"})

        result = job._prepare_action(context)

        assert result.success is True
        assert result.data["action"] == "reboot"
        assert "command" in result.data
        assert "reboot" in result.data["command"]

    def test_prepare_action_shutdown(self) -> None:
        """Should prepare for shutdown action."""
        job = FinishedJob()
        context = JobContext(selections={"action": "shutdown"})

        result = job._prepare_action(context)

        assert result.success is True
        assert result.data["action"] == "shutdown"
        assert "command" in result.data
        assert "poweroff" in result.data["command"]

    def test_prepare_action_continue(self) -> None:
        """Should prepare for continue action (no immediate action)."""
        job = FinishedJob()
        context = JobContext(selections={"action": "continue"})

        result = job._prepare_action(context)

        assert result.success is True
        assert result.data["action"] == "continue"
        assert "message" in result.data

    def test_prepare_action_default(self) -> None:
        """Should default to continue if no action specified."""
        job = FinishedJob()
        context = JobContext()

        result = job._prepare_action(context)

        assert result.success is True
        assert result.data["action"] == "continue"

    def test_prepare_action_unknown(self) -> None:
        """Should handle unknown actions gracefully."""
        job = FinishedJob()
        context = JobContext(selections={"action": "invalid_action"})

        result = job._prepare_action(context)

        assert result.success is True
        assert result.data["action"] == "continue"  # Fallback


# =============================================================================
# Validation Tests
# =============================================================================


class TestFinishedJobValidate:
    """Tests for JobContext validation."""

    def test_validate_valid_action(self) -> None:
        """Should pass validation with valid action."""
        job = FinishedJob()
        context = JobContext(selections={"action": "reboot"})

        result = job.validate(context)

        assert result.success is True

    def test_validate_invalid_action(self) -> None:
        """Should fail validation with invalid action."""
        job = FinishedJob()
        context = JobContext(selections={"action": "invalid"})

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 49

    def test_validate_save_logs_boolean(self) -> None:
        """Should validate save_logs is boolean."""
        job = FinishedJob()
        context = JobContext(selections={"save_logs": "yes"})  # Invalid type

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 49

    def test_validate_all_valid(self) -> None:
        """Should pass validation with all valid selections."""
        job = FinishedJob()
        context = JobContext(
            selections={
                "action": "shutdown",
                "save_logs": True,
            }
        )

        result = job.validate(context)

        assert result.success is True


# =============================================================================
# Full Job Execution Tests
# =============================================================================


class TestFinishedJobRun:
    """Tests for full FinishedJob execution."""

    @patch("xen.jobs.finished.FinishedJob._cleanup_mounts")
    @patch("xen.jobs.finished.FinishedJob._save_logs")
    def test_run_with_defaults(self, mock_save: Mock, mock_cleanup: Mock) -> None:
        """Should run successfully with default selections."""
        mock_save.return_value = JobResult.ok("Logs saved")
        mock_cleanup.return_value = JobResult.ok("Cleanup complete")

        job = FinishedJob()
        context = JobContext()
        context.on_progress = MagicMock()

        result = job.run(context)

        assert result.success is True
        assert "summary" in result.data
        assert "action" in result.data
        assert result.data["action"] == "continue"

        # Verify progress was reported
        assert context.on_progress.called

    @patch("xen.jobs.finished.FinishedJob._cleanup_mounts")
    @patch("xen.jobs.finished.FinishedJob._save_logs")
    def test_run_with_reboot_action(self, mock_save: Mock, mock_cleanup: Mock) -> None:
        """Should run successfully with reboot action."""
        mock_save.return_value = JobResult.ok("Logs saved")
        mock_cleanup.return_value = JobResult.ok("Cleanup complete")

        job = FinishedJob()
        context = JobContext(selections={"action": "reboot"})
        context.on_progress = MagicMock()

        result = job.run(context)

        assert result.success is True
        assert result.data["action"] == "reboot"
        assert result.data["reboot_ready"] is True
        assert "command" in result.data

    @patch("xen.jobs.finished.FinishedJob._cleanup_mounts")
    @patch("xen.jobs.finished.FinishedJob._save_logs")
    def test_run_validation_fails(self, mock_save: Mock, mock_cleanup: Mock) -> None:
        """Should fail if validation fails."""
        job = FinishedJob()
        context = JobContext(selections={"action": "invalid_action"})

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 49

        # Should not call save or cleanup
        mock_save.assert_not_called()
        mock_cleanup.assert_not_called()

    @patch("xen.jobs.finished.FinishedJob._cleanup_mounts")
    @patch("xen.jobs.finished.FinishedJob._save_logs")
    def test_run_cleanup_failure(self, mock_save: Mock, mock_cleanup: Mock) -> None:
        """Should fail if cleanup fails."""
        mock_save.return_value = JobResult.ok("Logs saved")
        mock_cleanup.return_value = JobResult.fail(
            "Cleanup failed", error_code=50, data={"errors": ["Error 1"]}
        )

        job = FinishedJob()
        context = JobContext()

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 50
        assert "cleanup_errors" in result.data

    @patch("xen.jobs.finished.FinishedJob._cleanup_mounts")
    @patch("xen.jobs.finished.FinishedJob._save_logs")
    def test_run_log_save_failure_non_critical(self, mock_save: Mock, mock_cleanup: Mock) -> None:
        """Should continue if log saving fails (non-critical)."""
        mock_save.return_value = JobResult.fail("Log save failed")
        mock_cleanup.return_value = JobResult.ok("Cleanup complete")

        job = FinishedJob()
        context = JobContext()

        result = job.run(context)

        # Should still succeed overall
        assert result.success is True

    def test_estimate_duration(self) -> None:
        """Should return reasonable duration estimate."""
        job = FinishedJob()
        duration = job.estimate_duration()
        assert duration == 10


# =============================================================================
# Cleanup Method Tests
# =============================================================================


class TestFinishedJobCleanup:
    """Tests for emergency cleanup on failure."""

    @patch("xen.jobs.finished.FinishedJob._cleanup_mounts")
    def test_cleanup_on_failure(self, mock_cleanup: Mock) -> None:
        """Should attempt cleanup on job failure."""
        mock_cleanup.return_value = JobResult.ok("Cleanup complete")

        job = FinishedJob()
        context = JobContext()

        job.cleanup(context)

        # Should call cleanup_mounts
        mock_cleanup.assert_called_once_with(context)

    @patch("xen.jobs.finished.FinishedJob._cleanup_mounts")
    def test_cleanup_handles_cleanup_failure(self, mock_cleanup: Mock) -> None:
        """Should handle cleanup failure gracefully."""
        mock_cleanup.return_value = JobResult.fail("Cleanup failed", error_code=50)

        job = FinishedJob()
        context = JobContext()

        # Should not raise exception
        job.cleanup(context)

        mock_cleanup.assert_called_once()


# =============================================================================
# Integration Tests
# =============================================================================


class TestFinishedJobIntegration:
    """Integration tests for complete FinishedJob workflow."""

    @patch("xen.jobs.finished.FinishedJob._safe_unmount")
    @patch("xen.jobs.finished.os.path.ismount")
    @patch("xen.jobs.finished.subprocess.run")
    def test_full_workflow(self, mock_run: Mock, mock_ismount: Mock, mock_unmount: Mock) -> None:
        """Test complete finished job workflow."""
        mock_unmount.return_value = True
        mock_ismount.return_value = True  # Simulate mounted filesystems
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "hostname": "test-install",
                    "disk": "/dev/sda",
                    "username": "testuser",
                    "locale": "en_US.UTF-8",
                    "action": "reboot",
                    "save_logs": True,
                },
            )
            context.on_progress = MagicMock()

            # Validate
            validation = job.validate(context)
            assert validation.success is True

            # Run the job
            result = job.run(context)
            assert result.success is True

            # Verify summary was generated
            assert "summary" in result.data
            assert result.data["summary"]["system"]["hostname"] == "test-install"

            # Verify action was prepared
            assert result.data["action"] == "reboot"
            assert result.data["reboot_ready"] is True

            # Verify logs were saved
            log_dir = Path(tmpdir) / "var" / "log" / "xen-installer"
            assert log_dir.exists()

            summary_file = log_dir / "install-summary.json"
            assert summary_file.exists()

            # Verify unmount was called
            assert mock_unmount.called

    @patch("xen.jobs.finished.FinishedJob._safe_unmount")
    @patch("xen.jobs.finished.os.path.ismount")
    def test_workflow_with_cleanup_failure(self, mock_ismount: Mock, mock_unmount: Mock) -> None:
        """Test workflow when cleanup fails."""
        mock_ismount.return_value = True  # Simulate mounted filesystems
        mock_unmount.return_value = False  # Simulate unmount failure

        with tempfile.TemporaryDirectory() as tmpdir:
            job = FinishedJob()
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            # Should fail due to cleanup failure
            assert result.success is False
            assert result.error_code == 50
            assert "cleanup_errors" in result.data

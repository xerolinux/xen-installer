"""Unit tests for RemoveUserJob."""

import subprocess
import tempfile
from unittest.mock import Mock, patch

from xen.jobs.base import JobContext
from xen.jobs.removeuser import RemoveUserJob


class TestRun:
    def test_dry_run_skips(self) -> None:
        job = RemoveUserJob()
        context = JobContext(selections={"dry_run": True})

        result = job.run(context)

        assert result.success is True
        assert "dry run" in result.message.lower()

    @patch("subprocess.run")
    def test_removes_default_username(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = RemoveUserJob()
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            mock_run.assert_called_once_with(
                ["arch-chroot", tmpdir, "userdel", "-r", "liveuser"],
                capture_output=True,
                text=True,
                timeout=30,
            )

    @patch("subprocess.run")
    def test_uses_configured_username(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = RemoveUserJob({"username": "archiso"})
            context = JobContext(target_root=tmpdir)

            job.run(context)

            assert mock_run.call_args[0][0] == ["arch-chroot", tmpdir, "userdel", "-r", "archiso"]

    @patch("subprocess.run")
    def test_no_such_user_is_not_a_failure(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=6, stdout="", stderr="user does not exist")
            job = RemoveUserJob()
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True

    @patch("subprocess.run")
    def test_other_failure_is_fatal(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="permission denied")
            job = RemoveUserJob()
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 112

    @patch("subprocess.run")
    def test_arch_chroot_missing_fails(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = FileNotFoundError()
            job = RemoveUserJob()
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 110

    @patch("subprocess.run")
    def test_timeout_fails(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.TimeoutExpired("userdel", 30)
            job = RemoveUserJob()
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 111

    def test_estimate_duration(self) -> None:
        assert RemoveUserJob().estimate_duration() == 5

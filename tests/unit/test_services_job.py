"""Unit tests for ServicesJob."""

import subprocess
import tempfile
from unittest.mock import MagicMock, Mock, patch

from xen.jobs.base import JobContext
from xen.jobs.services import ServicesJob


class TestRunSystemctl:
    @patch("subprocess.run")
    def test_enable_success(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(stdout="", stderr="")
            job = ServicesJob()

            result = job._run_systemctl(tmpdir, "enable", "NetworkManager")

            assert result.success is True
            call_args = mock_run.call_args[0][0]
            assert call_args == ["arch-chroot", tmpdir, "systemctl", "enable", "NetworkManager"]

    @patch("subprocess.run")
    def test_disable_success(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(stdout="", stderr="")
            job = ServicesJob()

            result = job._run_systemctl(tmpdir, "disable", "reflector")

            assert result.success is True
            call_args = mock_run.call_args[0][0]
            assert call_args == ["arch-chroot", tmpdir, "systemctl", "disable", "reflector"]

    @patch("subprocess.run")
    def test_failure(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl", stderr="not found")
            job = ServicesJob()

            result = job._run_systemctl(tmpdir, "enable", "nonexistent")

            assert result.success is False
            assert result.error_code == 60

    @patch("subprocess.run")
    def test_timeout(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.TimeoutExpired("systemctl", 30)
            job = ServicesJob()

            result = job._run_systemctl(tmpdir, "enable", "slow")

            assert result.success is False
            assert result.error_code == 61

    @patch("subprocess.run")
    def test_arch_chroot_missing(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = FileNotFoundError()
            job = ServicesJob()

            result = job._run_systemctl(tmpdir, "enable", "x")

            assert result.success is False
            assert result.error_code == 62


class TestRun:
    def test_no_units_configured(self) -> None:
        job = ServicesJob()
        context = JobContext()

        result = job.run(context)

        assert result.success is True

    @patch("subprocess.run")
    def test_enables_all_configured_units(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(stdout="", stderr="")
            job = ServicesJob(
                {
                    "units": [
                        {"name": "NetworkManager", "action": "enable", "mandatory": True},
                        {"name": "reflector", "action": "disable", "mandatory": False},
                    ]
                }
            )
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_progress_uses_one_whole_sentence_per_action(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(stdout="", stderr="")
            job = ServicesJob(
                {"units": [{"name": "sshd", "action": "enable"}, {"name": "reflector", "action": "disable"}]}
            )
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()

            job.run(context)

            templates = [c.args[1].template for c in context.on_progress.call_args_list]
            assert "Enabling {name}..." in templates
            assert "Disabling {name}..." in templates

    @patch("subprocess.run")
    def test_mandatory_failure_names_the_service(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl", stderr="fail")
            job = ServicesJob({"units": [{"name": "sshd", "action": "enable", "mandatory": True}]})

            result = job.run(JobContext(target_root=tmpdir))

            assert result.message.template == "Required service {name} could not be enabled: {error}"

    @patch("subprocess.run")
    def test_mandatory_failure_fails_job(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl", stderr="fail")
            job = ServicesJob({"units": [{"name": "sshd", "action": "enable", "mandatory": True}]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is False

    @patch("subprocess.run")
    def test_non_mandatory_failure_does_not_fail_job(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl", stderr="fail")
            job = ServicesJob(
                {"units": [{"name": "cups.socket", "action": "enable", "mandatory": False}]}
            )
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            assert "cups.socket" in result.data["failed"]

    def test_estimate_duration(self) -> None:
        job = ServicesJob({"units": [{"name": f"u{i}"} for i in range(10)]})
        assert job.estimate_duration() == 20

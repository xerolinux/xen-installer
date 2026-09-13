"""Unit tests for SystemUpdateJob."""

import subprocess
import tempfile
from unittest.mock import Mock, patch

from xen.jobs.base import JobContext
from xen.jobs.systemupdate import SystemUpdateJob


class TestNoNetwork:
    @patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity")
    def test_run_skips_gracefully_without_network(self, mock_check: Mock) -> None:
        """Offline must not abort the install (single profile, no separate
        offline build) - it's a clean skip, not a failure."""
        mock_check.return_value = False
        job = SystemUpdateJob()
        context = JobContext()

        result = job.run(context)

        assert result.success is True

    def test_run_skips_when_selections_say_offline(self) -> None:
        """Prefers the once-at-startup probe from bridge.py's selections over
        a live re-check, so the UI notice and job behavior always agree."""
        job = SystemUpdateJob()
        context = JobContext(selections={"has_internet": False})

        with patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity") as mock_check:
            result = job.run(context)
            mock_check.assert_not_called()

        assert result.success is True


class TestRunChroot:
    def test_run_chroot_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", stderr="")
            job = SystemUpdateJob()

            result = job._run_chroot(["pacman", "-Syy"], tmpdir, 60, error_code=52)

            assert result.success is True
            call_args = mock_run.call_args[0][0]
            assert call_args == ["arch-chroot", tmpdir, "pacman", "-Syy"]

    def test_run_chroot_called_process_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "pacman", stderr="boom")
            job = SystemUpdateJob()

            result = job._run_chroot(["pacman", "-Syy"], tmpdir, 60, error_code=52)

            assert result.success is False
            assert result.error_code == 52
            assert "boom" in result.message

    def test_run_chroot_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("pacman", 60)
            job = SystemUpdateJob()

            result = job._run_chroot(["pacman", "-Syy"], tmpdir, 60, error_code=52)

            assert result.success is False
            assert "timed out" in result.message.lower()

    def test_run_chroot_arch_chroot_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            job = SystemUpdateJob()

            result = job._run_chroot(["pacman", "-Syy"], tmpdir, 60, error_code=52)

            assert result.success is False
            assert "arch-chroot not found" in result.message


class TestFullRun:
    @patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity")
    @patch("subprocess.run")
    def test_run_executes_all_five_steps_in_order(self, mock_run: Mock, mock_check: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_check.return_value = True
            mock_run.return_value = Mock(stdout="", stderr="")

            job = SystemUpdateJob()
            context = JobContext(target_root=tmpdir)
            context.on_progress = Mock()

            result = job.run(context)

            assert result.success is True
            assert mock_run.call_count == 5
            commands = [call.args[0] for call in mock_run.call_args_list]
            assert commands[0] == ["arch-chroot", tmpdir, *SystemUpdateJob.REFLECTOR_COMMAND]
            assert commands[1][:3] == ["arch-chroot", tmpdir, "sh"]
            assert commands[2] == ["arch-chroot", tmpdir, "pacman", "-Syy", "--noconfirm"]
            assert commands[3] == [
                "arch-chroot",
                tmpdir,
                "pacman",
                "-S",
                "--noconfirm",
                "archlinux-keyring",
            ]
            assert commands[4] == [
                "arch-chroot",
                tmpdir,
                "pacman",
                "-Su",
                "--noconfirm",
                "--disable-download-timeout",
            ]
            assert context.selections["repos_synced"] is True

    @patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity")
    @patch("subprocess.run")
    def test_reflector_failure_is_non_fatal(self, mock_run: Mock, mock_check: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_check.return_value = True

            def run_side_effect(cmd: list[str], **_kwargs: object) -> Mock:
                if "reflector" in cmd:
                    raise subprocess.CalledProcessError(1, cmd, stderr="no mirrors found")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = run_side_effect
            job = SystemUpdateJob()
            context = JobContext(target_root=tmpdir)
            context.on_progress = Mock()

            result = job.run(context)

            assert result.success is True
            assert mock_run.call_count == 5

    @patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity")
    @patch("subprocess.run")
    def test_repos_synced_not_set_when_syncdb_step_fails(
        self, mock_run: Mock, mock_check: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_check.return_value = True

            def run_side_effect(cmd: list[str], **_kwargs: object) -> Mock:
                if "-Syy" in cmd:
                    raise subprocess.CalledProcessError(1, cmd, stderr="sync failed")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = run_side_effect
            job = SystemUpdateJob()
            context = JobContext(target_root=tmpdir)
            context.on_progress = Mock()

            job.run(context)

            assert "repos_synced" not in context.selections

    @patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity")
    @patch("subprocess.run")
    def test_run_stops_at_first_failing_step(self, mock_run: Mock, mock_check: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_check.return_value = True
            mock_run.side_effect = [
                Mock(stdout="", stderr=""),  # reflector (non-fatal either way)
                Mock(stdout="", stderr=""),  # strip-db
                subprocess.CalledProcessError(1, "pacman", stderr="sync failed"),  # -Syy
            ]

            job = SystemUpdateJob()
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 52
            assert mock_run.call_count == 3

    @patch("xen.jobs.systemupdate.NetworkHelper.check_internet_connectivity")
    def test_uses_configured_timeouts(self, mock_check: Mock) -> None:
        mock_check.return_value = True
        job = SystemUpdateJob(
            {
                "timeout_reflector": 5,
                "timeout_strip_db": 10,
                "timeout_syncdb": 20,
                "timeout_keyring": 30,
                "timeout_upgrade": 40,
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", stderr="")
            context = JobContext(target_root=tmpdir)

            job.run(context)

            timeouts = [call.kwargs["timeout"] for call in mock_run.call_args_list]
            assert timeouts == [5, 10, 20, 30, 40]


class TestEstimateDuration:
    def test_estimate_duration_default(self) -> None:
        job = SystemUpdateJob()
        assert job.estimate_duration() == 60 + 60 + 600 + 300 + 900

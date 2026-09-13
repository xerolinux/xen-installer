"""Unit tests for LuksBootKeyfileJob."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from xen.jobs.base import JobContext
from xen.jobs.luksbootkeyfile import LuksBootKeyfileJob


class TestRun:
    def test_dry_run_skips(self) -> None:
        job = LuksBootKeyfileJob()
        context = JobContext(selections={"dry_run": True, "encryption": True})

        result = job.run(context)

        assert result.success is True

    def test_not_encrypted_skips(self) -> None:
        job = LuksBootKeyfileJob()
        context = JobContext(selections={"encryption": False})

        result = job.run(context)

        assert result.success is True
        assert "luks_keyfile_path" not in context.selections

    def test_encrypted_without_root_partition_fails(self) -> None:
        job = LuksBootKeyfileJob()
        context = JobContext(selections={"encryption": True})

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 110

    def test_encrypted_without_passphrase_fails(self) -> None:
        job = LuksBootKeyfileJob()
        context = JobContext(
            selections={"encryption": True, "root_partition": "/dev/vda2"}
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 111

    @patch("subprocess.run")
    def test_success_writes_keyfile_and_sets_selection(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = LuksBootKeyfileJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "encryption": True,
                    "root_partition": "/dev/vda2",
                    "encryption_passphrase": "hunter2",
                },
            )
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert context.selections["luks_keyfile_path"] == "/etc/cryptsetup-keys.d/root.key"

            keyfile = Path(tmpdir) / "etc" / "cryptsetup-keys.d" / "root.key"
            assert keyfile.exists()
            assert keyfile.stat().st_size == LuksBootKeyfileJob.KEYFILE_SIZE
            assert (keyfile.stat().st_mode & 0o777) == 0o000

    @patch("subprocess.run")
    def test_passphrase_passed_via_stdin_not_argv(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = LuksBootKeyfileJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "encryption": True,
                    "root_partition": "/dev/vda2",
                    "encryption_passphrase": "hunter2",
                },
            )

            job.run(context)

            cmd, kwargs = mock_run.call_args
            assert "hunter2" not in cmd[0]
            assert kwargs["input"] == "hunter2"
            assert cmd[0] == [
                "cryptsetup",
                "luksAddKey",
                "/dev/vda2",
                str(Path(tmpdir) / "etc" / "cryptsetup-keys.d" / "root.key"),
                "--key-file=-",
            ]

    @patch("subprocess.run")
    def test_luksaddkey_failure(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="wrong passphrase")
            job = LuksBootKeyfileJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "encryption": True,
                    "root_partition": "/dev/vda2",
                    "encryption_passphrase": "hunter2",
                },
            )

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 115
            assert "luks_keyfile_path" not in context.selections

    @patch("subprocess.run")
    def test_luksaddkey_timeout(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="cryptsetup", timeout=30)
            job = LuksBootKeyfileJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "encryption": True,
                    "root_partition": "/dev/vda2",
                    "encryption_passphrase": "hunter2",
                },
            )

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 113

    @patch("subprocess.run")
    def test_cryptsetup_missing(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = FileNotFoundError()
            job = LuksBootKeyfileJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "encryption": True,
                    "root_partition": "/dev/vda2",
                    "encryption_passphrase": "hunter2",
                },
            )

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 114


class TestEstimateDuration:
    def test_estimate_duration(self) -> None:
        job = LuksBootKeyfileJob()
        assert job.estimate_duration() == 10

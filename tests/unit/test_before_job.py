"""Unit tests for BeforeJob."""

import tempfile
from unittest.mock import Mock, patch

from xen.jobs.base import JobContext
from xen.jobs.before import BeforeJob


class TestRun:
    """BeforeJob reuses FinalizeJob's tested run() logic entirely - these
    tests just confirm the subclass wiring (name, messages) is correct."""

    def test_dry_run_message_uses_before_label(self) -> None:
        job = BeforeJob({"commands": ["/usr/local/bin/xerolinux-before"]})
        context = JobContext(selections={"dry_run": True})

        result = job.run(context)

        assert result.success is True
        assert "pre-configuration" in result.message

    @patch("subprocess.run")
    def test_runs_xerolinux_before_script(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = BeforeJob({"commands": ["/usr/local/bin/xerolinux-before"]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            mock_run.assert_called_once_with(
                ["arch-chroot", tmpdir, "/usr/local/bin/xerolinux-before"],
                capture_output=True,
                text=True,
                timeout=300,
            )

    def test_name_is_before(self) -> None:
        assert BeforeJob().name == "before"

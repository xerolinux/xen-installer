"""Unit tests for FinalizeJob."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from xen.jobs.base import JobContext
from xen.jobs.finalize import FinalizeJob


class TestRun:
    def test_no_commands_configured(self) -> None:
        job = FinalizeJob()
        context = JobContext()

        result = job.run(context)

        assert result.success is True

    @patch("subprocess.run")
    def test_runs_all_commands_in_order(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck", "/usr/local/bin/nouveau"]})
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert mock_run.call_count == 2
            commands = [call.args[0] for call in mock_run.call_args_list]
            assert commands[0] == ["arch-chroot", tmpdir, "/usr/local/bin/vcheck"]
            assert commands[1] == ["arch-chroot", tmpdir, "/usr/local/bin/nouveau"]

    @patch("subprocess.run")
    def test_failing_command_is_non_fatal(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="boom")
            job = FinalizeJob({"commands": ["/usr/local/bin/skel"]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            assert "/usr/local/bin/skel" in result.data["failed"]

    @patch("subprocess.run")
    def test_timeout_is_non_fatal(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 300)
            job = FinalizeJob({"commands": ["/usr/local/bin/slow"]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            assert "/usr/local/bin/slow" in result.data["failed"]

    @patch("subprocess.run")
    def test_arch_chroot_missing_fails_job(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.side_effect = FileNotFoundError()
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 63

    @patch("subprocess.run")
    def test_uses_configured_timeout(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"], "timeout": 60})
            context = JobContext(target_root=tmpdir)

            job.run(context)

            assert mock_run.call_args.kwargs["timeout"] == 60

    def test_estimate_duration(self) -> None:
        job = FinalizeJob({"commands": ["a", "b", "c"]})
        assert job.estimate_duration() == 45


class TestDedupeGrubCmdline:
    @patch("subprocess.run")
    def test_dedupes_case_insensitive_and_regenerates_grub_cfg(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            grub_default = Path(tmpdir) / "etc" / "default" / "grub"
            grub_default.parent.mkdir(parents=True)
            grub_default.write_text(
                'GRUB_CMDLINE_LINUX_DEFAULT="quiet nowatchdog nvme_load=YES zswap.enabled=0 '
                'nowatchdog loglevel=3 nvme_load=yes modprobe.blacklist=nouveau"\n'
            )

            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            job = FinalizeJob({"commands": ["/usr/local/bin/nouveau"]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            new_content = grub_default.read_text()
            assert new_content == (
                'GRUB_CMDLINE_LINUX_DEFAULT="quiet nowatchdog nvme_load=YES zswap.enabled=0 '
                'loglevel=3 modprobe.blacklist=nouveau"\n'
            )

            grub_mkconfig_calls = [
                call.args[0] for call in mock_run.call_args_list if "grub-mkconfig" in call.args[0]
            ]
            assert len(grub_mkconfig_calls) == 1
            assert grub_mkconfig_calls[0] == [
                "arch-chroot",
                tmpdir,
                "grub-mkconfig",
                "-o",
                "/boot/grub/grub.cfg",
            ]

    @patch("subprocess.run")
    def test_no_grub_default_file_is_noop(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            grub_mkconfig_calls = [
                call.args[0] for call in mock_run.call_args_list if "grub-mkconfig" in call.args[0]
            ]
            assert grub_mkconfig_calls == []

    @patch("subprocess.run")
    def test_no_duplicates_is_noop(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            grub_default = Path(tmpdir) / "etc" / "default" / "grub"
            grub_default.parent.mkdir(parents=True)
            original = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet nowatchdog zswap.enabled=0"\n'
            grub_default.write_text(original)
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
            context = JobContext(target_root=tmpdir)

            result = job.run(context)

            assert result.success is True
            assert grub_default.read_text() == original
            grub_mkconfig_calls = [
                call.args[0] for call in mock_run.call_args_list if "grub-mkconfig" in call.args[0]
            ]
            assert grub_mkconfig_calls == []


class TestTakeFreshInstallSnapshot:
    """One permanent snapshot taken as the very last step, matching
    CachyOS's own shellprocess@btrfs_snapshot timing."""

    @patch("subprocess.run")
    def test_takes_snapshot_when_btrfs(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
            context = JobContext(target_root=tmpdir, selections={"filesystem": "btrfs"})

            result = job.run(context)

            assert result.success is True
            calls = [c.args[0] for c in mock_run.call_args_list if c.args]
            snapshot_cmd = [
                "arch-chroot",
                tmpdir,
                "snapper",
                "--no-dbus",
                "-c",
                "root",
                "create",
                "--description",
                "Fresh installation",
            ]
            assert snapshot_cmd in calls
            mkconfig_cmd = ["arch-chroot", tmpdir, "grub-mkconfig", "-o", "/boot/grub/grub.cfg"]
            assert mkconfig_cmd in calls
            # Must run after the snapshot, so it's already reflected in
            # the boot menu.
            assert calls.index(snapshot_cmd) < calls.index(mkconfig_cmd)

    @patch("subprocess.run")
    def test_no_snapshot_for_non_btrfs(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
            context = JobContext(target_root=tmpdir, selections={"filesystem": "ext4"})

            result = job.run(context)

            assert result.success is True
            calls = [c.args[0] for c in mock_run.call_args_list if c.args]
            assert not any("snapper" in c for c in calls)

    @patch("subprocess.run")
    def test_snapshot_failure_is_non_fatal(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:

            def run_side_effect(cmd: list[str], **_kwargs: object) -> Mock:
                if "snapper" in cmd:
                    return Mock(returncode=1, stdout="", stderr="no config named root")
                return Mock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = run_side_effect
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
            context = JobContext(target_root=tmpdir, selections={"filesystem": "btrfs"})

            result = job.run(context)

            assert result.success is True

    @patch("subprocess.run")
    def test_runs_even_with_no_commands_configured(self, mock_run: Mock) -> None:
        """Regression: a profile with no finalize commands configured
        at all - the snapshot step must still run when filesystem=btrfs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = FinalizeJob()
            context = JobContext(target_root=tmpdir, selections={"filesystem": "btrfs"})

            result = job.run(context)

            assert result.success is True
            calls = [c.args[0] for c in mock_run.call_args_list if c.args]
            assert any("snapper" in c for c in calls)


class TestRestoreSnapshotDetectionScript:
    """snapshots.py disables grub-btrfs's own 41_snapshots-btrfs before
    bootloader.py's grub-mkconfig runs (see that file for why) - restored
    here once a real snapshot exists for it to actually find."""

    @patch("subprocess.run")
    def test_restores_script_before_taking_snapshot(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            disabled = Path(tmpdir) / "etc" / "grub.d" / "41_snapshots-btrfs.xen-disabled"
            disabled.parent.mkdir(parents=True)
            disabled.write_text("#!/bin/sh\necho detecting\n")
            disabled.chmod(0o644)

            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
            context = JobContext(target_root=tmpdir, selections={"filesystem": "btrfs"})

            result = job.run(context)

            assert result.success is True
            assert not disabled.exists()
            restored = Path(tmpdir) / "etc" / "grub.d" / "41_snapshots-btrfs"
            assert restored.exists()
            assert restored.read_text() == "#!/bin/sh\necho detecting\n"
            assert restored.stat().st_mode & 0o111, "must be executable again"

    def test_noop_when_no_disabled_script(self, tmp_path: Path) -> None:
        job = FinalizeJob()
        job._restore_snapshot_detection_script(str(tmp_path))  # must not raise

    def test_not_restored_for_non_btrfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            disabled = Path(tmpdir) / "etc" / "grub.d" / "41_snapshots-btrfs.xen-disabled"
            disabled.parent.mkdir(parents=True)
            disabled.write_text("#!/bin/sh\necho detecting\n")

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
                job = FinalizeJob({"commands": ["/usr/local/bin/vcheck"]})
                context = JobContext(target_root=tmpdir, selections={"filesystem": "ext4"})

                result = job.run(context)

            assert result.success is True
            assert disabled.exists()

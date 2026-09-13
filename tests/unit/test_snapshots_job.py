"""Unit tests for SnapshotsJob."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from xen.jobs.base import JobContext
from xen.jobs.snapshots import SnapshotsJob


class TestRun:
    def test_dry_run_skips(self) -> None:
        job = SnapshotsJob()
        context = JobContext(selections={"dry_run": True, "filesystem": "btrfs"})

        result = job.run(context)

        assert result.success is True

    def test_non_btrfs_skips(self) -> None:
        job = SnapshotsJob()
        context = JobContext(selections={"filesystem": "ext4"})

        result = job.run(context)

        assert result.success is True
        assert "Not Btrfs" in result.message

    @patch("subprocess.run")
    def test_full_run_writes_and_configures_everything(self, mock_run: Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target_root = Path(tmpdir) / "target"
            mkinitcpio_conf = target_root / "etc" / "mkinitcpio.conf"
            mkinitcpio_conf.parent.mkdir(parents=True)
            mkinitcpio_conf.write_text(
                "MODULES=()\nHOOKS=(base udev modconf block filesystems keyboard)\n"
            )
            detection_script = target_root / "etc" / "grub.d" / "41_snapshots-btrfs"
            detection_script.parent.mkdir(parents=True)
            detection_script.write_text("#!/bin/sh\necho detecting\n")

            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            job = SnapshotsJob()
            context = JobContext(
                target_root=str(target_root), selections={"filesystem": "btrfs"}
            )

            result = job.run(context)

            assert result.success is True

            template_path = (
                target_root / "etc" / "snapper" / "config-templates" / "xen-root"
            )
            assert template_path.exists()
            assert 'TIMELINE_CREATE="no"' in template_path.read_text()
            assert 'NUMBER_LIMIT="50"' in template_path.read_text()

            path_unit = target_root / "etc" / "systemd" / "system" / "grub-btrfs-snapper.path"
            service_unit = (
                target_root / "etc" / "systemd" / "system" / "grub-btrfs-snapper.service"
            )
            assert path_unit.exists()
            assert "PathModified=/.snapshots" in path_unit.read_text()
            assert service_unit.exists()
            assert "grub-mkconfig" in service_unit.read_text()

            calls = [c.args[0] for c in mock_run.call_args_list if c.args]
            assert [
                "arch-chroot",
                str(target_root),
                "snapper",
                "--no-dbus",
                "-c",
                "root",
                "create-config",
                "--template",
                "xen-root",
                "/",
            ] in calls
            assert [
                "arch-chroot",
                str(target_root),
                "systemctl",
                "enable",
                "snapper-cleanup.timer",
            ] in calls
            assert [
                "arch-chroot",
                str(target_root),
                "systemctl",
                "enable",
                "grub-btrfs-snapper.path",
            ] in calls
            assert ["arch-chroot", str(target_root), "mkinitcpio", "-P"] in calls

            assert not detection_script.exists()
            disabled_script = target_root / "etc" / "grub.d" / "41_snapshots-btrfs.xen-disabled"
            assert disabled_script.exists()

            hooks_content = mkinitcpio_conf.read_text()
            assert "grub-btrfs-overlayfs" in hooks_content


class TestCreateSnapperConfig:
    @patch("subprocess.run")
    def test_failure_reported(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="subvolume must exist")
        job = SnapshotsJob()

        result = job._create_snapper_config("/mnt")

        assert result.success is False
        assert result.error_code == 133

    @patch("subprocess.run")
    def test_timeout(self, mock_run: Mock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="snapper", timeout=30)
        job = SnapshotsJob()

        result = job._create_snapper_config("/mnt")

        assert result.success is False
        assert result.error_code == 131


class TestAddOverlayfsHook:
    @patch("subprocess.run")
    def test_appends_hook_and_rebuilds(self, mock_run: Mock, tmp_path: Path) -> None:
        conf_path = tmp_path / "etc" / "mkinitcpio.conf"
        conf_path.parent.mkdir(parents=True)
        conf_path.write_text("HOOKS=(base udev modconf block filesystems keyboard)\n")
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        job = SnapshotsJob()

        result = job._add_overlayfs_hook(str(tmp_path))

        assert result.success is True
        content = conf_path.read_text()
        assert (
            "HOOKS=(base udev modconf block filesystems keyboard grub-btrfs-overlayfs)"
            in content
        )

    def test_noop_when_hook_already_present(self, tmp_path: Path) -> None:
        conf_path = tmp_path / "etc" / "mkinitcpio.conf"
        conf_path.parent.mkdir(parents=True)
        conf_path.write_text(
            "HOOKS=(base udev modconf block filesystems keyboard grub-btrfs-overlayfs)\n"
        )
        job = SnapshotsJob()

        result = job._add_overlayfs_hook(str(tmp_path))

        assert result.success is True
        assert "already present" in result.message

    def test_fails_when_hooks_line_missing(self, tmp_path: Path) -> None:
        conf_path = tmp_path / "etc" / "mkinitcpio.conf"
        conf_path.parent.mkdir(parents=True)
        conf_path.write_text("MODULES=()\n")
        job = SnapshotsJob()

        result = job._add_overlayfs_hook(str(tmp_path))

        assert result.success is False
        assert result.error_code == 138

    @patch("subprocess.run")
    def test_mkinitcpio_failure_reported(self, mock_run: Mock, tmp_path: Path) -> None:
        conf_path = tmp_path / "etc" / "mkinitcpio.conf"
        conf_path.parent.mkdir(parents=True)
        conf_path.write_text("HOOKS=(base udev modconf block filesystems keyboard)\n")
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="boom")
        job = SnapshotsJob()

        result = job._add_overlayfs_hook(str(tmp_path))

        assert result.success is False
        assert result.error_code == 142


class TestDisableSnapshotDetectionScript:
    """grub-btrfs's own 41_snapshots-btrfs mounts the root filesystem via
    /dev/disk/by-uuid/<uuid> to enumerate existing snapshots - unreliable
    mid-install, and there are none to find yet anyway. Renamed out of
    the way (not deleted) so finalize.py can restore it once one exists.

    Renaming alone does not stop grub-mkconfig from running it (its file
    filter only skips *.dpkg-*, *.rpmsave, *.rpmnew, README*, *.sig) - the
    executable bit must also be stripped, since `test -x` is grub-mkconfig's
    real gate."""

    def test_renames_script_when_present(self, tmp_path: Path) -> None:
        script = tmp_path / "etc" / "grub.d" / "41_snapshots-btrfs"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\necho detecting\n")
        script.chmod(0o755)
        job = SnapshotsJob()

        job._disable_snapshot_detection_script(str(tmp_path))

        assert not script.exists()
        disabled = tmp_path / "etc" / "grub.d" / "41_snapshots-btrfs.xen-disabled"
        assert disabled.exists()
        assert disabled.read_text() == "#!/bin/sh\necho detecting\n"
        assert not (disabled.stat().st_mode & 0o111), "must not be executable"

    def test_noop_when_script_missing(self, tmp_path: Path) -> None:
        job = SnapshotsJob()
        job._disable_snapshot_detection_script(str(tmp_path))  # must not raise


class TestEstimateDuration:
    def test_estimate_duration(self) -> None:
        job = SnapshotsJob()
        assert job.estimate_duration() == 30

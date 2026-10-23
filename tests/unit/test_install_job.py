"""Unit tests for InstallJob."""

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from xen.jobs.base import JobContext, JobResult, JobStatus
    from xen.jobs.install import InstallJob

    HAS_INSTALL_JOB = True
except ImportError:
    HAS_INSTALL_JOB = False

# Skip entire module if xen is not available
pytestmark = pytest.mark.skipif(not HAS_INSTALL_JOB, reason="InstallJob not available")


class TestInstallJob:
    """Tests for InstallJob basic functionality."""

    def test_init_defaults(self) -> None:
        """InstallJob should have correct defaults."""
        job = InstallJob()
        assert job.name == "install"
        assert job.description == "System installation - copy files to target"
        assert job.status == JobStatus.PENDING
        assert job._source_size_bytes == 0
        assert job._bytes_copied == 0

    def test_estimate_duration_default(self) -> None:
        """estimate_duration should return default value without source size."""
        job = InstallJob()
        duration = job.estimate_duration()
        assert duration == 300  # 5 minutes

    def test_estimate_duration_with_size(self) -> None:
        """estimate_duration should adjust based on source size."""
        job = InstallJob()
        job._source_size_bytes = 10 * 1024 * 1024 * 1024  # 10 GB

        duration = job.estimate_duration()
        # 10 GB / 100 MB/s = 100 seconds * 1.5 = 150 seconds
        assert duration >= 60  # At least minimum
        assert duration > 100  # Should be adjusted


class TestConstants:
    """Tests for class constants."""

    def test_exclude_dirs(self) -> None:
        """EXCLUDE_DIRS should contain expected virtual filesystems."""
        job = InstallJob()
        assert "/proc" in job.EXCLUDE_DIRS
        assert "/sys" in job.EXCLUDE_DIRS
        assert "/dev" in job.EXCLUDE_DIRS
        assert "/run" in job.EXCLUDE_DIRS
        assert "/tmp" in job.EXCLUDE_DIRS

    def test_min_free_space(self) -> None:
        """MIN_FREE_SPACE should be 5 GB."""
        job = InstallJob()
        expected_bytes = 5 * 1024 * 1024 * 1024
        assert expected_bytes == job.MIN_FREE_SPACE


class TestValidate:
    @pytest.fixture(autouse=True)
    def _target_is_mounted(self) -> object:
        with patch("xen.jobs.install.os.path.ismount", return_value=True):
            yield

    @patch("xen.jobs.install.os.access", return_value=True)
    @patch("xen.jobs.install.shutil.which", return_value="/usr/bin/unsquashfs")
    def test_validate_rejects_unmounted_target(
        self, _which: MagicMock, _access: MagicMock, tmp_path: Path
    ) -> None:
        sfs = tmp_path / "airootfs.sfs"
        sfs.write_bytes(b"")
        context = JobContext(
            target_root=str(tmp_path),
            selections={"source_type": "squashfs", "squashfs_path": str(sfs)},
        )
        with patch("xen.jobs.install.os.path.ismount", return_value=False):
            result = InstallJob().validate(context)
        assert result.success is False
        assert "not mounted" in result.message
    """Tests for the validate() method."""

    def test_validate_invalid_source_type(self) -> None:
        """validate should fail for invalid source_type."""
        job = InstallJob()
        context = JobContext(
            selections={
                "source_type": "invalid",
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 51
        assert "Invalid source_type" in result.message

    @patch("xen.jobs.install.InstallJob._find_live_squashfs")
    def test_validate_squashfs_missing_path(self, mock_find: MagicMock) -> None:
        """validate should fail if squashfs_path not provided and none can
        be auto-detected."""
        mock_find.return_value = None
        job = InstallJob()
        context = JobContext(
            selections={
                "source_type": "squashfs",
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 50
        assert "auto-detected" in result.message

    @patch("xen.jobs.install.Path")
    def test_validate_squashfs_not_found(self, mock_path: MagicMock) -> None:
        """validate should fail if squashfs file doesn't exist."""
        job = InstallJob()

        mock_sfs_path = MagicMock()
        mock_sfs_path.exists.return_value = False
        mock_path.return_value = mock_sfs_path

        context = JobContext(
            selections={
                "source_type": "squashfs",
                "squashfs_path": "/nonexistent.sfs",
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 52
        assert "Squashfs file not found" in result.message

    @patch("xen.jobs.install.shutil.which")
    @patch("xen.jobs.install.Path")
    def test_validate_squashfs_missing_tool(
        self, mock_path: MagicMock, mock_which: MagicMock
    ) -> None:
        """validate should fail if unsquashfs tool not available."""
        job = InstallJob()

        mock_sfs_path = MagicMock()
        mock_sfs_path.exists.return_value = True
        mock_sfs_path.is_file.return_value = True
        mock_path.return_value = mock_sfs_path

        mock_which.return_value = None  # Tool not found

        context = JobContext(
            selections={
                "source_type": "squashfs",
                "squashfs_path": "/test.sfs",
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 54
        assert "unsquashfs tool not found" in result.message

    @patch("xen.jobs.install.Path")
    def test_validate_source_not_found(self, mock_path: MagicMock) -> None:
        """validate should fail if live source doesn't exist."""
        job = InstallJob()

        mock_source = MagicMock()
        mock_source.exists.return_value = False

        # Mock Path to return different objects for source and target
        def path_side_effect(p: str) -> MagicMock:
            if p == "/nonexistent":
                return mock_source
            # Return a valid target mock for other paths
            mock_target = MagicMock()
            mock_target.exists.return_value = True
            mock_target.is_dir.return_value = True
            mock_target.stat.return_value.st_mode = 0o755
            return mock_target

        mock_path.side_effect = path_side_effect

        context = JobContext(
            target_root="/mnt",
            selections={
                "source": "/nonexistent",
                "source_type": "live",
            },
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 55
        assert "Source directory not found" in result.message

    @patch("xen.jobs.install.Path")
    def test_validate_target_not_found(self, mock_path: MagicMock) -> None:
        """validate should fail if target directory doesn't exist."""
        job = InstallJob()

        mock_source = MagicMock()
        mock_source.exists.return_value = True
        mock_source.is_dir.return_value = True

        mock_target = MagicMock()
        mock_target.exists.return_value = False

        def path_side_effect(p: str) -> MagicMock:
            if p == "/":
                return mock_source
            return mock_target

        mock_path.side_effect = path_side_effect

        context = JobContext(
            target_root="/mnt",
            selections={
                "source": "/",
                "source_type": "live",
            },
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 57
        assert "Target directory not found" in result.message

    @patch("xen.jobs.install.os.access")
    @patch("xen.jobs.install.shutil.which")
    @patch("xen.jobs.install.Path")
    def test_validate_rsync_not_found(
        self, mock_path: MagicMock, mock_which: MagicMock, mock_access: MagicMock
    ) -> None:
        """validate should fail if rsync tool not available for live install."""
        job = InstallJob()

        # Mock valid source and target
        mock_source = MagicMock()
        mock_source.exists.return_value = True
        mock_source.is_dir.return_value = True

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.is_dir.return_value = True
        mock_target.stat.return_value.st_mode = 0o755

        def path_side_effect(p: str) -> MagicMock:
            if p == "/":
                return mock_source
            return mock_target

        mock_path.side_effect = path_side_effect
        mock_access.return_value = True

        # rsync not found, du found
        def which_side_effect(tool: str) -> str | None:
            if tool == "rsync":
                return None
            return "/usr/bin/du"

        mock_which.side_effect = which_side_effect

        context = JobContext(
            target_root="/mnt",
            selections={
                "source": "/",
                "source_type": "live",
            },
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 60
        assert "rsync tool not found" in result.message

    @patch("xen.jobs.install.os.access")
    @patch("xen.jobs.install.shutil.disk_usage")
    @patch("xen.jobs.install.shutil.which")
    @patch("xen.jobs.install.Path")
    def test_validate_insufficient_space(
        self,
        mock_path: MagicMock,
        mock_which: MagicMock,
        mock_disk_usage: MagicMock,
        mock_access: MagicMock,
    ) -> None:
        """validate should fail if insufficient disk space."""
        job = InstallJob()

        # Mock valid paths
        mock_source = MagicMock()
        mock_source.exists.return_value = True
        mock_source.is_dir.return_value = True

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.is_dir.return_value = True
        mock_target.stat.return_value.st_mode = 0o755

        def path_side_effect(p: str) -> MagicMock:
            if p == "/":
                return mock_source
            return mock_target

        mock_path.side_effect = path_side_effect
        mock_access.return_value = True

        # Tools available
        mock_which.return_value = "/usr/bin/rsync"

        # Only 2 GB free (less than MIN_FREE_SPACE of 5 GB)
        mock_disk_usage.return_value = MagicMock(free=2 * 1024**3)

        context = JobContext(
            target_root="/mnt",
            selections={
                "source": "/",
                "source_type": "live",
            },
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 62
        assert "Insufficient disk space" in result.message

    @patch("xen.jobs.install.os.access")
    @patch("xen.jobs.install.InstallJob._get_source_size")
    @patch("xen.jobs.install.shutil.disk_usage")
    @patch("xen.jobs.install.shutil.which")
    @patch("xen.jobs.install.Path")
    def test_validate_success(
        self,
        mock_path: MagicMock,
        mock_which: MagicMock,
        mock_disk_usage: MagicMock,
        mock_get_source_size: MagicMock,
        mock_access: MagicMock,
    ) -> None:
        """validate should succeed with valid configuration."""
        job = InstallJob()

        # Mock valid paths
        mock_source = MagicMock()
        mock_source.exists.return_value = True
        mock_source.is_dir.return_value = True

        mock_target = MagicMock()
        mock_target.exists.return_value = True
        mock_target.is_dir.return_value = True
        mock_target.stat.return_value.st_mode = 0o755

        def path_side_effect(p: str) -> MagicMock:
            if p == "/":
                return mock_source
            return mock_target

        mock_path.side_effect = path_side_effect
        mock_access.return_value = True

        # Tools available
        mock_which.return_value = "/usr/bin/rsync"

        # 100 GB free
        mock_disk_usage.return_value = MagicMock(free=100 * 1024**3)

        # Source size 10 GB
        mock_get_source_size.return_value = 10 * 1024**3

        context = JobContext(
            target_root="/mnt",
            selections={
                "source": "/",
                "source_type": "live",
            },
        )

        result = job.validate(context)

        assert result.success is True
        assert "validated" in result.message.lower()


class TestGetSourceSize:
    """Tests for _get_source_size() method."""

    @patch("xen.jobs.install.subprocess.run")
    def test_get_source_size_success(self, mock_subprocess: MagicMock) -> None:
        """_get_source_size should parse du output correctly."""
        job = InstallJob()

        mock_subprocess.return_value = MagicMock(
            stdout="12345678\t/source/path\n",
            returncode=0,
        )

        size = job._get_source_size("/source/path")

        assert size == 12345678
        assert job._source_size_bytes == 12345678

        # Verify du command was called with exclusions
        call_args = mock_subprocess.call_args
        assert "du" in call_args[0][0]
        assert "-sb" in call_args[0][0]
        assert "--exclude" in call_args[0][0]

    @patch("xen.jobs.install.subprocess.run")
    def test_get_source_size_with_exclusions(self, mock_subprocess: MagicMock) -> None:
        """_get_source_size should include exclusions in du command."""
        job = InstallJob()

        mock_subprocess.return_value = MagicMock(
            stdout="10000000\t/\n",
            returncode=0,
        )

        job._get_source_size("/")

        call_args = mock_subprocess.call_args[0][0]

        # Check exclusions are present
        assert "--exclude" in call_args
        assert "/proc" in call_args or any("/proc" in str(arg) for arg in call_args)

    @patch("xen.jobs.install.subprocess.run")
    def test_get_source_size_failure(self, mock_subprocess: MagicMock) -> None:
        """_get_source_size should raise CalledProcessError on failure."""
        job = InstallJob()

        mock_subprocess.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["du"],
        )

        with pytest.raises(subprocess.CalledProcessError):
            job._get_source_size("/source")


class TestRunRsync:
    """Tests for _run_rsync() method."""

    @patch("xen.jobs.install.InstallJob._get_source_size")
    @patch("xen.jobs.install.subprocess.Popen")
    def test_run_rsync_success(self, mock_popen: MagicMock, mock_get_size: MagicMock) -> None:
        """_run_rsync should execute rsync and parse progress."""
        job = InstallJob()

        mock_get_size.return_value = 10 * 1024**3  # 10 GB

        # Mock rsync progress output
        mock_process = MagicMock()
        mock_process.stdout = [
            "     1,234,567  10%   12.34MB/s    0:01:23\n",
            "     5,678,901  50%   15.67MB/s    0:00:45\n",
            "    10,000,000  100%  20.00MB/s   0:00:00\n",
        ]
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        context = JobContext(target_root="/mnt")
        result = job._run_rsync("/source/", "/mnt", context)

        assert result.success is True
        assert "copied successfully" in result.message.lower()
        assert job._bytes_copied == 10000000

        # Verify rsync command structure
        call_args = mock_popen.call_args[0][0]
        assert "rsync" in call_args
        assert "-aAXHv" in call_args
        assert "--info=progress2" in call_args

    @patch("xen.jobs.install.InstallJob._get_source_size")
    @patch("xen.jobs.install.subprocess.Popen")
    def test_run_rsync_with_exclusions(
        self, mock_popen: MagicMock, mock_get_size: MagicMock
    ) -> None:
        """_run_rsync should include exclusions in command."""
        job = InstallJob()

        mock_get_size.return_value = 0

        mock_process = MagicMock()
        mock_process.stdout = []
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        context = JobContext()
        job._run_rsync("/", "/mnt", context)

        call_args = mock_popen.call_args[0][0]

        # Check exclusions are present
        assert "--exclude" in call_args
        assert any("/proc" in str(arg) for arg in call_args)
        assert any("/sys" in str(arg) for arg in call_args)

    @patch("xen.jobs.install.InstallJob._get_source_size")
    @patch("xen.jobs.install.subprocess.Popen")
    def test_run_rsync_adds_trailing_slash(
        self, mock_popen: MagicMock, mock_get_size: MagicMock
    ) -> None:
        """_run_rsync should add trailing slash to source."""
        job = InstallJob()

        mock_get_size.return_value = 0

        mock_process = MagicMock()
        mock_process.stdout = []
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        context = JobContext()
        job._run_rsync("/source", "/mnt", context)

        call_args = mock_popen.call_args[0][0]

        # Source should have trailing slash
        assert "/source/" in call_args

    @patch("xen.jobs.install.InstallJob._get_source_size")
    @patch("xen.jobs.install.subprocess.Popen")
    def test_run_rsync_failure(self, mock_popen: MagicMock, mock_get_size: MagicMock) -> None:
        """_run_rsync should handle rsync failure."""
        job = InstallJob()

        mock_get_size.return_value = 0

        mock_process = MagicMock()
        mock_process.stdout = []
        mock_process.wait.return_value = 1  # Non-zero exit
        mock_popen.return_value = mock_process

        context = JobContext()
        result = job._run_rsync("/source/", "/mnt", context)

        assert result.success is False
        assert result.error_code == 64
        assert "rsync failed" in result.message

    @patch("xen.jobs.install.InstallJob._get_source_size")
    @patch("xen.jobs.install.subprocess.Popen")
    def test_run_rsync_progress_tracking(
        self, mock_popen: MagicMock, mock_get_size: MagicMock
    ) -> None:
        """_run_rsync should track and report progress correctly."""
        job = InstallJob()

        mock_get_size.return_value = 0

        # Mock progress updates
        mock_process = MagicMock()
        mock_process.stdout = [
            "     1,000,000  25%   10.00MB/s    0:00:30\n",
            "     2,000,000  50%   10.00MB/s    0:00:15\n",
            "     3,000,000  75%   10.00MB/s    0:00:05\n",
        ]
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        progress_calls: list[tuple[int, str]] = []

        def track_progress(percent: int, message: str) -> None:
            progress_calls.append((percent, message))

        context = JobContext(on_progress=track_progress)
        result = job._run_rsync("/source/", "/mnt", context)

        assert result.success is True

        # Should have reported progress
        assert len(progress_calls) > 0

        # Progress should be scaled to 5-90% range
        percentages = [p[0] for p in progress_calls]
        assert all(5 <= p <= 90 for p in percentages)

    @patch("xen.jobs.install.InstallJob._get_source_size")
    @patch("xen.jobs.install.subprocess.Popen")
    def test_run_rsync_message_percent_matches_reported_percent(
        self, mock_popen: MagicMock, mock_get_size: MagicMock
    ) -> None:
        """Regression: the "Copying files... X%" message used rsync's raw
        (unscaled) percent while the actual reported/bar percent was scaled
        to the job's 5-90% range, so the two numbers disagreed (e.g. message
        said "99%" while the bar showed "89%")."""
        job = InstallJob()

        mock_get_size.return_value = 0

        mock_process = MagicMock()
        mock_process.stdout = ["     1,000,000  50%   10.00MB/s    0:00:15\n"]
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        progress_calls: list[tuple[int, str]] = []
        context = JobContext(
            on_progress=lambda percent, message: progress_calls.append((percent, message))
        )

        job._run_rsync("/source/", "/mnt", context)

        percent, message = next(c for c in progress_calls if c[1].startswith("Copying files"))
        assert f"{percent}%" in message


class TestEstimateEta:
    def test_zero_percent_returns_zero(self) -> None:
        assert InstallJob._estimate_eta(time.monotonic(), 0) == "0:00:00"

    def test_scales_from_elapsed_time_and_percent(self) -> None:
        # 10 seconds elapsed at 50% done -> another ~10 seconds remaining
        start = time.monotonic() - 10
        eta = InstallJob._estimate_eta(start, 50)
        assert eta in ("0:00:09", "0:00:10", "0:00:11")  # allow tiny timing slack

    def test_stays_realistic_near_completion_unlike_instantaneous_eta(self) -> None:
        # Regression: 12GB copied steadily over 600s, now at 99% - the
        # remaining 1% should read as a few seconds, not minutes (which is
        # what rsync's own instantaneous-throughput ETA showed when the
        # tail of the transfer was thousands of tiny files).
        start = time.monotonic() - 600
        eta = InstallJob._estimate_eta(start, 99)
        hours, minutes, seconds = (int(x) for x in eta.split(":"))
        total_seconds = hours * 3600 + minutes * 60 + seconds
        assert total_seconds < 15


class TestFindLiveSquashfs:
    """Auto-detection of the live ISO's own squashfs image.

    Switched to as the default install source (from rsync-copying the
    running live session's "/") because the live overlay's tmpfs upper
    layer picks up session-only content the rsync then copied to the
    target - live user credentials, browser data, session-only mkinitcpio
    state - none of which exist in the pristine build-time squashfs image.
    """

    def test_finds_image_at_standard_archiso_path(self, tmp_path: Path) -> None:
        bootmnt = tmp_path / "run" / "archiso" / "bootmnt" / "XERO_202609" / "x86_64"
        bootmnt.mkdir(parents=True)
        (bootmnt / "airootfs.sfs").write_bytes(b"fake squashfs")

        with patch("xen.jobs.install.Path") as mock_path_cls:
            mock_path_cls.return_value = tmp_path
            found = InstallJob._find_live_squashfs()

        assert found == str(bootmnt / "airootfs.sfs")

    def test_returns_none_when_not_mounted(self, tmp_path: Path) -> None:
        with patch("xen.jobs.install.Path") as mock_path_cls:
            mock_path_cls.return_value = tmp_path
            found = InstallJob._find_live_squashfs()

        assert found is None


class TestResolveSquashfsPath:
    def test_explicit_selection_wins_over_auto_detect(self) -> None:
        job = InstallJob()
        context = JobContext(selections={"squashfs_path": "/explicit.sfs"})

        with patch.object(InstallJob, "_find_live_squashfs") as mock_find:
            result = job._resolve_squashfs_path(context)

        mock_find.assert_not_called()
        assert result == "/explicit.sfs"

    @patch("xen.jobs.install.InstallJob._find_live_squashfs")
    def test_falls_back_to_auto_detection(self, mock_find: MagicMock) -> None:
        mock_find.return_value = "/run/archiso/bootmnt/XERO/x86_64/airootfs.sfs"
        job = InstallJob()
        context = JobContext(selections={})

        result = job._resolve_squashfs_path(context)

        assert result == "/run/archiso/bootmnt/XERO/x86_64/airootfs.sfs"

    @patch("xen.jobs.install.InstallJob._find_live_squashfs")
    def test_caches_detected_path_onto_selections(self, mock_find: MagicMock) -> None:
        mock_find.return_value = "/run/archiso/bootmnt/XERO/x86_64/airootfs.sfs"
        job = InstallJob()
        context = JobContext(selections={})

        job._resolve_squashfs_path(context)

        assert context.selections["squashfs_path"] == (
            "/run/archiso/bootmnt/XERO/x86_64/airootfs.sfs"
        )

    @patch("xen.jobs.install.InstallJob._find_live_squashfs")
    def test_returns_none_when_nothing_found(self, mock_find: MagicMock) -> None:
        mock_find.return_value = None
        job = InstallJob()
        context = JobContext(selections={})

        result = job._resolve_squashfs_path(context)

        assert result is None
        assert "squashfs_path" not in context.selections


class TestExtractSquashfs:
    """Tests for _extract_squashfs() method."""

    @patch("xen.jobs.install.subprocess.Popen")
    def test_extract_squashfs_success(self, mock_popen: MagicMock) -> None:
        """_extract_squashfs should execute unsquashfs successfully."""
        job = InstallJob()

        mock_process = MagicMock()
        mock_process.stdout = [
            "Parallel unsquashfs: Using 4 processors\n",
            "1234 inodes (5678 blocks) to write\n",
        ]
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        context = JobContext()
        result = job._extract_squashfs("/test.sfs", "/mnt", context)

        assert result.success is True
        assert "extracted successfully" in result.message.lower()

        # Verify command structure
        call_args = mock_popen.call_args[0][0]
        assert "unsquashfs" in call_args
        assert "-f" in call_args  # Force overwrite
        assert "-d" in call_args  # Destination
        assert "/mnt" in call_args
        assert "/test.sfs" in call_args

    @patch("xen.jobs.install.subprocess.Popen")
    def test_extract_squashfs_failure(self, mock_popen: MagicMock) -> None:
        """_extract_squashfs should handle unsquashfs failure."""
        job = InstallJob()

        mock_process = MagicMock()
        mock_process.stdout = []
        mock_process.wait.return_value = 1  # Non-zero exit
        mock_popen.return_value = mock_process

        context = JobContext()
        result = job._extract_squashfs("/test.sfs", "/mnt", context)

        assert result.success is False
        assert result.error_code == 67
        assert "unsquashfs failed" in result.message

    @patch("xen.jobs.install.subprocess.Popen")
    def test_extract_squashfs_tool_not_found(self, mock_popen: MagicMock) -> None:
        """_extract_squashfs should handle missing unsquashfs tool."""
        job = InstallJob()

        mock_popen.side_effect = FileNotFoundError()

        context = JobContext()
        result = job._extract_squashfs("/test.sfs", "/mnt", context)

        assert result.success is False
        assert result.error_code == 54
        assert "unsquashfs command not found" in result.message


class TestEnsurePseudoMountpoints:
    """Tests for _ensure_pseudo_mountpoints (regression: arch-chroot failing
    to bind-mount /proc because rsync's EXCLUDE_DIRS never recreates the
    excluded pseudo-filesystem directories on the target)."""

    def test_creates_all_excluded_pseudo_dirs(self, tmp_path: Path) -> None:
        job = InstallJob()

        result = job._ensure_pseudo_mountpoints(str(tmp_path))

        assert result.success is True
        for name in ("proc", "sys", "dev", "run", "tmp", "mnt", "media"):
            assert (tmp_path / name).is_dir()

    def test_sets_expected_modes(self, tmp_path: Path) -> None:
        job = InstallJob()

        job._ensure_pseudo_mountpoints(str(tmp_path))

        assert (tmp_path / "proc").stat().st_mode & 0o777 == 0o555
        assert (tmp_path / "dev").stat().st_mode & 0o777 == 0o755
        assert (tmp_path / "tmp").stat().st_mode & 0o1777 == 0o1777

    def test_idempotent_when_dirs_already_exist(self, tmp_path: Path) -> None:
        job = InstallJob()
        (tmp_path / "proc").mkdir()

        result = job._ensure_pseudo_mountpoints(str(tmp_path))

        assert result.success is True
        assert (tmp_path / "proc").is_dir()

    def test_fails_on_oserror(self, tmp_path: Path) -> None:
        job = InstallJob()

        with patch("pathlib.Path.mkdir", side_effect=OSError("no space left")):
            result = job._ensure_pseudo_mountpoints(str(tmp_path))

        assert result.success is False
        assert result.error_code == 70


class TestRun:
    """Tests for the run() method integration."""

    @patch("xen.jobs.install.InstallJob._ensure_pseudo_mountpoints")
    @patch("xen.jobs.install.InstallJob._run_rsync")
    def test_run_live_install_success(
        self, mock_rsync: MagicMock, mock_mountpoints: MagicMock
    ) -> None:
        """run should execute live install successfully."""
        job = InstallJob()

        mock_rsync.return_value = JobResult.ok("Files copied", data={"bytes_copied": 1000000})
        mock_mountpoints.return_value = JobResult.ok("Mountpoints created")

        context = JobContext(
            target_root="/mnt",
            selections={
                "source": "/",
                "source_type": "live",
            },
        )

        result = job.run(context)

        assert result.success is True
        assert "copied successfully" in result.message.lower()
        assert result.data["source"] == "/"
        assert result.data["target"] == "/mnt"

        mock_rsync.assert_called_once()

    @patch("xen.jobs.install.InstallJob._ensure_pseudo_mountpoints")
    @patch("xen.jobs.install.InstallJob._extract_squashfs")
    def test_run_squashfs_install(
        self, mock_extract: MagicMock, mock_mountpoints: MagicMock
    ) -> None:
        """run should handle squashfs installation."""
        job = InstallJob()

        mock_extract.return_value = JobResult.ok("Extracted")
        mock_mountpoints.return_value = JobResult.ok("Mountpoints created")

        context = JobContext(
            target_root="/mnt",
            selections={
                "source_type": "squashfs",
                "squashfs_path": "/test.sfs",
            },
        )

        result = job.run(context)

        assert result.success is True

        mock_extract.assert_called_once_with("/test.sfs", "/mnt", context)

    @patch("xen.jobs.install.InstallJob._find_live_squashfs")
    @patch("xen.jobs.install.InstallJob._extract_squashfs")
    def test_run_squashfs_missing_path(
        self, mock_extract: MagicMock, mock_find: MagicMock
    ) -> None:
        """run should fail if squashfs_path not provided and none can be
        auto-detected."""
        mock_find.return_value = None
        job = InstallJob()

        context = JobContext(
            selections={
                "source_type": "squashfs",
                # Missing squashfs_path
            }
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 50

        mock_extract.assert_not_called()

    @patch("xen.jobs.install.InstallJob._run_rsync")
    def test_run_propagates_errors(self, mock_rsync: MagicMock) -> None:
        """run should propagate errors from rsync."""
        job = InstallJob()

        mock_rsync.return_value = JobResult.fail("rsync failed", error_code=64)

        context = JobContext(
            selections={
                "source": "/",
                "source_type": "live",
            }
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 64

class TestCleanup:
    """Tests for cleanup() method."""

    def test_cleanup_no_action(self) -> None:
        """cleanup should complete without errors."""
        job = InstallJob()
        context = JobContext()

        # Should not raise exception
        job.cleanup(context)

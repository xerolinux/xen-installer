"""Unit tests for BootloaderJob."""

import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

try:
    from xen.jobs.base import JobContext, JobResult, JobStatus
    from xen.jobs.bootloader import BootloaderJob

    HAS_BOOTLOADER_JOB = True
except ImportError:
    HAS_BOOTLOADER_JOB = False

# Skip entire module if xen is not available
pytestmark = pytest.mark.skipif(not HAS_BOOTLOADER_JOB, reason="BootloaderJob not available")


# =============================================================================
# BootloaderJob Initialization Tests
# =============================================================================


class TestBootloaderJobInit:
    """Tests for BootloaderJob initialization."""

    def test_init_defaults(self) -> None:
        """BootloaderJob should have correct defaults."""
        job = BootloaderJob()
        assert job.name == "bootloader"
        assert job.description == "System bootloader installation"
        assert job.status == JobStatus.PENDING

    def test_init_with_config(self) -> None:
        """BootloaderJob should accept configuration."""
        config = {"timeout": 5}
        job = BootloaderJob(config)
        assert job._config == config

    def test_bootloader_constants(self) -> None:
        """BootloaderJob should define bootloader constants."""
        assert BootloaderJob.SYSTEMD_BOOT == "systemd-boot"
        assert BootloaderJob.GRUB == "grub"

    def test_estimate_duration(self) -> None:
        """estimate_duration should return reasonable value."""
        job = BootloaderJob()
        duration = job.estimate_duration()
        assert duration == 60


# =============================================================================
# EFI System Validation Tests
# =============================================================================


class TestValidateEfiSystem:
    """Tests for EFI system validation."""

    @patch("xen.jobs.bootloader.Path")
    def test_validate_efi_system_no_efivars(self, mock_path: MagicMock) -> None:
        """Should fail if system not booted in UEFI mode."""
        job = BootloaderJob()

        # Mock efivars path not existing
        mock_efivars = MagicMock()
        mock_efivars.exists.return_value = False

        def path_side_effect(path_str: str) -> MagicMock:
            if "efivars" in str(path_str):
                return mock_efivars
            mock_other = MagicMock()
            mock_other.exists.return_value = False
            return mock_other

        mock_path.side_effect = path_side_effect

        context = JobContext(target_root="/mnt")
        result = job._validate_efi_system(context)

        assert result.success is False
        assert result.error_code == 50
        assert "UEFI mode" in result.message

    def test_validate_efi_system_efi_mounted(self) -> None:
        """Should succeed if EFI partition is mounted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create EFI mount point
            efi_mount = Path(tmpdir) / "boot" / "efi"
            efi_mount.mkdir(parents=True, exist_ok=True)

            job = BootloaderJob()

            with patch("xen.jobs.bootloader.Path") as mock_path:
                # Mock efivars exists
                mock_efivars = MagicMock()
                mock_efivars.exists.return_value = True

                def path_side_effect(path_str: str) -> MagicMock:
                    if "efivars" in str(path_str):
                        return mock_efivars
                    if "boot/efi" in str(path_str):
                        mock_efi = MagicMock()
                        mock_efi.exists.return_value = True
                        mock_efi.__truediv__ = lambda *_: Path(tmpdir) / "boot" / "efi"
                        return efi_mount
                    mock_other = MagicMock()
                    mock_other.exists.return_value = False
                    mock_other.__truediv__ = lambda *_: MagicMock()
                    return mock_other

                mock_path.side_effect = path_side_effect

                context = JobContext(target_root=tmpdir)
                result = job._validate_efi_system(context)

                assert result.success is True
                assert job._efi_mount is not None

    @patch("xen.jobs.bootloader.Path")
    def test_validate_efi_system_no_efi_mount(self, mock_path: MagicMock) -> None:
        """Should fail if EFI partition not mounted."""
        job = BootloaderJob()

        # Mock efivars exists but no EFI mount points
        mock_efivars = MagicMock()
        mock_efivars.exists.return_value = True

        # Create mock for target_root path
        mock_target = MagicMock()

        # Create mock for each possible EFI mount that doesn't exist
        mock_efi_mount = MagicMock()
        mock_efi_mount.exists.return_value = False

        # Mock __truediv__ to return mock_efi_mount for path divisions
        mock_target.__truediv__.return_value = mock_efi_mount
        mock_efi_mount.__truediv__.return_value = mock_efi_mount

        def path_side_effect(path_str: str) -> MagicMock:
            if "efivars" in str(path_str):
                return mock_efivars
            return mock_target

        mock_path.side_effect = path_side_effect

        context = JobContext(target_root="/mnt")
        result = job._validate_efi_system(context)

        assert result.success is False
        assert result.error_code == 51
        assert "not mounted" in result.message


# =============================================================================
# Kernel Detection Tests
# =============================================================================


class TestDetectKernels:
    """Tests for kernel detection."""

    def test_detect_kernels_success(self) -> None:
        """Should detect kernels in boot directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)

            # Create mock kernel files
            (boot_dir / "vmlinuz-6.1.0").touch()
            (boot_dir / "vmlinuz-6.1.1").touch()
            (boot_dir / "initramfs-6.1.0.img").touch()
            (boot_dir / "initramfs-6.1.1.img").touch()

            job = BootloaderJob()
            context = JobContext(target_root=tmpdir)

            result = job._detect_kernels(context)

            assert result.success is True
            assert len(job._kernels) == 2
            assert "vmlinuz-6.1.0" in job._kernels
            assert "vmlinuz-6.1.1" in job._kernels

    def test_detect_kernels_no_boot_dir(self) -> None:
        """Should fail if boot directory doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            context = JobContext(target_root=tmpdir)

            result = job._detect_kernels(context)

            assert result.success is False
            assert result.error_code == 53
            assert "Boot directory not found" in result.message

    def test_detect_kernels_no_kernels(self) -> None:
        """Should fail if no kernel images found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)

            job = BootloaderJob()
            context = JobContext(target_root=tmpdir)

            result = job._detect_kernels(context)

            assert result.success is False
            assert result.error_code == 54
            assert "No kernel images found" in result.message

    def test_detect_kernels_missing_initramfs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Should warn if initramfs missing but not fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)

            # Create kernel without initramfs
            (boot_dir / "vmlinuz-6.1.0").touch()

            job = BootloaderJob()
            context = JobContext(target_root=tmpdir)

            result = job._detect_kernels(context)

            assert result.success is True
            assert len(job._kernels) == 1

            # Check warning was logged
            warnings = [rec.message for rec in caplog.records if rec.levelname == "WARNING"]
            assert any("Missing initramfs" in msg for msg in warnings)


# =============================================================================
# systemd-boot Installation Tests
# =============================================================================


class TestInstallSystemdBoot:
    """Tests for systemd-boot installation."""

    @patch("xen.jobs.bootloader.BootloaderJob._create_systemd_boot_entries")
    @patch("xen.jobs.bootloader.BootloaderJob._configure_systemd_boot_loader")
    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_systemd_boot_success(
        self,
        mock_subprocess: MagicMock,
        mock_configure: MagicMock,
        mock_entries: MagicMock,
    ) -> None:
        """Should install systemd-boot successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            mock_subprocess.return_value = MagicMock(returncode=0, stdout="Success")
            mock_configure.return_value = JobResult.ok()
            mock_entries.return_value = JobResult.ok()

            context = JobContext(target_root=tmpdir)
            result = job._install_systemd_boot(context)

            assert result.success is True
            assert "systemd-boot installed" in result.message

            # Verify bootctl was called
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args[0][0]
            assert "bootctl" in call_args
            assert "install" in call_args

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_systemd_boot_no_efi_mount(self, _mock_subprocess: MagicMock) -> None:
        """Should fail if EFI mount not initialized."""
        job = BootloaderJob()
        job._efi_mount = None

        context = JobContext(target_root="/mnt")
        result = job._install_systemd_boot(context)

        assert result.success is False
        assert result.error_code == 55

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_systemd_boot_command_fails(self, mock_subprocess: MagicMock) -> None:
        """Should handle bootctl failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            mock_subprocess.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["bootctl"], stderr="Installation failed"
            )

            context = JobContext(target_root=tmpdir)
            result = job._install_systemd_boot(context)

            assert result.success is False
            assert result.error_code == 56
            assert "Failed to install systemd-boot" in result.message

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_systemd_boot_tool_not_found(self, mock_subprocess: MagicMock) -> None:
        """Should handle missing bootctl tool."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            mock_subprocess.side_effect = FileNotFoundError()

            context = JobContext(target_root=tmpdir)
            result = job._install_systemd_boot(context)

            assert result.success is False
            assert result.error_code == 57


# =============================================================================
# systemd-boot Configuration Tests
# =============================================================================


class TestConfigureSystemdBootLoader:
    """Tests for systemd-boot loader configuration."""

    def test_configure_loader_default_timeout(self) -> None:
        """Should create loader.conf with default timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            context = JobContext(target_root=tmpdir)
            result = job._configure_systemd_boot_loader(context)

            assert result.success is True

            loader_conf = job._efi_mount / "loader" / "loader.conf"
            assert loader_conf.exists()

            content = loader_conf.read_text()
            assert "timeout 3" in content
            assert "default arch.conf" in content
            assert "editor no" in content

    def test_configure_loader_custom_timeout(self) -> None:
        """Should respect custom timeout from config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob(config={"timeout": 10})
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            context = JobContext(target_root=tmpdir)
            result = job._configure_systemd_boot_loader(context)

            assert result.success is True

            loader_conf = job._efi_mount / "loader" / "loader.conf"
            content = loader_conf.read_text()
            assert "timeout 10" in content

    def test_configure_loader_no_efi_mount(self) -> None:
        """Should fail if EFI mount not initialized."""
        job = BootloaderJob()
        job._efi_mount = None

        context = JobContext(target_root="/mnt")
        result = job._configure_systemd_boot_loader(context)

        assert result.success is False
        assert result.error_code == 58


# =============================================================================
# systemd-boot Entries Tests
# =============================================================================


class TestCreateSystemdBootEntries:
    """Tests for systemd-boot boot entry creation."""

    @patch("xen.jobs.bootloader.BootloaderJob._get_root_partition_uuid")
    def test_create_entries_single_kernel(self, mock_uuid: MagicMock) -> None:
        """Should create boot entry for single kernel."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup boot directory with kernel and initramfs
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)
            (boot_dir / "vmlinuz-6.1.0").touch()
            (boot_dir / "initramfs-6.1.0.img").touch()

            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            job._kernels = ["vmlinuz-6.1.0"]

            mock_uuid.return_value = "1234-5678"

            context = JobContext(target_root=tmpdir)
            result = job._create_systemd_boot_entries(context)

            assert result.success is True

            # Verify entry was created
            entries_dir = job._efi_mount / "loader" / "entries"
            entry_file = entries_dir / "arch-6.1.0.conf"
            assert entry_file.exists()

            content = entry_file.read_text()
            assert "linux   /vmlinuz-6.1.0" in content
            assert "initrd  /initramfs-6.1.0.img" in content
            assert "root=UUID=1234-5678" in content
            assert "quiet splash" in content

    @patch("xen.jobs.bootloader.BootloaderJob._get_root_partition_uuid")
    def test_create_entries_multiple_kernels(self, mock_uuid: MagicMock) -> None:
        """Should create entries for multiple kernels."""
        with tempfile.TemporaryDirectory() as tmpdir:
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)
            (boot_dir / "vmlinuz-6.1.0").touch()
            (boot_dir / "vmlinuz-6.1.1").touch()
            (boot_dir / "initramfs-6.1.0.img").touch()
            (boot_dir / "initramfs-6.1.1.img").touch()

            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            job._kernels = ["vmlinuz-6.1.0", "vmlinuz-6.1.1"]

            mock_uuid.return_value = "1234-5678"

            context = JobContext(target_root=tmpdir)
            result = job._create_systemd_boot_entries(context)

            assert result.success is True

            # Verify both entries were created
            entries_dir = job._efi_mount / "loader" / "entries"
            assert (entries_dir / "arch-6.1.0.conf").exists()
            assert (entries_dir / "arch-6.1.1.conf").exists()

            # Verify default symlink points to latest
            default_link = entries_dir / "arch.conf"
            assert default_link.is_symlink()
            assert default_link.resolve().name == "arch-6.1.1.conf"

    @patch("xen.jobs.bootloader.BootloaderJob._get_root_partition_uuid")
    def test_create_entries_custom_kernel_params(self, mock_uuid: MagicMock) -> None:
        """Should use custom kernel parameters if provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)
            (boot_dir / "vmlinuz-6.1.0").touch()
            (boot_dir / "initramfs-6.1.0.img").touch()

            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            job._kernels = ["vmlinuz-6.1.0"]

            mock_uuid.return_value = "1234-5678"

            context = JobContext(
                target_root=tmpdir,
                selections={"kernel_params": "quiet loglevel=3 nowatchdog"},
            )
            result = job._create_systemd_boot_entries(context)

            assert result.success is True

            entry_file = job._efi_mount / "loader" / "entries" / "arch-6.1.0.conf"
            content = entry_file.read_text()
            assert "quiet loglevel=3 nowatchdog" in content

    @patch("xen.jobs.bootloader.BootloaderJob._blkid_uuid")
    @patch("xen.jobs.bootloader.BootloaderJob._get_root_partition_uuid")
    def test_create_entries_adds_cryptkey_when_keyfile_present(
        self, mock_root_uuid: MagicMock, mock_blkid: MagicMock
    ) -> None:
        """Mirrors bootloader.py's GRUB path: LuksBootKeyfileJob's recorded
        keyfile path must also reach the systemd-boot options line as
        cryptkey=, so the kernel's "encrypt" hook can auto-unlock with it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)
            (boot_dir / "vmlinuz-6.1.0").touch()
            (boot_dir / "initramfs-6.1.0.img").touch()

            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            job._kernels = ["vmlinuz-6.1.0"]

            mock_root_uuid.return_value = "1234-5678"
            mock_blkid.return_value = "abe5431a-d024-485c-9bd0-bd146e2fac1f"

            context = JobContext(
                target_root=tmpdir,
                selections={
                    "encryption": True,
                    "root_partition": "/dev/vda2",
                    "luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key",
                },
            )
            result = job._create_systemd_boot_entries(context)

            assert result.success is True
            entry_file = job._efi_mount / "loader" / "entries" / "arch-6.1.0.conf"
            content = entry_file.read_text()
            assert "cryptdevice=UUID=abe5431a-d024-485c-9bd0-bd146e2fac1f:cryptroot" in content
            assert "cryptkey=rootfs:/etc/cryptsetup-keys.d/root.key" in content

    @patch("xen.jobs.bootloader.BootloaderJob._get_root_partition_uuid")
    def test_create_entries_no_root_uuid(self, mock_uuid: MagicMock) -> None:
        """Should fail if cannot determine root UUID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            job._kernels = ["vmlinuz-6.1.0"]

            mock_uuid.return_value = None

            context = JobContext(target_root=tmpdir)
            result = job._create_systemd_boot_entries(context)

            assert result.success is False
            assert result.error_code == 61


# =============================================================================
# GRUB Installation Tests
# =============================================================================


class TestInstallGrub:
    """Tests for GRUB installation."""

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_success(self, mock_subprocess: MagicMock) -> None:
        """Should install GRUB successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            mock_subprocess.return_value = MagicMock(returncode=0, stdout="Success")

            context = JobContext(target_root=tmpdir)
            result = job._install_grub(context)

            assert result.success is True
            assert "GRUB installed" in result.message

            # Verify grub-install (primary + EFI fallback) and grub-mkconfig were called
            assert mock_subprocess.call_count == 3
            calls = [str(call) for call in mock_subprocess.call_args_list]
            assert sum("grub-install" in call for call in calls) == 2
            assert any("grub-mkconfig" in call for call in calls)
            assert any("--bootloader-id=XeroLinux" in call for call in calls)
            assert any("--removable" in call for call in calls)

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_no_efi_mount(self, _mock_subprocess: MagicMock) -> None:
        """Should fail if EFI mount not initialized."""
        job = BootloaderJob()
        job._efi_mount = None

        context = JobContext(target_root="/mnt")
        result = job._install_grub(context)

        assert result.success is False
        assert result.error_code == 63

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_enables_cryptodisk_for_manual_mode_encrypted_root(
        self, mock_subprocess: MagicMock
    ) -> None:
        """GRUB_ENABLE_CRYPTODISK must get set for a manual-mode encrypted
        root the same way it does for AUTO mode's single encryption
        selection - without it, grub-install omits the cryptodisk/luks2
        modules and grub-mkconfig won't detect the LUKS device."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            mock_subprocess.return_value = MagicMock(returncode=0, stdout="Success")

            context = JobContext(
                target_root=tmpdir,
                selections={
                    "partition_mode": "manual",
                    "partition_assignments": [
                        {
                            "path": "/dev/mapper/cryptroot",
                            "mountpoint": "/",
                            "raw_path": "/dev/sda2",
                        },
                    ],
                },
            )
            result = job._install_grub(context)

            assert result.success is True
            content = (Path(tmpdir) / "etc" / "default" / "grub").read_text()
            assert "GRUB_ENABLE_CRYPTODISK=y" in content

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_install_fails(self, mock_subprocess: MagicMock) -> None:
        """Should handle grub-install failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            mock_subprocess.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["grub-install"], stderr="Installation failed"
            )

            context = JobContext(target_root=tmpdir)
            result = job._install_grub(context)

            assert result.success is False
            assert result.error_code == 64

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_mkconfig_fails(self, mock_subprocess: MagicMock) -> None:
        """Should handle grub-mkconfig failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            # First call (grub-install) succeeds, second (grub-mkconfig) fails
            def run_side_effect(*args: Any, **_kwargs: Any) -> MagicMock:
                cmd = args[0]
                if "grub-install" in cmd:
                    return MagicMock(returncode=0, stdout="Success")
                if "grub-mkconfig" in cmd:
                    raise subprocess.CalledProcessError(
                        returncode=1, cmd=cmd, stderr="Config generation failed"
                    )
                return MagicMock()

            mock_subprocess.side_effect = run_side_effect

            context = JobContext(target_root=tmpdir)
            result = job._install_grub(context)

            assert result.success is False
            assert result.error_code == 66

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_strips_script_load_env_but_keeps_grubenv_for_btrfs(
        self, mock_subprocess: MagicMock
    ) -> None:
        """00_header's unconditional `if [ -s $prefix/grubenv ]; then
        load_env; fi` must be stripped from the SCRIPT (it reads the file
        every boot regardless of GRUB_DEFAULT/GRUB_SAVEDEFAULT, and fails
        with "sparse file not allowed" on Btrfs's CoW layout). But the
        FILE itself must survive: grub-install's own compiled binary bakes
        a separate load_env attempt into the core EFI image's embedded
        bootstrap config (confirmed via `strings grub-install`), run
        before grub.cfg is even read - deleting the file just changes that
        unrelated attempt's failure from a sparse-file error into a
        `fs/btrfs.c:find_path: file not found` one instead."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            grubenv = Path(tmpdir) / "boot" / "grub" / "grubenv"
            grubenv.parent.mkdir(parents=True)
            grubenv.write_text("# GRUB Environment Block\n")
            header_path = Path(tmpdir) / "etc" / "grub.d" / "00_header"
            header_path.parent.mkdir(parents=True)
            header_path.write_text(_REAL_00_HEADER_EXCERPT, encoding="utf-8")

            mock_subprocess.return_value = MagicMock(returncode=0, stdout="Success")

            context = JobContext(target_root=tmpdir, selections={"filesystem": "btrfs"})
            result = job._install_grub(context)

            assert result.success is True
            assert grubenv.exists(), "grubenv must survive - grub-install's own embedded load_env needs it"
            assert "load_env" not in header_path.read_text()

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_keeps_grubenv_for_non_btrfs(self, mock_subprocess: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            grubenv = Path(tmpdir) / "boot" / "grub" / "grubenv"
            grubenv.parent.mkdir(parents=True)
            grubenv.write_text("# GRUB Environment Block\n")

            mock_subprocess.return_value = MagicMock(returncode=0, stdout="Success")

            context = JobContext(target_root=tmpdir, selections={"filesystem": "ext4"})
            result = job._install_grub(context)

            assert result.success is True
            assert grubenv.exists()

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_install_grub_disables_efi_bootnext_before_mkconfig(
        self, mock_subprocess: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)
            bootnext = Path(tmpdir) / "etc" / "grub.d" / "31_efi_bootnext"
            bootnext.parent.mkdir(parents=True)
            bootnext.write_text("#! /bin/sh\n", encoding="utf-8")
            bootnext.chmod(0o755)

            mock_subprocess.return_value = MagicMock(returncode=0, stdout="Success")

            context = JobContext(target_root=tmpdir, selections={"filesystem": "ext4"})
            result = job._install_grub(context)

            assert result.success is True
            assert not (bootnext.stat().st_mode & 0o111)


# Trimmed but byte-exact excerpt of the real, unmodified /etc/grub.d/00_header
# shipped by Arch's grub package (confirmed via a real local install) - the
# distinctive part that _disable_grubenv_loading must remove, with enough
# real surrounding script to prove nothing else gets damaged.
_REAL_00_HEADER_EXCERPT = (
    'if [ "x${GRUB_DEFAULT_BUTTON}" = "x" ] ; then GRUB_DEFAULT_BUTTON="$GRUB_DEFAULT" ; fi\n'
    "\n"
    "cat << EOF\n"
    "if [ -s \\$prefix/grubenv ]; then\n"
    "  load_env\n"
    "fi\n"
    "\n"
    'if [ "\\${env_block}" ] ; then\n'
    '  set env_block="(\\${root})\\${env_block}"\n'
    "  export env_block\n"
    '  load_env -f "\\${env_block}"\n'
    "fi\n"
    "\n"
    "EOF\n"
    'if [ "x$GRUB_BUTTON_CMOS_ADDRESS" != "x" ]; then\n'
    "    cat <<EOF\n"
    "if cmostest $GRUB_BUTTON_CMOS_ADDRESS ; then\n"
    '   set default="${GRUB_DEFAULT_BUTTON}"\n'
    "fi\n"
    "EOF\n"
    "fi\n"
)


class TestDisableGrubenvLoading:
    """/etc/grub.d/00_header emits an unconditional
    `if [ -s $prefix/grubenv ]; then load_env; fi` into every grub.cfg,
    regardless of GRUB_DEFAULT/GRUB_SAVEDEFAULT - fails on Btrfs at every
    boot. Removing it from the source script (not the generated grub.cfg,
    which gets overwritten on the next grub-mkconfig) is durable across
    future regenerations too."""

    def test_removes_the_load_env_block(self, tmp_path: Path) -> None:
        header_path = tmp_path / "etc" / "grub.d" / "00_header"
        header_path.parent.mkdir(parents=True)
        header_path.write_text(_REAL_00_HEADER_EXCERPT, encoding="utf-8")
        job = BootloaderJob()

        job._disable_grubenv_loading(str(tmp_path))

        content = header_path.read_text()
        assert "load_env" not in content
        assert "grubenv" not in content
        # Surrounding script must survive untouched.
        assert 'GRUB_DEFAULT_BUTTON="$GRUB_DEFAULT"' in content
        assert "GRUB_BUTTON_CMOS_ADDRESS" in content
        assert 'set default="${GRUB_DEFAULT_BUTTON}"' in content

    def test_noop_when_file_missing(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        job._disable_grubenv_loading(str(tmp_path))  # must not raise

    def test_noop_when_block_not_found(self, tmp_path: Path) -> None:
        header_path = tmp_path / "etc" / "grub.d" / "00_header"
        header_path.parent.mkdir(parents=True)
        header_path.write_text("#! /bin/sh\necho hello\n", encoding="utf-8")
        job = BootloaderJob()

        job._disable_grubenv_loading(str(tmp_path))

        assert header_path.read_text() == "#! /bin/sh\necho hello\n"


class TestDisableEfiBootnextScript:
    """/etc/grub.d/31_efi_bootnext must not run during grub-mkconfig -
    chmod -x it in the target chroot before GRUB config generation."""

    def test_strips_execute_bit(self, tmp_path: Path) -> None:
        script_path = tmp_path / "etc" / "grub.d" / "31_efi_bootnext"
        script_path.parent.mkdir(parents=True)
        script_path.write_text("#! /bin/sh\necho bootnext\n", encoding="utf-8")
        script_path.chmod(0o755)
        job = BootloaderJob()

        job._disable_efi_bootnext_script(str(tmp_path))

        mode = script_path.stat().st_mode
        assert not (mode & 0o111), "execute bit must be cleared"
        assert mode & 0o600, "read/write bits must survive"

    def test_noop_when_file_missing(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        job._disable_efi_bootnext_script(str(tmp_path))  # must not raise


class TestWriteGrubDefaults:
    """Mirrors XeroLinux's Calamares grubcfg.conf: GRUB_TIMEOUT,
    GRUB_DEFAULT, GRUB_DISABLE_SUBMENU, GRUB_DISABLE_RECOVERY,
    GRUB_EARLY_INITRD_LINUX_STOCK, and the kernel_params list."""

    def test_writes_all_expected_keys(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        context = JobContext(selections={})

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert 'GRUB_TIMEOUT="5"' in content
        assert 'GRUB_DEFAULT="saved"' in content
        assert 'GRUB_DISABLE_SUBMENU="false"' in content
        assert 'GRUB_DISABLE_RECOVERY="true"' in content
        assert 'GRUB_EARLY_INITRD_LINUX_STOCK=""' in content
        assert 'GRUB_CMDLINE_LINUX_DEFAULT="quiet nowatchdog nvme_load=YES zswap.enabled=0"' in (
            content
        )

    def test_grub_default_is_numeric_for_btrfs(self, tmp_path: Path) -> None:
        """Regression: GRUB_DEFAULT=saved needs to write grubenv, which
        GRUB cannot do on Btrfs (no separate /boot here) - produces
        "error: sparse file not allowed" at every boot."""
        job = BootloaderJob()
        context = JobContext(selections={"filesystem": "btrfs"})

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert 'GRUB_DEFAULT="0"' in content
        assert 'GRUB_DEFAULT="saved"' not in content

    def test_grub_default_stays_saved_for_non_btrfs(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        context = JobContext(selections={"filesystem": "ext4"})

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert 'GRUB_DEFAULT="saved"' in content

    def test_grub_savedefault_forced_false_for_btrfs(self, tmp_path: Path) -> None:
        """Regression: install.py's rsync copy carries over the live ISO's
        GRUB_SAVEDEFAULT="true" - every generated boot entry then calls
        the `savedefault` function (writes to grubenv on EVERY boot),
        hitting the exact grubenv-write path GRUB_DEFAULT=0 alone doesn't
        avoid. Matches Calamares's own real grubcfg module, which forces
        GRUB_SAVEDEFAULT=false whenever root/boot is btrfs/f2fs/zfs."""
        grub_default = tmp_path / "etc" / "default" / "grub"
        grub_default.parent.mkdir(parents=True)
        grub_default.write_text('GRUB_SAVEDEFAULT="true"\n')
        job = BootloaderJob()
        context = JobContext(selections={"filesystem": "btrfs"})

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = grub_default.read_text()
        assert 'GRUB_SAVEDEFAULT="false"' in content
        assert 'GRUB_SAVEDEFAULT="true"' not in content

    def test_grub_savedefault_untouched_for_non_btrfs(self, tmp_path: Path) -> None:
        grub_default = tmp_path / "etc" / "default" / "grub"
        grub_default.parent.mkdir(parents=True)
        grub_default.write_text('GRUB_SAVEDEFAULT="true"\n')
        job = BootloaderJob()
        context = JobContext(selections={"filesystem": "ext4"})

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = grub_default.read_text()
        assert 'GRUB_SAVEDEFAULT="true"' in content

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        target = tmp_path / "target"
        assert not target.exists()
        context = JobContext(selections={})

        result = job._write_grub_defaults(str(target), context)

        assert result.success is True
        assert (target / "etc" / "default" / "grub").is_file()

    def test_overwrites_existing_key(self, tmp_path: Path) -> None:
        grub_default = tmp_path / "etc" / "default" / "grub"
        grub_default.parent.mkdir(parents=True)
        grub_default.write_text('GRUB_TIMEOUT="30"\nGRUB_DEFAULT="0"\n')
        job = BootloaderJob()
        context = JobContext(selections={})

        job._write_grub_defaults(str(tmp_path), context)

        content = grub_default.read_text()
        assert 'GRUB_TIMEOUT="5"' in content
        assert 'GRUB_TIMEOUT="30"' not in content

    @patch("xen.jobs.bootloader.BootloaderJob._blkid_uuid")
    def test_adds_cryptdevice_when_encrypted(self, mock_blkid: MagicMock, tmp_path: Path) -> None:
        """Regression: GRUB booted fine and LUKS never got unlocked because
        no cryptdevice= kernel parameter told mkinitcpio's "encrypt" hook
        which raw partition to unlock - GRUB_ENABLE_CRYPTODISK alone isn't
        enough, that only lets GRUB itself read an encrypted /boot."""
        mock_blkid.return_value = "abe5431a-d024-485c-9bd0-bd146e2fac1f"
        job = BootloaderJob()
        context = JobContext(
            selections={"encryption": True, "root_partition": "/dev/vda2"}
        )

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert "cryptdevice=UUID=abe5431a-d024-485c-9bd0-bd146e2fac1f:cryptroot" in content
        mock_blkid.assert_called_once_with("/dev/vda2")

    def test_no_cryptdevice_when_not_encrypted(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        context = JobContext(selections={"encryption": False})

        job._write_grub_defaults(str(tmp_path), context)

        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert "cryptdevice=" not in content

    @patch("xen.jobs.bootloader.BootloaderJob._blkid_uuid")
    def test_missing_uuid_does_not_fail_the_job(
        self, mock_blkid: MagicMock, tmp_path: Path
    ) -> None:
        mock_blkid.return_value = None
        job = BootloaderJob()
        context = JobContext(
            selections={"encryption": True, "root_partition": "/dev/vda2"}
        )

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert "cryptdevice=" not in content

    @patch("xen.jobs.bootloader.BootloaderJob._blkid_uuid")
    def test_adds_cryptkey_when_keyfile_present(
        self, mock_blkid: MagicMock, tmp_path: Path
    ) -> None:
        """LuksBootKeyfileJob (run before this job) embeds a keyfile in the
        initramfs and records its path - this must add the matching
        cryptkey= param so the kernel's "encrypt" hook auto-unlocks with it
        instead of prompting for the passphrase a second time."""
        mock_blkid.return_value = "abe5431a-d024-485c-9bd0-bd146e2fac1f"
        job = BootloaderJob()
        context = JobContext(
            selections={
                "encryption": True,
                "root_partition": "/dev/vda2",
                "luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key",
            }
        )

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert "cryptkey=rootfs:/etc/cryptsetup-keys.d/root.key" in content

    def test_no_cryptkey_when_not_encrypted(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        context = JobContext(
            selections={
                "encryption": False,
                "luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key",
            }
        )

        job._write_grub_defaults(str(tmp_path), context)

        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert "cryptkey=" not in content

    @patch("xen.jobs.bootloader.BootloaderJob._blkid_uuid")
    def test_adds_cryptdevice_for_manual_mode_encrypted_root(
        self, mock_blkid: MagicMock, tmp_path: Path
    ) -> None:
        """Manual mode's encrypted root (a mapper path at mountpoint "/" in
        partition_assignments) must produce the same cryptdevice= kernel
        param as AUTO mode's single encryption selection."""
        mock_blkid.return_value = "abe5431a-d024-485c-9bd0-bd146e2fac1f"
        job = BootloaderJob()
        context = JobContext(
            selections={
                "partition_mode": "manual",
                "partition_assignments": [
                    {
                        "path": "/dev/mapper/cryptroot",
                        "mountpoint": "/",
                        "raw_path": "/dev/sda2",
                    },
                ],
            }
        )

        result = job._write_grub_defaults(str(tmp_path), context)

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert "cryptdevice=UUID=abe5431a-d024-485c-9bd0-bd146e2fac1f:cryptroot" in content
        mock_blkid.assert_called_once_with("/dev/sda2")


class TestEnableGrubCryptodisk:
    def test_sets_unquoted_y(self, tmp_path: Path) -> None:
        job = BootloaderJob()

        result = job._enable_grub_cryptodisk(str(tmp_path))

        assert result.success is True
        content = (tmp_path / "etc" / "default" / "grub").read_text()
        assert "GRUB_ENABLE_CRYPTODISK=y" in content
        assert 'GRUB_ENABLE_CRYPTODISK="y"' not in content

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        job = BootloaderJob()
        target = tmp_path / "target"

        result = job._enable_grub_cryptodisk(str(target))

        assert result.success is True
        assert (target / "etc" / "default" / "grub").is_file()


class TestInstallGrubEfiFallback:
    @patch("xen.jobs.bootloader.subprocess.run")
    def test_fallback_failure_is_non_fatal(self, mock_subprocess: MagicMock) -> None:
        """installEFIFallback failing shouldn't fail the whole job - the
        primary named boot entry already makes the disk bootable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()
            job._efi_mount = Path(tmpdir) / "boot" / "efi"
            job._efi_mount.mkdir(parents=True, exist_ok=True)

            def run_side_effect(*args: Any, **_kwargs: Any) -> MagicMock:
                cmd = args[0]
                if "--removable" in cmd:
                    return MagicMock(returncode=1, stdout="", stderr="fallback failed")
                return MagicMock(returncode=0, stdout="Success", stderr="")

            mock_subprocess.side_effect = run_side_effect

            context = JobContext(target_root=tmpdir)
            result = job._install_grub(context)

            assert result.success is True


# =============================================================================
# Root UUID Detection Tests
# =============================================================================


class TestGetRootPartitionUuid:
    """Tests for root partition UUID detection."""

    def test_get_uuid_from_fstab(self) -> None:
        """Should read UUID from fstab."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fstab with UUID
            fstab_path = Path(tmpdir) / "etc" / "fstab"
            fstab_path.parent.mkdir(parents=True, exist_ok=True)
            fstab_content = """# /etc/fstab
UUID=1234-5678-abcd / ext4 rw,relatime 0 1
UUID=9999-0000-ffff /boot/efi vfat defaults 0 2
"""
            fstab_path.write_text(fstab_content)

            job = BootloaderJob()
            context = JobContext(target_root=tmpdir)

            uuid = job._get_root_partition_uuid(context)

            assert uuid == "1234-5678-abcd"

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_get_uuid_from_blkid(self, mock_subprocess: MagicMock) -> None:
        """Should fallback to blkid if fstab not available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()

            # Mock findmnt and blkid
            def run_side_effect(*args: Any, **_kwargs: Any) -> MagicMock:
                cmd = args[0]
                if "findmnt" in cmd:
                    return MagicMock(stdout="/dev/sda2\n")
                if "blkid" in cmd:
                    return MagicMock(stdout="abcd-1234-efgh\n")
                return MagicMock()

            mock_subprocess.side_effect = run_side_effect

            context = JobContext(target_root=tmpdir)
            uuid = job._get_root_partition_uuid(context)

            assert uuid == "abcd-1234-efgh"

    @patch("xen.jobs.bootloader.subprocess.run")
    def test_get_uuid_failure(self, mock_subprocess: MagicMock) -> None:
        """Should return None if UUID cannot be determined."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = BootloaderJob()

            mock_subprocess.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["findmnt"], stderr="Not found"
            )

            context = JobContext(target_root=tmpdir)
            uuid = job._get_root_partition_uuid(context)

            assert uuid is None


# =============================================================================
# Validation Tests
# =============================================================================


class TestGetBootloaderType:
    """Regression: the yaml profile's `type:` (e.g. xerolinux.yaml's
    `type: grub`, matching Calamares's efiBootLoader: "grub") was never
    actually read - run()/validate() only checked
    context.selections["bootloader"], a key nothing ever sets, so every
    profile silently ran systemd-boot regardless of its yaml config."""

    def test_uses_configured_type_from_job_config(self) -> None:
        job = BootloaderJob(config={"type": "grub"})
        context = JobContext()

        assert job._get_bootloader_type(context) == "grub"

    def test_job_config_wins_over_selections(self) -> None:
        job = BootloaderJob(config={"type": "grub"})
        context = JobContext(selections={"bootloader": "systemd-boot"})

        assert job._get_bootloader_type(context) == "grub"

    def test_falls_back_to_selections_when_not_configured(self) -> None:
        job = BootloaderJob()
        context = JobContext(selections={"bootloader": "systemd-boot"})

        assert job._get_bootloader_type(context) == "systemd-boot"

    def test_falls_back_to_systemd_boot_default(self) -> None:
        job = BootloaderJob()
        context = JobContext()

        assert job._get_bootloader_type(context) == "systemd-boot"

    @patch("xen.jobs.bootloader.BootloaderJob._install_grub")
    @patch("xen.jobs.bootloader.BootloaderJob.validate")
    def test_run_installs_grub_when_configured_in_yaml(
        self, mock_validate: MagicMock, mock_install: MagicMock
    ) -> None:
        mock_validate.return_value = JobResult.ok()
        mock_install.return_value = JobResult.ok("GRUB installed and configured")
        job = BootloaderJob(config={"type": "grub"})
        # No "bootloader" in selections at all - matches the real xerolinux.yaml case.
        context = JobContext(selections={})

        result = job.run(context)

        assert result.success is True
        mock_install.assert_called_once()


class TestResolveRootEncryption:
    """is_root_encrypted/raw_root_partition resolution: the single
    AUTO-mode selection pair, or - when partition_mode is "manual" - the
    manual plan's resolved mount assignments, so GRUB/mkinitcpio's
    cryptdevice= works correctly whichever mode produced the encrypted
    root."""

    def test_auto_mode_encrypted(self) -> None:
        job = BootloaderJob()
        context = JobContext(selections={"encryption": True, "root_partition": "/dev/sda2"})

        assert job._resolve_root_encryption(context) == (True, "/dev/sda2")

    def test_auto_mode_not_encrypted(self) -> None:
        job = BootloaderJob()
        context = JobContext(selections={"encryption": False})

        assert job._resolve_root_encryption(context) == (False, "")

    def test_manual_mode_root_encryption_detected_from_assignments(self) -> None:
        """When partition_mode is manual, "is root encrypted" comes from
        partition_assignments (a mapper path at mountpoint "/"), not the
        single auto-mode encryption selection."""
        job = BootloaderJob()
        context = JobContext(
            selections={
                "partition_mode": "manual",
                "partition_assignments": [
                    {
                        "path": "/dev/mapper/cryptroot",
                        "mountpoint": "/",
                        "raw_path": "/dev/sda2",
                    },
                ],
            }
        )

        assert job._resolve_root_encryption(context) == (True, "/dev/sda2")

    def test_manual_mode_root_not_encrypted(self) -> None:
        job = BootloaderJob()
        context = JobContext(
            selections={
                "partition_mode": "manual",
                "partition_assignments": [
                    {"path": "/dev/sda2", "mountpoint": "/"},
                ],
            }
        )

        assert job._resolve_root_encryption(context) == (False, "")

    def test_manual_mode_no_root_assignment(self) -> None:
        job = BootloaderJob()
        context = JobContext(
            selections={"partition_mode": "manual", "partition_assignments": []}
        )

        assert job._resolve_root_encryption(context) == (False, "")

    def test_manual_mode_ignores_auto_encryption_flag(self) -> None:
        """A stale/unrelated AUTO-mode 'encryption' flag must not leak into
        manual mode's result - only the resolved mount assignment counts."""
        job = BootloaderJob()
        context = JobContext(
            selections={
                "partition_mode": "manual",
                "encryption": True,
                "root_partition": "/dev/sda2",
                "partition_assignments": [
                    {"path": "/dev/sda3", "mountpoint": "/"},
                ],
            }
        )

        assert job._resolve_root_encryption(context) == (False, "")


class TestValidate:
    """Tests for bootloader configuration validation."""

    def test_validate_invalid_bootloader(self) -> None:
        """Should fail with invalid bootloader selection."""
        job = BootloaderJob()
        context = JobContext(
            selections={
                "bootloader": "lilo",  # Not supported
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 67
        assert "Invalid bootloader" in result.message

    @patch("xen.jobs.bootloader.BootloaderJob._detect_kernels")
    @patch("xen.jobs.bootloader.BootloaderJob._validate_efi_system")
    def test_validate_efi_system_fails(
        self,
        mock_validate_efi: MagicMock,
        _mock_detect_kernels: MagicMock,
    ) -> None:
        """Should fail if EFI system validation fails."""
        job = BootloaderJob()

        mock_validate_efi.return_value = JobResult.fail("No EFI", error_code=50)

        context = JobContext(
            selections={"bootloader": "systemd-boot"},
            target_root="/mnt",
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 50

    @patch("xen.jobs.bootloader.BootloaderJob._detect_kernels")
    @patch("xen.jobs.bootloader.BootloaderJob._validate_efi_system")
    def test_validate_kernel_detection_fails(
        self,
        mock_validate_efi: MagicMock,
        mock_detect_kernels: MagicMock,
    ) -> None:
        """Should fail if kernel detection fails."""
        job = BootloaderJob()

        mock_validate_efi.return_value = JobResult.ok()
        mock_detect_kernels.return_value = JobResult.fail("No kernels", error_code=54)

        context = JobContext(
            selections={"bootloader": "systemd-boot"},
            target_root="/mnt",
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 54

    @patch("xen.jobs.bootloader.BootloaderJob._detect_kernels")
    @patch("xen.jobs.bootloader.BootloaderJob._validate_efi_system")
    def test_validate_success(
        self,
        mock_validate_efi: MagicMock,
        mock_detect_kernels: MagicMock,
    ) -> None:
        """Should pass validation with valid configuration."""
        job = BootloaderJob()
        job._efi_mount = Path("/mnt/boot/efi")
        job._kernels = ["vmlinuz-6.1.0"]

        mock_validate_efi.return_value = JobResult.ok()
        mock_detect_kernels.return_value = JobResult.ok()

        context = JobContext(
            selections={"bootloader": "systemd-boot"},
            target_root="/mnt",
        )

        result = job.validate(context)

        assert result.success is True
        assert result.data["bootloader"] == "systemd-boot"
        assert result.data["kernels"] == ["vmlinuz-6.1.0"]


# =============================================================================
# Full Job Execution Tests
# =============================================================================


class TestBootloaderJobRun:
    """Tests for full BootloaderJob execution."""

    @patch("xen.jobs.bootloader.BootloaderJob._install_systemd_boot")
    @patch("xen.jobs.bootloader.BootloaderJob.validate")
    def test_run_systemd_boot_success(
        self,
        mock_validate: MagicMock,
        mock_install: MagicMock,
    ) -> None:
        """Should run systemd-boot installation successfully."""
        job = BootloaderJob()
        job._efi_mount = Path("/mnt/boot/efi")
        job._kernels = ["vmlinuz-6.1.0"]

        mock_validate.return_value = JobResult.ok()
        mock_install.return_value = JobResult.ok()

        context = JobContext(
            target_root="/mnt",
            selections={"bootloader": "systemd-boot"},
        )
        context.on_progress = MagicMock()

        result = job.run(context)

        assert result.success is True
        assert "systemd-boot" in result.message
        assert context.on_progress.called
        mock_install.assert_called_once()

    @patch("xen.jobs.bootloader.BootloaderJob._install_grub")
    @patch("xen.jobs.bootloader.BootloaderJob.validate")
    def test_run_grub_success(
        self,
        mock_validate: MagicMock,
        mock_install: MagicMock,
    ) -> None:
        """Should run GRUB installation successfully."""
        job = BootloaderJob()
        job._efi_mount = Path("/mnt/boot/efi")
        job._kernels = ["vmlinuz-6.1.0"]

        mock_validate.return_value = JobResult.ok()
        mock_install.return_value = JobResult.ok()

        context = JobContext(
            target_root="/mnt",
            selections={"bootloader": "grub"},
        )
        context.on_progress = MagicMock()

        result = job.run(context)

        assert result.success is True
        assert "grub" in result.message.lower()
        mock_install.assert_called_once()

    @patch("xen.jobs.bootloader.BootloaderJob.validate")
    def test_run_validation_fails(self, mock_validate: MagicMock) -> None:
        """Should fail if validation fails."""
        job = BootloaderJob()

        mock_validate.return_value = JobResult.fail("Validation failed", error_code=50)

        context = JobContext(
            target_root="/mnt",
            selections={"bootloader": "systemd-boot"},
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 50

    @patch("xen.jobs.bootloader.BootloaderJob._install_systemd_boot")
    @patch("xen.jobs.bootloader.BootloaderJob.validate")
    def test_run_installation_fails(
        self,
        mock_validate: MagicMock,
        mock_install: MagicMock,
    ) -> None:
        """Should propagate installation failures."""
        job = BootloaderJob()

        mock_validate.return_value = JobResult.ok()
        mock_install.return_value = JobResult.fail("Installation failed", error_code=56)

        context = JobContext(
            target_root="/mnt",
            selections={"bootloader": "systemd-boot"},
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 56

    @patch("xen.jobs.bootloader.BootloaderJob.validate")
    def test_run_default_systemd_boot(self, mock_validate: MagicMock) -> None:
        """Should default to systemd-boot if not specified."""
        job = BootloaderJob()

        mock_validate.return_value = JobResult.ok()

        with patch.object(job, "_install_systemd_boot") as mock_install:
            mock_install.return_value = JobResult.ok()

            context = JobContext(
                target_root="/mnt",
                selections={},  # No bootloader specified
            )

            result = job.run(context)

            assert result.success is True
            mock_install.assert_called_once()


# =============================================================================
# Integration Tests
# =============================================================================


class TestBootloaderJobIntegration:
    """Integration tests for complete BootloaderJob workflow."""

    @patch("xen.jobs.bootloader.subprocess.run")
    @patch("xen.jobs.bootloader.Path")
    def test_full_systemd_boot_workflow(
        self,
        mock_path: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Test complete systemd-boot installation workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup directory structure
            boot_dir = Path(tmpdir) / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)
            (boot_dir / "vmlinuz-6.1.0").touch()
            (boot_dir / "initramfs-6.1.0.img").touch()

            efi_mount = Path(tmpdir) / "boot" / "efi"
            efi_mount.mkdir(parents=True, exist_ok=True)

            fstab_path = Path(tmpdir) / "etc" / "fstab"
            fstab_path.parent.mkdir(parents=True, exist_ok=True)
            fstab_path.write_text("UUID=test-uuid / ext4 defaults 0 1\n")

            # Mock Path for efivars check
            mock_efivars = MagicMock()
            mock_efivars.exists.return_value = True

            def path_side_effect(path_str: str) -> Path | MagicMock:
                if "efivars" in str(path_str):
                    return mock_efivars
                return Path(path_str)

            mock_path.side_effect = path_side_effect

            # Mock subprocess calls
            mock_subprocess.return_value = MagicMock(returncode=0, stdout="Success")

            # Create and run job
            job = BootloaderJob(config={"timeout": 5})
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "bootloader": "systemd-boot",
                    "kernel_params": "quiet",
                },
            )

            result = job.run(context)

            assert result.success is True
            assert result.data["bootloader"] == "systemd-boot"

            # Verify configuration files were created
            loader_conf = efi_mount / "loader" / "loader.conf"
            assert loader_conf.exists()
            assert "timeout 5" in loader_conf.read_text()

            entry_file = efi_mount / "loader" / "entries" / "arch-6.1.0.conf"
            assert entry_file.exists()
            content = entry_file.read_text()
            assert "vmlinuz-6.1.0" in content
            assert "initramfs-6.1.0.img" in content
            assert "UUID=test-uuid" in content

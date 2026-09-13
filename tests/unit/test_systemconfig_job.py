"""Unit tests for SystemConfigJob."""

import os
from pathlib import Path
from unittest.mock import Mock, patch

from xen.jobs.systemconfig import SystemConfigJob


def _write_conf(tmp_path: Path, hooks_line: str) -> str:
    target_root = tmp_path / "target"
    conf_path = target_root / "etc" / "mkinitcpio.conf"
    conf_path.parent.mkdir(parents=True)
    conf_path.write_text(f"MODULES=()\n{hooks_line}\nCOMPRESSION=\"zstd\"\n")
    return str(target_root)


class TestGenerateCrypttab:
    """Mirrors Calamares's fstab module (crypttabOptions: luks): a
    crypttab entry for the encrypted root, alongside the kernel cmdline
    cryptdevice= param bootloader.py already sets."""

    @patch("subprocess.run")
    def test_writes_cryptroot_entry(self, mock_run: Mock, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()
        mock_run.return_value = Mock(returncode=0, stdout="1234-5678-uuid\n", stderr="")
        job = SystemConfigJob()

        result = job._generate_crypttab(str(target), {"root_partition": "/dev/vda2"})

        assert result.success is True
        content = (target / "etc" / "crypttab").read_text()
        assert content == "cryptroot UUID=1234-5678-uuid none luks\n"

    def test_fails_without_root_partition(self, tmp_path: Path) -> None:
        job = SystemConfigJob()

        result = job._generate_crypttab(str(tmp_path), {})

        assert result.success is False
        assert result.error_code == 107

    @patch("subprocess.run")
    def test_fails_when_uuid_lookup_fails(self, mock_run: Mock, tmp_path: Path) -> None:
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(1, "blkid")
        job = SystemConfigJob()

        result = job._generate_crypttab(str(tmp_path), {"root_partition": "/dev/vda2"})

        assert result.success is False
        assert result.error_code == 108

    @patch("subprocess.run")
    def test_idempotent_when_entry_already_present(self, mock_run: Mock, tmp_path: Path) -> None:
        target = tmp_path / "target"
        crypttab = target / "etc" / "crypttab"
        crypttab.parent.mkdir(parents=True)
        crypttab.write_text("cryptroot UUID=existing none luks\n")
        job = SystemConfigJob()

        result = job._generate_crypttab(str(target), {"root_partition": "/dev/vda2"})

        assert result.success is True
        mock_run.assert_not_called()
        assert crypttab.read_text() == "cryptroot UUID=existing none luks\n"


class TestGenerateMachineId:
    """Mirrors Calamares's machineid.conf (dbus: true, dbus-symlink:
    true): /var/lib/dbus/machine-id must be a symlink to /etc/machine-id,
    not a separate copy that can drift after a later regeneration."""

    @patch("subprocess.run")
    def test_creates_dbus_symlink(self, mock_run: Mock, tmp_path: Path) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        job = SystemConfigJob()

        result = job._generate_machine_id(str(tmp_path))

        assert result.success is True
        dbus_link = tmp_path / "var" / "lib" / "dbus" / "machine-id"
        assert dbus_link.is_symlink()
        assert os.readlink(dbus_link) == "/etc/machine-id"

    @patch("subprocess.run")
    def test_replaces_stale_regular_file(self, mock_run: Mock, tmp_path: Path) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        dbus_link = tmp_path / "var" / "lib" / "dbus" / "machine-id"
        dbus_link.parent.mkdir(parents=True)
        dbus_link.write_text("stale-machine-id-copy\n")
        job = SystemConfigJob()

        result = job._generate_machine_id(str(tmp_path))

        assert result.success is True
        assert dbus_link.is_symlink()

    @patch("subprocess.run")
    def test_symlink_failure_is_non_fatal(self, mock_run: Mock, tmp_path: Path) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        job = SystemConfigJob()

        with patch("pathlib.Path.symlink_to", side_effect=OSError("boom")):
            result = job._generate_machine_id(str(tmp_path))

        assert result.success is True


class TestConfigureMkinitcpioHooks:
    def test_strips_live_iso_only_hooks(self, tmp_path: Path) -> None:
        """Regression: install.py's rsync copy carries over the live ISO's
        own /etc/mkinitcpio.conf verbatim, including archiso/memdisk hooks
        meaningless (and breaking mkinitcpio -P) on an installed target."""
        target = _write_conf(
            tmp_path,
            "HOOKS=(base udev modconf kms memdisk archiso archiso_loop_mnt "
            "archiso_pxe_common archiso_pxe_nbd archiso_pxe_http "
            "archiso_pxe_nfs block filesystems keyboard)",
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        for hook in SystemConfigJob._LIVE_ONLY_HOOKS:
            assert hook not in content
        assert "HOOKS=(base udev modconf kms block filesystems keyboard)" in content

    def test_inserts_encrypt_before_filesystems_when_encryption_selected(
        self, tmp_path: Path
    ) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {"encryption": True})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev modconf block encrypt filesystems keyboard)" in content

    def test_inserts_resume_after_filesystems_when_hibernate_selected(
        self, tmp_path: Path
    ) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {"swap_strategy": "hibernate"})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev modconf block filesystems resume keyboard)" in content

    def test_noop_when_hooks_already_correct(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {})

        assert result.success is True
        assert "already correct" in result.message

    def test_fails_when_hooks_line_missing(self, tmp_path: Path) -> None:
        target_root = tmp_path / "target"
        conf_path = target_root / "etc" / "mkinitcpio.conf"
        conf_path.parent.mkdir(parents=True)
        conf_path.write_text("MODULES=()\n")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(str(target_root), {})

        assert result.success is False
        assert result.error_code == 99

    def test_fails_when_conf_file_missing(self, tmp_path: Path) -> None:
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(str(tmp_path / "nope"), {})

        assert result.success is False
        assert result.error_code == 98


def _write_conf_with_files(tmp_path: Path, hooks_line: str, files_line: str) -> str:
    target_root = tmp_path / "target"
    conf_path = target_root / "etc" / "mkinitcpio.conf"
    conf_path.parent.mkdir(parents=True)
    conf_path.write_text(f"MODULES=()\n{files_line}\n{hooks_line}\nCOMPRESSION=\"zstd\"\n")
    return str(target_root)


class TestAddLuksKeyfileToFiles:
    """LuksBootKeyfileJob (run before this job) sets
    context.selections["luks_keyfile_path"] - this embeds it into the
    initramfs via mkinitcpio's FILES=, so the kernel's "encrypt" hook can
    auto-unlock with it instead of prompting for the passphrase again."""

    def test_appends_keyfile_to_empty_files(self, tmp_path: Path) -> None:
        target = _write_conf_with_files(
            tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)", "FILES=()"
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target, {"luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key"}
        )

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "FILES=(/etc/cryptsetup-keys.d/root.key)" in content

    def test_appends_keyfile_to_existing_files(self, tmp_path: Path) -> None:
        target = _write_conf_with_files(
            tmp_path,
            "HOOKS=(base udev modconf block filesystems keyboard)",
            "FILES=(/etc/some-other-file)",
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target, {"luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key"}
        )

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "FILES=(/etc/some-other-file /etc/cryptsetup-keys.d/root.key)" in content

    def test_noop_when_keyfile_already_present(self, tmp_path: Path) -> None:
        target = _write_conf_with_files(
            tmp_path,
            "HOOKS=(base udev modconf block filesystems keyboard)",
            "FILES=(/etc/cryptsetup-keys.d/root.key)",
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target, {"luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key"}
        )

        assert result.success is True
        assert "already correct" in result.message

    def test_noop_when_no_keyfile_configured(self, tmp_path: Path) -> None:
        target = _write_conf_with_files(
            tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)", "FILES=()"
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "FILES=()" in content

    def test_combines_with_encrypt_hook_insertion(self, tmp_path: Path) -> None:
        target = _write_conf_with_files(
            tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)", "FILES=()"
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target,
            {"encryption": True, "luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key"},
        )

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev modconf block encrypt filesystems keyboard)" in content
        assert "FILES=(/etc/cryptsetup-keys.d/root.key)" in content


class TestAddBtrfsModule:
    """Btrfs is a kernel module, not built-in - needs adding to MODULES=
    for the initramfs to be able to mount a Btrfs root."""

    def test_adds_btrfs_module_when_filesystem_is_btrfs(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {"filesystem": "btrfs"})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "MODULES=(btrfs)" in content

    def test_noop_when_filesystem_is_not_btrfs(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {"filesystem": "ext4"})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "MODULES=()" in content

    def test_noop_when_btrfs_module_already_present(self, tmp_path: Path) -> None:
        target_root = tmp_path / "target"
        conf_path = target_root / "etc" / "mkinitcpio.conf"
        conf_path.parent.mkdir(parents=True)
        conf_path.write_text(
            "MODULES=(btrfs)\n"
            "HOOKS=(base udev modconf block filesystems keyboard)\n"
            'COMPRESSION="zstd"\n'
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(str(target_root), {"filesystem": "btrfs"})

        assert result.success is True
        assert "already correct" in result.message

    def test_appends_to_existing_modules(self, tmp_path: Path) -> None:
        target_root = tmp_path / "target"
        conf_path = target_root / "etc" / "mkinitcpio.conf"
        conf_path.parent.mkdir(parents=True)
        conf_path.write_text(
            "MODULES=(i915)\n"
            "HOOKS=(base udev modconf block filesystems keyboard)\n"
            'COMPRESSION="zstd"\n'
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(str(target_root), {"filesystem": "btrfs"})

        assert result.success is True
        content = conf_path.read_text()
        assert "MODULES=(i915 btrfs)" in content

    def test_combines_with_encrypt_hook_insertion(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target, {"encryption": True, "filesystem": "btrfs"}
        )

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev modconf block encrypt filesystems keyboard)" in content
        assert "MODULES=(btrfs)" in content


def _write_kernel(tmp_path: Path) -> str:
    target_root = tmp_path / "target"
    boot_dir = target_root / "boot"
    boot_dir.mkdir(parents=True)
    (boot_dir / "vmlinuz-linux").write_bytes(b"fake kernel")
    return str(target_root)


class TestCheckKernelReadable:
    def test_ok_when_vmlinuz_present(self, tmp_path: Path) -> None:
        target = _write_kernel(tmp_path)
        job = SystemConfigJob()

        result = job._check_kernel_readable(target)

        assert result.success is True

    def test_fails_when_boot_dir_missing(self, tmp_path: Path) -> None:
        job = SystemConfigJob()

        result = job._check_kernel_readable(str(tmp_path / "target"))

        assert result.success is False
        assert result.error_code == 105

    def test_fails_with_directory_listing_when_no_kernel(self, tmp_path: Path) -> None:
        target_root = tmp_path / "target"
        boot_dir = target_root / "boot"
        boot_dir.mkdir(parents=True)
        (boot_dir / "initramfs-linux.img").write_bytes(b"not a kernel")
        job = SystemConfigJob()

        with patch.object(SystemConfigJob, "_find_live_kernel_image", return_value=None):
            result = job._check_kernel_readable(str(target_root))

        assert result.success is False
        assert result.error_code == 106
        assert "initramfs-linux.img" in result.message


class TestRemoveLiveOnlyPresets:
    def test_removes_archiso_preset_keeps_real_one(self, tmp_path: Path) -> None:
        """Regression: install.py's rsync copy brings over the live ISO's
        own archiso preset (PRESETS=('archiso')) alongside the real
        installed-system preset; mkinitcpio -P then processes both."""
        target_root = tmp_path / "target"
        preset_dir = target_root / "etc" / "mkinitcpio.d"
        preset_dir.mkdir(parents=True)
        (preset_dir / "linux.preset").write_text(
            "PRESETS=('archiso')\nALL_kver='/boot/vmlinuz-linux'\n"
            "archiso_config='/etc/mkinitcpio.conf'\n"
        )
        (preset_dir / "xero.preset").write_text(
            "ALL_kver='/boot/vmlinuz-linux'\nPRESETS=('default' 'fallback')\n"
        )
        job = SystemConfigJob()

        result = job._remove_live_only_presets(str(target_root))

        assert result.success is True
        assert not (preset_dir / "linux.preset").exists()
        assert (preset_dir / "xero.preset").exists()

    def test_noop_when_no_preset_dir(self, tmp_path: Path) -> None:
        job = SystemConfigJob()

        result = job._remove_live_only_presets(str(tmp_path / "target"))

        assert result.success is True

    def test_keeps_presets_without_archiso_reference(self, tmp_path: Path) -> None:
        target_root = tmp_path / "target"
        preset_dir = target_root / "etc" / "mkinitcpio.d"
        preset_dir.mkdir(parents=True)
        (preset_dir / "xero.preset").write_text(
            "ALL_kver='/boot/vmlinuz-linux'\nPRESETS=('default' 'fallback')\n"
        )
        job = SystemConfigJob()

        result = job._remove_live_only_presets(str(target_root))

        assert result.success is True
        assert (preset_dir / "xero.preset").exists()


class TestRebuildInitramfs:
    @patch("subprocess.run")
    def test_success(self, mock_run: Mock, tmp_path: Path) -> None:
        target = _write_kernel(tmp_path)
        mock_run.return_value = Mock(stdout="==> Building image", stderr="")
        job = SystemConfigJob()

        result = job._rebuild_initramfs(target)

        assert result.success is True
        assert mock_run.call_args[0][0] == ["arch-chroot", target, "mkinitcpio", "-P"]

    @patch("subprocess.run")
    def test_failure_surfaces_stderr(self, mock_run: Mock, tmp_path: Path) -> None:
        import subprocess

        target = _write_kernel(tmp_path)
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "mkinitcpio", stderr="Invalid option -k -- '/boot/vmlinuz-linux' must be readable"
        )
        job = SystemConfigJob()

        result = job._rebuild_initramfs(target)

        assert result.success is False
        assert "must be readable" in result.message
        assert result.error_code == 101

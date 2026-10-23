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


ENC_ROOT = {"path": "/dev/mapper/cryptroot", "raw_path": "/dev/vda2", "mountpoint": "/"}


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

        result = job._generate_crypttab(
            str(target), {"partition_assignments": [ENC_ROOT]}
        )

        assert result.success is True
        content = (target / "etc" / "crypttab").read_text()
        assert content == "cryptroot UUID=1234-5678-uuid none luks\n"

    def test_fails_when_encryption_selected_without_root_partition(
        self, tmp_path: Path
    ) -> None:
        """AUTO mode's encryption flag implies root_partition must be
        resolvable; if it isn't, that's a genuine data-integrity issue
        worth failing on loudly."""
        job = SystemConfigJob()

        result = job._generate_crypttab(
            str(tmp_path), {"partition_assignments": [{**ENC_ROOT, "raw_path": ""}]}
        )

        assert result.success is False
        assert result.error_code == 107

    def test_noop_when_nothing_to_do(self, tmp_path: Path) -> None:
        """Calling with no encryption signal at all (no AUTO flag, no
        manual partition_assignments) is a normal, correct state - e.g. a
        plain unencrypted manual install - not an error. Nothing should
        fail-fast just because there is genuinely nothing to write."""
        job = SystemConfigJob()

        result = job._generate_crypttab(str(tmp_path), {})

        assert result.success is True
        assert not (tmp_path / "etc" / "crypttab").exists()

    @patch("subprocess.run")
    def test_fails_when_uuid_lookup_fails(self, mock_run: Mock, tmp_path: Path) -> None:
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(1, "blkid")
        job = SystemConfigJob()

        result = job._generate_crypttab(
            str(tmp_path), {"partition_assignments": [ENC_ROOT]}
        )

        assert result.success is False
        assert result.error_code == 108

    @patch("subprocess.run")
    def test_idempotent_when_entry_already_present(self, mock_run: Mock, tmp_path: Path) -> None:
        target = tmp_path / "target"
        crypttab = target / "etc" / "crypttab"
        crypttab.parent.mkdir(parents=True)
        crypttab.write_text("cryptroot UUID=existing none luks\n")
        job = SystemConfigJob()

        result = job._generate_crypttab(
            str(target), {"partition_assignments": [ENC_ROOT]}
        )

        assert result.success is True
        mock_run.assert_not_called()
        assert crypttab.read_text() == "cryptroot UUID=existing none luks\n"

    @patch("subprocess.run")
    def test_manual_mode_unencrypted_root_writes_no_cryptroot_line(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """Regression: the real engine always sets selections["root_partition"]
        to whatever the resolved root partition is, encrypted or not -
        trusting it blindly used to write a bogus "cryptroot UUID=<the
        plain filesystem's own UUID> none luks" line for EVERY manual
        install, making systemd-cryptsetup-generator try (and fail) to
        LUKS-open a plain ext4 partition on every single boot."""
        target = tmp_path / "target"
        target.mkdir()
        job = SystemConfigJob()
        selections = {
            "partition_mode": "manual",
            "root_partition": "/dev/sda2",
            "partition_assignments": [
                {"path": "/dev/sda2", "mountpoint": "/"},
            ],
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is True
        mock_run.assert_not_called()
        assert not (target / "etc" / "crypttab").exists()

    @patch("subprocess.run")
    def test_manual_mode_encrypted_root_writes_correct_cryptroot_line(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """A manual-mode encrypted root must get its crypttab line from the
        RAW LUKS partition's own UUID (via partition_assignments' raw_path,
        resolved through _resolve_root_encryption) - not the mapper's
        filesystem UUID, and the real engine sets root_partition to the
        MAPPER path here, not the raw device, so this must not be read
        directly either."""
        target = tmp_path / "target"
        target.mkdir()
        mock_run.return_value = Mock(returncode=0, stdout="raw-luks-uuid\n", stderr="")
        job = SystemConfigJob()
        selections = {
            "partition_mode": "manual",
            "root_partition": "/dev/mapper/cryptroot",
            "partition_assignments": [
                {
                    "path": "/dev/mapper/cryptroot",
                    "mountpoint": "/",
                    "raw_path": "/dev/sda2",
                },
            ],
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is True
        content = (target / "etc" / "crypttab").read_text()
        assert content == "cryptroot UUID=raw-luks-uuid none luks\n"
        mock_run.assert_called_once_with(
            ["blkid", "-s", "UUID", "-o", "value", "/dev/sda2"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("subprocess.run")
    def test_generate_crypttab_adds_non_root_keyfile_entries(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """A non-root encrypted partition (e.g. /home) gets its own crypttab
        line pointing at a per-partition keyfile, plus the keyfile itself
        written and added as a LUKS keyslot - using the passphrase carried
        over from live-Apply."""
        target = tmp_path / "target"
        target.mkdir()

        def run_side_effect(cmd: list[str], **kwargs: object) -> Mock:
            if cmd[0] == "blkid":
                if cmd[-1] == "/dev/sda3":
                    return Mock(returncode=0, stdout="home-uuid\n", stderr="")
                return Mock(returncode=0, stdout="\n", stderr="")
            if cmd[:2] == ["cryptsetup", "luksAddKey"]:
                return Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {cmd}")

        mock_run.side_effect = run_side_effect
        job = SystemConfigJob()
        selections = {
            "partition_assignments": [
                {
                    "path": "/dev/mapper/crypthome",
                    "mountpoint": "/home",
                    "raw_path": "/dev/sda3",
                },
            ],
            "luks_passphrases": {"sda3": "generated-pass"},
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is True
        content = (target / "etc" / "crypttab").read_text()
        assert content == (
            "crypthome UUID=home-uuid /etc/cryptsetup-keys.d/crypthome.key luks\n"
        )
        keyfile = target / "etc" / "cryptsetup-keys.d" / "crypthome.key"
        assert keyfile.exists()
        assert keyfile.stat().st_mode & 0o777 == 0

        luks_add_key_calls = [
            c
            for c in mock_run.call_args_list
            if c.args[0][:2] == ["cryptsetup", "luksAddKey"]
        ]
        assert len(luks_add_key_calls) == 1
        assert luks_add_key_calls[0].args[0][2] == "/dev/sda3"
        # The assertion below checks the carried-over passphrase reached
        # luksAddKey via stdin (--key-file=-) - it's a test fixture value
        # ("generated-pass"), not a real secret, so asserting it here is
        # fine; the thing that must never happen is a REAL passphrase
        # appearing in argv/logs, which this call shape (input=, not argv)
        # already guarantees regardless of what the test checks.
        assert luks_add_key_calls[0].args[0][-1] == "--key-file=-"
        assert luks_add_key_calls[0].kwargs["input"] == "generated-pass"

    @patch("subprocess.run")
    def test_generate_crypttab_adds_swap_partuuid_entry_and_fstab_line(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """An encrypted swap partition gets a plain dm-crypt crypttab line
        keyed on the GPT partition's own PARTUUID (stable across reboots,
        unlike a filesystem UUID - swap is fresh-mkswap'd with a random
        key every boot, ArchWiki: dm-crypt/Swap encryption), plus the
        /etc/fstab line that actually swapons the mapper once crypttab's
        "swap" option has mkswap'd it (crypttab alone only creates it)."""
        target = tmp_path / "target"
        target.mkdir()
        mock_run.return_value = Mock(returncode=0, stdout="swap-partuuid\n", stderr="")
        job = SystemConfigJob()
        selections = {
            "partition_assignments": [
                {
                    "path": "/dev/mapper/cryptswap",
                    "mountpoint": "swap",
                    "raw_path": "/dev/sda4",
                },
            ],
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is True
        content = (target / "etc" / "crypttab").read_text()
        assert content == (
            "cryptswap PARTUUID=swap-partuuid /dev/urandom "
            "swap,cipher=aes-xts-plain64,size=256\n"
        )
        assert not (target / "etc" / "cryptsetup-keys.d").exists()
        mock_run.assert_called_once_with(
            ["blkid", "-s", "PARTUUID", "-o", "value", "/dev/sda4"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fstab_content = (target / "etc" / "fstab").read_text()
        assert fstab_content == "/dev/mapper/cryptswap none swap defaults 0 0\n"

    @patch("subprocess.run")
    def test_generate_crypttab_fails_when_swap_partuuid_lookup_fails(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """A partition the user explicitly asked to encrypt as swap that
        silently doesn't work (the old UUID-based lookup would always fail
        for a never-formatted partition and just skip it) is worse than a
        clear failure - fail loudly instead."""
        target = tmp_path / "target"
        target.mkdir()
        mock_run.return_value = Mock(returncode=0, stdout="\n", stderr="")
        job = SystemConfigJob()
        selections = {
            "partition_assignments": [
                {
                    "path": "/dev/mapper/cryptswap",
                    "mountpoint": "swap",
                    "raw_path": "/dev/sda4",
                },
            ],
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is False
        assert result.error_code == 117
        assert not (target / "etc" / "crypttab").exists()

    @patch("subprocess.run")
    def test_swap_fstab_entry_is_idempotent(self, mock_run: Mock, tmp_path: Path) -> None:
        target = tmp_path / "target"
        fstab = target / "etc" / "fstab"
        fstab.parent.mkdir(parents=True)
        fstab.write_text("/dev/mapper/cryptswap none swap defaults 0 0\n")
        mock_run.return_value = Mock(returncode=0, stdout="swap-partuuid\n", stderr="")
        job = SystemConfigJob()
        selections = {
            "partition_assignments": [
                {
                    "path": "/dev/mapper/cryptswap",
                    "mountpoint": "swap",
                    "raw_path": "/dev/sda4",
                },
            ],
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is True
        assert fstab.read_text() == "/dev/mapper/cryptswap none swap defaults 0 0\n"

    @patch("subprocess.run")
    def test_generate_crypttab_fails_without_carried_over_passphrase(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """Without a carried-over passphrase, a non-root keyfile cannot be
        authenticated against the LUKS header - fail rather than silently
        skip the partition (it would never auto-unlock at boot)."""
        target = tmp_path / "target"
        target.mkdir()
        job = SystemConfigJob()
        selections = {
            "partition_assignments": [
                {
                    "path": "/dev/mapper/crypthome",
                    "mountpoint": "/home",
                    "raw_path": "/dev/sda3",
                },
            ],
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is False
        assert result.error_code == 114
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_swap_fstab_entry_regenerated_when_crypttab_already_has_entry(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """A retry where /etc/crypttab already has the swap mapper's line
        (e.g. a prior attempt wrote crypttab but not fstab) must still get
        the fstab line regenerated - the idempotency short-circuit used to
        skip the whole swap branch before ever reaching the fstab-queueing
        step, losing the fstab line permanently on any such retry."""
        target = tmp_path / "target"
        crypttab = target / "etc" / "crypttab"
        crypttab.parent.mkdir(parents=True)
        crypttab.write_text(
            "cryptswap PARTUUID=existing-partuuid /dev/urandom "
            "swap,cipher=aes-xts-plain64,size=256\n"
        )
        job = SystemConfigJob()
        selections = {
            "partition_assignments": [
                {
                    "path": "/dev/mapper/cryptswap",
                    "mountpoint": "swap",
                    "raw_path": "/dev/sda4",
                },
            ],
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is True
        mock_run.assert_not_called()
        fstab_content = (target / "etc" / "fstab").read_text()
        assert fstab_content == "/dev/mapper/cryptswap none swap defaults 0 0\n"
        assert crypttab.read_text() == (
            "cryptswap PARTUUID=existing-partuuid /dev/urandom "
            "swap,cipher=aes-xts-plain64,size=256\n"
        )

    @patch("subprocess.run")
    def test_swap_fstab_entry_not_written_when_a_later_partition_fails(
        self, mock_run: Mock, tmp_path: Path
    ) -> None:
        """If a later partition in the same plan fails (e.g. a missing
        passphrase), the swap mapper's fstab entry must not have been
        written either - crypttab never ends up creating that mapper when
        the whole write is never reached, so an orphaned fstab entry would
        try to swapon something that doesn't exist at boot."""
        target = tmp_path / "target"
        target.mkdir()
        mock_run.return_value = Mock(returncode=0, stdout="swap-partuuid\n", stderr="")
        job = SystemConfigJob()
        selections = {
            "partition_assignments": [
                {
                    "path": "/dev/mapper/cryptswap",
                    "mountpoint": "swap",
                    "raw_path": "/dev/sda4",
                },
                {
                    "path": "/dev/mapper/crypthome",
                    "mountpoint": "/home",
                    "raw_path": "/dev/sda3",
                },
            ],
            # No luks_passphrases - the /home entry fails.
        }

        result = job._generate_crypttab(str(target), selections)

        assert result.success is False
        assert result.error_code == 114
        assert not (target / "etc" / "crypttab").exists()
        assert not (target / "etc" / "fstab").exists()

    @staticmethod
    def test_crypttab_mapper_names_does_not_substring_match() -> None:
        """Cheap fix: deciding whether an entry already exists must parse
        each line's first field exactly, not substring-match the whole
        file content (e.g. "cryptdata" must not match inside an existing
        "cryptdata-srv" line)."""
        content = "cryptdata-srv UUID=abc /etc/cryptsetup-keys.d/cryptdata-srv.key luks\n"

        names = SystemConfigJob._crypttab_mapper_names(content)

        assert names == {"cryptdata-srv"}
        assert "cryptdata" not in names


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
            "HOOKS=(base udev microcode modconf kms memdisk archiso archiso_loop_mnt "
            "archiso_pxe_common archiso_pxe_nbd archiso_pxe_http "
            "archiso_pxe_nfs block filesystems keyboard)",
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        for hook in SystemConfigJob._LIVE_ONLY_HOOKS:
            assert hook not in content
        assert "HOOKS=(base udev microcode modconf kms block filesystems keyboard)" in content

    def test_inserts_encrypt_before_filesystems_when_encryption_selected(
        self, tmp_path: Path
    ) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {"partition_assignments": [ENC_ROOT]})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev microcode modconf block encrypt filesystems keyboard)" in content

    def test_inserts_encrypt_for_manual_mode_encrypted_root(self, tmp_path: Path) -> None:
        """Regression: manual mode never sets the top-level "encryption"
        selection (it threads intent through partition_assignments/per-op
        encrypt flags instead), so the old `selections.get("encryption")`
        read always evaluated False for a manual-mode encrypted root - the
        "encrypt" hook never got added, and the initramfs had no way to
        unlock root at boot at all. Must resolve the same way
        bootloader.py's _resolve_root_encryption does."""
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target,
            {
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

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev microcode modconf block encrypt filesystems keyboard)" in content

    def test_manual_mode_unencrypted_root_does_not_insert_encrypt(self, tmp_path: Path) -> None:
        """A manual-mode root that isn't itself encrypted (e.g. only /home
        is) must not spuriously get the "encrypt" hook."""
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target,
            {
                "partition_mode": "manual",
                "partition_assignments": [
                    {"path": "/dev/sda2", "mountpoint": "/"},
                    {"path": "/dev/mapper/crypthome", "mountpoint": "/home"},
                ],
            },
        )

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "encrypt" not in content.split("HOOKS=(")[1].split(")")[0].split()

    def test_inserts_resume_after_filesystems_when_hibernate_selected(
        self, tmp_path: Path
    ) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {"swap_strategy": "hibernate"})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev microcode modconf block filesystems resume keyboard)" in content

    def test_inserts_resume_for_an_unencrypted_swap_partition(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode block filesystems fsck)")
        swap = {"path": "/dev/sdx3", "raw_path": "/dev/sdx3", "mountpoint": "swap", "fstype": "swap"}
        result = SystemConfigJob()._configure_mkinitcpio_hooks(
            target, {"swap_strategy": "none", "partition_assignments": [swap]}
        )
        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev microcode block filesystems resume fsck)" in content

    def test_noop_when_hooks_already_correct(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
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
            tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)", "FILES=()"
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
            "HOOKS=(base udev microcode modconf block filesystems keyboard)",
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
            "HOOKS=(base udev microcode modconf block filesystems keyboard)",
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
            tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)", "FILES=()"
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "FILES=()" in content

    def test_combines_with_encrypt_hook_insertion(self, tmp_path: Path) -> None:
        target = _write_conf_with_files(
            tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)", "FILES=()"
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target,
            {"partition_assignments": [ENC_ROOT], "luks_keyfile_path": "/etc/cryptsetup-keys.d/root.key"},
        )

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev microcode modconf block encrypt filesystems keyboard)" in content
        assert "FILES=(/etc/cryptsetup-keys.d/root.key)" in content


class TestAddBtrfsModule:
    """Btrfs is a kernel module, not built-in - needs adding to MODULES=
    for the initramfs to be able to mount a Btrfs root."""

    def test_adds_btrfs_module_when_filesystem_is_btrfs(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(target, {"filesystem": "btrfs"})

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "MODULES=(btrfs)" in content

    def test_noop_when_filesystem_is_not_btrfs(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
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
            "HOOKS=(base udev microcode modconf block filesystems keyboard)\n"
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
            "HOOKS=(base udev microcode modconf block filesystems keyboard)\n"
            'COMPRESSION="zstd"\n'
        )
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(str(target_root), {"filesystem": "btrfs"})

        assert result.success is True
        content = conf_path.read_text()
        assert "MODULES=(i915 btrfs)" in content

    def test_combines_with_encrypt_hook_insertion(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev microcode modconf block filesystems keyboard)")
        job = SystemConfigJob()

        result = job._configure_mkinitcpio_hooks(
            target, {"partition_assignments": [ENC_ROOT], "filesystem": "btrfs"}
        )

        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev microcode modconf block encrypt filesystems keyboard)" in content
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

        result = job._check_kernel_readable(str(target_root))

        assert result.success is False
        assert result.error_code == 106
        assert "initramfs-linux.img" in result.message

    def test_recovers_the_kernel_matching_the_target_modules(self, tmp_path: Path) -> None:
        """Regression: the kernel came from the live boot medium, whose version need not match
        the extracted squashfs's modules (an explicit squashfs_path on another medium); mkinitcpio
        then failed with "'/lib/modules/<live version>' is not a valid kernel module directory"."""
        target_root = tmp_path / "target"
        (target_root / "boot").mkdir(parents=True)
        modules = target_root / "usr" / "lib" / "modules" / "7.2.10-arch1-1"
        modules.mkdir(parents=True)
        (modules / "vmlinuz").write_bytes(b"target kernel")
        (modules / "pkgbase").write_text("linux\n")

        result = SystemConfigJob()._check_kernel_readable(str(target_root))

        assert result.success is True
        assert (target_root / "boot" / "vmlinuz-linux").read_bytes() == b"target kernel"

    def test_recovers_every_installed_kernel(self, tmp_path: Path) -> None:
        target_root = tmp_path / "target"
        (target_root / "boot").mkdir(parents=True)
        for version, pkgbase in (("7.2.10-arch1-1", "linux"), ("6.18.9-1-lts", "linux-lts")):
            modules = target_root / "usr" / "lib" / "modules" / version
            modules.mkdir(parents=True)
            (modules / "vmlinuz").write_bytes(pkgbase.encode())
            (modules / "pkgbase").write_text(pkgbase)

        assert SystemConfigJob()._check_kernel_readable(str(target_root)).success is True

        boot = target_root / "boot"
        assert (boot / "vmlinuz-linux").read_bytes() == b"linux"
        assert (boot / "vmlinuz-linux-lts").read_bytes() == b"linux-lts"


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


def test_blkid_partuuid_has_timeout() -> None:
    import subprocess

    with patch(
        "xen.jobs.systemconfig.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["blkid"], 30),
    ) as mock_run:
        assert SystemConfigJob()._blkid_partuuid("/dev/sda3") is None
    assert mock_run.call_args.kwargs.get("timeout")


def test_snapshots_and_systemconfig_share_the_hooks_regex() -> None:
    from xen.jobs import snapshots, systemconfig

    assert snapshots.HOOKS_LINE is systemconfig.HOOKS_LINE


GENFSTAB_WITH_ENCRYPTED_SWAP = """\
# /dev/mapper/cryptroot
UUID=1111-root	/         	ext4      	rw,relatime	0 1

# /dev/vda1
UUID=AB12-CD34      	/boot/efi 	vfat      	rw,relatime	0 2

# /dev/mapper/cryptswap
UUID=2222-install-time-swap	none      	swap      	defaults  	0 0

# /dev/vda5
UUID=3333-plain-swap	none      	swap      	defaults  	0 0

"""
CRYPT_SWAP = {"path": "/dev/mapper/cryptswap", "raw_path": "/dev/vda4", "mountpoint": "swap",
              "fstype": "swap"}  # fmt: skip


class TestGenerateFstab:
    def _run(self, tmp_path: Path, output: str, selections: dict) -> str:
        (tmp_path / "etc").mkdir()
        with patch("xen.jobs.systemconfig.subprocess.run") as run:
            run.return_value = Mock(stdout=output, returncode=0)
            result = SystemConfigJob()._generate_fstab(str(tmp_path), selections)
        assert result.success is True
        return (tmp_path / "etc" / "fstab").read_text()

    def test_drops_genfstab_entry_of_an_encrypted_swap_mapper(self, tmp_path: Path) -> None:
        fstab = self._run(
            tmp_path, GENFSTAB_WITH_ENCRYPTED_SWAP, {"partition_assignments": [CRYPT_SWAP]}
        )
        assert "2222-install-time-swap" not in fstab
        assert "# /dev/mapper/cryptswap" not in fstab
        assert "UUID=1111-root" in fstab and "UUID=AB12-CD34" in fstab
        assert "UUID=3333-plain-swap" in fstab

    def test_drops_entry_named_by_the_mappers_dm_node(self, tmp_path: Path) -> None:
        output = GENFSTAB_WITH_ENCRYPTED_SWAP.replace("# /dev/mapper/cryptswap", "# /dev/dm-2")
        real = {"/dev/mapper/cryptswap": "/dev/dm-2"}
        with patch("xen.jobs.systemconfig.os.path.realpath", side_effect=lambda p: real.get(p, p)):
            fstab = self._run(tmp_path, output, {"partition_assignments": [CRYPT_SWAP]})
        assert "2222-install-time-swap" not in fstab and "UUID=3333-plain-swap" in fstab

    @patch("xen.jobs.systemconfig.SystemConfigJob._blkid_partuuid", return_value="pu-4")
    def test_one_mapper_swap_line_after_crypttab(self, _pu: Mock, tmp_path: Path) -> None:
        selections = {"partition_assignments": [CRYPT_SWAP]}
        self._run(tmp_path, GENFSTAB_WITH_ENCRYPTED_SWAP, selections)
        SystemConfigJob()._generate_crypttab(str(tmp_path), selections)
        swaps = [
            line for line in (tmp_path / "etc" / "fstab").read_text().splitlines()
            if line.strip() and not line.startswith("#") and line.split()[2] == "swap"
        ]  # fmt: skip
        assert [line.split()[0] for line in swaps] == [
            "UUID=3333-plain-swap",
            "/dev/mapper/cryptswap",
        ]


class TestMicrocodeHook:
    def test_adds_microcode_right_after_autodetect(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev autodetect modconf block filesystems)")
        result = SystemConfigJob()._configure_mkinitcpio_hooks(target, {})
        assert result.success is True
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev autodetect microcode modconf block filesystems)" in content

    def test_adds_microcode_after_udev_without_autodetect(self, tmp_path: Path) -> None:
        target = _write_conf(tmp_path, "HOOKS=(base udev modconf kms block encrypt filesystems keyboard)")
        SystemConfigJob()._configure_mkinitcpio_hooks(target, {})
        content = (Path(target) / "etc" / "mkinitcpio.conf").read_text()
        assert "HOOKS=(base udev microcode modconf kms block encrypt filesystems keyboard)" in content

    def test_keeps_an_existing_microcode_hook(self, tmp_path: Path) -> None:
        line = "HOOKS=(base microcode udev autodetect modconf block filesystems)"
        target = _write_conf(tmp_path, line)
        result = SystemConfigJob()._configure_mkinitcpio_hooks(target, {})
        assert "already correct" in result.message
        assert line in (Path(target) / "etc" / "mkinitcpio.conf").read_text()


def test_restores_the_missing_kernel_when_another_one_is_present(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    boot = target_root / "boot"
    boot.mkdir(parents=True)
    (boot / "vmlinuz-linux").write_bytes(b"linux")
    for version, pkgbase in (("7.2.10-arch1-1", "linux"), ("6.18.9-1-lts", "linux-lts")):
        modules = target_root / "usr" / "lib" / "modules" / version
        modules.mkdir(parents=True)
        (modules / "vmlinuz").write_bytes(f"{pkgbase} from modules".encode())
        (modules / "pkgbase").write_text(pkgbase)

    assert SystemConfigJob()._check_kernel_readable(str(target_root)).success is True

    assert (boot / "vmlinuz-linux").read_bytes() == b"linux"
    assert (boot / "vmlinuz-linux-lts").read_bytes() == b"linux-lts from modules"

"""Unit tests for UsersJob."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from xen.jobs.base import JobContext, JobResult, JobStatus
    from xen.jobs.users import UsersJob

    HAS_USERS_JOB = True
except ImportError:
    HAS_USERS_JOB = False

# Skip entire module if xen is not available
pytestmark = pytest.mark.skipif(not HAS_USERS_JOB, reason="UsersJob not available")


@pytest.fixture(autouse=True)
def _no_real_chroot() -> object:
    """Safety net: nothing here may run a real arch-chroot on the host."""
    with patch("xen.jobs.users.subprocess.run", return_value=MagicMock(returncode=0)):
        yield


class TestUsersJob:
    """Tests for UsersJob implementation."""

    def test_init_defaults(self) -> None:
        """UsersJob should have correct defaults."""
        job = UsersJob()
        assert job.name == "users"
        assert job.description == "User account and hostname configuration"
        assert job.status == JobStatus.PENDING

    def test_estimate_duration(self) -> None:
        """estimate_duration should return reasonable value."""
        job = UsersJob()
        duration = job.estimate_duration()
        assert duration == 10


class TestUsernameValidation:
    """Tests for username validation logic."""

    def test_valid_usernames(self) -> None:
        """Valid usernames should pass validation."""
        job = UsersJob()

        valid_usernames = [
            "john",
            "john_doe",
            "john-doe",
            "john123",
            "_system",
            "a",
            "user_name_123",
        ]

        for username in valid_usernames:
            assert job._validate_username(username), f"'{username}' should be valid"

    def test_invalid_usernames(self) -> None:
        """Invalid usernames should fail validation."""
        job = UsersJob()

        invalid_usernames = [
            "",  # empty
            "John",  # uppercase
            "123user",  # starts with digit
            "-user",  # starts with hyphen
            "user name",  # contains space
            "user@name",  # invalid character
            "a" * 33,  # too long (>32 chars)
            "User",  # uppercase
        ]

        for username in invalid_usernames:
            assert not job._validate_username(username), f"'{username}' should be invalid"


class TestHostnameValidation:
    """Tests for hostname validation logic."""

    def test_valid_hostnames(self) -> None:
        """Valid hostnames should pass validation."""
        job = UsersJob()

        valid_hostnames = [
            "my-computer",
            "server01",
            "web-server-123",
            "a",
            "srv",
        ]

        for hostname in valid_hostnames:
            assert job._validate_hostname(hostname), f"'{hostname}' should be valid"

    def test_invalid_hostnames(self) -> None:
        """Invalid hostnames should fail validation."""
        job = UsersJob()

        invalid_hostnames = [
            "",  # empty
            "localhost",  # reserved (already resolves to 127.0.0.1)
            "-hostname",  # starts with hyphen
            "hostname-",  # ends with hyphen
            "host name",  # contains space
            "HOST",  # uppercase
            "host_name",  # underscore not allowed
            "a" * 254,  # too long (>253 chars)
            "a" * 64,  # label too long (>63 chars)
        ]

        for hostname in invalid_hostnames:
            assert not job._validate_hostname(hostname), f"'{hostname}' should be invalid"


class TestValidateMethod:
    """Tests for the validate() method."""

    def test_validate_missing_username(self) -> None:
        """validate should fail if username is missing."""
        job = UsersJob()
        context = JobContext(
            selections={
                "password": "secret123",
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 10
        assert "Username is required" in result.message

    def test_validate_invalid_username(self) -> None:
        """validate should fail if username is invalid."""
        job = UsersJob()
        context = JobContext(
            selections={
                "username": "InvalidUser",  # uppercase
                "password": "secret123",
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 11
        assert "Invalid username" in result.message

    def test_validate_reserved_username_nobody(self) -> None:
        """validate should reject the reserved 'nobody' username (nixos-install
        assertion: exactly one of isSystemUser/isNormalUser)."""
        job = UsersJob()
        context = JobContext(selections={"username": "nobody", "password": "secret123"})

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 14
        assert "reserved" in result.message.lower()

    def test_validate_reserved_username_root(self) -> None:
        """validate should reject the reserved 'root' username."""
        job = UsersJob()
        context = JobContext(selections={"username": "root", "password": "secret123"})

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 14

    def test_validate_reserved_username_liveuser(self) -> None:
        """The live ISO's passwordless polkit rule matches 'liveuser' by name."""
        context = JobContext(selections={"username": "liveuser", "password": "secret123"})

        result = UsersJob().validate(context)

        assert result.success is False
        assert result.error_code == 14

    def test_validate_non_reserved_username_ok(self) -> None:
        """A normal username passes (control for the reserved-name check)."""
        job = UsersJob()
        context = JobContext(selections={"username": "gamer", "password": "secret123"})

        assert job.validate(context).success is True

    def test_validate_missing_password(self) -> None:
        """validate should fail if password is missing."""
        job = UsersJob()
        context = JobContext(
            selections={
                "username": "john",
                "password": "",  # empty
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 12
        assert "Password is required" in result.message

    def test_validate_invalid_hostname(self) -> None:
        """validate should fail if hostname is invalid."""
        job = UsersJob()
        context = JobContext(
            selections={
                "username": "john",
                "password": "secret123",
                "hostname": "-invalid",  # starts with hyphen
            }
        )

        result = job.validate(context)

        assert result.success is False
        assert result.error_code == 13
        assert "Invalid hostname" in result.message

    def test_validate_success(self) -> None:
        """validate should succeed with valid configuration."""
        job = UsersJob()
        context = JobContext(
            selections={
                "username": "john",
                "password": "secret123",
                "hostname": "mypc",
            }
        )

        result = job.validate(context)

        assert result.success is True
        assert "valid" in result.message.lower()


class TestCreateUser:
    """Tests for _create_user() method."""

    @patch("xen.jobs.users.subprocess.run")
    @patch("xen.jobs.users.Path")
    def test_create_user_success(self, mock_path: MagicMock, mock_subprocess: MagicMock) -> None:
        """_create_user should execute useradd command correctly."""
        job = UsersJob()

        # Mock group file to check for wheel/sudo groups
        mock_group_file = MagicMock()
        mock_group_file.exists.return_value = True
        mock_group_file.read_text.return_value = "wheel:x:998:\n"
        mock_path.return_value.__truediv__.return_value = mock_group_file

        # Mock subprocess success
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = job._create_user(
            target_root="/mnt",
            username="john",
            fullname="John Doe",
            shell="/bin/bash",
        )

        assert result.success is True
        # useradd, then chmod 700 on the new home directory.
        assert mock_subprocess.call_count == 2

        # Verify command structure
        cmd = mock_subprocess.call_args_list[0][0][0]
        assert "arch-chroot" in cmd
        assert "/mnt" in cmd
        assert "useradd" in cmd
        assert "-m" in cmd  # create home
        assert "john" in cmd
        assert "/bin/bash" in cmd

        chmod_cmd = mock_subprocess.call_args_list[1][0][0]
        assert chmod_cmd == ["arch-chroot", "/mnt", "chmod", "700", "/home/john"]

    @patch("xen.jobs.users.subprocess.run")
    @patch("xen.jobs.users.Path")
    def test_create_user_with_fullname(
        self, mock_path: MagicMock, mock_subprocess: MagicMock
    ) -> None:
        """_create_user should include GECOS field if fullname provided."""
        job = UsersJob()

        mock_group_file = MagicMock()
        mock_group_file.exists.return_value = True
        mock_group_file.read_text.return_value = "wheel:x:998:\n"
        mock_path.return_value.__truediv__.return_value = mock_group_file

        mock_subprocess.return_value = MagicMock(returncode=0)

        result = job._create_user(
            target_root="/mnt",
            username="john",
            fullname="John Doe",
            shell="/bin/bash",
        )

        assert result.success is True

        # Verify GECOS field is included
        cmd = mock_subprocess.call_args_list[0][0][0]
        assert "-c" in cmd
        assert "John Doe" in cmd

    @patch("xen.jobs.users.subprocess.run")
    @patch("xen.jobs.users.Path")
    def test_create_user_admin_groups(
        self, mock_path: MagicMock, mock_subprocess: MagicMock
    ) -> None:
        """_create_user should always grant the wheel group (no admin toggle)."""
        job = UsersJob()

        mock_group_file = MagicMock()
        mock_group_file.exists.return_value = True
        mock_group_file.read_text.return_value = "wheel:x:998:\n"
        mock_path.return_value.__truediv__.return_value = mock_group_file

        mock_subprocess.return_value = MagicMock(returncode=0)

        result = job._create_user(
            target_root="/mnt",
            username="admin",
            fullname="",
            shell="/bin/bash",
        )

        assert result.success is True

        # Verify wheel group is in groups
        cmd = mock_subprocess.call_args_list[0][0][0]
        groups_idx = cmd.index("-G") + 1
        groups = cmd[groups_idx]
        assert "wheel" in groups

    @patch("xen.jobs.users.subprocess.run")
    @patch("xen.jobs.users.Path")
    def test_create_user_failure(self, mock_path: MagicMock, mock_subprocess: MagicMock) -> None:
        """_create_user should handle subprocess failures."""
        job = UsersJob()

        mock_group_file = MagicMock()
        mock_group_file.exists.return_value = True
        mock_group_file.read_text.return_value = "wheel:x:998:\n"
        mock_path.return_value.__truediv__.return_value = mock_group_file

        # Simulate useradd failure with CalledProcessError
        mock_subprocess.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["useradd"], stderr="User already exists"
        )

        result = job._create_user(
            target_root="/mnt",
            username="john",
            fullname="",
            shell="/bin/bash",
        )

        assert result.success is False


class TestEnsureSudoAccess:
    """Tests for _ensure_sudo_access() method."""

    @patch("xen.jobs.users.subprocess.run")
    def test_valid_dropin_kept(self, mock_subprocess: MagicMock, tmp_path: Path) -> None:
        """Drop-in is written and kept when visudo validates it."""
        job = UsersJob()
        mock_subprocess.return_value = MagicMock(returncode=0, stderr="")

        result = job._ensure_sudo_access(str(tmp_path))

        assert result.success is True
        drop_in = tmp_path / "etc" / "sudoers.d" / "99-wheel"
        assert drop_in.exists()
        assert drop_in.read_text() == "%wheel ALL=(ALL:ALL) ALL\n"
        assert (drop_in.stat().st_mode & 0o777) == 0o440

        cmd = mock_subprocess.call_args[0][0]
        assert cmd == ["arch-chroot", str(tmp_path), "visudo", "-cf", "/etc/sudoers.d/99-wheel"]

    @patch("xen.jobs.users.subprocess.run")
    def test_dropin_requires_password_not_nopasswd(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Regression: must require a password on the installed system -
        NOPASSWD is only appropriate for the live ISO's own g_wheel rule,
        which xerolinux-final deletes on purpose."""
        job = UsersJob()
        mock_subprocess.return_value = MagicMock(returncode=0, stderr="")

        job._ensure_sudo_access(str(tmp_path))

        drop_in = tmp_path / "etc" / "sudoers.d" / "99-wheel"
        assert "NOPASSWD" not in drop_in.read_text()

    @patch("xen.jobs.users.subprocess.run")
    def test_invalid_dropin_removed(self, mock_subprocess: MagicMock, tmp_path: Path) -> None:
        """Drop-in is removed if visudo rejects it, still non-fatal."""
        job = UsersJob()
        mock_subprocess.return_value = MagicMock(returncode=1, stderr="syntax error")

        result = job._ensure_sudo_access(str(tmp_path))

        assert result.success is True
        drop_in = tmp_path / "etc" / "sudoers.d" / "99-wheel"
        assert not drop_in.exists()

    @patch("xen.jobs.users.subprocess.run")
    def test_validation_missing_arch_chroot_keeps_dropin(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """Missing arch-chroot skips validation but keeps the drop-in, non-fatal."""
        job = UsersJob()
        mock_subprocess.side_effect = FileNotFoundError("arch-chroot not found")

        result = job._ensure_sudo_access(str(tmp_path))

        assert result.success is True
        drop_in = tmp_path / "etc" / "sudoers.d" / "99-wheel"
        assert drop_in.exists()

    @patch("xen.jobs.users.subprocess.run")
    def test_validation_timeout_keeps_dropin(
        self, mock_subprocess: MagicMock, tmp_path: Path
    ) -> None:
        """visudo timing out skips validation but keeps the drop-in, non-fatal."""
        job = UsersJob()
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="visudo", timeout=10)

        result = job._ensure_sudo_access(str(tmp_path))

        assert result.success is True
        drop_in = tmp_path / "etc" / "sudoers.d" / "99-wheel"
        assert drop_in.exists()

    @patch("xen.jobs.users.Path")
    def test_write_failure_is_non_fatal(self, mock_path: MagicMock) -> None:
        """OSError while writing the drop-in is swallowed, non-fatal."""
        job = UsersJob()

        mock_sudoers_d = MagicMock()
        mock_sudoers_d.mkdir.side_effect = OSError("read-only filesystem")
        mock_path.return_value.__truediv__.return_value = mock_sudoers_d

        result = job._ensure_sudo_access("/mnt")

        assert result.success is True


class TestSetPassword:
    """Tests for _set_password() method."""

    @patch("xen.jobs.users.subprocess.run")
    def test_set_password_success(self, mock_subprocess: MagicMock) -> None:
        """_set_password should use chpasswd with stdin."""
        job = UsersJob()

        mock_subprocess.return_value = MagicMock(returncode=0)

        result = job._set_password(
            target_root="/mnt",
            username="john",
            password="secret123",
        )

        assert result.success is True
        mock_subprocess.assert_called_once()

        # Verify chpasswd is called
        cmd = mock_subprocess.call_args[0][0]
        assert "arch-chroot" in cmd
        assert "/mnt" in cmd
        assert "chpasswd" in cmd

        # Verify password is passed via stdin (not command line)
        kwargs = mock_subprocess.call_args[1]
        assert kwargs.get("input") == "john:secret123"
        assert kwargs.get("text") is True

    @patch("xen.jobs.users.subprocess.run")
    def test_set_password_never_logged(
        self, mock_subprocess: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SECURITY: _set_password should never log the password."""
        job = UsersJob()

        mock_subprocess.return_value = MagicMock(returncode=0)

        # Set a distinctive password
        test_password = "SuperSecretPassword123!"

        result = job._set_password(
            target_root="/mnt",
            username="john",
            password=test_password,
        )

        assert result.success is True

        # CRITICAL: Verify password is NOT in any log messages
        for record in caplog.records:
            assert test_password not in record.message, "Password found in log!"
            assert test_password not in str(record.args), "Password found in log args!"

    @patch("xen.jobs.users.subprocess.run")
    def test_set_password_failure_no_leak(
        self, mock_subprocess: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SECURITY: _set_password should not leak password on error."""
        job = UsersJob()

        test_password = "SuperSecretPassword123!"

        # Simulate chpasswd failure
        import subprocess

        mock_subprocess.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["chpasswd"], stderr="chpasswd failed"
        )

        result = job._set_password(
            target_root="/mnt",
            username="john",
            password=test_password,
        )

        assert result.success is False

        # CRITICAL: Verify password is NOT in error messages or logs
        assert test_password not in result.message
        for record in caplog.records:
            assert test_password not in record.message
            assert test_password not in str(record.args)


class TestSetHostname:
    """Tests for _set_hostname() method."""

    def test_set_hostname_success(self) -> None:
        """_set_hostname should write /etc/hostname and /etc/hosts."""
        import tempfile

        job = UsersJob()

        # Use a temporary directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create /etc directory
            etc_dir = Path(tmpdir) / "etc"
            etc_dir.mkdir()

            result = job._set_hostname(tmpdir, "mypc")

            assert result.success is True

            # Verify /etc/hostname was written
            hostname_file = etc_dir / "hostname"
            assert hostname_file.exists()
            hostname_content = hostname_file.read_text()
            assert "mypc" in hostname_content

            # Verify /etc/hosts was written
            hosts_file = etc_dir / "hosts"
            assert hosts_file.exists()
            hosts_content = hosts_file.read_text()
            assert "mypc" in hosts_content
            assert "127.0.1.1" in hosts_content

    @patch("xen.jobs.users.Path")
    def test_set_hostname_failure(self, mock_path_class: MagicMock) -> None:
        """_set_hostname should handle file write errors."""
        job = UsersJob()

        # Create a mock for the hostname file that raises OSError on write_text
        mock_hostname_file = MagicMock()
        mock_hostname_file.write_text.side_effect = OSError("Permission denied")

        # Mock the Path chain: Path(target_root) / "etc" / "hostname"
        mock_root = MagicMock()
        mock_etc = MagicMock()
        mock_etc.__truediv__.return_value = mock_hostname_file
        mock_root.__truediv__.return_value = mock_etc
        mock_path_class.return_value = mock_root

        result = job._set_hostname("/mnt", "mypc")

        assert result.success is False
        assert result.error_code == 24


class TestRunMethod:
    """Tests for the run() method."""

    @patch("xen.jobs.users.UsersJob._set_hostname")
    @patch("xen.jobs.users.UsersJob._set_password")
    @patch("xen.jobs.users.UsersJob._create_user")
    def test_run_success_full_workflow(
        self,
        mock_create: MagicMock,
        mock_password: MagicMock,
        mock_hostname: MagicMock,
    ) -> None:
        """run should execute full workflow successfully."""
        job = UsersJob()

        # Mock all operations as successful
        mock_create.return_value = JobResult.ok("User created")
        mock_password.return_value = JobResult.ok("Password set")
        mock_hostname.return_value = JobResult.ok("Hostname set")

        context = JobContext(
            target_root="/mnt",
            selections={
                "username": "john",
                "password": "secret123",
                "fullname": "John Doe",
                "hostname": "mypc",
                "shell": "/bin/bash",
            },
        )
        context.on_progress = MagicMock()

        result = job.run(context)

        assert result.success is True
        assert "john" in result.message

        # Verify all steps were called
        mock_create.assert_called_once()
        mock_password.assert_called_once()
        mock_hostname.assert_called_once()

        # Verify progress was reported
        assert context.on_progress.call_count >= 4

    @patch("xen.jobs.users.UsersJob._create_user")
    def test_run_create_user_failure(self, mock_create: MagicMock) -> None:
        """run should stop if user creation fails."""
        job = UsersJob()

        # Simulate user creation failure
        mock_create.return_value = JobResult.fail("User creation failed", error_code=22)

        context = JobContext(
            selections={
                "username": "john",
                "password": "secret123",
            }
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 22

    @patch("xen.jobs.users.UsersJob._set_password")
    @patch("xen.jobs.users.UsersJob._create_user")
    def test_run_password_failure(self, mock_create: MagicMock, mock_password: MagicMock) -> None:
        """run should stop if password setting fails."""
        job = UsersJob()

        mock_create.return_value = JobResult.ok("User created")
        mock_password.return_value = JobResult.fail("Password failed", error_code=23)

        context = JobContext(
            selections={
                "username": "john",
                "password": "secret123",
            }
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 23

    @patch("xen.jobs.users.UsersJob._set_password")
    @patch("xen.jobs.users.UsersJob._create_user")
    def test_run_without_hostname(self, mock_create: MagicMock, mock_password: MagicMock) -> None:
        """run should skip hostname if not provided."""
        job = UsersJob()

        mock_create.return_value = JobResult.ok("User created")
        mock_password.return_value = JobResult.ok("Password set")

        context = JobContext(
            selections={
                "username": "john",
                "password": "secret123",
                # No hostname provided
            }
        )

        result = job.run(context)

        assert result.success is True
        assert result.data["hostname"] == "not_set"

    def test_run_validation_failure(self) -> None:
        """run should fail if validation fails."""
        job = UsersJob()

        context = JobContext(
            selections={
                "username": "InvalidUser",  # uppercase - invalid
                "password": "secret123",
            }
        )

        result = job.run(context)

        assert result.success is False
        assert result.error_code == 11  # validation error code


class TestUnderscoreFirstPosition:
    """
    Lot 2 : un underscore en première position est autorisé.

    UsersJob.USERNAME_PATTERN vaut ^[a-z_][a-z0-9_-]*$ et le QML a été aligné
    sur ce motif. On garde un test explicite documentant ce choix.
    """

    def test_underscore_first_is_valid(self) -> None:
        """Le job doit accepter un username commençant par underscore."""
        job = UsersJob()
        assert job._validate_username("_system") is True

        context = JobContext(
            selections={
                "username": "_system",
                "password": "secret123",
            }
        )
        result = job.validate(context)
        assert result.success is True


class TestPasswordNeverInLogs:
    """SECURITY : le mot de passe ne doit jamais apparaître dans les logs."""

    @patch("xen.jobs.users.subprocess.run")
    def test_password_never_appears_in_logs(
        self, mock_subprocess: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        Forcer un CalledProcessError sur chpasswd et vérifier qu'aucun message
        loggué ne contient le mot de passe, même si chpasswd renvoyait la ligne
        'username:password' dans stderr.
        """
        import logging
        import subprocess

        caplog.set_level(logging.DEBUG)

        job = UsersJob()
        test_password = "SuperSecretPassword123!"
        leaky_stderr = f"chpasswd: line: john:{test_password}"

        # _create_user réussit, _set_password échoue avec un stderr qui
        # contiendrait le mot de passe si on le logguait.
        mock_subprocess.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["arch-chroot", "/mnt", "chpasswd"],
            stderr=leaky_stderr,
        )

        result = job._set_password(
            target_root="/mnt",
            username="john",
            password=test_password,
        )

        assert result.success is False
        assert result.error_code == 23
        assert test_password not in result.message

        for record in caplog.records:
            assert test_password not in record.getMessage()
            assert test_password not in record.message
            assert test_password not in str(record.args)


class TestRootPassword:
    """The live ISO's /etc/shadow is copied verbatim: root must be set or locked."""

    def _run(self, **selections: object) -> tuple[JobResult, MagicMock, MagicMock]:
        job = UsersJob()
        context = JobContext(
            target_root="/mnt",
            selections={"username": "john", "password": "userpw", **selections},
        )
        with (
            patch.object(UsersJob, "_create_user", return_value=JobResult.ok()),
            patch.object(UsersJob, "_set_password", return_value=JobResult.ok()) as set_pw,
            patch.object(UsersJob, "_ensure_sudo_access", return_value=JobResult.ok()),
            patch("xen.jobs.users.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = job.run(context)
        return result, set_pw, mock_run

    def test_same_as_user_sets_root_to_user_password(self) -> None:
        result, set_pw, _ = self._run(root_same_as_user=True, root_password="ignored")
        assert result.success is True
        assert set_pw.call_args_list[-1].kwargs == {
            "target_root": "/mnt",
            "username": "root",
            "password": "userpw",
        }

    def test_separate_root_password_is_applied(self) -> None:
        result, set_pw, _ = self._run(root_same_as_user=False, root_password="rootpw")
        assert result.success is True
        assert set_pw.call_args_list[-1].kwargs["username"] == "root"
        assert set_pw.call_args_list[-1].kwargs["password"] == "rootpw"

    def test_no_root_password_locks_root(self) -> None:
        result, set_pw, mock_run = self._run(root_same_as_user=False, root_password="")
        assert result.success is True
        assert set_pw.call_count == 1
        cmds = [c.args[0] for c in mock_run.call_args_list]
        assert ["arch-chroot", "/mnt", "passwd", "-l", "root"] in cmds

    def test_lock_failure_fails_the_job(self) -> None:
        job = UsersJob()
        with patch(
            "xen.jobs.users.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["passwd"], stderr="nope"),
        ):
            result = job._lock_root("/mnt")
        assert result.success is False


class TestCredentialValidation:
    @pytest.mark.parametrize(
        "selections",
        [
            {"password": "bad\npw"},
            {"password": "bad\rpw"},
            {"root_same_as_user": False, "root_password": "bad\npw"},
            {"fullname": "John:Doe"},
            {"fullname": "John\nDoe"},
        ],
    )
    def test_rejects_chpasswd_and_passwd_field_separators(self, selections: dict) -> None:
        context = JobContext(selections={"username": "john", "password": "ok", **selections})
        result = UsersJob().validate(context)
        assert result.success is False
        assert "bad" not in result.message


class TestConfigDefaults:
    def test_default_groups_from_config_override_builtin_list(self, tmp_path: Path) -> None:
        (tmp_path / "etc").mkdir()
        (tmp_path / "etc" / "group").write_text("wheel:x:1:\nvideo:x:2:\nlp:x:3:\n")
        job = UsersJob({"default_groups": ["wheel", "video", "missing"]})
        with patch("xen.jobs.users.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            job._create_user(str(tmp_path), "john", "", "/bin/bash")
        useradd = mock_run.call_args_list[0].args[0]
        assert useradd[useradd.index("-G") + 1] == "wheel,video"

    def test_default_shell_from_config_used_when_no_selection(self) -> None:
        job = UsersJob({"default_shell": "/usr/bin/zsh"})
        context = JobContext(
            target_root="/mnt", selections={"username": "john", "password": "pw"}
        )
        with (
            patch.object(UsersJob, "_create_user", return_value=JobResult.ok()) as create,
            patch.object(UsersJob, "_set_password", return_value=JobResult.ok()),
            patch.object(UsersJob, "_configure_root_password", return_value=JobResult.ok()),
            patch.object(UsersJob, "_ensure_sudo_access", return_value=JobResult.ok()),
        ):
            job.run(context)
        assert create.call_args.kwargs["shell"] == "/usr/bin/zsh"


class TestUsersTimeouts:
    def test_chpasswd_timeout_fails_without_leaking(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch(
            "xen.jobs.users.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["chpasswd"], 1),
        ) as mock_run:
            result = UsersJob()._set_password("/mnt", "john", "s3cret-pw")
        assert result.success is False
        assert mock_run.call_args.kwargs.get("timeout")
        assert "s3cret-pw" not in caplog.text
        assert "s3cret-pw" not in result.message

    def test_useradd_timeout_fails(self, tmp_path: Path) -> None:
        with patch(
            "xen.jobs.users.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["useradd"], 1),
        ) as mock_run:
            result = UsersJob()._create_user(str(tmp_path), "john", "", "/bin/bash")
        assert result.success is False
        assert mock_run.call_args.kwargs.get("timeout")

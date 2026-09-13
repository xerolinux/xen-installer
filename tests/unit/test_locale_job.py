"""Unit tests for LocaleJob."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

try:
    from xen.jobs.base import JobContext, JobStatus
    from xen.jobs.locale import LocaleJob

    HAS_LOCALE_JOB = True
except ImportError:
    HAS_LOCALE_JOB = False

# Skip entire module if xen locale job is not available
pytestmark = pytest.mark.skipif(not HAS_LOCALE_JOB, reason="LocaleJob not available")


# =============================================================================
# LocaleJob Initialization Tests
# =============================================================================


class TestLocaleJobInit:
    """Tests for LocaleJob initialization."""

    def test_init_defaults(self) -> None:
        """LocaleJob should have correct defaults."""
        job = LocaleJob()
        assert job.name == "locale"
        assert job.description == "System locale, timezone and keyboard configuration"
        assert job.status == JobStatus.PENDING

    def test_init_with_config(self) -> None:
        """LocaleJob should accept configuration."""
        config = {"default_locale": "fr_FR.UTF-8"}
        job = LocaleJob(config)
        assert job._config == config

    def test_common_locales_defined(self) -> None:
        """LocaleJob should define common locales."""
        assert len(LocaleJob.COMMON_LOCALES) > 0
        assert "en_US.UTF-8" in LocaleJob.COMMON_LOCALES
        assert "fr_FR.UTF-8" in LocaleJob.COMMON_LOCALES

    def test_common_keymaps_defined(self) -> None:
        """LocaleJob should define common keymaps."""
        assert len(LocaleJob.COMMON_KEYMAPS) > 0
        assert "us" in LocaleJob.COMMON_KEYMAPS
        assert "fr" in LocaleJob.COMMON_KEYMAPS


# =============================================================================
# Validation Tests
# =============================================================================


class TestLocaleValidation:
    """Tests for locale validation."""

    def test_validate_locale_valid_utf8(self) -> None:
        """Valid UTF-8 locales should pass validation."""
        job = LocaleJob()
        assert job._validate_locale("en_US.UTF-8") is True
        assert job._validate_locale("fr_FR.UTF-8") is True
        assert job._validate_locale("de_DE.UTF8") is True

    def test_validate_locale_invalid_format(self) -> None:
        """Invalid locale formats should fail validation."""
        job = LocaleJob()
        assert job._validate_locale("") is False
        assert job._validate_locale("invalid") is False
        assert job._validate_locale("en_US") is False  # Missing encoding
        assert job._validate_locale("en.UTF-8") is False  # Missing country
        assert job._validate_locale("en_U.UTF-8") is False  # Country too short

    def test_validate_timezone_valid(self) -> None:
        """Valid timezones should pass validation."""
        job = LocaleJob()
        # UTC is always available
        assert job._validate_timezone("UTC") is True

        # Test with actual system timezones if available
        zoneinfo_path = Path("/usr/share/zoneinfo")
        if zoneinfo_path.exists() and (zoneinfo_path / "Europe" / "Paris").exists():
            assert job._validate_timezone("Europe/Paris") is True

    def test_validate_timezone_invalid(self) -> None:
        """Invalid timezones should fail validation."""
        job = LocaleJob()
        assert job._validate_timezone("") is False
        assert job._validate_timezone("Invalid/Timezone") is False
        assert job._validate_timezone("NotAZone") is False

    def test_validate_keymap_valid(self) -> None:
        """Valid keymaps should pass validation."""
        job = LocaleJob()
        assert job._validate_keymap("us") is True
        assert job._validate_keymap("fr") is True
        assert job._validate_keymap("dvorak") is True
        # Alphanumeric keymaps should be accepted
        assert job._validate_keymap("custom123") is True

    def test_validate_keymap_invalid(self) -> None:
        """Invalid keymaps should fail validation."""
        job = LocaleJob()
        assert job._validate_keymap("") is False
        # Special characters should fail
        assert job._validate_keymap("fr-azerty!") is False


class TestLocaleJobValidate:
    """Tests for JobContext validation."""

    def test_validate_all_valid(self) -> None:
        """Validate should pass with all valid selections."""
        job = LocaleJob()
        context = JobContext(
            selections={
                "locale": "en_US.UTF-8",
                "timezone": "UTC",
                "keymap": "us",
            }
        )

        result = job.validate(context)
        assert result.success is True

    def test_validate_invalid_locale(self) -> None:
        """Validate should fail with invalid locale."""
        job = LocaleJob()
        context = JobContext(selections={"locale": "invalid"})

        result = job.validate(context)
        assert result.success is False
        assert "locale" in result.message.lower()
        assert result.error_code == 19

    def test_validate_invalid_timezone(self) -> None:
        """Validate should fail with invalid timezone."""
        job = LocaleJob()
        context = JobContext(selections={"timezone": "Invalid/Zone"})

        result = job.validate(context)
        assert result.success is False
        assert "timezone" in result.message.lower()

    def test_validate_invalid_keymap(self) -> None:
        """Validate should fail with invalid keymap."""
        job = LocaleJob()
        context = JobContext(selections={"keymap": "invalid!"})

        result = job.validate(context)
        assert result.success is False
        assert "keyboard" in result.message.lower()

    def test_validate_multiple_errors(self) -> None:
        """Validate should report all validation errors."""
        job = LocaleJob()
        context = JobContext(
            selections={
                "locale": "invalid",
                "timezone": "Bad/Zone",
                "keymap": "bad!",
            }
        )

        result = job.validate(context)
        assert result.success is False
        assert "errors" in result.data
        assert len(result.data["errors"]) == 3


# =============================================================================
# Timezone Discovery Tests
# =============================================================================


class TestGetAvailableTimezones:
    """Tests for timezone discovery."""

    def test_get_timezones_with_zoneinfo(self) -> None:
        """Should discover timezones from /usr/share/zoneinfo if available."""
        job = LocaleJob()
        timezones = job._get_available_timezones()

        assert len(timezones) > 0
        assert "UTC" in timezones or any("Europe" in tz for tz in timezones)

    @patch("pathlib.Path.exists")
    def test_get_timezones_fallback(self, mock_exists: Mock) -> None:
        """Should provide fallback list if zoneinfo not available."""
        mock_exists.return_value = False
        job = LocaleJob()
        timezones = job._get_available_timezones()

        assert len(timezones) > 0
        assert "UTC" in timezones
        assert "Europe/Paris" in timezones


# =============================================================================
# Configuration Tests
# =============================================================================


class TestConfigureLocale:
    """Tests for locale configuration."""

    def test_configure_locale_default(self) -> None:
        """Should configure default locale if none specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(target_root=tmpdir)

            result = job._configure_locale(context)

            assert result.success is True
            assert "en_US.UTF-8" in result.message

            # Check locale.conf was created
            locale_conf = Path(tmpdir) / "etc" / "locale.conf"
            assert locale_conf.exists()
            content = locale_conf.read_text()
            assert "LANG=en_US.UTF-8" in content

    def test_configure_locale_custom(self) -> None:
        """Should configure custom locale."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"locale": "fr_FR.UTF-8"},
            )

            result = job._configure_locale(context)

            assert result.success is True
            assert "fr_FR.UTF-8" in result.message

            # Check locale.conf was created
            locale_conf = Path(tmpdir) / "etc" / "locale.conf"
            assert locale_conf.exists()
            content = locale_conf.read_text()
            assert "LANG=fr_FR.UTF-8" in content

    def test_configure_locale_invalid(self) -> None:
        """Should fail with invalid locale."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"locale": "invalid"},
            )

            result = job._configure_locale(context)

            assert result.success is False
            assert result.error_code == 20

    def test_configure_locale_with_locale_gen(self) -> None:
        """Should update locale.gen if it exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create locale.gen with commented locale
            locale_gen = Path(tmpdir) / "etc" / "locale.gen"
            locale_gen.parent.mkdir(parents=True, exist_ok=True)
            locale_gen.write_text("#fr_FR.UTF-8 UTF-8\n#en_US.UTF-8 UTF-8\n")

            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"locale": "fr_FR.UTF-8"},
            )

            result = job._configure_locale(context)

            assert result.success is True

            # Check locale.gen was updated
            content = locale_gen.read_text()
            assert "fr_FR.UTF-8 UTF-8" in content  # Uncommented
            assert content.count("#fr_FR.UTF-8") == 0  # No commented version


class TestConfigureTimezone:
    """Tests for timezone configuration."""

    def test_configure_timezone_utc(self) -> None:
        """Should configure UTC timezone."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"timezone": "UTC"},
            )

            result = job._configure_timezone(context)

            assert result.success is True
            assert "UTC" in result.message

            # Check symlink was created
            localtime = Path(tmpdir) / "etc" / "localtime"
            assert localtime.exists() or localtime.is_symlink()

            # Check timezone file was created
            timezone_file = Path(tmpdir) / "etc" / "timezone"
            assert timezone_file.exists()
            content = timezone_file.read_text()
            assert "UTC" in content

    def test_configure_timezone_custom(self) -> None:
        """Should configure custom timezone if it exists."""
        # Only test if the timezone actually exists
        zoneinfo = Path("/usr/share/zoneinfo/Europe/Paris")
        if not zoneinfo.exists():
            pytest.skip("Europe/Paris timezone not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"timezone": "Europe/Paris"},
            )

            result = job._configure_timezone(context)

            assert result.success is True

            # Check timezone file
            timezone_file = Path(tmpdir) / "etc" / "timezone"
            assert timezone_file.exists()
            content = timezone_file.read_text()
            assert "Europe/Paris" in content

    def test_configure_timezone_invalid(self) -> None:
        """Should fail with invalid timezone."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"timezone": "Invalid/Zone"},
            )

            result = job._configure_timezone(context)

            assert result.success is False
            assert result.error_code == 22


class TestConfigureKeyboard:
    """Tests for keyboard configuration."""

    def test_configure_keyboard_default(self) -> None:
        """Should configure default US keyboard."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(target_root=tmpdir)

            result = job._configure_keyboard(context)

            assert result.success is True
            assert "us" in result.message

            # Check vconsole.conf was created
            vconsole = Path(tmpdir) / "etc" / "vconsole.conf"
            assert vconsole.exists()
            content = vconsole.read_text()
            assert "KEYMAP=us" in content

            # Check X11 keyboard config was created
            xorg_kbd = Path(tmpdir) / "etc" / "X11" / "xorg.conf.d" / "00-keyboard.conf"
            assert xorg_kbd.exists()
            content = xorg_kbd.read_text()
            assert 'Option "XkbLayout" "us"' in content

    def test_configure_keyboard_custom(self) -> None:
        """Should configure custom keyboard layout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"keymap": "fr"},
            )

            result = job._configure_keyboard(context)

            assert result.success is True
            assert "fr" in result.message

            # Check vconsole.conf
            vconsole = Path(tmpdir) / "etc" / "vconsole.conf"
            content = vconsole.read_text()
            assert "KEYMAP=fr" in content

            # Check X11 config
            xorg_kbd = Path(tmpdir) / "etc" / "X11" / "xorg.conf.d" / "00-keyboard.conf"
            content = xorg_kbd.read_text()
            assert 'Option "XkbLayout" "fr"' in content

    def test_configure_keyboard_invalid(self) -> None:
        """Should fail with invalid keyboard layout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"keymap": "invalid!"},
            )

            result = job._configure_keyboard(context)

            assert result.success is False
            assert result.error_code == 24


# =============================================================================
# Full Job Execution Tests
# =============================================================================


class TestLocaleJobRun:
    """Tests for full LocaleJob execution."""

    def test_run_with_defaults(self) -> None:
        """Should run successfully with default selections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(target_root=tmpdir)
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert "locale" in result.data
            assert "timezone" in result.data
            assert "keymap" in result.data

            # Verify progress was reported
            assert context.on_progress.called

    def test_run_with_custom_selections(self) -> None:
        """Should run successfully with custom selections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "locale": "fr_FR.UTF-8",
                    "timezone": "UTC",
                    "keymap": "fr",
                },
            )
            context.on_progress = MagicMock()

            result = job.run(context)

            assert result.success is True
            assert result.data["locale"] == "fr_FR.UTF-8"
            assert result.data["timezone"] == "UTC"
            assert result.data["keymap"] == "fr"

    def test_run_validation_fails(self) -> None:
        """Should fail if validation fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()
            context = JobContext(
                target_root=tmpdir,
                selections={"locale": "invalid"},
            )

            result = job.run(context)

            assert result.success is False
            assert result.error_code == 19

    def test_run_locale_config_fails(self) -> None:
        """Should fail if locale configuration fails."""
        job = LocaleJob()
        # Use invalid target_root to cause failure
        context = JobContext(
            target_root="/nonexistent/path",
            selections={"locale": "en_US.UTF-8"},
        )

        result = job.run(context)

        assert result.success is False

    def test_estimate_duration(self) -> None:
        """Should return reasonable duration estimate."""
        job = LocaleJob()
        duration = job.estimate_duration()
        assert duration == 15


# =============================================================================
# Integration Tests
# =============================================================================


class TestLocaleJobIntegration:
    """Integration tests for complete LocaleJob workflow."""

    def test_full_workflow(self) -> None:
        """Test complete locale configuration workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup: create locale.gen for realistic scenario
            locale_gen = Path(tmpdir) / "etc" / "locale.gen"
            locale_gen.parent.mkdir(parents=True, exist_ok=True)
            locale_gen.write_text("#en_US.UTF-8 UTF-8\n#fr_FR.UTF-8 UTF-8\n#de_DE.UTF-8 UTF-8\n")

            # Create job with full config
            job = LocaleJob({"default_locale": "fr_FR.UTF-8"})

            # Prepare context
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "locale": "fr_FR.UTF-8",
                    "timezone": "UTC",
                    "keymap": "fr",
                },
            )
            context.on_progress = MagicMock()

            # Validate first
            validation = job.validate(context)
            assert validation.success is True

            # Run the job
            result = job.run(context)
            assert result.success is True

            # Verify all configuration files were created
            assert (Path(tmpdir) / "etc" / "locale.conf").exists()
            assert (Path(tmpdir) / "etc" / "timezone").exists()
            assert (Path(tmpdir) / "etc" / "vconsole.conf").exists()
            assert (Path(tmpdir) / "etc" / "X11" / "xorg.conf.d" / "00-keyboard.conf").exists()

            # Verify content
            locale_conf = (Path(tmpdir) / "etc" / "locale.conf").read_text()
            assert "LANG=fr_FR.UTF-8" in locale_conf

            timezone_file = (Path(tmpdir) / "etc" / "timezone").read_text()
            assert "UTC" in timezone_file

            vconsole = (Path(tmpdir) / "etc" / "vconsole.conf").read_text()
            assert "KEYMAP=fr" in vconsole

    def test_partial_failure_recovery(self) -> None:
        """Test that job handles partial failures gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            job = LocaleJob()

            # Start with valid selections
            context = JobContext(
                target_root=tmpdir,
                selections={
                    "locale": "en_US.UTF-8",
                    "timezone": "UTC",
                    "keymap": "us",
                },
            )

            # First run should succeed
            result = job.run(context)
            assert result.success is True

            # Verify files exist
            assert (Path(tmpdir) / "etc" / "locale.conf").exists()
            assert (Path(tmpdir) / "etc" / "timezone").exists()

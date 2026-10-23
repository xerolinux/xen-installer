"""
Locale Job - System locale, timezone and keyboard configuration.

Configures the target system's locale settings, timezone, and keyboard layout.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from xen.i18n.messages import msg
from xen.jobs.base import BaseJob, JobContext, JobResult

logger = logging.getLogger(__name__)

_CHROOT_TIMEOUT = 120

_LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?\.[A-Za-z0-9-]+(@[A-Za-z0-9]+)?$")
_XKB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# X11 layout -> kbd console keymap, from systemd's kbd-model-map (what
# Calamares and localectl use). Layouts with no console equivalent fall back
# to "us" (the console can't render their scripts anyway).
_CONSOLE_KEYMAPS = {
    "us": "us",
    "gb": "uk",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "it": "it",
    "pt": "pt-latin1",
    "br": "br-abnt2",
    "ru": "ru",
    "jp": "jp106",
    "il": "il",
    "tr": "trq",
    "pl": "pl2",
    "nl": "nl",
    "se": "sv-latin1",
    "no": "no",
    "dk": "dk",
    "fi": "fi",
    "cz": "cz",
    "sk": "sk-qwertz",
    "hu": "hu",
    "ro": "ro",
    "gr": "gr",
    "ua": "ua-utf",
    "bg": "bg_bds-utf8",
    "rs": "sr-cy",
    "hr": "croat",
    "si": "slovene",
    "lt": "lt",
    "lv": "lv",
    "ee": "et",
    "ch": "sg",
    "be": "be-latin1",
    "ca": "cf",
    "ie": "ie",
    "is": "is-latin1",
    "latam": "la-latin1",
}
_CONSOLE_KEYMAP_VARIANTS = {
    ("us", "dvorak"): "dvorak",
    ("ch", "fr"): "fr_CH",
    ("de", "nodeadkeys"): "de-latin1-nodeadkeys",
}


class LocaleJob(BaseJob):
    """
    System locale, timezone and keyboard configuration.

    Responsibilities:
    - Configure system locale (language, character encoding)
    - Set system timezone with hardware clock sync
    - Configure console and X11 keyboard layouts
    """

    name = "locale"
    description = "System locale, timezone and keyboard configuration"

    # Comprehensive list of locales available on most Linux systems
    # Sorted by language family for better organization
    COMMON_LOCALES = [
        # English variants
        "en_US.UTF-8",
        "en_GB.UTF-8",
        "en_CA.UTF-8",
        "en_AU.UTF-8",
        "en_NZ.UTF-8",
        "en_IE.UTF-8",
        "en_ZA.UTF-8",
        "en_IN.UTF-8",
        # French variants
        "fr_FR.UTF-8",
        "fr_CA.UTF-8",
        "fr_BE.UTF-8",
        "fr_CH.UTF-8",
        "fr_LU.UTF-8",
        # German variants
        "de_DE.UTF-8",
        "de_AT.UTF-8",
        "de_CH.UTF-8",
        "de_LU.UTF-8",
        "de_LI.UTF-8",
        # Spanish variants
        "es_ES.UTF-8",
        "es_MX.UTF-8",
        "es_AR.UTF-8",
        "es_CO.UTF-8",
        "es_CL.UTF-8",
        "es_PE.UTF-8",
        "es_VE.UTF-8",
        # Italian
        "it_IT.UTF-8",
        "it_CH.UTF-8",
        # Portuguese variants
        "pt_BR.UTF-8",
        "pt_PT.UTF-8",
        # Russian and Cyrillic languages
        "ru_RU.UTF-8",
        "uk_UA.UTF-8",
        "be_BY.UTF-8",
        "bg_BG.UTF-8",
        "sr_RS.UTF-8",
        "mk_MK.UTF-8",
        # Chinese variants
        "zh_CN.UTF-8",
        "zh_TW.UTF-8",
        "zh_HK.UTF-8",
        "zh_SG.UTF-8",
        # Japanese
        "ja_JP.UTF-8",
        # Korean
        "ko_KR.UTF-8",
        # Arabic variants
        "ar_SA.UTF-8",
        "ar_EG.UTF-8",
        "ar_MA.UTF-8",
        "ar_DZ.UTF-8",
        "ar_TN.UTF-8",
        "ar_AE.UTF-8",
        # Hebrew
        "he_IL.UTF-8",
        # Persian
        "fa_IR.UTF-8",
        # Hindi and Indian languages
        "hi_IN.UTF-8",
        "bn_IN.UTF-8",
        "bn_BD.UTF-8",
        "ta_IN.UTF-8",
        "te_IN.UTF-8",
        "mr_IN.UTF-8",
        "gu_IN.UTF-8",
        "kn_IN.UTF-8",
        "ml_IN.UTF-8",
        "pa_IN.UTF-8",
        # Thai
        "th_TH.UTF-8",
        # Vietnamese
        "vi_VN.UTF-8",
        # Indonesian and Malay
        "id_ID.UTF-8",
        "ms_MY.UTF-8",
        # Turkish
        "tr_TR.UTF-8",
        # Greek
        "el_GR.UTF-8",
        "el_CY.UTF-8",
        # Polish
        "pl_PL.UTF-8",
        # Dutch
        "nl_NL.UTF-8",
        "nl_BE.UTF-8",
        # Nordic languages
        "sv_SE.UTF-8",
        "sv_FI.UTF-8",
        "da_DK.UTF-8",
        "nb_NO.UTF-8",
        "nn_NO.UTF-8",
        "fi_FI.UTF-8",
        "is_IS.UTF-8",
        # Czech and Slovak
        "cs_CZ.UTF-8",
        "sk_SK.UTF-8",
        # Hungarian
        "hu_HU.UTF-8",
        # Romanian
        "ro_RO.UTF-8",
        "ro_MD.UTF-8",
        # Baltic languages
        "lt_LT.UTF-8",
        "lv_LV.UTF-8",
        "et_EE.UTF-8",
        # Slavic languages
        "sl_SI.UTF-8",
        "hr_HR.UTF-8",
        "bs_BA.UTF-8",
        # Catalan
        "ca_ES.UTF-8",
        # Basque
        "eu_ES.UTF-8",
        # Galician
        "gl_ES.UTF-8",
        # Welsh
        "cy_GB.UTF-8",
        # Irish
        "ga_IE.UTF-8",
        # Albanian
        "sq_AL.UTF-8",
        # Georgian
        "ka_GE.UTF-8",
        # Armenian
        "hy_AM.UTF-8",
        # Kazakh
        "kk_KZ.UTF-8",
        # Uzbek
        "uz_UZ.UTF-8",
        # Afrikaans
        "af_ZA.UTF-8",
        # Swahili
        "sw_KE.UTF-8",
        # Filipino
        "fil_PH.UTF-8",
        # Esperanto
        "eo.UTF-8",
    ]

    # Comprehensive keyboard layouts (XKB-compatible)
    COMMON_KEYMAPS = [
        # Major layouts
        "us",
        "gb",
        "fr",
        "de",
        "es",
        "it",
        "pt",
        "br",
        "ru",
        "jp",
        "kr",
        "cn",
        # Middle Eastern
        "ara",
        "il",
        "tr",
        "ir",
        # European
        "pl",
        "nl",
        "se",
        "no",
        "dk",
        "fi",
        "cz",
        "sk",
        "hu",
        "ro",
        "gr",
        "ua",
        "bg",
        "rs",
        "hr",
        "si",
        "lt",
        "lv",
        "ee",
        "ch",
        "be",
        "ca",
        "ie",
        "is",
        # Asian
        "in",
        "th",
        "vn",
        "id",
        # Special layouts
        "latam",
    ]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the locale job."""
        super().__init__(config)

    def _get_available_timezones(self) -> list[str]:
        """
        Get list of available timezones from the system.

        Returns:
            List of timezone names (e.g., "Europe/Paris")
        """
        # stdlib zoneinfo is backed by the system tz database or the bundled
        # ``tzdata`` package, so this works even on minimal or non-standard
        # root layouts, unlike probing /usr/share/zoneinfo.
        zones = available_timezones()
        if zones:
            return sorted(zones)

        logger.warning("No tz database available, using fallback list")
        return [
            "UTC",
            "Europe/Paris",
            "Europe/London",
            "America/New_York",
            "America/Los_Angeles",
        ]

    def _validate_locale(self, locale: str) -> bool:
        """
        Validate locale format.

        Args:
            locale: Locale string: lang[_CC].ENCODING[@modifier] ("fr_FR.UTF-8", "sr_RS.UTF-8@latin")

        Returns:
            True if locale format is valid
        """
        if not locale or not _LOCALE_RE.match(locale):
            return False

        encoding = locale.split("@", 1)[0].rsplit(".", 1)[1]
        if encoding.upper() not in ["UTF-8", "UTF8"]:
            logger.warning(f"Non-UTF-8 encoding detected: {encoding}")
        return True

    def _validate_timezone(self, timezone: str) -> bool:
        """
        Validate a timezone against the IANA database.

        Uses :class:`zoneinfo.ZoneInfo` rather than probing
        ``/usr/share/zoneinfo`` so validation still works on minimal or
        non-standard root layouts where that path does not exist.

        Args:
            timezone: Timezone name (e.g., "Europe/Paris")

        Returns:
            True if timezone is valid
        """
        if not timezone:
            return False

        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return False
        return True

    def _validate_keymap(self, keymap: str) -> bool:
        """
        Validate keyboard layout.

        Args:
            keymap: Keyboard layout name (e.g., "fr")

        Returns:
            True if keymap is valid
        """
        if not keymap:
            return False

        # Accept common keymaps or any alphanumeric layout name
        return keymap in self.COMMON_KEYMAPS or keymap.isalnum()

    @staticmethod
    def _console_keymap(layout: str, variant: str) -> str:
        """The kbd console keymap (vconsole.conf KEYMAP=) for an X11 layout/variant."""
        return _CONSOLE_KEYMAP_VARIANTS.get(
            (layout, variant), _CONSOLE_KEYMAPS.get(layout, "us")
        )

    def _configure_locale(self, context: JobContext) -> JobResult:
        """
        Configure system locale.

        Args:
            context: Execution context with target_root and selections

        Returns:
            JobResult indicating success or failure
        """
        locale = context.selections.get("locale", "en_US.UTF-8")

        if not self._validate_locale(locale):
            return JobResult.fail(
                msg("Invalid locale format: {locale}", locale=locale), error_code=20
            )

        context.report_progress(10, msg("Configuring locale: {locale}", locale=locale))

        target_root = Path(context.target_root)
        locale_gen_path = target_root / "etc" / "locale.gen"
        locale_conf_path = target_root / "etc" / "locale.conf"

        try:
            # Ensure /etc directory exists
            locale_conf_path.parent.mkdir(parents=True, exist_ok=True)

            # Write locale.conf
            locale_conf_content = f"LANG={locale}\n"
            locale_conf_path.write_text(locale_conf_content, encoding="utf-8")
            logger.info(f"Written {locale_conf_path}")

            # If locale.gen exists, uncomment the selected locale
            if locale_gen_path.exists():
                locale_gen_content = locale_gen_path.read_text(encoding="utf-8")
                # locale.gen lists modifier locales without the encoding
                # ("sr_RS@latin UTF-8" for LANG=sr_RS.UTF-8@latin).
                name, _, modifier = locale.partition("@")
                entry = f"{name.rsplit('.', 1)[0]}@{modifier}" if modifier else locale
                updated_content = locale_gen_content.replace(f"#{entry}", entry).replace(
                    f"# {entry}", entry
                )
                locale_gen_path.write_text(updated_content, encoding="utf-8")
                logger.info(f"Updated {locale_gen_path}")

                # Generate locale (using chroot)
                try:
                    subprocess.run(
                        ["arch-chroot", str(target_root), "locale-gen"],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=_CHROOT_TIMEOUT,
                    )
                    logger.info("locale-gen executed successfully")
                except subprocess.CalledProcessError as e:
                    logger.warning(f"locale-gen failed: {e.stderr}")
                except subprocess.TimeoutExpired:
                    logger.warning(f"locale-gen timed out after {_CHROOT_TIMEOUT}s")
                    # Non-critical: continue even if locale-gen fails
                except FileNotFoundError:
                    logger.warning("arch-chroot not found, skipping locale-gen")

            return JobResult.ok(msg("Locale configured: {locale}", locale=locale))

        except OSError as e:
            return JobResult.fail(
                msg("Failed to configure locale: {error}", error=e), error_code=21
            )

    def _configure_timezone(self, context: JobContext) -> JobResult:
        """
        Configure system timezone.

        Args:
            context: Execution context with target_root and selections

        Returns:
            JobResult indicating success or failure
        """
        timezone = context.selections.get("timezone", "UTC")

        if not self._validate_timezone(timezone):
            return JobResult.fail(
                msg("Invalid timezone: {timezone}", timezone=timezone), error_code=22
            )

        context.report_progress(
            40, msg("Configuring timezone: {timezone}", timezone=timezone)
        )

        target_root = Path(context.target_root)
        localtime_link = target_root / "etc" / "localtime"
        timezone_file = target_root / "etc" / "timezone"

        try:
            # Ensure /etc directory exists
            localtime_link.parent.mkdir(parents=True, exist_ok=True)

            # Remove existing symlink if present
            if localtime_link.exists() or localtime_link.is_symlink():
                localtime_link.unlink()

            # Create symlink to zoneinfo
            zoneinfo_target = Path("/usr/share/zoneinfo") / timezone
            localtime_link.symlink_to(zoneinfo_target)
            logger.info(f"Created symlink: {localtime_link} -> {zoneinfo_target}")

            # Write timezone file
            timezone_file.write_text(f"{timezone}\n", encoding="utf-8")
            logger.info(f"Written {timezone_file}")

            # Sync hardware clock (using chroot)
            try:
                subprocess.run(
                    ["arch-chroot", str(target_root), "hwclock", "--systohc"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=_CHROOT_TIMEOUT,
                )
                logger.info("Hardware clock synchronized")
            except subprocess.CalledProcessError as e:
                logger.warning(f"hwclock failed: {e.stderr}")
            except subprocess.TimeoutExpired:
                logger.warning(f"hwclock timed out after {_CHROOT_TIMEOUT}s")
                # Non-critical: continue even if hwclock fails
            except FileNotFoundError:
                logger.warning("arch-chroot not found, skipping hwclock")

            return JobResult.ok(msg("Timezone configured: {timezone}", timezone=timezone))

        except OSError as e:
            return JobResult.fail(
                msg("Failed to configure timezone: {error}", error=e), error_code=23
            )

    def _configure_keyboard(self, context: JobContext) -> JobResult:
        """
        Configure keyboard layout.

        Args:
            context: Execution context with target_root and selections

        Returns:
            JobResult indicating success or failure
        """
        keymap = context.selections.get("keymap", "us")
        variant = str(context.selections.get("keyboard_variant") or "")

        if not self._validate_keymap(keymap):
            return JobResult.fail(
                msg("Invalid keyboard layout: {keymap}", keymap=keymap), error_code=24
            )
        if variant and not _XKB_NAME_RE.match(variant):
            return JobResult.fail(
                msg("Invalid keyboard variant: {variant}", variant=variant), error_code=24
            )

        context.report_progress(70, msg("Configuring keyboard: {keymap}", keymap=keymap))

        target_root = Path(context.target_root)
        vconsole_conf = target_root / "etc" / "vconsole.conf"
        xorg_kbd_conf = target_root / "etc" / "X11" / "xorg.conf.d" / "00-keyboard.conf"

        try:
            # Ensure /etc directory exists
            vconsole_conf.parent.mkdir(parents=True, exist_ok=True)

            vconsole_content = f"KEYMAP={self._console_keymap(keymap, variant)}\n"
            vconsole_conf.write_text(vconsole_content, encoding="utf-8")
            logger.info(f"Written {vconsole_conf}")

            # Write X11 keyboard config (for graphical environment)
            xorg_kbd_conf.parent.mkdir(parents=True, exist_ok=True)
            variant_line = f'    Option "XkbVariant" "{variant}"\n' if variant else ""
            xorg_content = f"""# Keyboard configuration for X11
Section "InputClass"
    Identifier "system-keyboard"
    MatchIsKeyboard "on"
    Option "XkbLayout" "{keymap}"
{variant_line}EndSection
"""
            xorg_kbd_conf.write_text(xorg_content, encoding="utf-8")
            logger.info(f"Written {xorg_kbd_conf}")

            return JobResult.ok(msg("Keyboard configured: {keymap}", keymap=keymap))

        except OSError as e:
            return JobResult.fail(
                msg("Failed to configure keyboard: {error}", error=e), error_code=25
            )

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate that locale configuration can proceed.

        Args:
            context: Execution context

        Returns:
            JobResult indicating if selections are valid
        """
        errors = []

        # Validate locale
        locale = context.selections.get("locale")
        if locale and not self._validate_locale(locale):
            errors.append(msg("Invalid locale: {locale}", locale=locale))

        # Validate timezone
        timezone = context.selections.get("timezone")
        if timezone and not self._validate_timezone(timezone):
            errors.append(msg("Invalid timezone: {timezone}", timezone=timezone))

        # Validate keymap
        keymap = context.selections.get("keymap")
        if keymap and not self._validate_keymap(keymap):
            errors.append(msg("Invalid keyboard layout: {keymap}", keymap=keymap))

        variant = context.selections.get("keyboard_variant")
        if variant and not _XKB_NAME_RE.match(str(variant)):
            errors.append(msg("Invalid keyboard variant: {variant}", variant=variant))

        if errors:
            return JobResult.fail(
                message=msg("Validation errors: {errors}", errors=", ".join(errors)),
                error_code=19,
                data={"errors": errors},
            )

        return JobResult.ok(msg("Locale configuration validated"))

    def run(self, context: JobContext) -> JobResult:
        """
        Execute the locale configuration job.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(0, msg("Starting locale configuration..."))

        if context.selections.get("dry_run"):
            context.report_progress(100, msg("Skipped (dry run)"))
            return JobResult.ok(msg("Skipped: locale configuration (dry run)"))

        # Validate first
        validation = self.validate(context)
        if not validation.success:
            return validation

        # Configure locale
        locale_result = self._configure_locale(context)
        if not locale_result.success:
            return locale_result

        # Configure timezone
        timezone_result = self._configure_timezone(context)
        if not timezone_result.success:
            return timezone_result

        # Configure keyboard
        keyboard_result = self._configure_keyboard(context)
        if not keyboard_result.success:
            return keyboard_result

        context.report_progress(100, msg("Locale configuration complete"))

        return JobResult.ok(
            msg("Locale, timezone and keyboard configured successfully"),
            data={
                "locale": context.selections.get("locale", "en_US.UTF-8"),
                "timezone": context.selections.get("timezone", "UTC"),
                "keymap": context.selections.get("keymap", "us"),
            },
        )

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Returns:
            Estimated duration (locale configuration is quick)
        """
        return 15

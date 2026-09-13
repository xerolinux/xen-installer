"""
Locale Detector - Automatic locale, timezone, and keymap detection.

Detects system locale settings using multiple methods in cascade:
1. GeoIP (ip-api.com) - Geographic location from IP
2. Kernel cmdline - Boot parameters (lang=, locale=, keymap=, timezone=)
3. EFI variables - UEFI PlatformLang variable
4. Default fallback - en_US.UTF-8, UTC, us
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Mapping: timezone -> (locale, keymap)
# Used to derive locale and keymap from detected timezone
TIMEZONE_TO_LOCALE: dict[str, tuple[str, str]] = {
    # Europe
    "Europe/Paris": ("fr_FR.UTF-8", "fr"),
    "Europe/London": ("en_GB.UTF-8", "uk"),
    "Europe/Berlin": ("de_DE.UTF-8", "de"),
    "Europe/Madrid": ("es_ES.UTF-8", "es"),
    "Europe/Rome": ("it_IT.UTF-8", "it"),
    "Europe/Lisbon": ("pt_PT.UTF-8", "pt"),
    "Europe/Amsterdam": ("nl_NL.UTF-8", "nl"),
    "Europe/Brussels": ("nl_BE.UTF-8", "be"),
    "Europe/Stockholm": ("sv_SE.UTF-8", "sv"),
    "Europe/Oslo": ("nb_NO.UTF-8", "no"),
    "Europe/Copenhagen": ("da_DK.UTF-8", "dk"),
    "Europe/Helsinki": ("fi_FI.UTF-8", "fi"),
    "Europe/Warsaw": ("pl_PL.UTF-8", "pl"),
    "Europe/Prague": ("cs_CZ.UTF-8", "cz"),
    "Europe/Vienna": ("de_AT.UTF-8", "de"),
    "Europe/Zurich": ("de_CH.UTF-8", "ch"),
    "Europe/Moscow": ("ru_RU.UTF-8", "ru"),
    "Europe/Kiev": ("uk_UA.UTF-8", "ua"),
    "Europe/Athens": ("el_GR.UTF-8", "gr"),
    "Europe/Istanbul": ("tr_TR.UTF-8", "tr"),
    "Europe/Bucharest": ("ro_RO.UTF-8", "ro"),
    "Europe/Budapest": ("hu_HU.UTF-8", "hu"),
    # Americas
    "America/New_York": ("en_US.UTF-8", "us"),
    "America/Chicago": ("en_US.UTF-8", "us"),
    "America/Denver": ("en_US.UTF-8", "us"),
    "America/Los_Angeles": ("en_US.UTF-8", "us"),
    "America/Toronto": ("en_CA.UTF-8", "us"),
    "America/Vancouver": ("en_CA.UTF-8", "us"),
    "America/Montreal": ("fr_CA.UTF-8", "cf"),
    "America/Mexico_City": ("es_MX.UTF-8", "latam"),
    "America/Sao_Paulo": ("pt_BR.UTF-8", "br"),
    "America/Buenos_Aires": ("es_AR.UTF-8", "latam"),
    "America/Lima": ("es_PE.UTF-8", "latam"),
    "America/Bogota": ("es_CO.UTF-8", "latam"),
    "America/Santiago": ("es_CL.UTF-8", "latam"),
    # Asia
    "Asia/Tokyo": ("ja_JP.UTF-8", "jp"),
    "Asia/Seoul": ("ko_KR.UTF-8", "kr"),
    "Asia/Shanghai": ("zh_CN.UTF-8", "cn"),
    "Asia/Hong_Kong": ("zh_HK.UTF-8", "cn"),
    "Asia/Taipei": ("zh_TW.UTF-8", "tw"),
    "Asia/Singapore": ("en_SG.UTF-8", "us"),
    "Asia/Bangkok": ("th_TH.UTF-8", "th"),
    "Asia/Jakarta": ("id_ID.UTF-8", "us"),
    "Asia/Kolkata": ("hi_IN.UTF-8", "in"),
    "Asia/Dubai": ("ar_AE.UTF-8", "ara"),
    "Asia/Riyadh": ("ar_SA.UTF-8", "ara"),
    "Asia/Jerusalem": ("he_IL.UTF-8", "il"),
    # Oceania
    "Australia/Sydney": ("en_AU.UTF-8", "us"),
    "Australia/Melbourne": ("en_AU.UTF-8", "us"),
    "Australia/Perth": ("en_AU.UTF-8", "us"),
    "Pacific/Auckland": ("en_NZ.UTF-8", "us"),
    # Africa
    "Africa/Cairo": ("ar_EG.UTF-8", "ara"),
    "Africa/Johannesburg": ("en_ZA.UTF-8", "us"),
    "Africa/Lagos": ("en_NG.UTF-8", "us"),
    "Africa/Casablanca": ("ar_MA.UTF-8", "ara"),
}

# GeoIP country code -> timezone mapping (for ip-api.com)
COUNTRY_TO_TIMEZONE: dict[str, str] = {
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "DE": "Europe/Berlin",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "PT": "Europe/Lisbon",
    "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels",
    "SE": "Europe/Stockholm",
    "NO": "Europe/Oslo",
    "DK": "Europe/Copenhagen",
    "FI": "Europe/Helsinki",
    "PL": "Europe/Warsaw",
    "CZ": "Europe/Prague",
    "AT": "Europe/Vienna",
    "CH": "Europe/Zurich",
    "RU": "Europe/Moscow",
    "UA": "Europe/Kiev",
    "GR": "Europe/Athens",
    "TR": "Europe/Istanbul",
    "RO": "Europe/Bucharest",
    "HU": "Europe/Budapest",
    "US": "America/New_York",
    "CA": "America/Toronto",
    "MX": "America/Mexico_City",
    "BR": "America/Sao_Paulo",
    "AR": "America/Buenos_Aires",
    "CL": "America/Santiago",
    "CO": "America/Bogota",
    "PE": "America/Lima",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "TW": "Asia/Taipei",
    "SG": "Asia/Singapore",
    "TH": "Asia/Bangkok",
    "ID": "Asia/Jakarta",
    "IN": "Asia/Kolkata",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "IL": "Asia/Jerusalem",
    "AU": "Australia/Sydney",
    "NZ": "Pacific/Auckland",
    "EG": "Africa/Cairo",
    "ZA": "Africa/Johannesburg",
    "NG": "Africa/Lagos",
    "MA": "Africa/Casablanca",
}

# EFI PlatformLang -> locale mapping
EFI_LANG_TO_LOCALE: dict[str, str] = {
    "en-US": "en_US.UTF-8",
    "en-GB": "en_GB.UTF-8",
    "fr-FR": "fr_FR.UTF-8",
    "de-DE": "de_DE.UTF-8",
    "es-ES": "es_ES.UTF-8",
    "it-IT": "it_IT.UTF-8",
    "pt-BR": "pt_BR.UTF-8",
    "pt-PT": "pt_PT.UTF-8",
    "ru-RU": "ru_RU.UTF-8",
    "zh-CN": "zh_CN.UTF-8",
    "zh-TW": "zh_TW.UTF-8",
    "ja-JP": "ja_JP.UTF-8",
    "ko-KR": "ko_KR.UTF-8",
    "nl-NL": "nl_NL.UTF-8",
    "pl-PL": "pl_PL.UTF-8",
    "sv-SE": "sv_SE.UTF-8",
    "tr-TR": "tr_TR.UTF-8",
    "ar-SA": "ar_SA.UTF-8",
}

# English locales to ignore for cmdline/EFI detection (prefer geoip)
ENGLISH_DEFAULTS = frozenset({"en_US.UTF-8", "en_US", "en-US", "C", "C.UTF-8", "POSIX", ""})


@dataclass(frozen=True)
class LocaleDetectionResult:
    """Result of locale auto-detection."""

    language: str  # e.g., "fr_FR.UTF-8"
    timezone: str  # e.g., "Europe/Paris"
    keymap: str  # e.g., "fr"
    source: str  # "geoip", "cmdline", "efi", "default"
    confidence: float  # 0.0-1.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "language": self.language,
            "timezone": self.timezone,
            "keymap": self.keymap,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass
class LocaleDetectorConfig:
    """Configuration for locale detection methods."""

    enabled: bool = True
    geoip_enabled: bool = True
    geoip_timeout: float = 2.0
    cmdline_enabled: bool = True
    efi_enabled: bool = True
    override_mode: str = "auto"  # "auto", "prefer_geoip", "prefer_local"
    confidence_threshold: float = 0.8


class LocaleDetector:
    """
    Automatic locale detection using multiple methods.

    Detection cascade:
    1. GeoIP (ip-api.com) - Best accuracy for geographic location
    2. Kernel cmdline - Boot parameters set by live ISO
    3. EFI variables - UEFI firmware language setting
    4. Default fallback - en_US.UTF-8, UTC, us
    """

    GEOIP_URL = "http://ip-api.com/json/?fields=status,countryCode,timezone"
    GEOIP_USER_AGENT = "Xen-Installer/1.0"
    CMDLINE_PATH = Path("/proc/cmdline")
    EFI_PATH = Path("/sys/firmware/efi")

    def __init__(self, config: LocaleDetectorConfig | None = None) -> None:
        """
        Initialize locale detector.

        Args:
            config: Optional configuration for detection methods
        """
        self.config = config or LocaleDetectorConfig()

    def detect(self) -> LocaleDetectionResult:
        """
        Detect locale settings using cascade of methods.

        Returns:
            LocaleDetectionResult with detected settings
        """
        if not self.config.enabled:
            return self._get_default_result()

        # "prefer_local": an explicit boot-medium choice (kernel cmdline, e.g.
        # GRUB kbd.* params on the live ISO) is authoritative and must win over the
        # network GeoIP lookup. This honors override_mode from the config.
        if self.config.override_mode == "prefer_local" and self.config.cmdline_enabled:
            result = self._detect_cmdline()
            if result is not None:
                logger.info(f"Cmdline detection (prefer_local) successful: {result}")
                return result

        # Try GeoIP first (highest confidence)
        if self.config.geoip_enabled:
            result = self._detect_geoip()
            if result is not None:
                logger.info(f"GeoIP detection successful: {result}")
                return result

        # Try kernel cmdline
        if self.config.cmdline_enabled:
            result = self._detect_cmdline()
            if result is not None:
                logger.info(f"Cmdline detection successful: {result}")
                return result

        # Try EFI variables
        if self.config.efi_enabled:
            result = self._detect_efi()
            if result is not None:
                logger.info(f"EFI detection successful: {result}")
                return result

        # Fall back to default
        logger.info("All detection methods failed, using default")
        return self._get_default_result()

    def _detect_geoip(self) -> LocaleDetectionResult | None:
        """
        Detect locale from GeoIP service.

        Uses ip-api.com to determine country and timezone from IP address.

        Returns:
            LocaleDetectionResult if successful, None otherwise
        """
        try:
            import urllib.request

            req = urllib.request.Request(
                self.GEOIP_URL,
                headers={"User-Agent": self.GEOIP_USER_AGENT},
            )

            with urllib.request.urlopen(req, timeout=self.config.geoip_timeout) as response:
                import json

                data = json.loads(response.read().decode("utf-8"))

                if data.get("status") != "success":
                    logger.warning(f"GeoIP returned error status: {data}")
                    return None

                country_code = data.get("countryCode", "")
                timezone = data.get("timezone", "")

                if not timezone:
                    # Fall back to country-based timezone
                    timezone = COUNTRY_TO_TIMEZONE.get(country_code, "")
                    if not timezone:
                        logger.warning(f"No timezone for country: {country_code}")
                        return None

                # Get locale and keymap from timezone
                locale_keymap = TIMEZONE_TO_LOCALE.get(timezone)
                if locale_keymap:
                    language, keymap = locale_keymap
                else:
                    # Fallback based on country code
                    language = "en_US.UTF-8"
                    keymap = "us"

                return LocaleDetectionResult(
                    language=language,
                    timezone=timezone,
                    keymap=keymap,
                    source="geoip",
                    confidence=0.9,
                )

        except TimeoutError:
            logger.debug("GeoIP request timed out")
        except OSError as e:
            logger.debug(f"GeoIP network error: {e}")
        except Exception as e:
            logger.debug(f"GeoIP detection failed: {e}")

        return None

    def _detect_cmdline(self) -> LocaleDetectionResult | None:
        """
        Detect locale from kernel command line.

        Parses /proc/cmdline. A live ISO can drive the selection from the
        GRUB menu via ``kbd.layout=`` / ``kbd.locale=`` / ``kbd.keymap=``:
        - kbd.locale=fr_FR.UTF-8  (system locale / language)
        - kbd.layout=fr           (XKB layout -> Xen keymap field)
        - kbd.keymap=fr           (CONSOLE keymap, e.g. "de-latin1"; NOT XKB)
        Generic ``lang=`` / ``locale=`` / ``keymap=`` / ``timezone=`` are still
        accepted as a fallback for boot media that does not set these.

        Returns:
            LocaleDetectionResult if useful values found, None otherwise
        """
        if not self.CMDLINE_PATH.exists():
            return None

        try:
            cmdline = self.CMDLINE_PATH.read_text(encoding="utf-8").strip()
            logger.debug(f"Kernel cmdline: {cmdline}")

            # Parse cmdline parameters
            language = None
            timezone = None
            keymap = None

            # Locale: prefer the explicit kbd.locale= (live ISO), else lang/locale.
            lang_match = re.search(r"\bkbd\.locale=(\S+)", cmdline) or re.search(
                r"(?:lang|locale)=(\S+)", cmdline
            )
            if lang_match:
                language = lang_match.group(1)
                # Normalize to full locale format
                if "." not in language:
                    language = f"{language}.UTF-8"

            # Keymap: kbd.layout= is the XKB layout Xen expects (fr, de, us).
            # kbd.keymap= holds a CONSOLE keymap (e.g. "de-latin1") that is NOT a
            # valid XKB layout, so it must never be used; fall back only to a
            # standalone keymap=/keyboard= for media that does not set kbd.layout=.
            layout_match = re.search(r"\bkbd\.layout=(\S+)", cmdline)
            if layout_match:
                keymap = layout_match.group(1)
            else:
                keymap_match = re.search(r"(?<!kbd\.)(?:keymap|keyboard)=(\S+)", cmdline)
                if keymap_match:
                    keymap = keymap_match.group(1)

            # Match timezone= or tz= (the live ISO does not set a timezone param).
            tz_match = re.search(r"(?:timezone|tz)=(\S+)", cmdline)
            if tz_match:
                timezone = tz_match.group(1)

            # Skip if only English defaults found (prefer geoip)
            if language in ENGLISH_DEFAULTS:
                logger.debug(f"Ignoring English default locale from cmdline: {language}")
                language = None

            # Need at least one useful value
            if not any([language, timezone, keymap]):
                return None

            # Fill in missing values from timezone mapping
            if timezone and timezone in TIMEZONE_TO_LOCALE:
                default_locale, default_keymap = TIMEZONE_TO_LOCALE[timezone]
                if not language:
                    language = default_locale
                if not keymap:
                    keymap = default_keymap

            # Apply defaults for any still missing
            language = language or "en_US.UTF-8"
            timezone = timezone or "UTC"
            keymap = keymap or "us"

            return LocaleDetectionResult(
                language=language,
                timezone=timezone,
                keymap=keymap,
                source="cmdline",
                # Explicit boot-medium choice: high confidence so it clears the
                # bridge's confidence threshold and pre-fills the UI selections.
                confidence=0.9,
            )

        except OSError as e:
            logger.debug(f"Failed to read cmdline: {e}")

        return None

    def _detect_efi(self) -> LocaleDetectionResult | None:
        """
        Detect locale from EFI PlatformLang variable.

        Reads UEFI firmware language setting if available.

        Returns:
            LocaleDetectionResult if successful, None otherwise
        """
        if not self.EFI_PATH.exists():
            logger.debug("EFI directory not found (non-UEFI system)")
            return None

        try:
            # Try reading PlatformLang via efivar
            result = subprocess.run(
                ["efivar", "-n", "8be4df61-93ca-11d2-aa0d-00e098032b8c-PlatformLang"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                logger.debug(f"efivar failed: {result.stderr}")
                return None

            # Parse output - extract the language code
            output = result.stdout.strip()
            # efivar output format varies, try to extract language code
            lang_match = re.search(r"([a-z]{2}-[A-Z]{2})", output)
            if not lang_match:
                logger.debug(f"Could not parse EFI lang from: {output}")
                return None

            efi_lang = lang_match.group(1)
            logger.debug(f"EFI PlatformLang: {efi_lang}")

            # Skip English defaults
            if efi_lang in ("en-US", "en-GB"):
                logger.debug("Ignoring English default EFI language")
                return None

            # Map to locale
            language = EFI_LANG_TO_LOCALE.get(efi_lang)
            if not language:
                logger.debug(f"No mapping for EFI language: {efi_lang}")
                return None

            # Try to derive timezone and keymap from locale
            # Reverse lookup in TIMEZONE_TO_LOCALE
            timezone = "UTC"
            keymap = "us"
            for tz, (loc, km) in TIMEZONE_TO_LOCALE.items():
                if loc == language:
                    timezone = tz
                    keymap = km
                    break

            return LocaleDetectionResult(
                language=language,
                timezone=timezone,
                keymap=keymap,
                source="efi",
                confidence=0.5,
            )

        except FileNotFoundError:
            logger.debug("efivar command not found")
        except subprocess.TimeoutExpired:
            logger.debug("efivar command timed out")
        except Exception as e:
            logger.debug(f"EFI detection failed: {e}")

        return None

    def _get_default_result(self) -> LocaleDetectionResult:
        """
        Return default locale settings.

        Returns:
            LocaleDetectionResult with en_US.UTF-8, UTC, us
        """
        return LocaleDetectionResult(
            language="en_US.UTF-8",
            timezone="UTC",
            keymap="us",
            source="default",
            confidence=0.1,
        )

    @classmethod
    def from_config(cls, config_dict: dict[str, Any]) -> LocaleDetector:
        """
        Create LocaleDetector from configuration dictionary.

        Args:
            config_dict: Configuration from YAML (auto_detection section)

        Returns:
            Configured LocaleDetector instance
        """
        if not config_dict:
            return cls()

        methods = config_dict.get("methods", {})
        geoip_config = methods.get("geoip", {})
        cmdline_config = methods.get("cmdline", {})
        efi_config = methods.get("efi", {})

        detector_config = LocaleDetectorConfig(
            enabled=config_dict.get("enabled", True),
            geoip_enabled=geoip_config.get("enabled", True),
            geoip_timeout=geoip_config.get("timeout", 2.0),
            cmdline_enabled=cmdline_config.get("enabled", True),
            efi_enabled=efi_config.get("enabled", True),
            override_mode=config_dict.get("override_mode", "auto"),
            confidence_threshold=config_dict.get("confidence_threshold", 0.8),
        )

        return cls(detector_config)

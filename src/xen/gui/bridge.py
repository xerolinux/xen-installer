"""
Bridge between QML UI and Python Engine.

Exposes engine functionality to QML via Qt properties and signals.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from PySide6.QtCore import Property, QObject, QThread, QTimer, QUrl, Signal, Slot

from xen.i18n.translator import get_translator
from xen.jobs.base import JobStatus
from xen.jobs.locale import LocaleJob
from xen.jobs.partition import (
    PartitionOperation,
    manual_has_separate_boot,
    manual_mapper_name_for,
    simulate_operations,
    validate_encryption_rules,
    validate_manual_assignments,
    validate_operations,
    validate_operations_applicable,
)
from xen.jobs.requirements import SystemRequirementsChecker
from xen.utils import disk_detector
from xen.utils.keyboard_layout import apply_keyboard_layout_live
from xen.utils.locale_detector import LocaleDetectionResult, LocaleDetector
from xen.utils.log_capture import BridgeLogHandler, SecretRedactor, resolve_log_path, upload_log
from xen.utils.network_helper import NetworkHelper

if TYPE_CHECKING:
    from xen.core.engine import Engine

logger = logging.getLogger(__name__)

# Native names for locales (displayed in UI with proper Unicode characters)
# Each language is shown in its native script/form
LOCALE_NATIVE_NAMES: dict[str, str] = {
    # English variants
    "en_US.UTF-8": "English (United States)",
    "en_GB.UTF-8": "English (United Kingdom)",
    "en_CA.UTF-8": "English (Canada)",
    "en_AU.UTF-8": "English (Australia)",
    "en_NZ.UTF-8": "English (New Zealand)",
    "en_IE.UTF-8": "English (Ireland)",
    "en_ZA.UTF-8": "English (South Africa)",
    "en_IN.UTF-8": "English (India)",
    # French variants
    "fr_FR.UTF-8": "Français (France)",
    "fr_CA.UTF-8": "Français (Canada)",
    "fr_BE.UTF-8": "Français (Belgique)",
    "fr_CH.UTF-8": "Français (Suisse)",
    "fr_LU.UTF-8": "Français (Luxembourg)",
    # German variants
    "de_DE.UTF-8": "Deutsch (Deutschland)",
    "de_AT.UTF-8": "Deutsch (Österreich)",
    "de_CH.UTF-8": "Deutsch (Schweiz)",
    "de_LU.UTF-8": "Deutsch (Luxemburg)",
    "de_LI.UTF-8": "Deutsch (Liechtenstein)",
    # Spanish variants
    "es_ES.UTF-8": "Español (España)",
    "es_MX.UTF-8": "Español (México)",
    "es_AR.UTF-8": "Español (Argentina)",
    "es_CO.UTF-8": "Español (Colombia)",
    "es_CL.UTF-8": "Español (Chile)",
    "es_PE.UTF-8": "Español (Perú)",
    "es_VE.UTF-8": "Español (Venezuela)",
    # Italian
    "it_IT.UTF-8": "Italiano (Italia)",
    "it_CH.UTF-8": "Italiano (Svizzera)",
    # Portuguese variants
    "pt_BR.UTF-8": "Português (Brasil)",
    "pt_PT.UTF-8": "Português (Portugal)",
    # Russian and Cyrillic languages
    "ru_RU.UTF-8": "Русский (Россия)",
    "uk_UA.UTF-8": "Українська (Україна)",
    "be_BY.UTF-8": "Беларуская (Беларусь)",
    "bg_BG.UTF-8": "Български (България)",
    "sr_RS.UTF-8": "Српски (Србија)",
    "mk_MK.UTF-8": "Македонски (Македонија)",
    # Chinese variants
    "zh_CN.UTF-8": "简体中文 (中国)",
    "zh_TW.UTF-8": "繁體中文 (台灣)",
    "zh_HK.UTF-8": "繁體中文 (香港)",
    "zh_SG.UTF-8": "简体中文 (新加坡)",
    # Japanese
    "ja_JP.UTF-8": "日本語 (日本)",
    # Korean
    "ko_KR.UTF-8": "한국어 (대한민국)",
    # Arabic variants
    "ar_SA.UTF-8": "العربية (السعودية)",
    "ar_EG.UTF-8": "العربية (مصر)",
    "ar_MA.UTF-8": "العربية (المغرب)",
    "ar_DZ.UTF-8": "العربية (الجزائر)",
    "ar_TN.UTF-8": "العربية (تونس)",
    "ar_AE.UTF-8": "العربية (الإمارات)",
    # Hebrew
    "he_IL.UTF-8": "עברית (ישראל)",
    # Persian
    "fa_IR.UTF-8": "فارسی (ایران)",
    # Hindi and Indian languages
    "hi_IN.UTF-8": "हिन्दी (भारत)",
    "bn_IN.UTF-8": "বাংলা (ভারত)",
    "bn_BD.UTF-8": "বাংলা (বাংলাদেশ)",
    "ta_IN.UTF-8": "தமிழ் (இந்தியா)",
    "te_IN.UTF-8": "తెలుగు (భారతదేశం)",
    "mr_IN.UTF-8": "मराठी (भारत)",
    "gu_IN.UTF-8": "ગુજરાતી (ભારત)",
    "kn_IN.UTF-8": "ಕನ್ನಡ (ಭಾರತ)",
    "ml_IN.UTF-8": "മലയാളം (ഇന്ത്യ)",
    "pa_IN.UTF-8": "ਪੰਜਾਬੀ (ਭਾਰਤ)",
    # Thai
    "th_TH.UTF-8": "ไทย (ประเทศไทย)",
    # Vietnamese
    "vi_VN.UTF-8": "Tiếng Việt (Việt Nam)",
    # Indonesian and Malay
    "id_ID.UTF-8": "Bahasa Indonesia (Indonesia)",
    "ms_MY.UTF-8": "Bahasa Melayu (Malaysia)",
    # Turkish
    "tr_TR.UTF-8": "Türkçe (Türkiye)",
    # Greek
    "el_GR.UTF-8": "Ελληνικά (Ελλάδα)",
    "el_CY.UTF-8": "Ελληνικά (Κύπρος)",
    # Polish
    "pl_PL.UTF-8": "Polski (Polska)",
    # Dutch
    "nl_NL.UTF-8": "Nederlands (Nederland)",
    "nl_BE.UTF-8": "Nederlands (België)",
    # Nordic languages
    "sv_SE.UTF-8": "Svenska (Sverige)",
    "sv_FI.UTF-8": "Svenska (Finland)",
    "da_DK.UTF-8": "Dansk (Danmark)",
    "nb_NO.UTF-8": "Norsk Bokmål (Norge)",
    "nn_NO.UTF-8": "Norsk Nynorsk (Norge)",
    "fi_FI.UTF-8": "Suomi (Suomi)",
    "is_IS.UTF-8": "Íslenska (Ísland)",
    # Czech and Slovak
    "cs_CZ.UTF-8": "Čeština (Česko)",
    "sk_SK.UTF-8": "Slovenčina (Slovensko)",
    # Hungarian
    "hu_HU.UTF-8": "Magyar (Magyarország)",
    # Romanian
    "ro_RO.UTF-8": "Română (România)",
    "ro_MD.UTF-8": "Română (Moldova)",
    # Baltic languages
    "lt_LT.UTF-8": "Lietuvių (Lietuva)",
    "lv_LV.UTF-8": "Latviešu (Latvija)",
    "et_EE.UTF-8": "Eesti (Eesti)",
    # Slavic languages
    "sl_SI.UTF-8": "Slovenščina (Slovenija)",
    "hr_HR.UTF-8": "Hrvatski (Hrvatska)",
    "bs_BA.UTF-8": "Bosanski (Bosna i Hercegovina)",
    # Catalan
    "ca_ES.UTF-8": "Català (Espanya)",
    # Basque
    "eu_ES.UTF-8": "Euskara (Espainia)",
    # Galician
    "gl_ES.UTF-8": "Galego (España)",
    # Welsh
    "cy_GB.UTF-8": "Cymraeg (Y Deyrnas Unedig)",
    # Irish
    "ga_IE.UTF-8": "Gaeilge (Éire)",
    # Albanian
    "sq_AL.UTF-8": "Shqip (Shqipëri)",
    # Georgian
    "ka_GE.UTF-8": "ქართული (საქართველო)",
    # Armenian
    "hy_AM.UTF-8": "Հայերեն (Հայաստան)",
    # Kazakh
    "kk_KZ.UTF-8": "Қазақша (Қазақстан)",
    # Uzbek
    "uz_UZ.UTF-8": "O'zbek (O'zbekiston)",
    # Afrikaans
    "af_ZA.UTF-8": "Afrikaans (Suid-Afrika)",
    # Swahili
    "sw_KE.UTF-8": "Kiswahili (Kenya)",
    # Filipino
    "fil_PH.UTF-8": "Filipino (Pilipinas)",
    # Esperanto
    "eo.UTF-8": "Esperanto",
}

# Friendly, user-facing labels for the internal job identifiers (BaseJob.name)
# shown on the Progress view. The raw identifiers (e.g. "install", "locale")
# are stable API used for step tracking and tests; only the display layer
# needs a human-readable label.
JOB_DISPLAY_NAMES: dict[str, str] = {
    "welcome": "Preparing",
    "locale": "Configuring Locale",
    "packagechooser": "Selecting Packages",
    "users": "Creating User Account",
    "partition": "Partitioning Disk",
    "install": "Installing",
    "systemconfig": "Configuring System",
    "packages": "Installing Packages",
    "systemupdate": "Updating System",
    "bootloader": "Installing Bootloader",
    "services": "Configuring Services",
    "finalize": "Finalizing Installation",
    "finished": "Finishing Up",
}


def _job_display_name(job_name: str) -> str:
    """Human-readable label for a job's internal identifier."""
    return JOB_DISPLAY_NAMES.get(job_name, job_name.replace("_", " ").title())


class _CommandCollector:
    """Collects joined command strings issued during a dry-run preview."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def record(self, cmd: list[str]) -> None:
        """Append a command as a single joined string."""
        self.lines.append(" ".join(cmd))


def _build_preview_job(
    disk: str, operations: list[dict[str, Any]], collector: _CommandCollector
) -> Any:
    """
    Build a zero-argument callable that dry-runs the M2 operations.

    Runs :meth:`PartitionJob._apply_operations` in dry-run mode with the
    command runner swapped for the ``collector`` so no subprocess is spawned
    and every command line is captured for the UI preview.
    """
    from xen.jobs.base import JobContext, JobResult
    from xen.jobs.partition import PartitionJob

    job = PartitionJob()

    def _record(cmd: list[str], description: str, dry_run: bool) -> JobResult:  # noqa: ARG001
        collector.record(cmd)
        return JobResult.ok(description)

    job._run_partitioning_command = _record  # type: ignore[method-assign]

    def _run() -> None:
        job._apply_operations(
            JobContext(),
            disk,
            {"partition_operations": operations},
            dry_run=True,
        )

    return _run


class InstallationWorker(QObject):
    """Worker that runs installation in a separate thread."""

    finished = Signal(bool)  # success: bool
    error = Signal(str)  # error_message

    def __init__(self, engine: Engine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine

    def run(self) -> None:
        """Execute the installation process."""
        try:
            success = self._engine.run_all()
            self.finished.emit(success)
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(False)


class LogUploadWorker(QObject):
    """Worker that uploads the (already redacted) install log in a background thread."""

    finished = Signal(str, bool, str)  # url, ok, error_message

    def __init__(self, text: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._text = text

    def run(self) -> None:
        """Upload the log text and emit the result."""
        try:
            url = upload_log(self._text)
            self.finished.emit(url, True, "")
        except Exception as e:  # noqa: BLE001 - never let the thread crash the UI
            self.finished.emit("", False, str(e))


class PartitionApplyWorker(QObject):
    """Worker that applies the manual (M2) partition operations off the UI thread."""

    finished = Signal(bool, str, object)  # success, message, {raw_path: LUKS passphrase}

    def __init__(
        self,
        disk: str,
        operations: list[dict[str, Any]],
        dry_run: bool,
        bootloader: str,
        has_separate_boot: bool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._disk = disk
        self._operations = operations
        self._dry_run = dry_run
        self._bootloader = bootloader
        self._has_separate_boot = has_separate_boot

    def run(self) -> None:
        """Execute the queued operations (real sgdisk/mkfs) against the disk."""
        try:
            from xen.jobs.base import JobContext
            from xen.jobs.partition import PartitionJob

            job = PartitionJob()
            context = JobContext(
                selections={
                    "disk": self._disk,
                    "partition_operations": self._operations,
                    "bootloader": self._bootloader,
                    "has_separate_boot": self._has_separate_boot,
                }
            )
            try:
                result = job._apply_operations(
                    context, self._disk, context.selections, dry_run=self._dry_run
                )
            except Exception:
                job.cleanup(context)
                raise
            if not result.success:
                # Close any LUKS mapper this batch opened before failing, so
                # a retry doesn't hit "device busy"/"already exists".
                job.cleanup(context)
                self.finished.emit(False, result.message, {})
                return
            self.finished.emit(True, result.message, dict(job._manual_luks_passphrases))
        except Exception as e:  # noqa: BLE001 - never let the thread crash the UI
            self.finished.emit(False, str(e), {})


class BrandingProxy(QObject):
    """Exposes branding configuration to QML."""

    # Signal emitted when translatable text properties change
    brandingChanged = Signal()

    # Two profiles, each with its own fixed identity - no hardware
    # detection: xerolinux.yaml (Intel/AMD, one ISO for both) always shows
    # this blue-to-red gradient; xerolinux-nvidia.yaml always shows solid
    # green. Which profile is running is already known from its own
    # `edition` config value.
    _INTEL_AMD_START = "#5597e6"
    _INTEL_AMD_END = "#ED1C24"
    _NVIDIA_COLOR = "#76B900"

    # Full-page background gradients are a much larger wash of color than a
    # button or badge - nvidia's actual brand green at that scale reads as
    # sickly rather than sleek, so backgrounds get their own muted,
    # low-saturation green-to-grey pair instead of the vivid brand color.
    _NVIDIA_BG_START = "#4a5a3d"
    _NVIDIA_BG_END = "#43453f"

    def __init__(
        self, engine: Engine, theme_base: Path, parent: QObject | None = None, debug: bool = False
    ) -> None:
        super().__init__(parent)
        self._branding = engine.get_branding()
        self._theme_base = theme_base
        self._debug = debug
        self._is_nvidia = "nvidia" in self._branding.edition.lower()

        if self._debug:
            print(f"[Branding] Loaded: {self._branding.name}")
            print(f"[Branding] Theme base: {self._theme_base}")
            print(
                f"[Branding] Colors - primary: {self._branding.colors.primary}, "
                f"bg: {self._branding.colors.background}, text: {self._branding.colors.text}"
            )

    def _resolve_asset(self, relative_path: str) -> str:
        """Resolve asset path to absolute file:// URL."""
        if not relative_path:
            if self._debug:
                print("[Branding] Asset path is empty")
            return ""
        full_path = self._theme_base / relative_path
        if full_path.exists():
            url = QUrl.fromLocalFile(str(full_path.resolve())).toString()
            if self._debug:
                print(f"[Branding] Resolved: {relative_path} -> {url}")
            return url
        if self._debug:
            print(f"[Branding] Asset not found: {full_path}")
        return ""

    @Property(str, constant=True)
    def name(self) -> str:
        """Distribution name."""
        return self._branding.name

    @Property(str, constant=True)
    def version(self) -> str:
        """Distribution version."""
        return self._branding.version

    @Property(str, constant=True)
    def edition(self) -> str:
        """Distribution edition."""
        return self._branding.edition

    @Property(str, constant=True)
    def primaryColor(self) -> str:
        """Primary brand color / gradient start. Green for the nvidia
        profile, blue for Intel/AMD (paired with accentGradientEnd)."""
        return self._NVIDIA_COLOR if self._is_nvidia else self._INTEL_AMD_START

    @Property(str, constant=True)
    def accentGradientEnd(self) -> str:
        """Gradient end color for buttons/glows. Equals primaryColor for
        nvidia (a solid-looking two-stop gradient); red for Intel/AMD,
        since that one ISO covers both vendors."""
        return self._NVIDIA_COLOR if self._is_nvidia else self._INTEL_AMD_END

    @Property(str, constant=True)
    def backgroundGradientStart(self) -> str:
        """Full-page background gradient start - muted for nvidia (the
        vivid brand green reads as sickly at that scale), same blue as
        primaryColor for Intel/AMD."""
        return self._NVIDIA_BG_START if self._is_nvidia else self._INTEL_AMD_START

    @Property(str, constant=True)
    def backgroundGradientEnd(self) -> str:
        """Full-page background gradient end - muted grey for nvidia, red
        for Intel/AMD (same as accentGradientEnd)."""
        return self._NVIDIA_BG_END if self._is_nvidia else self._INTEL_AMD_END

    @Property(str, constant=True)
    def secondaryColor(self) -> str:
        """Secondary brand color."""
        return self._branding.colors.secondary

    @Property(str, constant=True)
    def accentColor(self) -> str:
        """Accent color - same as primaryColor (see accentGradientEnd for
        the second gradient stop)."""
        return self._NVIDIA_COLOR if self._is_nvidia else self._INTEL_AMD_START

    @Property(str, constant=True)
    def editionLabel(self) -> str:
        """Small badge text above the title, e.g. "nVidia Edition" -
        derived straight from the profile's own `edition` config value.
        No hardware detection: each profile already declares its own
        identity in config."""
        return f"{self._branding.edition} Edition" if self._branding.edition else ""

    @Property(str, constant=True)
    def backgroundColor(self) -> str:
        """Background color."""
        return self._branding.colors.background

    @Property(str, constant=True)
    def surfaceColor(self) -> str:
        """Surface color."""
        return self._branding.colors.surface

    @Property(str, constant=True)
    def textColor(self) -> str:
        """Primary text color."""
        return self._branding.colors.text

    @Property(str, constant=True)
    def textMutedColor(self) -> str:
        """Muted text color."""
        return self._branding.colors.text_muted

    @Property(str, constant=True)
    def backgroundLightColor(self) -> str:
        """Lighter background shade."""
        return self._branding.colors.background_light

    @Property(str, constant=True)
    def textOnPrimaryColor(self) -> str:
        """Text color drawn on primary-colored surfaces."""
        return self._branding.colors.text_on_primary

    @Property(str, constant=True)
    def successColor(self) -> str:
        """Success / positive status color."""
        return self._branding.colors.success

    @Property(str, constant=True)
    def warningColor(self) -> str:
        """Warning status color."""
        return self._branding.colors.warning

    @Property(str, constant=True)
    def errorColor(self) -> str:
        """Error / failure status color."""
        return self._branding.colors.error

    @Property(str, constant=True)
    def fontPrimary(self) -> str:
        return self._branding.fonts.primary

    @Property(str, constant=True)
    def fontDisplay(self) -> str:
        return self._branding.fonts.display

    @Property(str, constant=True)
    def fontMonospace(self) -> str:
        return self._branding.fonts.monospace

    @Slot(str, result=str)
    def themeIconUrl(self, relative_path: str) -> str:
        return self._resolve_asset(relative_path)

    @Slot(str, result=str)
    def requirementIconUrl(self, name: str) -> str:
        if not name:
            return ""
        rel = self._branding.requirement_icons.get(name) or f"icons/requirements/cat-{name}.svg"
        return self._resolve_asset(rel)

    @Property(str, notify=brandingChanged)
    def welcomeTitle(self) -> str:
        """Welcome screen title with i18n interpolation."""
        translator = get_translator()
        return translator.get(
            "title",
            "welcome",
            default=self._branding.strings.welcome_title,
            distro_name=self._branding.name,
        )

    @Property(str, notify=brandingChanged)
    def welcomeSubtitle(self) -> str:
        """Welcome screen subtitle with i18n interpolation."""
        translator = get_translator()
        return translator.get(
            "subtitle",
            "welcome",
            default=self._branding.strings.welcome_subtitle,
            distro_tagline=self._branding.strings.welcome_subtitle,
        )

    @Property(str, notify=brandingChanged)
    def installButton(self) -> str:
        """Install button text with i18n interpolation."""
        translator = get_translator()
        return translator.get(
            "install_button",
            "welcome",
            default=self._branding.strings.install_button,
            distro_name=self._branding.name,
        )

    @Property(str, notify=brandingChanged)
    def finishedTitle(self) -> str:
        """Finished screen title with i18n interpolation."""
        translator = get_translator()
        return translator.get(
            "title",
            "finished",
            default=self._branding.strings.finished_title,
            distro_name=self._branding.name,
        )

    @Property(str, notify=brandingChanged)
    def finishedSubtitle(self) -> str:
        """Finished screen subtitle with i18n interpolation."""
        translator = get_translator()
        return translator.get(
            "subtitle",
            "finished",
            default=f"{self._branding.name} has been successfully installed",
            distro_name=self._branding.name,
        )

    @Property(str, notify=brandingChanged)
    def finishedMessage(self) -> str:
        """Finished screen closing message with i18n interpolation."""
        translator = get_translator()
        return translator.get(
            "message",
            "finished",
            default=self._branding.strings.finished_message
            or f"You can now restart your computer to start using {self._branding.name}.",
            distro_name=self._branding.name,
        )

    @Property(str, constant=True)
    def logoPath(self) -> str:
        """Path to logo asset."""
        return self._branding.assets.logo

    # Asset URLs (resolved to absolute file:// URLs)
    @Property(str, constant=True)
    def logoUrl(self) -> str:
        """URL to main logo."""
        return self._resolve_asset(self._branding.assets.logo)

    @Property(str, constant=True)
    def logoLightUrl(self) -> str:
        """URL to light variant logo."""
        return self._resolve_asset(self._branding.assets.logo_light)

    @Property(str, constant=True)
    def logoSmallUrl(self) -> str:
        """URL to small logo (64px)."""
        return self._resolve_asset(self._branding.assets.logo_small)

    @Property(str, constant=True)
    def backgroundUrl(self) -> str:
        """URL to background wallpaper."""
        return self._resolve_asset(self._branding.assets.background)

    @Property(str, constant=True)
    def iconUrl(self) -> str:
        """URL to icon."""
        return self._resolve_asset(self._branding.assets.icon)

    # Users view icons (configurable via theme)
    @Property(str, constant=True)
    def iconUserUrl(self) -> str:
        """URL to user icon for UsersView."""
        return self._resolve_asset(self._branding.assets.icon_user)

    @Property(str, constant=True)
    def iconFullnameUrl(self) -> str:
        """URL to fullname/identity icon for UsersView."""
        return self._resolve_asset(self._branding.assets.icon_fullname)

    @Property(str, constant=True)
    def iconHostnameUrl(self) -> str:
        """URL to hostname/computer icon for UsersView."""
        return self._resolve_asset(self._branding.assets.icon_hostname)

    @Property(str, constant=True)
    def iconPasswordUrl(self) -> str:
        """URL to password/lock icon for UsersView."""
        return self._resolve_asset(self._branding.assets.icon_password)

    @Property(str, constant=True)
    def iconSettingsUrl(self) -> str:
        """URL to settings/gear icon for UsersView."""
        return self._resolve_asset(self._branding.assets.icon_settings)

    @Property(str, constant=True)
    def iconCheckUrl(self) -> str:
        """URL to check/valid icon for validation feedback."""
        return self._resolve_asset(self._branding.assets.icon_check)

    @Property(str, constant=True)
    def iconCrossUrl(self) -> str:
        """URL to cross/error icon for validation feedback."""
        return self._resolve_asset(self._branding.assets.icon_cross)

    # Links
    @Property(str, constant=True)
    def websiteUrl(self) -> str:
        """Main website URL."""
        return self._branding.links.website

    @Property(str, constant=True)
    def websiteLabel(self) -> str:
        """Display label for website link (falls back to full URL if empty)."""
        label = self._branding.links.website_label
        if not label and self._branding.links.website:
            # Use full URL as fallback, only remove trailing slash
            label = self._branding.links.website.rstrip("/")
        return label

    @Property(str, constant=True)
    def gitUrl(self) -> str:
        """Git repository URL."""
        return self._branding.links.git

    @Property(str, constant=True)
    def documentationUrl(self) -> str:
        """Documentation URL."""
        return self._branding.links.documentation

    @Property(str, constant=True)
    def supportUrl(self) -> str:
        """Support/forum URL."""
        return self._branding.links.support

    @Slot()
    def retranslate(self) -> None:
        """
        Force re-evaluation of all translatable text properties.

        Call this when the application language changes to update
        all QML bindings that use branding text properties.
        """
        if self._debug:
            print("[Branding] Retranslating branding strings...")
        self.brandingChanged.emit()


class EngineBridge(QObject):
    """
    Main bridge between QML and installation engine.

    Exposes:
    - Installation control (start, pause, cancel)
    - Progress reporting
    - Job navigation
    - Branding configuration
    """

    _STALL_THRESHOLD_S: float = 6.0

    # Signals for QML
    installationStarted = Signal()
    installationFinished = Signal(bool)  # success: bool
    jobStarted = Signal(str)  # job_name
    jobProgress = Signal(str, int, str)  # job_name, percent, message
    jobCompleted = Signal(str, bool)  # job_name, success
    errorOccurred = Signal(str, str)  # job_name, error_message
    requirementsChanged = Signal()  # emitted when requirements check completes

    # Network settings signals
    networkSettingsLaunched = Signal(str)  # command that was launched
    networkSettingsError = Signal(str)  # error message
    internetStatusChanged = Signal(bool)  # connected: bool

    # Signals for user selections
    selectionsChanged = Signal()  # emitted when any selection changes
    localeDataChanged = Signal()  # emitted when locale data is loaded
    keyboardVariantsChanged = Signal()  # emitted when keyboard variants change
    disksChanged = Signal()  # emitted when disks are scanned
    partitionPlanChanged = Signal()  # emitted when a manual partition assignment changes
    partitionOperationsChanged = Signal()  # emitted when the M2 operation list/simulation changes
    partitionApplyingChanged = Signal()  # emitted when the live-apply busy state flips
    partitionApplyFinished = Signal(bool, str)  # success, message (live GParted-style apply)
    progressChanged = Signal()  # emitted when progress updates
    systemFontChanged = Signal()  # emitted when system font family changes

    # Logs/diagnostics signals
    logMessageAppended = Signal(str)  # a single redacted log line (live streaming)
    logChanged = Signal()  # notify for the installationLog Property
    logUploadFinished = Signal(str, bool, str)  # url, ok, error_message

    # Locale prefixes that require non-Latin font (CJK, Arabic, Hebrew, etc.)
    # These scripts need fonts with broader Unicode coverage like Noto Sans
    NON_LATIN_LOCALE_PREFIXES = (
        # CJK (Chinese, Japanese, Korean)
        "zh_",
        "ja_",
        "ko_",
        # Arabic script
        "ar_",
        "fa_",
        "ur_",
        # Hebrew script
        "he_",
        "yi_",
        # Indic scripts (Devanagari, Bengali, Tamil, etc.)
        "hi_",
        "bn_",
        "ta_",
        "te_",
        "mr_",
        "gu_",
        "kn_",
        "ml_",
        "pa_",
        "ne_",
        "si_",
        # Thai script
        "th_",
        # Georgian script
        "ka_",
        # Armenian script
        "hy_",
        # Greek script (while Latin-based in some ways, benefits from Noto)
        "el_",
        # Cyrillic script (Russian, Ukrainian, etc.)
        "ru_",
        "uk_",
        "be_",
        "bg_",
        "sr_",
        "mk_",
        "kk_",
        "ky_",
        "mn_",
        "tg_",
        # Other scripts
        "am_",  # Amharic (Ethiopic)
        "my_",  # Burmese
        "km_",  # Khmer
        "lo_",  # Lao
    )

    # Font family for non-Latin scripts (Noto Sans has excellent Unicode coverage)
    UNICODE_FONT_FAMILY = "Noto Sans"

    # Default Latin font family (empty string = system default)
    LATIN_FONT_FAMILY = ""

    def __init__(
        self,
        engine: Engine,
        theme_base: Path,
        debug: bool = False,
        dry_run: bool = False,
        skip_requirements: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        # Weighted by each job's own estimate_duration() (seconds) rather
        # than splitting the bar evenly across job count: "install" (an
        # rsync copy that can take minutes) and "welcome" (an instant check)
        # both counting as 1/N of the bar made the overall percentage
        # meaningless - it barely moved during the one job that actually
        # takes most of the wall-clock time.
        self._job_weights: list[int] = [max(1, job.estimate_duration()) for job in engine.jobs]
        self._total_job_weight: int = sum(self._job_weights) or 1
        self._debug = debug
        self._dry_run = dry_run
        self._skip_requirements = skip_requirements
        self._branding_proxy = BrandingProxy(engine, theme_base, self, debug=debug)
        self._theme_base = theme_base

        # Thread management
        self._worker: InstallationWorker | None = None
        self._thread: QThread | None = None

        # Installation timing (for the summary "duration" field).
        self._install_start_time: float | None = None
        self._install_end_time: float | None = None

        # Logs/diagnostics: in-process capture (jobs run in this same process).
        self._redactor = SecretRedactor()
        self._log_path = resolve_log_path()
        self._log_handler = BridgeLogHandler(
            self._redactor, self._log_path, on_line=self._mark_log_dirty
        )
        self._log_handler.setLevel(logging.DEBUG)
        # Attach ONLY to the "xen" logger. Adding it to the root logger as well
        # made every ``xen.*`` record fire the handler twice (it also
        # propagates to root) — doubling both the file and the UI-refresh load.
        logging.getLogger("xen").addHandler(self._log_handler)
        logging.getLogger("xen").setLevel(logging.DEBUG)

        # Throttle live log refresh. Some install jobs emit thousands of lines; a
        # per-line Qt signal + full re-render floods the GUI thread and freezes
        # the window ("application not responding"). Instead each captured line
        # just flips a dirty flag (cheap, called from the worker thread) and a
        # GUI-thread timer coalesces refreshes to a few per second.
        self._log_dirty = False
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(200)
        self._log_flush_timer.timeout.connect(self._flush_log)
        self._log_flush_timer.start()

        self._log_upload_thread: QThread | None = None
        self._log_upload_worker: LogUploadWorker | None = None

        # Live (GParted-style) partition-apply thread state.
        self._apply_worker: PartitionApplyWorker | None = None
        self._apply_thread: QThread | None = None
        self._partition_applying: bool = False

        # Requirements state
        self._requirements_checker: SystemRequirementsChecker | None = None
        self._requirements_model: list[dict[str, Any]] = []
        self._can_proceed: bool = True
        self._is_checking_requirements: bool = False
        self._show_requirements: bool = True

        # Locale data
        self._locales_model: list[str] = []
        self._locales_model_native: list[dict[str, str]] = []
        self._timezones_model: list[str] = []
        self._keymaps_model: list[str] = []
        self._keyboard_variants_model: list[str] = []
        self._locale_detection_result: LocaleDetectionResult | None = None
        self._locale_auto_detection_config: dict[str, Any] = {}

        # Keyboard variants by layout (XKB-compatible)
        # Each layout has a list of available variants
        # Empty string "" means default/basic variant
        self._keyboard_variants: dict[str, list[str]] = {
            # US layouts
            "us": [
                "",
                "alt-intl",
                "altgr-intl",
                "chr",
                "colemak",
                "colemak_dh",
                "colemak_dh_iso",
                "dvorak",
                "dvorak-alt-intl",
                "dvorak-classic",
                "dvorak-intl",
                "dvorak-l",
                "dvorak-r",
                "dvp",
                "euro",
                "hbs",
                "intl",
                "mac",
                "norman",
                "olpc2",
                "rus",
                "workman",
                "workman-intl",
            ],
            # French layouts
            "fr": [
                "",
                "azerty",
                "bepo",
                "bepo_afnor",
                "bepo_latin9",
                "bre",
                "dvorak",
                "geo",
                "latin9",
                "mac",
                "nodeadkeys",
                "oci",
                "oss",
                "oss_latin9",
                "oss_nodeadkeys",
                "sun_type6",
                "us",
            ],
            # German layouts
            "de": [
                "",
                "T3",
                "adnw",
                "bone",
                "deadacute",
                "deadgraveacute",
                "deadtilde",
                "dsb",
                "dsb_qwertz",
                "dvorak",
                "e1",
                "e2",
                "koy",
                "legacy",
                "mac",
                "mac_nodeadkeys",
                "neo",
                "nodeadkeys",
                "qwerty",
                "ro",
                "ro_nodeadkeys",
                "ru",
                "sun_type6",
                "tr",
            ],
            # UK layouts
            "gb": [
                "",
                "colemak",
                "colemak_dh",
                "dvorak",
                "dvorakukp",
                "extd",
                "intl",
                "mac",
                "mac_intl",
                "sun_type6",
            ],
            # Spanish layouts
            "es": [
                "",
                "ast",
                "cat",
                "deadtilde",
                "dvorak",
                "mac",
                "nodeadkeys",
                "sun_type6",
                "winkeys",
            ],
            # Italian layouts
            "it": [
                "",
                "fur",
                "geo",
                "ibm",
                "intl",
                "lld",
                "mac",
                "nodeadkeys",
                "sun_type6",
                "us",
                "winkeys",
            ],
            # Portuguese layouts
            "pt": [
                "",
                "mac",
                "mac_nodeadkeys",
                "mac_sundeadkeys",
                "nativo",
                "nativo-epo",
                "nativo-us",
                "nodeadkeys",
                "sun_type6",
                "sundeadkeys",
            ],
            # Brazilian Portuguese
            "br": [
                "",
                "dvorak",
                "nativo",
                "nativo-epo",
                "nativo-us",
                "nodeadkeys",
                "sun_type6",
                "thinkpad",
            ],
            # Russian layouts
            "ru": [
                "",
                "bak",
                "chm",
                "chu",
                "cv",
                "cv_latin",
                "dos",
                "komi",
                "legacy",
                "mac",
                "os_legacy",
                "os_winkeys",
                "phonetic",
                "phonetic_azerty",
                "phonetic_dvorak",
                "phonetic_fr",
                "phonetic_winkeys",
                "rulemak",
                "sah",
                "srp",
                "sun_type6",
                "tt",
                "typewriter",
                "typewriter-legacy",
                "udm",
                "xal",
            ],
            # Japanese layouts
            "jp": [
                "",
                "OADG109A",
                "dvorak",
                "kana",
                "kana86",
                "mac",
                "sun_type6",
                "sun_type7",
                "sun_type7_suncompat",
            ],
            # Chinese layouts
            "cn": ["", "altgr-pinyin", "tib", "tib_asciinum", "ug"],
            # Korean layouts
            "kr": ["", "kr104", "sun_type6"],
            # Arabic layouts
            "ara": [
                "",
                "azerty",
                "azerty_digits",
                "basic",
                "buckwalter",
                "digits",
                "mac",
                "olpc",
                "qwerty",
                "qwerty_digits",
                "sun_type6",
            ],
            # Hebrew layouts
            "il": ["", "biblical", "lyx", "phonetic", "sun_type6"],
            # Greek layouts
            "gr": [
                "",
                "extended",
                "nodeadkeys",
                "polytonic",
                "simple",
                "sun_type6",
            ],
            # Turkish layouts
            "tr": [
                "",
                "alt",
                "crh",
                "crh_alt",
                "crh_f",
                "f",
                "intl",
                "ku",
                "ku_alt",
                "ku_f",
                "sun_type6",
            ],
            # Polish layouts
            "pl": [
                "",
                "csb",
                "dvorak",
                "dvorak_altquotes",
                "dvorak_quotes",
                "dvp",
                "legacy",
                "qwertz",
                "ru_phonetic_dvorak",
                "sun_type6",
                "szl",
            ],
            # Dutch layouts
            "nl": ["", "mac", "std", "sun_type6", "sundeadkeys"],
            # Swedish layouts
            "se": [
                "",
                "dvorak",
                "dvorak_a5",
                "mac",
                "nodeadkeys",
                "rus",
                "rus_nodeadkeys",
                "smi",
                "sun_type6",
                "svdvorak",
                "swl",
                "us",
                "us_dvorak",
            ],
            # Norwegian layouts
            "no": [
                "",
                "colemak",
                "dvorak",
                "mac",
                "mac_nodeadkeys",
                "nodeadkeys",
                "smi",
                "smi_nodeadkeys",
                "sun_type6",
                "winkeys",
            ],
            # Danish layouts
            "dk": [
                "",
                "dvorak",
                "mac",
                "mac_nodeadkeys",
                "nodeadkeys",
                "sun_type6",
                "winkeys",
            ],
            # Finnish layouts
            "fi": [
                "",
                "classic",
                "das",
                "dvorak",
                "mac",
                "nodeadkeys",
                "smi",
                "sun_type6",
                "winkeys",
            ],
            # Czech layouts
            "cz": [
                "",
                "bksl",
                "dvorak-ucw",
                "qwerty",
                "qwerty_bksl",
                "sun_type6",
                "ucw",
            ],
            # Slovak layouts
            "sk": ["", "bksl", "qwerty", "qwerty_bksl", "sun_type6"],
            # Hungarian layouts
            "hu": [
                "",
                "101_qwerty_comma_dead",
                "101_qwerty_comma_nodead",
                "101_qwerty_dot_dead",
                "101_qwerty_dot_nodead",
                "101_qwertz_comma_dead",
                "101_qwertz_comma_nodead",
                "101_qwertz_dot_dead",
                "101_qwertz_dot_nodead",
                "102_qwerty_comma_dead",
                "102_qwerty_comma_nodead",
                "102_qwerty_dot_dead",
                "102_qwerty_dot_nodead",
                "102_qwertz_comma_dead",
                "102_qwertz_comma_nodead",
                "102_qwertz_dot_dead",
                "102_qwertz_dot_nodead",
                "nodeadkeys",
                "qwerty",
                "standard",
                "sun_type6",
            ],
            # Romanian layouts
            "ro": [
                "",
                "cedilla",
                "crh_dobruja",
                "std",
                "std_cedilla",
                "sun_type6",
                "winkeys",
            ],
            # Ukrainian layouts
            "ua": [
                "",
                "crh",
                "crh_alt",
                "crh_f",
                "homophonic",
                "legacy",
                "macOS",
                "phonetic",
                "rstu",
                "rstu_ru",
                "sun_type6",
                "typewriter",
                "winkeys",
            ],
            # Bulgarian layouts
            "bg": [
                "",
                "bas_phonetic",
                "bekl",
                "phonetic",
                "sun_type6",
            ],
            # Serbian layouts
            "rs": [
                "",
                "alterstrstrings",
                "combstrstrings",
                "latin",
                "latinyz",
                "latinunicodeyz",
                "latinunicode",
                "rue",
                "sun_type6",
            ],
            # Croatian layouts
            "hr": [
                "",
                "alterstrstrings",
                "combstrstrings",
                "sun_type6",
                "unicode",
                "unicodeus",
                "us",
            ],
            # Slovenian layouts
            "si": ["", "alterstrstrings", "sun_type6", "us"],
            # Lithuanian layouts
            "lt": [
                "",
                "ibm",
                "lekp",
                "lekpa",
                "ratise",
                "sgs",
                "std",
                "sun_type6",
                "us",
                "us_dvorak",
            ],
            # Latvian layouts
            "lv": [
                "",
                "adapted",
                "apostrophe",
                "ergonomic",
                "fkey",
                "modern",
                "sun_type6",
                "tilde",
            ],
            # Estonian layouts
            "ee": ["", "dvorak", "nodeadkeys", "sun_type6", "us"],
            # Swiss layouts (multilingual)
            "ch": [
                "",
                "de_mac",
                "de_nodeadkeys",
                "de_sundeadkeys",
                "fr",
                "fr_mac",
                "fr_nodeadkeys",
                "fr_sundeadkeys",
                "legacy",
                "sun_type6",
            ],
            # Belgian layouts
            "be": [
                "",
                "iso-alternate",
                "nodeadkeys",
                "oss",
                "oss_latin9",
                "oss_sundeadkeys",
                "sun_type6",
                "sundeadkeys",
                "wang",
            ],
            # Canadian layouts
            "ca": [
                "",
                "eng",
                "fr-dvorak",
                "fr-legacy",
                "ike",
                "kut",
                "multi",
                "multi-2gr",
                "multix",
                "shs",
                "sun_type6",
            ],
            # Indian layouts
            "in": [
                "",
                "ben",
                "ben_baishakhi",
                "ben_bornona",
                "ben_gitanjali",
                "ben_inscript",
                "ben_probhat",
                "bolnagri",
                "deva",
                "eng",
                "guj",
                "guru",
                "hin-kagapa",
                "hin-wx",
                "jhelum",
                "kan",
                "kan-kagapa",
                "mal",
                "mal_enhanced",
                "mal_lalitha",
                "mar-kagapa",
                "ori",
                "san-kagapa",
                "tam",
                "tam_TAB",
                "tam_TSCII",
                "tam_keyboard_with_numerals",
                "tam_unicode",
                "tel",
                "tel-kagapa",
                "urd-phonetic",
                "urd-phonetic3",
                "urd-winkeys",
            ],
            # Thai layouts
            "th": ["", "pat", "sun_type6", "tis"],
            # Vietnamese layouts
            "vn": ["", "fr", "us"],
            # Indonesian layouts
            "id": ["", "phoneticx"],
            # Catalan layouts
            "ad": [""],  # Andorra/Catalan
            # Irish layouts
            "ie": ["", "CloGaelach", "UnicodeExpert", "ogam", "ogam_is434"],
            # Default fallback
            "latam": [
                "",
                "colemak",
                "deadtilde",
                "dvorak",
                "nodeadkeys",
                "sun_type6",
            ],
        }

        # User selections
        self._selections: dict[str, Any] = {
            "locale": "en_US.UTF-8",
            "timezone": "UTC",
            "keymap": "us",
            "keyboardVariant": "qwerty",
            "username": "",
            "fullName": "",
            "hostname": "",
            "password": "",
            "rootPassword": "",
            "rootSameAsUser": True,
            "disk": "",
            "partitionMode": "auto",
            "filesystem": "xfs",
            "swapStrategy": "none",
            "encryption": False,
            "encryptionPassphrase": "",
            "efiSizeMb": 2048,
            # SECURITY: final confirmation gate. Destructive jobs (e.g. partition)
            # refuse to run for real unless this is armed to True at the
            # summary step. Defaults to False so an accidental start stays in a
            # non-destructive posture.
            "confirmed": False,
        }

        # Disk data
        self._disks_model: list[dict[str, Any]] = []

        # Manual partitioning: per-partition assignment keyed by partition name.
        # Each value: {"path": str, "mountpoint": str, "format": bool, "fstype": str}
        self._partition_assignments: dict[str, dict[str, Any]] = {}

        # Real partition name -> generated LUKS passphrase, populated after a
        # live Apply encrypts a partition without an explicit user passphrase.
        self._manual_luks_passphrases: dict[str, str] = {}

        # M2 manual editor: ordered list of pending operation dicts (the strict
        # QML<->Python contract) plus the last computed validity/error message.
        self._partition_operations: list[dict[str, Any]] = []
        self._manual_ops_valid: bool = False
        self._manual_ops_error: str = ""
        # Structural applicability (GParted-style), independent of a complete
        # installable layout: gates the Apply button.
        self._manual_ops_applicable: bool = False
        self._manual_ops_applicable_error: str = ""

        # Progress tracking
        self._overall_progress: int = 0
        self._current_job_name: str = ""
        self._current_job_message: str = ""
        self._jobs_list: list[dict[str, Any]] = []
        self._installation_status: str = "idle"  # idle, running, success, failed
        self._error_message: str = ""

        self._is_stalled: bool = False
        self._indeterminate: bool = False
        self._last_progress_ts: float | None = None
        self._stall_timer = QTimer(self)
        self._stall_timer.setInterval(1000)
        self._stall_timer.timeout.connect(self._check_stall)
        self._stall_timer.start()

        # Connect engine callbacks
        self._engine.on_job_start = self._on_job_start
        self._engine.on_job_progress = self._on_job_progress
        self._engine.on_job_complete = self._on_job_complete
        self._engine.on_error = self._on_error
        self._engine.on_job_indeterminate = self._on_job_indeterminate

        # Initialize requirements checker with config
        self._init_requirements_checker()

        # Online/offline gating: a single profile probes connectivity once at
        # startup instead of shipping separate online/offline configs. Jobs
        # read "has_internet" from selections and skip cleanly when False.
        self._has_internet = NetworkHelper.check_internet_connectivity()
        self._selections["has_internet"] = self._has_internet
        self._package_groups: list[dict[str, Any]] = self._load_package_groups()

    @property
    def branding_proxy(self) -> BrandingProxy:
        """Get branding proxy for QML context."""
        return self._branding_proxy

    def _init_requirements_checker(self) -> None:
        """Initialize the requirements checker with config from engine."""
        # Get requirements config from welcome job config if available
        welcome_config = {}
        for job_def in self._engine.config.normalize_jobs():
            if job_def.name == "welcome":
                welcome_config = job_def.config.get("requirements", {})
                break

        self._requirements_checker = SystemRequirementsChecker(welcome_config)

        if self._debug:
            print(f"[Engine] Requirements checker initialized with config: {welcome_config}")

    def _load_package_groups(self) -> list[dict[str, Any]]:
        """Load optional package groups from the packagechooser job's config.

        Mirrors PackageChooserJob._load_groups() so the UI shows exactly the
        groups the job will act on - "hidden" groups (always applied, never
        offered as a choice) are filtered out here.
        """
        groups_file = None
        for job_def in self._engine.config.normalize_jobs():
            if job_def.name == "packagechooser":
                groups_file = job_def.config.get("groups_file")
                break

        if not groups_file:
            return []

        try:
            with open(groups_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            if self._debug:
                print(f"[Engine] Failed to load package groups from {groups_file}: {e}")
            return []

        if not isinstance(data, list):
            return []

        groups = []
        for entry in data:
            if not isinstance(entry, dict) or entry.get("hidden"):
                continue

            raw_packages = entry.get("packages")
            package_list = raw_packages if isinstance(raw_packages, list) else []
            is_multi_choice = any(isinstance(p, dict) for p in package_list)

            packages: list[dict[str, Any]] = []
            if is_multi_choice:
                for pkg in package_list:
                    if not isinstance(pkg, dict):
                        continue
                    name = pkg.get("package")
                    if not name:
                        continue
                    packages.append(
                        {
                            "package": str(name),
                            "description": str(pkg.get("description", "")),
                            "selected": bool(pkg.get("selected", False)),
                        }
                    )

            groups.append(
                {
                    "id": str(entry.get("id", "")),
                    "name": str(entry.get("name", "")),
                    "description": str(entry.get("description", "")),
                    "screenshot": str(entry.get("screenshot", "")),
                    "details": str(entry.get("details", "")),
                    "selected": bool(entry.get("selected", False)),
                    "critical": bool(entry.get("critical", False)),
                    "isMultiChoice": is_multi_choice,
                    "packages": packages,
                }
            )
        return groups

    def _weighted_overall_progress(self, current_idx: int, percent: int) -> int:
        """Overall progress weighted by each job's estimate_duration(),
        so a long job (e.g. "install") advances the bar proportionally to
        how much of the total estimated time it actually represents."""
        if not (0 <= current_idx < len(self._job_weights)):
            return self._overall_progress
        completed_weight = sum(self._job_weights[:current_idx])
        current_weight = self._job_weights[current_idx]
        weighted = completed_weight + (current_weight * percent / 100)
        return int((weighted / self._total_job_weight) * 100)

    def _on_job_start(self, job_name: str) -> None:
        """Handle job start event."""
        self._current_job_name = job_name
        self._current_job_message = f"Starting: {_job_display_name(job_name)}..."
        self._overall_progress = self._weighted_overall_progress(self.getCurrentJobIndex(), 0)
        self._mark_progress_alive()
        self._update_jobs_list()
        self.progressChanged.emit()
        self.jobStarted.emit(job_name)

    def _on_job_progress(self, job_name: str, percent: int, message: str) -> None:
        """Handle job progress event."""
        self._current_job_name = job_name
        self._current_job_message = message
        self._overall_progress = self._weighted_overall_progress(
            self.getCurrentJobIndex(), percent
        )

        self._mark_progress_alive()
        self.progressChanged.emit()
        self.jobProgress.emit(job_name, percent, message)

    def _mark_progress_alive(self) -> None:
        """Enregistre un « battement » de progression (peut venir du thread worker).

        N'écrit qu'un timestamp (thread-safe : écriture atomique d'un float) et
        sort d'un éventuel état figé. Le passage EN état figé est décidé par
        ``_check_stall`` côté GUI — jamais ici — pour ne pas piloter le QTimer
        depuis le thread worker.
        """
        self._last_progress_ts = time.monotonic()
        if self._is_stalled:
            self._is_stalled = False
            self.progressChanged.emit()

    def _check_stall(self) -> None:
        """Sonde GUI (1 Hz) : bascule ``isStalled`` après un silence prolongé.

        Aucun progress reçu depuis ``_STALL_THRESHOLD_S`` secondes alors qu'une
        installation est en cours ⇒ la barre passe en mode « indéterminé »
        (build local silencieux). Le prochain progress ré-arme via
        :meth:`_mark_progress_alive`.
        """
        if self._last_progress_ts is None:
            return
        stalled = (time.monotonic() - self._last_progress_ts) >= self._STALL_THRESHOLD_S
        if stalled != self._is_stalled:
            self._is_stalled = stalled
            self.progressChanged.emit()

    def _on_job_complete(self, job_name: str, result: Any) -> None:
        """Handle job completion event."""
        self._update_jobs_list()
        self.progressChanged.emit()
        self.jobCompleted.emit(job_name, result.success)

    def _on_job_indeterminate(self, _job_name: str, active: bool) -> None:
        """Reflète le mode indéterminé demandé par le job (barre en pulse)."""
        if self._indeterminate != active:
            self._indeterminate = active
            self.progressChanged.emit()

    def _on_error(self, job_name: str, error: str) -> None:
        """Handle error event."""
        # Job failures only reach the UI's error card via this callback -
        # without logging it here too, a failing job is invisible in the
        # captured log (engine.logTail / the log sent to termbin), even
        # though it's the exact reason the install stopped.
        logger.error(f"Job '{job_name}' failed: {error}")
        self._error_message = error
        self._installation_status = "failed"
        self._reset_stall_state()
        if self._indeterminate:
            self._indeterminate = False
        self.progressChanged.emit()
        self.errorOccurred.emit(job_name, error)

    def _reset_stall_state(self) -> None:
        """Stoppe la détection de silence et sort de l'état figé (thread-safe)."""
        self._last_progress_ts = None
        if self._is_stalled:
            self._is_stalled = False
            self.progressChanged.emit()

    def _mark_log_dirty(self, _line: str) -> None:
        """Flag that new log line(s) arrived (called from the worker thread).

        Intentionally cheap — NO Qt signal here. Emitting one signal per line
        (some install jobs produce >10k lines) floods the GUI thread's event queue
        and freezes the window. ``_flush_log`` (a GUI-thread timer) coalesces the
        refreshes into a few per second instead.
        """
        self._log_dirty = True

    def _flush_log(self) -> None:
        """Emit a single coalesced log refresh when new lines have arrived."""
        if not self._log_dirty:
            return
        self._log_dirty = False
        self.logChanged.emit()

    @Property(str, notify=logChanged)
    def installationLog(self) -> str:
        """Get the full captured (redacted) installation log text."""
        return self._log_handler.get_text()

    @Property(str, notify=logChanged)
    def logTail(self) -> str:
        """Recent captured log lines for the live in-progress view (bounded).

        Only the tail is joined so each refresh stays cheap even when the full
        buffer holds thousands of lines.
        """
        return self._log_handler.get_tail(300)

    @Slot()
    def uploadInstallLog(self) -> None:
        """Upload the captured install log to a public pastebin, asynchronously."""
        if self._log_upload_thread is not None and self._log_upload_thread.isRunning():
            if self._debug:
                print("[Engine] Log upload already in progress")
            return

        text = self._log_handler.get_text()

        self._log_upload_thread = QThread(self)
        self._log_upload_worker = LogUploadWorker(text)
        self._log_upload_worker.moveToThread(self._log_upload_thread)

        self._log_upload_thread.started.connect(self._log_upload_worker.run)
        self._log_upload_worker.finished.connect(self._on_log_upload_finished)
        self._log_upload_worker.finished.connect(self._log_upload_thread.quit)
        self._log_upload_worker.finished.connect(self._log_upload_worker.deleteLater)
        self._log_upload_thread.finished.connect(self._log_upload_thread.deleteLater)
        self._log_upload_thread.finished.connect(self._cleanup_log_upload_thread)

        self._log_upload_thread.start()

    def _on_log_upload_finished(self, url: str, ok: bool, error_message: str) -> None:
        """Handle log upload completion."""
        if self._debug:
            status = url if ok else error_message
            print(f"[Engine] Log upload finished: {status}")
        self.logUploadFinished.emit(url, ok, error_message)

    def _cleanup_log_upload_thread(self) -> None:
        """Clean up log-upload thread references after completion."""
        self._log_upload_thread = None
        self._log_upload_worker = None

    @Property(bool, constant=True)
    def debugMode(self) -> bool:
        """Check if debug mode is enabled."""
        return self._debug

    @Property(bool, constant=True)
    def dryRun(self) -> bool:
        """Check if dry-run mode is enabled."""
        return self._dry_run

    @Property(bool, constant=True)
    def softwareRendering(self) -> bool:
        """Whether Qt Quick uses the software backend (no GPU).

        Under ``QT_QUICK_BACKEND=software`` (forced on the live ISO for
        GPU-less VMs), shader-based ``layer.effect``/``MultiEffect`` do not
        render, so QML guards ``layer.enabled`` with ``!engine.softwareRendering``
        to keep content visible (effects apply only on accelerated backends).
        """
        return os.environ.get("QT_QUICK_BACKEND", "") == "software"

    @Property(str, constant=True)
    def bootloaderType(self) -> str:
        """Which bootloader this install profile configures ("grub" or
        "systemd-boot") - set by the distro's config YAML, never changed by
        the user. Used only to decide whether to show the GRUB/Argon2id
        LUKS2 compatibility note near the encryption passphrase field."""
        return self._engine.bootloader_type()

    # =========================================================================
    # Font Properties for Non-Latin Languages
    # =========================================================================

    def _needs_unicode_font(self, locale: str) -> bool:
        """
        Check if the given locale needs a Unicode-complete font.

        Args:
            locale: Locale string (e.g., "zh_CN.UTF-8", "ja_JP.UTF-8")

        Returns:
            True if locale requires non-Latin font (CJK, Arabic, etc.)
        """
        # Check if locale starts with any non-Latin prefix
        return any(locale.startswith(prefix) for prefix in self.NON_LATIN_LOCALE_PREFIXES)

    @Property(str, notify=systemFontChanged)
    def systemFontFamily(self) -> str:
        """
        Get the recommended font family for the current locale.

        Returns:
            Font family name (e.g., "Noto Sans") or empty string for system default
        """
        current_locale = self._selections.get("locale", "en_US.UTF-8")
        if self._needs_unicode_font(current_locale):
            return self.UNICODE_FONT_FAMILY
        return self.LATIN_FONT_FAMILY

    @Property(bool, notify=systemFontChanged)
    def needsUnicodeFont(self) -> bool:
        """
        Check if current locale needs Unicode-complete font.

        Returns:
            True if current locale is non-Latin (CJK, Arabic, Hebrew, etc.)
        """
        current_locale = self._selections.get("locale", "en_US.UTF-8")
        return self._needs_unicode_font(current_locale)

    # Requirements properties
    @Property(bool, notify=requirementsChanged)
    def showRequirements(self) -> bool:
        """Whether to show requirements panel."""
        return self._show_requirements

    @Property(list, notify=requirementsChanged)
    def requirementsModel(self) -> list[dict[str, Any]]:
        """Get requirements list for QML ListView."""
        return self._requirements_model

    @Property(bool, notify=requirementsChanged)
    def canProceed(self) -> bool:
        """Whether installation can proceed based on requirements."""
        if self._skip_requirements:
            return True
        return self._can_proceed

    @Property(bool, constant=True)
    def skipValidation(self) -> bool:
        """Dev flag: bypass all per-screen validation gating (design testing)."""
        return self._skip_requirements

    @Property(bool, notify=requirementsChanged)
    def isCheckingRequirements(self) -> bool:
        """Whether requirements are currently being checked."""
        return self._is_checking_requirements

    @Slot()
    def checkRequirements(self) -> None:
        """Perform requirements check and update model."""
        if self._requirements_checker is None:
            self._init_requirements_checker()

        # Safety check for mypy (should never be None after init)
        if self._requirements_checker is None:
            return

        self._is_checking_requirements = True
        self.requirementsChanged.emit()

        if self._debug:
            print("[Engine] Starting requirements check...")

        # Perform the check
        result = self._requirements_checker.check_all()

        # Convert to QML-compatible list
        self._requirements_model = []
        for check in result.checks:
            self._requirements_model.append(
                {
                    "name": check.name,
                    "description": check.description,
                    "status": check.status.name.lower(),
                    "currentValue": check.current_value,
                    "requiredValue": check.required_value,
                    "recommendedValue": check.recommended_value,
                    "details": check.details,
                }
            )

        # Update state
        self._can_proceed = result.can_continue
        self._is_checking_requirements = False

        # Hide panel if no checks are configured/enabled
        self._show_requirements = len(result.checks) > 0

        if self._debug:
            print(f"[Engine] Requirements check complete: {len(result.checks)} checks")
            print(f"[Engine] Can proceed: {self._can_proceed}")
            print(f"[Engine] Show requirements panel: {self._show_requirements}")
            for item in self._requirements_model:
                print(f"[Engine]   - {item['name']}: {item['status']}")

        self.requirementsChanged.emit()

    @Property(bool, constant=True)
    def hasInternet(self) -> bool:
        """Whether internet connectivity was detected at startup."""
        return self._has_internet

    @Property(list, constant=True)
    def packageGroupsModel(self) -> list[dict[str, Any]]:
        """Optional package groups for the package-chooser step's QML view."""
        return self._package_groups

    @Slot(str, bool)
    def setPackageGroupSelected(self, group_id: str, selected: bool) -> None:
        """Record the user's checkbox choice for one bundle package group
        (all-or-nothing group, e.g. "Firefox")."""
        choices = dict(self._selections.get("packagechooserSelection", {}))
        choices[group_id] = selected
        self._selections["packagechooserSelection"] = choices
        self.selectionsChanged.emit()

    @Slot(str, str, bool)
    def setPackageSelected(self, group_id: str, package: str, selected: bool) -> None:
        """Record the user's checkbox choice for one package inside a
        multi-choice group (e.g. "gimp" within "Graphics & Design")."""
        choices = dict(self._selections.get("packagechooserPackages", {}))
        choices[f"{group_id}:{package}"] = selected
        self._selections["packagechooserPackages"] = choices
        self.selectionsChanged.emit()

    @Property(list, constant=True)
    def jobNames(self) -> list[str]:
        """Get list of job names in order."""
        return self._engine.get_job_names()

    @Property(int, constant=True)
    def totalJobs(self) -> int:
        """Get total number of jobs."""
        return len(self._engine.jobs)

    @Slot()
    def startInstallation(self) -> None:
        """Start the installation process in a separate thread."""
        # Prevent multiple concurrent installations
        if self._thread is not None and self._thread.isRunning():
            if self._debug:
                print("[Engine] Installation already in progress")
            return

        self.installationStarted.emit()
        self._install_start_time = time.monotonic()
        self._install_end_time = None

        # Create worker and thread
        self._thread = QThread(self)
        self._worker = InstallationWorker(self._engine)
        self._worker.moveToThread(self._thread)

        # Connect signals
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_installation_finished)
        self._worker.error.connect(self._on_installation_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup_thread)

        # Start installation
        if self._debug:
            print("[Engine] Starting installation in background thread")
        self._thread.start()

    def _on_installation_finished(self, success: bool) -> None:
        """Handle installation completion."""
        self._install_end_time = time.monotonic()
        self._reset_stall_state()
        if self._debug:
            print(f"[Engine] Installation finished: {'success' if success else 'failed'}")
        self.installationFinished.emit(success)

    def _on_installation_error(self, error_message: str) -> None:
        """Handle installation error."""
        logger.error(f"Installation error: {error_message}")
        self.errorOccurred.emit("installation", error_message)

    def _cleanup_thread(self) -> None:
        """Clean up thread references after completion."""
        self._thread = None
        self._worker = None

    @Slot()
    def resetInstallation(self) -> None:
        """Reset installation state so the user can retry after a failure.

        Clears the status/error, resets the engine and per-job state to a clean
        slate, and empties the captured log so the retry starts fresh. No-op
        while an installation is still running.
        """
        if self._thread is not None and self._thread.isRunning():
            return

        self._installation_status = "idle"
        self._error_message = ""
        self._reset_stall_state()
        self._engine.state.last_error = None
        self._engine.state.is_running = False
        self._engine.state.is_finished = False
        self._engine.state.current_job_index = 0
        for job in self._engine.jobs:
            job.status = JobStatus.PENDING
        self._update_jobs_list()
        self._log_handler.clear()

        self.progressChanged.emit()
        self.logChanged.emit()

    @Slot(result=int)
    def getCurrentJobIndex(self) -> int:
        """Get current job index (0-based)."""
        current, _ = self._engine.get_progress()
        return current - 1

    @Slot(result=str)
    def getCurrentJobName(self) -> str:
        """Get current job name."""
        idx = self.getCurrentJobIndex()
        if 0 <= idx < len(self._engine.jobs):
            return self._engine.jobs[idx].name
        return ""

    @Slot(result=bool)
    def isRunning(self) -> bool:
        """Check if installation is running."""
        return self._engine.state.is_running

    @Slot(result=bool)
    def isFinished(self) -> bool:
        """Check if installation is finished."""
        return self._engine.state.is_finished

    @Slot(result=str)
    def getLastError(self) -> str:
        """Get last error message."""
        return self._engine.state.last_error or ""

    # =========================================================================
    # Locale Data Properties
    # =========================================================================

    @Property(list, notify=localeDataChanged)
    def localesModel(self) -> list[str]:
        """Get available locales for QML."""
        return self._locales_model

    @Property(list, notify=localeDataChanged)
    def localesModelNative(self) -> list[dict[str, str]]:
        """Get available locales with native names for QML."""
        return self._locales_model_native

    @Property(list, notify=localeDataChanged)
    def timezonesModel(self) -> list[str]:
        """Get available timezones for QML."""
        return self._timezones_model

    @Property(list, notify=localeDataChanged)
    def keymapsModel(self) -> list[str]:
        """Get available keyboard layouts for QML."""
        return self._keymaps_model

    @Property(list, notify=keyboardVariantsChanged)
    def keyboardVariantsModel(self) -> list[str]:
        """Get available keyboard variants for selected layout."""
        return self._keyboard_variants_model

    @Property(str, notify=localeDataChanged)
    def detectedLocale(self) -> str:
        """Get auto-detected locale."""
        if self._locale_detection_result:
            return self._locale_detection_result.language
        return ""

    @Property(str, notify=localeDataChanged)
    def detectedTimezone(self) -> str:
        """Get auto-detected timezone."""
        if self._locale_detection_result:
            return self._locale_detection_result.timezone
        return ""

    @Property(str, notify=localeDataChanged)
    def detectedKeymap(self) -> str:
        """Get auto-detected keymap."""
        if self._locale_detection_result:
            return self._locale_detection_result.keymap
        return ""

    @Property(str, notify=localeDataChanged)
    def detectionSource(self) -> str:
        """Get detection source (geoip, cmdline, efi, default)."""
        if self._locale_detection_result:
            return self._locale_detection_result.source
        return ""

    @Property(float, notify=localeDataChanged)
    def detectionConfidence(self) -> float:
        """Get detection confidence (0.0-1.0)."""
        if self._locale_detection_result:
            return self._locale_detection_result.confidence
        return 0.0

    def _load_locale_auto_detection_config(self) -> None:
        """Load auto-detection configuration from locale job config."""
        for job_def in self._engine.config.normalize_jobs():
            if job_def.name == "locale":
                self._locale_auto_detection_config = job_def.config.get("auto_detection", {})
                break
        if self._debug:
            print(f"[Engine] Locale auto-detection config: {self._locale_auto_detection_config}")

    def _run_locale_detection(self) -> None:
        """Run locale auto-detection if enabled."""
        if not self._locale_auto_detection_config.get("enabled", False):
            if self._debug:
                print("[Engine] Locale auto-detection disabled")
            return

        detector = LocaleDetector.from_config(self._locale_auto_detection_config)
        self._locale_detection_result = detector.detect()

        if self._debug:
            result = self._locale_detection_result
            print(
                f"[Engine] Auto-detected locale: {result.language} "
                f"(source: {result.source}, confidence: {result.confidence})"
            )
            print(f"[Engine] Auto-detected timezone: {result.timezone}")
            print(f"[Engine] Auto-detected keymap: {result.keymap}")

        # Apply detected values if confidence is high enough
        threshold = self._locale_auto_detection_config.get("confidence_threshold", 0.8)
        if self._locale_detection_result.confidence >= threshold:
            self._selections["locale"] = self._locale_detection_result.language
            self._selections["timezone"] = self._locale_detection_result.timezone
            self._selections["keymap"] = self._locale_detection_result.keymap
            if self._debug:
                print("[Engine] Applied auto-detected locale settings")
            # Emit signal to update QML UI with detected values
            self._update_keyboard_variants(self._selections["keymap"])
            self.selectionsChanged.emit()

    @Slot()
    def loadLocaleData(self) -> None:
        """Load locale, timezone, and keymap data."""
        if self._debug:
            print("[Engine] Loading locale data...")

        # Load auto-detection config and run detection
        self._load_locale_auto_detection_config()
        self._run_locale_detection()

        # Load locales from LocaleJob
        self._locales_model = list(LocaleJob.COMMON_LOCALES)

        # Build native names model
        self._locales_model_native = []
        for locale_code in self._locales_model:
            native_name = LOCALE_NATIVE_NAMES.get(locale_code, locale_code)
            self._locales_model_native.append({"code": locale_code, "name": native_name})

        # Load timezones from system
        locale_job = LocaleJob()
        self._timezones_model = locale_job._get_available_timezones()

        # Load keymaps from LocaleJob
        self._keymaps_model = list(LocaleJob.COMMON_KEYMAPS)

        if self._debug:
            print(f"[Engine] Loaded {len(self._locales_model)} locales")
            print(f"[Engine] Loaded {len(self._timezones_model)} timezones")
            print(f"[Engine] Loaded {len(self._keymaps_model)} keymaps")

        self.localeDataChanged.emit()

    # =========================================================================
    # User Selections Properties
    # =========================================================================

    @Property(str, notify=selectionsChanged)
    def selectedLocale(self) -> str:
        """Get selected locale."""
        return str(self._selections.get("locale", "en_US.UTF-8"))

    @Slot(str)
    def setSelectedLocale(self, locale: str) -> None:
        """Set selected locale and update font/keymap if needed."""
        if self._selections.get("locale") != locale:
            old_locale = self._selections.get("locale", "en_US.UTF-8")
            self._selections["locale"] = locale

            if self._debug:
                print(f"[Engine] Locale set to: {locale}")

            # Auto-derive keymap from locale
            derived_keymap = self._derive_keymap_from_locale(locale)
            if self._selections.get("keymap") != derived_keymap:
                self._selections["keymap"] = derived_keymap
                self._update_keyboard_variants(derived_keymap)
                if self._debug:
                    print(f"[Engine] Keymap auto-derived to: {derived_keymap}")

            # Check if font needs to change (Latin <-> non-Latin transition)
            old_needs_unicode = self._needs_unicode_font(old_locale)
            new_needs_unicode = self._needs_unicode_font(locale)
            if old_needs_unicode != new_needs_unicode:
                if self._debug:
                    font = self.UNICODE_FONT_FAMILY if new_needs_unicode else "system default"
                    print(f"[Engine] Font changed to: {font}")
                self.systemFontChanged.emit()

            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def selectedTimezone(self) -> str:
        """Get selected timezone."""
        return str(self._selections.get("timezone", "UTC"))

    @Slot(str)
    def setSelectedTimezone(self, timezone: str) -> None:
        """Set selected timezone."""
        if self._selections.get("timezone") != timezone:
            self._selections["timezone"] = timezone
            if self._debug:
                print(f"[Engine] Timezone set to: {timezone}")
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def selectedKeymap(self) -> str:
        """Get selected keyboard layout."""
        return str(self._selections.get("keymap", "us"))

    @Slot(str)
    def setSelectedKeymap(self, keymap: str) -> None:
        """Set selected keyboard layout and update available variants."""
        if self._selections.get("keymap") != keymap:
            self._selections["keymap"] = keymap

            # Update keyboard variants for new layout
            self._update_keyboard_variants(keymap)

            if self._debug:
                print(f"[Engine] Keymap set to: {keymap}")
                print(f"[Engine] Available variants: {self._keyboard_variants_model}")
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def selectedKeyboardVariant(self) -> str:
        """Get selected keyboard variant."""
        return str(self._selections.get("keyboardVariant", "qwerty"))

    @Slot(str)
    def setSelectedKeyboardVariant(self, variant: str) -> None:
        """Set selected keyboard variant."""
        if self._selections.get("keyboardVariant") != variant:
            self._selections["keyboardVariant"] = variant
            if self._debug:
                print(f"[Engine] Keyboard variant set to: {variant}")
            self.selectionsChanged.emit()

    def _update_keyboard_variants(self, keymap: str) -> None:
        """Update keyboard variants model based on selected keymap."""
        # Extract base layout code (e.g., "us" from "us" or "fr_CA" from "fr")
        base_layout = keymap.split("_")[0] if "_" in keymap else keymap

        # Get variants for this layout (default to qwerty if unknown)
        self._keyboard_variants_model = self._keyboard_variants.get(base_layout, ["qwerty"])

        # Auto-select first variant if current one is not available
        current_variant = self._selections.get("keyboardVariant", "")
        if current_variant not in self._keyboard_variants_model:
            self._selections["keyboardVariant"] = self._keyboard_variants_model[0]

        self.keyboardVariantsChanged.emit()

    @Slot(str, str, result=bool)
    def applyKeyboardLayout(self, layout: str, variant: str) -> bool:
        """
        Apply a keyboard layout to the live installer session (best-effort).

        This only affects the running session (GNOME Wayland via gsettings,
        X11 via setxkbmap fallback) so every input field in the installer
        reflects the chosen layout immediately. It never touches the target
        install config, which stays driven by setSelectedKeymap /
        setSelectedKeyboardVariant.
        """
        applied = apply_keyboard_layout_live(layout, variant)
        if self._debug:
            print(f"[Engine] Live keyboard apply ({layout}+{variant}): {applied}")
        return applied

    @Property(str, notify=selectionsChanged)
    def username(self) -> str:
        """Get username."""
        return str(self._selections.get("username", ""))

    @Slot(str)
    def setUsername(self, username: str) -> None:
        """Set username."""
        if self._selections.get("username") != username:
            self._selections["username"] = username
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def fullName(self) -> str:
        """Get full name."""
        return str(self._selections.get("fullName", ""))

    @Slot(str)
    def setFullName(self, fullName: str) -> None:
        """Set full name."""
        if self._selections.get("fullName") != fullName:
            self._selections["fullName"] = fullName
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def hostname(self) -> str:
        """Get hostname."""
        return str(self._selections.get("hostname", ""))

    @Slot(str)
    def setHostname(self, hostname: str) -> None:
        """Set hostname."""
        if self._selections.get("hostname") != hostname:
            self._selections["hostname"] = hostname
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def password(self) -> str:
        """Get password (for internal use, not displayed)."""
        return str(self._selections.get("password", ""))

    @Slot(str)
    def setPassword(self, password: str) -> None:
        """Set password."""
        if self._selections.get("password") != password:
            self._selections["password"] = password
            self._redactor.add_secret(password)
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def rootPassword(self) -> str:
        """Get root password (for internal use, not displayed)."""
        return str(self._selections.get("rootPassword", ""))

    @Slot(str)
    def setRootPassword(self, rootPassword: str) -> None:
        """Set root password (SECURITY: never logged, same posture as password)."""
        if self._selections.get("rootPassword") != rootPassword:
            self._selections["rootPassword"] = rootPassword
            self._redactor.add_secret(rootPassword)
            self.selectionsChanged.emit()

    @Property(bool, notify=selectionsChanged)
    def rootSameAsUser(self) -> bool:
        """Get whether the root password mirrors the user account password."""
        return bool(self._selections.get("rootSameAsUser", True))

    @Slot(bool)
    def setRootSameAsUser(self, rootSameAsUser: bool) -> None:
        """Set whether the root password mirrors the user account password."""
        if self._selections.get("rootSameAsUser") != rootSameAsUser:
            self._selections["rootSameAsUser"] = rootSameAsUser
            self.selectionsChanged.emit()

    # =========================================================================
    # Disk/Partition Properties
    # =========================================================================

    @Property(list, notify=disksChanged)
    def disksModel(self) -> list[dict[str, Any]]:
        """Get available disks for QML."""
        return self._disks_model

    @Property(str, notify=selectionsChanged)
    def selectedDisk(self) -> str:
        """Get selected disk."""
        return str(self._selections.get("disk", ""))

    @Slot(str)
    def setSelectedDisk(self, disk: str) -> None:
        """Set selected disk."""
        if self._selections.get("disk") != disk:
            self._selections["disk"] = disk
            if self._debug:
                print(f"[Engine] Disk set to: {disk}")
            self.selectionsChanged.emit()
            # The M2 simulation/validation is scoped to the selected disk, so a
            # disk change invalidates the previous geometry and must re-notify.
            self._revalidate_operations()
            self.partitionOperationsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def selectedDiskSize(self) -> str:
        """Get the human-readable size of the selected disk (empty if unknown).

        Derived from the scanned disks model on the fly so the summary can
        display the target disk capacity. Notified via ``selectionsChanged`` so
        QML re-evaluates it whenever the disk selection changes.
        """
        return self._get_disk_size(str(self._selections.get("disk", "")))

    @Property(str, notify=selectionsChanged)
    def partitionMode(self) -> str:
        """Get partition mode (auto/manual)."""
        return str(self._selections.get("partitionMode", "auto"))

    @Slot(str)
    def setPartitionMode(self, mode: str) -> None:
        """Set partition mode."""
        if self._selections.get("partitionMode") != mode:
            self._selections["partitionMode"] = mode
            if self._debug:
                print(f"[Engine] Partition mode set to: {mode}")
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def filesystem(self) -> str:
        """Get selected root filesystem (ext4/xfs/btrfs)."""
        return str(self._selections.get("filesystem", "xfs"))

    @Slot(str)
    def setFilesystem(self, filesystem: str) -> None:
        """Set root filesystem type."""
        if self._selections.get("filesystem") != filesystem:
            self._selections["filesystem"] = filesystem
            if self._debug:
                print(f"[Engine] Filesystem set to: {filesystem}")
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def swapStrategy(self) -> str:
        """Get swap strategy (file/none/hibernate)."""
        return str(self._selections.get("swapStrategy", "none"))

    @Slot(str)
    def setSwapStrategy(self, strategy: str) -> None:
        """Set swap strategy."""
        if self._selections.get("swapStrategy") != strategy:
            self._selections["swapStrategy"] = strategy
            if self._debug:
                print(f"[Engine] Swap strategy set to: {strategy}")
            self.selectionsChanged.emit()

    @Property(bool, notify=selectionsChanged)
    def encryption(self) -> bool:
        """Get whether root encryption (LUKS2) is enabled."""
        return bool(self._selections.get("encryption", False))

    @Slot(bool)
    def setEncryption(self, enabled: bool) -> None:
        """Set whether root encryption is enabled."""
        if self._selections.get("encryption") != enabled:
            self._selections["encryption"] = enabled
            if self._debug:
                print(f"[Engine] Encryption set to: {enabled}")
            self.selectionsChanged.emit()

    @Property(str, notify=selectionsChanged)
    def encryptionPassphrase(self) -> str:
        """Get LUKS passphrase (for internal use, never displayed)."""
        return str(self._selections.get("encryptionPassphrase", ""))

    @Slot(str)
    def setEncryptionPassphrase(self, passphrase: str) -> None:
        """Set LUKS passphrase (SECURITY: never logged, same posture as password)."""
        if self._selections.get("encryptionPassphrase") != passphrase:
            self._selections["encryptionPassphrase"] = passphrase
            self._redactor.add_secret(passphrase)
            if self._debug:
                print("[Engine] Encryption passphrase set (hidden)")
            self.selectionsChanged.emit()

    @Property(int, notify=selectionsChanged)
    def efiSizeMb(self) -> int:
        """Get EFI System Partition size in MiB."""
        return int(self._selections.get("efiSizeMb", 2048))

    @Slot(int)
    def setEfiSizeMb(self, size: int) -> None:
        """Set EFI System Partition size in MiB."""
        if self._selections.get("efiSizeMb") != size:
            self._selections["efiSizeMb"] = size
            if self._debug:
                print(f"[Engine] EFI size set to: {size} MB")
            self.selectionsChanged.emit()

    # =========================================================================
    # Final installation confirmation (security gate)
    # =========================================================================

    @Property(bool, notify=selectionsChanged)
    def confirmed(self) -> bool:
        """Whether the user armed the final, destructive installation."""
        return bool(self._selections.get("confirmed", False))

    @Slot(bool)
    def setConfirmed(self, confirmed: bool) -> None:
        """
        Arm/disarm the final installation gate.

        SECURITY: destructive jobs (e.g. partition) refuse to perform any real,
        destructive operation unless this flag is True (see their run() guards).
        SummaryView is expected to expose a confirmation checkbox bound to this
        slot before enabling the "Install" action.
        """
        if self._selections.get("confirmed") != confirmed:
            self._selections["confirmed"] = confirmed
            if self._debug:
                print(f"[Engine] Installation confirmed set to: {confirmed}")
            self.selectionsChanged.emit()

    # =========================================================================
    # Manual partitioning plan (assign existing partitions)
    # =========================================================================

    def _assignment(self, name: str) -> dict[str, Any]:
        """Return (creating if needed) the assignment record for a partition."""
        return self._partition_assignments.setdefault(
            name,
            {"path": f"/dev/{name}", "mountpoint": "", "format": False, "fstype": ""},
        )

    @Slot(str, str)
    def setPartitionMount(self, name: str, mountpoint: str) -> None:
        """Assign (or clear, with "") the mount point of an existing partition."""
        entry = self._assignment(name)
        if entry["mountpoint"] != mountpoint:
            entry["mountpoint"] = mountpoint
            if self._debug:
                print(f"[Engine] Partition {name} mount -> {mountpoint or '(unused)'}")
            self.partitionPlanChanged.emit()
            # manualPlanValid/manualPlanError are declared with
            # notify=partitionOperationsChanged (shared with the M2 operation
            # editor); without this, QML's binding to them never re-reads
            # after an M1 mount-point assignment and the validation banner
            # stays stuck on stale state. Queued-op validity also depends on
            # assignments (encryption rules see root's applied state).
            self._revalidate_operations()
            self.partitionOperationsChanged.emit()

    @Slot(str, bool)
    def setPartitionFormat(self, name: str, do_format: bool) -> None:
        """Toggle whether an existing partition is reformatted."""
        entry = self._assignment(name)
        if entry["format"] != do_format:
            entry["format"] = do_format
            if self._debug:
                print(f"[Engine] Partition {name} format -> {do_format}")
            self.partitionPlanChanged.emit()
            self._revalidate_operations()
            self.partitionOperationsChanged.emit()

    @Slot(str, str)
    def setPartitionFsType(self, name: str, fstype: str) -> None:
        """Set the target filesystem used when a partition is reformatted."""
        entry = self._assignment(name)
        if entry["fstype"] != fstype:
            entry["fstype"] = fstype
            if self._debug:
                print(f"[Engine] Partition {name} fstype -> {fstype}")
            self.partitionPlanChanged.emit()
            self._revalidate_operations()
            self.partitionOperationsChanged.emit()

    @Slot(str, str)
    def setPartitionSubvolumes(self, name: str, subvolumes_json: str) -> None:
        """
        Set (or clear, with ``"[]"``) the Btrfs subvolume layout for a
        partition, mounted per-subvolume at install time instead of the
        assignment's own bare mount point (see ``_mount_manual``).

        Takes JSON (``JSON.stringify`` from QML) for the same PySide6
        QVariant-marshalling reason as ``addPartitionOperation``.
        """
        try:
            subvolumes = json.loads(subvolumes_json) if subvolumes_json else []
        except (ValueError, TypeError) as exc:
            if self._debug:
                print(f"[Engine] REJECTED subvolumes payload ({exc}): {subvolumes_json!r}")
            return
        entry = self._assignment(name)
        if entry.get("subvolumes") != subvolumes:
            entry["subvolumes"] = subvolumes
            if self._debug:
                print(f"[Engine] Partition {name} subvolumes -> {subvolumes}")
            self.partitionPlanChanged.emit()
            self._revalidate_operations()
            self.partitionOperationsChanged.emit()

    @Slot(str, result=str)
    def partitionSubvolumes(self, name: str) -> str:
        """Current Btrfs subvolume layout for a partition, as a JSON string ('[]' if none)."""
        return json.dumps(self._partition_assignments.get(name, {}).get("subvolumes", []))

    @Slot(str, result=str)
    def partitionMount(self, name: str) -> str:
        """Current mount point assigned to a partition (empty if unused)."""
        return str(self._partition_assignments.get(name, {}).get("mountpoint", ""))

    @Slot(str, result=bool)
    def partitionFormat(self, name: str) -> bool:
        """Whether a partition is flagged for reformatting."""
        return bool(self._partition_assignments.get(name, {}).get("format", False))

    @Slot(str, result=str)
    def partitionFsType(self, name: str) -> str:
        """Target filesystem for a partition (empty means keep current)."""
        return str(self._partition_assignments.get(name, {}).get("fstype", ""))

    def _used_assignments(self) -> list[dict[str, Any]]:
        """M1 assignments the user actually touched, across every disk.

        Mount assignment is global (root and /home can each sit on a
        different physical disk), so this is never scoped to the currently
        selected disk - matches :func:`validate_manual_assignments` and
        ``PartitionJob._partition_manual``'s own ``used`` filter.
        """
        return [
            {"name": name, **entry}
            for name, entry in self._partition_assignments.items()
            if entry.get("mountpoint")
            or entry.get("format")
            or entry.get("subvolumes")
            or entry.get("raw_path")
        ]

    def _manual_assignment_validity(self) -> tuple[bool, str]:
        """The M1 fallback validity check (no pending M2 operations)."""
        used = self._used_assignments()
        if not used:
            return False, "No partition is mounted at /"
        uefi = Path("/sys/firmware/efi").exists()
        return validate_manual_assignments(self._disks_model, used, uefi=uefi)

    @Property(bool, notify=partitionOperationsChanged)
    def manualPlanValid(self) -> bool:
        """
        Global validity of the manual plan.

        When the M2 operation editor holds pending operations, validity is
        driven by :func:`validate_operations` over the selected disk geometry.
        Otherwise it falls back to :func:`validate_manual_assignments` across
        every disk (root/ESP/boot-mountpoint/Btrfs-subvolume correctness).
        """
        if self._partition_operations:
            return self._manual_ops_valid
        valid, _reason = self._manual_assignment_validity()
        return valid

    @Property(str, notify=partitionOperationsChanged)
    def manualPlanError(self) -> str:
        """
        Human-readable error for the manual plan (plain English; UI wraps qsTr).

        Empty when the plan is valid. Reflects the M2 operation validation when
        operations exist, else the M1 assignment validation.
        """
        if self._partition_operations:
            return self._manual_ops_error
        _valid, reason = self._manual_assignment_validity()
        return reason

    @Property(bool, notify=partitionOperationsChanged)
    def operationsApplicable(self) -> bool:
        """
        Whether the pending operations can be written to disk (GParted-style).

        Independent of a complete installable layout: this gates the Apply
        button so delete/create/resize can be applied without first assigning a
        root/ESP. Install completeness is enforced separately by
        ``manualPlanValid`` at navigation time.
        """
        return bool(self._partition_operations) and self._manual_ops_applicable

    @Property(str, notify=partitionOperationsChanged)
    def operationsApplicableError(self) -> str:
        """Human-readable reason the pending operations cannot be applied."""
        if not self._partition_operations:
            return ""
        return self._manual_ops_applicable_error

    # =========================================================================
    # Manual partitioning editor (M2: create/delete/format/setflag/resize)
    # =========================================================================

    def _selected_disk_geometry(self) -> dict[str, Any]:
        """Return the disk_detector contract dict for the selected disk (or {})."""
        selected = str(self._selections.get("disk", ""))
        tail = selected.rsplit("/", 1)[-1]
        for disk in self._disks_model:
            if disk.get("name") == tail or f"/dev/{disk.get('name')}" == selected:
                return disk
        return {}

    def _parsed_operations(self) -> list[PartitionOperation]:
        """Parse the pending operation dicts, dropping malformed ones defensively."""
        parsed: list[PartitionOperation] = []
        for raw in self._partition_operations:
            try:
                parsed.append(PartitionOperation.from_dict(raw))
            except ValueError:
                continue
        return parsed

    def _revalidate_operations(self) -> None:
        """Recompute validity/error for the current operations + selected disk.

        Two levels: ``applicable`` (structural + encryption rules, gates Apply)
        and ``valid`` (complete installable layout, gates navigation/install).
        """
        if not self._partition_operations:
            self._manual_ops_valid = False
            self._manual_ops_error = ""
            self._manual_ops_applicable = False
            self._manual_ops_applicable_error = ""
            return
        geom = self._selected_disk_geometry()
        if not geom:
            self._manual_ops_valid = False
            self._manual_ops_error = "No disk selected"
            self._manual_ops_applicable = False
            self._manual_ops_applicable_error = "No disk selected"
            return
        uefi = Path("/sys/firmware/efi").exists()
        try:
            operations = [PartitionOperation.from_dict(op) for op in self._partition_operations]
        except ValueError as exc:
            self._manual_ops_valid = False
            self._manual_ops_error = str(exc)
            self._manual_ops_applicable = False
            self._manual_ops_applicable_error = str(exc)
            return
        applied = self._used_assignments()
        applicable, applicable_error = validate_operations_applicable(geom, operations)
        if applicable:
            applicable, applicable_error = validate_encryption_rules(geom, operations, applied)
        self._manual_ops_applicable = applicable
        self._manual_ops_applicable_error = applicable_error
        valid, error = validate_operations(geom, operations, uefi=uefi, applied_assignments=applied)
        self._manual_ops_valid = valid
        self._manual_ops_error = error

    @Property(list, notify=partitionOperationsChanged)
    def pendingOperations(self) -> list[dict[str, Any]]:
        """The ordered list of pending operation dicts (contract shape)."""
        return self._partition_operations

    @Slot(str)
    def addPartitionOperation(self, op_json: str) -> None:
        """
        Append an operation received as a JSON string from QML.

        The operation is passed as JSON (``JSON.stringify`` in QML) rather than
        a plain QML object because a JS object does NOT marshal reliably to a
        QVariant slot across PySide6 versions — the slot would silently never be
        invoked, leaving the queue empty. Malformed / invalid operations are
        rejected (surfaced through manualPlanError) rather than raising.
        """
        op_dict: Any = op_json
        if isinstance(op_json, str):
            try:
                op_dict = json.loads(op_json)
            except (ValueError, TypeError) as exc:
                self._manual_ops_valid = False
                self._manual_ops_error = f"Malformed operation payload: {exc}"
                self.partitionOperationsChanged.emit()
                return
        params = op_dict.get("params") if isinstance(op_dict, dict) else None
        passphrase = params.get("encryption_passphrase") if isinstance(params, dict) else None
        if isinstance(passphrase, str) and passphrase:
            self._redactor.add_secret(passphrase)
        try:
            PartitionOperation.from_dict(op_dict)
        except ValueError as exc:
            self._manual_ops_valid = False
            self._manual_ops_error = str(exc)
            if self._debug:
                print(f"[Engine] REJECTED partition op ({exc}): {self._redactor.redact(repr(op_dict))}")
            self.partitionOperationsChanged.emit()
            return
        self._partition_operations.append(op_dict)
        if self._debug:
            print(f"[Engine] Added partition op: {op_dict.get('type')} on {op_dict.get('target')}")
        self._revalidate_operations()
        self.partitionOperationsChanged.emit()

    @Slot(int)
    def removePartitionOperation(self, index: int) -> None:
        """Remove the operation at ``index`` (no-op if out of range), revalidate."""
        if 0 <= index < len(self._partition_operations):
            removed = self._partition_operations.pop(index)
            if self._debug:
                print(f"[Engine] Removed partition op #{index}: {removed.get('type')}")
            self._revalidate_operations()
            self.partitionOperationsChanged.emit()

    @Slot()
    def resetPartitionOperations(self) -> None:
        """Clear all pending operations and reset validity/error."""
        if self._partition_operations:
            self._partition_operations = []
            self._manual_ops_valid = False
            self._manual_ops_error = ""
            self._manual_ops_applicable = False
            self._manual_ops_applicable_error = ""
            if self._debug:
                print("[Engine] Cleared all partition operations")
            self.partitionOperationsChanged.emit()

    @Property(bool, notify=partitionApplyingChanged)
    def partitionApplying(self) -> bool:
        """True while a live partition-apply is running (UI shows a busy state)."""
        return self._partition_applying

    @Slot()
    def applyPartitionOperations(self) -> None:
        """
        Execute the queued M2 operations on the selected disk (GParted-style),
        off the UI thread, then rescan so 'Available Disks' shows the real new
        layout. Requires privileges (sgdisk/mkfs): on the live ISO Xen runs as
        root; in dev, launch the app with sudo.
        """
        if self._partition_applying:
            return
        selected = str(self._selections.get("disk", ""))
        if not selected or not self._partition_operations:
            self.partitionApplyFinished.emit(False, "No operations to apply")
            return
        self._revalidate_operations()
        if not self._manual_ops_applicable:
            self.partitionApplyFinished.emit(
                False, self._manual_ops_applicable_error or "Operations cannot be applied"
            )
            return

        disk_path = selected if selected.startswith("/dev/") else f"/dev/{selected}"

        # Not self._selections: nothing in the UI sets "bootloader"; the
        # bootloader job's yaml type: is authoritative.
        bootloader = self._engine.bootloader_type()
        has_separate_boot = any(
            op.get("params", {}).get("mountpoint") == "/boot"
            for op in self._partition_operations
        ) or manual_has_separate_boot(self._used_assignments())

        self._partition_applying = True
        self.partitionApplyingChanged.emit()

        self._apply_thread = QThread(self)
        self._apply_worker = PartitionApplyWorker(
            disk_path, list(self._partition_operations), self._dry_run,
            bootloader, has_separate_boot,
        )
        self._apply_worker.moveToThread(self._apply_thread)
        self._apply_thread.started.connect(self._apply_worker.run)
        self._apply_worker.finished.connect(self._on_partition_apply_finished)
        self._apply_worker.finished.connect(self._apply_thread.quit)
        self._apply_worker.finished.connect(self._apply_worker.deleteLater)
        self._apply_thread.finished.connect(self._apply_thread.deleteLater)
        self._apply_thread.finished.connect(self._cleanup_apply_thread)
        self._apply_thread.start()

    def _reconcile_after_apply(self, applied_ops: list[dict[str, Any]]) -> None:
        """
        Promote each applied create/format op's queued mountpoint/encryption
        intent onto the real partition it now corresponds to.

        Fixes a pre-existing gap: a newly-created partition's Mount Point
        field was captured in its `create` op's params but nothing ever
        carried it into `_partition_assignments` (keyed by the partition's
        real, post-Apply kernel name) - only the Edit dialog's
        `setPartitionMount`, on an already-existing partition, worked. This
        also has to exist for `encrypt` to correctly resolve to a mapper
        device path for a newly-created partition, so both are fixed here
        together.
        """
        disk = self._selected_disk_geometry()
        segments = disk.get("segments", [])

        for op in applied_ops:
            if op.get("type") not in ("create", "format"):
                continue
            params = op.get("params", {})
            mountpoint = params.get("mountpoint")
            encrypt = bool(params.get("encrypt"))
            if not mountpoint and not encrypt and not params.get("subvolumes"):
                continue

            real_name = self._resolve_real_partition_name(op, segments)
            if not real_name:
                continue

            entry = self._assignment(real_name)
            if mountpoint:
                entry["mountpoint"] = mountpoint
            if params.get("fstype"):
                entry["fstype"] = params["fstype"]
            if params.get("subvolumes"):
                entry["subvolumes"] = params["subvolumes"]
            if encrypt:
                entry["raw_path"] = f"/dev/{real_name}"
                entry["path"] = f"/dev/mapper/{manual_mapper_name_for(params)}"
            else:
                entry.pop("raw_path", None)
                entry["path"] = f"/dev/{real_name}"

        self.partitionPlanChanged.emit()

    @staticmethod
    def _resolve_real_partition_name(
        op: dict[str, Any], segments: list[dict[str, Any]]
    ) -> str | None:
        """Match a `create`/`format` op to the real partition it now
        corresponds to, by start sector - same sector-matching
        `_resolve_partition_number` already does job-side, mirrored here
        client-side against freshly-rescanned disk geometry."""
        params = op.get("params", {})
        if op.get("type") == "format":
            path = str(params.get("path", ""))
            return path.rsplit("/", 1)[-1] if path else None
        start = params.get("start_sector")
        if start is None:
            return None
        for seg in segments:
            if seg.get("kind") == "partition" and seg.get("startSector") == start:
                return str(seg.get("name", ""))
        return None

    def _on_partition_apply_finished(
        self, success: bool, message: str, passphrases: dict[str, str]
    ) -> None:
        """Main-thread handler: on success, reconcile queued intent onto
        real partition names, carry the batch's LUKS passphrases forward
        (keyed by real partition name), clear the queue, and rescan."""
        self._partition_applying = False
        self.partitionApplyingChanged.emit()
        if success:
            self.refreshDisks()
            self._reconcile_after_apply(list(self._partition_operations))
            for raw_path, pw in passphrases.items():
                self._redactor.add_secret(pw)
                self._manual_luks_passphrases[raw_path.rsplit("/", 1)[-1]] = pw
            self.resetPartitionOperations()
        if self._debug:
            print(f"[Engine] Partition apply finished: success={success} ({message})")
        self.partitionApplyFinished.emit(success, message)

    def _cleanup_apply_thread(self) -> None:
        """Drop the finished apply thread/worker references."""
        self._apply_thread = None
        self._apply_worker = None

    @Property(list, notify=partitionOperationsChanged)
    def simulatedSegments(self) -> list[dict[str, Any]]:
        """
        Geometry of the selected disk AFTER the pending operations.

        Each segment carries the exact UI contract shape (see
        :func:`xen.jobs.partition.simulate_operations`). With no operations,
        this is the disk's current segment geometry normalized to that shape.
        """
        geom = self._selected_disk_geometry()
        segments = geom.get("segments", []) if geom else []
        operations = self._parsed_operations()
        return simulate_operations(segments, operations)

    @Property(list, notify=partitionOperationsChanged)
    def commandPreview(self) -> list[str]:
        """
        Dry-run preview of the commands the plan would run (joined strings).

        Executes the operation path in dry-run mode against an in-memory
        collector so the UI can show exactly what will happen with no side
        effects. Returns an empty list when there are no operations.
        """
        if not self._partition_operations:
            return []
        selected = str(self._selections.get("disk", ""))
        if not selected:
            return []
        collector = _CommandCollector()
        job = _build_preview_job(selected, self._partition_operations, collector)
        job()
        return collector.lines

    @Slot()
    def refreshDisks(self) -> None:
        """Scan and refresh available disks via the unified disk detector."""
        if self._debug:
            print("[Engine] Scanning disks...")

        try:
            # disk_detector.list_disks() already returns the histobar contract
            # (name, model, size, sizeBytes, type, removable, partitions[...])
            # and has its own lsblk-failure fallback.
            self._disks_model = disk_detector.list_disks()
        except Exception as e:  # noqa: BLE001 - defensive: never crash the UI
            if self._debug:
                print(f"[Engine] Disk scan error: {e}")
            self._disks_model = []

        if self._debug:
            print(f"[Engine] Found {len(self._disks_model)} disks")

        self.disksChanged.emit()
        # A rescan changes the selected disk's geometry, so the manual editor's
        # derived views (simulatedSegments, validity, command preview) must
        # re-evaluate against the fresh geometry. Without this, applying a plan
        # updates "Available Disks" but leaves the partition editor stale.
        self._revalidate_operations()
        self.partitionOperationsChanged.emit()

    # =========================================================================
    # Progress Properties
    # =========================================================================

    @Property(int, notify=progressChanged)
    def overallProgress(self) -> int:
        """Get overall installation progress (0-100)."""
        return self._overall_progress

    @Property(str, notify=progressChanged)
    def currentJobName(self) -> str:
        """Get current job's friendly display name (e.g. "install" -> "Installing")."""
        return _job_display_name(self._current_job_name)

    @Property(str, notify=progressChanged)
    def currentJobMessage(self) -> str:
        """Get current job status message."""
        return self._current_job_message

    @Property(list, notify=progressChanged)
    def jobsList(self) -> list[dict[str, Any]]:
        """Get jobs list with status for progress view."""
        return self._jobs_list

    @Property(bool, notify=progressChanged)
    def isStalled(self) -> bool:
        """True quand aucun progress n'est arrivé depuis ``_STALL_THRESHOLD_S`` s.

        L'UI bascule alors la barre en animation « indéterminée » : le système
        construit localement un paquet (silencieux), l'interface n'est pas figée.
        """
        return self._is_stalled

    @Property(bool, notify=progressChanged)
    def indeterminate(self) -> bool:
        """True quand le job progresse réellement mais sans total fiable.

        Piloté explicitement par le job (``report_indeterminate``), et non par
        le timer de silence. L'UI pulse la barre et affiche les compteurs texte
        (« X construits, Y copiés ») au lieu d'un pourcentage inventé.
        """
        return self._indeterminate

    @Property(str, notify=progressChanged)
    def installationStatus(self) -> str:
        """Get installation status: idle, running, success, failed."""
        return self._installation_status

    @Property(str, notify=progressChanged)
    def errorMessage(self) -> str:
        """Get error message if installation failed."""
        return self._error_message

    def _update_jobs_list(self) -> None:
        """Update jobs list with current status."""
        self._jobs_list = []
        job_names = self._engine.get_job_names()
        job_labels = self._engine.get_job_descriptions()
        current_idx = self.getCurrentJobIndex()

        for i, name in enumerate(job_names):
            if i < current_idx:
                status = "completed"
            elif i == current_idx:
                status = "running"
            else:
                status = "pending"

            label = job_labels[i] if i < len(job_labels) else name
            self._jobs_list.append({"name": name, "status": status, "label": label})

    # =========================================================================
    # Summary Properties
    # =========================================================================

    @Property(object, notify=selectionsChanged)  # type: ignore[arg-type]
    def selections(self) -> dict[str, Any]:
        """Get all selections for summary view."""
        return self._selections.copy()

    @Property("QVariantMap", notify=progressChanged)  # type: ignore[arg-type]
    def installationSummary(self) -> dict[str, Any]:
        """Get installation summary for finished view.

        Declared as ``QVariantMap`` (not ``object``): a ``Property(object)``
        returning a plain dict marshals to QML as an opaque QVariant whose keys
        can't be read via dot/bracket notation (they resolve to ``undefined``),
        which is why the summary previously rendered only fallbacks. QVariantMap
        exposes the dict to QML as an introspectable JS object.
        """
        branding = self._engine.get_branding()
        disk = str(self._selections.get("disk", "") or "")
        distribution = branding.name
        size = self._get_disk_size(disk)
        target = f"{disk} ({size})" if disk and size else disk
        filesystem = str(self._selections.get("filesystem", "") or "")
        if target and filesystem:
            target = f"{target} > {filesystem.upper()}"
        return {
            "distribution": distribution,
            "distroName": branding.name,
            "distroVersion": branding.version,
            "targetDisk": target,
            "diskSize": size,
            "installationTime": self._format_install_duration(),
            "installedPackages": 0,  # Could track from packages job
        }

    def _format_install_duration(self) -> str:
        """Format the elapsed installation time as ``Xm Ys`` (or ``Ys``)."""
        if self._install_start_time is None:
            return ""
        end = self._install_end_time if self._install_end_time is not None else time.monotonic()
        seconds = max(0, int(end - self._install_start_time))
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"

    def _get_disk_size(self, disk_name: str) -> str:
        """Get disk size by name."""
        for disk in self._disks_model:
            if disk.get("name") == disk_name:
                return str(disk.get("size", ""))
        return ""

    # =========================================================================
    # Actions
    # =========================================================================

    @Slot(str)
    def executeFinishAction(self, action: str) -> None:
        """Execute finish action: reboot, shutdown, or continue."""
        if self._debug:
            print(f"[Engine] Executing finish action: {action}")

        if self._dry_run:
            print(f"[Engine] Dry run: would execute {action}")
            return

        try:
            if action == "reboot":
                subprocess.run(["systemctl", "reboot"], check=False)
            elif action == "shutdown":
                subprocess.run(["systemctl", "poweroff"], check=False)
            # "continue" does nothing - user can manually close
        except Exception as e:
            if self._debug:
                print(f"[Engine] Action failed: {e}")

    @Slot()
    def applySelectionsToContext(self) -> None:
        """Apply user selections to the engine context before installation."""
        # SECURITY: never log password material (user, root or LUKS). Register
        # any secret value with the redactor as a defense-in-depth measure,
        # in case a secret reached self._selections outside the dedicated
        # setPassword/setRootPassword/setEncryptionPassphrase slots.
        secret_keys = {"password", "rootPassword", "encryptionPassphrase"}
        for key in secret_keys:
            value = self._selections.get(key)
            if isinstance(value, str):
                self._redactor.add_secret(value)

        for op in self._partition_operations:
            passphrase = op.get("params", {}).get("encryption_passphrase")
            if isinstance(passphrase, str) and passphrase:
                self._redactor.add_secret(passphrase)
        for passphrase in self._manual_luks_passphrases.values():
            if passphrase:
                self._redactor.add_secret(passphrase)

        if self._debug:
            print("[Engine] Applying selections to context...")
            for key, value in self._selections.items():
                if key not in secret_keys:
                    print(f"[Engine]   {key}: {value}")

        # The engine context will receive these during job execution.
        # Jobs read from context.selections which we'll populate.
        #
        # Les slots QML stockent certaines clés en camelCase (fullName),
        # alors que les jobs Python (UsersJob) lisent en snake_case
        # (fullname). On normalise dans une copie locale
        # pour ne PAS muter self._selections : la Property `selections` doit
        # conserver le camelCase pour le résumé d'installation côté QML.
        normalized = self._selections.copy()
        if "fullName" in normalized:
            normalized["fullname"] = normalized.pop("fullName")
        if "rootPassword" in normalized:
            normalized["root_password"] = normalized.pop("rootPassword")
        if "rootSameAsUser" in normalized:
            normalized["root_same_as_user"] = normalized.pop("rootSameAsUser")
        if "packagechooserSelection" in normalized:
            normalized["packagechooser_selection"] = normalized.pop("packagechooserSelection")
        if "packagechooserPackages" in normalized:
            normalized["packagechooser_packages"] = normalized.pop("packagechooserPackages")
        # Disk/partition camelCase -> snake_case (partition.py reads snake_case).
        if "partitionMode" in normalized:
            normalized["partition_mode"] = normalized.pop("partitionMode")
        if "swapStrategy" in normalized:
            normalized["swap_strategy"] = normalized.pop("swapStrategy")
        if "encryptionPassphrase" in normalized:
            normalized["encryption_passphrase"] = normalized.pop("encryptionPassphrase")
        if "efiSizeMb" in normalized:
            normalized["efi_size_mb"] = normalized.pop("efiSizeMb")
        if "keyboardVariant" in normalized:
            normalized["keyboard_variant"] = normalized.pop("keyboardVariant")
        # filesystem and encryption keys are already snake-compatible.

        # The QML wizard stores the short disk name (e.g. "sdb") for UI matching,
        # but the destructive jobs (e.g. partition) expect a device path
        # ("/dev/sdb"). Normalize here, idempotently.
        disk = str(normalized.get("disk", ""))
        if disk and not disk.startswith("/dev/"):
            normalized["disk"] = f"/dev/{disk}"

        # Manual partitioning: forward the per-partition assignment plan, keeping
        # only entries the user actually touched (a mount point and/or a format).
        if normalized.get("partition_mode") == "manual":
            # A stale AUTO-mode "encryption" flag (toggled on before switching
            # to manual mode) must not leak through: luksbootkeyfile.py would
            # try to run against whatever root_partition happens to be (and
            # fail, aborting the install at the very end), and finished.py's
            # cleanup would add "cryptroot" to its close list even when
            # that's not this install's actual manual-mode mapper situation.
            # Manual mode threads its own encryption intent entirely through
            # partition_assignments/manual_luks_passphrases instead.
            normalized["encryption"] = False
            normalized["partition_assignments"] = [
                {
                    "name": name,
                    "path": entry["path"],
                    "mountpoint": entry["mountpoint"],
                    "format": entry["format"],
                    "fstype": entry["fstype"],
                    "subvolumes": entry.get("subvolumes") or [],
                    "raw_path": entry.get("raw_path", ""),
                }
                for name, entry in self._partition_assignments.items()
                if entry["mountpoint"]
                or entry["format"]
                or entry.get("subvolumes")
                or entry.get("raw_path")
            ]
            # M2 editor: forward the ordered operation list (snake contract).
            # When present, the partition job drives the operation path instead
            # of the legacy assignment path.
            if self._partition_operations:
                normalized["partition_operations"] = list(self._partition_operations)
            if self._manual_luks_passphrases:
                normalized["manual_luks_passphrases"] = dict(self._manual_luks_passphrases)

        # SECURITY: installer-wide guard-rails read by the destructive jobs
        # (e.g. partition). ``dry_run`` comes from the bridge launch flag;
        # ``confirmed`` is armed by the summary confirmation gate (setConfirmed).
        # Without confirmed=True a real (non-dry-run) install is refused.
        normalized["dry_run"] = self._dry_run
        normalized["confirmed"] = bool(self._selections.get("confirmed", False))

        self._engine.set_selections(normalized)

    # =========================================================================
    # Network Settings
    # =========================================================================

    @Slot()
    def launchNetworkSettings(self) -> None:
        """Launch the native network configuration tool."""
        if self._debug:
            print("[Engine] Launching network settings...")

        success, message = NetworkHelper.launch_network_settings()

        if success:
            if self._debug:
                print(f"[Engine] Network settings launched: {message}")
            self.networkSettingsLaunched.emit(message)
        else:
            if self._debug:
                print(f"[Engine] Network settings error: {message}")
            self.networkSettingsError.emit(message)

    @Slot(result=bool)
    def checkInternetConnectivity(self) -> bool:
        """Check if internet connectivity is available."""
        connected = NetworkHelper.check_internet_connectivity()
        if self._debug:
            print(f"[Engine] Internet connectivity: {connected}")
        self.internetStatusChanged.emit(connected)
        return connected

    @Slot()
    def recheckInternetStatus(self) -> None:
        """Re-check internet status and update requirements if changed."""
        if self._debug:
            print("[Engine] Re-checking internet status...")

        # Check connectivity (this emits internetStatusChanged signal)
        self.checkInternetConnectivity()

        # Re-run requirements check to update the internet requirement status
        if self._requirements_checker is not None:
            self.checkRequirements()

    # =========================================================================
    # Locale to Keymap Derivation
    # =========================================================================

    # Mapping from locale prefix to keyboard layout
    LOCALE_TO_KEYMAP: dict[str, str] = {
        # English variants
        "en_US": "us",
        "en_GB": "gb",
        "en_CA": "ca",
        "en_AU": "us",  # Australia uses US layout
        "en_NZ": "us",  # New Zealand uses US layout
        "en_IE": "ie",
        "en_ZA": "us",
        "en_IN": "in",
        # French variants
        "fr_FR": "fr",
        "fr_CA": "ca",
        "fr_BE": "be",
        "fr_CH": "ch",
        "fr_LU": "fr",
        # German variants
        "de_DE": "de",
        "de_AT": "de",
        "de_CH": "ch",
        "de_LU": "de",
        "de_LI": "ch",
        # Spanish variants
        "es_ES": "es",
        "es_MX": "latam",
        "es_AR": "latam",
        "es_CO": "latam",
        "es_CL": "latam",
        "es_PE": "latam",
        "es_VE": "latam",
        # Italian
        "it_IT": "it",
        "it_CH": "ch",
        # Portuguese variants
        "pt_BR": "br",
        "pt_PT": "pt",
        # Russian and Cyrillic
        "ru_RU": "ru",
        "uk_UA": "ua",
        "be_BY": "by",
        "bg_BG": "bg",
        "sr_RS": "rs",
        "mk_MK": "mk",
        # Chinese
        "zh_CN": "cn",
        "zh_TW": "tw",
        "zh_HK": "cn",
        "zh_SG": "cn",
        # Japanese
        "ja_JP": "jp",
        # Korean
        "ko_KR": "kr",
        # Arabic
        "ar_SA": "ara",
        "ar_EG": "ara",
        "ar_MA": "ara",
        "ar_DZ": "ara",
        "ar_TN": "ara",
        "ar_AE": "ara",
        # Hebrew
        "he_IL": "il",
        # Persian
        "fa_IR": "ir",
        # Hindi and Indian
        "hi_IN": "in",
        "bn_IN": "in",
        "bn_BD": "bd",
        "ta_IN": "in",
        "te_IN": "in",
        # Thai
        "th_TH": "th",
        # Vietnamese
        "vi_VN": "vn",
        # Indonesian and Malay
        "id_ID": "id",
        "ms_MY": "my",
        # Turkish
        "tr_TR": "tr",
        # Greek
        "el_GR": "gr",
        "el_CY": "gr",
        # Polish
        "pl_PL": "pl",
        # Dutch
        "nl_NL": "nl",
        "nl_BE": "be",
        # Nordic languages
        "sv_SE": "se",
        "sv_FI": "fi",
        "da_DK": "dk",
        "nb_NO": "no",
        "nn_NO": "no",
        "fi_FI": "fi",
        "is_IS": "is",
        # Czech and Slovak
        "cs_CZ": "cz",
        "sk_SK": "sk",
        # Hungarian
        "hu_HU": "hu",
        # Romanian
        "ro_RO": "ro",
        "ro_MD": "ro",
        # Baltic
        "lt_LT": "lt",
        "lv_LV": "lv",
        "et_EE": "ee",
        # Slavic
        "sl_SI": "si",
        "hr_HR": "hr",
        "bs_BA": "ba",
        # Catalan, Basque, Galician
        "ca_ES": "es",
        "eu_ES": "es",
        "gl_ES": "es",
        # Welsh, Irish
        "cy_GB": "gb",
        "ga_IE": "ie",
        # Albanian
        "sq_AL": "al",
        # Georgian
        "ka_GE": "ge",
        # Armenian
        "hy_AM": "am",
        # Kazakh
        "kk_KZ": "kz",
        # Uzbek
        "uz_UZ": "uz",
        # Afrikaans
        "af_ZA": "za",
    }

    def _derive_keymap_from_locale(self, locale: str) -> str:
        """
        Derive keyboard layout from locale.

        Args:
            locale: Locale string (e.g., "fr_FR.UTF-8", "de_DE.UTF-8")

        Returns:
            Keyboard layout code (e.g., "fr", "de", "us")
        """
        # Remove .UTF-8 suffix if present
        locale_base = locale.replace(".UTF-8", "").replace(".utf8", "")

        # Try exact match first
        if locale_base in self.LOCALE_TO_KEYMAP:
            return self.LOCALE_TO_KEYMAP[locale_base]

        # Try matching just the language part (e.g., "fr" from "fr_FR")
        lang_code = locale_base.split("_")[0]
        for locale_prefix, keymap in self.LOCALE_TO_KEYMAP.items():
            if locale_prefix.startswith(lang_code + "_"):
                return keymap

        # Default to US layout if no match
        return "us"

    @Slot(str, result=str)
    def deriveKeymapFromLocale(self, locale: str) -> str:
        """QML-accessible slot to derive keymap from locale."""
        return self._derive_keymap_from_locale(locale)

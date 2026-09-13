"""
Welcome Job for Xen Installer.

Displays welcome screen with system requirements check and branding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xen.jobs.base import BaseJob, JobContext, JobResult
from xen.jobs.requirements import (
    RequirementCheck,
    RequirementsResult,
    RequirementStatus,
    SystemRequirementsChecker,
)

logger = logging.getLogger(__name__)


@dataclass
class WelcomeConfig:
    """Configuration for the welcome job."""

    # Display options
    show_release_notes: bool = True

    # Wallpaper paths (relative to theme directory)
    wallpaper_dark: str = ""
    wallpaper_light: str = ""
    wallpaper_fallback: str = ""

    # Requirements configuration
    requirements: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> WelcomeConfig:
        """Create config from dictionary."""
        wallpapers = config.get("wallpapers", {})
        return cls(
            show_release_notes=config.get("show_release_notes", True),
            wallpaper_dark=wallpapers.get("dark", ""),
            wallpaper_light=wallpapers.get("light", ""),
            wallpaper_fallback=wallpapers.get("fallback", ""),
            requirements=config.get("requirements", {}),
        )


@dataclass
class WelcomeState:
    """State of the welcome job for UI binding."""

    # Wallpaper URLs
    wallpaper_dark_url: str = ""
    wallpaper_light_url: str = ""
    current_wallpaper_url: str = ""

    # Requirements state
    requirements_result: RequirementsResult | None = None
    all_requirements_met: bool = False
    can_proceed: bool = False

    # UI state
    is_dark_mode: bool = True
    show_details: bool = False


class WelcomeJob(BaseJob):
    """
    Welcome screen job with system requirements validation.

    Responsibilities:
    - Display branded welcome screen with configurable wallpaper
    - Perform comprehensive system requirements checks
    - Present check results in a modern, user-friendly overlay
    - Block installation if critical requirements are not met
    """

    name = "welcome"
    description = "Welcome screen and system requirements check"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the welcome job."""
        super().__init__(config)
        self._welcome_config = WelcomeConfig.from_dict(self._config)
        self._state = WelcomeState()
        self._checker: SystemRequirementsChecker | None = None

    @property
    def welcome_config(self) -> WelcomeConfig:
        """Get the welcome configuration."""
        return self._welcome_config

    @property
    def state(self) -> WelcomeState:
        """Get current state for UI binding."""
        return self._state

    def initialize(self, theme_base: Path | None = None) -> None:
        """
        Initialize the job with theme resources.

        Args:
            theme_base: Base path to theme directory
        """
        # Initialize requirements checker
        self._checker = SystemRequirementsChecker(self._welcome_config.requirements)

        # Resolve wallpaper paths to URLs
        if theme_base:
            self._resolve_wallpapers(theme_base)

    def _resolve_wallpapers(self, theme_base: Path) -> None:
        """Resolve wallpaper paths to file:// URLs."""
        # Dark wallpaper
        if self._welcome_config.wallpaper_dark:
            dark_path = theme_base / self._welcome_config.wallpaper_dark
            if dark_path.exists():
                self._state.wallpaper_dark_url = f"file://{dark_path}"

        # Light wallpaper
        if self._welcome_config.wallpaper_light:
            light_path = theme_base / self._welcome_config.wallpaper_light
            if light_path.exists():
                self._state.wallpaper_light_url = f"file://{light_path}"

        # Fallback
        if self._welcome_config.wallpaper_fallback:
            fallback_path = theme_base / self._welcome_config.wallpaper_fallback
            if fallback_path.exists():
                fallback_url = f"file://{fallback_path}"
                # Use as default if specific wallpapers not found
                if not self._state.wallpaper_dark_url:
                    self._state.wallpaper_dark_url = fallback_url
                if not self._state.wallpaper_light_url:
                    self._state.wallpaper_light_url = fallback_url

        # Set current wallpaper based on mode
        self._update_current_wallpaper()

    def _update_current_wallpaper(self) -> None:
        """Update current wallpaper based on dark/light mode."""
        if self._state.is_dark_mode:
            self._state.current_wallpaper_url = self._state.wallpaper_dark_url
        else:
            self._state.current_wallpaper_url = self._state.wallpaper_light_url

    def set_dark_mode(self, is_dark: bool) -> None:
        """
        Set dark/light mode for wallpaper.

        Args:
            is_dark: True for dark mode, False for light mode
        """
        self._state.is_dark_mode = is_dark
        self._update_current_wallpaper()

    def check_requirements(self) -> RequirementsResult:
        """
        Perform all system requirements checks.

        Returns:
            RequirementsResult with all check outcomes
        """
        if not self._checker:
            self._checker = SystemRequirementsChecker(self._welcome_config.requirements)

        result = self._checker.check_all()

        # Update state
        self._state.requirements_result = result
        self._state.all_requirements_met = result.all_passed
        self._state.can_proceed = result.can_continue

        logger.info(
            f"Requirements check: {len(result.passed_checks)} passed, "
            f"{len(result.warnings)} warnings, {len(result.failures)} failures"
        )

        return result

    def get_requirements_summary(self) -> dict[str, Any]:
        """
        Get a summary of requirements for UI display.

        Returns:
            Dictionary with categorized requirement checks
        """
        if not self._state.requirements_result:
            self.check_requirements()

        result = self._state.requirements_result
        if not result:
            return {"passed": [], "warnings": [], "failures": [], "can_proceed": False}

        def check_to_dict(check: RequirementCheck) -> dict[str, Any]:
            return {
                "name": check.name,
                "description": check.description,
                "status": check.status.name.lower(),
                "current": check.current_value,
                "required": check.required_value,
                "recommended": check.recommended_value,
                "details": check.details,
                "is_critical": check.is_critical,
            }

        return {
            "passed": [
                check_to_dict(c) for c in result.checks if c.status == RequirementStatus.PASS
            ],
            "warnings": [
                check_to_dict(c) for c in result.checks if c.status == RequirementStatus.WARN
            ],
            "failures": [
                check_to_dict(c) for c in result.checks if c.status == RequirementStatus.FAIL
            ],
            "skipped": [
                check_to_dict(c) for c in result.checks if c.status == RequirementStatus.SKIP
            ],
            "can_proceed": result.can_continue,
            "all_passed": result.all_passed,
            "total_checks": len(result.checks),
        }

    def validate(self, context: JobContext) -> JobResult:
        """
        Validate that the welcome job can proceed.

        This runs the requirements check and determines if installation
        can continue.

        Args:
            context: Execution context

        Returns:
            JobResult indicating if requirements are met
        """
        context.report_progress(0, "Checking system requirements...")

        result = self.check_requirements()

        if not result.can_continue:
            failure_names = [f.description for f in result.failures]
            return JobResult.fail(
                message=f"System requirements not met: {', '.join(failure_names)}",
                error_code=10,
                data={"failures": [f.name for f in result.failures]},
            )

        if result.warnings:
            warning_names = [w.description for w in result.warnings]
            logger.warning(f"Requirements warnings: {', '.join(warning_names)}")

        return JobResult.ok(
            message="System requirements check passed",
            data={
                "passed": len(result.passed_checks),
                "warnings": len(result.warnings),
            },
        )

    def run(self, context: JobContext) -> JobResult:
        """
        Execute the welcome job.

        The welcome job is primarily UI-driven. This method validates
        requirements and prepares the state for UI display.

        Args:
            context: Execution context

        Returns:
            JobResult indicating success or failure
        """
        context.report_progress(10, "Initializing welcome screen...")

        # Validate requirements first
        validation = self.validate(context)
        if not validation.success:
            return validation

        context.report_progress(50, "Loading branding assets...")

        # The actual UI display is handled by QML
        # This job just prepares the data

        context.report_progress(100, "Welcome screen ready")

        return JobResult.ok(
            message="Welcome job completed",
            data={
                "requirements_summary": self.get_requirements_summary(),
                "wallpaper_url": self._state.current_wallpaper_url,
                "can_proceed": self._state.can_proceed,
            },
        )

    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Returns:
            Estimated duration (requirements checks are quick)
        """
        return 5

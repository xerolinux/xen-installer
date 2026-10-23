"""
Welcome Job for Xen Installer.

Displays welcome screen with system requirements check and branding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from xen.i18n.messages import msg
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

    requirements: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> WelcomeConfig:
        """Create config from dictionary."""
        return cls(requirements=config.get("requirements", {}))


@dataclass
class WelcomeState:
    """State of the welcome job for UI binding."""

    requirements_result: RequirementsResult | None = None
    all_requirements_met: bool = False
    can_proceed: bool = False


class WelcomeJob(BaseJob):
    """
    Welcome screen job with system requirements validation.

    Responsibilities:
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
        context.report_progress(0, msg("Checking system requirements..."))

        result = self.check_requirements()

        if not result.can_continue:
            failure_names = [f.description for f in result.failures]
            return JobResult.fail(
                message=msg(
                    "System requirements not met: {failures}",
                    failures=", ".join(failure_names),
                ),
                error_code=10,
                data={"failures": [f.name for f in result.failures]},
            )

        if result.warnings:
            warning_names = [w.description for w in result.warnings]
            logger.warning(f"Requirements warnings: {', '.join(warning_names)}")

        return JobResult.ok(
            message=msg("System requirements check passed"),
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
        context.report_progress(10, msg("Initializing welcome screen..."))

        # Validate requirements first
        validation = self.validate(context)
        if not validation.success:
            return validation

        context.report_progress(50, msg("Loading branding assets..."))

        # The actual UI display is handled by QML
        # This job just prepares the data

        context.report_progress(100, msg("Welcome screen ready"))

        return JobResult.ok(
            message=msg("Welcome job completed"),
            data={
                "requirements_summary": self.get_requirements_summary(),
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

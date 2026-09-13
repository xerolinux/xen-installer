"""Xen installation jobs."""

from xen.jobs.base import BaseJob, JobContext, JobResult, JobStatus
from xen.jobs.requirements import (
    RequirementCheck,
    RequirementsResult,
    RequirementStatus,
    SystemRequirementsChecker,
)
from xen.jobs.welcome import WelcomeConfig, WelcomeJob, WelcomeState

__all__ = [
    # Base classes
    "BaseJob",
    "JobContext",
    "JobResult",
    "JobStatus",
    # Requirements
    "RequirementCheck",
    "RequirementsResult",
    "RequirementStatus",
    "SystemRequirementsChecker",
    # Jobs
    "WelcomeJob",
    "WelcomeConfig",
    "WelcomeState",
]

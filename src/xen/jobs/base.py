"""
Base Job interface for Xen Installer.

All installation jobs must inherit from BaseJob and implement the required methods.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class JobStatus(Enum):
    """Status of a job in the installation pipeline."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass(frozen=True)
class JobResult:
    """Result of a job execution."""

    success: bool
    message: str = ""
    error_code: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, message: str = "", data: dict[str, Any] | None = None) -> "JobResult":
        """Create a successful result."""
        return cls(success=True, message=message, data=data or {})

    @classmethod
    def fail(
        cls, message: str, error_code: int = 1, data: dict[str, Any] | None = None
    ) -> "JobResult":
        """Create a failed result."""
        return cls(success=False, message=message, error_code=error_code, data=data or {})


@dataclass
class JobContext:
    """
    Context passed to jobs during execution.

    Contains installation parameters and callbacks for progress reporting.
    """

    # Installation target
    target_root: str = "/mnt/target"

    # User selections from previous jobs
    selections: dict[str, Any] = field(default_factory=dict)

    # Job-specific configuration from xen.yaml
    config: dict[str, Any] = field(default_factory=dict)

    # Callback for progress updates (percent: 0-100, message: str)
    on_progress: Callable[[int, str], None] | None = None

    # Callback for the indeterminate flag (progress without a reliable total)
    on_indeterminate: Callable[[bool], None] | None = None

    def report_progress(self, percent: int, message: str = "") -> None:
        """Report progress to the UI."""
        if self.on_progress:
            self.on_progress(min(100, max(0, percent)), message)

    def report_indeterminate(self, active: bool) -> None:
        """Signale à l'UI que la progression est vivante mais sans total fiable."""
        if self.on_indeterminate:
            self.on_indeterminate(active)


class BaseJob(ABC):
    """
    Abstract base class for all installation jobs.

    Each job represents a discrete step in the installation process.
    Jobs are executed sequentially by the Engine.

    Example:
        class WelcomeJob(BaseJob):
            name = "welcome"
            description = "Welcome screen and requirements check"

            def run(self, context: JobContext) -> JobResult:
                # Check requirements
                if not self._check_disk_space(context):
                    return JobResult.fail("Insufficient disk space")
                return JobResult.ok()

            def estimate_duration(self) -> int:
                return 5
    """

    # Job identifier (must match name in xen.yaml)
    name: str = "base"

    # Human-readable description
    description: str = "Base job"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        Initialize the job with optional configuration.

        Args:
            config: Job-specific configuration from xen.yaml
        """
        self._config = config or {}
        self._status = JobStatus.PENDING

    @property
    def status(self) -> JobStatus:
        """Current status of the job."""
        return self._status

    @status.setter
    def status(self, value: JobStatus) -> None:
        """Update job status."""
        self._status = value

    @property
    def config(self) -> dict[str, Any]:
        """This job's own configuration dict, as given at construction."""
        return self._config

    @abstractmethod
    def run(self, context: JobContext) -> JobResult:
        """
        Execute the job.

        Args:
            context: Execution context with parameters and callbacks

        Returns:
            JobResult indicating success or failure
        """
        ...

    @abstractmethod
    def estimate_duration(self) -> int:
        """
        Estimate execution time in seconds.

        Used by the UI to display progress estimation.

        Returns:
            Estimated duration in seconds
        """
        ...

    def validate(self, _context: JobContext) -> JobResult:
        """
        Validate that the job can be executed.

        Called before run() to check preconditions.
        Override in subclasses for custom validation.

        Args:
            _context: Execution context

        Returns:
            JobResult.ok() if valid, JobResult.fail() otherwise
        """
        return JobResult.ok()

    def cleanup(self, _context: JobContext) -> None:
        """
        Clean up after job execution (success or failure).

        Override in subclasses for custom cleanup logic.

        Args:
            _context: Execution context
        """
        return None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, status={self.status.name})>"

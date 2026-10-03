"""
Xen Engine - Core installation orchestrator.

Handles configuration loading, job management, and execution pipeline.
Runs in root context, separated from UI process.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from xen.jobs.base import BaseJob, JobContext, JobResult, JobStatus
from xen.jobs.bootloader import BootloaderJob

logger = logging.getLogger(__name__)


class BrandingColors(BaseModel):
    """Color scheme for UI branding."""

    primary: str = "#7C3AED"
    secondary: str = "#A78BFA"
    accent: str = "#F59E0B"
    background: str = "#1F2937"
    background_light: str = "#374151"
    surface: str = "#374151"
    text: str = "#F9FAFB"
    text_muted: str = "#9CA3AF"
    text_on_primary: str = "#FFFFFF"
    success: str = "#10B981"
    warning: str = "#F59E0B"
    error: str = "#EF4444"

    @field_validator(
        "primary",
        "secondary",
        "accent",
        "background",
        "background_light",
        "surface",
        "text",
        "text_muted",
        "text_on_primary",
        "success",
        "warning",
        "error",
    )
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        """Validate that color is a valid 6-digit hex color."""
        if not re.match(r"^#[0-9A-Fa-f]{6}$", v):
            raise ValueError(f"Invalid hex color format: {v}. Expected #RRGGBB format.")
        return v


class BrandingAssets(BaseModel):
    """Asset paths for UI branding."""

    logo: str = ""
    logo_light: str = ""
    logo_small: str = ""
    logo_256: str = ""
    background: str = ""
    background_alt: str = ""
    icon: str = ""
    bootloader: str = ""
    efi_icon: str = ""
    # Users view icons (configurable via theme)
    icon_user: str = ""
    icon_fullname: str = ""
    icon_hostname: str = ""
    icon_password: str = ""
    icon_settings: str = ""
    icon_check: str = ""
    icon_cross: str = ""


class BrandingFonts(BaseModel):
    """Typography configuration."""

    primary: str = "sans-serif"
    display: str = "sans-serif"
    monospace: str = "monospace"


class BrandingStrings(BaseModel):
    """Customizable UI strings."""

    welcome_title: str = "Welcome"
    welcome_subtitle: str = ""
    install_button: str = "Install"
    finished_title: str = "Installation Complete"
    finished_message: str = ""


class BrandingLinks(BaseModel):
    """External links configuration."""

    website: str = ""  # Main distribution website
    website_label: str = ""  # Display text for website link
    git: str = ""  # Git repository URL
    documentation: str = ""  # Documentation URL
    support: str = ""  # Support/forum URL


class BrandingConfig(BaseModel):
    """Complete branding configuration."""

    name: str = "Linux"
    version: str = ""
    edition: str = ""
    colors: BrandingColors = Field(default_factory=BrandingColors)
    assets: BrandingAssets = Field(default_factory=BrandingAssets)
    strings: BrandingStrings = Field(default_factory=BrandingStrings)
    fonts: BrandingFonts = Field(default_factory=BrandingFonts)
    links: BrandingLinks = Field(default_factory=BrandingLinks)
    # Category -> theme-relative SVG path for requirement icons, set from
    # theme.yaml; falls back to the icons/requirements/cat-<name>.svg convention.
    requirement_icons: dict[str, str] = Field(default_factory=dict)


class JobDefinition(BaseModel):
    """Job definition from configuration."""

    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class AdvancedConfig(BaseModel):
    """Advanced engine configuration."""

    log_level: str = "info"
    log_file: str = "/var/log/xen-install.log"
    ipc_socket: str = "/run/xen/ipc.sock"
    job_timeout: int = 3600
    debug_mode: bool = False


class XenConfig(BaseModel):
    """Root configuration model."""

    version: str = "1.0"
    theme: str = ""  # Path to theme directory containing assets
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    jobs: list[JobDefinition | str] = Field(default_factory=list)
    advanced: AdvancedConfig = Field(default_factory=AdvancedConfig)

    def normalize_jobs(self) -> list[JobDefinition]:
        """Convert all job entries to JobDefinition objects."""
        normalized: list[JobDefinition] = []
        for job in self.jobs:
            if isinstance(job, str):
                normalized.append(JobDefinition(name=job))
            else:
                normalized.append(job)
        return normalized


class ConfigurationError(Exception):
    """Raised when configuration is invalid."""

    pass


class JobLoadError(Exception):
    """Raised when a job cannot be loaded."""

    pass


@dataclass
class EngineState:
    """Current state of the engine."""

    current_job_index: int = -1
    total_jobs: int = 0
    is_running: bool = False
    is_finished: bool = False
    last_error: str | None = None


@dataclass
class Engine:
    """
    Core installation engine.

    Responsibilities:
    - Load and validate configuration
    - Instantiate and manage jobs
    - Execute jobs sequentially
    - Report progress to UI via callbacks

    Example:
        engine = Engine.from_config_file("xen.yaml")
        engine.on_progress = lambda job, pct, msg: print(f"{job}: {pct}%")
        result = engine.run_all()
    """

    config: XenConfig
    jobs: list[BaseJob] = field(default_factory=list)
    state: EngineState = field(default_factory=EngineState)

    # User selections from UI
    _selections: dict[str, Any] = field(default_factory=dict)

    # Callbacks
    on_job_start: Any | None = None  # (job_name: str) -> None
    on_job_progress: Any | None = None  # (job_name: str, percent: int, msg: str) -> None
    on_job_complete: Any | None = None  # (job_name: str, result: JobResult) -> None
    on_error: Any | None = None  # (job_name: str, error: str) -> None
    on_job_indeterminate: Any | None = None  # (job_name: str, active: bool) -> None

    def set_selections(self, selections: dict[str, Any]) -> None:
        """
        Set user selections from UI to be passed to jobs.

        Args:
            selections: Dictionary with user selections (locale, timezone, user, disk, etc.)
        """
        self._selections = selections.copy()

    @staticmethod
    def _apply_theme_overlay(config_dir: Path, raw_config: dict[str, Any]) -> None:
        """
        Overlay ``<theme_dir>/theme.yaml`` onto the inline ``branding`` config.

        The theme file is the source of truth for the visual identity: its
        ``colors`` / ``fonts`` / ``strings`` sections and ``metadata`` override
        the matching values from the inline ``branding:`` block, which remains
        the fallback for anything the theme does not define. A missing, empty or
        invalid theme file is ignored (inline branding is used as-is).

        Note: keys unknown to the branding models (e.g. ``colors.success``) are
        accepted here but dropped at validation time, since those models only
        expose a subset of fields.
        """
        theme_rel = raw_config.get("theme")
        if not theme_rel:
            return

        theme_file = (config_dir / str(theme_rel) / "theme.yaml").resolve()
        if not theme_file.exists():
            logger.debug("No theme.yaml at %s; using inline branding only", theme_file)
            return

        try:
            with theme_file.open("r", encoding="utf-8") as f:
                theme = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.warning("Ignoring invalid theme.yaml (%s): %s", theme_file, e)
            return

        if not isinstance(theme, dict):
            return

        branding = raw_config.get("branding")
        if not isinstance(branding, dict):
            branding = {}
            raw_config["branding"] = branding

        # Sub-sections whose keys map 1:1 onto the branding sub-models.
        for section in ("colors", "fonts", "strings", "requirement_icons"):
            values = theme.get(section)
            if isinstance(values, dict):
                target = branding.get(section)
                if not isinstance(target, dict):
                    target = {}
                    branding[section] = target
                target.update(values)  # theme overrides, inline branding fills gaps

        # Theme metadata maps onto the top-level branding identity fields.
        meta = theme.get("metadata")
        if isinstance(meta, dict):
            for meta_key, brand_key in (
                ("name", "name"),
                ("version", "version"),
                ("codename", "edition"),
            ):
                if meta.get(meta_key):
                    branding[brand_key] = meta[meta_key]
            if meta.get("website"):
                links = branding.get("links")
                if not isinstance(links, dict):
                    links = {}
                    branding["links"] = links
                links["website"] = meta["website"]

        logger.info("Applied theme overlay from %s", theme_file)

    @staticmethod
    def _resolve_packagechooser_groups_file(config_dir: Path, raw_config: dict[str, Any]) -> None:
        """
        Resolve packagechooser's ``groups_file`` to an absolute path.

        Mirrors ``_apply_theme_overlay``: the path in ``xen.yaml`` is
        relative to the config file's own directory, not the process cwd.
        """
        jobs = raw_config.get("jobs")
        if not isinstance(jobs, list):
            return

        for job_entry in jobs:
            if not isinstance(job_entry, dict) or job_entry.get("name") != "packagechooser":
                continue

            job_config = job_entry.get("config")
            if not isinstance(job_config, dict):
                continue

            groups_file = job_config.get("groups_file")
            if not groups_file or Path(groups_file).is_absolute():
                continue

            job_config["groups_file"] = str((config_dir / str(groups_file)).resolve())

    @classmethod
    def from_config_file(cls, path: str | Path) -> "Engine":
        """
        Create an Engine instance from a YAML configuration file.

        Args:
            path: Path to xen.yaml configuration file

        Returns:
            Configured Engine instance

        Raises:
            ConfigurationError: If config is invalid or missing
        """
        config_path = Path(path)

        if not config_path.exists():
            raise ConfigurationError(f"Configuration file not found: {config_path}")

        try:
            with config_path.open("r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML syntax: {e}") from e

        if raw_config is None:
            raise ConfigurationError("Configuration file is empty")

        cls._apply_theme_overlay(config_path.parent, raw_config)
        cls._resolve_packagechooser_groups_file(config_path.parent, raw_config)

        try:
            config = XenConfig.model_validate(raw_config)
        except ValidationError as e:
            raise ConfigurationError(f"Configuration validation failed: {e}") from e

        engine = cls(config=config)
        engine._load_jobs()

        return engine

    def _load_jobs(self) -> None:
        """
        Load and instantiate all jobs defined in configuration.

        Jobs are loaded from xen.jobs.<name> module.
        """
        self.jobs = []
        job_definitions = self.config.normalize_jobs()

        for job_def in job_definitions:
            job = self._load_single_job(job_def)
            self.jobs.append(job)

        self.state.total_jobs = len(self.jobs)

    def _load_single_job(self, job_def: JobDefinition) -> BaseJob:
        """
        Load a single job by name.

        Dynamically imports the job module from xen.jobs.<name> and
        instantiates the job class. The job class must have a name ending
        with 'Job' (e.g., WelcomeJob for the 'welcome' job).

        Args:
            job_def: Job definition with name and config

        Returns:
            Instantiated job

        Raises:
            JobLoadError: If job cannot be loaded or is invalid
        """
        import importlib
        import inspect

        job_name = job_def.name
        module_name = f"xen.jobs.{job_name}"

        # Import the job module
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise JobLoadError(f"Failed to import job module '{module_name}': {e}") from e

        # Find the job class (must end with 'Job' and inherit from BaseJob)
        job_class = None
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if name.endswith("Job") and issubclass(obj, BaseJob) and obj is not BaseJob:
                job_class = obj
                break

        if job_class is None:
            raise JobLoadError(
                f"No valid job class found in module '{module_name}'. "
                "Job class must inherit from BaseJob and have a name ending with 'Job'."
            )

        # Instantiate the job
        try:
            return job_class(config=job_def.config)
        except Exception as e:
            raise JobLoadError(
                f"Failed to instantiate job class '{job_class.__name__}': {e}"
            ) from e

    def get_branding(self) -> BrandingConfig:
        """Get branding configuration for UI."""
        return self.config.branding

    def get_theme_path(self) -> str:
        """Get theme directory path from configuration."""
        return self.config.theme

    def get_job_names(self) -> list[str]:
        """Get ordered list of job names."""
        return [job.name for job in self.jobs]

    def get_job_descriptions(self) -> list[str]:
        """Get ordered list of human-readable job descriptions (UI display labels)."""
        return [job.description for job in self.jobs]

    def bootloader_type(self) -> str:
        """The bootloader this run installs: the bootloader job's own yaml
        `type:` wins, then selections["bootloader"], then systemd-boot."""
        for job in self.jobs:
            if job.name == BootloaderJob.name:
                configured = job.config.get("type")
                if configured:
                    return str(configured)
        return str(self._selections.get("bootloader", BootloaderJob.SYSTEMD_BOOT))

    def run_all(self, context: JobContext | None = None) -> bool:
        """
        Execute all jobs sequentially.

        Args:
            context: Execution context (created if not provided)

        Returns:
            True if all jobs succeeded, False otherwise
        """
        if context is None:
            context = JobContext()

        # Populate context with user selections from UI
        context.selections = self._selections.copy()
        # Seeded here because jobs that run before the bootloader (partition's
        # LUKS KDF choice) only see selections, not the bootloader job config.
        context.selections["bootloader"] = self.bootloader_type()

        self.state.is_running = True
        self.state.is_finished = False
        self.state.last_error = None

        for index, job in enumerate(self.jobs):
            self.state.current_job_index = index

            result = self._run_single_job(job, context)

            if not result.success:
                self.state.last_error = result.message
                self.state.is_running = False
                return False

            layout = result.data.get("layout")
            if isinstance(layout, dict):
                for src_key, dst_key in (
                    ("root", "root_partition"),
                    ("efi", "efi_partition"),
                    ("swap", "swap_partition"),
                ):
                    if layout.get(src_key):
                        context.selections[dst_key] = str(layout[src_key])

        self.state.is_running = False
        self.state.is_finished = True
        return True

    def _run_single_job(self, job: BaseJob, context: JobContext) -> JobResult:
        """
        Execute a single job with progress reporting.

        Args:
            job: Job to execute
            context: Execution context

        Returns:
            Job result
        """
        job.status = JobStatus.RUNNING

        if self.on_job_start:
            self.on_job_start(job.name)

        # Set up progress callback
        def progress_callback(percent: int, message: str) -> None:
            if self.on_job_progress:
                self.on_job_progress(job.name, percent, message)

        def indeterminate_callback(active: bool) -> None:
            if self.on_job_indeterminate:
                self.on_job_indeterminate(job.name, active)

        context.on_progress = progress_callback
        context.on_indeterminate = indeterminate_callback
        context.config = job._config

        # Validate. Skipped in dry-run: validate() checks real target-system
        # state (mounted partitions, existing files) that a simulated
        # install never creates, so every job would fail here before its
        # own dry-run short-circuit in run() ever gets a chance to run.
        if not context.selections.get("dry_run"):
            validation = job.validate(context)
            if not validation.success:
                job.status = JobStatus.FAILED
                if self.on_error:
                    self.on_error(job.name, validation.message)
                return validation

        # Execute
        try:
            result = job.run(context)
        except Exception as e:
            result = JobResult.fail(str(e))
            job.status = JobStatus.FAILED
            if self.on_error:
                self.on_error(job.name, str(e))
            return result
        finally:
            job.cleanup(context)

        # Update status
        job.status = JobStatus.COMPLETED if result.success else JobStatus.FAILED

        if not result.success and self.on_error:
            self.on_error(job.name, result.message)

        if self.on_job_complete:
            self.on_job_complete(job.name, result)

        return result

    def get_progress(self) -> tuple[int, int]:
        """
        Get current progress as (current_job, total_jobs).

        Returns:
            Tuple of (current job index, total jobs)
        """
        return (self.state.current_job_index + 1, self.state.total_jobs)

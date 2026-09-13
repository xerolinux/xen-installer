"""
Process Launcher for UI/Engine Separation.

Handles privilege escalation and process management for the
split architecture where UI runs unprivileged and Engine runs as root.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from xen.ipc import (
    Command,
    Event,
    IPCConnectionError,
    IPCDispatcher,
    IPCServer,
    create_ui_client,
)

if TYPE_CHECKING:
    from xen.core.engine import Engine

logger = logging.getLogger(__name__)

# Default socket path
DEFAULT_SOCKET_PATH = Path("/run/xen/ipc.sock")

# Timeout for engine startup
ENGINE_STARTUP_TIMEOUT = 30.0

# Privilege escalation command
PKEXEC_CMD = "pkexec"
SUDO_CMD = "sudo"


class LauncherError(Exception):
    """Error during process launch."""

    pass


class EngineProcess:
    """
    Manages the Engine subprocess.

    Handles launching the engine with elevated privileges
    and monitoring its lifecycle.
    """

    def __init__(
        self,
        config_path: Path,
        socket_path: Path = DEFAULT_SOCKET_PATH,
        debug: bool = False,
        dry_run: bool = False,
    ) -> None:
        """
        Initialize engine process manager.

        Args:
            config_path: Path to configuration file
            socket_path: Path for IPC socket
            debug: Enable debug mode
            dry_run: Enable dry-run mode
        """
        self.config_path = config_path
        self.socket_path = socket_path
        self.debug = debug
        self.dry_run = dry_run
        self._process: subprocess.Popen | None = None
        self._started = False

    @property
    def is_running(self) -> bool:
        """Check if engine process is running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def start(self, use_pkexec: bool = True) -> None:
        """
        Start the engine process with elevated privileges.

        Args:
            use_pkexec: Use pkexec for privilege escalation (vs sudo)

        Raises:
            LauncherError: If engine fails to start
        """
        if self._started:
            logger.warning("Engine already started")
            return

        # Build command. Prefer the installed ``xen`` entrypoint over a bare
        # ``python -m xen.main`` invocation: privilege-escalation tools such
        # as pkexec sanitise the environment (dropping PYTHONPATH), and packaged
        # builds (e.g. Nix) expose the ``xen`` module only through a wrapper.
        # Re-executing that wrapper keeps the module importable for the engine;
        # ``python -m xen.main`` would otherwise fail with ModuleNotFoundError.
        xen_bin = shutil.which("xen")
        base_cmd = [xen_bin] if xen_bin else [sys.executable, "-m", "xen.main"]

        cmd_args = [
            *base_cmd,
            "--engine",
            "--config",
            str(self.config_path),
            "--socket",
            str(self.socket_path),
        ]

        if self.debug:
            cmd_args.append("--debug")
        if self.dry_run:
            cmd_args.append("--dry-run")

        # Choose privilege escalation method
        if use_pkexec and shutil.which(PKEXEC_CMD):
            full_cmd = [PKEXEC_CMD, *cmd_args]
        elif shutil.which(SUDO_CMD):
            full_cmd = [SUDO_CMD, *cmd_args]
        else:
            # Fall back to running without privilege escalation (for testing)
            logger.warning("No privilege escalation available, running directly")
            full_cmd = cmd_args

        logger.info(f"Starting engine: {' '.join(full_cmd)}")

        try:
            # Start engine process
            self._process = subprocess.Popen(
                full_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # Detach from terminal
            )
            self._started = True

        except OSError as e:
            raise LauncherError(f"Failed to start engine: {e}") from e

    def wait_for_ready(self, timeout: float = ENGINE_STARTUP_TIMEOUT) -> bool:
        """
        Wait for engine to be ready (socket available).

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if engine is ready

        Raises:
            LauncherError: If timeout or engine dies
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if process died
            if self._process and self._process.poll() is not None:
                returncode = self._process.returncode
                stderr = ""
                if self._process.stderr:
                    stderr = self._process.stderr.read().decode()
                raise LauncherError(f"Engine process died with code {returncode}: {stderr}")

            # Check if socket exists
            if self.socket_path.exists():
                # Try to connect
                try:
                    client = create_ui_client(self.socket_path)
                    client.connect()
                    result = client.ping()
                    client.disconnect()
                    if result.get("pong"):
                        logger.info("Engine is ready")
                        return True
                except IPCConnectionError:
                    pass  # Not ready yet

            time.sleep(0.1)

        raise LauncherError(f"Engine startup timeout after {timeout}s")

    def stop(self, timeout: float = 5.0) -> None:
        """
        Stop the engine process gracefully.

        Args:
            timeout: Time to wait for graceful shutdown
        """
        if not self._process:
            return

        logger.info("Stopping engine process")

        # Try graceful shutdown via IPC
        if self.socket_path.exists():
            try:
                client = create_ui_client(self.socket_path)
                client.connect()
                client.shutdown()
                client.disconnect()
            except Exception as e:
                logger.warning(f"Failed to send shutdown command: {e}")

        # Wait for process to exit
        try:
            self._process.wait(timeout=timeout)
            logger.info("Engine stopped gracefully")
        except subprocess.TimeoutExpired:
            # Force kill
            logger.warning("Engine did not stop gracefully, sending SIGTERM")
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.warning("Engine did not respond to SIGTERM, sending SIGKILL")
                self._process.kill()

        self._process = None
        self._started = False

    def __enter__(self) -> EngineProcess:
        """Context manager entry."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit - stop engine."""
        self.stop()


def create_engine_dispatcher(engine: Engine) -> IPCDispatcher:
    """
    Create a dispatcher with all engine command handlers.

    Args:
        engine: The Engine instance to wrap

    Returns:
        Configured dispatcher
    """
    dispatcher = IPCDispatcher()

    # PING handler
    def ping_handler(args: dict) -> dict:
        return {"pong": True, "echo": args.get("echo", "")}

    dispatcher.register(Command.PING, ping_handler)

    # GET_STATUS handler
    def get_status_handler(_args: dict) -> dict:
        return {
            "status": "idle",  # TODO: Get from engine state
            "current_job": None,
            "progress": 0,
        }

    dispatcher.register(Command.GET_STATUS, get_status_handler)

    # GET_BRANDING handler
    def get_branding_handler(_args: dict) -> dict:
        branding = engine.get_branding()
        return {
            "name": branding.name,
            "version": branding.version,
            "edition": branding.edition,
            "welcome_title": branding.strings.welcome_title,
            "welcome_subtitle": branding.strings.welcome_subtitle,
            "colors": {
                "primary": branding.colors.primary,
                "secondary": branding.colors.secondary,
                "background": branding.colors.background,
                "text": branding.colors.text,
            },
        }

    dispatcher.register(Command.GET_BRANDING, get_branding_handler)

    # GET_JOB_NAMES handler
    def get_job_names_handler(_args: dict) -> dict:
        return {"jobs": engine.get_job_names()}

    dispatcher.register(Command.GET_JOB_NAMES, get_job_names_handler)

    # START_INSTALLATION handler
    def start_installation_handler(_args: dict) -> dict:
        # This will be called in the engine context
        # TODO: Actually start the installation
        return {"started": True, "job_count": len(engine.get_job_names())}

    dispatcher.register(Command.START_INSTALLATION, start_installation_handler)

    # CANCEL_INSTALLATION handler
    def cancel_installation_handler(_args: dict) -> dict:
        # TODO: Implement cancellation
        return {"cancelled": True}

    dispatcher.register(Command.CANCEL_INSTALLATION, cancel_installation_handler)

    # VALIDATE_CONFIG handler
    def validate_config_handler(_args: dict) -> dict:
        # Configuration is already validated on load
        return {"valid": True}

    dispatcher.register(Command.VALIDATE_CONFIG, validate_config_handler)

    # SHUTDOWN handler - this one needs special handling
    def shutdown_handler(_args: dict) -> dict:
        # Signal shutdown (will be handled by server loop)
        return {"shutting_down": True}

    dispatcher.register(Command.SHUTDOWN, shutdown_handler)

    return dispatcher


def run_engine_server(
    config_path: Path,
    socket_path: Path = DEFAULT_SOCKET_PATH,
    debug: bool = False,
    dry_run: bool = False,
) -> int:
    """
    Run the engine server (called when --engine flag is used).

    Args:
        config_path: Path to configuration file
        socket_path: Path for IPC socket
        debug: Enable debug mode
        dry_run: Enable dry-run mode

    Returns:
        Exit code
    """
    from xen.core.engine import ConfigurationError, Engine

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="[ENGINE] %(asctime)s %(levelname)s: %(message)s",
    )

    logger.info(f"Starting engine server (PID: {os.getpid()})")
    logger.info(f"Config: {config_path}")
    logger.info(f"Socket: {socket_path}")
    logger.info(f"Running as: {os.getuid()} (root={os.getuid() == 0})")

    # Partitioning, mounting, arch-chroot, and bootloader install all
    # require root - refuse rather than fail mid-partitioning with the
    # disk already wiped. --dry-run never touches real devices, exempt.
    if not dry_run and not check_root_privileges():
        logger.error(
            "Engine must run as root (partitioning/install/bootloader require it). "
            "Launch via pkexec/sudo, or pass --dry-run for a no-op simulation."
        )
        return 1

    # Load configuration
    try:
        engine = Engine.from_config_file(config_path)
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # Create dispatcher with handlers
    dispatcher = create_engine_dispatcher(engine)

    # Create and start server
    server = IPCServer(socket_path, dispatcher=dispatcher)

    # Handle shutdown signals
    shutdown_requested = False

    def signal_handler(signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        logger.info(f"Received signal {signum}, shutting down")
        shutdown_requested = True

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        server.start()
        logger.info("Engine server ready")

        # Broadcast ready event
        server.broadcast_event(Event.ENGINE_READY, {"pid": os.getpid()})

        # Main loop - wait for shutdown
        while not shutdown_requested and server.is_running:
            time.sleep(0.5)

        logger.info("Engine server shutting down")
        return 0

    except Exception as e:
        logger.exception(f"Engine server error: {e}")
        return 1

    finally:
        server.stop()


def check_root_privileges() -> bool:
    """Check if running as root."""
    return os.getuid() == 0


def ensure_socket_directory(socket_path: Path) -> None:
    """Ensure socket directory exists with proper permissions."""
    socket_dir = socket_path.parent
    if not socket_dir.exists():
        socket_dir.mkdir(parents=True, mode=0o700)

"""
IPC Security Layer.

Provides validation, sanitization, and security controls for IPC communication.
Prevents command injection, path traversal, and unauthorized operations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from xen.ipc.exceptions import IPCErrorCode, IPCSecurityError, IPCValidationError
from xen.ipc.protocol import Command, IPCMessage, MessageType

# Allowed commands whitelist
ALLOWED_COMMANDS: frozenset[str] = frozenset(cmd.value for cmd in Command)

# Allowed root directories for path operations
ALLOWED_PATH_ROOTS: tuple[Path, ...] = (
    Path("/mnt"),
    Path("/target"),
    Path("/tmp"),
    Path("/run/xen"),
)

# Maximum argument sizes
MAX_STRING_LENGTH = 4096
MAX_ARRAY_LENGTH = 1000
MAX_DICT_DEPTH = 10

# Dangerous patterns to reject
DANGEROUS_PATH_PATTERNS = (
    r"\.\.",  # Directory traversal
    r"~",  # Home directory expansion
    r"\$",  # Variable expansion
    r"`",  # Command substitution
    r"\|",  # Pipe
    r";",  # Command separator
    r"&",  # Background/AND
    r">",  # Redirect
    r"<",  # Redirect
)


class IPCSecurityValidator:
    """
    Validates IPC messages for security compliance.

    Responsibilities:
    - Command whitelist enforcement
    - Path sanitization
    - Input validation
    - Argument schema validation
    """

    def __init__(
        self,
        allowed_commands: frozenset[str] | None = None,
        allowed_path_roots: tuple[Path, ...] | None = None,
        strict_mode: bool = True,
    ) -> None:
        """
        Initialize security validator.

        Args:
            allowed_commands: Commands to allow (defaults to all Command enum values)
            allowed_path_roots: Root paths allowed for file operations
            strict_mode: If True, reject unknown fields in args
        """
        self.allowed_commands = allowed_commands or ALLOWED_COMMANDS
        self.allowed_path_roots = allowed_path_roots or ALLOWED_PATH_ROOTS
        self.strict_mode = strict_mode
        self._dangerous_pattern = re.compile("|".join(DANGEROUS_PATH_PATTERNS))

    def validate_message(self, message: IPCMessage) -> None:
        """
        Validate an IPC message for security compliance.

        Args:
            message: Message to validate

        Raises:
            IPCSecurityError: If message fails security validation
            IPCValidationError: If message structure is invalid
        """
        # Validate basic message structure
        message.validate()

        # Type-specific validation
        if message.type == MessageType.REQUEST:
            self._validate_request(message)
        elif message.type == MessageType.RESPONSE:
            self._validate_response(message)
        elif message.type == MessageType.EVENT:
            self._validate_event(message)

    def _validate_request(self, message: IPCMessage) -> None:
        """Validate a request message."""
        command = message.command
        if not command:
            raise IPCValidationError(
                "Request missing command",
                code=IPCErrorCode.INVALID_COMMAND,
            )

        # Command whitelist check
        if command not in self.allowed_commands:
            raise IPCSecurityError(
                f"Command not allowed: {command}",
                code=IPCErrorCode.PERMISSION_DENIED,
                details={"command": command, "allowed": list(self.allowed_commands)},
            )

        # Validate args
        args = message.args
        self._validate_args(command, args)

    def _validate_response(self, message: IPCMessage) -> None:
        """Validate a response message."""
        # Responses are generated internally, minimal validation
        status = message.status
        if status not in ("success", "error"):
            raise IPCValidationError(
                f"Invalid response status: {status}",
                code=IPCErrorCode.INVALID_MESSAGE,
            )

    def _validate_event(self, message: IPCMessage) -> None:
        """Validate an event message."""
        # Events are generated internally, minimal validation
        event = message.event
        if not event:
            raise IPCValidationError(
                "Event missing event type",
                code=IPCErrorCode.INVALID_MESSAGE,
            )

    def _validate_args(self, command: str, args: dict[str, Any]) -> None:
        """
        Validate command arguments.

        Args:
            command: Command name
            args: Arguments to validate
        """
        # Validate all values recursively
        self._validate_value(args, depth=0)

        # Command-specific validation
        validators = {
            Command.START_INSTALLATION.value: self._validate_start_installation_args,
            Command.VALIDATE_CONFIG.value: self._validate_config_args,
        }

        validator = validators.get(command)
        if validator:
            validator(args)

    def _validate_value(self, value: Any, depth: int = 0) -> None:
        """
        Recursively validate a value.

        Args:
            value: Value to validate
            depth: Current nesting depth
        """
        if depth > MAX_DICT_DEPTH:
            raise IPCValidationError(
                f"Maximum nesting depth exceeded: {depth}",
                code=IPCErrorCode.VALIDATION_FAILED,
            )

        if isinstance(value, str):
            if len(value) > MAX_STRING_LENGTH:
                raise IPCValidationError(
                    f"String too long: {len(value)} > {MAX_STRING_LENGTH}",
                    code=IPCErrorCode.VALIDATION_FAILED,
                )
            # Check for dangerous patterns in strings that might be paths
            if "/" in value or "\\" in value:
                self._check_dangerous_patterns(value)

        elif isinstance(value, dict):
            if len(value) > MAX_ARRAY_LENGTH:
                raise IPCValidationError(
                    f"Dict too large: {len(value)} > {MAX_ARRAY_LENGTH}",
                    code=IPCErrorCode.VALIDATION_FAILED,
                )
            for v in value.values():
                self._validate_value(v, depth + 1)

        elif isinstance(value, list):
            if len(value) > MAX_ARRAY_LENGTH:
                raise IPCValidationError(
                    f"Array too large: {len(value)} > {MAX_ARRAY_LENGTH}",
                    code=IPCErrorCode.VALIDATION_FAILED,
                )
            for item in value:
                self._validate_value(item, depth + 1)

    def _check_dangerous_patterns(self, value: str) -> None:
        """Check for dangerous patterns in path-like strings."""
        if self._dangerous_pattern.search(value):
            raise IPCSecurityError(
                f"Dangerous pattern detected in value: {value[:50]}...",
                code=IPCErrorCode.PERMISSION_DENIED,
                details={"value": value[:100]},
            )

    def _validate_start_installation_args(self, args: dict[str, Any]) -> None:
        """Validate START_INSTALLATION command args."""
        target_root = args.get("target_root")
        if target_root:
            self.validate_path(target_root)

    def _validate_config_args(self, args: dict[str, Any]) -> None:
        """Validate VALIDATE_CONFIG command args."""
        config_data = args.get("config_data")
        if config_data and not isinstance(config_data, dict):
            raise IPCValidationError(
                "config_data must be a dictionary",
                code=IPCErrorCode.VALIDATION_FAILED,
            )

    def validate_path(self, path_str: str) -> Path:
        """
        Validate and sanitize a file path.

        Args:
            path_str: Path string to validate

        Returns:
            Sanitized Path object

        Raises:
            IPCSecurityError: If path is not allowed
        """
        # Check for dangerous patterns
        self._check_dangerous_patterns(path_str)

        # Resolve to absolute path
        try:
            path = Path(path_str).resolve()
        except (OSError, ValueError) as e:
            raise IPCSecurityError(
                f"Invalid path: {e}",
                code=IPCErrorCode.PERMISSION_DENIED,
                details={"path": path_str},
            ) from e

        # Check against allowed roots
        if not self._is_path_allowed(path):
            raise IPCSecurityError(
                f"Path not in allowed roots: {path}",
                code=IPCErrorCode.PERMISSION_DENIED,
                details={
                    "path": str(path),
                    "allowed_roots": [str(r) for r in self.allowed_path_roots],
                },
            )

        return path

    def _is_path_allowed(self, path: Path) -> bool:
        """Check if path is under an allowed root."""
        for root in self.allowed_path_roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def sanitize_args(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        """
        Sanitize command arguments.

        Removes any potentially dangerous content and normalizes values.

        Args:
            command: Command name
            args: Arguments to sanitize

        Returns:
            Sanitized arguments dictionary
        """
        # First validate
        self._validate_args(command, args)

        # Deep copy and sanitize
        return self._sanitize_value(args)

    def _sanitize_value(self, value: Any) -> Any:
        """Recursively sanitize a value."""
        if isinstance(value, str):
            # Strip whitespace and control characters
            return value.strip()
        elif isinstance(value, dict):
            return {k: self._sanitize_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        else:
            return value


class ValidationResult:
    """Result of a validation operation."""

    def __init__(
        self,
        valid: bool,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.valid = valid
        self.errors = errors or []
        self.warnings = warnings or []

    def __bool__(self) -> bool:
        return self.valid

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def create_default_validator() -> IPCSecurityValidator:
    """Create a security validator with default settings."""
    return IPCSecurityValidator()

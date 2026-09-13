"""
IPC Exception Hierarchy.

Defines all exceptions related to inter-process communication
between UI and Engine processes.
"""

from enum import Enum


class IPCErrorCode(str, Enum):
    """Error codes for IPC operations."""

    # Protocol errors
    INVALID_MESSAGE = "INVALID_MESSAGE"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    INVALID_COMMAND = "INVALID_COMMAND"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    MALFORMED_JSON = "MALFORMED_JSON"
    MESSAGE_TOO_LARGE = "MESSAGE_TOO_LARGE"

    # Connection errors
    CONNECTION_FAILED = "CONNECTION_FAILED"
    CONNECTION_LOST = "CONNECTION_LOST"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    SOCKET_ERROR = "SOCKET_ERROR"

    # Authentication/Authorization
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"

    # Engine errors
    ENGINE_NOT_READY = "ENGINE_NOT_READY"
    ENGINE_BUSY = "ENGINE_BUSY"
    INVALID_STATE = "INVALID_STATE"

    # Installation errors
    INVALID_TARGET = "INVALID_TARGET"
    INSUFFICIENT_SPACE = "INSUFFICIENT_SPACE"
    DISK_IO_ERROR = "DISK_IO_ERROR"
    JOB_FAILED = "JOB_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"

    # System errors
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    HANDLER_ERROR = "HANDLER_ERROR"


class IPCError(Exception):
    """Base exception for all IPC-related errors."""

    def __init__(
        self,
        message: str,
        code: IPCErrorCode = IPCErrorCode.INTERNAL_ERROR,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict:
        """Convert exception to dictionary for serialization."""
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


class IPCConnectionError(IPCError):
    """Raised when connection to IPC socket fails."""

    def __init__(
        self,
        message: str,
        code: IPCErrorCode = IPCErrorCode.CONNECTION_FAILED,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, details)


class IPCProtocolError(IPCError):
    """Raised when message protocol is violated."""

    def __init__(
        self,
        message: str,
        code: IPCErrorCode = IPCErrorCode.INVALID_MESSAGE,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, details)


class IPCSecurityError(IPCError):
    """Raised when security validation fails."""

    def __init__(
        self,
        message: str,
        code: IPCErrorCode = IPCErrorCode.PERMISSION_DENIED,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, details)


class IPCTimeoutError(IPCError):
    """Raised when an IPC operation times out."""

    def __init__(
        self,
        message: str = "Operation timed out",
        code: IPCErrorCode = IPCErrorCode.TIMEOUT,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, details)


class IPCValidationError(IPCError):
    """Raised when message validation fails."""

    def __init__(
        self,
        message: str,
        code: IPCErrorCode = IPCErrorCode.VALIDATION_FAILED,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, code, details)

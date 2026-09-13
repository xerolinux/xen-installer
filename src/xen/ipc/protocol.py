"""
IPC Message Protocol.

Defines the message format and serialization for communication
between UI and Engine processes.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from xen.ipc.exceptions import IPCErrorCode, IPCProtocolError

# Protocol version
PROTOCOL_VERSION = "1.0"
SUPPORTED_VERSIONS = {"1.0"}

# Message size limits
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB


class MessageType(str, Enum):
    """Types of IPC messages."""

    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"


class Command(str, Enum):
    """Available commands from UI to Engine."""

    START_INSTALLATION = "START_INSTALLATION"
    GET_STATUS = "GET_STATUS"
    CANCEL_INSTALLATION = "CANCEL_INSTALLATION"
    VALIDATE_CONFIG = "VALIDATE_CONFIG"
    GET_BRANDING = "GET_BRANDING"
    GET_JOB_NAMES = "GET_JOB_NAMES"
    SHUTDOWN = "SHUTDOWN"
    PING = "PING"


class Event(str, Enum):
    """Events from Engine to UI."""

    JOB_STARTED = "JOB_STARTED"
    JOB_PROGRESS = "JOB_PROGRESS"
    JOB_COMPLETED = "JOB_COMPLETED"
    ERROR_OCCURRED = "ERROR_OCCURRED"
    INSTALLATION_COMPLETE = "INSTALLATION_COMPLETE"
    ENGINE_READY = "ENGINE_READY"
    ENGINE_SHUTDOWN = "ENGINE_SHUTDOWN"


class ResponseStatus(str, Enum):
    """Status of response messages."""

    SUCCESS = "success"
    ERROR = "error"


@dataclass
class IPCMessage:
    """
    IPC Message envelope.

    All communication between UI and Engine uses this format.
    Messages are serialized as JSON with a 4-byte length prefix.
    """

    version: str
    type: MessageType
    id: str
    timestamp: int
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_request(cls, command: Command | str, args: dict | None = None) -> IPCMessage:
        """Create a new request message."""
        cmd = command.value if isinstance(command, Command) else command
        return cls(
            version=PROTOCOL_VERSION,
            type=MessageType.REQUEST,
            id=str(uuid.uuid4()),
            timestamp=int(time.time()),
            payload={"command": cmd, "args": args or {}},
        )

    @classmethod
    def create_response(
        cls,
        request_id: str,
        command: str,
        status: ResponseStatus,
        result: dict | None = None,
        error: dict | None = None,
    ) -> IPCMessage:
        """Create a response message for a request."""
        payload: dict[str, Any] = {"status": status.value, "command": command}
        if status == ResponseStatus.SUCCESS:
            payload["result"] = result or {}
        else:
            payload["error"] = error or {}
        return cls(
            version=PROTOCOL_VERSION,
            type=MessageType.RESPONSE,
            id=request_id,
            timestamp=int(time.time()),
            payload=payload,
        )

    @classmethod
    def create_event(cls, event: Event | str, data: dict | None = None) -> IPCMessage:
        """Create an event message."""
        evt = event.value if isinstance(event, Event) else event
        return cls(
            version=PROTOCOL_VERSION,
            type=MessageType.EVENT,
            id=str(uuid.uuid4()),
            timestamp=int(time.time()),
            payload={"event": evt, "data": data or {}},
        )

    def to_json(self) -> str:
        """Serialize message to JSON string."""
        return json.dumps(
            {
                "version": self.version,
                "type": self.type.value,
                "id": self.id,
                "timestamp": self.timestamp,
                "payload": self.payload,
            },
            separators=(",", ":"),  # Compact JSON
        )

    def to_bytes(self) -> bytes:
        """Serialize message to bytes (UTF-8 encoded JSON)."""
        return self.to_json().encode("utf-8")

    @classmethod
    def from_json(cls, data: str) -> IPCMessage:
        """
        Deserialize message from JSON string.

        Raises:
            IPCProtocolError: If message format is invalid
        """
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as e:
            raise IPCProtocolError(
                f"Invalid JSON: {e}",
                code=IPCErrorCode.MALFORMED_JSON,
                details={"raw": data[:100]},
            ) from e

        return cls._from_dict(obj)

    @classmethod
    def from_bytes(cls, data: bytes) -> IPCMessage:
        """
        Deserialize message from bytes.

        Raises:
            IPCProtocolError: If message format is invalid
        """
        try:
            json_str = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise IPCProtocolError(
                f"Invalid UTF-8 encoding: {e}",
                code=IPCErrorCode.MALFORMED_JSON,
            ) from e

        return cls.from_json(json_str)

    @classmethod
    def _from_dict(cls, obj: dict) -> IPCMessage:
        """Create message from dictionary."""
        required_fields = {"version", "type", "id", "timestamp", "payload"}
        missing = required_fields - set(obj.keys())
        if missing:
            raise IPCProtocolError(
                f"Missing required fields: {missing}",
                code=IPCErrorCode.INVALID_MESSAGE,
                details={"missing_fields": list(missing)},
            )

        # Validate version
        version = obj["version"]
        if version not in SUPPORTED_VERSIONS:
            raise IPCProtocolError(
                f"Unsupported protocol version: {version}",
                code=IPCErrorCode.UNSUPPORTED_VERSION,
                details={"version": version, "supported": list(SUPPORTED_VERSIONS)},
            )

        # Validate message type
        try:
            msg_type = MessageType(obj["type"])
        except ValueError as e:
            raise IPCProtocolError(
                f"Invalid message type: {obj['type']}",
                code=IPCErrorCode.INVALID_MESSAGE,
            ) from e

        return cls(
            version=version,
            type=msg_type,
            id=obj["id"],
            timestamp=obj["timestamp"],
            payload=obj.get("payload", {}),
        )

    def validate(self) -> bool:
        """
        Validate message structure.

        Returns:
            True if message is valid

        Raises:
            IPCProtocolError: If message is invalid
        """
        # Check version
        if self.version not in SUPPORTED_VERSIONS:
            raise IPCProtocolError(
                f"Unsupported version: {self.version}",
                code=IPCErrorCode.UNSUPPORTED_VERSION,
            )

        # Type-specific validation
        if self.type == MessageType.REQUEST:
            if "command" not in self.payload:
                raise IPCProtocolError(
                    "Request missing 'command' in payload",
                    code=IPCErrorCode.INVALID_MESSAGE,
                )
        elif self.type == MessageType.RESPONSE:
            if "status" not in self.payload:
                raise IPCProtocolError(
                    "Response missing 'status' in payload",
                    code=IPCErrorCode.INVALID_MESSAGE,
                )
        elif self.type == MessageType.EVENT and "event" not in self.payload:
            raise IPCProtocolError(
                "Event missing 'event' in payload",
                code=IPCErrorCode.INVALID_MESSAGE,
            )

        return True

    @property
    def command(self) -> str | None:
        """Get command from request message."""
        if self.type == MessageType.REQUEST:
            return self.payload.get("command")
        return None

    @property
    def args(self) -> dict:
        """Get args from request message."""
        if self.type == MessageType.REQUEST:
            return self.payload.get("args", {})
        return {}

    @property
    def event(self) -> str | None:
        """Get event type from event message."""
        if self.type == MessageType.EVENT:
            return self.payload.get("event")
        return None

    @property
    def data(self) -> dict:
        """Get data from event message."""
        if self.type == MessageType.EVENT:
            return self.payload.get("data", {})
        return {}

    @property
    def status(self) -> str | None:
        """Get status from response message."""
        if self.type == MessageType.RESPONSE:
            return self.payload.get("status")
        return None

    @property
    def is_success(self) -> bool:
        """Check if response indicates success."""
        return self.status == ResponseStatus.SUCCESS.value

    @property
    def result(self) -> dict:
        """Get result from successful response."""
        if self.type == MessageType.RESPONSE and self.is_success:
            return self.payload.get("result", {})
        return {}

    @property
    def error(self) -> dict:
        """Get error from failed response."""
        if self.type == MessageType.RESPONSE and not self.is_success:
            return self.payload.get("error", {})
        return {}

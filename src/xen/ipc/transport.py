"""
Unix Socket Transport Layer.

Low-level socket operations for IPC communication.
Uses length-prefixed messages for reliable framing.
"""

from __future__ import annotations

import contextlib
import os
import socket
import struct
from pathlib import Path
from typing import TYPE_CHECKING

from xen.ipc.exceptions import (
    IPCConnectionError,
    IPCErrorCode,
    IPCProtocolError,
    IPCTimeoutError,
)
from xen.ipc.protocol import MAX_MESSAGE_SIZE, IPCMessage

if TYPE_CHECKING:
    pass

# Default socket path
DEFAULT_SOCKET_PATH = "/run/xen/ipc.sock"

# Socket timeouts (seconds)
DEFAULT_CONNECTION_TIMEOUT = 30.0
DEFAULT_RECEIVE_TIMEOUT = 300.0  # 5 minutes for long operations

# Length prefix format: unsigned 4-byte integer, big-endian
LENGTH_PREFIX_FORMAT = ">I"
LENGTH_PREFIX_SIZE = struct.calcsize(LENGTH_PREFIX_FORMAT)


class UnixSocketTransport:
    """
    Transport layer for Unix socket communication.

    Handles:
    - Socket creation (server/client)
    - Message framing (length-prefix protocol)
    - Reliable send/receive operations
    - Connection state management
    """

    def __init__(
        self,
        socket_path: str | Path = DEFAULT_SOCKET_PATH,
        connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.connection_timeout = connection_timeout
        self.receive_timeout = receive_timeout
        self._socket: socket.socket | None = None
        self._is_server = False

    @property
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self._socket is not None

    def create_server_socket(self) -> socket.socket:
        """
        Create and bind a server socket.

        Returns:
            Bound server socket ready for listening

        Raises:
            IPCConnectionError: If socket creation fails
        """
        # Ensure socket directory exists with secure permissions
        socket_dir = self.socket_path.parent
        try:
            # Create directory if needed (exist_ok handles race conditions)
            # Don't try to chmod system directories like /tmp
            if not socket_dir.exists():
                socket_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            # Always enforce permissions on our dedicated directory
            # (handles race condition where another process created it first)
            if socket_dir == Path("/run/xen"):
                with contextlib.suppress(PermissionError):
                    os.chmod(socket_dir, 0o700)
        except OSError as e:
            raise IPCConnectionError(
                f"Failed to create socket directory: {e}",
                code=IPCErrorCode.SOCKET_ERROR,
                details={"path": str(socket_dir)},
            ) from e

        # Remove existing socket file
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError as e:
                raise IPCConnectionError(
                    f"Failed to remove existing socket: {e}",
                    code=IPCErrorCode.SOCKET_ERROR,
                    details={"path": str(self.socket_path)},
                ) from e

        # Create socket
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(str(self.socket_path))

            # Set secure permissions on socket file
            os.chmod(self.socket_path, 0o600)  # Owner read/write only

            sock.listen(5)  # Allow up to 5 pending connections
            sock.settimeout(self.connection_timeout)

            self._socket = sock
            self._is_server = True
            return sock

        except OSError as e:
            raise IPCConnectionError(
                f"Failed to create server socket: {e}",
                code=IPCErrorCode.SOCKET_ERROR,
                details={"path": str(self.socket_path)},
            ) from e

    def connect_client_socket(self) -> socket.socket:
        """
        Create and connect a client socket.

        Returns:
            Connected client socket

        Raises:
            IPCConnectionError: If connection fails
        """
        if not self.socket_path.exists():
            raise IPCConnectionError(
                f"Socket does not exist: {self.socket_path}",
                code=IPCErrorCode.CONNECTION_FAILED,
                details={"path": str(self.socket_path)},
            )

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.connection_timeout)
            sock.connect(str(self.socket_path))
            sock.settimeout(self.receive_timeout)

            self._socket = sock
            self._is_server = False
            return sock

        except TimeoutError as e:
            raise IPCTimeoutError(
                f"Connection timed out: {self.socket_path}",
                details={"path": str(self.socket_path)},
            ) from e
        except ConnectionRefusedError as e:
            raise IPCConnectionError(
                f"Connection refused: {self.socket_path}",
                code=IPCErrorCode.CONNECTION_REFUSED,
                details={"path": str(self.socket_path)},
            ) from e
        except OSError as e:
            raise IPCConnectionError(
                f"Failed to connect: {e}",
                code=IPCErrorCode.CONNECTION_FAILED,
                details={"path": str(self.socket_path)},
            ) from e

    def close(self) -> None:
        """Close the socket connection."""
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.close()
            self._socket = None

        # Clean up socket file if we're the server
        if self._is_server and self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()

    def send_message(self, sock: socket.socket, message: IPCMessage) -> None:
        """
        Send a message with length prefix.

        Args:
            sock: Socket to send on
            message: Message to send

        Raises:
            IPCProtocolError: If message is too large
            IPCConnectionError: If send fails
        """
        data = message.to_bytes()

        if len(data) > MAX_MESSAGE_SIZE:
            raise IPCProtocolError(
                f"Message too large: {len(data)} bytes (max: {MAX_MESSAGE_SIZE})",
                code=IPCErrorCode.MESSAGE_TOO_LARGE,
                details={"size": len(data), "max": MAX_MESSAGE_SIZE},
            )

        # Create length-prefixed message
        length_prefix = struct.pack(LENGTH_PREFIX_FORMAT, len(data))
        framed_message = length_prefix + data

        try:
            sock.sendall(framed_message)
        except BrokenPipeError as e:
            raise IPCConnectionError(
                "Connection lost during send",
                code=IPCErrorCode.CONNECTION_LOST,
            ) from e
        except OSError as e:
            raise IPCConnectionError(
                f"Send failed: {e}",
                code=IPCErrorCode.SOCKET_ERROR,
            ) from e

    def recv_message(self, sock: socket.socket) -> IPCMessage | None:
        """
        Receive a length-prefixed message.

        Args:
            sock: Socket to receive from

        Returns:
            Received message, or None if connection closed

        Raises:
            IPCProtocolError: If message format is invalid
            IPCConnectionError: If receive fails
            IPCTimeoutError: If operation times out
        """
        try:
            # Read length prefix
            length_data = self._recv_exact(sock, LENGTH_PREFIX_SIZE)
            if length_data is None:
                return None  # Connection closed

            message_length = struct.unpack(LENGTH_PREFIX_FORMAT, length_data)[0]

            # Validate message length
            if message_length > MAX_MESSAGE_SIZE:
                raise IPCProtocolError(
                    f"Message too large: {message_length} bytes",
                    code=IPCErrorCode.MESSAGE_TOO_LARGE,
                    details={"size": message_length, "max": MAX_MESSAGE_SIZE},
                )

            if message_length == 0:
                raise IPCProtocolError(
                    "Empty message received",
                    code=IPCErrorCode.INVALID_MESSAGE,
                )

            # Read message body
            message_data = self._recv_exact(sock, message_length)
            if message_data is None:
                raise IPCConnectionError(
                    "Connection closed during message receive",
                    code=IPCErrorCode.CONNECTION_LOST,
                )

            return IPCMessage.from_bytes(message_data)

        except TimeoutError as e:
            raise IPCTimeoutError("Receive operation timed out") from e

    def _recv_exact(self, sock: socket.socket, length: int) -> bytes | None:
        """
        Receive exactly `length` bytes from socket.

        Args:
            sock: Socket to receive from
            length: Exact number of bytes to receive

        Returns:
            Received bytes, or None if connection closed
        """
        data = bytearray()
        remaining = length

        while remaining > 0:
            try:
                chunk = sock.recv(min(remaining, 65536))
                if not chunk:
                    # Connection closed
                    if len(data) == 0:
                        return None
                    raise IPCConnectionError(
                        f"Connection closed after receiving {len(data)}/{length} bytes",
                        code=IPCErrorCode.CONNECTION_LOST,
                    )
                data.extend(chunk)
                remaining -= len(chunk)
            except OSError as e:
                raise IPCConnectionError(
                    f"Receive failed: {e}",
                    code=IPCErrorCode.SOCKET_ERROR,
                ) from e

        return bytes(data)

    def accept_client(self) -> tuple[socket.socket, str]:
        """
        Accept a client connection (server only).

        Returns:
            Tuple of (client_socket, client_address)

        Raises:
            IPCConnectionError: If not a server socket
            IPCTimeoutError: If accept times out
        """
        if not self._is_server or self._socket is None:
            raise IPCConnectionError(
                "Not a server socket",
                code=IPCErrorCode.INVALID_STATE,
            )

        try:
            client_sock, client_addr = self._socket.accept()
            client_sock.settimeout(self.receive_timeout)
            return client_sock, str(client_addr) if client_addr else "local"
        except TimeoutError as e:
            raise IPCTimeoutError("Accept operation timed out") from e
        except OSError as e:
            raise IPCConnectionError(
                f"Accept failed: {e}",
                code=IPCErrorCode.SOCKET_ERROR,
            ) from e

    def __enter__(self) -> UnixSocketTransport:
        """Context manager entry."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit - close socket."""
        self.close()

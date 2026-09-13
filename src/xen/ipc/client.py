"""
IPC Client.

Runs in the UI process (user context) and communicates with the Engine process.
Provides a high-level API for sending commands and receiving events.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import socket
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from xen.ipc.exceptions import (
    IPCConnectionError,
    IPCErrorCode,
    IPCProtocolError,
    IPCTimeoutError,
)
from xen.ipc.protocol import Command, Event, IPCMessage, MessageType
from xen.ipc.transport import UnixSocketTransport

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Event callback type
EventCallback = Callable[[str, dict[str, Any]], None]


class IPCClient:
    """
    IPC Client for UI process.

    Provides:
    - Synchronous command execution
    - Asynchronous command support
    - Event subscription
    - Connection management
    """

    def __init__(
        self,
        socket_path: str | Path | None = None,
        connect_timeout: float = 30.0,
        request_timeout: float = 300.0,
    ) -> None:
        """
        Initialize IPC client.

        Args:
            socket_path: Path to Unix socket (defaults to /run/xen/ipc.sock)
            connect_timeout: Timeout for connection attempts
            request_timeout: Timeout for request/response
        """
        self._transport = (
            UnixSocketTransport(socket_path, connect_timeout, request_timeout)
            if socket_path
            else UnixSocketTransport(
                connection_timeout=connect_timeout, receive_timeout=request_timeout
            )
        )

        self._connected = False
        self._socket: socket.socket | None = None

        # Request tracking
        self._pending_requests: dict[str, queue.Queue[IPCMessage]] = {}
        self._request_lock = threading.Lock()

        # Event handling
        self._event_callbacks: dict[str, list[EventCallback]] = {}
        self._global_event_callbacks: list[EventCallback] = []
        self._event_lock = threading.Lock()

        # Receiver thread
        self._receiver_thread: threading.Thread | None = None
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected and self._socket is not None

    def connect(self) -> None:
        """
        Connect to the IPC server.

        Raises:
            IPCConnectionError: If connection fails
        """
        if self._connected:
            logger.warning("Already connected")
            return

        logger.info(f"Connecting to IPC server at {self._transport.socket_path}")

        self._socket = self._transport.connect_client_socket()
        self._connected = True
        self._running = True

        # Start receiver thread
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop,
            name="ipc-receiver",
            daemon=True,
        )
        self._receiver_thread.start()

        logger.info("Connected to IPC server")

    def disconnect(self) -> None:
        """Disconnect from the IPC server."""
        if not self._connected:
            return

        logger.info("Disconnecting from IPC server")

        self._running = False
        self._connected = False

        # Close socket
        if self._socket:
            with contextlib.suppress(OSError):
                self._socket.close()
            self._socket = None

        # Wait for receiver thread
        if self._receiver_thread and self._receiver_thread.is_alive():
            self._receiver_thread.join(timeout=2.0)

        # Cancel pending requests
        with self._request_lock:
            for request_queue in self._pending_requests.values():
                # Put error message to unblock waiters
                error_msg = IPCMessage.create_response(
                    request_id="",
                    command="",
                    status="error",  # type: ignore[arg-type]
                    error={"code": "CONNECTION_LOST", "message": "Connection closed"},
                )
                request_queue.put(error_msg)
            self._pending_requests.clear()

        logger.info("Disconnected from IPC server")

    def send_command(
        self,
        command: Command | str,
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Send a command and wait for response.

        Args:
            command: Command to send
            args: Command arguments
            timeout: Response timeout (uses default if None)

        Returns:
            Result dictionary from response

        Raises:
            IPCConnectionError: If not connected
            IPCTimeoutError: If response times out
            IPCProtocolError: If response indicates error
        """
        if not self.is_connected:
            raise IPCConnectionError(
                "Not connected to server",
                code=IPCErrorCode.CONNECTION_FAILED,
            )

        # Create request
        request = IPCMessage.create_request(command, args)
        logger.debug(f"Sending command: {request.command}")

        # Create response queue
        response_queue: queue.Queue[IPCMessage] = queue.Queue()
        with self._request_lock:
            self._pending_requests[request.id] = response_queue

        try:
            # Send request
            self._transport.send_message(self._socket, request)  # type: ignore[arg-type]

            # Wait for response
            try:
                response = response_queue.get(timeout=timeout or 300.0)
            except queue.Empty as err:
                raise IPCTimeoutError(
                    f"Timeout waiting for response to {request.command}",
                    details={"command": request.command, "request_id": request.id},
                ) from err

            # Check response status
            if not response.is_success:
                error = response.error
                raise IPCProtocolError(
                    error.get("message", "Unknown error"),
                    code=IPCErrorCode(error.get("code", "INTERNAL_ERROR")),
                    details=error.get("details", {}),
                )

            return response.result

        finally:
            # Remove from pending
            with self._request_lock:
                self._pending_requests.pop(request.id, None)

    def send_command_async(
        self,
        command: Command | str,
        args: dict[str, Any] | None = None,
        callback: Callable[[dict[str, Any] | None, Exception | None], None] | None = None,
    ) -> str:
        """
        Send a command asynchronously.

        Args:
            command: Command to send
            args: Command arguments
            callback: Called with (result, error) when response received

        Returns:
            Request ID

        Raises:
            IPCConnectionError: If not connected
        """
        if not self.is_connected:
            raise IPCConnectionError(
                "Not connected to server",
                code=IPCErrorCode.CONNECTION_FAILED,
            )

        # Create request
        request = IPCMessage.create_request(command, args)

        # Register pending request regardless of callback presence
        response_queue: queue.Queue[IPCMessage] = queue.Queue()
        with self._request_lock:
            self._pending_requests[request.id] = response_queue

        if callback:
            # Set up callback handler
            def response_handler(response: IPCMessage) -> None:
                if response.is_success:
                    callback(response.result, None)
                else:
                    error = response.error
                    callback(
                        None,
                        IPCProtocolError(
                            error.get("message", "Unknown error"),
                            code=IPCErrorCode(error.get("code", "INTERNAL_ERROR")),
                        ),
                    )

            # Start handler thread for callbacks
            def waiter() -> None:
                try:
                    response = response_queue.get(timeout=300.0)
                    response_handler(response)
                except queue.Empty:
                    callback(None, IPCTimeoutError("Request timed out"))
                finally:
                    with self._request_lock:
                        self._pending_requests.pop(request.id, None)

            threading.Thread(target=waiter, daemon=True).start()
        else:
            # No callback: consume response to clear pending request
            def _discard() -> None:
                try:
                    response_queue.get(timeout=300.0)
                except queue.Empty:
                    pass
                finally:
                    with self._request_lock:
                        self._pending_requests.pop(request.id, None)

            threading.Thread(target=_discard, daemon=True).start()

        # Send request
        self._transport.send_message(self._socket, request)  # type: ignore[arg-type]
        return request.id

    def subscribe_event(
        self,
        event: Event | str | None,
        callback: EventCallback,
    ) -> None:
        """
        Subscribe to an event.

        Args:
            event: Event type to subscribe to, or None for all events
            callback: Function called with (event_type, data) when event received
        """
        evt = event.value if isinstance(event, Event) else event

        with self._event_lock:
            if evt is None:
                self._global_event_callbacks.append(callback)
            else:
                if evt not in self._event_callbacks:
                    self._event_callbacks[evt] = []
                self._event_callbacks[evt].append(callback)

    def unsubscribe_event(
        self,
        event: Event | str | None,
        callback: EventCallback,
    ) -> bool:
        """
        Unsubscribe from an event.

        Args:
            event: Event type, or None for global subscription
            callback: Callback to remove

        Returns:
            True if callback was removed
        """
        evt = event.value if isinstance(event, Event) else event

        with self._event_lock:
            if evt is None:
                if callback in self._global_event_callbacks:
                    self._global_event_callbacks.remove(callback)
                    return True
            elif evt in self._event_callbacks and callback in self._event_callbacks[evt]:
                self._event_callbacks[evt].remove(callback)
                return True
        return False

    def _receiver_loop(self) -> None:
        """Receive messages from server."""
        while self._running and self._socket:
            try:
                message = self._transport.recv_message(self._socket)

                if message is None:
                    # Connection closed
                    logger.info("Server closed connection")
                    self._connected = False
                    break

                self._handle_message(message)

            except IPCTimeoutError:
                # Normal timeout, continue
                continue
            except IPCConnectionError as e:
                logger.error(f"Connection error: {e}")
                self._connected = False
                break
            except Exception as e:
                if self._running:
                    logger.exception(f"Receiver error: {e}")
                break

    def _handle_message(self, message: IPCMessage) -> None:
        """Handle an incoming message."""
        if message.type == MessageType.RESPONSE:
            # Route to pending request
            with self._request_lock:
                response_queue = self._pending_requests.get(message.id)
                if response_queue:
                    response_queue.put(message)
                else:
                    logger.warning(f"Received response for unknown request: {message.id}")

        elif message.type == MessageType.EVENT:
            # Dispatch to event callbacks
            self._dispatch_event(message)

        else:
            logger.warning(f"Unexpected message type: {message.type}")

    def _dispatch_event(self, message: IPCMessage) -> None:
        """Dispatch an event to registered callbacks."""
        event_type = message.event
        event_data = message.data

        if not event_type:
            return

        with self._event_lock:
            # Call specific callbacks
            callbacks = self._event_callbacks.get(event_type, [])
            for callback in callbacks:
                try:
                    callback(event_type, event_data)
                except Exception as e:
                    logger.exception(f"Event callback error: {e}")

            # Call global callbacks
            for callback in self._global_event_callbacks:
                try:
                    callback(event_type, event_data)
                except Exception as e:
                    logger.exception(f"Global event callback error: {e}")

    # Convenience methods for common commands

    def ping(self, echo: str = "") -> dict[str, Any]:
        """Send a ping command."""
        return self.send_command(Command.PING, {"echo": echo})

    def get_status(self) -> dict[str, Any]:
        """Get engine status."""
        return self.send_command(Command.GET_STATUS)

    def get_branding(self) -> dict[str, Any]:
        """Get branding configuration."""
        return self.send_command(Command.GET_BRANDING)

    def get_job_names(self) -> dict[str, Any]:
        """Get list of job names."""
        return self.send_command(Command.GET_JOB_NAMES)

    def start_installation(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start installation process."""
        return self.send_command(Command.START_INSTALLATION, config or {})

    def cancel_installation(self) -> dict[str, Any]:
        """Cancel ongoing installation."""
        return self.send_command(Command.CANCEL_INSTALLATION)

    def shutdown(self) -> dict[str, Any]:
        """Request engine shutdown."""
        return self.send_command(Command.SHUTDOWN)

    def __enter__(self) -> IPCClient:
        """Context manager entry - connect."""
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit - disconnect."""
        self.disconnect()


def create_ui_client(socket_path: str | Path | None = None) -> IPCClient:
    """
    Create an IPC client configured for the UI process.

    Args:
        socket_path: Optional custom socket path

    Returns:
        Configured IPCClient ready to connect
    """
    return IPCClient(socket_path=socket_path)

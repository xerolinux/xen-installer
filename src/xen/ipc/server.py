"""
IPC Server.

Runs in the Engine process (root context) and handles requests from the UI process.
Manages client connections, message processing, and event broadcasting.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import socket
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from xen.ipc.dispatcher import IPCDispatcher, create_default_dispatcher
from xen.ipc.exceptions import IPCConnectionError, IPCErrorCode, IPCTimeoutError
from xen.ipc.protocol import Event, IPCMessage
from xen.ipc.security import IPCSecurityValidator, create_default_validator
from xen.ipc.transport import UnixSocketTransport

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Event listener type
EventListener = Callable[[IPCMessage], None]


class IPCServer:
    """
    IPC Server running in the Engine process.

    Responsibilities:
    - Accept client connections
    - Process incoming requests
    - Validate and dispatch commands
    - Broadcast events to connected clients
    - Handle connection lifecycle
    """

    def __init__(
        self,
        socket_path: str | Path | None = None,
        dispatcher: IPCDispatcher | None = None,
        validator: IPCSecurityValidator | None = None,
    ) -> None:
        """
        Initialize IPC server.

        Args:
            socket_path: Path to Unix socket (defaults to /run/xen/ipc.sock)
            dispatcher: Message dispatcher (creates default if None)
            validator: Security validator (creates default if None)
        """
        self._transport = UnixSocketTransport(socket_path) if socket_path else UnixSocketTransport()
        self._dispatcher = dispatcher or create_default_dispatcher()
        self._validator = validator or create_default_validator()

        self._running = False
        self._accept_thread: threading.Thread | None = None
        self._client_threads: list[threading.Thread] = []
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()

        # Event queue for broadcasting
        self._event_queue: queue.Queue[IPCMessage] = queue.Queue()
        self._event_thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running

    @property
    def socket_path(self) -> str:
        """Get socket path."""
        return str(self._transport.socket_path)

    @property
    def dispatcher(self) -> IPCDispatcher:
        """Get the message dispatcher."""
        return self._dispatcher

    @property
    def connected_clients(self) -> int:
        """Get number of connected clients."""
        with self._clients_lock:
            return len(self._clients)

    def start(self) -> None:
        """
        Start the IPC server.

        Creates socket and starts accepting connections.

        Raises:
            IPCConnectionError: If server cannot be started
        """
        if self._running:
            logger.warning("Server already running")
            return

        logger.info(f"Starting IPC server on {self._transport.socket_path}")

        # Create server socket
        self._transport.create_server_socket()
        self._running = True

        # Start accept thread
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="ipc-accept",
            daemon=True,
        )
        self._accept_thread.start()

        # Start event broadcast thread
        self._event_thread = threading.Thread(
            target=self._event_loop,
            name="ipc-events",
            daemon=True,
        )
        self._event_thread.start()

        logger.info("IPC server started")

    def stop(self, timeout: float = 5.0) -> None:
        """
        Stop the IPC server.

        Args:
            timeout: Seconds to wait for threads to stop
        """
        if not self._running:
            return

        logger.info("Stopping IPC server")
        self._running = False

        # Broadcast shutdown event
        try:
            self.broadcast_event(Event.ENGINE_SHUTDOWN, {})
        except Exception as e:
            logger.warning(f"Failed to broadcast shutdown event: {e}")

        # Close all client connections
        with self._clients_lock:
            for client in self._clients:
                with contextlib.suppress(OSError):
                    client.close()
            self._clients.clear()

        # Close server socket
        self._transport.close()

        # Wait for threads
        if self._accept_thread and self._accept_thread.is_alive():
            self._accept_thread.join(timeout=timeout)
        if self._event_thread and self._event_thread.is_alive():
            # Signal event thread to stop
            self._event_queue.put(None)  # type: ignore[arg-type]
            self._event_thread.join(timeout=timeout)

        # Wait for client threads
        for thread in self._client_threads:
            if thread.is_alive():
                thread.join(timeout=1.0)

        logger.info("IPC server stopped")

    def broadcast_event(self, event: Event | str, data: dict[str, Any]) -> None:
        """
        Broadcast an event to all connected clients.

        Args:
            event: Event type
            data: Event data
        """
        message = IPCMessage.create_event(event, data)
        self._event_queue.put(message)

    def _accept_loop(self) -> None:
        """Accept incoming client connections."""
        while self._running:
            try:
                client_sock, client_addr = self._transport.accept_client()
                logger.info(f"Client connected: {client_addr}")

                # Track client
                with self._clients_lock:
                    self._clients.append(client_sock)

                # Start client handler thread
                client_thread = threading.Thread(
                    target=self._client_handler,
                    args=(client_sock,),
                    name=f"ipc-client-{client_addr}",
                    daemon=True,
                )
                client_thread.start()
                self._client_threads.append(client_thread)

            except IPCTimeoutError:
                # Normal timeout, continue accepting
                continue
            except IPCConnectionError as e:
                if self._running:
                    logger.error(f"Accept error: {e}")
                break
            except Exception as e:
                if self._running:
                    logger.exception(f"Unexpected accept error: {e}")
                break

    def _client_handler(self, client_sock: socket.socket) -> None:
        """
        Handle a single client connection.

        Receives messages, validates, dispatches, and sends responses.

        Args:
            client_sock: Client socket
        """
        try:
            while self._running:
                # Receive message
                try:
                    message = self._transport.recv_message(client_sock)
                    if message is None:
                        # Connection closed
                        logger.info("Client disconnected")
                        break
                except IPCTimeoutError:
                    # Timeout, check if still running
                    continue
                except IPCConnectionError:
                    logger.info("Client connection lost")
                    break

                # Process message
                response = self._process_message(message)

                # Send response
                try:
                    self._transport.send_message(client_sock, response)
                except IPCConnectionError:
                    logger.warning("Failed to send response, client disconnected")
                    break

        except Exception as e:
            logger.exception(f"Error in client handler: {e}")
        finally:
            # Cleanup
            with self._clients_lock:
                if client_sock in self._clients:
                    self._clients.remove(client_sock)
            with contextlib.suppress(OSError):
                client_sock.close()

    def _process_message(self, message: IPCMessage) -> IPCMessage:
        """
        Process an incoming message.

        Validates, dispatches, and returns response.

        Args:
            message: Incoming message

        Returns:
            Response message
        """
        # Validate message security
        try:
            self._validator.validate_message(message)
        except Exception as e:
            logger.warning(f"Message validation failed: {e}")
            return IPCMessage.create_response(
                request_id=message.id,
                command=message.command or "UNKNOWN",
                status="error",  # type: ignore[arg-type]
                error={
                    "code": IPCErrorCode.VALIDATION_FAILED.value,
                    "message": str(e),
                },
            )

        # Dispatch to handler
        return self._dispatcher.dispatch(message)

    def _event_loop(self) -> None:
        """Broadcast events to all connected clients."""
        while self._running:
            try:
                # Get next event with timeout
                try:
                    event = self._event_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                if event is None:
                    # Shutdown signal
                    break

                # Broadcast to all clients
                with self._clients_lock:
                    disconnected: list[socket.socket] = []
                    for client in self._clients:
                        try:
                            self._transport.send_message(client, event)
                        except (IPCConnectionError, OSError):
                            disconnected.append(client)

                    # Remove disconnected clients
                    for client in disconnected:
                        self._clients.remove(client)

            except Exception as e:
                if self._running:
                    logger.exception(f"Error in event loop: {e}")

    def __enter__(self) -> IPCServer:
        """Context manager entry - start server."""
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit - stop server."""
        self.stop()


def create_engine_server(
    socket_path: str | Path | None = None,
) -> IPCServer:
    """
    Create an IPC server configured for the Engine process.

    Args:
        socket_path: Optional custom socket path

    Returns:
        Configured IPCServer ready to start
    """
    return IPCServer(socket_path=socket_path)

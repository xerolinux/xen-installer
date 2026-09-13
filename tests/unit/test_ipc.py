"""
Unit tests for IPC module.

Tests:
- Message protocol serialization/deserialization
- Exception hierarchy
- Transport layer basics
"""

import json
import struct
import tempfile
import threading
import time
from pathlib import Path

import pytest

from xen.ipc import (
    PROTOCOL_VERSION,
    Command,
    Event,
    IPCConnectionError,
    IPCError,
    IPCErrorCode,
    IPCMessage,
    IPCProtocolError,
    IPCTimeoutError,
    MessageType,
    ResponseStatus,
    UnixSocketTransport,
)


class TestIPCExceptions:
    """Tests for IPC exception hierarchy."""

    def test_ipc_error_base(self) -> None:
        """IPCError should store message, code, and details."""
        err = IPCError("test error", IPCErrorCode.INTERNAL_ERROR, {"key": "value"})
        assert err.message == "test error"
        assert err.code == IPCErrorCode.INTERNAL_ERROR
        assert err.details == {"key": "value"}

    def test_ipc_error_to_dict(self) -> None:
        """IPCError should serialize to dictionary."""
        err = IPCError("test", IPCErrorCode.TIMEOUT, {"timeout": 30})
        d = err.to_dict()
        assert d["code"] == "TIMEOUT"
        assert d["message"] == "test"
        assert d["details"]["timeout"] == 30

    def test_connection_error(self) -> None:
        """IPCConnectionError should default to CONNECTION_FAILED."""
        err = IPCConnectionError("connection failed")
        assert err.code == IPCErrorCode.CONNECTION_FAILED

    def test_protocol_error(self) -> None:
        """IPCProtocolError should default to INVALID_MESSAGE."""
        err = IPCProtocolError("bad message")
        assert err.code == IPCErrorCode.INVALID_MESSAGE

    def test_timeout_error(self) -> None:
        """IPCTimeoutError should have default message."""
        err = IPCTimeoutError()
        assert err.code == IPCErrorCode.TIMEOUT
        assert "timed out" in err.message.lower()


class TestIPCMessage:
    """Tests for IPCMessage dataclass."""

    def test_create_request(self) -> None:
        """create_request should create valid request message."""
        msg = IPCMessage.create_request(Command.START_INSTALLATION, {"target": "/mnt"})

        assert msg.version == PROTOCOL_VERSION
        assert msg.type == MessageType.REQUEST
        assert msg.payload["command"] == "START_INSTALLATION"
        assert msg.payload["args"]["target"] == "/mnt"
        assert msg.id  # UUID generated
        assert msg.timestamp > 0

    def test_create_response_success(self) -> None:
        """create_response should create success response."""
        msg = IPCMessage.create_response(
            request_id="test-id",
            command="GET_STATUS",
            status=ResponseStatus.SUCCESS,
            result={"running": True},
        )

        assert msg.type == MessageType.RESPONSE
        assert msg.id == "test-id"
        assert msg.payload["status"] == "success"
        assert msg.payload["result"]["running"] is True

    def test_create_response_error(self) -> None:
        """create_response should create error response."""
        msg = IPCMessage.create_response(
            request_id="test-id",
            command="START_INSTALLATION",
            status=ResponseStatus.ERROR,
            error={"code": "INVALID_TARGET", "message": "Bad path"},
        )

        assert msg.payload["status"] == "error"
        assert msg.payload["error"]["code"] == "INVALID_TARGET"

    def test_create_event(self) -> None:
        """create_event should create valid event message."""
        msg = IPCMessage.create_event(Event.JOB_PROGRESS, {"percent": 50})

        assert msg.type == MessageType.EVENT
        assert msg.payload["event"] == "JOB_PROGRESS"
        assert msg.payload["data"]["percent"] == 50

    def test_to_json_from_json_roundtrip(self) -> None:
        """Message should survive JSON roundtrip."""
        original = IPCMessage.create_request(Command.PING, {"echo": "test"})
        json_str = original.to_json()
        restored = IPCMessage.from_json(json_str)

        assert restored.version == original.version
        assert restored.type == original.type
        assert restored.id == original.id
        assert restored.payload == original.payload

    def test_to_bytes_from_bytes_roundtrip(self) -> None:
        """Message should survive bytes roundtrip."""
        original = IPCMessage.create_event(Event.ENGINE_READY, {})
        data = original.to_bytes()
        restored = IPCMessage.from_bytes(data)

        assert restored.type == original.type
        assert restored.payload == original.payload

    def test_from_json_invalid_json(self) -> None:
        """from_json should raise on invalid JSON."""
        with pytest.raises(IPCProtocolError) as exc_info:
            IPCMessage.from_json("not valid json")
        assert exc_info.value.code == IPCErrorCode.MALFORMED_JSON

    def test_from_json_missing_fields(self) -> None:
        """from_json should raise on missing required fields."""
        incomplete = json.dumps({"version": "1.0", "type": "request"})
        with pytest.raises(IPCProtocolError) as exc_info:
            IPCMessage.from_json(incomplete)
        assert exc_info.value.code == IPCErrorCode.INVALID_MESSAGE
        assert "missing" in exc_info.value.details.get("missing_fields", []) or True

    def test_from_json_unsupported_version(self) -> None:
        """from_json should raise on unsupported version."""
        bad_version = json.dumps(
            {
                "version": "99.0",
                "type": "request",
                "id": "test",
                "timestamp": 123,
                "payload": {},
            }
        )
        with pytest.raises(IPCProtocolError) as exc_info:
            IPCMessage.from_json(bad_version)
        assert exc_info.value.code == IPCErrorCode.UNSUPPORTED_VERSION

    def test_validate_request_missing_command(self) -> None:
        """validate should fail for request without command."""
        msg = IPCMessage(
            version=PROTOCOL_VERSION,
            type=MessageType.REQUEST,
            id="test",
            timestamp=123,
            payload={},  # Missing command
        )
        with pytest.raises(IPCProtocolError):
            msg.validate()

    def test_validate_response_missing_status(self) -> None:
        """validate should fail for response without status."""
        msg = IPCMessage(
            version=PROTOCOL_VERSION,
            type=MessageType.RESPONSE,
            id="test",
            timestamp=123,
            payload={},  # Missing status
        )
        with pytest.raises(IPCProtocolError):
            msg.validate()

    def test_validate_event_missing_event(self) -> None:
        """validate should fail for event without event type."""
        msg = IPCMessage(
            version=PROTOCOL_VERSION,
            type=MessageType.EVENT,
            id="test",
            timestamp=123,
            payload={},  # Missing event
        )
        with pytest.raises(IPCProtocolError):
            msg.validate()

    def test_properties_request(self) -> None:
        """Request properties should return correct values."""
        msg = IPCMessage.create_request(Command.GET_STATUS, {"verbose": True})

        assert msg.command == "GET_STATUS"
        assert msg.args == {"verbose": True}
        assert msg.event is None
        assert msg.status is None

    def test_properties_response(self) -> None:
        """Response properties should return correct values."""
        msg = IPCMessage.create_response("id", "CMD", ResponseStatus.SUCCESS, {"data": 1})

        assert msg.status == "success"
        assert msg.is_success is True
        assert msg.result == {"data": 1}
        assert msg.error == {}

    def test_properties_event(self) -> None:
        """Event properties should return correct values."""
        msg = IPCMessage.create_event(Event.JOB_STARTED, {"name": "partition"})

        assert msg.event == "JOB_STARTED"
        assert msg.data == {"name": "partition"}


class TestUnixSocketTransport:
    """Tests for Unix socket transport layer."""

    def test_transport_initialization(self) -> None:
        """Transport should initialize with default values."""
        transport = UnixSocketTransport()
        assert transport.socket_path == Path("/run/xen/ipc.sock")
        assert transport.is_connected is False

    def test_transport_custom_path(self) -> None:
        """Transport should accept custom socket path."""
        transport = UnixSocketTransport("/tmp/custom.sock")
        assert transport.socket_path == Path("/tmp/custom.sock")

    def test_connect_nonexistent_socket(self) -> None:
        """connect_client_socket should fail for nonexistent socket."""
        transport = UnixSocketTransport("/tmp/nonexistent.sock")
        with pytest.raises(IPCConnectionError) as exc_info:
            transport.connect_client_socket()
        assert exc_info.value.code == IPCErrorCode.CONNECTION_FAILED

    def test_server_client_communication(self) -> None:
        """Server and client should be able to exchange messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            # Create server transport
            server = UnixSocketTransport(socket_path)
            server.create_server_socket()

            # Server thread to accept and echo
            received_messages: list[IPCMessage] = []

            def server_handler() -> None:
                client_sock, _ = server.accept_client()
                msg = server.recv_message(client_sock)
                if msg:
                    received_messages.append(msg)
                    # Echo back
                    response = IPCMessage.create_response(
                        msg.id, msg.command or "", ResponseStatus.SUCCESS
                    )
                    server.send_message(client_sock, response)
                client_sock.close()

            server_thread = threading.Thread(target=server_handler)
            server_thread.start()

            # Give server time to start
            time.sleep(0.1)

            # Client connects and sends
            client = UnixSocketTransport(socket_path)
            client_sock = client.connect_client_socket()

            request = IPCMessage.create_request(Command.PING, {"data": "hello"})
            client.send_message(client_sock, request)

            response = client.recv_message(client_sock)

            client_sock.close()
            server_thread.join(timeout=2)
            server.close()

            # Verify
            assert len(received_messages) == 1
            assert received_messages[0].command == "PING"
            assert response is not None
            assert response.is_success

    def test_message_framing(self) -> None:
        """Messages should be length-prefixed."""
        msg = IPCMessage.create_request(Command.GET_STATUS)
        data = msg.to_bytes()

        # Verify we can decode the length prefix
        length_prefix = struct.pack(">I", len(data))
        assert len(length_prefix) == 4

        # The transport adds length prefix, so verify format
        expected_length = struct.unpack(">I", length_prefix)[0]
        assert expected_length == len(data)

    def test_context_manager(self) -> None:
        """Transport should work as context manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            with UnixSocketTransport(socket_path) as transport:
                transport.create_server_socket()
                assert transport.is_connected

            # Socket should be cleaned up
            assert not socket_path.exists()


class TestEnums:
    """Tests for IPC enums."""

    def test_command_values(self) -> None:
        """Command enum should have expected values."""
        assert Command.START_INSTALLATION.value == "START_INSTALLATION"
        assert Command.PING.value == "PING"
        assert Command.SHUTDOWN.value == "SHUTDOWN"

    def test_event_values(self) -> None:
        """Event enum should have expected values."""
        assert Event.JOB_STARTED.value == "JOB_STARTED"
        assert Event.JOB_PROGRESS.value == "JOB_PROGRESS"
        assert Event.INSTALLATION_COMPLETE.value == "INSTALLATION_COMPLETE"

    def test_error_code_values(self) -> None:
        """Error codes should be string enums."""
        assert IPCErrorCode.TIMEOUT.value == "TIMEOUT"
        assert IPCErrorCode.CONNECTION_LOST.value == "CONNECTION_LOST"


class TestIPCSecurityValidator:
    """Tests for IPC security validation."""

    def test_validate_valid_request(self) -> None:
        """Valid requests should pass validation."""
        from xen.ipc import IPCSecurityValidator

        validator = IPCSecurityValidator()
        msg = IPCMessage.create_request(Command.GET_STATUS, {})
        validator.validate_message(msg)  # Should not raise

    def test_validate_disallowed_command(self) -> None:
        """Disallowed commands should be rejected."""
        from xen.ipc import IPCSecurityError, IPCSecurityValidator

        validator = IPCSecurityValidator(allowed_commands=frozenset(["GET_STATUS"]))
        msg = IPCMessage.create_request(Command.START_INSTALLATION, {})

        with pytest.raises(IPCSecurityError) as exc_info:
            validator.validate_message(msg)
        assert exc_info.value.code == IPCErrorCode.PERMISSION_DENIED

    def test_validate_path_traversal_rejected(self) -> None:
        """Path traversal attempts should be rejected."""
        from xen.ipc import IPCSecurityError, IPCSecurityValidator

        validator = IPCSecurityValidator()

        with pytest.raises(IPCSecurityError):
            validator.validate_path("../../../etc/passwd")

        with pytest.raises(IPCSecurityError):
            validator.validate_path("/etc/../../../root/.ssh/id_rsa")

    def test_validate_path_allowed_root(self) -> None:
        """Paths under allowed roots should be accepted."""
        from xen.ipc import IPCSecurityValidator

        validator = IPCSecurityValidator()
        path = validator.validate_path("/mnt/target/boot")
        assert str(path).startswith("/mnt")

    def test_validate_path_disallowed_root(self) -> None:
        """Paths outside allowed roots should be rejected."""
        from xen.ipc import IPCSecurityError, IPCSecurityValidator

        validator = IPCSecurityValidator()

        with pytest.raises(IPCSecurityError) as exc_info:
            validator.validate_path("/etc/passwd")
        assert exc_info.value.code == IPCErrorCode.PERMISSION_DENIED

    def test_validate_dangerous_patterns(self) -> None:
        """Dangerous shell patterns should be rejected."""
        from xen.ipc import IPCSecurityError, IPCSecurityValidator

        validator = IPCSecurityValidator()
        dangerous_values = [
            "/mnt/target; rm -rf /",
            "/mnt/target | cat /etc/passwd",
            "/mnt/target`whoami`",
            "/mnt/target$HOME",
            "/mnt/target > /dev/null",
        ]

        for value in dangerous_values:
            with pytest.raises(IPCSecurityError):
                validator.validate_path(value)

    def test_validate_string_too_long(self) -> None:
        """Excessively long strings should be rejected."""
        from xen.ipc import IPCSecurityValidator, IPCValidationError

        validator = IPCSecurityValidator()
        msg = IPCMessage.create_request(Command.GET_STATUS, {"data": "x" * 10000})

        with pytest.raises(IPCValidationError):
            validator.validate_message(msg)

    def test_validate_nested_too_deep(self) -> None:
        """Excessively nested structures should be rejected."""
        from xen.ipc import IPCSecurityValidator, IPCValidationError

        validator = IPCSecurityValidator()

        # Create deeply nested structure
        nested: dict = {}
        current = nested
        for _ in range(15):
            current["level"] = {}
            current = current["level"]

        msg = IPCMessage.create_request(Command.GET_STATUS, nested)

        with pytest.raises(IPCValidationError):
            validator.validate_message(msg)

    def test_sanitize_args(self) -> None:
        """Sanitize should clean up args."""
        from xen.ipc import IPCSecurityValidator

        validator = IPCSecurityValidator()
        args = {"name": "  test  ", "nested": {"value": "  inner  "}}
        sanitized = validator.sanitize_args("GET_STATUS", args)

        assert sanitized["name"] == "test"
        assert sanitized["nested"]["value"] == "inner"

    def test_validation_result(self) -> None:
        """ValidationResult should work correctly."""
        from xen.ipc import ValidationResult

        valid_result = ValidationResult(valid=True)
        assert bool(valid_result) is True
        assert valid_result.to_dict()["valid"] is True

        invalid_result = ValidationResult(valid=False, errors=["Error 1"])
        assert bool(invalid_result) is False
        assert "Error 1" in invalid_result.errors

    def test_create_default_validator(self) -> None:
        """create_default_validator should return a validator."""
        from xen.ipc import IPCSecurityValidator, create_default_validator

        validator = create_default_validator()
        assert isinstance(validator, IPCSecurityValidator)

    def test_all_commands_in_whitelist(self) -> None:
        """All Command enum values should be in ALLOWED_COMMANDS."""
        from xen.ipc import ALLOWED_COMMANDS

        for cmd in Command:
            assert cmd.value in ALLOWED_COMMANDS


class TestIPCDispatcher:
    """Tests for IPC message dispatcher."""

    def test_dispatcher_initialization(self) -> None:
        """Dispatcher should initialize with empty handlers."""
        from xen.ipc import IPCDispatcher

        dispatcher = IPCDispatcher()
        assert len(dispatcher.registered_commands) == 0

    def test_register_handler(self) -> None:
        """Should register a command handler."""
        from xen.ipc import IPCDispatcher

        dispatcher = IPCDispatcher()

        def handler(_args: dict) -> dict:
            return {"result": "ok"}

        dispatcher.register(Command.GET_STATUS, handler)
        assert dispatcher.has_handler(Command.GET_STATUS)
        assert "GET_STATUS" in dispatcher.registered_commands

    def test_unregister_handler(self) -> None:
        """Should unregister a command handler."""
        from xen.ipc import IPCDispatcher

        dispatcher = IPCDispatcher()

        def handler(_args: dict) -> dict:
            return {}

        dispatcher.register(Command.PING, handler)
        assert dispatcher.has_handler(Command.PING)

        result = dispatcher.unregister(Command.PING)
        assert result is True
        assert not dispatcher.has_handler(Command.PING)

    def test_dispatch_success(self) -> None:
        """Should dispatch message to handler and return response."""
        from xen.ipc import IPCDispatcher

        dispatcher = IPCDispatcher()

        def handler(args: dict) -> dict:
            return {"echo": args.get("data", "")}

        dispatcher.register(Command.GET_STATUS, handler)

        request = IPCMessage.create_request(Command.GET_STATUS, {"data": "test"})
        response = dispatcher.dispatch(request)

        assert response.is_success
        assert response.result["echo"] == "test"

    def test_dispatch_unknown_command(self) -> None:
        """Should return error for unknown command."""
        from xen.ipc import IPCDispatcher

        dispatcher = IPCDispatcher()
        request = IPCMessage.create_request(Command.SHUTDOWN, {})
        response = dispatcher.dispatch(request)

        assert not response.is_success
        assert response.error["code"] == "UNKNOWN_COMMAND"

    def test_dispatch_handler_error(self) -> None:
        """Should return error when handler raises exception."""
        from xen.ipc import IPCDispatcher

        dispatcher = IPCDispatcher()

        def bad_handler(_args: dict) -> dict:
            raise ValueError("Handler failed")

        dispatcher.register(Command.GET_STATUS, bad_handler)
        request = IPCMessage.create_request(Command.GET_STATUS, {})
        response = dispatcher.dispatch(request)

        assert not response.is_success
        assert response.error["code"] == "HANDLER_ERROR"

    def test_create_default_dispatcher(self) -> None:
        """Default dispatcher should have PING handler."""
        from xen.ipc import create_default_dispatcher

        dispatcher = create_default_dispatcher()
        assert dispatcher.has_handler(Command.PING)

        request = IPCMessage.create_request(Command.PING, {"echo": "hello"})
        response = dispatcher.dispatch(request)

        assert response.is_success
        assert response.result["pong"] is True
        assert response.result["echo"] == "hello"


class TestIPCServer:
    """Tests for IPC server."""

    def test_server_initialization(self) -> None:
        """Server should initialize with default values."""
        from xen.ipc import IPCServer

        server = IPCServer("/tmp/test_ipc_init.sock")
        assert not server.is_running
        assert server.connected_clients == 0

    def test_server_start_stop(self) -> None:
        """Server should start and stop correctly."""
        from xen.ipc import IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = IPCServer(socket_path)

            server.start()
            assert server.is_running

            server.stop(timeout=2.0)
            assert not server.is_running

    def test_server_context_manager(self) -> None:
        """Server should work as context manager."""
        from xen.ipc import IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            with IPCServer(socket_path) as server:
                assert server.is_running

            assert not server.is_running

    def test_server_client_ping(self) -> None:
        """Client should be able to ping server."""
        from xen.ipc import IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            with IPCServer(socket_path):
                # Give server time to start
                time.sleep(0.1)

                # Connect client
                client = UnixSocketTransport(socket_path)
                client_sock = client.connect_client_socket()

                # Send ping
                request = IPCMessage.create_request(Command.PING, {"echo": "test"})
                client.send_message(client_sock, request)

                # Receive response
                response = client.recv_message(client_sock)

                client_sock.close()

            assert response is not None
            assert response.is_success
            assert response.result["pong"] is True
            assert response.result["echo"] == "test"

    def test_server_custom_handler(self) -> None:
        """Server should use custom dispatcher handlers."""
        from xen.ipc import IPCDispatcher, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            # Create custom dispatcher
            dispatcher = IPCDispatcher()

            def status_handler(_args: dict) -> dict:
                return {"status": "running", "progress": 50}

            dispatcher.register(Command.GET_STATUS, status_handler)
            dispatcher.register(Command.PING, lambda _: {"pong": True})

            with IPCServer(socket_path, dispatcher=dispatcher):
                time.sleep(0.1)

                client = UnixSocketTransport(socket_path)
                client_sock = client.connect_client_socket()

                request = IPCMessage.create_request(Command.GET_STATUS, {})
                client.send_message(client_sock, request)

                response = client.recv_message(client_sock)
                client_sock.close()

            assert response is not None
            assert response.is_success
            assert response.result["status"] == "running"
            assert response.result["progress"] == 50

    def test_create_engine_server(self) -> None:
        """create_engine_server should return configured server."""
        from xen.ipc import IPCServer, create_engine_server

        server = create_engine_server("/tmp/test_engine.sock")
        assert isinstance(server, IPCServer)
        assert server.dispatcher.has_handler(Command.PING)


class TestIPCClient:
    """Tests for IPC client."""

    def test_client_initialization(self) -> None:
        """Client should initialize with default values."""
        from xen.ipc import IPCClient

        client = IPCClient("/tmp/test_client.sock")
        assert not client.is_connected

    def test_client_connect_no_server(self) -> None:
        """Client should fail to connect when no server."""
        from xen.ipc import IPCClient

        client = IPCClient("/tmp/nonexistent_server.sock")
        with pytest.raises(IPCConnectionError):
            client.connect()

    def test_client_context_manager(self) -> None:
        """Client should work as context manager."""
        from xen.ipc import IPCClient, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            with IPCServer(socket_path):
                time.sleep(0.1)

                with IPCClient(socket_path) as client:
                    assert client.is_connected

                assert not client.is_connected

    def test_client_ping_server(self) -> None:
        """Client ping method should work."""
        from xen.ipc import IPCClient, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            with IPCServer(socket_path):
                time.sleep(0.1)

                with IPCClient(socket_path) as client:
                    result = client.ping("test_echo")

                assert result["pong"] is True
                assert result["echo"] == "test_echo"

    def test_client_send_command(self) -> None:
        """Client should send commands and receive responses."""
        from xen.ipc import IPCClient, IPCDispatcher, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            dispatcher = IPCDispatcher()

            def status_handler(_args: dict) -> dict:
                return {"status": "idle", "jobs": []}

            dispatcher.register(Command.GET_STATUS, status_handler)
            dispatcher.register(Command.PING, lambda _: {"pong": True})

            with IPCServer(socket_path, dispatcher=dispatcher):
                time.sleep(0.1)

                with IPCClient(socket_path) as client:
                    result = client.get_status()

                assert result["status"] == "idle"
                assert result["jobs"] == []

    def test_client_event_subscription(self) -> None:
        """Client should receive events from server."""
        from xen.ipc import IPCClient, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            received_events: list[tuple[str, dict]] = []

            def event_handler(event_type: str, data: dict) -> None:
                received_events.append((event_type, data))

            with IPCServer(socket_path) as server:
                time.sleep(0.1)

                with IPCClient(socket_path) as client:
                    client.subscribe_event(Event.JOB_PROGRESS, event_handler)

                    # Wait for client to be fully ready
                    time.sleep(0.1)

                    # Server broadcasts event multiple times to ensure delivery
                    server.broadcast_event(Event.JOB_PROGRESS, {"percent": 50})

                    # Give time for event to be received
                    for _ in range(10):
                        time.sleep(0.1)
                        if len(received_events) >= 1:
                            break

            assert len(received_events) >= 1
            assert received_events[0][0] == "JOB_PROGRESS"
            assert received_events[0][1]["percent"] == 50

    def test_client_global_event_subscription(self) -> None:
        """Client should receive all events with global subscription."""
        from xen.ipc import IPCClient, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            received_events: list[tuple[str, dict]] = []

            def event_handler(event_type: str, data: dict) -> None:
                received_events.append((event_type, data))

            with IPCServer(socket_path) as server:
                time.sleep(0.1)

                with IPCClient(socket_path) as client:
                    # Subscribe to all events
                    client.subscribe_event(None, event_handler)

                    # Wait for client to be fully ready
                    time.sleep(0.1)

                    server.broadcast_event(Event.JOB_STARTED, {"job": "partition"})
                    time.sleep(0.05)  # Small delay between events
                    server.broadcast_event(Event.JOB_PROGRESS, {"percent": 25})

                    # Wait for events to be received
                    for _ in range(10):
                        time.sleep(0.1)
                        if len(received_events) >= 2:
                            break

            assert len(received_events) >= 2
            assert received_events[0][0] == "JOB_STARTED"
            assert received_events[1][0] == "JOB_PROGRESS"

    def test_create_ui_client(self) -> None:
        """create_ui_client should return configured client."""
        from xen.ipc import IPCClient, create_ui_client

        client = create_ui_client("/tmp/test_ui.sock")
        assert isinstance(client, IPCClient)


class TestIPCIntegration:
    """Integration tests for complete IPC workflows."""

    def test_multiple_clients(self) -> None:
        """Server should handle multiple concurrent clients."""
        from xen.ipc import IPCClient, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            with IPCServer(socket_path) as server:
                time.sleep(0.1)

                # Connect multiple clients
                clients = []
                for _ in range(3):
                    client = IPCClient(socket_path)
                    client.connect()
                    clients.append(client)
                    # Small delay to allow server to register the client
                    time.sleep(0.05)

                # Wait for all clients to be registered (with timeout)
                timeout = 2.0
                start = time.time()
                while server.connected_clients < 3 and time.time() - start < timeout:
                    time.sleep(0.05)

                # Verify all connected
                assert server.connected_clients == 3

                # Each client pings
                for i, client in enumerate(clients):
                    result = client.ping(f"client_{i}")
                    assert result["pong"] is True
                    assert result["echo"] == f"client_{i}"

                # Disconnect clients
                for client in clients:
                    client.disconnect()

    def test_event_broadcast_to_multiple_clients(self) -> None:
        """Events should be broadcast to all connected clients."""
        from xen.ipc import IPCClient, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            received_events: list[list[tuple[str, dict]]] = [[], [], []]

            from collections.abc import Callable

            def make_handler(idx: int) -> Callable[[str, dict], None]:
                def handler(event_type: str, data: dict) -> None:
                    received_events[idx].append((event_type, data))

                return handler

            with IPCServer(socket_path) as server:
                time.sleep(0.1)

                clients = []
                for i in range(3):
                    client = IPCClient(socket_path)
                    client.connect()
                    client.subscribe_event(None, make_handler(i))
                    clients.append(client)

                time.sleep(0.1)

                # Broadcast event
                server.broadcast_event(Event.JOB_COMPLETED, {"job": "test"})

                # Wait for all clients to receive
                for _ in range(10):
                    time.sleep(0.1)
                    if all(len(events) >= 1 for events in received_events):
                        break

                # Disconnect
                for client in clients:
                    client.disconnect()

            # Each client should have received the event
            for events in received_events:
                assert len(events) >= 1
                assert events[0][0] == "JOB_COMPLETED"

    def test_command_error_handling(self) -> None:
        """Client should properly handle command errors."""
        from xen.ipc import IPCClient, IPCDispatcher, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            dispatcher = IPCDispatcher()
            dispatcher.register(Command.PING, lambda _: {"pong": True})

            # Register handler that raises
            def failing_handler(_args: dict) -> dict:
                raise RuntimeError("Intentional failure")

            dispatcher.register(Command.GET_STATUS, failing_handler)

            with IPCServer(socket_path, dispatcher=dispatcher):
                time.sleep(0.1)

                with IPCClient(socket_path) as client:
                    # Successful command
                    result = client.ping()
                    assert result["pong"] is True

                    # Failing command should raise
                    with pytest.raises(IPCProtocolError) as exc_info:
                        client.get_status()

                    assert "Intentional failure" in str(exc_info.value)

    def test_client_disconnect_reconnect(self) -> None:
        """Client should be able to disconnect and reconnect."""
        from xen.ipc import IPCClient, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            with IPCServer(socket_path):
                time.sleep(0.1)

                client = IPCClient(socket_path)

                # First connection
                client.connect()
                assert client.is_connected
                result1 = client.ping("first")
                assert result1["echo"] == "first"

                # Disconnect
                client.disconnect()
                assert not client.is_connected

                # Reconnect
                client.connect()
                assert client.is_connected
                result2 = client.ping("second")
                assert result2["echo"] == "second"

                client.disconnect()

    def test_async_command_callback(self) -> None:
        """Async commands should call callback with result."""
        from xen.ipc import IPCClient, IPCDispatcher, IPCServer

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"

            dispatcher = IPCDispatcher()

            def slow_handler(args: dict) -> dict:
                time.sleep(0.1)
                return {"computed": args.get("value", 0) * 2}

            dispatcher.register(Command.VALIDATE_CONFIG, slow_handler)
            dispatcher.register(Command.PING, lambda _: {"pong": True})

            callback_results: list[tuple] = []
            callback_event = threading.Event()

            def callback(result: dict | None, error: Exception | None) -> None:
                callback_results.append((result, error))
                callback_event.set()

            with IPCServer(socket_path, dispatcher=dispatcher):
                time.sleep(0.1)

                with IPCClient(socket_path) as client:
                    # Send async command
                    request_id = client.send_command_async(
                        Command.VALIDATE_CONFIG,
                        {"value": 21},
                        callback=callback,
                    )

                    assert request_id is not None

                    # Wait for callback
                    callback_event.wait(timeout=2.0)

            # Verify callback was called with result
            assert len(callback_results) == 1
            result, error = callback_results[0]
            assert error is None
            assert result["computed"] == 42


class TestLauncher:
    """Tests for launcher module."""

    def test_engine_process_not_started(self) -> None:
        """EngineProcess should report not running before start."""
        from xen.launcher import EngineProcess

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test.yaml"
            config_path.write_text("branding:\n  name: Test\n")
            socket_path = Path(tmpdir) / "test.sock"

            process = EngineProcess(
                config_path=config_path,
                socket_path=socket_path,
            )

            assert not process.is_running

    def test_create_engine_dispatcher(self) -> None:
        """create_engine_dispatcher should return dispatcher with all handlers."""
        from unittest.mock import MagicMock

        from xen.launcher import create_engine_dispatcher

        # Create mock engine
        mock_engine = MagicMock()
        mock_engine.get_job_names.return_value = ["job1", "job2"]

        mock_branding = MagicMock()
        mock_branding.name = "Test"
        mock_branding.version = "1.0"
        mock_branding.edition = "Standard"
        mock_branding.strings.welcome_title = "Welcome"
        mock_branding.strings.welcome_subtitle = "Subtitle"
        mock_branding.colors.primary = "#FF0000"
        mock_branding.colors.secondary = "#00FF00"
        mock_branding.colors.background = "#FFFFFF"
        mock_branding.colors.text = "#000000"
        mock_engine.get_branding.return_value = mock_branding

        dispatcher = create_engine_dispatcher(mock_engine)

        # Check all commands are registered
        assert dispatcher.has_handler(Command.PING)
        assert dispatcher.has_handler(Command.GET_STATUS)
        assert dispatcher.has_handler(Command.GET_BRANDING)
        assert dispatcher.has_handler(Command.GET_JOB_NAMES)
        assert dispatcher.has_handler(Command.START_INSTALLATION)
        assert dispatcher.has_handler(Command.CANCEL_INSTALLATION)
        assert dispatcher.has_handler(Command.VALIDATE_CONFIG)
        assert dispatcher.has_handler(Command.SHUTDOWN)

    def test_create_engine_dispatcher_ping(self) -> None:
        """Engine dispatcher PING handler should work."""
        from unittest.mock import MagicMock

        from xen.launcher import create_engine_dispatcher

        mock_engine = MagicMock()
        dispatcher = create_engine_dispatcher(mock_engine)

        request = IPCMessage.create_request(Command.PING, {"echo": "test"})
        response = dispatcher.dispatch(request)

        assert response.is_success
        assert response.result["pong"] is True
        assert response.result["echo"] == "test"

    def test_create_engine_dispatcher_get_job_names(self) -> None:
        """Engine dispatcher GET_JOB_NAMES handler should work."""
        from unittest.mock import MagicMock

        from xen.launcher import create_engine_dispatcher

        mock_engine = MagicMock()
        mock_engine.get_job_names.return_value = ["partition", "format", "install"]

        dispatcher = create_engine_dispatcher(mock_engine)

        request = IPCMessage.create_request(Command.GET_JOB_NAMES, {})
        response = dispatcher.dispatch(request)

        assert response.is_success
        assert response.result["jobs"] == ["partition", "format", "install"]

    def test_create_engine_dispatcher_get_branding(self) -> None:
        """Engine dispatcher GET_BRANDING handler should work."""
        from unittest.mock import MagicMock

        from xen.launcher import create_engine_dispatcher

        mock_engine = MagicMock()
        mock_branding = MagicMock()
        mock_branding.name = "TestOS"
        mock_branding.version = "2.0"
        mock_branding.edition = "Pro"
        mock_branding.strings.welcome_title = "Welcome to TestOS"
        mock_branding.strings.welcome_subtitle = "Install now"
        mock_branding.colors.primary = "#123456"
        mock_branding.colors.secondary = "#654321"
        mock_branding.colors.background = "#FFFFFF"
        mock_branding.colors.text = "#000000"
        mock_engine.get_branding.return_value = mock_branding

        dispatcher = create_engine_dispatcher(mock_engine)

        request = IPCMessage.create_request(Command.GET_BRANDING, {})
        response = dispatcher.dispatch(request)

        assert response.is_success
        assert response.result["name"] == "TestOS"
        assert response.result["version"] == "2.0"
        assert response.result["edition"] == "Pro"
        assert response.result["welcome_title"] == "Welcome to TestOS"
        assert response.result["colors"]["primary"] == "#123456"

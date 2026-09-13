"""
IPC Message Dispatcher.

Routes incoming messages to appropriate command handlers.
Provides a registry pattern for command handlers.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from xen.ipc.exceptions import IPCErrorCode, IPCProtocolError, IPCValidationError
from xen.ipc.protocol import Command, IPCMessage, MessageType, ResponseStatus

logger = logging.getLogger(__name__)

# Type alias for command handlers
# Handler receives args dict and returns result dict
CommandHandler = Callable[[dict[str, Any]], dict[str, Any]]
AsyncCommandHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class IPCDispatcher:
    """
    Dispatch IPC messages to registered command handlers.

    Provides:
    - Command handler registration
    - Message routing
    - Response generation
    - Error handling
    """

    def __init__(self) -> None:
        """Initialize dispatcher with empty handler registry."""
        self._handlers: dict[str, CommandHandler] = {}
        self._async_handlers: dict[str, AsyncCommandHandler] = {}

    def register(self, command: Command | str, handler: CommandHandler) -> None:
        """
        Register a synchronous handler for a command.

        Args:
            command: Command to handle (Command enum or string)
            handler: Function that takes args dict and returns result dict
        """
        cmd = command.value if isinstance(command, Command) else command
        if cmd in self._handlers or cmd in self._async_handlers:
            logger.warning(f"Overwriting existing handler for command: {cmd}")
        self._handlers[cmd] = handler
        logger.debug(f"Registered handler for command: {cmd}")

    def register_async(self, command: Command | str, handler: AsyncCommandHandler) -> None:
        """
        Register an asynchronous handler for a command.

        Args:
            command: Command to handle (Command enum or string)
            handler: Async function that takes args dict and returns result dict
        """
        cmd = command.value if isinstance(command, Command) else command
        if cmd in self._handlers or cmd in self._async_handlers:
            logger.warning(f"Overwriting existing handler for command: {cmd}")
        self._async_handlers[cmd] = handler
        logger.debug(f"Registered async handler for command: {cmd}")

    def unregister(self, command: Command | str) -> bool:
        """
        Unregister a handler for a command.

        Args:
            command: Command to unregister

        Returns:
            True if handler was removed, False if not found
        """
        cmd = command.value if isinstance(command, Command) else command
        if cmd in self._handlers:
            del self._handlers[cmd]
            logger.debug(f"Unregistered handler for command: {cmd}")
            return True
        if cmd in self._async_handlers:
            del self._async_handlers[cmd]
            logger.debug(f"Unregistered async handler for command: {cmd}")
            return True
        return False

    def has_handler(self, command: Command | str) -> bool:
        """Check if a handler is registered for a command."""
        cmd = command.value if isinstance(command, Command) else command
        return cmd in self._handlers or cmd in self._async_handlers

    def is_async_handler(self, command: Command | str) -> bool:
        """Check if the handler for a command is async."""
        cmd = command.value if isinstance(command, Command) else command
        return cmd in self._async_handlers

    def dispatch(self, message: IPCMessage) -> IPCMessage:
        """
        Dispatch a message to its handler and return response.

        Args:
            message: Request message to dispatch

        Returns:
            Response message

        Raises:
            IPCProtocolError: If message is not a request
        """
        if message.type != MessageType.REQUEST:
            raise IPCProtocolError(
                f"Can only dispatch request messages, got: {message.type.value}",
                code=IPCErrorCode.INVALID_MESSAGE,
            )

        command = message.command
        if not command:
            return self._error_response(
                message,
                IPCErrorCode.INVALID_COMMAND,
                "Request missing command",
            )

        # Check for handler
        if command not in self._handlers:
            if command in self._async_handlers:
                return self._error_response(
                    message,
                    IPCErrorCode.INTERNAL_ERROR,
                    f"Handler for {command} is async, use dispatch_async()",
                )
            return self._error_response(
                message,
                IPCErrorCode.UNKNOWN_COMMAND,
                f"No handler registered for command: {command}",
            )

        # Execute handler
        handler = self._handlers[command]
        try:
            result = handler(message.args)
            return IPCMessage.create_response(
                request_id=message.id,
                command=command,
                status=ResponseStatus.SUCCESS,
                result=result,
            )
        except IPCValidationError as e:
            logger.warning(f"Validation error in handler for {command}: {e}")
            return self._error_response(message, e.code, str(e), e.details)
        except Exception as e:
            logger.exception(f"Error in handler for {command}: {e}")
            return self._error_response(
                message,
                IPCErrorCode.HANDLER_ERROR,
                f"Handler error: {e}",
            )

    async def dispatch_async(self, message: IPCMessage) -> IPCMessage:
        """
        Dispatch a message to its async handler and return response.

        Args:
            message: Request message to dispatch

        Returns:
            Response message
        """
        if message.type != MessageType.REQUEST:
            raise IPCProtocolError(
                f"Can only dispatch request messages, got: {message.type.value}",
                code=IPCErrorCode.INVALID_MESSAGE,
            )

        command = message.command
        if not command:
            return self._error_response(
                message,
                IPCErrorCode.INVALID_COMMAND,
                "Request missing command",
            )

        # Check for handler - try async first, then sync
        if command in self._async_handlers:
            handler = self._async_handlers[command]
            try:
                result = await handler(message.args)
                return IPCMessage.create_response(
                    request_id=message.id,
                    command=command,
                    status=ResponseStatus.SUCCESS,
                    result=result,
                )
            except IPCValidationError as e:
                logger.warning(f"Validation error in async handler for {command}: {e}")
                return self._error_response(message, e.code, str(e), e.details)
            except Exception as e:
                logger.exception(f"Error in async handler for {command}: {e}")
                return self._error_response(
                    message,
                    IPCErrorCode.HANDLER_ERROR,
                    f"Handler error: {e}",
                )

        # Fall back to sync handler
        if command in self._handlers:
            return self.dispatch(message)

        return self._error_response(
            message,
            IPCErrorCode.UNKNOWN_COMMAND,
            f"No handler registered for command: {command}",
        )

    def _error_response(
        self,
        message: IPCMessage,
        code: IPCErrorCode,
        error_message: str,
        details: dict[str, Any] | None = None,
    ) -> IPCMessage:
        """Create an error response message."""
        return IPCMessage.create_response(
            request_id=message.id,
            command=message.command or "UNKNOWN",
            status=ResponseStatus.ERROR,
            error={
                "code": code.value,
                "message": error_message,
                "details": details or {},
            },
        )

    @property
    def registered_commands(self) -> list[str]:
        """Get list of registered command names."""
        return list(set(self._handlers.keys()) | set(self._async_handlers.keys()))


def create_default_dispatcher() -> IPCDispatcher:
    """
    Create a dispatcher with default command handlers.

    Returns:
        Configured IPCDispatcher with basic handlers
    """
    dispatcher = IPCDispatcher()

    # Register PING handler
    def ping_handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"pong": True, "echo": args.get("echo", "")}

    dispatcher.register(Command.PING, ping_handler)

    return dispatcher

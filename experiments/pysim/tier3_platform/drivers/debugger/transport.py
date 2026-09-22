"""Replaceable physical transport sinks for the Tier 3 debugger plugin."""

from __future__ import annotations

import socket
from typing import Protocol, TypeAlias


SendBuffer: TypeAlias = bytes | bytearray | memoryview


class DebuggerConnection(Protocol):
    """Minimal bidirectional connection consumed by the debugger plugin."""

    def setblocking(self, flag: bool) -> None: ...

    def recv(self, buffer_size: int) -> bytes: ...

    def send(self, data: SendBuffer) -> int: ...

    def close(self) -> None: ...


class DebuggerSink(Protocol):
    """Physical RSP transport boundary owned by a Tier 3 platform driver."""

    def bind(self) -> int: ...

    def set_nonblocking(self, enabled: bool) -> None: ...

    def accept(self) -> DebuggerConnection | None: ...

    def close(self) -> None: ...


class SocketDebuggerSink:
    """Default host-side TCP transport for GDB RSP sessions."""

    __slots__ = ("_server_sock", "actual_port", "host", "port")

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server_sock: socket.socket | None = None
        self.actual_port = 0

    def bind(self) -> int:
        if self._server_sock is None:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((self.host, self.port))
            server_sock.listen(1)
            self._server_sock = server_sock
            self.actual_port = server_sock.getsockname()[1]
        return self.actual_port

    def set_nonblocking(self, enabled: bool) -> None:
        assert self._server_sock is not None, "Debugger sink must be bound first"
        self._server_sock.setblocking(not enabled)

    def accept(self) -> DebuggerConnection | None:
        assert self._server_sock is not None, "Debugger sink must be bound first"
        try:
            connection, _ = self._server_sock.accept()
        except (BlockingIOError, TimeoutError):
            return None
        return connection

    def close(self) -> None:
        if self._server_sock is not None:
            self._server_sock.close()
            self._server_sock = None

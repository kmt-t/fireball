"""
experiments/pysim/tier3_plugins/debugger/gdb_server.py
GDB Remote Serial Protocol (RSP) server for Fireball Hypervisor.
Provides replaceable physical transport handling, packet frame encoding/decoding,
ACK/NACK negotiation, and execution dispatch to GDBRspProtocol.
"""

from __future__ import annotations

import threading
from collections.abc import Generator, Mapping

from execution_context import WASMContext
from scheduler import ChannelAction
from tier3_platform.drivers.debugger.transport import (
    DebuggerConnection,
    DebuggerSink,
    SocketDebuggerSink,
)
from tier3_plugins.debugger.debugger import DebuggerManager, GDBRspProtocol
from wasm_module import BasicBlock


class GDBServer:
    """RSP server using an injected Tier 3 physical debugger transport."""

    def __init__(
        self,
        dbg: DebuggerManager,
        host: str = "127.0.0.1",
        port: int = 0,
        transport: DebuggerSink | None = None,
    ):
        self.dbg = dbg
        self.rsp = GDBRspProtocol(dbg)
        self.transport = transport if transport is not None else SocketDebuggerSink(host, port)
        self._client_sock: DebuggerConnection | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.actual_port: int = 0

    def bind_socket(self) -> int:
        """Binds the injected debugger transport and returns its endpoint ID."""
        self.actual_port = self.transport.bind()
        self._running = True
        return self.actual_port

    def start(self, current_pc: int, ctx: WASMContext, blocks: Mapping[int, BasicBlock]) -> int:
        """Starts a blocking transport loop in a background thread."""
        port = self.bind_socket()
        self._thread = threading.Thread(
            target=self._server_loop,
            args=(current_pc, ctx, blocks),
            daemon=True,
            name="GDBServerThread",
        )
        self._thread.start()
        return port

    def run_task(
        self, start_pc: int, ctx: WASMContext, blocks: Mapping[int, BasicBlock]
    ) -> Generator[tuple[str, None], None, None]:
        """
        COOS cooperative task coroutine for GDBServer.
        Listens and processes RSP packets asynchronously using a non-blocking sink
        and yields execution back to COOS scheduler when waiting for I/O.
        """
        self.bind_socket()
        self.transport.set_nonblocking(True)
        current_pc = start_pc
        buffer = ""
        tx_buffer = bytearray()

        def _try_flush_tx() -> None:
            if tx_buffer and self._client_sock:
                try:
                    n = self._client_sock.send(tx_buffer)
                    del tx_buffer[:n]
                except (BlockingIOError, TimeoutError):
                    pass

        try:
            # 1. Accept client non-blockingly
            while self._running and self._client_sock is None:
                client = self.transport.accept()
                if client is not None:
                    client.setblocking(False)
                    self._client_sock = client
                    self.dbg.attach()
                    break
                yield (ChannelAction.YIELD, None)

            # 2. Main packet dispatch loop
            while self._running and self._client_sock is not None:
                # Flush any pending outgoing bytes first
                _try_flush_tx()

                try:
                    data = self._client_sock.recv(4096)
                    if not data:
                        break
                    buffer += data.decode("latin1")
                except (BlockingIOError, TimeoutError):
                    _try_flush_tx()
                    yield (ChannelAction.YIELD, None)
                    continue
                except Exception:
                    break

                # Process all complete packets in buffer
                while buffer.find("$") >= 0 and buffer.find("#") >= 0:
                    dollar_idx = buffer.index("$")
                    hash_idx = buffer.find("#", dollar_idx)
                    if hash_idx == -1 or len(buffer) < hash_idx + 3:
                        break
                    packet_str = buffer[dollar_idx : hash_idx + 3]
                    buffer = buffer[hash_idx + 3 :]
                    # Queue immediate ACK
                    tx_buffer.extend(b"+")
                    # Handle packet via GDBRspProtocol
                    response, current_pc = self.rsp.handle_packet(
                        packet_str, current_pc, ctx, blocks
                    )
                    if response:
                        tx_buffer.extend(response.encode("latin1"))

                # Flush outgoing bytes accumulated from packet processing
                _try_flush_tx()
                yield (ChannelAction.YIELD, None)
        finally:
            self.stop()

    def stop(self) -> None:
        """Stops the server and closes the injected connection and transport."""
        self._running = False
        if self._client_sock:
            self._client_sock.close()
            self._client_sock = None
        self.transport.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _server_loop(
        self, start_pc: int, ctx: WASMContext, blocks: Mapping[int, BasicBlock]
    ) -> None:
        """Accepts a client connection and processes RSP packets until disconnected."""
        try:
            self.transport.set_nonblocking(False)
            while self._running:
                client = self.transport.accept()
                if client is not None:
                    client.setblocking(True)
                    self._client_sock = client
                    break
            if not self._running or self._client_sock is None:
                return
            current_pc = start_pc
            self.dbg.attach()
            buffer = ""
            while self._running:
                try:
                    data = self._client_sock.recv(4096)
                    if not data:
                        break
                    buffer += data.decode("latin1")
                except TimeoutError:
                    continue
                except Exception:
                    break
                # Process all complete packets in buffer
                while buffer.find("$") >= 0 and buffer.find("#") >= 0:
                    dollar_idx = buffer.index("$")
                    hash_idx = buffer.find("#", dollar_idx)
                    if hash_idx == -1 or len(buffer) < hash_idx + 3:
                        break  # Need more bytes for checksum
                    packet_str = buffer[dollar_idx : hash_idx + 3]
                    buffer = buffer[hash_idx + 3 :]
                    # Send immediate ACK
                    self._send_all(b"+")
                    # Handle packet via GDBRspProtocol
                    response, current_pc = self.rsp.handle_packet(
                        packet_str, current_pc, ctx, blocks
                    )
                    # Send response packet
                    if response:
                        self._send_all(response.encode("latin1"))
        except Exception:
            pass
        finally:
            if self._client_sock is not None:
                self._client_sock.close()
                self._client_sock = None
            self.transport.close()
            self.dbg.detach()

    def _send_all(self, data: bytes) -> None:
        """Send a complete response through an injected connection sink."""
        assert self._client_sock is not None
        remaining = memoryview(data)
        while remaining:
            sent = self._client_sock.send(remaining)
            assert sent > 0, "Debugger transport returned a zero-byte send"
            remaining = remaining[sent:]

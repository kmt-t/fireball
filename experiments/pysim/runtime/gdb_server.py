"""
experiments/pysim/runtime/gdb_server.py
GDB Remote Serial Protocol (RSP) TCP Server for Fireball Hypervisor.
Provides real TCP socket listening, packet frame encoding/decoding,
ACK/NACK negotiation, and execution dispatch to GDBRspProtocol.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Generator, Mapping

from debugger import DebuggerManager, GDBRspProtocol
from runtime_engine import BasicBlock, WASMContext
from scheduler import ChannelAction


class GDBServer:
    """TCP Server implementing GDB Remote Serial Protocol (RSP) wire interface."""

    def __init__(self, dbg: DebuggerManager, host: str = "127.0.0.1", port: int = 0):
        self.dbg = dbg
        self.rsp = GDBRspProtocol(dbg)
        self.host = host
        self.port = port
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.actual_port: int = 0

    def bind_socket(self) -> int:
        """Binds TCP socket and returns bound port."""
        if self._server_sock is None:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(1)
            self.actual_port = self._server_sock.getsockname()[1]
            self._running = True
        return self.actual_port

    def start(self, current_pc: int, ctx: WASMContext, blocks: Mapping[int, BasicBlock]) -> int:
        """Starts TCP listener in a background thread and returns bound port."""
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
    ) -> Generator[tuple[str, object], None, None]:
        """
        COOS cooperative task coroutine for GDBServer.
        Listens and processes RSP packets asynchronously using non-blocking socket
        and yields execution back to COOS scheduler when waiting for I/O.
        """
        self.bind_socket()
        assert self._server_sock is not None
        self._server_sock.setblocking(False)
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
                try:
                    client, _ = self._server_sock.accept()
                    client.setblocking(False)
                    self._client_sock = client
                    self.dbg.attach()
                    break
                except (BlockingIOError, TimeoutError):
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
                while "$" in buffer and "#" in buffer:
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
        """Stops the TCP server and closes all sockets."""
        self._running = False
        if self._client_sock:
            try:
                self._client_sock.close()
            except Exception:
                pass
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _server_loop(
        self, start_pc: int, ctx: WASMContext, blocks: Mapping[int, BasicBlock]
    ) -> None:
        """Accepts a client connection and processes RSP packets until disconnected."""
        try:
            self._server_sock.settimeout(2.0)
            while self._running:
                try:
                    client, _ = self._server_sock.accept()
                    self._client_sock = client
                    break
                except TimeoutError:
                    continue
            if not self._running or not self._client_sock:
                return
            self._client_sock.settimeout(2.0)
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
                while "$" in buffer and "#" in buffer:
                    dollar_idx = buffer.index("$")
                    hash_idx = buffer.find("#", dollar_idx)
                    if hash_idx == -1 or len(buffer) < hash_idx + 3:
                        break  # Need more bytes for checksum
                    packet_str = buffer[dollar_idx : hash_idx + 3]
                    buffer = buffer[hash_idx + 3 :]
                    # Send immediate ACK
                    self._client_sock.sendall(b"+")
                    # Handle packet via GDBRspProtocol
                    response, current_pc = self.rsp.handle_packet(
                        packet_str, current_pc, ctx, blocks
                    )
                    # Send response packet
                    if response:
                        self._client_sock.sendall(response.encode("latin1"))
        except Exception:
            pass
        finally:
            if self._client_sock:
                try:
                    self._client_sock.close()
                except Exception:
                    pass
            self.dbg.detach()

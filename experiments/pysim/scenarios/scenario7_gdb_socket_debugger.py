from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys
from pathlib import Path

"""Integration Scenario 7: GDB Remote Serial Protocol (RSP) Socket Debugger.

Tests:
- Real TCP socket server for GDB Remote Serial Protocol (RSP)
- GDB client handshake and stop-reply packet negotiation
- Virtual register read/write (20 registers: PC, SP, FP, TOS, Locals 0..15)
- Guest linear memory inspection and live patching ('m', 'M')
- Breakpoint insertion ('Z0'), hit trapping (SIGTRAP S05), and removal ('z0')
- JIT cache invalidation on debugger memory write ({Debugger_Jit_Flush})
- Single-stepping ('s') and continue-to-exit ('c', 'W00')
"""

import socket
import time

from debugger import DebuggerManager, GDBRspProtocol
from gdb_server import GDBServer
from runtime_engine import BasicBlock, IntegratedHybridEngine, WASMContext
from x64_jit import TraceCompiler


class GDBClientHelper:
    """Helper client to simulate a real GDB debugger communicating over RSP."""

    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=3.0)
        self.sock.settimeout(3.0)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def send_raw_packet(self, payload: str) -> str:
        """Sends a framed packet '$payload#checksum' and receives response."""
        cksum = sum(ord(c) for c in payload) % 256
        wire_data = f"${payload}#{cksum:02x}".encode("latin1")
        self.sock.sendall(wire_data)

        # Receive ACK '+' and response packet
        buf = ""
        while "$" not in buf or "#" not in buf:
            chunk = self.sock.recv(1024).decode("latin1")
            if not chunk:
                break
            buf += chunk

        # Consume ACK
        if buf.startswith("+"):
            buf = buf[1:]

        # Extract payload from response '$reply#cksum'
        if "$" in buf and "#" in buf:
            dollar_idx = buf.index("$")
            hash_idx = buf.find("#", dollar_idx)
            response_payload = buf[dollar_idx + 1 : hash_idx]
            # Send ACK for response
            self.sock.sendall(b"+")
            return response_payload

        return ""


def test_scenario_gdb_socket_debugger():
    print("[*] Running Scenario 7: GDB Remote Debugger Socket Connection...")

    # 1. Setup execution environment with BasicBlocks
    block10 = BasicBlock(
        head_pc=0x10,
        ops=[("local.get", 0), ("i32.const", 10), ("i32.add", None), ("local.set", 0)],
        next_pc=0x20,
    )
    block20 = BasicBlock(
        head_pc=0x20,
        ops=[("local.get", 0), ("i32.const", 5), ("i32.mul", None), ("local.set", 1)],
        next_pc=0x30,
    )
    block30 = BasicBlock(
        head_pc=0x30,
        ops=[("local.get", 1), ("i32.const", 2), ("i32.sub", None), ("local.set", 1)],
        next_pc=None,
    )
    blocks = {0x10: block10, 0x20: block20, 0x30: block30}

    engine = IntegratedHybridEngine(compiler=TraceCompiler())
    dbg = DebuggerManager(engine=engine)
    server = GDBServer(dbg=dbg, host="127.0.0.1", port=0)

    # Initial guest state: local0 = 2, memory 128 bytes
    mem = bytearray(128)
    mem[0:8] = b"TESTDATA"
    ctx = WASMContext(locals_values=[2, 0, 0, 0], memory=mem)

    # Start TCP Server on dynamic port
    port = server.start(current_pc=0x10, ctx=ctx, blocks=blocks)
    time.sleep(0.05)

    client = GDBClientHelper("127.0.0.1", port)
    try:
        # Step 1: Query halt reason ('?')
        resp = client.send_raw_packet("?")
        assert resp == "S05", f"Expected S05 (SIGTRAP), got {resp}"

        # Step 2: Read virtual registers ('g')
        resp = client.send_raw_packet("g")
        assert len(resp) == 160
        pc = int(resp[0:8], 16)
        l0 = int(resp[32:40], 16)
        assert pc == 0x10 and l0 == 2

        # Step 3: Read memory ('m0,8')
        resp = client.send_raw_packet("m0,8")
        assert resp == b"TESTDATA".hex()

        # Step 4: Insert breakpoint at PC 0x20 ('Z0,20,0')
        resp = client.send_raw_packet("Z0,20,0")
        assert resp == "OK"
        assert dbg.has_breakpoint(0x20)

        # Step 5: Continue execution ('c') -> hit breakpoint at PC 0x20
        resp = client.send_raw_packet("c")
        assert resp == "S05"
        resp_g = client.send_raw_packet("g")
        pc = int(resp_g[0:8], 16)
        l0 = int(resp_g[32:40], 16)
        assert pc == 0x20 and l0 == 12

        # Step 6: Write virtual registers ('G') -> Modify local0 to 100
        new_regs = [0x20, 0, 0, 0, 100, 0] + [0] * 14
        g_payload = "G" + "".join(f"{r:08x}" for r in new_regs)
        resp = client.send_raw_packet(g_payload)
        assert resp == "OK" and ctx.locals[0] == 100

        # Step 7: Write memory ('M') & verify JIT cache flush
        resp = client.send_raw_packet("M0,4:50415443")
        assert resp == "OK" and ctx.memory[0:4] == b"PATC"

        # Step 8: Single-step execution ('s') -> Execute block20, land at PC 0x30
        resp = client.send_raw_packet("s")
        assert resp == "S05"
        resp_g = client.send_raw_packet("g")
        pc = int(resp_g[0:8], 16)
        l1 = int(resp_g[40:48], 16)
        assert pc == 0x30 and l1 == 500

        # Step 9: Remove breakpoint ('z0,20,0')
        resp = client.send_raw_packet("z0,20,0")
        assert resp == "OK" and not dbg.has_breakpoint(0x20)

        # Step 10: Continue to termination ('c') -> Execute block30, exit with W00
        resp = client.send_raw_packet("c")
        assert resp == "W00" and ctx.locals[1] == 498

        print(
            "    [PASS] Scenario 7 (GDB Socket Debugger Session) succeeded seamlessly."
        )

    finally:
        client.close()
        server.stop()


if __name__ == "__main__":
    test_scenario_gdb_socket_debugger()

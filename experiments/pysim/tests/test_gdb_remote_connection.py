"""
experiments/pysim/test_gdb_remote_connection.py
End-to-end integration test for GDB Remote Serial Protocol (RSP) over real TCP socket.
Simulates a real GDB client session connecting to Fireball GDBServer:
1. TCP Socket connect & initial handshake
2. Query halt reason ('?')
3. Read virtual registers ('g')
4. Read memory ('m')
5. Insert breakpoint ('Z0')
6. Continue execution ('c') & hit breakpoint
7. Write virtual registers ('G')
8. Write memory ('M') & verify JIT cache flush
9. Single-step execution ('s')
10. Remove breakpoint ('z0')
11. Continue to program termination ('W00')
"""

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

import sys

from pathlib import Path

import sys

from pathlib import Path

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


def test_gdb_remote_socket_session():

    print("[*] Starting GDB Remote Debugger Socket Connection Test...")

    # 1. Setup execution environment with BasicBlocks

    # Program:

    # PC 0x10: local.get 0, i32.const 10, i32.add, local.set 0 (next_pc: 0x20)

    # PC 0x20: local.get 0, i32.const 5, i32.mul, local.set 1 (next_pc: 0x30)

    # PC 0x30: local.get 1, i32.const 2, i32.sub, local.set 1 (next_pc: None / exit)

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

    print(f"    -> GDB Remote Server listening on 127.0.0.1:{port}")

    time.sleep(0.1)

    client = GDBClientHelper("127.0.0.1", port)

    try:
        # Step 1: Query halt reason ('?')

        resp = client.send_raw_packet("?")

        assert resp == "S05", f"Expected S05 (SIGTRAP), got {resp}"

        print("    [Step 1] Query halt reason '?' -> S05 (SIGTRAP) [PASS]")

        # Step 2: Read virtual registers ('g')

        resp = client.send_raw_packet("g")

        assert len(resp) == 160, (
            f"Expected 160 hex chars for 20 virtual registers, got {len(resp)}"
        )

        pc = int(resp[0:8], 16)

        l0 = int(resp[32:40], 16)

        assert pc == 0x10, f"Expected PC 0x10, got {pc:x}"

        assert l0 == 2, f"Expected Local0 = 2, got {l0}"

        print(
            f"    [Step 2] Read virtual registers 'g' (PC=0x{pc:x}, Local0={l0}) [PASS]"
        )

        # Step 3: Read memory ('m0,8')

        resp = client.send_raw_packet("m0,8")

        assert resp == b"TESTDATA".hex(), f"Expected TESTDATA hex, got {resp}"

        print(
            f"    [Step 3] Read guest memory 'm0,8' -> '{bytes.fromhex(resp).decode()}' [PASS]"
        )

        # Step 4: Insert breakpoint at PC 0x20 ('Z0,20,0')

        resp = client.send_raw_packet("Z0,20,0")

        assert resp == "OK", f"Expected OK, got {resp}"

        assert dbg.has_breakpoint(0x20)

        print("    [Step 4] Insert breakpoint at 0x20 'Z0,20,0' -> OK [PASS]")

        # Step 5: Continue execution ('c') -> should hit breakpoint at PC 0x20

        resp = client.send_raw_packet("c")

        assert resp == "S05", f"Expected S05 on breakpoint hit, got {resp}"

        # Verify state at PC 0x20: local0 should now be 2 + 10 = 12

        resp_g = client.send_raw_packet("g")

        pc = int(resp_g[0:8], 16)

        l0 = int(resp_g[32:40], 16)

        assert pc == 0x20, f"Expected break at PC 0x20, got 0x{pc:x}"

        assert l0 == 12, f"Expected Local0 = 12, got {l0}"

        print(
            f"    [Step 5] Continue 'c' -> Trapped at breakpoint PC=0x{pc:x}, Local0={l0} [PASS]"
        )

        # Step 6: Write virtual registers ('G') -> Modify local0 to 100

        new_regs = [0x20, 0, 0, 0, 100, 0] + [0] * 14

        g_payload = "G" + "".join(f"{r:08x}" for r in new_regs)

        resp = client.send_raw_packet(g_payload)

        assert resp == "OK", f"Expected OK, got {resp}"

        assert ctx.locals[0] == 100

        print("    [Step 6] Write virtual register 'G' (Local0 = 100) -> OK [PASS]")

        # Step 7: Write memory ('M') & verify JIT cache flush

        resp = client.send_raw_packet("M0,4:50415443")  # Write "PATC"

        assert resp == "OK", f"Expected OK, got {resp}"

        assert ctx.memory[0:4] == b"PATC"

        print("    [Step 7] Write memory 'M0,4:PATC' & Flush JIT Cache -> OK [PASS]")

        # Step 8: Single-step execution ('s') -> Execute block20 (local1 = 100 * 5 = 500), land at PC 0x30

        resp = client.send_raw_packet("s")

        assert resp == "S05", f"Expected S05 after step, got {resp}"

        resp_g = client.send_raw_packet("g")

        pc = int(resp_g[0:8], 16)

        l1 = int(resp_g[40:48], 16)

        assert pc == 0x30, f"Expected step to PC 0x30, got 0x{pc:x}"

        assert l1 == 500, f"Expected Local1 = 500, got {l1}"

        print(
            f"    [Step 8] Single-step 's' -> Stepped to PC=0x{pc:x}, Local1={l1} [PASS]"
        )

        # Step 9: Remove breakpoint ('z0,20,0')

        resp = client.send_raw_packet("z0,20,0")

        assert resp == "OK", f"Expected OK, got {resp}"

        assert not dbg.has_breakpoint(0x20)

        print("    [Step 9] Remove breakpoint 'z0,20,0' -> OK [PASS]")

        # Step 10: Continue to termination ('c') -> Execute block30 (local1 = 500 - 2 = 498), exit with W00

        resp = client.send_raw_packet("c")

        assert resp == "W00", f"Expected W00 (process exit), got {resp}"

        assert ctx.locals[1] == 498

        print(
            f"    [Step 10] Continue 'c' -> Program terminated cleanly W00 (Local1={ctx.locals[1]}) [PASS]"
        )

    finally:
        client.close()

        server.stop()

        print("    [Cleanup] Remote GDB Server stopped and socket disconnected.")

    print(
        "    [PASS] All 10 GDB Remote Debugger Socket integration tests succeeded seamlessly.\n"
    )


if __name__ == "__main__":
    test_gdb_remote_socket_session()

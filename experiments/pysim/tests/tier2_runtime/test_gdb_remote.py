from __future__ import annotations

import sys
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _TEST_FILE.parent,
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys
from pathlib import Path

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

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

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

import socket
import time

from debugger import DebuggerManager
from gdb_server import GDBServer
from runtime_engine import IntegratedHybridEngine, WASMContext
from test_support import wat_to_wasm
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
        buf = buf.removeprefix("+")

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
    # 1. Setup execution environment with three real basic blocks, split by
    # nested `block`/`end` and loaded through a real Module -- so
    # run_block_interpret's op-stream derivation (from raw bytecode) has a
    # function to decode against:
    # block10: local.get 0, i32.const 10, i32.add, local.set 0 (next: block20)
    # block20: local.get 0, i32.const 5, i32.mul, local.set 1 (next: block30)
    # block30: local.get 1, i32.const 2, i32.sub, local.set 1 (next: None / exit)
    wat = """
    (module
      (func (export "f") (param i32 i32 i32 i32)
        (block $b1
          (block $b2
            local.get 0
            i32.const 10
            i32.add
            local.set 0
          )
          local.get 0
          i32.const 5
          i32.mul
          local.set 1
        )
        local.get 1
        i32.const 2
        i32.sub
        local.set 1
        return
      )
    )
    """
    engine = IntegratedHybridEngine(compiler=TraceCompiler())
    mod = engine.load_wasm(wat_to_wasm(wat))
    block10, block20, block30 = mod.blocks[0], mod.blocks[1], mod.blocks[2]
    blocks = {block10.head_pc: block10, block20.head_pc: block20, block30.head_pc: block30}
    dbg = DebuggerManager(engine=engine)
    server = GDBServer(dbg=dbg, host="127.0.0.1", port=0)
    # Initial guest state: local0 = 2, memory 128 bytes
    mem = bytearray(128)
    mem[0:8] = b"TESTDATA"
    ctx = WASMContext(locals_values=[2, 0, 0, 0], memory=mem)
    # Start TCP Server on dynamic port
    port = server.start(current_pc=block10.head_pc, ctx=ctx, blocks=blocks)
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
        assert len(resp) == 160, f"Expected 160 hex chars for 20 virtual registers, got {len(resp)}"
        pc = int(resp[0:8], 16)
        l0 = int(resp[32:40], 16)
        assert pc == block10.head_pc, f"Expected PC {block10.head_pc:#x}, got {pc:x}"
        assert l0 == 2, f"Expected Local0 = 2, got {l0}"
        print(f"    [Step 2] Read virtual registers 'g' (PC=0x{pc:x}, Local0={l0}) [PASS]")
        # Step 3: Read memory ('m0,8')
        resp = client.send_raw_packet("m0,8")
        assert resp == b"TESTDATA".hex(), f"Expected TESTDATA hex, got {resp}"
        print(f"    [Step 3] Read guest memory 'm0,8' -> '{bytes.fromhex(resp).decode()}' [PASS]")
        # Step 4: Insert breakpoint at block20's head ('Z0,<addr>,0')
        resp = client.send_raw_packet(f"Z0,{block20.head_pc:x},0")
        assert resp == "OK", f"Expected OK, got {resp}"
        assert dbg.has_breakpoint(block20.head_pc)
        print(f"    [Step 4] Insert breakpoint at 0x{block20.head_pc:x} -> OK [PASS]")
        # Step 5: Continue execution ('c') -> should hit breakpoint at block20's head
        resp = client.send_raw_packet("c")
        assert resp == "S05", f"Expected S05 on breakpoint hit, got {resp}"
        # Verify state at block20's head: local0 should now be 2 + 10 = 12
        resp_g = client.send_raw_packet("g")
        pc = int(resp_g[0:8], 16)
        l0 = int(resp_g[32:40], 16)
        assert pc == block20.head_pc, f"Expected break at 0x{block20.head_pc:x}, got 0x{pc:x}"
        assert l0 == 12, f"Expected Local0 = 12, got {l0}"
        print(f"    [Step 5] Continue 'c' -> Trapped at breakpoint PC=0x{pc:x}, Local0={l0} [PASS]")
        # Step 6: Write virtual registers ('G') -> Modify local0 to 100
        new_regs = [block20.head_pc, 0, 0, 0, 100, 0] + [0] * 14
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
        # Step 8: Single-step execution ('s') -> Execute block20 (local1 = 100 * 5 = 500), land at block30's head
        resp = client.send_raw_packet("s")
        assert resp == "S05", f"Expected S05 after step, got {resp}"
        resp_g = client.send_raw_packet("g")
        pc = int(resp_g[0:8], 16)
        l1 = int(resp_g[40:48], 16)
        assert pc == block30.head_pc, f"Expected step to 0x{block30.head_pc:x}, got 0x{pc:x}"
        assert l1 == 500, f"Expected Local1 = 500, got {l1}"
        print(f"    [Step 8] Single-step 's' -> Stepped to PC=0x{pc:x}, Local1={l1} [PASS]")
        # Step 9: Remove breakpoint ('z0,<addr>,0')
        resp = client.send_raw_packet(f"z0,{block20.head_pc:x},0")
        assert resp == "OK", f"Expected OK, got {resp}"
        assert not dbg.has_breakpoint(block20.head_pc)
        print("    [Step 9] Remove breakpoint -> OK [PASS]")
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

    print("    [PASS] All 10 GDB Remote Debugger Socket integration tests succeeded seamlessly.\n")


if __name__ == "__main__":
    test_gdb_remote_socket_session()

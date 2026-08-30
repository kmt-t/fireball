from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parents[1] if "tests" in str(Path(__file__)) or "scenarios" in str(Path(__file__)) else Path(__file__).resolve().parent
_REPO_ROOT = _PYSIM_DIR.parents[1]

for _p in [_PYSIM_DIR, _PYSIM_DIR / 'core', _PYSIM_DIR / 'runtime', _PYSIM_DIR / 'jit', _PYSIM_DIR / 'platforms',
           _REPO_ROOT / 'docs' / 'components' / 'tier1_core' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier2_runtime' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_jit' / 'concepts',
           _REPO_ROOT / 'docs' / 'components' / 'tier3_platform' / 'concepts']:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys

from pathlib import Path



import sys


from pathlib import Path





"""


experiments/pysim/test_debugger.py





Comprehensive tests for Debug Manager & GDB RSP Protocol Engine (debugger.py).


Strictly implements and verifies all test cases from:


docs/components/tier2_runtime/tests/debug_manager_test_spec.md (DBG-01 ~ DBG-15).


"""








import sys


from pathlib import Path








from debugger import DebuggerManager, GDBRspProtocol


from runtime_engine import BasicBlock, IntegratedHybridEngine, WASMContext


from x64_jit import TraceCompiler








def test_dbg_01_query_halt_reason():


    """DBG-01: '?' command returns last stop reason (S05 = SIGTRAP)."""


    dbg = DebuggerManager()


    dbg.attach()


    dbg.stop_signal = 5


    rsp = GDBRspProtocol(dbg)


    ctx = WASMContext()





    res, pc = rsp.handle_packet("?", 0x100, ctx, {})


    assert res == "$S05#b8"


    assert pc == 0x100








def test_dbg_02_read_virtual_registers():


    """DBG-02: 'g' command reads 20 virtual registers (0:pc, 1:sp, 2:fp, 3:tos, 4..19:local0..15)."""


    dbg = DebuggerManager()


    dbg.attach()


    rsp = GDBRspProtocol(dbg)





    ctx = WASMContext(locals_values=[10, 20, 30])


    ctx.push(999)  # tos





    res, pc = rsp.handle_packet("g", 0x100, ctx, {})


    # Strip framing


    raw = res[1:res.index("#")]


    assert len(raw) == 20 * 8  # 160 hex characters





    # Verify individual registers


    pc_val = int(raw[0:8], 16)


    sp_val = int(raw[8:16], 16)


    tos_val = int(raw[24:32], 16)


    l0_val = int(raw[32:40], 16)


    l1_val = int(raw[40:48], 16)





    assert pc_val == 0x100


    assert sp_val == 1


    assert tos_val == 999


    assert l0_val == 10


    assert l1_val == 20








def test_dbg_03_write_virtual_registers():


    """DBG-03: 'G' command writes all 20 virtual registers."""


    dbg = DebuggerManager()


    dbg.attach()


    rsp = GDBRspProtocol(dbg)


    ctx = WASMContext(locals_values=[0] * 16)





    # Set new PC=0x200, locals[0]=55, locals[1]=77


    regs = [0x200, 0, 0, 0, 55, 77] + [0] * 14


    hex_payload = "G" + "".join(f"{r:08x}" for r in regs)





    res, new_pc = rsp.handle_packet(hex_payload, 0x100, ctx, {})


    assert res.startswith("$OK#")


    assert new_pc == 0x200


    assert ctx.locals[0] == 55


    assert ctx.locals[1] == 77








def test_dbg_04_05_read_memory_and_bounds_check():


    """DBG-04, DBG-05: 'm' command reads guest memory with strict bounds check."""


    dbg = DebuggerManager()


    dbg.attach()


    rsp = GDBRspProtocol(dbg)


    mem = bytearray(b"HELLO FIREBALL WASM")


    ctx = WASMContext(memory=mem)





    # In-bounds read: offset 6, len 8 -> "FIREBALL"


    res, _ = rsp.handle_packet("m6,8", 0, ctx, {})


    raw = res[1:res.index("#")]


    assert bytes.fromhex(raw) == b"FIREBALL"





    # Out-of-bounds read -> E01


    res_err, _ = rsp.handle_packet("m100,10", 0, ctx, {})


    assert res_err.startswith("$E01#")








def test_dbg_06_07_write_memory_flush_jit_and_bounds_check():


    """DBG-06, DBG-07: 'M' command writes memory, flushes JIT cache, and checks bounds."""


    engine = IntegratedHybridEngine(compiler=TraceCompiler())


    dbg = DebuggerManager(engine=engine)


    dbg.attach()


    rsp = GDBRspProtocol(dbg)





    mem = bytearray(64)


    ctx = WASMContext(memory=mem)





    # Populate JIT cache


    block = BasicBlock(head_pc=0x100, ops=[("i32.const", 10)])


    trace = engine.compiler.compile_trace(0x100, block)


    engine.cache.insert(trace)


    assert engine.cache.active.has_trace(0x100)





    # In-bounds write: "M0,4:deadbeef"


    res, _ = rsp.handle_packet("M0,4:deadbeef", 0, ctx, {})


    assert res.startswith("$OK#")


    assert bytes(ctx.memory[0:4]) == bytes.fromhex("deadbeef")


    # Invariant: JIT cache must be flushed ({Debugger_Jit_Flush})


    assert not engine.cache.active.has_trace(0x100)





    # Out-of-bounds write -> E01


    res_err, _ = rsp.handle_packet("M1000,4:12345678", 0, ctx, {})


    assert res_err.startswith("$E01#")








def test_dbg_08_09_breakpoints_and_hit():


    """DBG-08, DBG-09: 'Z0' and 'z0' manage breakpoints; 'c' halts on hit."""


    engine = IntegratedHybridEngine()


    dbg = DebuggerManager(engine=engine)


    dbg.attach()


    rsp = GDBRspProtocol(dbg)





    block1 = BasicBlock(head_pc=0x100, ops=[("local.get", 0), ("i32.const", 1), ("i32.add", None), ("local.set", 0)], next_pc=0x200)


    block2 = BasicBlock(head_pc=0x200, ops=[("local.get", 0), ("i32.const", 2), ("i32.mul", None), ("local.set", 0)], next_pc=None)


    blocks = {0x100: block1, 0x200: block2}





    # Set breakpoint at 0x200


    res_z, _ = rsp.handle_packet("Z0,200,0", 0x100, WASMContext(), blocks)


    assert res_z.startswith("$OK#")


    assert dbg.has_breakpoint(0x200)





    # Continue from 0x100 -> should halt at 0x200 with SIGTRAP ($S05)


    ctx = WASMContext(locals_values=[5])


    res_c, stop_pc = rsp.handle_packet("c", 0x100, ctx, blocks)


    assert res_c.startswith("$S05#")


    assert stop_pc == 0x200


    assert ctx.locals[0] == 6  # block1 executed





    # Remove breakpoint at 0x200 and continue to completion


    rsp.handle_packet("z0,200,0", 0x200, ctx, blocks)


    assert not dbg.has_breakpoint(0x200)





    res_c2, stop_pc2 = rsp.handle_packet("c", 0x200, ctx, blocks)


    assert res_c2.startswith("$W00#")


    assert ctx.locals[0] == 12  # block2 executed








def test_dbg_10_11_single_step_and_termination():


    """DBG-10, DBG-11: 's' single-steps one instruction; ends with W00."""


    engine = IntegratedHybridEngine()


    dbg = DebuggerManager(engine=engine)


    dbg.attach()


    rsp = GDBRspProtocol(dbg)





    block1 = BasicBlock(head_pc=0x100, ops=[("local.get", 0), ("i32.const", 10), ("i32.add", None), ("local.set", 0)], next_pc=0x200)


    block2 = BasicBlock(head_pc=0x200, ops=[("local.get", 0)], next_pc=None)


    blocks = {0x100: block1, 0x200: block2}





    ctx = WASMContext(locals_values=[5])





    # Step 1 -> halts at 0x200 with S05


    res_s1, pc1 = rsp.handle_packet("s", 0x100, ctx, blocks)


    assert res_s1.startswith("$S05#")


    assert pc1 == 0x200


    assert ctx.locals[0] == 15





    # Step 2 -> ends with W00 (clean termination)


    res_s2, pc2 = rsp.handle_packet("s", 0x200, ctx, blocks)


    assert res_s2.startswith("$W00#")








def test_dbg_12_to_15_integrated_profiler_and_assertions():


    """DBG-12 ~ DBG-15: Integrated Profiler PC sampling and memory assertions ({Debug_Integrated})."""


    engine = IntegratedHybridEngine()


    dbg = DebuggerManager(engine=engine)


    dbg.attach()


    rsp = GDBRspProtocol(dbg)





    mem = bytearray([0x00, 0x42, 0x00])


    ctx = WASMContext(memory=mem)





    block = BasicBlock(head_pc=0x100, ops=[("i32.const", 1)], next_pc=None)


    blocks = {0x100: block}





    # Add memory assertion: address 1 must equal 0x42, address 2 must equal 0xFF (will fail)


    dbg.add_memory_assertion(1, 0x42, "status byte")


    dbg.add_memory_assertion(2, 0xFF, "flag byte")





    # Step instruction


    rsp.handle_packet("s", 0x100, ctx, blocks)





    # 1. PC sampling verification


    assert dbg.pc_sample_counts[0x100] == 1





    # 2. Dynamic memory assertion verification


    assert len(dbg.assertion_violations) == 1


    assert "0x2" in dbg.assertion_violations[0]


    assert "expected 255 got 0" in dbg.assertion_violations[0]








ALL_TESTS = sorted(


    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),


    key=lambda fn: fn.__code__.co_firstlineno,


)








if __name__ == "__main__":


    for test in ALL_TESTS:


        test()


        print(f"[PASS] {test.__name__}")


    print(f"\n[PASS] All {len(ALL_TESTS)} Debug Manager & GDB RSP tests passed.")

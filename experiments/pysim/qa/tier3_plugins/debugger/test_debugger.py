from __future__ import annotations

from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parents[2]
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent


"""
experiments/pysim/qa/tier3_plugins/debugger/test_debugger.py
Comprehensive tests for Debug Manager & GDB RSP Protocol Engine (debugger.py).
Contracts and unimplemented production execution boundaries are recorded in
docs/qa/tier3_plugins/debugger_test_spec.md.
"""

import pytest
from config import FB_CONF_DEBUG_MAX_BREAKPOINTS
from execution_context import WASMContext
from helpers import make_debug_execution, wat_to_wasm
from runtime_test_driver import RuntimeEngineDebugDriver
from tier3_plugins.debugger.debugger import (
    DebuggerManager,
    GDBRspProtocol,
    InterpreterExecutionControl,
)


def test_dbg_01_query_halt_reason():
    """TEST-DBG-01: '?' command returns last stop reason (S05 = SIGTRAP)."""
    dbg = DebuggerManager()
    dbg.attach()
    dbg.stop_signal = 5
    rsp = GDBRspProtocol(dbg)
    ctx = WASMContext()
    res, pc = rsp.handle_packet("?", 0x100, ctx, {})
    assert res == "$S05#b8"
    assert pc == 0x100


def test_dbg_02_read_virtual_registers():
    """TEST-DBG-02: 'g' command reads 20 virtual registers (0:pc, 1:sp, 2:fp, 3:tos, 4..19:local0..15)."""
    dbg = DebuggerManager()
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    ctx = WASMContext()
    ctx.locals = (10, 20, 30)
    ctx.push(999)  # tos
    res, pc = rsp.handle_packet("g", 0x100, ctx, {})
    # Strip framing
    raw = res[1 : res.index("#")]
    assert len(raw) == 20 * 8  # 160 hex characters
    # Verify individual registers
    pc_val = int.from_bytes(bytes.fromhex(raw[0:8]), "little")
    sp_val = int.from_bytes(bytes.fromhex(raw[8:16]), "little")
    tos_val = int.from_bytes(bytes.fromhex(raw[24:32]), "little")
    l0_val = int.from_bytes(bytes.fromhex(raw[32:40]), "little")
    l1_val = int.from_bytes(bytes.fromhex(raw[40:48]), "little")
    assert pc_val == 0x100
    assert sp_val == 1
    assert tos_val == 999
    assert l0_val == 10
    assert l1_val == 20


def test_dbg_03_write_virtual_registers():
    """TEST-DBG-03: 'G' command writes all 20 virtual registers."""
    dbg = DebuggerManager()
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    ctx = WASMContext()
    ctx.locals = (0,) * 16
    # Set new PC=0x200, locals[0]=55, locals[1]=77
    regs = [0x200, 0, 0, 0, 55, 77] + [0] * 14
    hex_payload = "G" + "".join((r & 0xFFFF_FFFF).to_bytes(4, "little").hex() for r in regs)
    res, new_pc = rsp.handle_packet(hex_payload, 0x100, ctx, {})
    assert res.startswith("$OK#")
    assert new_pc == 0x200
    assert ctx.locals[0] == 55
    assert ctx.locals[1] == 77
    assert len(ctx.stack) == 0

    invalid_regs = regs.copy()
    invalid_regs[1] = ctx.stack_capacity + 1
    invalid_payload = "G" + "".join(f"{value:08x}" for value in invalid_regs)
    rejected, rejected_pc = rsp.handle_packet(invalid_payload, new_pc, ctx, {})
    assert rejected.startswith("$E01#")
    assert rejected_pc == new_pc
    assert ctx.locals[0] == 55 and ctx.locals[1] == 77
    assert len(ctx.stack) == 0

    malformed_regs = regs.copy()
    malformed_payload = "G" + "".join(
        (value & 0xFFFF_FFFF).to_bytes(4, "little").hex() for value in malformed_regs
    )
    malformed_payload = malformed_payload[:9] + "z" + malformed_payload[10:]
    rejected, rejected_pc = rsp.handle_packet(malformed_payload, new_pc, ctx, {})
    assert rejected.startswith("$E01#")
    assert rejected_pc == new_pc
    assert ctx.locals[0] == 55 and ctx.locals[1] == 77


def test_dbg_03b_single_register_and_supported_query():
    dbg = DebuggerManager()
    rsp = GDBRspProtocol(dbg)
    ctx = WASMContext()
    ctx.locals[0] = 12
    response, pc = rsp.handle_packet("p4", 0x123, ctx, {})
    assert response.startswith("$0c000000#")
    assert pc == 0x123
    response, pc = rsp.handle_packet("P4=2a000000", pc, ctx, {})
    assert response.startswith("$OK#")
    assert ctx.locals[0] == 42
    response, _ = rsp.handle_packet("qSupported:multiprocess+", pc, ctx, {})
    assert response.startswith("$PacketSize=256#")
    assert not GDBRspProtocol.is_valid_packet("$M0,4:deadbeef#00")
    assert GDBRspProtocol.is_valid_packet(GDBRspProtocol.format_packet("?"))


def test_dbg_03c_malformed_register_packets_are_rejected():
    dbg = DebuggerManager()
    rsp = GDBRspProtocol(dbg)
    ctx = WASMContext()
    response, pc = rsp.handle_packet("pzz", 0x123, ctx, {})
    assert response.startswith("$E01#")
    assert pc == 0x123
    response, pc = rsp.handle_packet("P4", 0x123, ctx, {})
    assert response.startswith("$E01#")
    assert pc == 0x123
    assert ctx.locals[0] == 0


def test_dbg_08b_breakpoint_capacity_returns_protocol_error():
    dbg = DebuggerManager()
    rsp = GDBRspProtocol(dbg)
    for pc in range(FB_CONF_DEBUG_MAX_BREAKPOINTS):
        assert dbg.add_breakpoint(pc)

    response, pc = rsp.handle_packet(
        f"Z0,{FB_CONF_DEBUG_MAX_BREAKPOINTS:x},0",
        0x200,
        WASMContext(),
        {},
    )
    assert response.startswith("$E01#")
    assert pc == 0x200
    assert dbg.has_breakpoint(0)
    assert not dbg.has_breakpoint(FB_CONF_DEBUG_MAX_BREAKPOINTS)


def test_dbg_04_05_read_memory_and_bounds_check():
    """TEST-DBG-04, TEST-DBG-05: 'm' command reads guest memory with strict bounds check."""
    dbg = DebuggerManager()
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    mem = bytearray(b"HELLO FIREBALL WASM")
    ctx = WASMContext(memory=mem)
    # In-bounds read: offset 6, len 8 -> "FIREBALL"
    res, _ = rsp.handle_packet("m6,8", 0, ctx, {})
    raw = res[1 : res.index("#")]
    assert bytes.fromhex(raw) == b"FIREBALL"
    # Out-of-bounds read -> E01
    res_err, _ = rsp.handle_packet("m100,10", 0, ctx, {})
    assert res_err.startswith("$E01#")


def test_dbg_06_07_write_memory_and_bounds_check():
    """TEST-DBG-06, TEST-DBG-07: 'M' writes guest memory and checks bounds."""
    engine = RuntimeEngineDebugDriver()
    dbg = DebuggerManager(engine=engine)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    mem = bytearray(64)
    ctx = WASMContext(memory=mem)
    # In-bounds write: "M0,4:deadbeef"
    res, _ = rsp.handle_packet("M0,4:deadbeef", 0, ctx, {})
    assert res.startswith("$OK#")
    assert bytes(ctx.memory[0:4]) == bytes.fromhex("deadbeef")
    # Out-of-bounds write -> E01
    res_err, _ = rsp.handle_packet("M1000,4:12345678", 0, ctx, {})
    assert res_err.startswith("$E01#")


def test_dbg_08_09_breakpoints_and_hit():
    """TEST-DBG-08, TEST-DBG-09: 'Z0' and 'z0' manage breakpoints; 'c' halts on hit."""
    engine = RuntimeEngineDebugDriver()
    # Load real bytecode and use its block metadata only to locate the breakpoint.
    # Execution uses the statically composed native interpreter.
    wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0
          i32.const 1
          i32.add
          local.set 0
        )
        local.get 0
        i32.const 2
        i32.mul
        local.set 0
        local.get 0
        return
      )
    )
    """
    mod = engine.load_wasm(wat_to_wasm(wat))
    execution = make_debug_execution(mod, 0, (5,))
    dbg = DebuggerManager(engine=execution)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    block1, block2 = mod.blocks[0], mod.blocks[1]
    blocks = {block1.head_pc: block1, block2.head_pc: block2}
    # Set breakpoint at block2's head
    res_z, _ = rsp.handle_packet(f"Z0,{block2.head_pc:x},0", block1.head_pc, WASMContext(), blocks)
    assert res_z.startswith("$OK#")
    assert dbg.has_breakpoint(block2.head_pc)
    # Continue from block1 -> should halt at block2 with SIGTRAP ($S05)
    ctx = execution.context
    res_c, stop_pc = rsp.handle_packet("c", 0, ctx, blocks)
    assert res_c.startswith("$S05#")
    assert stop_pc == block2.head_pc
    assert ctx.locals[0] == 6  # block1 executed
    # Remove breakpoint at block2 and continue to completion
    rsp.handle_packet(f"z0,{block2.head_pc:x},0", block2.head_pc, ctx, blocks)
    assert not dbg.has_breakpoint(block2.head_pc)
    res_c2, stop_pc2 = rsp.handle_packet("c", block2.head_pc, ctx, blocks)
    assert res_c2.startswith("$W00#")
    assert execution.call.results == [12]


def test_dbg_11_native_execution_termination_response():
    """TEST-DBG-11: Resume the same native call after a debug stop and return its result."""
    engine = RuntimeEngineDebugDriver()
    wat = """
    (module
      (func (export "f") (param i32) (result i32)
        (block $b
          local.get 0
          i32.const 10
          i32.add
          local.set 0
        )
        local.get 0
        return
      )
    )
    """
    mod = engine.load_wasm(wat_to_wasm(wat))
    execution = make_debug_execution(mod, 0, (5,))
    dbg = DebuggerManager(engine=execution)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    block1, block2 = mod.blocks[0], mod.blocks[1]
    blocks = {block1.head_pc: block1, block2.head_pc: block2}
    ctx = execution.context
    # Step 1 -> halts at block2 with S05
    res_s1, pc1 = rsp.handle_packet("s", 0, ctx, blocks)
    assert res_s1.startswith("$S05#")
    assert pc1 == 2
    assert ctx.locals[0] == 5
    # Step 2 -> ends with W00 (clean termination)
    res_s2, pc2 = rsp.handle_packet("c", pc1, ctx, blocks)
    assert res_s2.startswith("$W00#")
    assert execution.call.results == [15]


def test_dbg_10_23_one_rsp_step_executes_only_one_wasm_instruction() -> None:
    """TEST-DBG-10/23: RSP s must leave later instructions pending."""
    engine = RuntimeEngineDebugDriver()
    module = engine.load_wasm(
        wat_to_wasm("(module (func (param i32) (result i32) local.get 0 i32.const 10 i32.add))")
    )
    assert module.code_for(0) == b"\x20\x00\x41\x0a\x6a\x0b"
    block = module.blocks[0]
    execution = make_debug_execution(module, 0, (5,))
    debugger = DebuggerManager(engine=execution)
    debugger.attach()
    rsp = GDBRspProtocol(debugger)
    ctx = execution.context
    response, next_pc = rsp.handle_packet("s", block.head_pc, ctx, {block.head_pc: block})
    assert response == GDBRspProtocol.format_packet("S05")
    assert next_pc == 2, "one local.get cannot execute the following constant and addition"
    assert tuple(ctx.stack) == (5,)
    assert ctx.locals[0] == 5
    response, next_pc = rsp.handle_packet("s", next_pc, ctx, {})
    assert response == GDBRspProtocol.format_packet("S05")
    assert next_pc == 4 and tuple(ctx.stack) == (5, 10)
    response, next_pc = rsp.handle_packet("c", next_pc, ctx, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert execution.call.results == [15]


def test_dbg_14_15_pc_sampling_and_memory_assertions():
    """TEST-DBG-14, TEST-DBG-15: PC sampling and byte assertion checks ({Debug_Integrated})."""
    engine = RuntimeEngineDebugDriver()
    mem = bytearray([0x00, 0x42, 0x00])
    wat = '(module (func (export "f") i32.const 1 drop return))'
    mod = engine.load_wasm(wat_to_wasm(wat))
    execution = make_debug_execution(mod, 0, (), memory=mem)
    dbg = DebuggerManager(engine=execution)
    dbg.attach()
    rsp = GDBRspProtocol(dbg)
    ctx = execution.context
    block = mod.blocks[0]
    blocks = {block.head_pc: block}
    # Add memory assertion: address 1 must equal 0x42, address 2 must equal 0xFF (will fail)
    dbg.add_memory_assertion(1, 0x42, "status byte")
    dbg.add_memory_assertion(2, 0xFF, "flag byte")
    # Stop the native execution through the configured hook, then inspect debugger records.
    rsp.handle_packet("s", block.head_pc, ctx, blocks)
    # 1. PC sampling verification
    assert dbg.pc_sample_counts[block.head_pc] == 1
    dbg.sample_pc(block.head_pc)
    dbg.sample_pc(block.head_pc)
    dbg.sample_pc(block.head_pc + 2)
    assert dbg.pc_sample_counts[block.head_pc] == 3
    assert dbg.pc_sample_counts[block.head_pc + 2] == 1
    # 2. Dynamic memory assertion verification
    assert len(dbg.assertion_violations) == 1
    assert "0x2" in dbg.assertion_violations[0]
    assert "expected 255 got 0" in dbg.assertion_violations[0]


def _debug_rsp(
    wat: str, args: tuple[int, ...] = (), memory: bytearray | None = None
) -> tuple[InterpreterExecutionControl, DebuggerManager, GDBRspProtocol]:
    module = RuntimeEngineDebugDriver().load_wasm(wat_to_wasm(wat))
    execution = make_debug_execution(module, 0, args, memory=memory)
    debugger = DebuggerManager(engine=execution)
    debugger.attach()
    return execution, debugger, GDBRspProtocol(debugger)


@pytest.mark.parametrize("command", ("Z", "z"))
def test_dbg_08c_breakpoint_pc_overflow_is_rejected_without_state_change(command: str) -> None:
    """TEST-DBG-08c: A PC outside the specified 32-bit width cannot alias another breakpoint."""
    debugger = DebuggerManager()
    debugger.attach()
    rsp = GDBRspProtocol(debugger)
    context = WASMContext()
    for address in (0, 7, 0xFFFF_FFFF):
        assert rsp.handle_packet(f"Z0,{address:x},0", 7, context, {}) == ("$OK#9a", 7)
    before = tuple(debugger._breakpoints)
    response, pc = rsp.handle_packet(f"{command}0,100000000,0", 7, context, {})
    assert response == "$E01#a6" and pc == 7
    assert tuple(debugger._breakpoints) == before == (0, 7, 0xFFFF_FFFF)


def test_dbg_09_interior_breakpoint_stops_before_side_effect_and_resumes() -> None:
    """TEST-DBG-09/10: Native hook stops inside a basic block and retains live values."""
    execution, debugger, rsp = _debug_rsp(
        "(module (func (param i32) (result i32) local.get 0 i32.const 10 i32.add))", (5,)
    )
    ctx = execution.context
    assert ctx.stack is execution.call.context.operand_stack
    rsp.handle_packet("P4=07000000", 0, ctx, {})
    rsp.handle_packet("Z0,2,0", 0, ctx, {})
    response, pc = rsp.handle_packet("c", 0, ctx, {})
    assert response == GDBRspProtocol.format_packet("S05")
    assert pc == 2 and tuple(ctx.stack) == (7,)
    response, pc = rsp.handle_packet("s", pc, ctx, {})
    assert response == GDBRspProtocol.format_packet("S05")
    assert pc == 4 and tuple(ctx.stack) == (7, 10)
    response, pc = rsp.handle_packet("c", pc, ctx, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert execution.call.results == [17]
    debugger.detach()
    assert execution.interpreter.call(0, (2,)) == [12]


@pytest.mark.parametrize("indirect", (False, True), ids=("call", "call_indirect"))
def test_dbg_10_call_and_return_stop_at_selected_function(indirect: bool) -> None:
    """TEST-DBG-10: A call stops at the callee; its end restores the caller PC."""
    dispatch = "i32.const 0 call_indirect (type $sig)" if indirect else "call 0"
    table = "(table 1 funcref) (elem (i32.const 0) 0)" if indirect else ""
    module = RuntimeEngineDebugDriver().load_wasm(
        wat_to_wasm(f"""(module
        (type $sig (func (result i32))) {table}
        (func (type $sig) i32.const 7)
        (func (result i32) {dispatch} i32.const 5 i32.add))""")
    )
    execution = make_debug_execution(module, 1, ())
    debugger = DebuggerManager(engine=execution)
    debugger.attach()
    rsp = GDBRspProtocol(debugger)
    pc = 0x10000
    if indirect:
        response, pc = rsp.handle_packet("s", pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05")
        assert pc == 0x10002 and tuple(execution.context.stack) == (0,)
    resume_pc = 0x10005 if indirect else 0x10002
    for expected_pc, expected_stack in (
        (0, ()),
        (2, (7,)),
        (resume_pc, (7,)),
        (resume_pc + 2, (7, 5)),
        (resume_pc + 3, (12,)),
    ):
        response, pc = rsp.handle_packet("s", pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05")
        assert pc == expected_pc and tuple(execution.context.stack) == expected_stack
    response, pc = rsp.handle_packet("s", pc, execution.context, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert execution.call.results == [12]


@pytest.mark.parametrize(
    "condition,trace,result",
    (
        (1, ((2, (1,)), (4, ()), (6, (10,)), (10, (10,)), (12, (10, 1)), (13, (11,))), 11),
        (0, ((2, (0,)), (7, ()), (9, (20,)), (10, (20,)), (12, (20, 1)), (13, (21,))), 21),
    ),
)
def test_dbg_10_branch_stop_retains_control_state(
    condition: int, trace: tuple[tuple[int, tuple[int, ...]], ...], result: int
) -> None:
    """TEST-DBG-10: The native branch handler selects the observed instruction PC."""
    execution, _, rsp = _debug_rsp(
        """(module (func (param i32) (result i32)
        local.get 0 if (result i32) i32.const 10 else i32.const 20 end
        i32.const 1 i32.add))""",
        (condition,),
    )
    pc = 0
    for expected_pc, stack in trace:
        response, pc = rsp.handle_packet("s", pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05")
        assert pc == expected_pc and tuple(execution.context.stack) == stack
    response, pc = rsp.handle_packet("c", pc, execution.context, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert execution.call.results == [result]


def test_dbg_10_loop_stop_keeps_local_and_control_frame_history() -> None:
    """TEST-DBG-10: Stop/restart does not replay the loop opener or lose its frame."""
    execution, _, rsp = _debug_rsp(
        """(module (func (param i32) (result i32)
        loop local.get 0 i32.const 1 i32.sub local.tee 0 br_if 0 end local.get 0))""",
        (2,),
    )
    pc = 0
    for expected_pc, stack in (
        (2, ()),
        (4, (2,)),
        (6, (2, 1)),
        (7, (1,)),
        (9, (1,)),
        (2, ()),
        (4, (1,)),
        (6, (1, 1)),
        (7, (0,)),
        (9, (0,)),
        (11, ()),
        (12, ()),
        (14, (0,)),
    ):
        response, pc = rsp.handle_packet("s", pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05")
        assert pc == expected_pc and tuple(execution.context.stack) == stack
    response, pc = rsp.handle_packet("c", pc, execution.context, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert execution.call.results == [0]


def test_dbg_10_store_does_not_execute_early_or_damage_other_bytes() -> None:
    """TEST-DBG-10: Stop before each store and compare the entire live memory."""
    memory = bytearray(b"\xaa" * 65536)
    execution, _, rsp = _debug_rsp(
        """(module (memory 1) (func
        i32.const 0 i32.const 7 i32.store8 i32.const 1 i32.const 9 i32.store8))""",
        memory=memory,
    )
    expected = bytearray(memory)
    pc = 0
    for expected_pc in (2, 4, 7, 9, 11, 14):
        response, pc = rsp.handle_packet("s", pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05")
        assert pc == expected_pc
        if pc == 7:
            expected[0] = 7
        if pc == 14:
            expected[1] = 9
        assert memory == expected
    response, pc = rsp.handle_packet("c", pc, execution.context, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert memory == expected


@pytest.mark.parametrize(
    "value_type,instruction,next_pc,words",
    (
        ("i32", "i32.const 128", 3, (128,)),
        ("i64", "i64.const -1", 2, (0xFFFF_FFFF, 0xFFFF_FFFF)),
        ("f32", "f32.const 1.5", 5, (0x3FC00000,)),
        ("f64", "f64.const 1.5", 9, (0, 0x3FF80000)),
    ),
)
def test_dbg_10_immediate_is_one_instruction(
    value_type: str, instruction: str, next_pc: int, words: tuple[int, ...]
) -> None:
    """TEST-DBG-10: The hook never stops between an opcode and its immediate bytes."""
    execution, _, rsp = _debug_rsp(f"(module (func (result {value_type}) {instruction}))")
    response, pc = rsp.handle_packet("s", 0, execution.context, {})
    assert response == GDBRspProtocol.format_packet("S05")
    assert pc == next_pc and tuple(execution.context.stack) == words


@pytest.mark.parametrize("command", ("s", "c"))
@pytest.mark.parametrize("prefix,fault_pc", (("", 0), ("i32.const 7 drop", 3)))
def test_dbg_10_trap_stops_without_normal_exit_or_later_update(
    command: str, prefix: str, fault_pc: int
) -> None:
    """TEST-DBG-10/11: Fault PC is retained; a trap is never reported as W00."""
    from tier3_executer.interpreter.interpreter import TrapCode

    execution, _, rsp = _debug_rsp(f"""(module (global (mut i32) (i32.const 0))
        (func {prefix} unreachable i32.const 1 global.set 0))""")
    if command == "s" and fault_pc != 0:
        _, pc = rsp.handle_packet("s", 0, execution.context, {})
        _, pc = rsp.handle_packet("s", pc, execution.context, {})
        assert pc == fault_pc
    else:
        pc = 0
    for _ in range(2):
        response, pc = rsp.handle_packet(command, pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05") and pc == fault_pc
        assert execution.call.trap is not None
        assert execution.call.trap.code == TrapCode.UNREACHABLE
        assert execution.interpreter.globals[0] == 0


def test_dbg_10_host_boundary_does_not_replay_the_import() -> None:
    """TEST-DBG-10: A host call completes once, before the next debug stop."""
    from system_containers import StaticVector

    calls: list[int] = []

    def record(value: int) -> None:
        calls.append(value)

    module = RuntimeEngineDebugDriver().load_wasm(
        wat_to_wasm("""(module
        (import "env" "record" (func (param i32)))
        (func i32.const 7 call 0 i32.const 9 drop))""")
    )
    execution = make_debug_execution(
        module, 1, (), host_functions=StaticVector.of((record,), capacity=1)
    )
    debugger = DebuggerManager(engine=execution)
    debugger.attach()
    rsp = GDBRspProtocol(debugger)
    response, pc = rsp.handle_packet("s", 0x10000, execution.context, {})
    assert pc == 0x10002 and calls == []
    response, pc = rsp.handle_packet("s", pc, execution.context, {})
    assert response == GDBRspProtocol.format_packet("S05")
    assert pc == 0x10004 and calls == [7] and tuple(execution.context.stack) == ()
    response, pc = rsp.handle_packet("c", pc, execution.context, {})
    assert response == GDBRspProtocol.format_packet("W00") and calls == [7]


def test_dbg_12_disabled_composition_has_no_debug_state_or_weave() -> None:
    """TEST-DBG-12: Disabled composition keeps the plain ABI and native entry."""
    import ctypes

    from helpers import make_native_interpreter
    from interop_abi import ExecutionContextNative
    from runtime_composer import RuntimeComposer, RuntimeCompositionConfig
    from tier3_executer.interpreter import native_abi
    from tier3_executer.interpreter.interpreter import NativeInterpreter

    module = RuntimeEngineDebugDriver().load_wasm(
        wat_to_wasm("(module (func (result i32) i32.const 7))")
    )
    weave_calls: list[int] = []

    def forbidden_weave(executor: NativeInterpreter) -> None:
        weave_calls.append(1)

    interpreter = RuntimeComposer.compose_execution(
        RuntimeCompositionConfig(), lambda: make_native_interpreter(module), forbidden_weave
    )
    call = interpreter.start(0, ())
    assert weave_calls == []
    assert interpreter._native_dispatcher is native_abi.run_native_dispatch
    assert (
        ctypes.sizeof(call.context.native_context) == ctypes.sizeof(ExecutionContextNative) == 144
    )
    interpreter.step(call)
    assert call.finished and call.results == [7]


def test_dbg_12_continue_returns_to_python_only_at_actual_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST-DBG-12/13: Thousands of instructions run inside one existing native step."""
    from tier3_executer.interpreter.interpreter import InterpreterCall, NativeInterpreter

    execution, _, rsp = _debug_rsp(
        "(module (func (result i32) " + "i32.const 1 drop " * 1000 + "i32.const 7))"
    )
    steps: list[int] = []
    original_step = NativeInterpreter.step

    def tracked_step(interpreter: NativeInterpreter, call: InterpreterCall) -> InterpreterCall:
        steps.append(1)
        return original_step(interpreter, call)

    monkeypatch.setattr(NativeInterpreter, "step", tracked_step)
    response, pc = rsp.handle_packet("c", 0, execution.context, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert execution.call.results == [7]
    assert steps == [1], "continue must not return through Python for each instruction"


def test_dbg_13_composition_rejects_jit_before_creating_executor() -> None:
    """GOTCHA-DBG-01: Static exclusion happens before construction or weaving."""
    from helpers import expect_assertion, make_native_interpreter
    from runtime_composer import (
        RuntimeComposer,
        RuntimeCompositionConfig,
        RuntimeExecutionKind,
        RuntimePluginSelection,
    )
    from tier3_executer.interpreter.interpreter import NativeInterpreter

    module = RuntimeEngineDebugDriver().load_wasm(wat_to_wasm("(module (func))"))
    constructions: list[int] = []

    def factory() -> NativeInterpreter:
        constructions.append(1)
        return make_native_interpreter(module)

    def weave(executor: NativeInterpreter) -> None:
        constructions.append(2)

    with expect_assertion():
        RuntimeComposer.compose_execution(
            RuntimeCompositionConfig(
                execution=RuntimeExecutionKind.JIT, plugins=RuntimePluginSelection(debugger=True)
            ),
            factory,
            weave,
        )
    assert constructions == []


def test_dbg_13_two_compositions_keep_stop_state_and_storage_independent() -> None:
    """TEST-DBG-12/13: A stopped debug runtime cannot alter another runtime."""
    from helpers import make_native_interpreter

    module = RuntimeEngineDebugDriver().load_wasm(
        wat_to_wasm("(module (func (param i32) (result i32) local.get 0 i32.const 10 i32.add))")
    )
    normal = make_native_interpreter(module)
    first, first_debugger, first_rsp = _debug_rsp(
        "(module (func (param i32) (result i32) local.get 0 i32.const 10 i32.add))", (5,)
    )
    second, second_debugger, second_rsp = _debug_rsp(
        "(module (func (param i32) (result i32) local.get 0 i32.const 10 i32.add))", (20,)
    )
    first_rsp.handle_packet("Z0,2,0", 0, first.context, {})
    response, first_pc = first_rsp.handle_packet("c", 0, first.context, {})
    assert response == GDBRspProtocol.format_packet("S05") and first_pc == 2
    assert normal.call(0, (30,)) == [40]
    response, second_pc = second_rsp.handle_packet("c", 0, second.context, {})
    assert response == GDBRspProtocol.format_packet("W00") and second.call.results == [30]
    assert tuple(first.context.stack) == (5,) and first.call.current_pc() == 2
    assert first_debugger.has_breakpoint(2) and not second_debugger.has_breakpoint(2)
    response, first_pc = first_rsp.handle_packet("c", first_pc, first.context, {})
    assert response == GDBRspProtocol.format_packet("W00") and first.call.results == [15]


if __name__ == "__main__":
    raise SystemExit(pytest.main([str(_TEST_FILE)]))

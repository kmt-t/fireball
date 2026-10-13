from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: WASM Interpreter & Instructions
Traceability: interpreter_test_spec.md, wasm_instruction_set_test_spec.md
"""

import ctypes
import struct
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parents[2]
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


# Keep the product Tier 2 package ahead of the QA package when importing
# RuntimeEngine and Interpreter modules.

import pytest
from qa.shared.helpers import (
    get_call_cont,
    make_native_interpreter,
    wat_to_wasm,
)
from qa.shared.helpers import make_interpreter as Interpreter
from system_containers import StaticVector
from tier2_runtime.interpreter.interpreter import ExecutionContext, Trap, WasmNumber
from tier2_runtime.interpreter.interpreter import TrapCode as InterpreterTrapCode
from tier2_runtime.runtime.engine import RuntimeEngine
from tier2_runtime.vmmio.controller import TrapCode
from tier2_runtime.wasm.module import (
    F64,
    I32,
    I64,
    FunctionTable,
    LocalLayout,
    Memory,
)
from tier2_runtime.wasm.reader import parse
from tier2_runtime.wasm.validation import InvalidWasm


def test_intp_01_python_reference_cps_handlers_and_dispatch_table():
    """TEST-INTP-01 (Python reference): handlers share stack-backed state.

    The reference table is not evidence of the native dispatcher implementation (TEST-INTP-02).
    """
    import inspect

    from tier2_runtime.interpreter.interpreter import _HANDLERS
    from tier2_runtime.wasm.opcodes import UNREACHABLE

    # Immutable direct dispatch catalog; an execution step cannot alter a handler.
    unreachable_handler = _HANDLERS[UNREACHABLE]
    with pytest.raises(TypeError):
        _HANDLERS[UNREACHABLE] = None
    assert _HANDLERS[UNREACHABLE] is unreachable_handler
    assert len(_HANDLERS) == 256
    # Every registered handler receives the context, instruction pointer, and operand stack.
    registered_count = 0
    for op, handler in enumerate(_HANDLERS):
        if handler is not None:
            registered_count += 1
            sig = inspect.signature(handler)
            assert len(sig.parameters) == 3, (
                f"Handler for opcode 0x{op:02X} must have exactly 3 arguments"
            )
            assert tuple(sig.parameters) == ("ctx", "ip", "sp"), (
                f"Handler for opcode 0x{op:02X} must use the shared ctx/ip/sp ABI"
            )

    assert registered_count >= 30, (
        f"Expected at least 30 registered MVP opcode handlers, found {registered_count}"
    )


def test_python_interpreter_preserves_shared_operand_stack_across_call():
    """The caller's live operand remains on the context stack across a nested call."""
    module = parse(
        wat_to_wasm(
            '(module (func (export "main") (result i32) '
            "i32.const 10 call 1 i32.add) "
            "(func (result i32) i32.const 32))"
        )
    )
    interpreter = Interpreter(module)
    call_state = interpreter.start(module.export_func_index("main"), [])
    context_stack = call_state.context.stack
    caller = call_state._frame

    assert caller is not None
    assert caller.values is context_stack
    call_state = interpreter.step(call_state)
    callee = call_state._frame
    assert callee is not None and callee is not caller
    assert callee.values is context_stack
    assert len(context_stack) == 1
    assert context_stack.raw_at(0) == 10

    for _ in range(5):
        if call_state.finished:
            break
        call_state = interpreter.step(call_state)

    assert call_state.finished
    assert call_state.results == [42]


def test_control_opcodes_use_static_metadata_without_control_frames():
    from tier2_runtime.interpreter.control_flow import (
        OPCODE_ATTRIBUTES,
        OpcodeAttribute,
        opcode_has_attribute,
    )
    from tier2_runtime.wasm.opcodes import BR_IF, CALL, I32_ADD, LOOP

    assert opcode_has_attribute(CALL, OpcodeAttribute.CALL)
    assert opcode_has_attribute(BR_IF, OpcodeAttribute.BRANCH)
    assert opcode_has_attribute(LOOP, OpcodeAttribute.BASIC_BLOCK_BOUNDARY)
    assert not opcode_has_attribute(I32_ADD, OpcodeAttribute.BASIC_BLOCK_BOUNDARY)
    attributes = memoryview(OPCODE_ATTRIBUTES.buffer)
    assert attributes.readonly
    with pytest.raises(TypeError):
        attributes[CALL // 2] = 0
    assert opcode_has_attribute(CALL, OpcodeAttribute.CALL)

    module = parse(
        wat_to_wasm(
            '(module (func (export "main") (result i32) '
            "(block $outer (result i32) (i32.const 17) (br $outer))))"
        )
    )
    interpreter = Interpreter(module)
    assert interpreter.call(0, []).unwrap() == [17]


def test_loader_builds_control_map_before_interpreter_creation():
    module = parse(
        wat_to_wasm(
            "(module (global i64 (i64.const 0)) "
            '(func (export "main") i32.const 1 drop (block (br 0))))'
        )
    )
    function = module.functions[0]

    assert function.control_map is not None
    loaded_control_map = function.control_map
    assert function.interpreter_annotation_pool is not None
    loaded_annotation_pool = function.interpreter_annotation_pool
    assert module.blocks
    assert tuple(loaded_annotation_pool) == (
        2,
        1,
        0,
        0,
        0,
        1,
        1,
        0,
        3,
        0,
        0,
        0,
        3,
        0,
        255,
        255,
    )
    assert function.entry_block_index == 0
    assert tuple(block.next_block_index for block in module.blocks) == (1, 3, 3, None)
    assert tuple(block.interpreter_annotation_byte_offset for block in module.blocks) == (
        0,
        8,
        12,
        14,
    )
    Interpreter(module)
    assert function.control_map is loaded_control_map
    assert function.interpreter_annotation_pool is loaded_annotation_pool
    assert tuple(block.interpreter_annotation_byte_offset for block in module.blocks) == (
        0,
        8,
        12,
        14,
    )

    from qa.shared.helpers import make_native_interpreter

    native = make_native_interpreter(module)
    assert native.call(module.export_func_index("main"), []).unwrap() == []
    native_execution = native._module_execution
    assert native_execution is not None
    assert native_execution._module_view.blocks == module.blocks.native_address
    assert native_execution._module_view.types == module.types.native_address
    assert native_execution._module_view.global_widths == module.global_widths_address
    assert ctypes.c_uint8.from_address(module.global_widths_address).value == 2
    function_view = native_execution._templates[0]._native_function_view
    assert not hasattr(function_view, "control_map")
    assert function_view.interpreter_annotation_pool == loaded_annotation_pool.native_address


@pytest.mark.parametrize(
    "expression, result_type, expected_trap",
    (
        ("unreachable", "i32", InterpreterTrapCode.UNREACHABLE),
        ("i32.const 1 i32.const 0 i32.div_s", "i32", InterpreterTrapCode.INTEGER_DIVIDE_BY_ZERO),
        ("i64.const 1 i64.const 0 i64.rem_u", "i64", InterpreterTrapCode.INTEGER_DIVIDE_BY_ZERO),
        (
            "i32.const -2147483648 i32.const -1 i32.div_s",
            "i32",
            InterpreterTrapCode.INTEGER_OVERFLOW,
        ),
        ("f64.const nan i32.trunc_f64_s", "i32", InterpreterTrapCode.INVALID_CONVERSION),
        ("f64.const 2147483648 i32.trunc_f64_s", "i32", InterpreterTrapCode.INVALID_CONVERSION),
        ("i32.const 65535 i32.load", "i32", InterpreterTrapCode.MEMORY_OUT_OF_BOUNDS),
    ),
)
@pytest.mark.parametrize("native", (False, True))
def test_wasm_runtime_traps_keep_code_and_stop_following_effects(
    expression: str, result_type: str, expected_trap: InterpreterTrapCode, native: bool
) -> None:
    module = parse(
        wat_to_wasm(
            "(module (memory 1) (global (mut i32) (i32.const 0)) "
            f"(func (result {result_type}) {expression} i32.const 99 global.set 0))"
        )
    )
    interpreter = make_native_interpreter(module) if native else Interpreter(module)
    call = interpreter.start(0, ())
    while not call.finished:
        interpreter.step(call)
    assert call.trap is not None and call.trap.code == expected_trap
    assert interpreter.globals[0] == 0


def test_intp_73_wide_frame_uses_compact_per_local_layout():
    """TEST-INTP-73: each local uses its precomputed packed word span."""
    module = parse(
        memoryview(
            wat_to_wasm(
                "(module (func (param i32 i64 f64) (result i64) (local i32 i64) local.get 1))"
            )
        )
    )
    function = module.functions[0]

    assert function.param_packed_slot_count_cache == 5
    layout = function.local_layout_cache
    assert layout is not None
    assert tuple(layout.offset(i) for i in range(len(layout))) == (0, 1, 3, 5, 6)
    assert tuple(layout.size_words(i) for i in range(len(layout))) == (1, 2, 2, 1, 2)
    assert layout.total_words == 8
    assert function.local_slot_count_cache == 8
    assert Interpreter(module).call(0, [7, 42, 3.5]).unwrap() == [42]


def test_interpreter_annotation_pool_stores_derived_operands_compactly():
    """Store derived values and encoded widths; fixed-width floats stay in code."""
    from tier2_runtime.interpreter.control_flow import build_interpreter_annotation_pool

    code = bytes.fromhex(
        "28 02 04 "  # i32.load: alignment, then offset
        "11 01 02 "  # call_indirect: type index, then table index
        "0e 01 00 01 "  # br_table: count and two depths
        "42 80 01 "  # i64.const 128: two u32 words
        "43 00 00 c0 7f "  # f32.const: fixed-width payload stays in bytecode
        "44 00 00 00 00 00 00 f8 7f "  # f64.const: fixed-width payload
        "0b"
    )
    annotations, block_offsets = build_interpreter_annotation_pool(
        memoryview(code), (0, 3, 6, 10, 13, 18, 27), local_layout=LocalLayout(())
    )

    assert list(block_offsets) == [0, 5, 10, 20, 29, 29, 29]
    assert list(annotations) == list(
        bytes.fromhex(
            "03 04 00 00 00 03 01 00 ff ff 01 00 ff ff 00 00 ff ff 00 00 "
            "03 80 00 00 00 00 00 00 00 ff ff"
        )
    )


@pytest.mark.parametrize("label_count", (0, 1, 255, 256))
def test_br_table_annotation_capacity_matches_depth_and_target_records(label_count: int):
    """The table stores one u16 count and a four-byte resolved record per label."""
    from tier2_runtime.interpreter.control_flow import (
        _count_interpreter_annotation_pool_bytes,
        build_interpreter_annotation_pool,
    )

    encoded_count = bytearray()
    remaining = label_count
    while remaining >= 0x80:
        encoded_count.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    encoded_count.append(remaining)
    code = memoryview(bytes((0x0E,)) + bytes(encoded_count) + bytes(label_count + 1))

    annotations, block_offsets = build_interpreter_annotation_pool(
        code, (0,), local_layout=LocalLayout(())
    )
    target_count = label_count + 1
    expected_bytes = 2 + target_count * 4
    assert _count_interpreter_annotation_pool_bytes(code) == expected_bytes
    assert len(annotations) == expected_bytes
    annotation_bytes = list(annotations)
    assert annotation_bytes[:2] == [label_count & 0xFF, (label_count >> 8) & 0xFF]
    assert list(block_offsets) == [0]
    for index in range(target_count):
        entry = 2 + index * 4
        assert annotation_bytes[entry : entry + 4] == [0xFF, 0xFF, 0, 0]


def test_native_br_table_with_255_labels_preserves_branch_result():
    labels = " ".join("$target" for _ in range(255))
    wasm_bytes = wat_to_wasm(
        '(module (func (export "dispatch") (param i32) '
        f"(block $target (local.get 0) (br_table {labels} $target))))"
    )
    assert wasm_bytes
    module = parse(memoryview(wasm_bytes))
    interpreter = make_native_interpreter(module)
    for selector in (0, 254, 255, 256):
        assert interpreter.call(module.export_func_index("dispatch"), [selector]).unwrap() == []


def test_native_br_table_single_label_preserves_branch_result():
    module = parse(
        memoryview(
            wat_to_wasm(
                '(module (func (export "dispatch") (param i32) '
                "(block $target (local.get 0) (br_table $target $target))))"
            )
        )
    )
    assert (
        make_native_interpreter(module).call(module.export_func_index("dispatch"), [0]).unwrap()
        == []
    )


def test_native_br_table_default_only_preserves_branch_result():
    module = parse(
        memoryview(
            wat_to_wasm(
                '(module (func (export "dispatch") (param i32) '
                "(block $target (local.get 0) (br_table $target))))"
            )
        )
    )

    interpreter = make_native_interpreter(module)
    for selector in (0, 1, 0xFFFF_FFFF):
        assert interpreter.call(module.export_func_index("dispatch"), [selector]).unwrap() == []


def test_native_single_byte_local_boundary_and_multibyte_call():
    prior_functions = " ".join("(func)" for _ in range(128))
    locals_decl = " ".join("(local i32)" for _ in range(128))
    module = parse(
        memoryview(
            wat_to_wasm(
                f"(module {prior_functions} "
                f"(func (result i32) {locals_decl} "
                "(local.set 127 (i32.const 37)) local.get 127) "
                '(func (export "main") (result i32) (call 128)))'
            )
        )
    )

    assert make_native_interpreter(module).call(module.export_func_index("main"), []).unwrap() == [
        37
    ]


def test_native_call_dispatch_resolves_callee_and_caller_blocks(monkeypatch):
    from qa.private.interpreter_native_abi import (
        RUN_DISPATCH_STATS,
        NativeDiagnosticResult,
    )
    from tier2_runtime.interpreter.interpreter import (
        InterpreterBindings,
        NativeInterpreter,
    )

    prior_functions = " ".join("(func)" for _ in range(128))
    module = parse(
        memoryview(
            wat_to_wasm(
                f"(module {prior_functions} (func (drop (i32.const 0))) "
                '(func (export "main") (result i64) (call 128) (i64.const 42)))'
            )
        )
    )

    native_dispatch = NativeInterpreter.run_native_dispatch
    observations: list[NativeDiagnosticResult] = []

    def capture_stats(self, call_state, idle_budget=0):
        result = NativeDiagnosticResult()
        call_state.context._native_result = result
        status = native_dispatch(self, call_state, idle_budget)
        observations.append(result)
        return status

    monkeypatch.setattr(NativeInterpreter, "run_native_dispatch", capture_stats)
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.empty(),
        native_dispatcher=RUN_DISPATCH_STATS,
    )

    assert interpreter.call(module.export_func_index("main"), []).unwrap() == [42]
    assert len(observations) == 1
    assert observations[0].block_search_cache_hits == 0
    assert observations[0].block_search_cache_misses == 0


def test_native_runtime_api_false_stops_before_following_store():
    module = parse(
        wat_to_wasm(
            '(module (memory 1) (func (export "main") '
            "i32.const 1 i32.const 0 i32.div_s drop "
            "i32.const 0 i32.const 99 i32.store))"
        )
    )
    memory = bytearray(65536)
    interpreter = make_native_interpreter(module, memory=memory)

    outcome = interpreter.call(module.export_func_index("main"), [])
    assert not outcome.is_ok
    assert (
        outcome.error is not None
        and outcome.error.code == InterpreterTrapCode.INTEGER_DIVIDE_BY_ZERO
    )
    assert memory[:4] == b"\x00\x00\x00\x00"


def test_intp_72_control_map_uses_four_entry_locality_caches():
    """TEST-INTP-72: static control metadata retains its bounded locality caches."""
    from tier2_runtime.interpreter.control_flow import build_control_map

    control_map = build_control_map(memoryview(b"\x02\x40\x0b"))
    assert control_map.block(0) == (2, None, 0)
    typed_map = build_control_map(memoryview(b"\x02\x7f\x0b"))
    assert typed_map.block(0) == (2, None, 1)
    wide_map = build_control_map(memoryview(b"\x02\x7e\x0b"))
    assert wide_map.block(0) == (2, None, 2)
    assert len(control_map.block_cache) == 4
    assert len(control_map.branch_cache) == 4
    for ip in (0, 1, 2, 0xFFFF, 0x1_0000, 0xFFFF_FFFF):
        assert 0 <= control_map._cache_slot(ip) < 4


def test_intp_01_handler_returns_specific_trap_outcome():
    """TEST-INTP-01: the reference handler and execution boundary report UNREACHABLE."""
    from tier2_runtime.interpreter.interpreter import _HANDLERS
    from tier2_runtime.wasm.opcodes import UNREACHABLE

    module = parse(wat_to_wasm("(module (func unreachable))"))
    call_state = Interpreter(module).start(0, [])
    assert get_call_cont(call_state) is not None
    ip, frame, _ = get_call_cont(call_state)
    call_state.context.ip = ip

    handler = _HANDLERS[UNREACHABLE]
    assert handler is not None
    result = handler(call_state.context, call_state.context.ip_ptr, frame.values)
    assert isinstance(result, Trap)
    assert result.code == InterpreterTrapCode.UNREACHABLE

    stepped = Interpreter(module).step(Interpreter(module).start(0, []))
    assert stepped.finished
    assert stepped.results is None
    assert isinstance(stepped.trap, Trap)
    assert stepped.trap.code == InterpreterTrapCode.UNREACHABLE


def test_intp_17_return_publishes_explicit_sentinel_before_frame_pop():
    """TEST-INTP-17: RETURN publishes the sentinel; the next step completes it."""
    from tier2_runtime.interpreter.interpreter import RETURN_SENTINEL_IP, RETURN_SENTINEL_PC

    module = parse(wat_to_wasm("(module (func (result i32) i32.const 7 return))"))
    interp = Interpreter(module)
    call_state = interp.step(interp.start(0, []))
    assert not call_state.finished
    assert get_call_cont(call_state) is not None
    sentinel_ip, _, _ = get_call_cont(call_state)
    assert sentinel_ip == RETURN_SENTINEL_IP
    assert call_state.current_pc() == RETURN_SENTINEL_PC
    completed = interp.step(call_state)
    assert completed.finished
    assert completed.results == [7]


def test_typed_block_results_keep_wide_native_slots():
    """Label pruning preserves i64/f32/f64 results through the shared raw stack."""
    module = parse(
        wat_to_wasm(
            """(module
              (func (result i64)
                (block (result i64) i64.const 42 br 0))
              (func (result f32)
                (block (result f32) f32.const 1.5 br 0))
              (func (result f64)
                (block (result f64) f64.const 2.5 br 0)))"""
        )
    )
    interp = Interpreter(module)
    assert interp.call(0, []).unwrap() == [42]
    assert interp.call(1, []).unwrap() == [1.5]
    assert interp.call(2, []).unwrap() == [2.5]


def test_native_interpreter_returns_to_python_at_loop_yield_counts(monkeypatch):
    """Native Interpreter returns at the configured backedge count, as Hybrid JIT does."""
    from config import FB_CONF_INTERPRETER_YIELD_THRESHOLD
    from tier2_runtime.interpreter.interpreter import (
        InterpreterBindings,
        InterpreterCall,
        NativeInterpreter,
    )

    module = parse(
        wat_to_wasm(
            """
            (module
              (func (export "sum_to") (param $n i32) (result i32)
                (local $sum i32)
                (block $exit
                  (loop $top
                    (br_if $exit (i32.eqz (local.get $n)))
                    (local.set $sum (i32.add (local.get $sum) (local.get $n)))
                    (local.set $n (i32.sub (local.get $n) (i32.const 1)))
                    (br $top)
                  )
                )
                (local.get $sum)
              )
            )
            """
        )
    )
    function_index = module.export_func_index("sum_to")
    native_dispatch = NativeInterpreter.run_native_dispatch
    returned_statuses: list[int] = []

    def record_native_dispatch(
        self: NativeInterpreter,
        call_state: InterpreterCall,
        idle_budget: int = 0,
    ) -> int:
        result = native_dispatch(
            self,
            call_state,
            idle_budget,
        )
        returned_statuses.append(result)
        return result

    monkeypatch.setattr(NativeInterpreter, "run_native_dispatch", record_native_dispatch)
    iteration_count = FB_CONF_INTERPRETER_YIELD_THRESHOLD * 2 + 1
    results = (
        NativeInterpreter(module, InterpreterBindings.empty())
        .call(function_index, [iteration_count])
        .unwrap()
    )

    assert results == [sum(range(1, iteration_count + 1))]
    assert returned_statuses == [5, 5, 1]


def test_native_dispatch_reuses_block_search_results_within_dispatch(monkeypatch):
    from qa.private.interpreter_native_abi import (
        RUN_DISPATCH_STATS,
        NativeDiagnosticResult,
    )
    from tier2_runtime.interpreter.interpreter import (
        InterpreterBindings,
        NativeInterpreter,
    )

    module = parse(
        wat_to_wasm(
            """(module
              (func (export "sum_to") (param $n i32) (result i32)
                (local $sum i32)
                (block $exit
                  (loop $top
                    (br_if $exit (i32.eqz (local.get $n)))
                    (local.set $sum (i32.add (local.get $sum) (local.get $n)))
                    (local.set $n (i32.sub (local.get $n) (i32.const 1)))
                    (br $top)))
                (local.get $sum)))"""
        )
    )
    native_dispatch = NativeInterpreter.run_native_dispatch
    observations: list[NativeDiagnosticResult] = []

    def capture_stats(self, call_state, idle_budget=0):
        result = NativeDiagnosticResult()
        call_state.context._native_result = result
        status = native_dispatch(self, call_state, idle_budget)
        observations.append(result)
        return status

    monkeypatch.setattr(NativeInterpreter, "run_native_dispatch", capture_stats)
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.empty(),
        native_dispatcher=RUN_DISPATCH_STATS,
        yield_threshold=64,
    )

    assert interpreter.call(module.export_func_index("sum_to"), [8]).unwrap() == [36]
    assert len(observations) == 1
    assert observations[0].block_search_cache_misses == 0
    assert observations[0].block_search_cache_hits == 0


def test_intp_16_nested_return_consumes_sentinel_and_restores_caller():
    """TEST-INTP-16: nested return restores the caller on the shared stack."""
    from tier2_runtime.interpreter.interpreter import RETURN_SENTINEL_IP

    module = parse(
        wat_to_wasm(
            """(module
              (func (result i32) i32.const 7 return)
              (func (result i32) call 0 i32.const 1 i32.add return)
              (func (result i64) i64.const 70 return)
              (func (result i64) call 2 i64.const 1 i64.add return)
              (func (result f32) f32.const 7.5 return)
              (func (result f32) call 4 f32.const 1.0 f32.add return)
              (func (result f64) f64.const 7.5 return)
              (func (result f64) call 6 f64.const 1.0 f64.add return))"""
        )
    )
    interp = Interpreter(module)
    state = interp.start(1, [])
    state = interp.step(state)
    assert not state.finished
    assert get_call_cont(state) is not None
    assert get_call_cont(state)[0] == 0
    state = interp.step(state)
    assert not state.finished
    assert get_call_cont(state) is not None
    assert get_call_cont(state)[0] == RETURN_SENTINEL_IP
    state = interp.step(state)
    assert not state.finished
    assert get_call_cont(state) is not None
    assert state.func_index == 1
    assert get_call_cont(state)[0] != RETURN_SENTINEL_IP
    completed = interp.call(1, []).unwrap()
    assert completed == [8]
    assert interp.call(3, []).unwrap() == [71]
    assert interp.call(5, []).unwrap() == [8.5]
    assert interp.call(7, []).unwrap() == [8.5]


def test_intp_06_memory_traps_are_handler_results():
    """vMMIO faults leave the handler as a trap result, never as a Python exception."""
    module = parse(
        wat_to_wasm("(module (memory 1) (func (param i32) (result i32) local.get 0 i32.load))")
    )
    interp = Interpreter(module, memory=bytearray(65536))
    stepped = interp.step(interp.start(0, [0x8000_0000]))
    assert stepped.finished
    assert stepped.results is None
    assert isinstance(stepped.trap, Trap)


def test_intp_07_host_call_and_vmmio_separation():
    """Interpreter imports dispatch directly to host calls, not SYSCTL vMMIO."""
    from system import FbSyscallId, System, WasiErrno
    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System()
    try:
        module = parse(
            wat_to_wasm(
                f"""(module
                  (import "fireball" "fireball_call"
                    (func $fireball_call
                      (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
                  (func (result i32)
                    i32.const {int(FbSyscallId.SYS_HALT)}
                    i32.const 0
                    i32.const 0
                    i32.const 0
                    i32.const 0
                    i32.const 0
                    i32.const 0
                    call $fireball_call))"""
            )
        )
        wasi_ctx = WasiHostContext(sysv, guest_memory=bytearray(65536))
        interp = Interpreter(
            module,
            memory=wasi_ctx.guest_memory,
            host_functions=wasi_ctx.build_interpreter_host_functions(module),
            vmmio=sysv.vmmio,
            phys_mem=sysv.phys_mem,
        )
        result = interp.call(1, []).unwrap()
        assert result[0] == WasiErrno.SUCCESS
        assert sysv.halted

        # SYSCTL is not a vMMIO transport anymore; its old page is unmapped.
        status, _ = sysv.vmmio.access(0xC000_0000, is_write=False)
        assert status == TrapCode.UNREGISTERED_PAGE
    finally:
        sysv.shutdown()


def test_wasm_01_to_06_unsupported_features_rejected():
    """TEST-WASM-01..06: Unsupported features (SIMD, threads, tail-call) are rejected with error code."""
    # Module with unsupported SIMD opcode 0xFD
    wasm_bytes = (
        b"\x00asm\x01\x00\x00\x00"
        b"\x01\x05\x01\x60\x00\x01\x7f"
        b"\x03\x02\x01\x00"
        b"\x07\x0d\x01\x09test_simd\x00\x00"
        b"\x0a\x06\x01\x04\x00\xfd\x00\x0b"
    )
    with pytest.raises(InvalidWasm, match="ERR_WASM_UNSUPPORTED_FEATURE"):
        mod = parse(wasm_bytes)
        interp = Interpreter(mod)
        interp.call(0, []).unwrap()


def test_wasm_10_to_15_control_flow_and_calls():
    """TEST-WASM-10..15: Unreachable trap, block/loop/if/br_table, call, and call_indirect."""
    wat = """
    (module
      (table 2 2 funcref)
      (type $sig_calc (func (param i32) (result i32)))
      (func $unreachable_fn (export "unreachable_fn")
        (unreachable)
      )
      (func $calc_fn (export "calc_fn") (param $x i32) (result i32)
        (block $b0
          (block $b1
            (br_table $b1 $b0 (local.get $x))
          )
          (return (i32.const 100))
        )
        (return (i32.const 200))
      )
      (func $loop_exit (export "loop_exit") (result i32)
        (local $i i32)
        (block $exit (result i32)
          (loop $continue (result i32)
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (if (i32.eq (local.get $i) (i32.const 5))
              (then (br $exit (local.get $i))))
            (br $continue)
          )
        )
      )
      (func $call_ind (export "call_ind") (param $arg i32) (param $idx i32) (result i32)
        (call_indirect (type $sig_calc) (local.get $arg) (local.get $idx))
      )
      (elem (i32.const 0) $calc_fn $calc_fn)
    )
"""

    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_10_to_15")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    # TEST-WASM-10: unreachable traps
    outcome = interp.call(mod.export_func_index("unreachable_fn"), [])
    assert not outcome.is_ok
    assert outcome.error is not None and outcome.error.code == InterpreterTrapCode.UNREACHABLE
    # TEST-WASM-13: br_table branch resolution
    assert interp.call(mod.export_func_index("calc_fn"), [0]).unwrap() == [100]
    assert interp.call(mod.export_func_index("calc_fn"), [1]).unwrap() == [200]
    assert interp.call(mod.export_func_index("loop_exit"), []).unwrap() == [5]
    # TEST-WASM-15: call_indirect
    assert interp.call(mod.export_func_index("call_ind"), [0, 0]).unwrap() == [100]
    assert interp.call(mod.export_func_index("call_ind"), [1, 1]).unwrap() == [200]


def test_wasm_20_21_drop_and_select():
    """TEST-WASM-20..21: drop and select parametric instructions."""
    wat = """
    (module
      (func $sel (export "sel") (param $cond i32) (param $val1 i32) (param $val2 i32) (result i32)
        (drop (local.get $cond))
        (select (local.get $val1) (local.get $val2) (local.get $cond))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_20_21")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("sel"), [1, 10, 20]).unwrap() == [10]
    assert interp.call(mod.export_func_index("sel"), [0, 10, 20]).unwrap() == [20]


def test_wasm_30_31_locals_and_globals():
    """TEST-WASM-30..31: local.get/set/tee and global.get/set."""
    wat = """
    (module
      (global $g (mut i32) (i32.const 42))
      (func $loc_glob (export "loc_glob") (param $p0 i32) (result i32)
        (local $l1 i32)
        (local.set $l1 (local.get $p0))
        (global.set $g (local.get $l1))
        (i32.add (global.get $g) (local.get $l1))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_30_31")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    assert interp.call(mod.export_func_index("loc_glob"), [5]).unwrap() == [10]
    assert interp.globals[0] == 5


def test_wasm_40_to_46_memory_load_store_grow_and_data():
    """TEST-WASM-40..46 & TEST-WASM-60: Linear memory load, store, size, grow, bounds traps, and Data segments."""
    wat = """
    (module
      (memory 1 2)
      (data (i32.const 0) "WASM_INIT")
      (func $mem_ops (export "mem_ops") (result i32)
        (drop (i32.load (i32.const 0)))
        (i32.store (i32.const 16) (i32.const 0x12345678))
        (drop (memory.grow (i32.const 1)))
        (memory.size)
      )
      (func $trap_oob (export "trap_oob")
        (drop (i32.load (i32.const 0x1000000)))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_40_to_46")
        return
    mod = parse(wasm_bytes)
    mem = bytearray(65536)
    mod.init_memory_data(mem, ())
    interp = Interpreter(mod, memory=mem)
    # Initial data check
    assert bytes(mem[0:9]) == b"WASM_INIT"
    # Execution
    pages = interp.call(mod.export_func_index("mem_ops"), []).unwrap()
    assert pages == [2]
    assert struct.unpack_from("<I", mem, 16)[0] == 0x12345678

    native_mem = bytearray(65536)
    mod.init_memory_data(native_mem, ())
    native_interp = make_native_interpreter(mod, memory=native_mem)
    assert native_interp.call(mod.export_func_index("mem_ops"), []).unwrap() == [2]
    assert struct.unpack_from("<I", native_mem, 16)[0] == 0x12345678
    assert len(native_mem) == 2 * 65536

    # OOB trap check
    before = bytes(mem)
    outcome = interp.call(mod.export_func_index("trap_oob"), [])
    assert not outcome.is_ok
    assert (
        outcome.error is not None and outcome.error.code == InterpreterTrapCode.MEMORY_OUT_OF_BOUNDS
    )
    assert bytes(mem) == before


def test_wasm_50_to_56_integer_arithmetic_and_bitwise():
    """TEST-WASM-50..56: 32-bit integer arithmetic, div-by-zero trap, popcnt, clz, rotl, rotr."""
    wat = """
    (module
      (func $div_s (export "div_s") (param $a i32) (param $b i32) (result i32)
        (i32.div_s (local.get $a) (local.get $b))
      )
      (func $bit_ops (export "bit_ops") (param $x i32) (result i32)
        (i32.xor
          (i32.add (i32.popcnt (local.get $x)) (i32.clz (local.get $x)))
          (i32.rotl (local.get $x) (i32.const 4))
        )
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_50_to_56")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)
    # TEST-WASM-54: Div by zero traps
    outcome = interp.call(mod.export_func_index("div_s"), [10, 0])
    assert not outcome.is_ok
    assert (
        outcome.error is not None
        and outcome.error.code == InterpreterTrapCode.INTEGER_DIVIDE_BY_ZERO
    )
    # Normal div
    assert interp.call(mod.export_func_index("div_s"), [10, 2]).unwrap() == [5]
    # TEST-WASM-52, 55, 56: Bit ops
    # x = 0x80000001 -> popcnt=2, clz=0 -> sum=2. rotl(x, 4) = 0x00000018. 2 ^ 0x18 = 0x1A (26)
    assert interp.call(mod.export_func_index("bit_ops"), [0x80000001]).unwrap() == [26]


def test_wasm_f32_arithmetic_min_max_and_precision():
    """TEST-WASM-57: F32 single-precision rounding, IEEE 754 min/max with NaNs and signed zeroes."""
    import math

    wat = """
    (module
      (func $f32_add (export "f32_add") (param $a f32) (param $b f32) (result f32)
        (f32.add (local.get $a) (local.get $b))
      )
      (func $f32_min (export "f32_min") (param $a f32) (param $b f32) (result f32)
        (f32.min (local.get $a) (local.get $b))
      )
      (func $f32_max (export "f32_max") (param $a f32) (param $b f32) (result f32)
        (f32.max (local.get $a) (local.get $b))
      )
      (func $f32_demote (export "f32_demote") (param $d f64) (result f32)
        (f32.demote_f64 (local.get $d))
      )
    )
"""
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_wasm_f32_arithmetic")
        return
    mod = parse(wasm_bytes)
    interp = Interpreter(mod)

    # 1. Single precision rounding: 1.0 + 1e-8 in double is > 1.0, but in f32 it rounds to 1.0
    res = interp.call(mod.export_func_index("f32_add"), [1.0, 1e-8]).unwrap()
    assert res[0] == 1.0

    # 2. Signed zero min/max: min(-0.0, +0.0) must preserve -0.0
    res_min = interp.call(mod.export_func_index("f32_min"), [-0.0, 0.0]).unwrap()
    assert math.copysign(1.0, res_min[0]) == -1.0
    res_min_rev = interp.call(mod.export_func_index("f32_min"), [0.0, -0.0]).unwrap()
    assert math.copysign(1.0, res_min_rev[0]) == -1.0

    res_max = interp.call(mod.export_func_index("f32_max"), [-0.0, 0.0]).unwrap()
    assert math.copysign(1.0, res_max[0]) == 1.0
    res_max_rev = interp.call(mod.export_func_index("f32_max"), [0.0, -0.0]).unwrap()
    assert math.copysign(1.0, res_max_rev[0]) == 1.0

    # 3. NaN propagation
    res_nan = interp.call(mod.export_func_index("f32_min"), [float("nan"), 42.0]).unwrap()
    assert math.isnan(res_nan[0])
    res_nan2 = interp.call(mod.export_func_index("f32_max"), [42.0, float("nan")]).unwrap()
    assert math.isnan(res_nan2[0])

    # 4. f32.demote_f64 rounding
    demote_res = interp.call(mod.export_func_index("f32_demote"), [1.0000000000000002]).unwrap()
    assert demote_res[0] == 1.0


# ===========================================================================
# System Containers (TEST-CONT-01 .. TEST-CONT-10)
# ===========================================================================


def test_intp_70_to_72_direct_bytecode_execution():
    """TEST-INTP-18/70/72/75: frame ABI, loaded code/control metadata, and reference result."""
    wat = """
    (module
      (func (export "calc") (param $x i32) (result i32)
        (local $res i32)
        (local.set $res (i32.add (local.get $x) (i32.const 10)))
        (block $b
          (if (i32.gt_s (local.get $x) (i32.const 5))
            (then
              (local.set $res (i32.add (local.get $res) (i32.const 100)))
            )
            (else
              (local.set $res (i32.add (local.get $res) (i32.const 200)))
            )
          )
        )
        (local.get $res)
      )
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print("    [SKIP] wasmtime not installed, skipping test_intp_70_to_72")
        return
    module = parse(wasm_bytes)
    interp = Interpreter(module)

    # 1. TEST-INTP-18/75: independent call descriptors and local storage follow the native ABI.
    context = ExecutionContext(module)
    built = interp._build_frame(0, StaticVector.of((15,), capacity=64), context).unwrap()
    assert built is not None
    frame, locals_arr = built
    assert frame.code == module.code_for(0)
    assert frame.control_map is not None
    assert frame.control_map is module.functions[0].control_map
    assert context.call_frame_stack[-1] is frame
    assert context.call_stack == context.native_call_frames.address
    assert len(context.call_frame_stack) == 1
    native_frame = context.native_call_frames[-1]
    assert native_frame.func_index == 0
    assert native_frame.function_view == ctypes.addressof(frame._native_function_view)
    assert frame._native_function_view.code != 0
    assert frame._native_function_view.code_size == len(frame.code)
    assert native_frame.local_base == context.local_stack.value_ptr(frame.frame_offset).value
    # Two i32 locals: the frame uses 4-byte slots, so it occupies two raw words.
    assert frame.local_layout.total_words == 2
    assert context.local_stack_pointer == context.local_stack.value_ptr(2).value
    assert len(context.local_stack) == 2
    context.end_call_frame(frame)
    assert context.local_stack_pointer == context.local_stack_base
    assert len(context.call_frame_stack) == 0
    assert not context.native_call_frames
    assert len(context.local_stack) == 0

    # 2. TEST-INTP-71 & TEST-INTP-72: Execution proceeds by direct byte reading and ip addition
    res = interp.call(0, [15]).unwrap()
    # 15 + 10 + 100 = 125
    assert res == [125]

    res2 = interp.call(0, [3]).unwrap()
    # 3 + 10 + 200 = 213
    assert res2 == [213]


def test_wasm_mvp_packed_memory_and_i64_float_conversions():
    """MVP: cover every i64 packed memory opcode and i64/float conversion."""
    wat = r"""
    (module
      (memory 1)
      (data (i32.const 0) "\80\ff\34\12\78\56\34\12")
      (func (export "load8s") (result i64)
        (i64.load8_s (i32.const 0)))
      (func (export "load8u") (result i64)
        (i64.load8_u (i32.const 1)))
      (func (export "load16s") (result i64)
        (i64.load16_s (i32.const 2)))
      (func (export "load16u") (result i64)
        (i64.load16_u (i32.const 0)))
      (func (export "load32s") (result i64)
        (i64.load32_s (i32.const 4)))
      (func (export "load32u") (result i64)
        (i64.load32_u (i32.const 0)))
      (func (export "store8")
        (i64.store8 (i32.const 16) (i64.const -1)))
      (func (export "store16")
        (i64.store16 (i32.const 18) (i64.const 4660)))
      (func (export "store32")
        (i64.store32 (i32.const 20) (i64.const 305419896)))
      (func (export "trunc_f32_s") (param f32) (result i64)
        (i64.trunc_f32_s (local.get 0)))
      (func (export "trunc_f32_u") (param f32) (result i64)
        (i64.trunc_f32_u (local.get 0)))
      (func (export "trunc_f64_s") (param f64) (result i64)
        (i64.trunc_f64_s (local.get 0)))
      (func (export "trunc_f64_u") (param f64) (result i64)
        (i64.trunc_f64_u (local.get 0)))
      (func (export "f32_from_s") (param i64) (result f32)
        (f32.convert_i64_s (local.get 0)))
      (func (export "f32_from_u") (param i64) (result f32)
        (f32.convert_i64_u (local.get 0)))
      (func (export "f64_from_s") (param i64) (result f64)
        (f64.convert_i64_s (local.get 0)))
      (func (export "f64_from_u") (param i64) (result f64)
        (f64.convert_i64_u (local.get 0)))
    )
    """
    wasm_bytes = wat_to_wasm(wat)
    if not wasm_bytes:
        print(
            "    [SKIP] wasmtime not installed, skipping test_wasm_mvp_packed_memory_and_i64_float_conversions"
        )
        return
    module = parse(wasm_bytes)
    memory = bytearray(65536)
    module.init_memory_data(memory, ())
    interp = Interpreter(module, memory=memory)

    assert interp.call(module.export_func_index("load8s"), []).unwrap() == [-128]
    assert interp.call(module.export_func_index("load8u"), []).unwrap() == [255]
    assert interp.call(module.export_func_index("load16s"), []).unwrap() == [0x1234]
    assert interp.call(module.export_func_index("load16u"), []).unwrap() == [0xFF80]
    assert interp.call(module.export_func_index("load32s"), []).unwrap() == [0x12345678]
    assert interp.call(module.export_func_index("load32u"), []).unwrap() == [0x1234FF80]

    interp.call(module.export_func_index("store8"), []).unwrap()
    interp.call(module.export_func_index("store16"), []).unwrap()
    interp.call(module.export_func_index("store32"), []).unwrap()
    assert bytes(memory[16:24]) == b"\xff\x00\x34\x12\x78\x56\x34\x12"

    assert interp.call(module.export_func_index("trunc_f32_s"), [-3.75]).unwrap() == [-3]
    assert interp.call(module.export_func_index("trunc_f32_u"), [3.75]).unwrap() == [3]
    assert interp.call(module.export_func_index("trunc_f64_s"), [-3.75]).unwrap() == [-3]
    assert interp.call(module.export_func_index("trunc_f64_u"), [3.75]).unwrap() == [3]
    assert interp.call(module.export_func_index("f32_from_s"), [-7]).unwrap() == [-7.0]
    max_u64 = 0xFFFF_FFFF_FFFF_FFFF
    expected_max_u64_f32 = struct.unpack("<f", struct.pack("<f", float(max_u64)))[0]
    assert interp.call(module.export_func_index("f32_from_u"), [max_u64]).unwrap() == [
        expected_max_u64_f32
    ]
    assert interp.call(module.export_func_index("f64_from_s"), [-7]).unwrap() == [-7.0]
    assert interp.call(module.export_func_index("f64_from_u"), [max_u64]).unwrap() == [
        float(max_u64)
    ]

    outcome = interp.call(module.export_func_index("trunc_f32_s"), [float("nan")])
    assert not outcome.is_ok
    assert (
        outcome.error is not None and outcome.error.code == InterpreterTrapCode.INVALID_CONVERSION
    )
    outcome = interp.call(module.export_func_index("trunc_f64_u"), [-1.0])
    assert not outcome.is_ok
    assert (
        outcome.error is not None and outcome.error.code == InterpreterTrapCode.INVALID_CONVERSION
    )


def test_wasm_mvp_select_preserves_wide_operands():
    """MVP select copies both raw slots for i64/f64 values."""
    wat = """
    (module
      (func (export "pick_i64") (param i64 i64 i32) (result i64)
        (select (local.get 0) (local.get 1) (local.get 2)))
      (func (export "pick_f64") (param f64 f64 i32) (result f64)
        (select (local.get 0) (local.get 1) (local.get 2)))
      (func (export "sum_with_pick") (param f64 f64 f64 i32) (result f64)
        (f64.add
          (local.get 0)
          (select (local.get 1) (local.get 2) (local.get 3))))
    )
    """
    module = parse(wat_to_wasm(wat))
    interp = Interpreter(module)
    import math

    assert interp.call(module.export_func_index("pick_i64"), [-9, 17, 1]).unwrap() == [-9]
    assert interp.call(module.export_func_index("pick_i64"), [-9, 17, 0]).unwrap() == [17]
    selected_negative_zero = interp.call(
        module.export_func_index("pick_f64"), [-0.0, 4.5, 1]
    ).unwrap()
    assert selected_negative_zero == [-0.0]
    assert math.copysign(1.0, selected_negative_zero[0]) == -1.0
    assert interp.call(module.export_func_index("pick_f64"), [-0.0, 4.5, 0]).unwrap() == [4.5]
    assert interp.call(
        module.export_func_index("sum_with_pick"), [10.0, 1.25, 2.5, 0]
    ).unwrap() == [12.5]


def test_wasm_mvp_typed_global_initializers_and_access():
    """MVP globals preserve each numeric type's full raw value and width."""
    wat = """
    (module
      (global $g_i32 i32 (i32.const -7))
      (global $g_i64 (mut i64) (i64.const -1234567890123))
      (global $g_f32 f32 (f32.const -0))
      (global $g_f64 (mut f64) (f64.const 1.25))
      (func (export "get_i32") (result i32) (global.get $g_i32))
      (func (export "get_i64") (result i64) (global.get $g_i64))
      (func (export "set_i64") (param i64) (global.set $g_i64 (local.get 0)))
      (func (export "get_f32") (result f32) (global.get $g_f32))
      (func (export "get_f64") (result f64) (global.get $g_f64))
      (func (export "set_f64") (param f64) (global.set $g_f64 (local.get 0)))
    )
    """
    module = parse(wat_to_wasm(wat))
    interp = Interpreter(module)
    assert interp.call(module.export_func_index("get_i32"), []).unwrap() == [-7]
    assert interp.call(module.export_func_index("get_i64"), []).unwrap() == [-1234567890123]
    assert interp.call(module.export_func_index("get_f32"), []).unwrap() == [-0.0]
    import math

    f32_value = interp.call(module.export_func_index("get_f32"), []).unwrap()[0]
    assert math.copysign(1.0, f32_value) == -1.0
    assert interp.call(module.export_func_index("get_f64"), []).unwrap() == [1.25]
    interp.call(module.export_func_index("set_i64"), [0x123456789ABCDEF]).unwrap()
    assert interp.call(module.export_func_index("get_i64"), []).unwrap() == [0x123456789ABCDEF]
    interp.call(module.export_func_index("set_f64"), [-2.5]).unwrap()
    assert interp.call(module.export_func_index("get_f64"), []).unwrap() == [-2.5]


def test_native_interpreter_executes_typed_global_get_set():
    """C++ global handlers read and write the same typed storage as Python."""
    from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter

    module = parse(
        wat_to_wasm(
            """(module
              (global $g_i32 (mut i32) (i32.const -7))
              (global $g_i64 (mut i64) (i64.const -1234567890123))
              (global $g_f32 (mut f32) (f32.const -0))
              (global $g_f64 (mut f64) (f64.const 1.25))
              (func (export "get_i32") (result i32) (global.get $g_i32))
              (func (export "set_i32") (param i32) (global.set $g_i32 (local.get 0)))
              (func (export "get_i64") (result i64) (global.get $g_i64))
              (func (export "set_i64") (param i64) (global.set $g_i64 (local.get 0)))
              (func (export "get_f32") (result f32) (global.get $g_f32))
              (func (export "set_f32") (param f32) (global.set $g_f32 (local.get 0)))
              (func (export "get_f64") (result f64) (global.get $g_f64))
              (func (export "set_f64") (param f64) (global.set $g_f64 (local.get 0))))"""
        )
    )
    interpreter = NativeInterpreter(module, InterpreterBindings.empty())

    assert interpreter.call(module.export_func_index("get_i32"), []).unwrap() == [-7]
    assert interpreter.call(module.export_func_index("get_i64"), []).unwrap() == [-1234567890123]
    f32_initial = interpreter.call(module.export_func_index("get_f32"), []).unwrap()[0]
    assert f32_initial == -0.0
    import math

    assert math.copysign(1.0, f32_initial) == -1.0
    assert interpreter.call(module.export_func_index("get_f64"), []).unwrap() == [1.25]

    interpreter.call(module.export_func_index("set_i32"), [0x12345678]).unwrap()
    interpreter.call(module.export_func_index("set_i64"), [0x0123456789ABCDEF]).unwrap()
    interpreter.call(module.export_func_index("set_f32"), [3.25]).unwrap()
    interpreter.call(module.export_func_index("set_f64"), [-2.5]).unwrap()

    assert interpreter.call(module.export_func_index("get_i32"), []).unwrap() == [0x12345678]
    assert interpreter.call(module.export_func_index("get_i64"), []).unwrap() == [
        0x0123456789ABCDEF
    ]
    assert interpreter.call(module.export_func_index("get_f32"), []).unwrap() == [3.25]
    assert interpreter.call(module.export_func_index("get_f64"), []).unwrap() == [-2.5]
    assert interpreter.globals[0] == 0x12345678
    assert interpreter.globals[1] == 0x0123456789ABCDEF
    assert interpreter.globals[2] == struct.unpack("<I", struct.pack("<f", 3.25))[0]
    assert interpreter.globals[3] == struct.unpack("<Q", struct.pack("<d", -2.5))[0]


def test_wasm_mvp_imported_immutable_global_initializer():
    """global.get constant expressions resolve an imported immutable global."""
    wat = """
    (module
      (import "env" "seed" (global i64))
      (global $copy i64 (global.get 0))
      (func (export "get_copy") (result i64) (global.get $copy))
    )
    """
    module = parse(wat_to_wasm(wat))
    imported_globals = StaticVector.of((0xFEDC_BA98_7654_3210,), capacity=1)
    interp = Interpreter(module, imported_globals=imported_globals)
    assert interp.call(module.export_func_index("get_copy"), []).unwrap() == [
        -0x0123_4567_89AB_CDF0
    ]


def test_wasm_mvp_imported_linear_memory():
    """An imported MVP memory is the exact shared bytearray used by the module."""
    wat = """
    (module
      (import "env" "memory" (memory 1 2))
      (func (export "write") (i32.store (i32.const 8) (i32.const 0x12345678)))
      (func (export "read") (result i32) (i32.load (i32.const 8)))
    )
    """
    module = parse(wat_to_wasm(wat))
    memory = bytearray(65536)
    interp = Interpreter(
        module,
        memory=memory,
        imported_memory=Memory(min_pages=1, max_pages=2),
    )
    interp.call(module.export_func_index("write"), []).unwrap()
    assert memory[8:12] == b"\x78\x56\x34\x12"
    assert interp.call(module.export_func_index("read"), []).unwrap() == [0x12345678]


def test_wasm_mvp_imported_table_and_active_element_segment():
    """An active element segment initializes the shared imported funcref table."""
    wat = """
    (module
      (type $result_i32 (func (result i32)))
      (import "env" "table" (table 1 funcref))
      (func $answer (type $result_i32) (i32.const 42))
      (elem (i32.const 0) $answer)
      (func (export "call") (result i32)
        (call_indirect (type $result_i32) (i32.const 0)))
    )
    """
    module = parse(wat_to_wasm(wat))
    external_table = FunctionTable.from_sequence((None,))
    interp = Interpreter(
        module,
        imported_tables=StaticVector.of((external_table,), capacity=1),
    )
    assert interp.tables[0] is external_table
    assert external_table[0] == 0
    assert interp.call(module.export_func_index("call"), []).unwrap() == [42]
    with pytest.raises(AssertionError, match="immutable"):
        external_table[0] = None


@pytest.mark.parametrize("native", (False, True), ids=("python", "native"))
def test_call_indirect_accepts_unselected_targets_with_other_signatures(native: bool):
    """A valid selected target remains callable beside a differently typed table slot."""
    module = parse(
        wat_to_wasm(
            """
            (module
              (type $expected (func (result i32)))
              (type $other (func (result i64)))
              (table 2 funcref)
              (func $answer (type $expected) (i32.const 42))
              (func $other (type $other) (i64.const 7))
              (elem (i32.const 0) $answer $other)
              (func (export "call") (result i32)
                (call_indirect (type $expected) (i32.const 0))))
            """
        )
    )
    interpreter = make_native_interpreter(module) if native else Interpreter(module)
    assert interpreter.tables[0][0] == 0
    assert interpreter.tables[0][1] == 1
    assert interpreter.call(module.export_func_index("call"), []).unwrap() == [42]


@pytest.mark.parametrize("native", (False, True), ids=("python", "native"))
def test_call_indirect_reads_wide_function_index_from_table_slot_two(native: bool):
    """A u16 function index is loaded from each loader-owned u32 table slot."""
    module = parse(
        wat_to_wasm(
            """
            (module
              (type $narrow (func (result i32)))
              (type $wide_type (func (param i64) (result i32)))
              (table 3 funcref)
              (func $narrow (type $narrow) (i32.const 7))
              (func $wrong (result i64) (i64.const 9))
              (func $wide (param $value i64) (result i32)
                (i32.wrap_i64 (local.get $value)))
              (elem (i32.const 0) $narrow $wrong $wide)
              (func (export "call") (result i32)
                (call_indirect (type $wide_type) (i64.const 42) (i32.const 2)))
            )
            """
        )
    )
    interpreter = make_native_interpreter(module) if native else Interpreter(module)
    assert list(interpreter.tables[0]) == [0, 1, 2]
    assert interpreter.call(module.export_func_index("call"), []).unwrap() == [42]


@pytest.mark.parametrize("native", (False, True), ids=("python", "native"))
@pytest.mark.parametrize(
    ("expected", "actual", "arguments", "result"),
    (
        ("(result i32)", "(result i64)", "", "(i64.const 42)"),
        ("(param i32) (result i32)", "(param f32) (result i32)", "(i32.const 7)", "(i32.const 42)"),
        ("(param i32) (result i32)", "(param i64) (result i32)", "(i32.const 7)", "(i32.const 42)"),
        (
            "(param i32) (result i32)",
            "(param i32 i32) (result i32)",
            "(i32.const 7)",
            "(i32.const 42)",
        ),
        ("(result i32)", "", "", ""),
    ),
    ids=("result-type", "parameter-type", "parameter-width", "parameter-count", "result-count"),
)
def test_call_indirect_signature_mismatch_traps_before_entering_target(
    native: bool, expected: str, actual: str, arguments: str, result: str
):
    """A selected signature mismatch traps without running the target body."""
    wat = f"""
    (module
      (type $expected (func {expected}))
      (type $actual (func {actual}))
      (table 1 funcref)
      (global $entered (export "entered") (mut i32) (i32.const 0))
      (func $wrong (type $actual)
        (global.set $entered (i32.const 1)) {result})
      (elem (i32.const 0) $wrong)
      (func (export "call") (result i32)
        (call_indirect (type $expected) {arguments} (i32.const 0)))
    )
    """
    module = parse(wat_to_wasm(wat))

    interpreter = make_native_interpreter(module) if native else Interpreter(module)
    call = interpreter.start(module.export_func_index("call"), [])
    while not call.finished:
        call = interpreter.step(call)
    assert call.trap is not None
    assert call.trap.code == InterpreterTrapCode.INDIRECT_CALL_TYPE_MISMATCH
    assert interpreter.globals[0] == 0


@pytest.mark.parametrize("native", (False, True), ids=("python", "native"))
def test_call_indirect_import_signature_mismatch_never_invokes_host(native: bool):
    """Imported targets are checked at the host boundary before callback side effects."""
    module = parse(
        wat_to_wasm(
            """(module
              (type $expected (func (result i32)))
              (import "env" "wrong" (func $wrong (result i64)))
              (table 1 funcref)
              (elem (i32.const 0) $wrong)
              (func (export "call") (result i32)
                (call_indirect (type $expected) (i32.const 0))))"""
        )
    )
    received: StaticVector[int] = StaticVector(capacity=1)

    def wrong() -> int:
        received.append(1)
        return 42

    functions = StaticVector.of((wrong,), capacity=1)
    interpreter = (
        make_native_interpreter(module, host_functions=functions)
        if native
        else Interpreter(module, host_functions=functions)
    )
    call = interpreter.start(module.export_func_index("call"), [])
    while not call.finished:
        call = interpreter.step(call)
    assert call.trap is not None
    assert call.trap.code == InterpreterTrapCode.INDIRECT_CALL_TYPE_MISMATCH
    assert len(received) == 0


@pytest.mark.parametrize("native", (False, True), ids=("python", "native"))
def test_call_indirect_links_structurally_equal_duplicate_signatures(native: bool):
    """Distinct type indices with identical signatures remain compatible."""
    wat = """
    (module
      (type $expected (func (result i32)))
      (type $actual (func (result i32)))
      (table 1 funcref)
      (func $answer (type $actual) (i32.const 42))
      (elem (i32.const 0) $answer)
      (func (export "call") (result i32)
        (call_indirect (type $expected) (i32.const 0)))
    )
    """
    module = parse(wat_to_wasm(wat))
    interpreter = make_native_interpreter(module) if native else Interpreter(module)

    assert interpreter.call(module.export_func_index("call"), []).unwrap() == [42]


def test_runtime_arena_accounts_wasm_native_tables_and_reuses_call_stacks():
    """Runtime load and native execution buffers share one measured arena."""
    wat = """
    (module
      (type $result_i32 (func (result i32)))
      (table 1 funcref)
      (func $answer (type $result_i32) (i32.const 42))
      (elem (i32.const 0) $answer)
      (func (export "call") (result i32)
        (call_indirect (type $result_i32) (i32.const 0)))
    )
    """
    runtime = RuntimeEngine()
    module = runtime.load_wasm(wat_to_wasm(wat))
    loaded_high_water = runtime.bump_allocator.offset

    assert module.allocator is runtime.bump_allocator
    assert module.arena_size == loaded_high_water

    interpreter = make_native_interpreter(module)
    result = interpreter.call(module.export_func_index("call"), []).unwrap()
    first_call_high_water = runtime.bump_allocator.offset
    assert result == [42]
    assert first_call_high_water > loaded_high_water

    repeated_result = interpreter.call(module.export_func_index("call"), []).unwrap()
    assert repeated_result == [42]
    assert runtime.bump_allocator.offset == first_call_high_water


def test_wasm_mvp_host_function_arguments_keep_declared_types():
    """Imported callbacks receive signature-typed i64/f32/f64/i32 arguments."""
    wat = """
    (module
      (import "env" "mix" (func $mix (param i64 f32 f64 i32) (result f64)))
      (func (export "call") (result f64)
        (call $mix
          (i64.const 4294967297)
          (f32.const 1.5)
          (f64.const -2.25)
          (i32.const 4)))
    )
    """
    module = parse(wat_to_wasm(wat))
    received: StaticVector[WasmNumber] = StaticVector(capacity=4)

    def mix(i64_value: int, f32_value: float, f64_value: float, i32_value: int) -> float:
        received.append(i64_value)
        received.append(f32_value)
        received.append(f64_value)
        received.append(i32_value)
        return float(i64_value) + f32_value + f64_value + i32_value

    interp = Interpreter(module, host_functions=StaticVector.of((mix,), capacity=1))
    assert interp.call(module.export_func_index("call"), []).unwrap() == [4294967300.25]
    assert received == [4294967297, 1.5, -2.25, 4]

    native_received: StaticVector[WasmNumber] = StaticVector(capacity=4)

    def native_mix(i64_value: int, f32_value: float, f64_value: float, i32_value: int) -> float:
        native_received.append(i64_value)
        native_received.append(f32_value)
        native_received.append(f64_value)
        native_received.append(i32_value)
        return float(i64_value) + f32_value + f64_value + i32_value

    native_interp = make_native_interpreter(
        module,
        host_functions=StaticVector.of((native_mix,), capacity=1),
    )
    assert native_interp.call(module.export_func_index("call"), []).unwrap() == [4294967300.25]
    assert native_received == [4294967297, 1.5, -2.25, 4]


def test_python_runtime_api_uses_values_without_interpreter_state():
    """Host imports and memory management cross the API as WebAssembly values."""
    from tier2_runtime.runtime.python_api import HostFunction, PythonRuntimeAPI

    module = parse(
        memoryview(
            wat_to_wasm(
                """
            (module
              (import "env" "increment" (func $increment (param i32) (result i32)))
              (memory 1 2))
            """
            )
        )
    )
    memory = bytearray(65536)

    def increment(value: int) -> int:
        return value + 1

    host_functions: StaticVector[HostFunction | None] = StaticVector.of((increment,), capacity=1)
    api = PythonRuntimeAPI(
        module,
        memory,
        Memory(min_pages=1, max_pages=2),
        host_functions,
    )

    assert api.invoke_import(0, (0xFFFF_FFFF,)) == [0]
    assert api.memory_size() == 1
    assert api.grow_memory(1) == 1
    assert api.memory_size() == 2
    assert api.grow_memory(1) == -1
    assert api.memory_size() == 2


def test_intp_73_local_layout_precomputes_offsets_and_sizes():
    """TEST-INTP-73: load-time local tables pack each value into its word width."""
    from tier2_runtime.wasm.module import F32, LocalLayout

    assert LocalLayout(()).total_words == 0
    assert LocalLayout((I32, F32, I32)).total_words == 3
    assert LocalLayout((I32, I64), (I32,)).total_words == 4
    assert LocalLayout((F64,)).total_words == 2
    mixed = LocalLayout((I32, I64), (F32, F64))
    assert tuple(mixed.offset(i) for i in range(4)) == (0, 1, 3, 4)
    assert tuple(mixed.size_words(i) for i in range(4)) == (1, 2, 1, 2)
    assert mixed.total_words == 6

    cases = (
        ("(param i32 i32) (local i32)", (1, 1, 1), 3),
        ("(param f32)", (1,), 1),
        ("(param i32) (local f64)", (1, 2), 3),
        ("(param i64 i32)", (2, 1), 3),
        ("", (), 0),
    )
    for declarations, expected_sizes, expected_slots in cases:
        module = parse(memoryview(wat_to_wasm(f"(module (func {declarations}))")))
        function = module.functions[0]
        assert function.local_layout_cache is not None
        layout = function.local_layout_cache
        assert tuple(layout.size_words(i) for i in range(len(layout))) == expected_sizes
        assert tuple(layout.offset(i) for i in range(len(layout))) == tuple(
            sum(expected_sizes[:index]) for index in range(len(expected_sizes))
        )
        assert function.local_slot_count_cache == expected_slots

    narrow_module = parse(
        memoryview(
            wat_to_wasm(
                "(module (func (param i32 i32) (result i32) (local i32) "
                "local.get 0 local.get 1 i32.add))"
            )
        )
    )
    assert Interpreter(narrow_module).call(0, [30, 12]).unwrap() == [42]
    wide_module = parse(
        memoryview(
            wat_to_wasm("(module (func (param i32 i64) (result i64) (local i64) local.get 1))")
        )
    )
    assert Interpreter(wide_module).call(0, [1, 5_000_000_000]).unwrap() == [5_000_000_000]


def test_intp_74_wide_local_adds_only_its_own_word_to_the_frame():
    """TEST-INTP-74: a wide local adds one word without widening its siblings."""
    locals_i32 = " ".join(f"(local $v{i} i32)" for i in range(15))

    def recursion(extra: str) -> bytes:
        return wat_to_wasm(
            f"""
(module
  (func $rec (param $n i32) (result i32) {locals_i32} {extra}
    (if (result i32) (i32.eqz (local.get $n))
      (then (i32.const 0))
      (else (i32.add (i32.const 1) (call $rec (i32.sub (local.get $n) (i32.const 1)))))))
  (export "rec" (func $rec)))"""
        )

    local_stack_words = 128  # NATIVE_VALUE_STACK_CAPACITY
    narrow_module = parse(recursion(""))
    assert narrow_module.functions[0].local_slot_count_cache == 16
    # Sixteen i32 values per frame exactly fit eight active frames.
    assert 8 * 16 == local_stack_words
    assert Interpreter(narrow_module).call(0, [7]).unwrap() == [7]
    assert make_native_interpreter(narrow_module).call(0, [7]).unwrap() == [7]
    wide_module = parse(recursion("(local $w f64)"))
    assert wide_module.functions[0].local_slot_count_cache == 18
    # The f64 adds two words; eight frames then exceed the fixed 128-word region.
    assert 8 * 18 > local_stack_words
    outcome = make_native_interpreter(wide_module).call(0, [7])
    assert not outcome.is_ok
    assert (
        outcome.error is not None and outcome.error.code == InterpreterTrapCode.LOCAL_STACK_CAPACITY
    )


def test_runtime_keeps_constructor_arena_and_rejects_foreign_interpreter():
    """A rejected activation cannot change the runtime owner or execute guest stores."""
    from bump_allocator import BumpAllocator
    from tier2_runtime.runtime.engine import RuntimeEngine

    runtime = RuntimeEngine()
    module = runtime.load_wasm(
        wat_to_wasm("(module (memory 1) (func i32.const 0 i32.const 42 i32.store))")
    )
    memory = bytearray(b"\xa5" * 65536)
    interpreter = make_native_interpreter(module, memory=memory, bump_allocator=BumpAllocator())
    arena = runtime.bump_allocator
    watermark = arena.offset
    call_state = interpreter.start(0, ())
    with pytest.raises(AssertionError, match="same arena"):
        runtime.run(interpreter, call_state)
    assert runtime.bump_allocator is arena and arena.offset == watermark
    assert memory == b"\xa5" * 65536


@pytest.mark.parametrize("status", (-1, 0))
def test_vmmio_load_rejects_unknown_success_status(monkeypatch, status: int) -> None:
    from scheduler import Scheduler
    from tier2_runtime.interpreter.handlers import _vmmio_load
    from tier2_runtime.vmmio.controller import VMMIOController

    module = parse(wat_to_wasm("(module (memory 1) (func))"))
    controller = VMMIOController(guest_ram_size=65536, scheduler=Scheduler())
    interpreter = Interpreter(module, memory=bytearray(65536), vmmio=controller)
    monkeypatch.setattr(VMMIOController, "access", lambda *args, **kwargs: (status, 0))
    with pytest.raises(AssertionError, match="unexpected vMMIO success status"):
        _vmmio_load(interpreter._env, 0xC0000000, 4, True)


def test_vmmio_load_preserves_backing_and_missing_dynamic_contract() -> None:
    from scheduler import Scheduler
    from tier2_runtime.interpreter.handlers import _vmmio_load
    from tier2_runtime.vmmio.controller import Tier3PTE, VMMIOController

    scheduler = Scheduler()
    scheduler.current_task = scheduler.get_task(scheduler.spawn("reader"))
    controller = VMMIOController(guest_ram_size=65536, scheduler=scheduler)
    dynamic = bytearray(4096)
    dynamic[16:20] = (61).to_bytes(4, "little")
    controller.map_dynamic_page(0xD0000, 0, 1, memoryview(dynamic))
    controller.map_shm_page(0xE0000, phys_page=0, owner_id=1)
    physical = bytearray(4096)
    physical[16:20] = (37).to_bytes(4, "little")
    module = parse(wat_to_wasm("(module (memory 1) (func))"))
    interpreter = Interpreter(module, memory=bytearray(65536), vmmio=controller, phys_mem=physical)
    assert _vmmio_load(interpreter._env, 0xD0000010, 4, False) == (61, None)
    assert _vmmio_load(interpreter._env, 0xE0000010, 4, False) == (37, None)
    pte = controller.ptes.view().find(0xD0000)
    assert isinstance(pte, Tier3PTE)
    pte.mapped_storage = None
    with pytest.raises(AssertionError, match="DYNAMIC mappings require backing storage"):
        _vmmio_load(interpreter._env, 0xD0000010, 4, False)


# ===========================================================================
# Test Runner
# ===========================================================================


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("callee", [False, True])
def test_function_entry_capacity_precedes_traps_and_side_effects(native: bool, callee: bool):
    """Function admission may replace an earlier WASM trap only when capacity is insufficient."""
    wide = " ".join(["i64.const 0"] * 33 + ["drop"] * 33)
    body = f"i32.const 99 global.set 0 local.get 0 if unreachable else {wide} end"
    functions = f"(func (param i32) {body})"
    index = 0
    if callee:
        functions += "(func (param i32) local.get 0 call 0)"
        index = 1
    module = parse(wat_to_wasm(f"(module (global (mut i32) (i32.const 0)) {functions})"))
    engine = make_native_interpreter(module) if native else Interpreter(module)
    call = engine.start(index, (1,))
    while not call.finished:
        call = engine.step(call)
    assert call.trap is not None
    assert call.trap.code == InterpreterTrapCode.OPERAND_STACK_CAPACITY
    assert engine.globals[0] == 0
    assert not call.context.call_frame_stack


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("count", [31, 32])
def test_callee_capacity_uses_entry_sp_after_arguments(native: bool, count: int):
    """Caller live values occupy capacity; parameters become locals before callee admission."""
    wide = " ".join(["i64.const 0"] * count + ["drop"] * count)
    module = parse(
        wat_to_wasm(f"""(module
      (func (param i64) {wide})
      (func (result i64) i64.const 7 i64.const 1 call 0))""")
    )
    engine = make_native_interpreter(module) if native else Interpreter(module)
    call = engine.start(1, ())
    while not call.finished:
        call = engine.step(call)
    if count == 31:
        assert call.trap is None and tuple(call.results or ()) == (7,)
    else:
        assert call.trap is not None
        assert call.trap.code == InterpreterTrapCode.OPERAND_STACK_CAPACITY


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

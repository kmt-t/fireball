from __future__ import annotations

"""Tests for the native C++ CPS handler entry point."""

import ctypes
from pathlib import Path

import pytest
import tier2_runtime.interpreter.interpreter as interpreter_module
from qa.shared.helpers import make_native_interpreter, wat_to_wasm
from tier2_runtime.wasm.reader import parse_standalone

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[3]


from tier2_runtime.abi import native_abi
from tier2_runtime.abi.interpreter_abi import (
    CallFrameNative,
    ExecutionContextABI,
    FunctionExecutionViewNative,
    NativeValueStack,
)
from tier2_runtime.abi.native_stack_abi import NativeCallFrameStack
from tier2_runtime.interpreter.control_flow import build_interpreter_annotation_pool
from tier2_runtime.interpreter.interpreter import TrapCode
from tier2_runtime.wasm.module import LocalLayout


def _run_step(
    code: memoryview,
    context: ExecutionContextABI,
    stack: NativeValueStack,
    locals_stack: NativeValueStack,
    stack_size: int,
    stack_capacity: int,
    ip: int,
) -> tuple[int, int, int, int]:
    lease = native_abi.BufferLease(code)
    annotations, _ = build_interpreter_annotation_pool(code, (0,), local_layout=LocalLayout(()))
    try:
        function_view = FunctionExecutionViewNative()
        function_view.code_size = len(code)
        function_view.function_index = 0
        call_stack = NativeCallFrameStack(capacity=1)
        frame = CallFrameNative(
            function_view=ctypes.addressof(function_view),
            local_base=locals_stack.address,
            operand_stack_base=stack.address,
            return_ip=0xFFFF_FFFF,
            boundary_next_pc=0xFFFF_FFFF,
            boundary_loops_to=0xFFFF_FFFF,
        )
        assert_result_1 = call_stack.push_back(frame)
        assert assert_result_1
        context.call_stack = call_stack.address
        context.call_frame_base = call_stack.frame_address(0)
        return native_abi.run_step(
            native_abi.RUN_STEP,
            lease.address,
            lease.size,
            ctypes.addressof(context),
            ctypes.sizeof(context),
            stack.address,
            stack.native_bytes,
            locals_stack.address,
            locals_stack.native_bytes,
            stack_size,
            stack_capacity,
            ip,
            0,
            call=native_abi.NativeStepCall(),
            result=native_abi.NativeResult(),
            rel=annotations.native_address,
        )
    finally:
        lease.release()


def test_native_cps_entry_uses_shared_operand_stack() -> None:
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    context = ExecutionContextABI()
    code = memoryview(bytearray((0x41, 3, 0x41, 4, 0x6A, 0x0B)))
    status, next_ip, stack_size, trap_code = _run_step(
        code,
        context,
        stack,
        locals_stack,
        0,
        stack.capacity,
        0,
    )
    assert (status, next_ip, stack_size, trap_code) == (1, len(code), 1, 0)
    assert stack.raw_at(0) == 7
    assert context.code_size == len(code)
    assert context.ip == len(code)
    assert context.stack_pointer == stack.value_ptr(1).value


def test_native_shared_stack_keeps_values_across_handler_dispatch() -> None:
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    context = ExecutionContextABI()
    # ((3 + 4) * 2) + 5: every intermediate value remains in the shared stack.
    code = memoryview(bytearray((0x41, 3, 0x41, 4, 0x6A, 0x41, 2, 0x6C, 0x41, 5, 0x6A, 0x0B)))
    outcome = _run_step(code, context, stack, locals_stack, 0, stack.capacity, 0)
    assert outcome == (1, len(code), 1, 0)
    assert stack.raw_at(0) == 19


def _run_guarded_const(
    code: bytes, stack_capacity: int, stack_size: int
) -> tuple[tuple[int, int, int, int], ExecutionContextABI, tuple[int, ...], tuple[int, ...]]:
    """Observe all physical words, including those beyond the lowered usable capacity."""
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    context = ExecutionContextABI()
    for index in range(stack.capacity):
        stack.write_raw_at(index, 0xA5A50000 + index)
    before = tuple(stack.raw_at(index) for index in range(stack.capacity))
    outcome = _run_step(
        memoryview(code),
        context,
        stack,
        locals_stack,
        stack_size,
        stack_capacity,
        0,
    )
    after = tuple(stack.raw_at(index) for index in range(stack.capacity))
    return outcome, context, before, after


@pytest.mark.parametrize(
    "constant, raw_words",
    [
        pytest.param(b"\x41\x7f", (0xFFFFFFFF,), id="i32-minus-one"),
        pytest.param(b"\x42\x7f", (0xFFFFFFFF, 0xFFFFFFFF), id="i64-minus-one"),
        pytest.param(b"\x43\x00\x00\x00\x80", (0x80000000,), id="f32-negative-zero"),
        pytest.param(
            b"\x44\x00\x00\x00\x00\x00\x00\x00\x80", (0, 0x80000000), id="f64-negative-zero"
        ),
    ],
)
@pytest.mark.parametrize("stack_capacity, free_words", [(0, 0), (1, 1), (4, 0), (4, 1), (4, 2)])
def test_native_const_capacity_traps_without_partial_push(
    constant: bytes,
    raw_words: tuple[int, ...],
    stack_capacity: int,
    free_words: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST-INTP-10: Const capacity failure is a specific trap with no partial or adjacent writes."""
    initial_size = stack_capacity - free_words
    if free_words >= len(raw_words):
        # A raw host entry must already satisfy the function's capacity contract.
        code = b"\x01\x01" + constant + b"\x0b"
        outcome, context, before, after = _run_guarded_const(code, stack_capacity, initial_size)
        assert outcome == (1, len(code), initial_size + len(raw_words), 0)
        assert context.stack_pointer == context.stack_base + (initial_size + len(raw_words)) * 4
        expected = list(before)
        expected[initial_size : initial_size + len(raw_words)] = raw_words
        assert after == tuple(expected)
        return

    # Capacity failure is now tested at the real validated function entry.
    expressions = {
        b"\x41\x7f": "i32.const -1",
        b"\x42\x7f": "i64.const -1",
        b"\x43\x00\x00\x00\x80": "f32.const -0",
        b"\x44\x00\x00\x00\x00\x00\x00\x00\x80": "f64.const -0",
    }
    expression = expressions[constant]
    result_type = expression.split(".")[0]
    module = parse_standalone(
        wat_to_wasm(f"(module (func (result {result_type}) nop nop {expression}))")
    )
    context = interpreter_module.ExecutionContext(module)
    context.stack._capacity = stack_capacity
    context.stack.set_size(initial_size)
    for index in range(stack_capacity):
        context.stack.write_raw_at(index, 0xA5A50000 + index)
    before = tuple(context.stack.raw_at(index) for index in range(stack_capacity))
    monkeypatch.setattr(interpreter_module.NativeInterpreter, "_new_context", lambda self: context)
    call = make_native_interpreter(module).start(0, ())
    assert call.finished and call.trap is not None
    assert call.trap.code == TrapCode.OPERAND_STACK_CAPACITY
    assert not context.call_frame_stack
    assert tuple(context.stack.raw_at(index) for index in range(stack_capacity)) == before


ALL_TESTS = sorted(
    (value for name, value in globals().items() if name.startswith("test_") and callable(value)),
    key=lambda function: function.__code__.co_firstlineno,
)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))


@pytest.fixture(scope="module")
def release_dispatcher(
    tmp_path_factory: pytest.TempPathFactory,
) -> native_abi.NativeDispatchEntryPoint:
    """Build NDEBUG code separately so the checked product library stays untouched."""
    import subprocess
    import sys

    directory = tmp_path_factory.mktemp("native-release")
    library = directory / ("release.dll" if sys.platform == "win32" else "librelease.so")
    source_dir = _PYSIM_DIR / "native/tier2_runtime/interpreter"
    subprocess.run(
        [
            "clang++",
            "-std=c++23",
            "-O2",
            "-DNDEBUG",
            "-shared",
            "-fPIC",
            "-fno-exceptions",
            "-fno-rtti",
            str(source_dir / "native_interpreter.cxx"),
            str(source_dir / "native_wasm_operations.cxx"),
            "-o",
            str(library),
        ],
        check=True,
    )
    loaded = ctypes.PyDLL(str(library))
    entry = loaded.fb_native_run_dispatch
    entry.argtypes = (ctypes.c_void_p, ctypes.POINTER(native_abi.NativeResult))
    entry.restype = ctypes.c_int
    return entry


@pytest.mark.parametrize("operation", ("copy", "fill"))
def test_release_bulk_memory_preserves_annotation_cursor(
    release_dispatcher: native_abi.NativeDispatchEntryPoint, operation: str
) -> None:
    """TEST-INTP-79: NDEBUG preserves the annotation cursor and following instruction."""
    from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter

    operands = (
        "i32.const 8 i32.const 0 i32.const 4"
        if operation == "copy"
        else "i32.const 8 i32.const 42 i32.const 4"
    )
    module = parse_standalone(
        wat_to_wasm(
            f'(module (memory 1) (func (export "run") (result i32) '
            f"{operands} memory.{operation} i32.const 42))"
        )
    )
    memory = bytearray(65536)
    memory[:4] = b"data"
    interpreter = NativeInterpreter.with_module_allocator(
        module, InterpreterBindings.with_memory(memory), native_dispatcher=release_dispatcher
    )
    assert interpreter.call(0, []).unwrap() == [42]
    assert memory[8:12] == (b"data" if operation == "copy" else b"****")
    assert memory[:4] == b"data"
    assert memory[12:16] == b"\x00" * 4

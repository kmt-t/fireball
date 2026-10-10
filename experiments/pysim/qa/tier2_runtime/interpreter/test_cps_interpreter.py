from __future__ import annotations

"""Tests for the native C++ CPS handler entry point."""

import ctypes
from pathlib import Path

import pytest

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[3]


from tier2_runtime.abi import native_abi
from tier2_runtime.abi.interpreter_abi import (
    CallFrameNative,
    ExecutionContextABI,
    FunctionExecutionViewNative,
    NativeValueStack,
)
from tier2_runtime.abi.native_stack_abi import NativeCallFrameStack, NativeControlStack
from tier2_runtime.interpreter.control_flow import build_interpreter_constant_pool
from tier2_runtime.interpreter.interpreter import TrapCode


def _run_step(
    code: memoryview,
    context: ExecutionContextABI,
    stack: NativeValueStack,
    locals_stack: NativeValueStack,
    control_stack: NativeControlStack,
    stack_size: int,
    stack_capacity: int,
    ip: int,
) -> tuple[int, int, int, int]:
    lease = native_abi.BufferLease(code)
    constants, _ = build_interpreter_constant_pool(code, (0,))
    constant_pool = (ctypes.c_uint8 * (4 * len(constants)))()
    for index, value in enumerate(constants):
        ctypes.memmove(
            ctypes.addressof(constant_pool) + 4 * index,
            value.to_bytes(4, "little"),
            4,
        )
    try:
        function_view = FunctionExecutionViewNative()
        function_view.code_size = len(code)
        call_stack = NativeCallFrameStack(capacity=1)
        frame = CallFrameNative(
            function_view=ctypes.addressof(function_view),
            local_base=locals_stack.address,
            control_frame_base=control_stack.frame_address(0),
            func_index=0,
            return_ip=0xFFFF_FFFF,
            boundary_next_pc=0xFFFF_FFFF,
            boundary_loops_to=0xFFFF_FFFF,
        )
        assert call_stack.push_back(frame)
        context.call_stack = call_stack.address
        context.call_frame_base = call_stack.frame_address(0)
        control_stack.bind_stack_base(stack.address)
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
            control_stack.address,
            control_stack.native_bytes,
            stack_size,
            stack_capacity,
            ip,
            0,
            control_stack.frame_address(0),
            call=native_abi.NativeStepCall(),
            result=native_abi.NativeResult(),
            rel=(0 if len(constants) == 0 else ctypes.addressof(constant_pool)),
        )
    finally:
        lease.release()


def test_native_cps_entry_uses_shared_operand_stack() -> None:
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    control_stack = NativeControlStack()
    context = ExecutionContextABI()
    code = memoryview(bytearray((0x41, 3, 0x41, 4, 0x6A, 0x0B)))
    status, next_ip, stack_size, trap_code = _run_step(
        code,
        context,
        stack,
        locals_stack,
        control_stack,
        0,
        stack.capacity,
        0,
    )
    assert (status, next_ip, stack_size, trap_code) == (1, len(code), 1, 0)
    assert stack.raw_at(0) == 7
    assert context.code_size == len(code)
    assert context.control_stack != 0
    assert context.control_frame_base == control_stack.frame_address(0)
    assert context.stack_checkpoint == stack.value_ptr(1).value
    assert context.ip == len(code)
    assert context.stack_pointer == stack.value_ptr(1).value
    assert context.control_frame_base == control_stack.frame_address(0)


def test_native_shared_stack_keeps_values_across_handler_dispatch() -> None:
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    control_stack = NativeControlStack()
    context = ExecutionContextABI()
    # ((3 + 4) * 2) + 5: every intermediate value remains in the shared stack.
    code = memoryview(bytearray((0x41, 3, 0x41, 4, 0x6A, 0x41, 2, 0x6C, 0x41, 5, 0x6A, 0x0B)))
    outcome = _run_step(code, context, stack, locals_stack, control_stack, 0, stack.capacity, 0)
    assert outcome == (1, len(code), 1, 0)
    assert stack.raw_at(0) == 19


def _run_guarded_const(
    code: bytes, stack_capacity: int, stack_size: int
) -> tuple[tuple[int, int, int, int], ExecutionContextABI, tuple[int, ...], tuple[int, ...]]:
    """Observe all physical words, including those beyond the lowered usable capacity."""
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    control_stack = NativeControlStack()
    context = ExecutionContextABI()
    for index in range(stack.capacity):
        stack.write_raw_at(index, 0xA5A50000 + index)
    before = tuple(stack.raw_at(index) for index in range(stack.capacity))
    outcome = _run_step(
        memoryview(code),
        context,
        stack,
        locals_stack,
        control_stack,
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
    constant: bytes, raw_words: tuple[int, ...], stack_capacity: int, free_words: int
) -> None:
    """TEST-INTP-10: Const capacity failure is a specific trap with no partial or adjacent writes."""
    initial_size = stack_capacity - free_words
    code = b"\x01\x01" + constant + b"\x0b"  # Trap after two CPS continuations.
    outcome, context, before, after = _run_guarded_const(code, stack_capacity, initial_size)
    if free_words < len(raw_words):
        assert outcome == (2, 2, initial_size, TrapCode.OPERAND_STACK_CAPACITY)
        assert context.ip == 2
        assert context.stack_pointer == context.stack_base + initial_size * 4
        assert after == before, "failed push must preserve every existing and sentinel word"
    else:
        assert outcome == (1, len(code), initial_size + len(raw_words), 0)
        assert context.ip == len(code)
        assert context.stack_pointer == context.stack_base + (initial_size + len(raw_words)) * 4
        expected = list(before)
        expected[initial_size : initial_size + len(raw_words)] = raw_words
        assert after == tuple(expected), (
            "successful push must write exactly its declared word width"
        )


ALL_TESTS = sorted(
    (value for name, value in globals().items() if name.startswith("test_") and callable(value)),
    key=lambda function: function.__code__.co_firstlineno,
)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

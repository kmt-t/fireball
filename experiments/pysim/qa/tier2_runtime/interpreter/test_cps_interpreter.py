from __future__ import annotations

"""Tests for the native C++ CPS handler entry point."""

import ctypes
from pathlib import Path

import pytest

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[3]


from tier2_runtime.abi import native_abi
from tier2_runtime.abi.interpreter_abi import ExecutionContextABI, NativeValueStack
from tier2_runtime.abi.native_stack_abi import NativeControlStack
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
    try:
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
            0,
            call=native_abi.NativeStepCall(),
            result=native_abi.NativeResult(),
        )
    finally:
        lease.release()


def test_native_cps_entry_uses_four_logical_arguments() -> None:
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
    assert context.control_base == 0
    assert context.stack_checkpoint == 1
    assert context.ip == len(code)
    assert context.sp_offset == 1
    assert context.control_base == 0


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
        1,
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
    code = b"\x01" + constant + b"\x0b"  # Start at the const, after a prefix nop.
    outcome, context, before, after = _run_guarded_const(code, stack_capacity, initial_size)
    if free_words < len(raw_words):
        assert outcome == (2, 1, initial_size, TrapCode.OPERAND_STACK_CAPACITY)
        assert context.ip == 1
        assert context.sp_offset == initial_size
        assert after == before, "failed push must preserve every existing and sentinel word"
    else:
        assert outcome == (1, len(code), initial_size + len(raw_words), 0)
        assert context.ip == len(code)
        assert context.sp_offset == initial_size + len(raw_words)
        expected = list(before)
        expected[initial_size : initial_size + len(raw_words)] = raw_words
        assert after == tuple(expected), (
            "successful push must write exactly its declared word width"
        )


@pytest.mark.parametrize(
    "incomplete_or_unsupported",
    [
        pytest.param(b"\x41\x80", id="truncated-i32"),
        pytest.param(b"\x42\x80", id="truncated-i64"),
        pytest.param(b"\x43\x00\x00\x00", id="truncated-f32"),
        pytest.param(b"\x44\x00\x00\x00\x00\x00\x00\x00", id="truncated-f64"),
        pytest.param(b"\xff", id="unsupported-opcode"),
    ],
)
@pytest.mark.parametrize("free_words", [0, 2])
def test_native_invalid_const_is_not_misclassified_as_capacity_trap(
    incomplete_or_unsupported: bytes, free_words: int
) -> None:
    """TEST-INTP-06/10: Decoder/unsupported fallback remains distinct from valid push overflow."""
    initial_size = 4 - free_words
    code = b"\x01" + incomplete_or_unsupported
    outcome, context, before, after = _run_guarded_const(code, 4, initial_size)
    assert outcome == (0, 1, initial_size, 0)
    assert context.ip == 1
    assert context.sp_offset == initial_size
    assert after == before


ALL_TESTS = sorted(
    (value for name, value in globals().items() if name.startswith("test_") and callable(value)),
    key=lambda function: function.__code__.co_firstlineno,
)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

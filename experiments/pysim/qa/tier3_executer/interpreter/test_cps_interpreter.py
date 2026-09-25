from __future__ import annotations

"""Tests for the native C++ CPS handler entry point."""

from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[3]


from interop_abi import ExecutionContextNative, NativeValueStack
from native_stacks import NativeControlStack
from tier3_executer.interpreter import _interpreter_native


def test_native_cps_entry_uses_four_logical_arguments() -> None:
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    control_stack = NativeControlStack()
    context = ExecutionContextNative()
    code = memoryview(bytearray((0x41, 3, 0x41, 4, 0x6A, 0x0B)))
    status, next_ip, stack_size, trap_code = _interpreter_native.run_step(
        code,
        memoryview(context),
        stack.raw_view,
        locals_stack.raw_view,
        control_stack.raw_view,
        0,
        stack.capacity,
        0,
        0,
        0,
    )
    assert (status, next_ip, stack_size, trap_code) == (1, 0xFFFF_FFFF, 1, 0)
    assert stack.raw_at(0) == 7
    assert context.code_size == len(code)
    assert context.control_stack != 0
    assert context.control_base == 0
    assert context.stack_checkpoint == 1
    assert context.ip == len(code)
    assert context.sp_offset == 1
    assert context.cf_offset == 0


ALL_TESTS = sorted(
    (value for name, value in globals().items() if name.startswith("test_") and callable(value)),
    key=lambda function: function.__code__.co_firstlineno,
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"[PASS] All {len(ALL_TESTS)} native C++ CPS tests passed.")

"""Tests for the Python mirror of the C++ interpreter/JIT Native ABI."""

from __future__ import annotations

import ctypes
from pathlib import Path

from qa.shared.helpers import expect_assertion
from tier2_runtime.abi.interpreter_abi import (
    NATIVE_STACK_ALIGNMENT_BYTES,
    CallFrameNative,
    CallStackNative,
    ExecutionContextABI,
    NativeValueStack,
    ValueStackNative,
)
from tier2_runtime.abi.native_abi import NativeResult
from tier2_runtime.abi.native_stack_abi import (
    LocalStackWindow,
)
from tier2_runtime.interpreter.interpreter import ExecutionContext
from tier2_runtime.wasm.module import F32, F64, I32, I64, LocalLayout


def _resolve_two_result_block(start: int) -> tuple[bool, int, int, int]:
    assert start == 0
    return False, 4, 2, 2


def test_native_layout_matches_x64_jit_context():
    assert ctypes.sizeof(ExecutionContextABI) == 144
    assert ExecutionContextABI.ip.offset == 0x00
    assert ExecutionContextABI.stack_base.offset == 0x08
    assert ExecutionContextABI.stack_pointer.offset == 0x10
    assert ExecutionContextABI.stack_limit.offset == 0x18
    assert ExecutionContextABI.local_stack_base.offset == 0x20
    assert ExecutionContextABI.local_stack_pointer.offset == 0x28
    assert ExecutionContextABI.local_stack_limit.offset == 0x30
    assert ExecutionContextABI.runtime_flags.offset == 0x38
    assert ExecutionContextABI.code_size.offset == 0x3C
    assert ExecutionContextABI.code.offset == 0x40
    assert ExecutionContextABI.call_stack.offset == 0x48
    assert ExecutionContextABI.call_frame_base.offset == 0x50
    assert ExecutionContextABI.loop_jump_count.offset == 0x58
    assert ExecutionContextABI.loop_jump_threshold.offset == 0x5C
    assert ExecutionContextABI.linear_memory_host_base.offset == 0x60
    assert ExecutionContextABI.linear_memory_size.offset == 0x68
    assert ExecutionContextABI.trap_code.offset == 0x70
    assert ExecutionContextABI.local_base.offset == 0x78
    assert ExecutionContextABI.rel.offset == 0x80
    assert ExecutionContextABI.current_block_index.offset == 0x88
    assert ctypes.sizeof(CallFrameNative) == 40
    assert CallFrameNative.return_block_index.offset == 36
    assert ctypes.sizeof(CallStackNative) == 1288
    assert CallStackNative.size.offset == 1280

    ctx = ExecutionContextABI()
    ctx.ip = 0x1234
    ctx.linear_memory_size = 0x4000
    assert ctx.ip == 0x1234
    assert ctx.linear_memory_size == 0x4000


def test_product_dispatch_result_contains_only_execution_state():
    """The result ABI carries status and error only; execution state stays in context."""
    assert ctypes.sizeof(NativeResult) == 8
    assert NativeResult.status.offset == 0
    assert NativeResult.error_code.offset == 4
    result = ExecutionContext()._native_result
    assert ctypes.sizeof(result) == 8


def test_product_dispatch_asserts_on_invalid_entry():
    """An invalid dispatch request is an internal contract violation."""
    import os
    import signal
    import subprocess
    import sys

    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        str(Path(path or ".").resolve()) for path in sys.path
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctypes; from tier2_runtime.abi.native_abi import RUN_DISPATCH, NativeResult; "
            "RUN_DISPATCH(None, ctypes.byref(NativeResult()))",
        ],
        capture_output=True,
        env=environment,
        check=False,
    )
    assert process.returncode == -signal.SIGABRT


def test_execution_context_extends_abi_record():
    context = ExecutionContext()

    assert isinstance(context, ExecutionContextABI)
    assert context.context_ptr.value == ctypes.addressof(context)
    assert context.debug_control is None
    assert ctypes.sizeof(context) == (
        ctypes.sizeof(ExecutionContextABI) + ctypes.sizeof(ctypes.c_void_p)
    )


def test_native_value_stack_owns_the_fixed_storage():
    assert ctypes.sizeof(ValueStackNative) == 520
    assert ValueStackNative.size.offset == 512
    stack = NativeValueStack(capacity=2)
    assert isinstance(stack.native, ValueStackNative)
    assert stack.push_i32(-1)
    assert stack.push_f32(1.5)
    pushed = stack.push_back(3)
    assert not pushed
    assert stack.native.size == 2
    assert stack.value_ptr().value % NATIVE_STACK_ALIGNMENT_BYTES == 0
    assert stack.read_i32(0) == -1
    assert stack.read_f32(1) == 1.5
    assert stack.value_ptr().value == ctypes.addressof(stack.native.values)
    assert stack.pop_f32() == 1.5
    assert stack.pop_i32() == -1
    with expect_assertion():
        stack.pop_back()

    wide_stack = NativeValueStack(capacity=4)
    assert wide_stack.push_i64(-1)
    assert wide_stack.native.size == 2
    assert wide_stack.push_f64(1.25)
    assert wide_stack.native.size == 4
    assert wide_stack.pop_f64() == 1.25
    assert wide_stack.pop_i64() == -1

    typed_stack = NativeValueStack(capacity=8)
    assert typed_stack.push_i32(-1)
    assert typed_stack.push_i64(-2)
    assert typed_stack.push_f32(1.25)
    assert typed_stack.push_f64(2.5)
    assert typed_stack.peek_f64() == 2.5
    peek_stack = NativeValueStack(capacity=1)
    assert peek_stack.push_f32(1.25)
    assert peek_stack.peek_f32() == 1.25
    i32_peek_stack = NativeValueStack(capacity=1)
    assert i32_peek_stack.push_i32(-7)
    assert i32_peek_stack.peek_i32() == -7
    i64_peek_stack = NativeValueStack(capacity=2)
    assert i64_peek_stack.push_i64(-8)
    assert i64_peek_stack.peek_i64() == -8
    assert typed_stack.read_i64(1) == -2
    assert typed_stack.read_f64(4) == 2.5
    typed_stack.write_i32(0, 7)
    typed_stack.write_i64(1, 9)
    typed_stack.write_f32(3, 3.5)
    typed_stack.write_f64(4, 4.5)
    assert typed_stack.read_i32(0) == 7
    assert typed_stack.read_i64(1) == 9
    assert typed_stack.read_f32(3) == 3.5
    assert typed_stack.read_f64(4) == 4.5
    del typed_stack[0]
    typed_stack.clear()
    assert not typed_stack


def test_local_stack_window_uses_packed_offsets_and_typed_accessors():
    storage = NativeValueStack(capacity=8)
    assert storage.extend((0, 0, 0, 0, 0, 0, 0, 0))
    window = LocalStackWindow(storage, base=0, layout=LocalLayout((I32, I64, F32, F64)))
    assert len(window) == 4
    assert window.raw_slot(2) == 3
    assert window.raw_width(1) == 2
    window.set_i32(0, -3)
    window.set_i64(1, -4)
    window.set_f32(2, 1.5)
    window.set_f64(3, 2.5)
    assert window.get_i32(0) == -3
    assert window.get_i64(1) == -4
    assert window.get_f32(2) == 1.5
    assert window.get_f64(3) == 2.5


if __name__ == "__main__":
    test_native_layout_matches_x64_jit_context()
    test_execution_context_extends_abi_record()
    test_native_value_stack_owns_the_fixed_storage()
    test_local_stack_window_uses_packed_offsets_and_typed_accessors()
    print("[PASS] test_interpreter_abi")

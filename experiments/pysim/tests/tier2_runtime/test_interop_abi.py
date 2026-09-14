"""Tests for the Python mirror of the C++ interpreter/JIT Native ABI."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[2]
_REPO_ROOT = _PYSIM_DIR.parent.parent
for _path in (
    _PYSIM_DIR,
    _PYSIM_DIR / "tests",
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier2_runtime",
    _REPO_ROOT,
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from execution_context import WASMContext
from helpers import expect_assertion
from interop_abi import (
    NATIVE_STACK_ALIGNMENT_BYTES,
    ConstBufferViewNative,
    ControlStackNative,
    ExecutionContextNative,
    NativeValueStack,
    ValueStackNative,
    WasmFunctionViewNative,
    WasmModuleViewNative,
    WasmRunRequestNative,
    WasmRunResultNative,
)
from interpreter import ControlFrameKind, InterpreterContext, NativeControlStack
from native_stacks import LocalStackWindow


def test_native_layout_matches_x64_jit_context():
    assert ctypes.sizeof(ExecutionContextNative) == 152
    assert ExecutionContextNative.mem_base.offset == 0x28
    assert ExecutionContextNative.handler_table.offset == 0x38
    assert ExecutionContextNative.reserved0.offset == 0x3C
    assert ExecutionContextNative.jit_helper_ptrs.offset == 0x40

    ctx = ExecutionContextNative()
    ctx.ip = 0x1234
    ctx.mem_size = 0x4000
    ctx.jit_helper_ptrs[0] = 0x0123_4567_89AB_CDEF
    ctx.jit_helper_ptrs[10] = 0x0FED_CBA9_8765_4321
    assert ctx.ip == 0x1234
    assert ctx.mem_size == 0x4000
    assert ctx.jit_helper_ptrs[0] == 0x0123_4567_89AB_CDEF
    assert ctx.jit_helper_ptrs[10] == 0x0FED_CBA9_8765_4321


def test_native_views_are_non_owning_fixed_width_records():
    code = ctypes.create_string_buffer(b"\x41\x01")
    code_view = ConstBufferViewNative(
        data=ctypes.addressof(code),
        size=2,
    )
    function = WasmFunctionViewNative(code=code_view, type_index=3, locals_count=5)
    functions = (WasmFunctionViewNative * 1)(function)
    module = WasmModuleViewNative(
        function_table=ctypes.addressof(functions),
        function_count=1,
        imported_function_count=0,
        start_function=0xFFFF_FFFF,
        flags=0,
    )
    context = ExecutionContextNative()
    request = WasmRunRequestNative(
        module_view=ctypes.addressof(module),
        execution_context=ctypes.addressof(context),
        arguments=0,
        results=0,
        function_index=0,
        argument_count=0,
        result_capacity=0,
        max_blocks=16,
    )
    result = WasmRunResultNative(status=0, fault_code=0, value_count=0, results=0)

    assert code_view.data == ctypes.addressof(code)
    assert function.code.size == 2
    assert module.function_table == ctypes.addressof(functions)
    assert request.module_view == ctypes.addressof(module)
    assert request.max_blocks == 16
    assert result.status == 0


def test_interpreter_and_jit_contexts_share_native_record_type():
    interpreter_context = InterpreterContext()
    jit_context = WASMContext()
    memory_context = WASMContext(memory=bytearray(8))

    assert isinstance(interpreter_context.native_context, ExecutionContextNative)
    assert isinstance(jit_context.native_context, ExecutionContextNative)
    assert interpreter_context.context_ptr.value == ctypes.addressof(
        interpreter_context.native_context
    )
    assert jit_context.context_ptr.value == ctypes.addressof(jit_context.native_context)
    assert memory_context.mem_ptr.value != 0
    helper_addresses = tuple(range(1, 1 + len(memory_context.jit_helper_ptrs)))
    memory_context.set_jit_helpers(helper_addresses)
    assert memory_context.jit_helper_ptrs == helper_addresses
    memory_context.clear_jit_helper()
    assert memory_context.jit_helper_ptrs == (0,) * len(helper_addresses)


def test_native_value_stack_owns_the_fixed_storage():
    assert ctypes.sizeof(ValueStackNative) == 520
    assert ValueStackNative.size.offset == 512
    stack = NativeValueStack(capacity=2)
    assert isinstance(stack.native, ValueStackNative)
    assert stack.push_i32(-1)
    assert stack.push_f32(1.5)
    assert not stack.push_back(3)
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


def test_native_control_stack_owns_flat_frame_records():
    stack = NativeControlStack(capacity=2)
    assert stack.capacity == 2
    assert stack.push_back(ControlFrameKind.LOOP, start=3, match_end=12, stack_height=4)
    assert stack.native.size == 1
    assert isinstance(stack.native, ControlStackNative)
    restored = stack[-1]
    assert restored.kind == int(ControlFrameKind.LOOP)
    assert (restored.start, restored.match_end, restored.stack_height) == (3, 12, 4)
    assert stack.pop_back().kind == int(ControlFrameKind.LOOP)
    assert stack.push_back(ControlFrameKind.BLOCK, start=4, match_end=8, stack_height=0)
    stack.truncate(0)
    assert len(stack) == 0
    with expect_assertion():
        stack.pop_back()


def test_runtime_contexts_expose_native_stack_records():
    interpreter_context = InterpreterContext()
    jit_context = WASMContext()
    assert isinstance(interpreter_context.operand_stack, NativeValueStack)
    assert isinstance(interpreter_context.local_stack, NativeValueStack)
    assert isinstance(interpreter_context.control_frame_stack, NativeControlStack)
    assert isinstance(jit_context.stack, NativeValueStack)


def test_local_stack_window_uses_fixed_slot_offsets_and_typed_accessors():
    storage = NativeValueStack(capacity=8)
    assert storage.extend((0, 0, 0, 0, 0, 0, 0, 0))
    window = LocalStackWindow(storage, base=0, widths=(1, 2, 1, 2), slot_count=8)
    assert len(window) == 4
    assert window.raw_slot(2) == 4
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
    test_native_views_are_non_owning_fixed_width_records()
    test_interpreter_and_jit_contexts_share_native_record_type()
    test_native_value_stack_owns_the_fixed_storage()
    test_native_control_stack_owns_flat_frame_records()
    test_runtime_contexts_expose_native_stack_records()
    test_local_stack_window_uses_fixed_slot_offsets_and_typed_accessors()
    print("[PASS] test_interop_abi")

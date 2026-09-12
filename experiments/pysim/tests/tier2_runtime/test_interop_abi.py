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
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier2_runtime",
    _REPO_ROOT,
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from execution_context import WASMContext
from interop_abi import (
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


def test_native_layout_matches_x64_jit_context():
    assert ctypes.sizeof(ExecutionContextNative) == 72
    assert ExecutionContextNative.mem_base.offset == 0x28
    assert ExecutionContextNative.handler_table.offset == 0x38
    assert ExecutionContextNative.reserved0.offset == 0x3C
    assert ExecutionContextNative.complex_helper_ptr.offset == 0x40

    ctx = ExecutionContextNative()
    ctx.ip = 0x1234
    ctx.mem_size = 0x4000
    ctx.complex_helper_ptr = 0x0123_4567_89AB_CDEF
    assert ctx.ip == 0x1234
    assert ctx.mem_size == 0x4000
    assert ctx.complex_helper_ptr == 0x0123_4567_89AB_CDEF


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

    assert isinstance(interpreter_context.native_context, ExecutionContextNative)
    assert isinstance(jit_context.native_context, ExecutionContextNative)
    assert interpreter_context.context_ptr.value == ctypes.addressof(
        interpreter_context.native_context
    )
    assert jit_context.context_ptr.value == ctypes.addressof(jit_context.native_context)


def test_native_value_stack_owns_the_fixed_storage():
    assert ctypes.sizeof(ValueStackNative) == 264
    assert ValueStackNative.size.offset == 256
    stack = NativeValueStack(capacity=2)
    assert isinstance(stack.native, ValueStackNative)
    assert stack.push_i32(-1)
    assert stack.push_f32(1.5)
    assert not stack.push_back(3)
    assert stack.native.size == 2
    assert stack.read_i32(0) == -1
    assert stack.read_f32(1) == 1.5
    assert stack.value_ptr().value == ctypes.addressof(stack.native.values)
    assert stack.pop_f32() == 1.5
    assert stack.pop_i32() == -1
    try:
        stack.pop_back()
    except AssertionError:
        pass
    else:
        raise AssertionError("empty Native value stack must fail fast")

    wide_stack = NativeValueStack(capacity=4)
    assert wide_stack.push_i64(-1)
    assert wide_stack.native.size == 2
    assert wide_stack.push_f64(1.25)
    assert wide_stack.native.size == 4
    assert wide_stack.pop_f64() == 1.25
    assert wide_stack.pop_i64() == -1


def test_native_control_stack_owns_flat_frame_records():
    stack = NativeControlStack(capacity=2)
    assert stack.push_back(ControlFrameKind.LOOP, start=3, match_end=12, stack_height=4)
    assert stack.native.size == 1
    assert isinstance(stack.native, ControlStackNative)
    restored = stack[-1]
    assert restored.kind == int(ControlFrameKind.LOOP)
    assert (restored.start, restored.match_end, restored.stack_height) == (3, 12, 4)
    assert stack.pop_back().kind == int(ControlFrameKind.LOOP)
    try:
        stack.pop_back()
    except IndexError:
        pass
    else:
        raise AssertionError("empty Native control stack must fail fast")


def test_runtime_contexts_expose_native_stack_records():
    interpreter_context = InterpreterContext()
    jit_context = WASMContext()
    assert isinstance(interpreter_context.operand_stack, NativeValueStack)
    assert isinstance(interpreter_context.local_stack, NativeValueStack)
    assert isinstance(interpreter_context.control_frame_stack, NativeControlStack)
    assert isinstance(jit_context.stack, NativeValueStack)


if __name__ == "__main__":
    test_native_layout_matches_x64_jit_context()
    test_native_views_are_non_owning_fixed_width_records()
    test_interpreter_and_jit_contexts_share_native_record_type()
    test_native_value_stack_owns_the_fixed_storage()
    test_native_control_stack_owns_flat_frame_records()
    test_runtime_contexts_expose_native_stack_records()
    print("[PASS] test_interop_abi")

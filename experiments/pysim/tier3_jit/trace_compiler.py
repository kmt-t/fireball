"""Tier 3 JIT trace compiler for the reference simulator.

This module owns trace materialization and the emulated CPS callable used by
the Tier 2 runtime when the native x64 compiler is not selected.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from jit_cache import JITTrace
from system_containers import ReadOnlyFlatMapStorage
from wasm_module import WASM_LOCAL_SLOT_WORDS, TraceBlock, WasmOperand
from control_flow import iter_block_ops
from wasm_opcodes import (
    I32_ADD,
    I32_CONST,
    I32_MUL,
    I32_SUB,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)


NativeLocals = ctypes.POINTER(ctypes.c_uint32) | None


def _emu_i32_const(stk: list[int], _arr: NativeLocals, arg: WasmOperand) -> None:
    assert arg is not None
    stk.append(arg)


def _emu_i32_add(stk: list[int], _arr: NativeLocals, _arg: WasmOperand) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a + b) & 0xFFFF_FFFF)


def _emu_i32_sub(stk: list[int], _arr: NativeLocals, _arg: WasmOperand) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a - b) & 0xFFFF_FFFF)


def _emu_i32_mul(stk: list[int], _arr: NativeLocals, _arg: WasmOperand) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a * b) & 0xFFFF_FFFF)


def _emu_local_get(stk: list[int], arr: NativeLocals, arg: WasmOperand) -> None:
    assert arg is not None
    index = arg * WASM_LOCAL_SLOT_WORDS
    stk.append((arr[index] if arr else 0) & 0xFFFF_FFFF)


def _emu_local_set(stk: list[int], arr: NativeLocals, arg: WasmOperand) -> None:
    assert arg is not None
    val = stk.pop() & 0xFFFF_FFFF
    if arr:
        arr[arg * WASM_LOCAL_SLOT_WORDS] = val


def _emu_local_tee(stk: list[int], arr: NativeLocals, arg: WasmOperand) -> None:
    assert arg is not None
    val = stk[-1] & 0xFFFF_FFFF if stk else 0
    if arr:
        arr[arg * WASM_LOCAL_SLOT_WORDS] = val


_EMU_TRACE_STORAGE: ReadOnlyFlatMapStorage[int, Callable[[list[int], NativeLocals, WasmOperand], None]] = (
    ReadOnlyFlatMapStorage.create(
        [
            (I32_CONST, _emu_i32_const),
            (I32_ADD, _emu_i32_add),
            (I32_SUB, _emu_i32_sub),
            (I32_MUL, _emu_i32_mul),
            (LOCAL_GET, _emu_local_get),
            (LOCAL_SET, _emu_local_set),
            (LOCAL_TEE, _emu_local_tee),
        ]
    )
)
class WASMTraceCompiler:
    """Compiles a loader-owned BasicBlock into a callable native JITTrace."""

    __slots__ = ()

    def compile_trace(self, head_pc: int, block: TraceBlock) -> JITTrace | None:
        assert block.code is not None
        has_ret = False
        for op, _arg in iter_block_ops(block.code, block.head_offset, block.byte_span):
            if op == I32_CONST or op == I32_ADD or op == I32_SUB or op == I32_MUL:
                has_ret = True

        def trace_fn(ctx: int, sp: int, local_base: int, tos: int) -> None:
            # Emulated handler matching CPS 4-argument C signature (ctx, sp, local_base, tos).
            # A trace's residual value is VM operand-stack state, not a C return
            # value ({ExecutionContext_Layout}): written to `sp` (mirroring
            # x64_jit.py's SPILL_RESULT_TO_SP) instead of returned.
            c_arr = ctypes.cast(local_base, ctypes.POINTER(ctypes.c_uint32)) if local_base else None
            stk: list[int] = [tos] if tos else []
            for op, arg in iter_block_ops(block.code, block.head_offset, block.byte_span):
                handler = _EMU_TRACE_STORAGE.view().find(op)
                if handler is not None:
                    handler(stk, c_arr, arg)
            if stk and sp:
                ctypes.cast(sp, ctypes.POINTER(ctypes.c_uint32))[0] = stk[-1] & 0xFFFF_FFFF

        c_fn = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )(trace_fn)
        trace = JITTrace(
            head_pc=head_pc,
            fn=c_fn,
            size_bytes=block.byte_span * 4,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            has_return_val=has_ret,
        )
        trace._keepalive = c_fn
        return trace

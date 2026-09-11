"""Tier 3 JIT trace compiler for the reference simulator.

This module owns trace materialization and the emulated CPS callable used by
the Tier 2 runtime when the native x64 compiler is not selected.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from jit_cache import JITTrace
from system_containers import FlatMapView, ReadOnlyFlatMapStorage, StaticVector
from wasm_module import TraceBlock
from wasm_opcodes import (
    I32_ADD,
    I32_CONST,
    I32_MUL,
    I32_SUB,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)


def _emu_i32_const(stk: list[int], _arr: object, arg: object) -> None:
    stk.append(int(arg))  # type: ignore[arg-type]


def _emu_i32_add(stk: list[int], _arr: object, _arg: object) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a + b) & 0xFFFF_FFFF)


def _emu_i32_sub(stk: list[int], _arr: object, _arg: object) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a - b) & 0xFFFF_FFFF)


def _emu_i32_mul(stk: list[int], _arr: object, _arg: object) -> None:
    b, a = stk.pop(), stk.pop()
    stk.append((a * b) & 0xFFFF_FFFF)


def _emu_local_get(stk: list[int], arr: object, arg: object) -> None:
    stk.append((arr[arg] if arr else 0) & 0xFFFF_FFFF)  # type: ignore[index]


def _emu_local_set(stk: list[int], arr: object, arg: object) -> None:
    val = stk.pop() & 0xFFFF_FFFF
    if arr:
        arr[arg] = val  # type: ignore[index]


def _emu_local_tee(stk: list[int], arr: object, arg: object) -> None:
    val = stk[-1] & 0xFFFF_FFFF if stk else 0
    if arr:
        arr[arg] = val  # type: ignore[index]


_EMU_TRACE_STORAGE: ReadOnlyFlatMapStorage[int, Callable[[list[int], object, object], None]] = (
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
_EMU_TRACE_MAP: FlatMapView[int, Callable[[list[int], object, object], None]] = (
    _EMU_TRACE_STORAGE.view()
)


class WASMTraceCompiler:
    """Compiles a TraceBlock op stream into a fast callable native JITTrace using table dispatch."""

    __slots__ = ()

    def compile_trace(self, head_pc: int, block: TraceBlock) -> JITTrace | None:
        # `ops` outlives this call, captured by `trace_fn` below for every
        # future invocation of the returned JITTrace, so it needs a fixed
        # capacity: `block.byte_span` (each op is at least 1 byte, so it can
        # never hold more ops than that).
        ops: StaticVector[tuple[int, object]] = StaticVector(capacity=block.byte_span)
        for op, arg in block.ops:
            if not ops.push_back((op, arg)):
                return None
        has_ret = any(op in (I32_CONST, I32_ADD, I32_SUB, I32_MUL) for op, _ in ops)

        def trace_fn(ip: int, stack_bot: object, local_base: object, tos: int) -> int:
            # Emulated handler matching CPS 4-argument C signature (ip, stack_bot, local_base, tos).
            # A trace's residual value is VM operand-stack state, not a C return
            # value ({ExecutionContext_Layout}): written to `stack_bot` (mirroring
            # x64_jit.py's SPILL_RESULT_TO_STACK_BOT) instead of returned.
            c_arr = ctypes.cast(local_base, ctypes.POINTER(ctypes.c_int64)) if local_base else None
            stk: list[int] = [tos] if tos else []
            for op, arg in ops:
                handler = _EMU_TRACE_MAP.find(op)
                if handler is not None:
                    handler(stk, c_arr, arg)
            if stk and stack_bot:
                ctypes.cast(stack_bot, ctypes.POINTER(ctypes.c_int64))[0] = stk[-1]
            return 0

        c_fn = ctypes.CFUNCTYPE(
            ctypes.c_int64,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )(trace_fn)
        trace = JITTrace(
            head_pc=head_pc,
            fn=c_fn,
            size_bytes=len(ops) * 4,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            has_return_val=has_ret,
        )
        trace._keepalive = c_fn
        return trace


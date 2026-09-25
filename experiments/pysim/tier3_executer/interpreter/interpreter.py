"""
experiments/pysim/tier3_executer/interpreter/interpreter.py
A minimal reference interpreter for the wasm_opcodes subset, used as the
correctness oracle the JIT's output is checked against -- mirroring the
real project's own "interpreter + JIT, cross-checked" architecture
(docs/components/tier3_executer/interpreter.md /
docs/components/tier3_executer/jit_compiler.md), just without the ARM/Copy-and-
Patch specifics.
Execution model: `docs/specs/wasm_instruction_set.md` §1 mandates a real
**threaded interpreter** (`{ThreadedInterpreter}`). Every handler in this
file receives the same `(ctx, sp, local_base, tos)` state and returns a
continuation with an optional trap. A return handler publishes the named
`RETURN_SENTINEL_IP`; the handler result is never absent.
`_HANDLERS` table stores those functions directly; there is no CPS adapter or
central switch/if-elif loop around them.
The one adaptation from the literal ARM/native design: native code tail-
calls the next handler directly (or dispatches via a jump table with no
return address at all), which Python cannot do without unbounded
recursion depth for long-running loops. So the four-argument continuation
is *returned* rather than tail-called, and a small trampoline in `_run`
re-dispatches it -- indirect threading instead of direct threading, same
handler-per-opcode shape, no stack growth per WASM instruction.
`R1: sp` addresses, in the real design, a fixed buffer shared by two
independently-growing stacks (ADR-INTERP-03, interpreter.md §3.1):
call frames/locals/operand values grow from the bottom, while block/loop/if
control frames grow from the top in a dedicated region of their own --
never interleaved with the operand values, since a JIT-resolved loop/block
exit never pops a control frame at all, and letting the two share one
growing region would let that leave operand-stack addressing corrupted.
`InterpreterContext` owns the three fixed-capacity runtime stacks. A
`CallFrame` is only an activation descriptor and a window into the
context-owned OperandStack, LocalStack, and control-frame stack; it does not
own a per-function stack. The descriptor also carries the decoded instruction
table needed to fetch the next instruction from `ip`, without adding a fifth
handler argument.
"""

from __future__ import annotations

import ctypes
import math
import struct
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from config import FB_CONF_MAX_VALUE_STACK, FB_CONF_RUNTIME_YIELD_THRESHOLD
from control_flow import (
    FB_CONF_MAX_NESTING_DEPTH,
    OpcodeAttribute,
    build_control_map,
    opcode_has_attribute,
)
from interop_abi import (
    EXECUTION_CONTEXT_FLAG_STOP_AT_BLOCK_BOUNDARY,
    NATIVE_VALUE_STACK_CAPACITY,
    CallFrameNative,
    ControlMapEntryNative,
    ExecutionContextNative,
    NativeValueStack,
)
from jit_runtime_contract import NativeBlockVisit, NativeTraceDispatchEntry
from leb128 import decode_signed, decode_unsigned
from native_stacks import (
    ControlFrameKind,
    NativeCallFrameStack,
    NativeControlStack,
    _ControlFrameWindow,
    _LocalStackWindow,
)
from system_containers import StaticVector
from tier2_runtime.logger import Logger, LogLevel
from vmmio import VMMIOController, VmmioStatus
from wasm_module import (
    F32,
    F64,
    I32,
    I64,
    Memory,
    Module,
    value_slot_width,
)
from wasm_opcodes import (
    BLOCK,
    BR,
    BR_IF,
    BR_TABLE,
    CALL,
    CALL_INDIRECT,
    DROP,
    ELSE,
    END,
    F32_ABS,
    F32_ADD,
    F32_CEIL,
    F32_CONST,
    F32_CONVERT_I32_S,
    F32_CONVERT_I32_U,
    F32_CONVERT_I64_S,
    F32_CONVERT_I64_U,
    F32_COPYSIGN,
    F32_DEMOTE_F64,
    F32_DIV,
    F32_EQ,
    F32_FLOOR,
    F32_GE,
    F32_GT,
    F32_LE,
    F32_LOAD,
    F32_LT,
    F32_MAX,
    F32_MIN,
    F32_MUL,
    F32_NE,
    F32_NEAREST,
    F32_NEG,
    F32_REINTERPRET_I32,
    F32_SQRT,
    F32_STORE,
    F32_SUB,
    F32_TRUNC,
    F64_ABS,
    F64_ADD,
    F64_CEIL,
    F64_CONST,
    F64_CONVERT_I32_S,
    F64_CONVERT_I32_U,
    F64_CONVERT_I64_S,
    F64_CONVERT_I64_U,
    F64_COPYSIGN,
    F64_DIV,
    F64_EQ,
    F64_FLOOR,
    F64_GE,
    F64_GT,
    F64_LE,
    F64_LOAD,
    F64_LT,
    F64_MAX,
    F64_MIN,
    F64_MUL,
    F64_NE,
    F64_NEAREST,
    F64_NEG,
    F64_PROMOTE_F32,
    F64_REINTERPRET_I64,
    F64_SQRT,
    F64_STORE,
    F64_SUB,
    F64_TRUNC,
    GLOBAL_GET,
    GLOBAL_SET,
    I32_ADD,
    I32_AND,
    I32_CLZ,
    I32_CONST,
    I32_CTZ,
    I32_DIV_S,
    I32_DIV_U,
    I32_EQ,
    I32_EQZ,
    I32_EXTEND8_S,
    I32_EXTEND16_S,
    I32_GE_S,
    I32_GE_U,
    I32_GT_S,
    I32_GT_U,
    I32_LE_S,
    I32_LE_U,
    I32_LOAD,
    I32_LOAD8_S,
    I32_LOAD8_U,
    I32_LOAD16_S,
    I32_LOAD16_U,
    I32_LT_S,
    I32_LT_U,
    I32_MUL,
    I32_NE,
    I32_OR,
    I32_POPCNT,
    I32_REINTERPRET_F32,
    I32_REM_S,
    I32_REM_U,
    I32_ROTL,
    I32_ROTR,
    I32_SHL,
    I32_SHR_S,
    I32_SHR_U,
    I32_STORE,
    I32_STORE8,
    I32_STORE16,
    I32_SUB,
    I32_TRUNC_F32_S,
    I32_TRUNC_F32_U,
    I32_TRUNC_F64_S,
    I32_TRUNC_F64_U,
    I32_WRAP_I64,
    I32_XOR,
    I64_ADD,
    I64_AND,
    I64_CLZ,
    I64_CONST,
    I64_CTZ,
    I64_DIV_S,
    I64_DIV_U,
    I64_EQ,
    I64_EQZ,
    I64_EXTEND8_S,
    I64_EXTEND16_S,
    I64_EXTEND32_S,
    I64_EXTEND_I32_S,
    I64_EXTEND_I32_U,
    I64_GE_S,
    I64_GE_U,
    I64_GT_S,
    I64_GT_U,
    I64_LE_S,
    I64_LE_U,
    I64_LOAD,
    I64_LOAD8_S,
    I64_LOAD8_U,
    I64_LOAD16_S,
    I64_LOAD16_U,
    I64_LOAD32_S,
    I64_LOAD32_U,
    I64_LT_S,
    I64_LT_U,
    I64_MUL,
    I64_NE,
    I64_OR,
    I64_POPCNT,
    I64_REINTERPRET_F64,
    I64_REM_S,
    I64_REM_U,
    I64_ROTL,
    I64_ROTR,
    I64_SHL,
    I64_SHR_S,
    I64_SHR_U,
    I64_STORE,
    I64_STORE8,
    I64_STORE16,
    I64_STORE32,
    I64_SUB,
    I64_TRUNC_F32_S,
    I64_TRUNC_F32_U,
    I64_TRUNC_F64_S,
    I64_TRUNC_F64_U,
    I64_XOR,
    IF,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
    LOOP,
    MEMORY_GROW,
    MEMORY_SIZE,
    NOP,
    RETURN,
    SELECT,
    UNREACHABLE,
)

from . import _interpreter_native

NATIVE_RUNTIME_PROFILE_STATS_ENABLED = bool(
    _interpreter_native.RUNTIME_PROFILE_STATS_ENABLED
)
NATIVE_JIT_HOTSPOT_PROFILING_ENABLED = bool(
    _interpreter_native.JIT_HOTSPOT_PROFILING_ENABLED
)

I32_MASK = 0xFFFFFFFF
PAGE_SIZE = 65536
# Reserved execution-PC values.  `-1` is kept only in the local continuation
# because it cannot be confused with a bytecode offset; the public unified PC
# is a named reserved value consumed by RuntimeEngine before normal lookup.
RETURN_SENTINEL_IP = -1
RETURN_SENTINEL_PC = 0xFFFF_FFFF
NATIVE_DISPATCH_YIELD = 5
NATIVE_DISPATCH_OLDEST_TRACE = 4


def _to_i32(v: int) -> int:
    v &= I32_MASK
    return v - (1 << 32) if v & 0x8000_0000 else v


def _to_u32(v: int) -> int:
    return v & I32_MASK


def _to_f32(v: float) -> float:
    """Rounds a Python float (double) to IEEE 754 single-precision float32."""
    try:
        return struct.unpack("<f", struct.pack("<f", float(v)))[0]
    except OverflowError:
        return math.copysign(float("inf"), v)


def _wasm_float_div(a: float, b: float) -> float:
    if b != 0.0:
        return a / b
    if a == 0.0 or math.isnan(a):
        return float("nan")
    sign = math.copysign(1.0, a) * math.copysign(1.0, b)
    return math.copysign(float("inf"), sign)


def _wasm_nearest(value: float) -> float:
    if math.isnan(value) or math.isinf(value) or value == 0.0:
        return value
    if -0.5 <= value <= 0.5:
        return math.copysign(0.0, value)
    return float(round(value))


def _wasm_integral_round(value: float, operation: str) -> float:
    if math.isnan(value) or math.isinf(value) or value == 0.0:
        return value
    if operation == "floor":
        result = math.floor(value)
    elif operation == "ceil":
        result = math.ceil(value)
    else:
        assert operation == "trunc"
        result = math.trunc(value)
    if result == 0 and value < 0.0:
        return math.copysign(0.0, -1.0)
    return float(result)


def _f32_min(a: float, b: float) -> float:
    """WASM f32.min IEEE 754 semantics: NaN propagation and signed zero."""
    if math.isnan(a) or math.isnan(b):
        return float("nan")
    if a == 0.0 and b == 0.0:
        return a if math.copysign(1.0, a) < 0.0 else b
    return a if a < b else b


def _f32_max(a: float, b: float) -> float:
    """WASM f32.max IEEE 754 semantics: NaN propagation and signed zero."""
    if math.isnan(a) or math.isnan(b):
        return float("nan")
    if a == 0.0 and b == 0.0:
        return a if math.copysign(1.0, a) > 0.0 else b
    return a if a > b else b


class TrapCode(IntEnum):
    LOCAL_STACK_CAPACITY = 1
    CALL_FRAME_CAPACITY = 2
    CALL_STACK_CAPACITY = 3
    OPERAND_STACK_CAPACITY = 4
    NO_HOST_HANDLER = 5
    TABLE_INDEX_OUT_OF_BOUNDS = 6
    TABLE_SLOT_UNINITIALIZED = 7
    INDIRECT_CALL_TYPE_MISMATCH = 8
    UNREACHABLE = 9
    CONTROL_FRAME_CAPACITY = 10
    VMMIO_NOT_CONFIGURED = 11
    VMMIO_ACCESS = 12
    MEMORY_OUT_OF_BOUNDS = 13
    MEMORY_SECTION_MISSING = 14
    INTEGER_DIVIDE_BY_ZERO = 15
    INTEGER_OVERFLOW = 16
    INVALID_CONVERSION = 17


class Trap(Exception):
    __slots__ = ("code", "detail")

    def __init__(self, code: TrapCode, detail: int = 0):
        self.code = code
        self.detail = detail


# Interpreter Runtime Trap Diagnostic Log Events (runtime_logging.md 4.2.1, GOTCHA-LOG-04).
# Event ID = LOG_EVT_TRAP_BASE + TrapCode value, so every TrapCode member maps to exactly
# one dictionary entry computed from the single enum, with no separately maintained ID
# table that could drift out of sync (verification-antipatterns.md pattern E).
LOG_EVT_TRAP_BASE = 0x0300

TRAP_LOG_EVENTS: tuple[tuple[int, str], ...] = (
    (
        LOG_EVT_TRAP_BASE + TrapCode.LOCAL_STACK_CAPACITY,
        "TRAP: local stack capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.CALL_FRAME_CAPACITY,
        "TRAP: call frame capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.CALL_STACK_CAPACITY,
        "TRAP: call stack capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.OPERAND_STACK_CAPACITY,
        "TRAP: operand stack capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.NO_HOST_HANDLER,
        "TRAP: import has no bound host handler (pc=0x%08X, func=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.TABLE_INDEX_OUT_OF_BOUNDS,
        "TRAP: table index out of bounds (pc=0x%08X, slot=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.TABLE_SLOT_UNINITIALIZED,
        "TRAP: table slot uninitialized (pc=0x%08X, slot=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.INDIRECT_CALL_TYPE_MISMATCH,
        "TRAP: indirect call type mismatch (pc=0x%08X, slot=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.UNREACHABLE,
        "TRAP: unreachable instruction executed (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.CONTROL_FRAME_CAPACITY,
        "TRAP: control frame capacity exceeded (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.VMMIO_NOT_CONFIGURED,
        "TRAP: vMMIO region not configured (pc=0x%08X, addr=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.VMMIO_ACCESS,
        "TRAP: vMMIO access rejected (pc=0x%08X, status=%d)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.MEMORY_OUT_OF_BOUNDS,
        "TRAP: linear memory access out of bounds (pc=0x%08X, addr=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.MEMORY_SECTION_MISSING,
        "TRAP: memory access without a declared memory section (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.INTEGER_DIVIDE_BY_ZERO,
        "TRAP: integer divide by zero (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.INTEGER_OVERFLOW,
        "TRAP: integer division overflow (pc=0x%08X)",
    ),
    (
        LOG_EVT_TRAP_BASE + TrapCode.INVALID_CONVERSION,
        "TRAP: invalid float-to-integer conversion (pc=0x%08X)",
    ),
)


class WasmNumber(Protocol):
    """Numeric host value accepted at the public interpreter boundary."""

    def __int__(self) -> int: ...

    def __float__(self) -> float: ...


FB_CONF_MAX_LOCAL_STACK = NATIVE_VALUE_STACK_CAPACITY


def _encode_public_args(
    values: Sequence[WasmNumber], param_types: Sequence[str]
) -> StaticVector[int]:
    assert len(values) == len(param_types)
    raw_args: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
    for value, value_type in zip(values, param_types, strict=True):
        if value_type == I64:
            raw = int(value) & I64_MASK
            raw_args.append(raw & I32_MASK)
            raw_args.append(raw >> 32)
        elif value_type == F32:
            bits = struct.unpack("<I", struct.pack("<f", float(value)))[0]
            raw_args.append(bits)
        elif value_type == F64:
            bits = struct.unpack("<Q", struct.pack("<d", float(value)))[0]
            raw_args.append(bits & I32_MASK)
            raw_args.append(bits >> 32)
        else:
            raw_args.append(int(value) & I32_MASK)
    return raw_args


@dataclass(slots=True)
class ExecEnv:
    """
    R2 (`env`): state shared across every call in this run -- the
        module, linear memory, globals, tables and host-import dispatch table.
        Never mutated per-instruction-dispatch, only by the opcodes that are
        specified to mutate it (global.set, stores, memory.grow).
    """

    module: Module
    memory: bytearray | None
    globals: StaticVector[int]
    tables: StaticVector[StaticVector[int | None]]
    host_functions: StaticVector[Callable[..., WasmNumber | None] | None]
    memory_decl: Memory
    vmmio: VMMIOController | None = None
    phys_mem: bytearray | None = None


@dataclass(slots=True)
class InterpreterBindings:
    """Concrete resources supplied while instantiating one WASM module."""

    memory: bytearray
    memory_decl: Memory
    imported_memory: bool
    host_functions: StaticVector[Callable[..., WasmNumber | None] | None]
    globals: StaticVector[int]
    tables: StaticVector[StaticVector[int | None]]

    @classmethod
    def empty(cls) -> InterpreterBindings:
        """Create the explicit empty binding set for a module with no imports."""
        return cls(
            memory=bytearray(),
            memory_decl=Memory(min_pages=0, max_pages=None),
            imported_memory=False,
            host_functions=StaticVector(capacity=0),
            globals=StaticVector(capacity=0),
            tables=StaticVector(capacity=0),
        )

    @classmethod
    def with_memory(cls, memory: bytearray) -> InterpreterBindings:
        """Create bindings with a concrete linear-memory instance only."""
        return cls(
            memory=memory,
            memory_decl=Memory(min_pages=0, max_pages=None),
            imported_memory=False,
            host_functions=StaticVector(capacity=0),
            globals=StaticVector(capacity=0),
            tables=StaticVector(capacity=0),
        )

    @classmethod
    def with_memory_and_functions(
        cls,
        memory: bytearray,
        host_functions: StaticVector[Callable[..., WasmNumber | None] | None],
    ) -> InterpreterBindings:
        """Create bindings for a host memory and dense function-import table."""
        return cls(
            memory=memory,
            memory_decl=Memory(min_pages=0, max_pages=None),
            imported_memory=False,
            host_functions=host_functions,
            globals=StaticVector(capacity=0),
            tables=StaticVector(capacity=0),
        )


class _CallFrameStack:
    """Python identity view backed by the native fixed-capacity call stack."""

    __slots__ = ("_frames", "_native")

    def __init__(self, native: NativeCallFrameStack, capacity: int):
        self._native = native
        self._frames: StaticVector[CallFrame] = StaticVector(capacity=capacity)

    @property
    def native(self) -> NativeCallFrameStack:
        return self._native

    @property
    def raw_view(self) -> memoryview:
        return self._native.raw_view

    def __len__(self) -> int:
        assert len(self._frames) == len(self._native)
        return len(self._frames)

    def __bool__(self) -> bool:
        return len(self) != 0

    def __getitem__(self, index: int) -> CallFrame:
        assert len(self._frames) == len(self._native)
        frame = self._frames[index]
        assert frame is not None
        return frame

    def push_back(self, frame: CallFrame) -> bool:
        assert len(self._frames) == len(self._native)
        if not self._native.push_back(frame.native):
            return False
        if not self._frames.push_back(frame):
            self._native.pop_back()
            return False
        return True

    def pop_back(self) -> CallFrame:
        assert len(self._frames) == len(self._native)
        native_frame = self._native.pop_back()
        frame = self._frames.pop_back()
        assert frame is not None
        assert frame.native.func_index == native_frame.func_index
        return frame


class InterpreterContext:
    """Execution-context-owned stacks shared by the complete call chain."""

    __slots__ = (
        "_c_context",
        "_call_stack_native",
        "_context_ptr",
        "_context_view",
        "call_frame_stack",
        "control_frame_stack",
        "local_offset",
        "local_stack",
        "module",
        "operand_stack",
    )

    def __init__(self, module: Module | None = None):
        # The interpreter and JIT share the same Native ABI record. Runtime
        # Value, local, control, and call-frame stacks are Native
        # fixed-capacity records. Python objects are identity wrappers only.
        self._c_context = ExecutionContextNative()
        self._context_ptr = ctypes.cast(ctypes.pointer(self._c_context), ctypes.c_void_p)
        self._context_view = memoryview(self._c_context)
        self.operand_stack: NativeValueStack = NativeValueStack(FB_CONF_MAX_VALUE_STACK)
        self.local_stack: NativeValueStack = NativeValueStack(FB_CONF_MAX_LOCAL_STACK)
        self.local_offset = 0
        self.module = module
        self._call_stack_native = NativeCallFrameStack(FB_CONF_MAX_NESTING_DEPTH)
        self.call_frame_stack = _CallFrameStack(
            self._call_stack_native,
            FB_CONF_MAX_NESTING_DEPTH,
        )
        self.control_frame_stack: NativeControlStack = NativeControlStack(FB_CONF_MAX_NESTING_DEPTH)
        self._c_context.call_stack = self._call_stack_native.address
        self._c_context.call_base = 0
        self._c_context.call_offset = 0
        self._c_context.sp_capacity = self.operand_stack.capacity

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        """Pointer to the fixed-size native execution-context backing store."""
        return self._context_ptr

    @property
    def native_context(self) -> ExecutionContextNative:
        """Return the shared Native ABI record used by interpreter and JIT."""
        return self._c_context

    @property
    def native_call_stack(self) -> NativeCallFrameStack:
        """Return the fixed Native CallFrame stack behind ``call_frame_stack``."""
        return self._call_stack_native

    @property
    def context_view(self) -> memoryview:
        """Return the zero-copy execution-context ABI record view."""
        return self._context_view

    def begin_call_frame(
        self,
        raw_args: Sequence[int],
        func_index: int,
        env: ExecEnv,
    ) -> CallFrame:
        """Push one frame's locals directly into the context-owned Native stack."""
        frame_offset = self.local_offset
        frame = CallFrame(
            self,
            func_index=func_index,
            frame_offset=frame_offset,
            env=env,
        )
        local_slot_count = frame.local_slot_count
        assert len(raw_args) == frame.param_packed_slot_count
        assert frame_offset + local_slot_count <= self.local_stack.capacity
        if not self.call_frame_stack.push_back(frame):
            assert False, "call-frame stack capacity exceeded"
        if not self.local_stack.push_zeroed(local_slot_count):
            self.call_frame_stack.pop_back()
            assert False, "local stack capacity exceeded"
        raw_offset = 0
        for index in range(frame.param_count):
            width = frame.local_widths.words(index)
            local_offset = frame_offset + index * frame.local_widths.slot_words
            for word in range(width):
                self.local_stack.write_raw_at(local_offset + word, raw_args[raw_offset])
                raw_offset += 1
        assert raw_offset == len(raw_args)
        self.local_offset += local_slot_count
        self._c_context.local_offset = self.local_offset
        self._c_context.call_offset = len(self.call_frame_stack)
        return frame

    def end_call_frame(self, frame: CallFrame) -> None:
        """Pop the active frame and its locals from the context stacks."""
        assert self.call_frame_stack
        assert self.call_frame_stack[-1] is frame
        self.local_stack.truncate(frame.frame_offset)
        self.local_offset = frame.frame_offset
        self._c_context.local_offset = self.local_offset
        self.call_frame_stack.pop_back()
        self._c_context.call_offset = len(self.call_frame_stack)

    def bind_handler_state(self, ip: int, frame: CallFrame) -> None:
        """Publish the current native handler state in the execution context."""
        assert self.call_frame_stack
        assert self.call_frame_stack[-1] is frame
        self._c_context.ip = ip


def _read_memarg(code: bytes, ip: int) -> tuple[int, int]:
    align, off = decode_unsigned(code, ip + 1)
    mem_offset, next_ip = decode_unsigned(code, off)
    return mem_offset, next_ip


class _PyBuffer(ctypes.Structure):
    """Minimal CPython buffer record used to obtain a read-only view address."""

    _fields_ = (
        ("buf", ctypes.c_void_p),
        ("obj", ctypes.c_void_p),
        ("len", ctypes.c_ssize_t),
        ("itemsize", ctypes.c_ssize_t),
        ("format", ctypes.c_char_p),
        ("ndim", ctypes.c_int),
        ("shape", ctypes.c_void_p),
        ("strides", ctypes.c_void_p),
        ("suboffsets", ctypes.c_void_p),
        ("internal", ctypes.c_void_p),
    )


def _native_buffer_address(view: memoryview) -> int:
    """Return a non-owning buffer address for both writable and ROM views."""

    if view.nbytes == 0:
        return 0
    try:
        return ctypes.addressof(ctypes.c_ubyte.from_buffer(view))
    except (TypeError, ValueError):
        descriptor = _PyBuffer()
        get_buffer = ctypes.pythonapi.PyObject_GetBuffer
        get_buffer.argtypes = (ctypes.py_object, ctypes.POINTER(_PyBuffer), ctypes.c_int)
        get_buffer.restype = ctypes.c_int
        release_buffer = ctypes.pythonapi.PyBuffer_Release
        release_buffer.argtypes = (ctypes.POINTER(_PyBuffer),)
        release_buffer.restype = None
        assert get_buffer(view, ctypes.byref(descriptor), 0) == 0
        try:
            assert descriptor.buf is not None
            return int(descriptor.buf)
        finally:
            release_buffer(ctypes.byref(descriptor))


class CallFrame:
    """
    Activation descriptor for one function. `values`, `locals`, and `frames`
    are windows into the three independent stacks owned by `InterpreterContext`;
    this object never owns a per-function stack.
    """

    __slots__ = (
        "_frames",
        "_locals",
        "_native",
        "_native_br_table_targets",
        "_native_control_map",
        "boundary_loops_to",
        "boundary_next_pc",
        "code",
        "context",
        "control_base",
        "control_map",
        "env",
        "frame_offset",
        "func_index",
        "has_nested_calls",
        "local_count",
        "local_slot_count",
        "local_types",
        "local_widths",
        "param_count",
        "param_packed_slot_count",
        "result_arity",
        "values",
    )

    def __init__(
        self,
        context: InterpreterContext,
        func_index: int,
        frame_offset: int,
        env: ExecEnv | None = None,
    ):
        module = context.module
        assert module is not None
        assert not module.is_import(func_index)
        function = module.functions[func_index - len(module.imports)]
        local_widths = function.local_width_map_cache
        assert local_widths is not None
        self.context = context
        self.values = context.operand_stack
        self.control_base = len(context.control_frame_stack)
        self._frames = _ControlFrameWindow(context.control_frame_stack, self.control_base)
        self.func_index = func_index
        self.has_nested_calls = function.has_nested_calls
        self.frame_offset = frame_offset
        self.local_count = len(local_widths)
        self.local_widths = local_widths
        local_slot_count = function.local_slot_count_cache
        param_count = function.param_count_cache
        param_packed_slot_count = function.param_packed_slot_count_cache
        result_arity = function.result_arity_cache
        assert local_slot_count is not None
        assert param_count is not None
        assert param_packed_slot_count is not None
        assert result_arity is not None
        self.local_slot_count = local_slot_count
        self.param_count = param_count
        self.param_packed_slot_count = param_packed_slot_count
        self.result_arity = result_arity
        assert self.result_arity <= 2, "multi-value function results exceed native return width"
        self._locals = _LocalStackWindow(
            context.local_stack,
            frame_offset,
            local_widths,
        )
        self.code = context.module.code_for(func_index)
        assert function.control_map is not None
        self.control_map = function.control_map
        control_map_array_type = ControlMapEntryNative * len(self.code)
        native_control_map = control_map_array_type()
        for index in range(len(self.code)):
            native_control_map[index].match_end = 0xFFFF_FFFF
            native_control_map[index].else_offset = 0xFFFF_FFFF
            native_control_map[index].next_pc = 0xFFFF_FFFF
            native_control_map[index].result_arity = 0
            native_control_map[index].operand_width = 1
            native_control_map[index].br_table_target_count = 0
            native_control_map[index].br_table_targets = 0
        for start, control in self.control_map.blocks.view().entries:
            match_end, else_offset, result_arity = control
            _, next_pc = decode_signed(self.code, start + 1)
            native_control_map[start].match_end = match_end
            native_control_map[start].else_offset = (
                0xFFFF_FFFF if else_offset is None else else_offset
            )
            native_control_map[start].next_pc = next_pc
            native_control_map[start].result_arity = result_arity
        if function.drop_widths is not None:
            for start, width in function.drop_widths.view().entries:
                native_control_map[start].operand_width = width
        if function.select_widths is not None:
            for start, width in function.select_widths.view().entries:
                native_control_map[start].operand_width = width
        br_tables = self.control_map.br_tables.view().entries
        target_count = 0
        for _, (labels, _) in br_tables:
            target_count += len(labels) + 1
        target_array_type = ctypes.c_uint32 * target_count
        native_br_table_targets = target_array_type()
        target_offset = 0
        for start, (labels, default_label) in br_tables:
            entry = native_control_map[start]
            entry.br_table_target_count = len(labels) + 1
            entry.br_table_targets = (
                ctypes.addressof(native_br_table_targets)
                + target_offset * ctypes.sizeof(ctypes.c_uint32)
            )
            for label in labels:
                native_br_table_targets[target_offset] = label
                target_offset += 1
            native_br_table_targets[target_offset] = default_label
            target_offset += 1
        assert target_offset == target_count
        self._native_br_table_targets = native_br_table_targets
        self._native_control_map = native_control_map
        self.env = env
        # Set by RuntimeEngine.run() right before each interp.step() call,
        # from that step's BasicBlock's own statically-computed next_pc /
        # loops_to (extract_basic_blocks -- the same static resolution the
        # JIT already uses, unified pc format) -- overrides the runtime
        # frame.frames-based target in _h_br / _h_br_if / _h_else below.
        # A JIT trace's computed jumps never touch frame.frames, so its
        # content can desync from reality across a JIT/interpreter boundary
        # (see RuntimeEngine.run()); the static BasicBlock target this step
        # is dispatching never can, since it is a pure function of `pc`
        # alone. Stays None for a bare Interpreter.call() run with no
        # owning RuntimeEngine, so that path is byte-for-byte unchanged.
        self.boundary_next_pc: int | None = None
        self.boundary_loops_to: int | None = None
        self._native = CallFrameNative(
            func_index=self.func_index,
            code=_native_buffer_address(self.code),
            code_size=len(self.code),
            control_map=ctypes.addressof(native_control_map),
            local_base=self.frame_offset,
            local_count=self.local_count,
            local_slot_count=self.local_slot_count,
            slot_words=self.local_widths.slot_words,
            local_width_map=_native_buffer_address(self.local_widths.raw_view),
            local_width_count=self.local_count,
            param_count=self.param_count,
            param_packed_slot_count=self.param_packed_slot_count,
            result_arity=self.result_arity,
            control_base=self.control_base,
            return_ip=0xFFFF_FFFF,
            return_func_index=0xFFFF_FFFF,
            boundary_next_pc=0xFFFF_FFFF,
            boundary_loops_to=0xFFFF_FFFF,
        )

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        return self.context.context_ptr

    @property
    def native(self) -> CallFrameNative:
        """Return the flat native activation descriptor stored on the call stack."""

        return self._native

    @property
    def frames(self) -> _ControlFrameWindow:
        return self._frames

    @property
    def locals(self) -> _LocalStackWindow:
        return self._locals

    def set_runtime_boundary(self, next_pc: int | None, loops_to: int | None) -> None:
        """Publish the current block exit targets to the native handlers."""
        self.boundary_next_pc = next_pc
        self.boundary_loops_to = loops_to
        assert self.context.call_frame_stack
        assert self.context.call_frame_stack[-1] is self
        native_frame = self.context.call_frame_stack.native[-1]
        assert native_frame.func_index == self.func_index
        native_frame.boundary_next_pc = 0xFFFF_FFFF if next_pc is None else next_pc
        native_frame.boundary_loops_to = 0xFFFF_FFFF if loops_to is None else loops_to


# The interpreter call's resumable continuation: (next_ip, frame, local_base,
# tos). `next_ip == RETURN_SENTINEL_IP` is the explicit return boundary;
# `cont=None` is reserved for a fully finished or trapped call. `tos` remains
# a runtime/JIT boundary field; operand values themselves live only in the
# Native stack.
_Cont = tuple[int, CallFrame, _LocalStackWindow, int] | None


class DebuggerAttachment(Protocol):
    halted: bool
    stop_signal: int


# A handler returns only an exceptional outcome. Successful handlers update
# ctx/native stacks in place and fall through with an implicit None.
_HandlerResult = Trap | None
_HandlerFn = Callable[
    [InterpreterContext, NativeValueStack, _LocalStackWindow, int], _HandlerResult
]

# Fixed 256-slot direct-indexed dispatch table for WASM byte opcodes (0x00..0xFF)
_HANDLERS: StaticVector[_HandlerFn | None] = StaticVector(capacity=256)
_BASIC_BLOCK_BOUNDARY: bytes = bytes(
    opcode_has_attribute(_opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY) for _opcode in range(256)
)


def _handler(opcode: int) -> Callable[[_HandlerFn], _HandlerFn]:
    def register(fn: _HandlerFn) -> _HandlerFn:
        while len(_HANDLERS) <= opcode:
            _HANDLERS.append(None)
        _HANDLERS[opcode] = fn
        return fn

    return register


def _handler_state(
    ctx: InterpreterContext, sp: NativeValueStack
) -> tuple[int, CallFrame, ExecEnv | None]:
    """Resolve the current instruction state from the shared execution context."""
    assert ctx.call_frame_stack
    frame = ctx.call_frame_stack[-1]
    assert sp is frame.values
    return int(ctx.native_context.ip), frame, frame.env


def _do_branch(depth: int, frame: CallFrame) -> int | None:
    """
    Shared by BR/BR_IF/BR_TABLE: unwind `depth` control frames and
        compute where execution resumes -- a loop resumes its body, a
        block/if resumes just past its matching END. Returns None if the
        branch unwinds past the outermost implicit function block (== return).
    """

    return frame.frames.branch(depth, frame.values, frame.result_arity)


class InterpreterCall:
    """
    Resumable state for one in-progress `Interpreter.call()`, stepped by
    `Interpreter.step()`. Crosses the interpreter/runtime boundary as plain
    data -- never as a Python coroutine -- so the runtime decides on its own
    terms what happens between steps.

    `call_stack` holds every caller frame currently suspended on a WASM
    `call`/`call_indirect` that has not yet returned, as `(func_index,
    resume_cont)` pairs, deepest-caller-last. Entering or returning from a
    nested WASM call is always a mandatory `step()` boundary: the interpreter hands the callee's freshly-entered frame
    straight back to the runtime rather than running it itself, so a JIT
    trace cache gets exactly the same chance to intercept a nested call as
    it gets for the outermost one -- tiering never depends on call depth.
    """

    __slots__ = (
        "_frame",
        "_ip",
        "_locals",
        "_tos",
        "call_stack",
        "context",
        "finished",
        "func_index",
        "results",
        "trap",
    )

    def __init__(
        self,
        func_index: int,
        context: InterpreterContext,
        cont: _Cont,
        finished: bool = False,
        results: StaticVector[WasmNumber] | None = None,
        trap: Trap | None = None,
    ):
        self.func_index = func_index
        self.context = context
        self._ip = 0
        self._frame: CallFrame | None = None
        self._locals: _LocalStackWindow | None = None
        self._tos = 0
        self.call_stack: StaticVector[tuple[int, _Cont]] = StaticVector(
            capacity=FB_CONF_MAX_NESTING_DEPTH
        )
        self.finished = finished
        self.results = results
        self.trap = trap
        self.cont = cont

    @property
    def cont(self) -> _Cont:
        """Compatibility view; the interpreter loop uses the scalar fields."""

        frame = self._frame
        if frame is None:
            return None
        assert self._locals is not None
        return self._ip, frame, self._locals, self._tos

    @cont.setter
    def cont(self, value: _Cont) -> None:
        if value is None:
            self._ip = 0
            self._frame = None
            self._locals = None
            self._tos = 0
            return
        ip, frame, local_base, tos = value
        self._ip = ip
        self._frame = frame
        self._locals = local_base
        self._tos = tos

    def current_pc(self) -> int:
        """
        Return this call's unified `(func_index, ip)` address.

        The return sentinel is exposed as its reserved unified PC so the
        runtime can recognize it without consulting bytecode. It is never a
        valid BasicBlock lookup key. A missing continuation is not an address.
        """
        frame = self._frame
        assert frame is not None
        ip = self._ip
        if ip == RETURN_SENTINEL_IP:
            return RETURN_SENTINEL_PC
        assert 0 <= ip < len(frame.code)
        return (self.func_index << 16) | ip


class Interpreter:
    __slots__ = (
        "_env",
        "debugger",
        "globals",
        "host_functions",
        "logger",
        "memory",
        "memory_decl",
        "module",
        "phys_mem",
        "tables",
        "vmmio",
    )

    def __init__(
        self,
        module: Module,
        bindings: InterpreterBindings,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: Logger | None = None,
    ):
        self.module = module
        self.logger = logger
        self.memory = bindings.memory
        if module.memory_import is not None:
            assert bindings.imported_memory
            assert len(bindings.memory) >= module.memory_import.min_limit * PAGE_SIZE
            assert bindings.memory_decl.min_pages >= module.memory_import.min_limit
            if module.memory_import.max_limit is not None:
                assert bindings.memory_decl.max_pages is not None
                assert bindings.memory_decl.max_pages <= module.memory_import.max_limit
            self.memory_decl = bindings.memory_decl
        else:
            assert not bindings.imported_memory
            self.memory_decl = module.memory if module.memory is not None else bindings.memory_decl
            if module.memory is not None:
                assert len(bindings.memory) >= module.memory.min_pages * PAGE_SIZE

        assert len(bindings.host_functions) == len(module.imports)
        self.host_functions = bindings.host_functions
        self.globals: StaticVector[int] = StaticVector(capacity=len(module.globals))
        global_import_index = 0
        assert len(bindings.globals) == module.global_import_count
        for global_value in module.globals:
            if global_value.imported:
                self.globals.append(bindings.globals[global_import_index])
                global_import_index += 1
            elif global_value.init_global_index is not None:
                assert global_value.init_global_index < len(self.globals)
                self.globals.append(self.globals[global_value.init_global_index])
            else:
                self.globals.append(global_value.init_value)
        self.tables: StaticVector[StaticVector[int | None]] = StaticVector(
            capacity=len(module.tables)
        )
        table_import_index = 0
        assert len(bindings.tables) == module.table_import_count
        for table_index in range(len(module.tables)):
            table = module.tables[table_index]
            if table.imported:
                external_table = bindings.tables[table_import_index]
                table_import_index += 1
                self.tables.append(module.table_contents(table_index, self.globals, external_table))
            else:
                self.tables.append(module.table_contents(table_index, self.globals))
        self.debugger: DebuggerAttachment | None = None
        self.vmmio = vmmio
        self.phys_mem = phys_mem
        self._env = ExecEnv(
            module,
            bindings.memory,
            self.globals,
            self.tables,
            self.host_functions,
            vmmio=vmmio,
            phys_mem=phys_mem,
            memory_decl=self.memory_decl,
        )
        if self.module.start_function is not None:
            self.call(self.module.start_function, ())

    def attach_debugger(self, debugger: DebuggerAttachment) -> None:
        """
        Records the attached debugger. Unlike the legacy block harness's
        {DebuggerInterpreterComposition}, the threaded interpreter's `_HANDLERS`
        dispatch table is a single fixed table with no separate debug/normal
        variant to switch between -- breakpoint/step behavior for
        interpreter-only execution is driven by DebuggerManager itself.
        """
        self.debugger = debugger

    def detach_debugger(self) -> None:
        self.debugger = None

    def call(self, func_index: int, args: Sequence[WasmNumber]) -> StaticVector[WasmNumber]:
        """Runs a function to completion in one call."""
        call_state = self.start(func_index, args)
        return self._complete_call(call_state)

    def _complete_call(self, call_state: InterpreterCall) -> StaticVector[WasmNumber]:
        """Template hook for alternate execution drivers.

        The public call contract, call-state construction, trap publication, and result
        validation stay owned by the interpreter.  Tiered execution may override only the
        driver hook while preserving the exact same call boundary as the base interpreter.
        """
        if not call_state.finished:
            frame = call_state._frame
            assert frame is not None
            if not frame.has_nested_calls:
                results = self._call_without_nested_calls(call_state)
                if results is not None:
                    return results
                assert call_state.trap is not None
                assert False, call_state.trap.code
        while not call_state.finished:
            call_state = self._step(call_state, stop_at_boundary=False)
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def _call_without_nested_calls(
        self, call_state: InterpreterCall
    ) -> StaticVector[WasmNumber] | None:
        """Run C++ handlers to completion, returning through Python at yield counts."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        ip = call_state._ip
        code = frame.code
        code_len = len(code)
        ctx = call_state.context
        values = frame.values
        while True:
            if ip == RETURN_SENTINEL_IP:
                break
            assert 0 <= ip < code_len

            native_status = self.run_native_dispatch(
                call_state,
                (),
                (),
                FB_CONF_RUNTIME_YIELD_THRESHOLD,
                0,
            )
            native_status = native_status[0]
            if native_status == 1:
                ip = RETURN_SENTINEL_IP
                break
            if native_status == 2:
                return None
            if native_status == NATIVE_DISPATCH_YIELD:
                ctx.native_context.loop_jump_count = 0
                ip = call_state._ip
                continue
            assert native_status == 0
            ip = call_state._ip
            opcode = code[ip]
            handler = _HANDLERS[opcode]
            assert handler is not None, f"interpreter: unhandled opcode 0x{opcode:02X}"
            ctx.bind_handler_state(ip, frame)
            trap = handler(ctx, values, locals_arr, call_state._tos)
            if trap is not None:
                self._abort_call(call_state, trap, ip)
                return None
            ip = int(ctx.native_context.ip)
            if ip >= code_len:
                ip = RETURN_SENTINEL_IP
            call_state._ip = ip
            call_state._tos = values.raw_top() if values else 0

        func_type = self.module.func_type(call_state.func_index)
        frame.frames.truncate(0)
        call_state.context.end_call_frame(frame)
        results: StaticVector[WasmNumber] = StaticVector(capacity=4)
        if func_type.results:
            result_type = func_type.results[0]
            if result_type == I64:
                result_value = frame.values.pop_i64()
            elif result_type == F32:
                result_value = frame.values.pop_f32()
            elif result_type == F64:
                result_value = frame.values.pop_f64()
            else:
                result_value = frame.values.pop_i32()
            results.append(result_value)
        call_state.cont = None
        call_state.finished = True
        call_state.results = results
        return results

    def _abort_call(self, call_state: InterpreterCall, trap: Trap, ip: int) -> None:
        """Terminate every active frame and publish a runtime trap outcome.

        `ip` is the trapping instruction's offset within `call_state.func_index`,
        passed explicitly by the caller rather than read from `call_state._ip`:
        the `_call_without_nested_calls` fast path advances a local `ip` without
        writing it back to `call_state` until the frame completes, so
        `call_state.current_pc()` would report a stale address there.
        """
        if self.logger is not None:
            # GOTCHA-LOG-04: unified_pc must be captured before frame teardown below;
            # func_index/bytecode_offset cannot be recovered from call_state afterward.
            unified_pc = (
                RETURN_SENTINEL_PC
                if ip == RETURN_SENTINEL_IP
                else (call_state.func_index << 16) | ip
            )
            self.logger.log_event(
                LogLevel.ERROR, LOG_EVT_TRAP_BASE + int(trap.code), unified_pc, trap.detail
            )
        while call_state.context.call_frame_stack:
            frame = call_state.context.call_frame_stack[-1]
            frame.frames.truncate(0)
            call_state.context.end_call_frame(frame)
        while call_state.call_stack:
            call_state.call_stack.pop_back()
        call_state.cont = None
        call_state.finished = True
        call_state.results = None
        call_state.trap = trap

    def run_iter(self, func_index: int, args: Sequence[WasmNumber]) -> Iterator[InterpreterCall]:
        """
        Drives a call basic-block by basic-block, yielding the (possibly still-unfinished)
        `call_state` after every `step()` and once more after it finishes.
        """
        call_state = self.start(func_index, args)
        while not call_state.finished:
            override = yield call_state
            call_state = override if override is not None else self.step(call_state)
        yield call_state

    def start(self, func_index: int, args: Sequence[WasmNumber]) -> InterpreterCall:
        """
        Sets up a resumable call, to be driven by repeated `step()` calls.
                A host import has nothing to step through: it resolves synchronously
                right here, so the returned call is already finished.
        """
        context = InterpreterContext(self.module)
        if self.module.is_import(func_index):
            results, trap = self._call_import(func_index, args)
            return InterpreterCall(
                func_index,
                context,
                cont=None,
                finished=True,
                results=results if trap is None else None,
                trap=trap,
            )

        raw_args = _encode_public_args(args, self.module.func_type(func_index).params)
        try:
            frame, locals_arr = self._build_frame(func_index, raw_args, context)
        except Trap as trap:
            return InterpreterCall(func_index, context, cont=None, finished=True, trap=trap)
        return InterpreterCall(func_index, context, cont=(0, frame, locals_arr, 0))

    def _call_import(
        self, func_index: int, args: Sequence[WasmNumber]
    ) -> tuple[StaticVector[WasmNumber], Trap | None]:
        """Resolves a host import synchronously -- there is no bytecode to step through."""
        handler = self.host_functions[func_index] if func_index < len(self.host_functions) else None
        if handler is None:
            return (
                StaticVector(capacity=4),
                Trap(TrapCode.NO_HOST_HANDLER, func_index),
            )
        ft = self.module.func_type(func_index)
        assert ft.params is not None and ft.results is not None
        assert len(args) == len(ft.params)
        host_args: StaticVector[WasmNumber] = StaticVector(capacity=len(ft.params))
        for index, value_type in enumerate(ft.params):
            value = args[index]
            if value_type == I64:
                argument: WasmNumber = _to_i64(int(value))
            elif value_type == F32:
                argument = _to_f32(float(value))
            elif value_type == F64:
                argument = float(value)
            else:
                assert value_type == I32
                argument = _to_i32(int(value))
            host_args.append(argument)
        result = handler(*host_args)
        results: StaticVector[WasmNumber] = StaticVector(capacity=4)
        if ft.results:
            assert len(ft.results) == 1 and result is not None
            result_type = ft.results[0]
            if result_type == I64:
                result_value: WasmNumber = _to_i64(int(result))
            elif result_type == F32:
                result_value = _to_f32(float(result))
            elif result_type == F64:
                result_value = float(result)
            else:
                result_value = _to_i32(int(result))
            results.append(result_value)
        return results, None

    def _build_frame(
        self, func_index: int, raw_args: StaticVector[int], context: InterpreterContext
    ) -> tuple[CallFrame, _LocalStackWindow]:
        """
        Builds the initial frame + locals for a WASM (non-import) function
        activation. `raw_args` contains the packed parameter values as
        32-bit slots; the load-time local-width metadata places them into the
        aligned local layout exactly once at frame creation.
        The internal call path obtains it directly from the operand stack;
        the public entry path encodes host values once at that boundary.

        The Native local slots are pushed into `context` by
        `InterpreterContext.begin_call_frame`; the operand stack is also owned
        by that same context and is shared across the complete call chain.
        """
        fn = self.module.functions[func_index - len(self.module.imports)]
        param_packed_slot_count = fn.param_packed_slot_count_cache
        assert fn.local_width_map_cache is not None
        assert param_packed_slot_count is not None
        assert len(raw_args) == param_packed_slot_count
        if fn.control_map is None:
            fn.control_map = build_control_map(self.module.code_for(func_index))
        assert fn.control_map is not None
        frame = context.begin_call_frame(
            raw_args,
            func_index=func_index,
            env=self._env,
        )
        return frame, frame.locals

    def step(self, call_state: InterpreterCall) -> InterpreterCall:
        """
        Executes one basic block (up to the next boundary instruction) and returns
        `call_state`, mutated in place: still `finished == False` with a resumable
        `.cont`, or `finished == True` with `.results` set once the outermost call
        actually returns.
        """
        if self._try_native_step_to_boundary(call_state):
            return call_state
        return self._step(call_state, stop_at_boundary=True)

    def step_native_control(self, call_state: InterpreterCall) -> InterpreterCall:
        """Execute one supported structured-control opcode through its C++ handler."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        assert call_state._ip != RETURN_SENTINEL_IP
        if self.debugger is not None:
            return self._step(call_state, stop_at_boundary=True)

        context = call_state.context
        native_status, native_ip, native_size, native_trap = (
            _interpreter_native.run_control_step(
                frame.code,
                context.context_view,
                frame.values.raw_view,
                locals_arr._storage.raw_view,
                context.control_frame_stack.raw_view,
                len(frame.values),
                frame.values.capacity,
                call_state._ip,
                frame.local_slot_count,
                frame.control_base,
            )
        )

        if native_status == 3 and native_ip >= len(frame.code):
            native_ip = RETURN_SENTINEL_IP
        frame.values.set_size(native_size)
        context.native_context.ip = native_ip
        call_state._ip = native_ip
        call_state._tos = frame.values.raw_top() if frame.values else 0

        if native_status == 3:
            return call_state
        if native_status == 1:
            call_state._ip = RETURN_SENTINEL_IP
            return self._step(call_state, stop_at_boundary=True)
        if native_status == 2:
            self._abort_call(call_state, Trap(TrapCode(native_trap)), native_ip)
            return call_state
        assert native_status == 0, f"unexpected native control status: {native_status}"
        return self._step(call_state, stop_at_boundary=True)

    def run_native_dispatch(
        self,
        call_state: InterpreterCall,
        entries: tuple[NativeTraceDispatchEntry, ...],
        trackable_blocks: tuple[int, ...],
        yield_threshold: int,
        execution_count: int,
        collect_stats: bool = False,
        collect_hotspots: bool = False,
    ) -> tuple[int, int, int, int, int, int, int, tuple[NativeBlockVisit, ...]]:
        """Run native traces and C++ handlers until a yield, fallback, or exit."""

        assert not collect_stats or NATIVE_RUNTIME_PROFILE_STATS_ENABLED, (
            "runtime profile stats were compiled out; rebuild with "
            "FB_CONF_RUNTIME_PROFILE_STATS=True"
        )
        assert not collect_hotspots or NATIVE_JIT_HOTSPOT_PROFILING_ENABLED, (
            "JIT hotspot profiling was compiled out; rebuild with "
            "FB_CONF_JIT_HOTSPOT_PROFILING=True"
        )
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        assert call_state._ip != RETURN_SENTINEL_IP
        assert yield_threshold > 0
        context = call_state.context
        (
            native_status,
            native_ip,
            native_size,
            native_trap,
            trace_count,
            body_count,
            dispatcher_trace_transitions,
            control_handler_count,
            eligible_block_visits,
            interpreted_block_count,
            visits,
        ) = (
            _interpreter_native.run_native_dispatch(
                frame.code,
                context.context_view,
                frame.values.raw_view,
                locals_arr._storage.raw_view,
                context.control_frame_stack.raw_view,
                entries,
                trackable_blocks,
                len(frame.values),
                frame.values.capacity,
                call_state._ip,
                frame.frame_offset,
                frame.local_slot_count,
                frame.control_base,
                call_state.func_index,
                yield_threshold,
                execution_count,
                collect_stats,
                collect_hotspots,
            )
        )
        frame.values.set_size(native_size)
        context.native_context.ip = native_ip
        call_state._ip = RETURN_SENTINEL_IP if native_status == 1 else native_ip
        call_state._tos = frame.values.raw_top() if frame.values else 0
        if native_status == 2:
            self._abort_call(call_state, Trap(TrapCode(native_trap)), native_ip)
        return (
            native_status,
            trace_count,
            body_count,
            dispatcher_trace_transitions,
            control_handler_count,
            eligible_block_visits,
            interpreted_block_count,
            visits,
        )

    def _try_native_step_to_boundary(self, call_state: InterpreterCall) -> bool:
        """Use native handlers up to the current block's next JIT boundary."""
        frame = call_state._frame
        locals_arr = call_state._locals
        if frame is None or locals_arr is None or self.debugger is not None:
            return False
        if call_state._ip == RETURN_SENTINEL_IP:
            return False
        if frame.boundary_next_pc is None and frame.boundary_loops_to is None:
            return False

        pc = call_state.current_pc()
        if pc == frame.boundary_next_pc or pc == frame.boundary_loops_to:
            return False

        context = call_state.context
        previous_flags = int(context.native_context.runtime_flags)
        context.native_context.runtime_flags = (
            previous_flags | EXECUTION_CONTEXT_FLAG_STOP_AT_BLOCK_BOUNDARY
        )
        try:
            native_status, native_ip, native_size, native_trap = _interpreter_native.run_step(
                frame.code,
                context.context_view,
                frame.values.raw_view,
                locals_arr._storage.raw_view,
                context.control_frame_stack.raw_view,
                len(frame.values),
                frame.values.capacity,
                call_state._ip,
                frame.local_slot_count,
                frame.control_base,
            )
        finally:
            context.native_context.runtime_flags = previous_flags

        if native_status == 3 and native_ip >= len(frame.code):
            native_ip = RETURN_SENTINEL_IP
        frame.values.set_size(native_size)
        context.native_context.ip = native_ip
        call_state._ip = native_ip
        call_state._tos = frame.values.raw_top() if frame.values else 0

        if native_status == 3:
            return True
        if native_status == 1:
            call_state._ip = RETURN_SENTINEL_IP
            return False
        if native_status == 2:
            self._abort_call(call_state, Trap(TrapCode(native_trap)), native_ip)
            return True
        assert native_status == 0, f"unexpected native interpreter status: {native_status}"
        return False

    def _step(self, call_state: InterpreterCall, stop_at_boundary: bool) -> InterpreterCall:
        """Run until a boundary or completion, depending on the driving caller."""
        while True:
            ip = call_state._ip
            frame = call_state._frame
            locals_arr = call_state._locals
            tos = call_state._tos
            assert frame is not None and locals_arr is not None
            if ip != RETURN_SENTINEL_IP:
                code = frame.code
                assert 0 <= ip < len(code)
                op = code[ip]
                if op == CALL or op == CALL_INDIRECT:
                    trap = self._enter_or_resolve_call(call_state, op, ip, frame, locals_arr, tos)
                    if trap is not None:
                        self._abort_call(call_state, trap, ip)
                        return call_state
                    if stop_at_boundary:
                        return call_state
                    continue
                is_boundary = _BASIC_BLOCK_BOUNDARY[op] != 0
                handler = _HANDLERS[op]
                assert handler is not None, f"interpreter: unhandled opcode 0x{op:02X}"
                # `context` and `values` are fixed for this frame's whole
                # lifetime (set once in CallFrame/InterpreterCall.__init__,
                # never reassigned) -- hoisted so this hottest of all loops
                # pays the attribute-lookup cost once per instruction
                # instead of four (context) and three (values) times.
                ctx = call_state.context
                values = frame.values
                ctx.bind_handler_state(ip, frame)
                trap = handler(ctx, values, locals_arr, tos)
                if trap is not None:
                    self._abort_call(call_state, trap, ip)
                    return call_state
                next_ip = int(ctx.native_context.ip)
                result_frame = ctx.call_frame_stack[-1]
                assert result_frame is frame
                result_locals = locals_arr
                next_tos = values.raw_top() if values else 0
                if next_ip >= len(code):
                    next_ip = RETURN_SENTINEL_IP
                call_state._ip = next_ip
                call_state._frame = result_frame
                call_state._locals = result_locals
                call_state._tos = next_tos
                if call_state._ip != RETURN_SENTINEL_IP:
                    if is_boundary and stop_at_boundary:
                        return call_state
                    continue
                # The explicit return sentinel ends this frame; nested calls
                # consume it here and restore the suspended caller below.  A
                # boundary-driven caller gets one observable sentinel step so
                # RuntimeEngine can perform its top-level completion check.
                if stop_at_boundary:
                    return call_state

            ft = self.module.func_type(call_state.func_index)
            # The context-owned control-frame window is independent from the
            # LocalStack frame lifetime; discard any frames left by a JIT
            # boundary before releasing this call activation.
            frame.frames.truncate(0)
            call_state.context.end_call_frame(frame)
            if not call_state.call_stack:
                results: StaticVector[WasmNumber] = StaticVector(capacity=4)
                # The Native operand stack contains raw slots only. The
                # caller's function signature selects the typed read here;
                # no return type is stored in the result buffer.
                if ft.results:
                    result_type = ft.results[0]
                    if result_type == I64:
                        result_value = frame.values.pop_i64()
                    elif result_type == F32:
                        result_value = frame.values.pop_f32()
                    elif result_type == F64:
                        result_value = frame.values.pop_f64()
                    else:
                        result_value = frame.values.pop_i32()
                    assert result_value is not None
                    results.append(result_value)
                call_state.cont = None
                call_state.finished = True
                call_state.results = results
                return call_state
            # Return to the suspended caller frame -- always a mandatory
            # boundary, so the runtime gets the same chance to check its JIT
            # trace cache here as it does entering any other frame. The
            # operand stack is shared across this whole call chain (see
            # the result -- already sitting on the context-owned operand
            # stack -- needs no copy:
            # the caller's own next pop/push simply continues from here.
            parent_func_index, parent_cont = call_state.call_stack.pop_back()
            p_ip, p_frame, p_locals, _ = parent_cont
            call_state.func_index = parent_func_index
            call_state._ip = p_ip
            call_state._frame = p_frame
            call_state._locals = p_locals
            call_state._tos = 0
            if stop_at_boundary:
                return call_state

    def _enter_or_resolve_call(
        self,
        call_state: InterpreterCall,
        opcode: int,
        ip: int,
        frame: CallFrame,
        locals_arr: StaticVector[int],
        tos: int,
    ) -> Trap | None:
        """
        Resolves the callee of a `call`/`call_indirect` directly from bytecode and either invokes a
        host import synchronously (there is nothing to step through) or
        pushes this frame onto `call_state.call_stack` and hands control to
        the callee's freshly-built entry frame.
        """
        if opcode == CALL:
            callee_func_index, next_ip = decode_unsigned(frame.code, ip + 1)
            callee_ft = self.module.func_type(callee_func_index)
        else:
            typeidx, off = decode_unsigned(frame.code, ip + 1)
            tableidx, next_ip = decode_unsigned(frame.code, off)
            table = frame.env.tables[tableidx]
            table_slot = _to_u32(frame.values.pop_back())
            if table_slot >= len(table):
                return Trap(TrapCode.TABLE_INDEX_OUT_OF_BOUNDS, table_slot)
            callee_func_index = table[table_slot]
            if callee_func_index is None:
                return Trap(TrapCode.TABLE_SLOT_UNINITIALIZED, table_slot)
            declared_type = self.module.type_at(typeidx)
            actual_type = self.module.func_type(callee_func_index)
            if declared_type != actual_type:
                return Trap(TrapCode.INDIRECT_CALL_TYPE_MISMATCH, table_slot)
            callee_ft = declared_type

        if self.module.is_import(callee_func_index):
            call_args: StaticVector[WasmNumber] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
            popped_args: StaticVector[WasmNumber] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
            for value_type in reversed(callee_ft.params):
                if value_type == I64:
                    value = frame.values.pop_i64()
                elif value_type == F32:
                    value = frame.values.pop_f32()
                elif value_type == F64:
                    value = frame.values.pop_f64()
                else:
                    value = frame.values.pop_i32()
                assert value is not None
                popped_args.append(value)
            for index in range(len(popped_args) - 1, -1, -1):
                call_args.append(popped_args[index])
            results, trap = self._call_import(callee_func_index, call_args)
            if trap is not None:
                return trap
            for index, result in enumerate(results):
                result_type = callee_ft.results[index]
                if result_type == I64:
                    pushed = frame.values.push_i64(int(result))
                elif result_type == F32:
                    pushed = frame.values.push_f32(float(result))
                elif result_type == F64:
                    pushed = frame.values.push_f64(float(result))
                else:
                    pushed = frame.values.push_i32(int(result))
                if not pushed:
                    return Trap(TrapCode.OPERAND_STACK_CAPACITY)
            resume_tos = frame.values[-1] if frame.values else 0
            call_state._ip = next_ip
            call_state._frame = frame
            call_state._locals = locals_arr
            call_state._tos = resume_tos
            return None

        popped_raw_args: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
        for value_type in reversed(callee_ft.params):
            for _ in range(value_slot_width(value_type)):
                value = frame.values.pop_back()
                assert value is not None
                popped_raw_args.append(value)
        popped_raw_args.reverse_in_place()
        resume_tos = frame.values[-1] if frame.values else 0
        resume_cont = (next_ip, frame, locals_arr, resume_tos)
        try:
            callee_frame, callee_locals = self._build_frame(
                callee_func_index, popped_raw_args, call_state.context
            )
        except Trap as trap:
            return trap
        callee_frame.native.return_ip = next_ip
        callee_frame.native.return_func_index = call_state.func_index
        if not call_state.call_stack.push_back((call_state.func_index, resume_cont)):
            return Trap(TrapCode.CALL_STACK_CAPACITY)
        call_state.func_index = callee_func_index
        callee_tos = callee_frame.values[-1] if callee_frame.values else 0
        call_state._ip = 0
        call_state._frame = callee_frame
        call_state._locals = callee_locals
        call_state._tos = callee_tos
        return None


# Per-opcode logical CPS handlers. Each receives the native CPS arguments and
# returns the exact arguments for the next handler, plus an optional trap.
# ---------------------------------------------------------------------------


@_handler(UNREACHABLE)
def _h_unreachable(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    return Trap(TrapCode.UNREACHABLE)


@_handler(NOP)
def _h_nop(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    ctx.native_context.ip = ip + 1
    return None


@_handler(BLOCK)
def _h_block(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end, _, result_arity = frame.control_map.block(ip)
    if not frame.frames.push_back(
        ControlFrameKind.BLOCK, ip, match_end, len(frame.values), result_arity
    ):
        return Trap(TrapCode.CONTROL_FRAME_CAPACITY)
    ctx.native_context.ip = ip + 2
    return None


@_handler(LOOP)
def _h_loop(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end, _, result_arity = frame.control_map.block(ip)
    if not frame.frames.push_back(
        ControlFrameKind.LOOP, ip, match_end, len(frame.values), result_arity
    ):
        return Trap(TrapCode.CONTROL_FRAME_CAPACITY)
    ctx.native_context.ip = ip + 2
    return None


@_handler(IF)
def _h_if(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end, else_off, result_arity = frame.control_map.block(ip)
    cond = frame.values.pop_back()
    if cond == 0:
        if else_off is not None:
            if not frame.frames.push_back(
                ControlFrameKind.IF, ip, match_end, len(frame.values), result_arity
            ):
                return Trap(TrapCode.CONTROL_FRAME_CAPACITY)
            ctx.native_context.ip = else_off + 1
            return None
        else:
            ctx.native_context.ip = match_end + 1
            return None
    if not frame.frames.push_back(
        ControlFrameKind.IF, ip, match_end, len(frame.values), result_arity
    ):
        return Trap(TrapCode.CONTROL_FRAME_CAPACITY)
    ctx.native_context.ip = ip + 2
    return None


@_handler(ELSE)
def _h_else(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    popped = frame.frames.pop_back() if frame.frames else None
    if frame.boundary_next_pc is not None:
        ctx.native_context.ip = frame.boundary_next_pc & 0xFFFF
        return None
    if popped is not None:
        ctx.native_context.ip = popped.match_end + 1
        return None
    ctx.native_context.ip = ip + 1
    return None


@_handler(END)
def _h_end(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    if frame.frames:
        frame.frames.pop_back()
    ctx.native_context.ip = ip + 1
    return None


@_handler(BR)
def _h_br(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    depth, _ = decode_unsigned(frame.code, ip + 1)
    next_ip = _do_branch(depth, frame)
    if (next_ip) is None:
        ctx.native_context.ip = RETURN_SENTINEL_IP
        return None
    if frame.boundary_next_pc is not None:
        next_ip = frame.boundary_next_pc & 0xFFFF
    ctx.native_context.ip = next_ip
    return None


@_handler(BR_IF)
def _h_br_if(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    depth, next_ip = decode_unsigned(frame.code, ip + 1)
    cond = frame.values.pop_back()
    if cond == 0:
        ctx.native_context.ip = next_ip
        return None
    target_ip = _do_branch(depth, frame)
    if target_ip is None:
        ctx.native_context.ip = RETURN_SENTINEL_IP
        return None
    if frame.boundary_loops_to is not None:
        ctx.native_context.ip = frame.boundary_loops_to & 0xFFFF
        return None
    ctx.native_context.ip = target_ip
    return None


@_handler(BR_TABLE)
def _h_br_table(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    labels, default_lbl = frame.control_map.br_table(ip)
    index = _to_u32(frame.values.pop_back())
    depth = labels[index] if index < len(labels) else default_lbl
    next_ip = _do_branch(depth, frame)
    if (next_ip) is None:
        ctx.native_context.ip = RETURN_SENTINEL_IP
        return None
    ctx.native_context.ip = next_ip
    return None


@_handler(RETURN)
def _h_return(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    ctx.native_context.ip = RETURN_SENTINEL_IP
    return None


# CALL and CALL_INDIRECT are not in `_HANDLERS`: entering or returning from a
# nested WASM call is always a mandatory `step()` boundary (see
# `InterpreterCall.call_stack`), so `Interpreter.step()` intercepts both
# opcodes itself, before the generic dispatch table lookup, via
# `_enter_or_resolve_call()`.


@_handler(DROP)
def _h_drop(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    module = frame.context.module
    assert module is not None
    function = module.functions[frame.func_index - len(module.imports)]
    width = function.drop_widths.view().find(ip) if function.drop_widths is not None else None
    frame.values.pop_back()
    if width == 2:
        frame.values.pop_back()
    ctx.native_context.ip = ip + 1
    return None


@_handler(SELECT)
def _h_select(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    c = frame.values.pop_back()
    assert c is not None
    module = frame.context.module
    assert module is not None
    function = module.functions[frame.func_index - len(module.imports)]
    width = function.select_widths.view().find(ip) if function.select_widths is not None else None
    if width == 2:
        b_high = frame.values.pop_back()
        b_low = frame.values.pop_back()
        a_high = frame.values.pop_back()
        a_low = frame.values.pop_back()
        assert b_high is not None and b_low is not None and a_high is not None and a_low is not None
        pushed_low = frame.values.push_back(a_low if c != 0 else b_low)
        assert pushed_low
        pushed_high = frame.values.push_back(a_high if c != 0 else b_high)
        assert pushed_high
    else:
        b = frame.values.pop_back()
        a = frame.values.pop_back()
        assert a is not None and b is not None
        pushed = frame.values.push_back(a if c != 0 else b)
        assert pushed
    ctx.native_context.ip = ip + 1
    return None


@_handler(LOCAL_GET)
def _h_local_get(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    raw_slot, raw_width = local_base.raw_span(idx)
    assert frame.values.push_raw_from(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.native_context.ip = next_ip
    return None


@_handler(LOCAL_SET)
def _h_local_set(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    raw_slot, raw_width = local_base.raw_span(idx)
    frame.values.pop_raw_to(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.native_context.ip = next_ip
    return None


@_handler(LOCAL_TEE)
def _h_local_tee(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    raw_slot, raw_width = local_base.raw_span(idx)
    frame.values.copy_raw_to(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.native_context.ip = next_ip
    return None


@_handler(I32_CONST)
def _h_i32_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1)
    frame.values.push_back(_to_i32(val))
    ctx.native_context.ip = next_ip
    return None


@_handler(I64_CONST)
def _h_i64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1)
    assert frame.values.push_i64(_to_i64(val))
    ctx.native_context.ip = next_ip
    return None


@_handler(F32_CONST)
def _h_f32_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<f", frame.code[ip + 1 : ip + 5])[0]
    assert frame.values.push_f32(val)
    ctx.native_context.ip = ip + 5
    return None


@_handler(F64_CONST)
def _h_f64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<d", frame.code[ip + 1 : ip + 9])[0]
    assert frame.values.push_f64(val)
    ctx.native_context.ip = ip + 9
    return None


@_handler(GLOBAL_GET)
def _h_global_get(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    value = env.globals[idx]
    value_type = env.module.globals[idx].vtype
    if value_type == I64 or value_type == F64:
        pushed_low = frame.values.push_back(value & I32_MASK)
        assert pushed_low
        pushed_high = frame.values.push_back((value >> 32) & I32_MASK)
        assert pushed_high
    else:
        pushed = frame.values.push_back(value & I32_MASK)
        assert pushed
    ctx.native_context.ip = next_ip
    return None


@_handler(GLOBAL_SET)
def _h_global_set(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    value_type = env.module.globals[idx].vtype
    if value_type == I64 or value_type == F64:
        high = frame.values.pop_back() & I32_MASK
        low = frame.values.pop_back() & I32_MASK
        env.globals[idx] = low | (high << 32)
    else:
        env.globals[idx] = frame.values.pop_back() & I32_MASK
    ctx.native_context.ip = next_ip
    return None


# --- Loads (Dedicated per-opcode handlers with Bit 31 RAM Bypass) ---


def _vmmio_load(env: ExecEnv, addr: int, width: int, signed: bool) -> tuple[int, Trap | None]:
    if env.vmmio is None:
        return 0, Trap(TrapCode.VMMIO_NOT_CONFIGURED, addr)
    status, phys_addr = env.vmmio.access(addr, is_write=False, value=0)
    if status > VmmioStatus.OK_PHYSICAL:
        return 0, Trap(TrapCode.VMMIO_ACCESS, int(status))
    if status == VmmioStatus.OK_STATIC_DEVICE:
        return phys_addr, None
    if status == VmmioStatus.OK_PHYSICAL:
        assert env.phys_mem is not None
        assert phys_addr + width <= len(env.phys_mem)
        return (
            int.from_bytes(env.phys_mem[phys_addr : phys_addr + width], "little", signed=signed),
            None,
        )
    return 0, None


def _vmmio_store(env: ExecEnv, addr: int, val_bytes: bytes) -> Trap | None:
    if env.vmmio is None:
        return Trap(TrapCode.VMMIO_NOT_CONFIGURED, addr)
    status, phys_addr = env.vmmio.access(
        addr,
        is_write=True,
        value=int.from_bytes(val_bytes, "little"),
    )
    if status > VmmioStatus.OK_PHYSICAL:
        return Trap(TrapCode.VMMIO_ACCESS, int(status))
    if status == VmmioStatus.OK_PHYSICAL:
        assert env.phys_mem is not None
        assert phys_addr + len(val_bytes) <= len(env.phys_mem)
        env.phys_mem[phys_addr : phys_addr + len(val_bytes)] = val_bytes
    return None


@_handler(I32_LOAD)
def _h_i32_load(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    if addr & 0x8000_0000:
        value, trap = _vmmio_load(env, addr, 4, signed=True)
        if trap is not None:
            return trap
        pushed = frame.values.push_back(value)
        assert pushed
    else:
        if env.memory is None or addr + 4 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 4], "little", signed=True))
    ctx.native_context.ip = next_ip
    return None


@_handler(I32_LOAD8_S)
def _h_i32_load8_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    if addr & 0x8000_0000:
        value, trap = _vmmio_load(env, addr, 1, signed=True)
        if trap is not None:
            return trap
        pushed = frame.values.push_back(value)
        assert pushed
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 1], "little", signed=True))
    ctx.native_context.ip = next_ip
    return None


@_handler(I32_LOAD8_U)
def _h_i32_load8_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    if addr & 0x8000_0000:
        value, trap = _vmmio_load(env, addr, 1, signed=False)
        if trap is not None:
            return trap
        pushed = frame.values.push_back(value)
        assert pushed
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        frame.values.push_back(env.memory[addr])
    ctx.native_context.ip = next_ip
    return None


@_handler(I32_LOAD16_S)
def _h_i32_load16_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    if addr & 0x8000_0000:
        value, trap = _vmmio_load(env, addr, 2, signed=True)
        if trap is not None:
            return trap
        pushed = frame.values.push_back(value)
        assert pushed
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 2], "little", signed=True))
    ctx.native_context.ip = next_ip
    return None


@_handler(I32_LOAD16_U)
def _h_i32_load16_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    if addr & 0x8000_0000:
        value, trap = _vmmio_load(env, addr, 2, signed=False)
        if trap is not None:
            return trap
        pushed = frame.values.push_back(value)
        assert pushed
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 2], "little", signed=False))
    ctx.native_context.ip = next_ip
    return None


# --- Stores (Dedicated per-opcode handlers with Bit 31 RAM Bypass) ---


@_handler(I32_STORE)
def _h_i32_store(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    value = _to_i32(frame.values.pop_back())
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    raw_val = (value & 0xFFFFFFFF).to_bytes(4, "little")
    if addr & 0x8000_0000:
        trap = _vmmio_store(env, addr, raw_val)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + 4 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        env.memory[addr : addr + 4] = raw_val
    ctx.native_context.ip = next_ip
    return None


@_handler(I32_STORE8)
def _h_i32_store8(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    value = frame.values.pop_back() & 0xFF
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    raw_val = value.to_bytes(1, "little")
    if addr & 0x8000_0000:
        trap = _vmmio_store(env, addr, raw_val)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        env.memory[addr] = value
    ctx.native_context.ip = next_ip
    return None


@_handler(I32_STORE16)
def _h_i32_store16(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    value = frame.values.pop_back() & 0xFFFF
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    raw_val = value.to_bytes(2, "little")
    if addr & 0x8000_0000:
        trap = _vmmio_store(env, addr, raw_val)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        env.memory[addr : addr + 2] = raw_val
    ctx.native_context.ip = next_ip
    return None


# --- Memory Size / Grow ---


@_handler(MEMORY_SIZE)
def _h_memory_size(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    if env.memory is None:
        return Trap(TrapCode.MEMORY_SECTION_MISSING)
    frame.values.push_back(len(env.memory) // PAGE_SIZE)
    ctx.native_context.ip = ip + 2
    return None


@_handler(MEMORY_GROW)
def _h_memory_grow(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    delta_pages = _to_u32(frame.values.pop_back())
    if env.memory is None:
        frame.values.push_back(_to_i32(0xFFFFFFFF))
    else:
        old_pages = len(env.memory) // PAGE_SIZE
        maximum = env.memory_decl.max_pages
        if maximum is None or maximum > 65536:
            maximum = 65536
        if delta_pages > maximum - old_pages:
            frame.values.push_back(_to_i32(0xFFFFFFFF))
        else:
            env.memory.extend(bytes(delta_pages * PAGE_SIZE))
            frame.values.push_back(old_pages)
    ctx.native_context.ip = ip + 2
    return None


# --- Comparisons (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_EQZ)
def _h_i32_eqz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    frame.values.push_back(1 if frame.values.pop_back() == 0 else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_EQ)
def _h_i32_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) == _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_NE)
def _h_i32_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) != _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_LT_S)
def _h_i32_lt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) < _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_LT_U)
def _h_i32_lt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) < _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_GT_S)
def _h_i32_gt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) > _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_GT_U)
def _h_i32_gt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) > _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_LE_S)
def _h_i32_le_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) <= _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_LE_U)
def _h_i32_le_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) <= _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_GE_S)
def _h_i32_ge_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) >= _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_GE_U)
def _h_i32_ge_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) >= _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


# --- Unary Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_CLZ)
def _h_i32_clz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(32 if v == 0 else 32 - v.bit_length())
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_CTZ)
def _h_i32_ctz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = _to_u32(frame.values.pop_back())
    if v == 0:
        res = 32
    else:
        n = 0
        while (v & 1) == 0:
            v >>= 1
            n += 1

        res = n

    frame.values.push_back(res)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_POPCNT)
def _h_i32_popcnt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(bin(v).count("1"))
    ctx.native_context.ip = ip + 1
    return None


# --- Binary Arithmetic & Bitwise Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_ADD)
def _h_i32_add(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a + b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_SUB)
def _h_i32_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a - b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_MUL)
def _h_i32_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a * b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_DIV_S)
def _h_i32_div_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_i32(frame.values.pop_back())
    a = _to_i32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    if a == -2147483648 and b == -1:
        return Trap(TrapCode.INTEGER_OVERFLOW)
    q = abs(a) // abs(b)
    frame.values.push_back(_to_i32(-q if (a < 0) != (b < 0) else q))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_DIV_U)
def _h_i32_div_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    frame.values.push_back(_to_i32(a // b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_REM_S)
def _h_i32_rem_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_i32(frame.values.pop_back())
    a = _to_i32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    r = abs(a) % abs(b)
    frame.values.push_back(_to_i32(-r if a < 0 else r))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_REM_U)
def _h_i32_rem_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    frame.values.push_back(_to_i32(a % b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_AND)
def _h_i32_and(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) & _to_u32(b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_OR)
def _h_i32_or(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) | _to_u32(b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_XOR)
def _h_i32_xor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) ^ _to_u32(b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_SHL)
def _h_i32_shl(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) << (_to_u32(b) & 31)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_SHR_S)
def _h_i32_shr_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_i32(a) >> (_to_u32(b) & 31)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_SHR_U)
def _h_i32_shr_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) >> (_to_u32(b) & 31)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_ROTL)
def _h_i32_rotl(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.push_back(_to_i32(((v << n) | (v >> (32 - n))) & I32_MASK if n else v))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_ROTR)
def _h_i32_rotr(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.push_back(_to_i32(((v >> n) | (v << (32 - n))) & I32_MASK if n else v))
    ctx.native_context.ip = ip + 1
    return None


# Helper conversion utilities
I64_MASK = 0xFFFF_FFFF_FFFF_FFFF


def _to_i64(v: int) -> int:
    v &= I64_MASK
    return v - (1 << 64) if v & 0x8000_0000_0000_0000 else v


def _to_u64(v: int) -> int:
    return v & I64_MASK


# --- i64 / f32 / f64 Memory Handlers ---


@_handler(I64_LOAD)
def _h_i64_load(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if addr & 0x8000_0000:
        val, trap = _vmmio_load(env, addr, 8, signed=True)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + 8 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        val = struct.unpack("<q", env.memory[addr : addr + 8])[0]
    assert frame.values.push_i64(val)
    ctx.native_context.ip = next_ip
    return None


def _h_i64_load_narrow(
    ctx: InterpreterContext,
    sp: NativeValueStack,
    local_base: _LocalStackWindow,
    tos: int,
    width: int,
    signed: bool,
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if addr & 0x8000_0000:
        val, trap = _vmmio_load(env, addr, width, signed=signed)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + width > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        val = int.from_bytes(env.memory[addr : addr + width], "little", signed=signed)
    assert frame.values.push_i64(_to_i64(val))
    ctx.native_context.ip = next_ip
    return None


@_handler(I64_LOAD8_S)
def _h_i64_load8_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 1, True)


@_handler(I64_LOAD8_U)
def _h_i64_load8_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 1, False)


@_handler(I64_LOAD16_S)
def _h_i64_load16_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 2, True)


@_handler(I64_LOAD16_U)
def _h_i64_load16_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 2, False)


@_handler(I64_LOAD32_S)
def _h_i64_load32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 4, True)


@_handler(I64_LOAD32_U)
def _h_i64_load32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 4, False)


@_handler(I64_STORE)
def _h_i64_store(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    val = frame.values.pop_i64()
    assert val is not None
    addr = _to_u32(frame.values.pop_back()) + offset
    raw_val = struct.pack("<q", int(val))
    if addr & 0x8000_0000:
        trap = _vmmio_store(env, addr, raw_val)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + 8 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        env.memory[addr : addr + 8] = raw_val
    ctx.native_context.ip = next_ip
    return None


def _h_i64_store_narrow(
    ctx: InterpreterContext,
    sp: NativeValueStack,
    local_base: _LocalStackWindow,
    tos: int,
    width: int,
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    val = frame.values.pop_i64()
    assert val is not None
    addr = _to_u32(frame.values.pop_back()) + offset
    raw_val = (int(val) & I64_MASK).to_bytes(8, "little")[:width]
    if addr & 0x8000_0000:
        trap = _vmmio_store(env, addr, raw_val)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + width > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        env.memory[addr : addr + width] = raw_val
    ctx.native_context.ip = next_ip
    return None


@_handler(I64_STORE8)
def _h_i64_store8(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, sp, local_base, tos, 1)


@_handler(I64_STORE16)
def _h_i64_store16(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, sp, local_base, tos, 2)


@_handler(I64_STORE32)
def _h_i64_store32(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, sp, local_base, tos, 4)


@_handler(F32_LOAD)
def _h_f32_load(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    val = struct.unpack("<f", env.memory[addr : addr + 4])[0]
    assert frame.values.push_f32(val)
    ctx.native_context.ip = next_ip
    return None


@_handler(F32_STORE)
def _h_f32_store(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    val = frame.values.pop_f32()
    assert val is not None
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    env.memory[addr : addr + 4] = struct.pack("<f", val)
    ctx.native_context.ip = next_ip
    return None


@_handler(F64_LOAD)
def _h_f64_load(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    val = struct.unpack("<d", env.memory[addr : addr + 8])[0]
    assert frame.values.push_f64(val)
    ctx.native_context.ip = next_ip
    return None


@_handler(F64_STORE)
def _h_f64_store(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    val = frame.values.pop_f64()
    assert val is not None
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    env.memory[addr : addr + 8] = struct.pack("<d", val)
    ctx.native_context.ip = next_ip
    return None


# --- Const Handlers ---


@_handler(I64_CONST)
def _h_i64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1)
    assert frame.values.push_i64(_to_i64(val))
    ctx.native_context.ip = next_ip
    return None


@_handler(F32_CONST)
def _h_f32_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<f", frame.code[ip + 1 : ip + 5])[0]
    assert frame.values.push_f32(val)
    ctx.native_context.ip = ip + 5
    return None


@_handler(F64_CONST)
def _h_f64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<d", frame.code[ip + 1 : ip + 9])[0]
    assert frame.values.push_f64(val)
    ctx.native_context.ip = ip + 9
    return None


# --- i64 Comparison & Arithmetic Handlers ---


@_handler(I64_EQZ)
def _h_i64_eqz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = frame.values.pop_i64()
    assert v is not None
    frame.values.push_back(1 if v == 0 else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_EQ)
def _h_i64_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) == _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_NE)
def _h_i64_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) != _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_LT_S)
def _h_i64_lt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) < _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_LT_U)
def _h_i64_lt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) < _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_ADD)
def _h_i64_add(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a + b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_SUB)
def _h_i64_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a - b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_MUL)
def _h_i64_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a * b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_DIV_S)
def _h_i64_div_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_i64()
    a = frame.values.pop_i64()
    assert b is not None and a is not None
    b = _to_i64(b)
    a = _to_i64(a)
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    if a == -0x8000_0000_0000_0000 and b == -1:
        return Trap(TrapCode.INTEGER_OVERFLOW)
    q = abs(a) // abs(b)
    assert frame.values.push_i64(_to_i64(-q if (a < 0) != (b < 0) else q))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_DIV_U)
def _h_i64_div_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_i64()
    a = frame.values.pop_i64()
    assert b is not None and a is not None
    b = _to_u64(b)
    a = _to_u64(a)
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    assert frame.values.push_i64(_to_i64(a // b))
    ctx.native_context.ip = ip + 1
    return None


# --- f32 Arithmetic Handlers ---


@_handler(F32_ADD)
def _h_f32_add(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a + b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_SUB)
def _h_f32_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a - b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_MUL)
def _h_f32_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a * b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_DIV)
def _h_f32_div(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_wasm_float_div(a, b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_SQRT)
def _h_f32_sqrt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(math.sqrt(a) if a >= 0 else float("nan")))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_MIN)
def _h_f32_min(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_f32_min(a, b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_MAX)
def _h_f32_max(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_f32_max(a, b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_LT)
def _h_f32_lt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_LE)
def _h_f32_le(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_GT)
def _h_f32_gt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_GE)
def _h_f32_ge(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.native_context.ip = ip + 1
    return None


# --- f64 Arithmetic Handlers ---


@_handler(F64_ADD)
def _h_f64_add(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a + b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_SUB)
def _h_f64_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a - b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_MUL)
def _h_f64_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a * b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_DIV)
def _h_f64_div(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(_wasm_float_div(a, b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_SQRT)
def _h_f64_sqrt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(math.sqrt(a) if a >= 0 else float("nan")))
    ctx.native_context.ip = ip + 1
    return None


# --- Conversion Handlers ---


def _truncate_float(value: float, bits: int, signed: bool) -> int | None:
    if math.isnan(value) or math.isinf(value):
        return None
    if signed:
        lower = -(1 << (bits - 1))
        upper = 1 << (bits - 1)
    else:
        lower = -1
        upper = 1 << bits
    if (value < lower if signed else value <= lower) or value >= upper:
        return None
    return int(value)


@_handler(I32_TRUNC_F32_S)
def _h_i32_trunc_f32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 32, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(converted))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_TRUNC_F64_S)
def _h_i32_trunc_f64_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 32, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(converted))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_TRUNC_F32_S)
def _h_i64_trunc_f32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 64, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_TRUNC_F32_U)
def _h_i64_trunc_f32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 64, False)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_TRUNC_F64_S)
def _h_i64_trunc_f64_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 64, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_TRUNC_F64_U)
def _h_i64_trunc_f64_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 64, False)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_CONVERT_I32_S)
def _h_f32_convert_i32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_i32(frame.values.pop_back())
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_CONVERT_I32_U)
def _h_f32_convert_i32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_u32(frame.values.pop_back())
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_CONVERT_I64_S)
def _h_f32_convert_i64_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_CONVERT_I64_U)
def _h_f32_convert_i64_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f32(_to_f32(float(_to_u64(a))))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_CONVERT_I32_S)
def _h_f64_convert_i32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_i32(frame.values.pop_back())
    assert frame.values.push_f64(float(a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_CONVERT_I32_U)
def _h_f64_convert_i32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_u32(frame.values.pop_back())
    assert frame.values.push_f64(float(a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_CONVERT_I64_S)
def _h_f64_convert_i64_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f64(float(a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_CONVERT_I64_U)
def _h_f64_convert_i64_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f64(float(_to_u64(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_PROMOTE_F32)
def _h_f64_promote_f32(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f64(float("nan") if math.isnan(a) else float(a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_DEMOTE_F64)
def _h_f32_demote_f64(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f32(float("nan") if math.isnan(a) else _to_f32(float(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_ABS)
def _h_f32_abs(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(abs(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_NEG)
def _h_f32_neg(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(-a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_ABS)
def _h_f64_abs(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(abs(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_NEG)
def _h_f64_neg(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(-a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_EQ)
def _h_f32_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_NE)
def _h_f32_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_EQ)
def _h_f64_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_NE)
def _h_f64_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_REM_S)
def _h_i64_rem_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_i64()
    a = frame.values.pop_i64()
    assert b is not None and a is not None
    b = _to_i64(b)
    a = _to_i64(a)
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    r = abs(a) % abs(b)
    assert frame.values.push_i64(_to_i64(-r if a < 0 else r))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_REM_U)
def _h_i64_rem_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_i64()
    a = frame.values.pop_i64()
    assert b is not None and a is not None
    b = _to_u64(b)
    a = _to_u64(a)
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    assert frame.values.push_i64(_to_i64(a % b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_AND)
def _h_i64_and(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a & b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_OR)
def _h_i64_or(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a | b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_XOR)
def _h_i64_xor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a ^ b))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_SHL)
def _h_i64_shl(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    assert frame.values.push_i64(_to_i64((v << (k % 64)) & I64_MASK))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_SHR_S)
def _h_i64_shr_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    assert frame.values.push_i64(_to_i64(_to_i64(v) >> (k % 64)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_SHR_U)
def _h_i64_shr_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    assert frame.values.push_i64(_to_i64(_to_u64(v) >> (k % 64)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_ROTL)
def _h_i64_rotl(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    k %= 64
    v = _to_u64(v)
    rotated = ((v << k) | (v >> (64 - k))) & I64_MASK if k else v
    assert frame.values.push_i64(_to_i64(rotated))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_ROTR)
def _h_i64_rotr(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    k %= 64
    v = _to_u64(v)
    rotated = ((v >> k) | (v << (64 - k))) & I64_MASK if k else v
    assert frame.values.push_i64(_to_i64(rotated))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_CLZ)
def _h_i64_clz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    if v == 0:
        assert frame.values.push_i64(64)
    else:
        assert frame.values.push_i64(64 - v.bit_length())
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_CTZ)
def _h_i64_ctz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    if v == 0:
        assert frame.values.push_i64(64)
    else:
        assert frame.values.push_i64((v & -v).bit_length() - 1)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_POPCNT)
def _h_i64_popcnt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    assert frame.values.push_i64(bin(v).count("1"))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_GT_S)
def _h_i64_gt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) > _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_GT_U)
def _h_i64_gt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) > _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_LE_S)
def _h_i64_le_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) <= _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_LE_U)
def _h_i64_le_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) <= _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_GE_S)
def _h_i64_ge_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) >= _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_GE_U)
def _h_i64_ge_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) >= _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return None


# --- Additional F32 / F64 Math Handlers ---


@_handler(F32_CEIL)
def _h_f32_ceil(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_integral_round(a, "ceil")))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_FLOOR)
def _h_f32_floor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_integral_round(a, "floor")))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_TRUNC)
def _h_f32_trunc(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_integral_round(a, "trunc")))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_NEAREST)
def _h_f32_nearest(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_nearest(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_COPYSIGN)
def _h_f32_copysign(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(math.copysign(a, b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_CEIL)
def _h_f64_ceil(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_integral_round(a, "ceil"))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_FLOOR)
def _h_f64_floor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_integral_round(a, "floor"))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_TRUNC)
def _h_f64_trunc(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_integral_round(a, "trunc"))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_NEAREST)
def _h_f64_nearest(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_nearest(a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_COPYSIGN)
def _h_f64_copysign(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(math.copysign(a, b)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_MIN)
def _h_f64_min(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    if math.isnan(a) or math.isnan(b):
        res = float("nan")
    elif a == b == 0.0:
        res = -0.0 if math.copysign(1.0, a) < 0 or math.copysign(1.0, b) < 0 else 0.0
    else:
        res = min(a, b)
    assert frame.values.push_f64(float(res))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_MAX)
def _h_f64_max(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    if math.isnan(a) or math.isnan(b):
        res = float("nan")
    elif a == b == 0.0:
        res = 0.0 if math.copysign(1.0, a) > 0 or math.copysign(1.0, b) > 0 else -0.0
    else:
        res = max(a, b)
    assert frame.values.push_f64(float(res))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_LT)
def _h_f64_lt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_LE)
def _h_f64_le(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_GT)
def _h_f64_gt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_GE)
def _h_f64_ge(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.native_context.ip = ip + 1
    return None


# --- Conversion & Reinterpret Handlers ---


@_handler(I32_EXTEND8_S)
def _h_i32_extend8_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u32(frame.values.pop_back()) & 0xFF
    pushed = frame.values.push_back(_to_i32(value - 0x100 if value & 0x80 else value))
    assert pushed
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_EXTEND16_S)
def _h_i32_extend16_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u32(frame.values.pop_back()) & 0xFFFF
    pushed = frame.values.push_back(_to_i32(value - 0x10000 if value & 0x8000 else value))
    assert pushed
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_EXTEND8_S)
def _h_i64_extend8_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u64(frame.values.pop_i64()) & 0xFF
    assert frame.values.push_i64(_to_i64(value - 0x100 if value & 0x80 else value))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_EXTEND16_S)
def _h_i64_extend16_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u64(frame.values.pop_i64()) & 0xFFFF
    assert frame.values.push_i64(_to_i64(value - 0x10000 if value & 0x8000 else value))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_EXTEND32_S)
def _h_i64_extend32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u64(frame.values.pop_i64()) & 0xFFFF_FFFF
    assert frame.values.push_i64(_to_i64(value - 0x1_0000_0000 if value & 0x8000_0000 else value))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_WRAP_I64)
def _h_i32_wrap_i64(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    frame.values.push_back(_to_i32(a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_TRUNC_F32_U)
def _h_i32_trunc_f32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(int(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_TRUNC_F64_U)
def _h_i32_trunc_f64_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(int(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_EXTEND_I32_S)
def _h_i64_extend_i32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    assert frame.values.push_i64(_to_i64(a))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_EXTEND_I32_U)
def _h_i64_extend_i32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    assert frame.values.push_i64(_to_i64(_to_u32(a)))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I32_REINTERPRET_F32)
def _h_i32_reinterpret_f32(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    u = struct.unpack("<i", struct.pack("<f", a))[0]
    frame.values.push_back(_to_i32(u))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F32_REINTERPRET_I32)
def _h_f32_reinterpret_i32(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    a = _to_u32(a)
    f = struct.unpack("<f", struct.pack("<I", a))[0]
    assert frame.values.push_f32(_to_f32(f))
    ctx.native_context.ip = ip + 1
    return None


@_handler(I64_REINTERPRET_F64)
def _h_i64_reinterpret_f64(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    u = struct.unpack("<q", struct.pack("<d", a))[0]
    assert frame.values.push_i64(_to_i64(u))
    ctx.native_context.ip = ip + 1
    return None


@_handler(F64_REINTERPRET_I64)
def _h_f64_reinterpret_i64(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    a = _to_u64(a)
    d = struct.unpack("<d", struct.pack("<Q", a))[0]
    assert frame.values.push_f64(float(d))
    ctx.native_context.ip = ip + 1
    return None


# Complete the direct-indexed table after decorator registration without
# materializing an iterator or a temporary tuple. The table is then read by
# every interpreter dispatch step, so this storage is required runtime state.
while len(_HANDLERS) < 256:
    _HANDLERS.append(None)

"""
experiments/pysim/tier2_runtime/interpreter/interpreter.py
A minimal reference interpreter for the wasm_opcodes subset, used as the
correctness oracle the JIT's output is checked against -- mirroring the
real project's own "interpreter + JIT, cross-checked" architecture
(docs/components/tier2_runtime/interpreter.md /
docs/components/tier3_plugins/jit_compiler.md), just without the ARM/Copy-and-
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
`ExecutionContext` owns the fixed-capacity operand, local, control-frame, and
call-frame storage shared by interpreter, JIT, and debugger execution. A
`CallFrame` is an activation descriptor and a window into that storage; it does
not own a per-function stack. The descriptor also carries decoded instruction
metadata needed to resume at `ip`, without adding a fifth handler argument.
"""

from __future__ import annotations

import ctypes
import math
import struct
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from bump_allocator import BumpAllocator
from config import (
    FB_CONF_MAX_VALUE_STACK,
    FB_CONF_RUNTIME_YIELD_THRESHOLD,
)
from tier2_runtime.interpreter.control_flow import (
    FB_CONF_MAX_NESTING_DEPTH,
    OpcodeAttribute,
    build_control_map,
    opcode_has_attribute,
)
from tier2_runtime.abi.interpreter_abi import (
    EXECUTION_CONTEXT_FLAG_STOP_AT_BLOCK_BOUNDARY,
    NATIVE_VALUE_STACK_CAPACITY,
    CallFrameNative,
    ControlMapEntryNative,
    ExecutionContextABI,
    FunctionExecutionViewNative,
    FunctionTypeExecutionViewNative,
    ModuleExecutionViewNative,
    NativeGlobalStorage,
    NativeValueStack,
    TableExecutionViewNative,
)
from tier2_runtime.wasm.leb128 import decode_signed, decode_unsigned
from tier2_runtime.abi.native_stack_abi import (
    ControlFrameKind,
    NativeCallFrameStack,
    NativeControlStack,
    _ControlFrameWindow,
    _LocalStackWindow,
)
from system_containers import StaticVector
from tier2_runtime.syscall.hostcall import VdmaTransfer
from tier2_runtime.abi.jit_abi import (
    EMPTY_NATIVE_DISPATCH_SNAPSHOT,
    NativeBlockVisitHistory,
    NativeDispatchSnapshot,
)
from tier2_runtime.observability.logging_interface import LogLevel, LoggerPort
from tier2_runtime.vmmio.controller import (
    FC_DYNAMIC,
    FC_PASSTHROUGH,
    FC_SHM,
    VmmioAddress,
    VMMIOController,
    VmmioStatus,
)
from tier2_runtime.wasm.module import (
    F32,
    F64,
    I32,
    I64,
    FunctionTable,
    Memory,
    Module,
    value_slot_width,
)
from tier2_runtime.wasm.opcodes import (
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
    FC_I32_TRUNC_SAT_F32_S,
    FC_I32_TRUNC_SAT_F64_U,
    FC_I64_TRUNC_SAT_F32_S,
    FC_I64_TRUNC_SAT_F64_U,
    FC_MEMORY_COPY,
    FC_MEMORY_FILL,
    FC_PREFIX,
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

from tier2_runtime.abi import native_abi as _native_abi

NATIVE_RUNTIME_PROFILE_STATS_AVAILABLE = bool(_native_abi.RUNTIME_PROFILE_STATS_AVAILABLE)
NATIVE_JIT_HOTSPOT_PROFILING_AVAILABLE = bool(_native_abi.JIT_HOTSPOT_PROFILING_AVAILABLE)

NativeDispatchMetrics = tuple[
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    NativeBlockVisitHistory,
]
NativeDispatchEntryPoint = Callable[..., _native_abi.NativeDispatchResult]


def select_native_dispatch_entry(
    collect_stats: bool, collect_hotspots: bool
) -> NativeDispatchEntryPoint:
    """Select one fixed native dispatcher while composing a Runtime instance."""

    if collect_stats:
        if collect_hotspots:
            return _native_abi.run_native_dispatch_stats_hotspots
        return _native_abi.run_native_dispatch_stats
    if collect_hotspots:
        return _native_abi.run_native_dispatch_hotspots
    return _native_abi.run_native_dispatch


I32_MASK = 0xFFFFFFFF
PAGE_SIZE = 65536
# Reserved execution-PC values.  `-1` is kept only in the local continuation
# because it cannot be confused with a bytecode offset; the public unified PC
# is a named reserved value consumed by RuntimeEngine before normal lookup.
RETURN_SENTINEL_IP = -1
RETURN_SENTINEL_PC = 0xFFFF_FFFF
NATIVE_DISPATCH_YIELD = 5
NATIVE_DISPATCH_DEBUG_STOP = 7
NATIVE_DISPATCH_CALL_BOUNDARY = 6
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


class WasmHostFunction(Protocol):
    """Host import callable whose arity is carried by the validated WASM type."""

    def __call__(self, *args: WasmNumber) -> WasmNumber | None: ...


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
    globals: NativeGlobalStorage
    tables: StaticVector[FunctionTable]
    host_functions: StaticVector[WasmHostFunction | None]
    memory_decl: Memory
    vmmio: VMMIOController | None = None
    phys_mem: bytearray | None = None
    vdma_transfer: VdmaTransfer | None = None


@contextmanager
def _native_linear_memory_scope(
    context: ExecutionContext, memory: bytearray | None
) -> Iterator[None]:
    """Publish the cached linear-memory address while native code is running."""
    memory_size = len(memory) if memory is not None else 0
    context.linear_memory_host_base = context.linear_memory_address(memory)
    context.linear_memory_size = memory_size
    context.mem_size = min(memory_size, 0xFFFF_FFFF)
    try:
        yield
    finally:
        context.linear_memory_host_base = None
        context.linear_memory_size = 0
        context.mem_size = 0


@dataclass(slots=True)
class InterpreterBindings:
    """Concrete resources supplied while instantiating one WASM module."""

    memory: bytearray
    memory_decl: Memory
    imported_memory: bool
    host_functions: StaticVector[WasmHostFunction | None]
    globals: StaticVector[int]
    tables: StaticVector[FunctionTable]
    vdma_transfer: VdmaTransfer | None = None

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
        host_functions: StaticVector[WasmHostFunction | None],
        *,
        vdma_transfer: VdmaTransfer | None = None,
    ) -> InterpreterBindings:
        """Create bindings for a host memory and dense function-import table."""
        return cls(
            memory=memory,
            memory_decl=Memory(min_pages=0, max_pages=None),
            imported_memory=False,
            host_functions=host_functions,
            globals=StaticVector(capacity=0),
            tables=StaticVector(capacity=0),
            vdma_transfer=vdma_transfer,
        )


class CallFrameStack:
    """Python identity view backed by the native fixed-capacity call stack."""

    __slots__ = ("_frames", "_native")

    def __init__(self, native: NativeCallFrameStack, capacity: int):
        self._native = native
        self._frames: StaticVector[CallFrame] = StaticVector(capacity=capacity)

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
        native_index = len(self._native)
        if not self._native.push_back(frame.native):
            return False
        frame._native_slot = native_index
        if not self._frames.push_back(frame):
            self._native.pop_back()
            frame._native_slot = -1
            return False
        frame._native = None
        return True

    def pop_back(self) -> CallFrame:
        assert len(self._frames) == len(self._native)
        frame = self._frames.pop_back()
        assert frame is not None
        assert frame.native.func_index == self._native[-1].func_index
        self._native.pop_back()
        frame._native_slot = -1
        frame._native = None
        return frame

    def sync_from_native(self, context: ExecutionContext, env: ExecEnv) -> None:
        """Reconcile Python wrappers with the Native stack without copying records."""
        adapter = context.module_execution
        assert adapter is not None
        common = 0
        common_limit = min(len(self._frames), len(self._native))
        while common < common_limit:
            native_frame = self._native[common]
            frame = self._frames[common]
            if (
                frame._native_slot != common
                or frame.func_index != native_frame.func_index
                or frame.frame_offset != native_frame.local_base
                or frame.control_base != native_frame.control_base
            ):
                break
            common += 1
        while len(self._frames) > common:
            frame = self._frames.pop_back()
            assert frame is not None
            frame._native_slot = -1
            frame._native = None
        assert len(self._native) <= self._frames.capacity
        while len(self._frames) < len(self._native):
            index = len(self._frames)
            native_frame = self._native[index]
            template = adapter.template(native_frame.func_index)
            frame = CallFrame(
                context,
                native_frame.func_index,
                native_frame.local_base,
                env,
                template=template,
                native_slot=index,
            )
            self._frames.append(frame)
        context.local_stack.set_size(context.local_offset)
        context.call_offset = len(self._native)


class ExecutionContext(ExecutionContextABI):
    """Single Python/native execution context and its owned fixed-capacity stacks."""

    _fields_ = (("debug_control", ctypes.c_void_p),)

    __slots__ = (
        "_allocator",
        "_arena_offset",
        "_arena_size",
        "_context_ptr",
        "_context_view",
        "_linear_memory_address",
        "_linear_memory_owner",
        "_linear_memory_size",
        "_native_dispatch_call",
        "_native_result",
        "_native_step_call",
        "_workspace_released",
        "call_frame_stack",
        "control_frame_stack",
        "local_stack",
        "module",
        "module_execution",
        "native_call_frames",
        "stack",
    )

    def __init__(
        self,
        module: Module | None = None,
        *,
        debug_control: int | None = None,
        allocator: BumpAllocator | None = None,
    ):
        """Own the ABI record and fixed-capacity stacks used by native execution."""
        super().__init__()
        self.stack: NativeValueStack = NativeValueStack(capacity=FB_CONF_MAX_VALUE_STACK)
        self.local_stack: NativeValueStack = NativeValueStack(capacity=FB_CONF_MAX_LOCAL_STACK)
        self.debug_control = debug_control
        self._context_ptr = ctypes.cast(ctypes.pointer(self), ctypes.c_void_p)
        self._context_view = memoryview(self)
        self.sp_capacity = self.stack.capacity
        self.local_capacity = self.local_stack.capacity
        self._linear_memory_address = 0
        self._linear_memory_owner: bytearray | None = None
        self._linear_memory_size = -1
        self._native_step_call = _native_abi.NativeStepCall()
        self._native_dispatch_call = _native_abi.NativeDispatchCall()
        self._native_result = _native_abi.NativeResult()
        self.module = module
        self.module_execution: NativeModuleExecution | None = None
        self.native_call_frames = NativeCallFrameStack(FB_CONF_MAX_NESTING_DEPTH)
        self.call_frame_stack = CallFrameStack(
            self.native_call_frames,
            FB_CONF_MAX_NESTING_DEPTH,
        )
        self.control_frame_stack: NativeControlStack = NativeControlStack(FB_CONF_MAX_NESTING_DEPTH)
        self.call_stack = self.native_call_frames.address
        self.call_base = 0
        self.call_offset = 0
        self._allocator = allocator
        self._arena_offset: int | None = None
        self._arena_size = (
            ctypes.sizeof(self)
            + ctypes.sizeof(self.stack.native)
            + ctypes.sizeof(self.local_stack.native)
            + ctypes.sizeof(self.native_call_frames.native)
            + ctypes.sizeof(self.control_frame_stack.native)
            + ctypes.sizeof(self._native_step_call)
            + ctypes.sizeof(self._native_dispatch_call)
            + ctypes.sizeof(self._native_result)
        )
        self._workspace_released = False
        if allocator is not None:
            self._arena_offset = allocator.acquire(self._arena_size, alignment=8)

    @property
    def arena_offset(self) -> int | None:
        return self._arena_offset

    @property
    def arena_size(self) -> int:
        return self._arena_size

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Move the active workspace accounting to the owning runtime arena."""

        if self._allocator is allocator and not self._workspace_released:
            return
        if self._allocator is not None and not self._workspace_released:
            assert self._arena_offset is not None
            self._allocator.release(self._arena_offset, self._arena_size, alignment=8)
        self._allocator = allocator
        self._arena_offset = allocator.acquire(self._arena_size, alignment=8)
        self._workspace_released = False

    def release_workspace(self) -> None:
        """Return the bounded native stacks for reuse after a call completes."""

        if self._allocator is None or self._workspace_released:
            return
        assert self._arena_offset is not None
        self._allocator.release(self._arena_offset, self._arena_size, alignment=8)
        self._workspace_released = True

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        """Address of this context's own ABI storage."""
        return self._context_ptr

    @property
    def context_view(self) -> memoryview:
        """Cached byte view of this context's own ABI storage."""
        return self._context_view

    @property
    def sp_ptr(self) -> ctypes.c_void_p:
        return self.stack.value_ptr(len(self.stack))

    @property
    def locals_ptr(self) -> ctypes.c_void_p:
        return self.local_stack.value_ptr()

    def attach_module_execution(self, execution: NativeModuleExecution) -> None:
        assert self.module_execution is None
        self.module_execution = execution

    def linear_memory_address(self, memory: bytearray | None) -> int:
        """Return a cached address, refreshing it only when memory grows or changes."""
        memory_size = len(memory) if memory is not None else 0
        if memory is None or memory_size == 0:
            self._linear_memory_owner = memory
            self._linear_memory_size = memory_size
            self._linear_memory_address = 0
            return 0
        if self._linear_memory_owner is not memory or self._linear_memory_size != memory_size:
            buffer_type = ctypes.c_ubyte * memory_size
            memory_buffer = buffer_type.from_buffer(memory)
            self._linear_memory_address = ctypes.addressof(memory_buffer)
            self._linear_memory_owner = memory
            self._linear_memory_size = memory_size
            del memory_buffer
        return self._linear_memory_address

    def begin_call_frame(
        self,
        raw_args: Sequence[int],
        func_index: int,
        env: ExecEnv,
    ) -> CallFrame:
        """Push one frame's locals directly into the context-owned Native stack."""
        frame_offset = self.local_offset
        template = None
        if self.module_execution is not None:
            template = self.module_execution.template(func_index)
        frame = CallFrame(
            self,
            func_index=func_index,
            frame_offset=frame_offset,
            env=env,
            template=template,
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
        self.call_offset = len(self.call_frame_stack)
        return frame

    def end_call_frame(self, frame: CallFrame) -> None:
        """Pop the active frame and its locals from the context stacks."""
        assert self.call_frame_stack
        assert self.call_frame_stack[-1] is frame
        self.local_stack.truncate(frame.frame_offset)
        self.local_offset = frame.frame_offset
        self.call_frame_stack.pop_back()
        self.call_offset = len(self.call_frame_stack)

    def bind_handler_state(self, ip: int, frame: CallFrame) -> None:
        """Publish the current native handler state in the execution context."""
        assert self.call_frame_stack
        assert self.call_frame_stack[-1] is frame
        self.ip = ip


assert ExecutionContext.debug_control.offset == ctypes.sizeof(ExecutionContextABI)
assert ctypes.sizeof(ExecutionContext) == ctypes.sizeof(ExecutionContextABI) + ctypes.sizeof(
    ctypes.c_void_p
)


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
    except TypeError, ValueError:
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
    are windows into the three independent stacks owned by `ExecutionContext`;
    this object never owns a per-function stack.
    """

    __slots__ = (
        "_frames",
        "_locals",
        "_native",
        "_native_br_table_targets",
        "_native_control_map",
        "_native_function_view",
        "_native_slot",
        "code",
        "code_pc_offset",
        "context",
        "control_base",
        "control_map",
        "env",
        "frame_offset",
        "func_index",
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
        context: ExecutionContext,
        func_index: int,
        frame_offset: int,
        env: ExecEnv | None = None,
        *,
        template: CallFrame | None = None,
        native_function_view: FunctionExecutionViewNative | None = None,
        native_slot: int = -1,
    ):
        module = context.module
        assert module is not None
        assert not module.is_import(func_index)
        assert native_slot == -1 or 0 <= native_slot < len(context.native_call_frames)
        self._native_slot = -1
        native_frame = context.native_call_frames[native_slot] if native_slot >= 0 else None
        function = module.functions[func_index - len(module.imports)]
        local_widths = function.local_width_map_cache
        assert local_widths is not None
        self.context = context
        self.values = context.stack
        self.control_base = (
            int(native_frame.control_base)
            if native_frame is not None
            else len(context.control_frame_stack)
        )
        self._frames = _ControlFrameWindow(context.control_frame_stack, self.control_base)
        self.func_index = func_index
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
        self.code_pc_offset = context.module.function_pc_offset(func_index)
        assert function.control_map is not None
        self.control_map = function.control_map
        if template is None:
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
                _, next_pc = decode_signed(self.code, start + 1, bits=32)
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
                    0
                    if target_count == 0
                    else ctypes.addressof(native_br_table_targets)
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
            function_view = native_function_view
            if function_view is None:
                function_view = FunctionExecutionViewNative()
            function_view.code = _native_buffer_address(self.code)
            function_view.code_size = len(self.code)
            function_view.code_pc_offset = self.code_pc_offset
            function_view.control_map = (
                0 if len(self.code) == 0 else ctypes.addressof(native_control_map)
            )
            function_view.local_width_map = _native_buffer_address(self.local_widths.raw_view)
            function_view.local_width_count = self.local_count
            function_view.local_slot_count = self.local_slot_count
            function_view.slot_words = self.local_widths.slot_words
            function_view.param_count = self.param_count
            function_view.param_packed_slot_count = self.param_packed_slot_count
            function_view.result_arity = self.result_arity
            function_view.type_index = function.type_index
            function_view.is_import = 0
            function_view.module_view = 0
            self._native_function_view = function_view
        else:
            assert native_function_view is None
            assert template.func_index == func_index
            self._native_br_table_targets = template._native_br_table_targets
            self._native_control_map = template._native_control_map
            self._native_function_view = template._native_function_view
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
        execution = context.module_execution
        function_view_address = (
            execution.function_view_address(func_index)
            if execution is not None
            else ctypes.addressof(self._native_function_view)
        )
        if native_frame is None:
            self._native = CallFrameNative(
                func_index=self.func_index,
                code=self._native_function_view.code,
                code_size=len(self.code),
                function_view=function_view_address,
                local_base=self.frame_offset,
                local_count=self.local_count,
                local_slot_count=self.local_slot_count,
                slot_words=self.local_widths.slot_words,
                local_width_map=self._native_function_view.local_width_map,
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
            assert native_slot == -1
        else:
            assert native_slot >= 0
            assert native_frame.func_index == func_index
            assert native_frame.code == self._native_function_view.code
            assert native_frame.code_size == len(self.code)
            assert native_frame.function_view == function_view_address
            assert native_frame.local_base == frame_offset
            assert native_frame.local_count == self.local_count
            assert native_frame.local_slot_count == self.local_slot_count
            assert native_frame.slot_words == self.local_widths.slot_words
            assert native_frame.local_width_map == self._native_function_view.local_width_map
            assert native_frame.local_width_count == self.local_count
            assert native_frame.param_count == self.param_count
            assert native_frame.param_packed_slot_count == self.param_packed_slot_count
            assert native_frame.result_arity == self.result_arity
            assert native_frame.control_base == self.control_base
            self._native = None
            self._native_slot = native_slot

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        return self.context.context_ptr

    @property
    def native(self) -> CallFrameNative:
        """Return the flat native activation descriptor stored on the call stack."""
        if self._native_slot >= 0:
            return self.context.native_call_frames[self._native_slot]
        assert self._native is not None, "inactive call frame has no Native descriptor"
        return self._native

    @property
    def boundary_next_pc(self) -> int | None:
        value = int(self.native.boundary_next_pc)
        return None if value == 0xFFFF_FFFF else value

    @boundary_next_pc.setter
    def boundary_next_pc(self, value: int | None) -> None:
        self.native.boundary_next_pc = 0xFFFF_FFFF if value is None else value

    @property
    def boundary_loops_to(self) -> int | None:
        value = int(self.native.boundary_loops_to)
        return None if value == 0xFFFF_FFFF else value

    @boundary_loops_to.setter
    def boundary_loops_to(self, value: int | None) -> None:
        self.native.boundary_loops_to = 0xFFFF_FFFF if value is None else value

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
        assert self.native.func_index == self.func_index


class NativeModuleExecution:
    """Own immutable module descriptors and borrow stable table buffers for C++."""

    __slots__ = (
        "_allocator",
        "_arena_layout",
        "_function_types",
        "_function_views",
        "_function_views_address",
        "_global_widths",
        "_module_view",
        "_signature_address",
        "_signature_bytes",
        "_table_views",
        "_templates",
        "globals",
        "module",
        "tables",
    )

    def __init__(
        self,
        template_context: ExecutionContext,
        module: Module,
        env: ExecEnv,
        tables: Sequence[FunctionTable],
        allocator: BumpAllocator | None = None,
    ):
        self._allocator: BumpAllocator | None = None
        self._arena_layout: tuple[tuple[int, int], ...] = ()
        self.module = module
        self.tables = tables
        self.globals = env.globals
        function_count = len(module.imports) + len(module.functions)
        self._function_views = (FunctionExecutionViewNative * function_count)()
        self._function_views_address = (
            0 if function_count == 0 else ctypes.addressof(self._function_views)
        )
        for import_index in range(len(module.imports)):
            import_record = module.imports[import_index]
            function_type = module.func_type(import_index)
            assert function_type.params is not None and function_type.results is not None
            view = self._function_views[import_index]
            view.code = 0
            view.code_size = 0
            view.code_pc_offset = 0
            view.control_map = 0
            view.local_width_map = 0
            view.local_width_count = 0
            view.local_slot_count = 0
            view.slot_words = 1
            view.param_count = len(function_type.params)
            view.param_packed_slot_count = sum(
                value_slot_width(value_type) for value_type in function_type.params
            )
            view.result_arity = sum(
                value_slot_width(value_type) for value_type in function_type.results
            )
            view.type_index = import_record.type_index
            view.is_import = 1

        self._templates: StaticVector[CallFrame] = StaticVector(capacity=len(module.functions))
        for local_index in range(len(module.functions)):
            function_index = len(module.imports) + local_index
            function = module.functions[local_index]
            if function.control_map is None:
                function.control_map = build_control_map(module.code_for(function_index))
            self._templates.append(
                CallFrame(
                    template_context,
                    function_index,
                    0,
                    env,
                    native_function_view=self._function_views[function_index],
                )
            )

        type_count = len(module.types)
        type_array_type = FunctionTypeExecutionViewNative * type_count
        self._function_types = type_array_type()
        signature_size = 0
        source_backed_signatures = module.source is not None
        for type_index in range(type_count):
            function_type = module.type_at(type_index)
            assert function_type.params is not None and function_type.results is not None
            signature_size += len(function_type.params) + len(function_type.results)
            source_backed_signatures = source_backed_signatures and (
                function_type.params_source_offset is not None
                and function_type.results_source_offset is not None
            )
        if source_backed_signatures:
            assert module.source is not None
            self._signature_bytes = None
            self._signature_address = _native_buffer_address(module.source)
            for type_index in range(type_count):
                function_type = module.type_at(type_index)
                descriptor = self._function_types[type_index]
                assert function_type.params_source_offset is not None
                assert function_type.results_source_offset is not None
                assert function_type.params is not None and function_type.results is not None
                descriptor.param_offset = function_type.params_source_offset
                descriptor.param_count = len(function_type.params)
                descriptor.result_offset = function_type.results_source_offset
                descriptor.result_count = len(function_type.results)
        else:
            signature_array_type = ctypes.c_uint8 * signature_size
            signature_bytes = signature_array_type()
            self._signature_bytes = signature_bytes
            signature_offset = 0
            for type_index in range(type_count):
                function_type = module.type_at(type_index)
                assert function_type.params is not None and function_type.results is not None
                descriptor = self._function_types[type_index]
                descriptor.param_offset = signature_offset
                descriptor.param_count = len(function_type.params)
                for value_type in function_type.params:
                    signature_bytes[signature_offset] = value_type
                    signature_offset += 1
                descriptor.result_offset = signature_offset
                descriptor.result_count = len(function_type.results)
                for value_type in function_type.results:
                    signature_bytes[signature_offset] = value_type
                    signature_offset += 1
            assert signature_offset == signature_size
            self._signature_address = (
                0 if signature_size == 0 else ctypes.addressof(signature_bytes)
            )

        global_count = len(module.globals)
        self._global_widths = (ctypes.c_uint8 * global_count)()
        for global_index in range(global_count):
            self._global_widths[global_index] = value_slot_width(module.globals[global_index].vtype)

        self._table_views = (TableExecutionViewNative * len(tables))()
        for table_index, table in enumerate(tables):
            descriptor = self._table_views[table_index]
            descriptor.function_indices = table.native_address
            descriptor.size = len(table)
        self._module_view = ModuleExecutionViewNative(
            functions=self._function_views_address,
            function_count=function_count,
            imported_function_count=len(module.imports),
            types=(0 if type_count == 0 else ctypes.addressof(self._function_types)),
            type_count=type_count,
            signature_bytes=self._signature_address,
            tables=(0 if len(tables) == 0 else ctypes.addressof(self._table_views)),
            table_count=len(tables),
            globals=self.globals.native_address,
            global_widths=(0 if global_count == 0 else ctypes.addressof(self._global_widths)),
            global_count=global_count,
        )
        module_view_address = ctypes.addressof(self._module_view)
        for function_index in range(function_count):
            self._function_views[function_index].module_view = module_view_address
        signature_buffer_size = (
            0 if self._signature_bytes is None else ctypes.sizeof(self._signature_bytes)
        )
        self._arena_layout = (
            (
                ctypes.sizeof(self._function_views),
                ctypes.alignment(FunctionExecutionViewNative),
            ),
            (
                ctypes.sizeof(self._function_types),
                ctypes.alignment(FunctionTypeExecutionViewNative),
            ),
            (signature_buffer_size, ctypes.alignment(ctypes.c_uint8)),
            (ctypes.sizeof(self._global_widths), ctypes.alignment(ctypes.c_uint8)),
            (ctypes.sizeof(self._table_views), ctypes.alignment(TableExecutionViewNative)),
            (ctypes.sizeof(self._module_view), ctypes.alignment(ModuleExecutionViewNative)),
        )
        if allocator is not None:
            self.bind_allocator(allocator)

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Record all C-compatible module descriptors in the runtime arena."""

        if self._allocator is allocator:
            return
        for size, alignment in self._arena_layout:
            allocator.allocate(size, alignment)
        self._allocator = allocator

    def template(self, func_index: int) -> CallFrame:
        local_index = func_index - len(self.module.imports)
        assert 0 <= local_index < len(self._templates)
        return self._templates[local_index]

    def function_view_address(self, func_index: int) -> int:
        assert 0 <= func_index < len(self.module.imports) + len(self.module.functions)
        return self._function_views_address + func_index * ctypes.sizeof(
            FunctionExecutionViewNative
        )


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
_HandlerFn = Callable[[ExecutionContext, NativeValueStack, _LocalStackWindow, int], _HandlerResult]

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
    ctx: ExecutionContext, sp: NativeValueStack
) -> tuple[int, CallFrame, ExecEnv | None]:
    """Resolve the current instruction state from the shared execution context."""
    assert ctx.call_frame_stack
    frame = ctx.call_frame_stack[-1]
    assert sp is frame.values
    return int(ctx.ip), frame, frame.env


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
        context: ExecutionContext,
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
        Return this call's Code-section-relative instruction address.

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
        return frame.code_pc_offset + ip


class Interpreter:
    __slots__ = (
        "_env",
        "_native_dispatcher",
        "bump_allocator",
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
        logger: LoggerPort | None = None,
        *,
        bump_allocator: BumpAllocator | None = None,
    ):
        self._native_dispatcher = _native_abi.run_native_dispatch
        self.module = module
        self.bump_allocator = (
            bump_allocator if bump_allocator is not None else module.allocator
        ) or BumpAllocator()
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
        self.globals = NativeGlobalStorage(
            capacity=len(module.globals), allocator=self.bump_allocator
        )
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
        self.tables: StaticVector[FunctionTable] = StaticVector(capacity=len(module.tables))
        table_import_index = 0
        assert len(bindings.tables) == module.table_import_count
        for table_index in range(len(module.tables)):
            table = module.tables[table_index]
            if table.imported:
                external_table = bindings.tables[table_import_index]
                table_import_index += 1
                self.tables.append(
                    module.table_contents(
                        table_index,
                        self.globals,
                        external_table,
                        allocator=self.bump_allocator,
                    )
                )
            else:
                self.tables.append(
                    module.table_contents(table_index, self.globals, allocator=self.bump_allocator)
                )
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
            vdma_transfer=bindings.vdma_transfer,
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
        """Run the Python reference interpreter until the call completes."""
        while not call_state.finished:
            call_state = self._step(call_state, stop_at_boundary=False)
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def _abort_call(self, call_state: InterpreterCall, trap: Trap, ip: int) -> None:
        """Terminate every active frame and publish a runtime trap outcome.

        `ip` is passed explicitly so the trap event records the instruction that
        failed even when dispatch has already moved the resumable call state to a
        boundary or return sentinel.
        """
        if self.logger is not None:
            frame = call_state._frame
            assert frame is not None
            # GOTCHA-LOG-04: Code-section PC must be captured before frame teardown below.
            unified_pc = (
                RETURN_SENTINEL_PC if ip == RETURN_SENTINEL_IP else frame.code_pc_offset + ip
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
        call_state.context.release_workspace()

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
        context = self._new_context()
        if self.module.is_import(func_index):
            results, trap = self._call_import(func_index, args)
            call_state = InterpreterCall(
                func_index,
                context,
                cont=None,
                finished=True,
                results=results if trap is None else None,
                trap=trap,
            )
            context.release_workspace()
            return call_state

        self._prepare_native_execution(context)
        raw_args = _encode_public_args(args, self.module.func_type(func_index).params)
        try:
            frame, locals_arr = self._build_frame(func_index, raw_args, context)
        except Trap as trap:
            context.release_workspace()
            return InterpreterCall(func_index, context, cont=None, finished=True, trap=trap)
        return InterpreterCall(func_index, context, cont=(0, frame, locals_arr, 0))

    def _new_context(self) -> ExecutionContext:
        """Create the one context object used by Python and native execution."""
        return ExecutionContext(self.module, allocator=self.bump_allocator)

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Bind interpreter-owned native buffers to the runtime resource arena."""

        if self.bump_allocator is allocator:
            return
        self.bump_allocator = allocator
        self.module.bind_allocator(allocator)
        self.globals.bind_allocator(allocator)
        for table in self.tables:
            table.bind_allocator(allocator)

    def _prepare_native_execution(self, context: ExecutionContext) -> None:
        """Allow the strict native interpreter to install call descriptors."""
        return None

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
        self, func_index: int, raw_args: StaticVector[int], context: ExecutionContext
    ) -> tuple[CallFrame, _LocalStackWindow]:
        """
        Builds the initial frame + locals for a WASM (non-import) function
        activation. `raw_args` contains the packed parameter values as
        32-bit slots; the load-time local-width metadata places them into the
        aligned local layout exactly once at frame creation.
        The internal call path obtains it directly from the operand stack;
        the public entry path encodes host values once at that boundary.

        The Native local slots are pushed into `context` by
        `ExecutionContext.begin_call_frame`; the operand stack is also owned
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
        return self._step(call_state, stop_at_boundary=True)

    def step_native(self, call_state: InterpreterCall) -> InterpreterCall:
        """Advance through C++ dispatch and explicit runtime call boundaries only."""
        assert self.debugger is None, "native stepping does not support Python debugger hooks"
        if call_state.finished:
            return call_state
        if call_state._ip == RETURN_SENTINEL_IP:
            return self._finish_native_frame(call_state)
        if self._try_native_step_to_boundary(call_state):
            return call_state
        if call_state.finished:
            return call_state
        if call_state._ip == RETURN_SENTINEL_IP:
            return self._finish_native_frame(call_state)

        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        native_status = 0
        while native_status == 0 and not call_state.finished:
            (
                native_status,
                _trace_count,
                _body_count,
                _dispatcher_trace_transitions,
                _control_handler_count,
                _eligible_block_visits,
                _interpreted_block_count,
                _visits,
            ) = self.run_native_dispatch(
                call_state,
                EMPTY_NATIVE_DISPATCH_SNAPSHOT,
                FB_CONF_RUNTIME_YIELD_THRESHOLD,
                0,
                native_dispatcher=self._native_dispatcher,
            )
            if native_status == 0:
                self.resolve_native_call_boundary(call_state)
                if call_state.finished:
                    native_status = 2

        if native_status == 1:
            call_state._ip = RETURN_SENTINEL_IP
            self._finish_native_frame(call_state)
        elif native_status == 2:
            assert call_state.finished and call_state.trap is not None
        elif native_status == NATIVE_DISPATCH_YIELD:
            call_state.context.loop_jump_count = 0
        elif native_status == NATIVE_DISPATCH_DEBUG_STOP:
            # A statically composed debugger has stopped native execution.
            # This is a runtime boundary, not a different instruction driver.
            return call_state
        else:
            assert False, f"unexpected C++ interpreter status: {native_status}"
        return call_state

    def resolve_native_call_boundary(self, call_state: InterpreterCall) -> InterpreterCall:
        """Resolve a host import or a memory-manager boundary after C++ stops."""
        frame = call_state._frame
        locals_arr = call_state._locals
        ip = call_state._ip
        assert frame is not None and locals_arr is not None
        assert 0 <= ip < len(frame.code)
        opcode = frame.code[ip]
        if opcode == CALL:
            callee_func_index, _ = decode_unsigned(frame.code, ip + 1)
            assert self.module.is_import(callee_func_index), (
                "C++ dispatcher returned a defined guest call instead of executing it"
            )
            trap = self._enter_or_resolve_call(
                call_state, opcode, ip, frame, locals_arr, call_state._tos
            )
            if trap is not None:
                self._abort_call(call_state, trap, ip)
            return call_state
        if opcode == CALL_INDIRECT:
            _, off = decode_unsigned(frame.code, ip + 1)
            table_index, _ = decode_unsigned(frame.code, off)
            table_slot = _to_u32(frame.values[-1])
            assert table_index < len(self.tables)
            table = self.tables[table_index]
            assert table_slot < len(table)
            callee_index = table[table_slot]
            assert callee_index is not None
            callee_func_index = callee_index
            assert self.module.is_import(callee_func_index), (
                "C++ dispatcher returned a defined guest call instead of executing it"
            )
            trap = self._enter_or_resolve_call(
                call_state, opcode, ip, frame, locals_arr, call_state._tos
            )
            if trap is not None:
                self._abort_call(call_state, trap, ip)
            return call_state
        if I32_LOAD <= opcode <= MEMORY_GROW:
            return self._resolve_native_memory_boundary(call_state, opcode, ip)
        if opcode == FC_PREFIX:
            subopcode, _ = decode_unsigned(frame.code, ip + 1)
            assert subopcode == FC_MEMORY_COPY or subopcode == FC_MEMORY_FILL, (
                f"C++ interpreter does not implement 0xFC subopcode {subopcode}"
            )
            return self._resolve_native_memory_boundary(call_state, opcode, ip)
        assert False, f"C++ interpreter does not implement opcode 0x{opcode:02X}"

    def _resolve_native_memory_boundary(
        self, call_state: InterpreterCall, opcode: int, ip: int
    ) -> InterpreterCall:
        """Run only the memory-manager operation C++ explicitly yielded."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        handler = _HANDLERS[opcode]
        assert handler is not None
        context = call_state.context
        context.bind_handler_state(ip, frame)
        trap = handler(context, frame.values, locals_arr, call_state._tos)
        if trap is not None:
            self._abort_call(call_state, trap, ip)
            return call_state
        next_ip = int(context.ip)
        call_state._ip = RETURN_SENTINEL_IP if next_ip >= len(frame.code) else next_ip
        call_state._tos = frame.values.raw_top() if frame.values else 0
        return call_state

    def _finish_native_frame(self, call_state: InterpreterCall) -> InterpreterCall:
        """Complete one C++ return boundary and restore its suspended caller."""
        frame = call_state._frame
        assert frame is not None and call_state._ip == RETURN_SENTINEL_IP
        frame.frames.truncate(0)
        call_state.context.end_call_frame(frame)
        if not call_state.call_stack:
            func_type = self.module.func_type(call_state.func_index)
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
                assert result_value is not None
                results.append(result_value)
            call_state.cont = None
            call_state.finished = True
            call_state.results = results
            call_state.context.release_workspace()
            return call_state

        parent_func_index, parent_cont = call_state.call_stack.pop_back()
        parent_ip, parent_frame, parent_locals, _ = parent_cont
        call_state.func_index = parent_func_index
        call_state._ip = parent_ip
        call_state._frame = parent_frame
        call_state._locals = parent_locals
        call_state._tos = parent_frame.values.raw_top() if parent_frame.values else 0
        return call_state

    def step_native_control(self, call_state: InterpreterCall) -> InterpreterCall:
        """Execute one supported structured-control opcode through its C++ handler."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        assert call_state._ip != RETURN_SENTINEL_IP
        assert self.debugger is None, (
            "native control stepping does not support Python debugger hooks"
        )

        context = call_state.context
        execution = context.module_execution
        assert execution is not None
        native_frame = frame.native
        with _native_linear_memory_scope(context, frame.env.memory):
            native_status, native_ip, native_size, native_trap = (
                _native_abi.run_control_step_addresses(
                    int(native_frame.code or 0),
                    int(native_frame.code_size),
                    int(context.context_ptr.value or 0),
                    context.context_view.nbytes,
                    frame.values.address,
                    frame.values.native_bytes,
                    locals_arr._storage.address,
                    locals_arr._storage.native_bytes,
                    context.control_frame_stack.address,
                    context.control_frame_stack.native_bytes,
                    len(frame.values),
                    frame.values.capacity,
                    call_state._ip,
                    frame.local_slot_count,
                    frame.control_base,
                    call=context._native_step_call,
                    result=context._native_result,
                )
            )

        frame.values.set_size(native_size)
        context.ip = native_ip
        context.call_frame_stack.sync_from_native(context, self._env)
        if context.call_frame_stack:
            active = context.call_frame_stack[-1]
            call_state.func_index = active.func_index
            call_state._frame = active
            call_state._locals = active.locals
            if native_status == 3 and native_ip >= len(active.code):
                native_ip = RETURN_SENTINEL_IP
            call_state._ip = native_ip
            call_state._tos = active.values.raw_top() if active.values else 0

        if native_status == 3:
            return call_state
        if native_status == 1:
            call_state._ip = RETURN_SENTINEL_IP
            return self._finish_native_frame(call_state)
        if native_status == 2:
            self._abort_call(call_state, Trap(TrapCode(native_trap)), native_ip)
            return call_state
        assert False, f"C++ control handler does not implement opcode at 0x{native_ip:04X}"

    def run_native_dispatch(
        self,
        call_state: InterpreterCall,
        snapshot: NativeDispatchSnapshot,
        yield_threshold: int,
        execution_count: int,
        native_dispatcher: NativeDispatchEntryPoint,
    ) -> NativeDispatchMetrics:
        """Run native traces and C++ handlers over Python-owned ctypes buffers."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        assert call_state._ip != RETURN_SENTINEL_IP
        assert yield_threshold > 0
        context = call_state.context
        execution = context.module_execution
        assert execution is not None
        native_frame = frame.native
        with _native_linear_memory_scope(context, frame.env.memory):
            native_entry = _native_abi.native_dispatch_entrypoint(native_dispatcher)
            if native_entry is not None:
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
                ) = _native_abi.run_native_dispatch_from_addresses(
                    native_entry,
                    int(native_frame.code or 0),
                    int(native_frame.code_size),
                    int(context.context_ptr.value or 0),
                    context.context_view.nbytes,
                    frame.values.address,
                    frame.values.native_bytes,
                    locals_arr._storage.address,
                    locals_arr._storage.native_bytes,
                    context.control_frame_stack.address,
                    context.control_frame_stack.native_bytes,
                    snapshot.entries,
                    snapshot.trackable_blocks,
                    snapshot.block_history,
                    snapshot.entry_count,
                    snapshot.trackable_count,
                    len(frame.values),
                    frame.values.capacity,
                    call_state._ip,
                    frame.frame_offset,
                    frame.local_slot_count,
                    frame.control_base,
                    call_state.func_index,
                    yield_threshold,
                    execution_count,
                    call=context._native_dispatch_call,
                    result=context._native_result,
                )
            else:
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
                ) = native_dispatcher(
                    frame.code,
                    context.context_view,
                    frame.values.raw_view,
                    locals_arr._storage.raw_view,
                    context.control_frame_stack.raw_view,
                    snapshot.entries,
                    snapshot.trackable_blocks,
                    snapshot.block_history,
                    snapshot.entry_count,
                    snapshot.trackable_count,
                    len(frame.values),
                    frame.values.capacity,
                    call_state._ip,
                    frame.frame_offset,
                    frame.local_slot_count,
                    frame.control_base,
                    call_state.func_index,
                    yield_threshold,
                    execution_count,
                )
        frame.values.set_size(native_size)
        context.ip = native_ip
        context.call_frame_stack.sync_from_native(context, self._env)
        if context.call_frame_stack:
            active = context.call_frame_stack[-1]
            call_state.func_index = active.func_index
            call_state._frame = active
            call_state._locals = active.locals
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
        native_frame = frame.native
        previous_flags = int(context.runtime_flags)
        context.runtime_flags = previous_flags | EXECUTION_CONTEXT_FLAG_STOP_AT_BLOCK_BOUNDARY
        try:
            with _native_linear_memory_scope(context, frame.env.memory):
                native_status, native_ip, native_size, native_trap = _native_abi.run_step_addresses(
                    int(native_frame.code or 0),
                    int(native_frame.code_size),
                    int(context.context_ptr.value or 0),
                    context.context_view.nbytes,
                    frame.values.address,
                    frame.values.native_bytes,
                    locals_arr._storage.address,
                    locals_arr._storage.native_bytes,
                    context.control_frame_stack.address,
                    context.control_frame_stack.native_bytes,
                    len(frame.values),
                    frame.values.capacity,
                    call_state._ip,
                    frame.local_slot_count,
                    frame.control_base,
                    call=context._native_step_call,
                    result=context._native_result,
                )
        finally:
            context.runtime_flags = previous_flags

        frame.values.set_size(native_size)
        context.ip = native_ip
        context.call_frame_stack.sync_from_native(context, self._env)
        if context.call_frame_stack:
            active = context.call_frame_stack[-1]
            call_state.func_index = active.func_index
            call_state._frame = active
            call_state._locals = active.locals
            if native_status == 3 and native_ip >= len(active.code):
                native_ip = RETURN_SENTINEL_IP
            call_state._ip = native_ip
            call_state._tos = active.values.raw_top() if active.values else 0

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
                next_ip = int(ctx.ip)
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
                call_state.context.release_workspace()
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


class NativeInterpreter(Interpreter):
    """Strict C++ interpreter; defined guest calls remain in native dispatch."""

    __slots__ = ("_module_execution",)

    def __init__(
        self,
        module: Module,
        bindings: InterpreterBindings,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: LoggerPort | None = None,
        *,
        bump_allocator: BumpAllocator | None = None,
    ):
        self._module_execution: NativeModuleExecution | None = None
        super().__init__(module, bindings, vmmio, phys_mem, logger, bump_allocator=bump_allocator)

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        previous_allocator = self.bump_allocator
        super().bind_allocator(allocator)
        if self._module_execution is not None and previous_allocator is not allocator:
            self._module_execution.bind_allocator(allocator)

    def _prepare_native_execution(self, context: ExecutionContext) -> None:
        if self._module_execution is None:
            self._module_execution = NativeModuleExecution(
                context,
                self.module,
                self._env,
                self.tables,
                self.bump_allocator,
            )
        context.attach_module_execution(self._module_execution)

    def _complete_call(self, call_state: InterpreterCall) -> StaticVector[WasmNumber]:
        while not call_state.finished:
            self.step_native(call_state)
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def step(self, call_state: InterpreterCall) -> InterpreterCall:
        return self.step_native(call_state)


# Per-opcode logical CPS handlers. Each receives the native CPS arguments and
# returns the exact arguments for the next handler, plus an optional trap.
# ---------------------------------------------------------------------------


@_handler(UNREACHABLE)
def _h_unreachable(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    return Trap(TrapCode.UNREACHABLE)


@_handler(NOP)
def _h_nop(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    ctx.ip = ip + 1
    return None


@_handler(BLOCK)
def _h_block(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end, _, result_arity = frame.control_map.block(ip)
    if not frame.frames.push_back(
        ControlFrameKind.BLOCK, ip, match_end, len(frame.values), result_arity
    ):
        return Trap(TrapCode.CONTROL_FRAME_CAPACITY)
    ctx.ip = ip + 2
    return None


@_handler(LOOP)
def _h_loop(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end, _, result_arity = frame.control_map.block(ip)
    if not frame.frames.push_back(
        ControlFrameKind.LOOP, ip, match_end, len(frame.values), result_arity
    ):
        return Trap(TrapCode.CONTROL_FRAME_CAPACITY)
    ctx.ip = ip + 2
    return None


@_handler(IF)
def _h_if(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
            ctx.ip = else_off + 1
            return None
        else:
            ctx.ip = match_end + 1
            return None
    if not frame.frames.push_back(
        ControlFrameKind.IF, ip, match_end, len(frame.values), result_arity
    ):
        return Trap(TrapCode.CONTROL_FRAME_CAPACITY)
    ctx.ip = ip + 2
    return None


@_handler(ELSE)
def _h_else(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    popped = frame.frames.pop_back() if frame.frames else None
    if frame.boundary_next_pc is not None:
        ctx.ip = frame.boundary_next_pc - frame.code_pc_offset
        return None
    if popped is not None:
        ctx.ip = popped.match_end + 1
        return None
    ctx.ip = ip + 1
    return None


@_handler(END)
def _h_end(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    if frame.frames:
        frame.frames.pop_back()
    ctx.ip = ip + 1
    return None


@_handler(BR)
def _h_br(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    depth, _ = decode_unsigned(frame.code, ip + 1)
    next_ip = _do_branch(depth, frame)
    if (next_ip) is None:
        ctx.ip = RETURN_SENTINEL_IP
        return None
    if frame.boundary_next_pc is not None:
        next_ip = frame.boundary_next_pc - frame.code_pc_offset
    ctx.ip = next_ip
    return None


@_handler(BR_IF)
def _h_br_if(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    depth, next_ip = decode_unsigned(frame.code, ip + 1)
    cond = frame.values.pop_back()
    if cond == 0:
        ctx.ip = next_ip
        return None
    target_ip = _do_branch(depth, frame)
    if target_ip is None:
        ctx.ip = RETURN_SENTINEL_IP
        return None
    if frame.boundary_loops_to is not None:
        ctx.ip = frame.boundary_loops_to - frame.code_pc_offset
        return None
    ctx.ip = target_ip
    return None


@_handler(BR_TABLE)
def _h_br_table(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    labels, default_lbl = frame.control_map.br_table(ip)
    index = _to_u32(frame.values.pop_back())
    depth = labels[index] if index < len(labels) else default_lbl
    next_ip = _do_branch(depth, frame)
    if (next_ip) is None:
        ctx.ip = RETURN_SENTINEL_IP
        return None
    ctx.ip = next_ip
    return None


@_handler(RETURN)
def _h_return(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    ctx.ip = RETURN_SENTINEL_IP
    return None


# CALL and CALL_INDIRECT are not in `_HANDLERS`. Python `Interpreter.step()`
# handles them before its table lookup. `NativeInterpreter.step_native()` and
# `RuntimeEngine` resolve the C++ dispatch boundary through the same call
# resolver; no guest opcode is sent to the Python handler table.


@_handler(DROP)
def _h_drop(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    module = frame.context.module
    assert module is not None
    function = module.functions[frame.func_index - len(module.imports)]
    width = function.drop_widths.view().find(ip) if function.drop_widths is not None else None
    frame.values.pop_back()
    if width == 2:
        frame.values.pop_back()
    ctx.ip = ip + 1
    return None


@_handler(SELECT)
def _h_select(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(LOCAL_GET)
def _h_local_get(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    raw_slot, raw_width = local_base.raw_span(idx)
    assert frame.values.push_raw_from(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.ip = next_ip
    return None


@_handler(LOCAL_SET)
def _h_local_set(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    raw_slot, raw_width = local_base.raw_span(idx)
    frame.values.pop_raw_to(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.ip = next_ip
    return None


@_handler(LOCAL_TEE)
def _h_local_tee(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    raw_slot, raw_width = local_base.raw_span(idx)
    frame.values.copy_raw_to(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.ip = next_ip
    return None


@_handler(I32_CONST)
def _h_i32_const(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1, bits=32)
    frame.values.push_back(_to_i32(val))
    ctx.ip = next_ip
    return None


@_handler(I64_CONST)
def _h_i64_const(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1, bits=64)
    assert frame.values.push_i64(_to_i64(val))
    ctx.ip = next_ip
    return None


@_handler(F32_CONST)
def _h_f32_const(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<f", frame.code[ip + 1 : ip + 5])[0]
    assert frame.values.push_f32(val)
    ctx.ip = ip + 5
    return None


@_handler(F64_CONST)
def _h_f64_const(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<d", frame.code[ip + 1 : ip + 9])[0]
    assert frame.values.push_f64(val)
    ctx.ip = ip + 9
    return None


@_handler(GLOBAL_GET)
def _h_global_get(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


@_handler(GLOBAL_SET)
def _h_global_set(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


# --- Loads (Dedicated per-opcode handlers with Bit 31 RAM Bypass) ---


def _vmmio_load(env: ExecEnv, addr: int, width: int, signed: bool) -> tuple[int, Trap | None]:
    if env.vmmio is None:
        return 0, Trap(TrapCode.VMMIO_NOT_CONFIGURED, addr)
    status, phys_addr = env.vmmio.access(addr, is_write=False, value=0, access_size=width)
    if status > VmmioStatus.OK_PHYSICAL:
        return 0, Trap(TrapCode.VMMIO_ACCESS, int(status))
    if status == VmmioStatus.OK_STATIC_DEVICE:
        return phys_addr, None
    if status == VmmioStatus.OK_PHYSICAL:
        address = VmmioAddress(addr)
        if address.fc() == FC_DYNAMIC or address.fc() == FC_SHM:
            pte = env.vmmio.ptes.view().find(address.vpn())
            assert pte is not None
            if address.fc() == FC_DYNAMIC:
                assert pte.mapped_storage is not None
            if pte.mapped_storage is not None:
                return int.from_bytes(
                    pte.mapped_storage[address.offset() : address.offset() + width],
                    "little",
                    signed=signed,
                ), None
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
        access_size=len(val_bytes),
    )
    if status > VmmioStatus.OK_PHYSICAL:
        return Trap(TrapCode.VMMIO_ACCESS, int(status))
    if status == VmmioStatus.OK_PHYSICAL:
        address = VmmioAddress(addr)
        if address.fc() == FC_DYNAMIC or address.fc() == FC_SHM:
            pte = env.vmmio.ptes.view().find(address.vpn())
            assert pte is not None
            if address.fc() == FC_DYNAMIC:
                assert pte.mapped_storage is not None
            if pte.mapped_storage is not None:
                pte.mapped_storage[address.offset() : address.offset() + len(val_bytes)] = val_bytes
                return None
        assert env.phys_mem is not None
        assert phys_addr + len(val_bytes) <= len(env.phys_mem)
        env.phys_mem[phys_addr : phys_addr + len(val_bytes)] = val_bytes
    return None


@_handler(I32_LOAD)
def _h_i32_load(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


@_handler(I32_LOAD8_S)
def _h_i32_load8_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


@_handler(I32_LOAD8_U)
def _h_i32_load8_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


@_handler(I32_LOAD16_S)
def _h_i32_load16_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


@_handler(I32_LOAD16_U)
def _h_i32_load16_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


# --- Stores (Dedicated per-opcode handlers with Bit 31 RAM Bypass) ---


@_handler(I32_STORE)
def _h_i32_store(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


@_handler(I32_STORE8)
def _h_i32_store8(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


@_handler(I32_STORE16)
def _h_i32_store16(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


# --- Memory Size / Grow ---


@_handler(MEMORY_SIZE)
def _h_memory_size(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    if env.memory is None:
        return Trap(TrapCode.MEMORY_SECTION_MISSING)
    frame.values.push_back(len(env.memory) // PAGE_SIZE)
    ctx.ip = ip + 2
    return None


@_handler(MEMORY_GROW)
def _h_memory_grow(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 2
    return None


# --- Comparisons (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_EQZ)
def _h_i32_eqz(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    frame.values.push_back(1 if frame.values.pop_back() == 0 else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_EQ)
def _h_i32_eq(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) == _to_i32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_NE)
def _h_i32_ne(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) != _to_i32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_LT_S)
def _h_i32_lt_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) < _to_i32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_LT_U)
def _h_i32_lt_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) < _to_u32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_GT_S)
def _h_i32_gt_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) > _to_i32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_GT_U)
def _h_i32_gt_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) > _to_u32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_LE_S)
def _h_i32_le_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) <= _to_i32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_LE_U)
def _h_i32_le_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) <= _to_u32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_GE_S)
def _h_i32_ge_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) >= _to_i32(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I32_GE_U)
def _h_i32_ge_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) >= _to_u32(b) else 0)
    ctx.ip = ip + 1
    return None


# --- Unary Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_CLZ)
def _h_i32_clz(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(32 if v == 0 else 32 - v.bit_length())
    ctx.ip = ip + 1
    return None


@_handler(I32_CTZ)
def _h_i32_ctz(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(I32_POPCNT)
def _h_i32_popcnt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(bin(v).count("1"))
    ctx.ip = ip + 1
    return None


# --- Binary Arithmetic & Bitwise Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_ADD)
def _h_i32_add(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a + b))
    ctx.ip = ip + 1
    return None


@_handler(I32_SUB)
def _h_i32_sub(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a - b))
    ctx.ip = ip + 1
    return None


@_handler(I32_MUL)
def _h_i32_mul(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a * b))
    ctx.ip = ip + 1
    return None


@_handler(I32_DIV_S)
def _h_i32_div_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(I32_DIV_U)
def _h_i32_div_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    frame.values.push_back(_to_i32(a // b))
    ctx.ip = ip + 1
    return None


@_handler(I32_REM_S)
def _h_i32_rem_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_i32(frame.values.pop_back())
    a = _to_i32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    r = abs(a) % abs(b)
    frame.values.push_back(_to_i32(-r if a < 0 else r))
    ctx.ip = ip + 1
    return None


@_handler(I32_REM_U)
def _h_i32_rem_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    frame.values.push_back(_to_i32(a % b))
    ctx.ip = ip + 1
    return None


@_handler(I32_AND)
def _h_i32_and(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) & _to_u32(b)))
    ctx.ip = ip + 1
    return None


@_handler(I32_OR)
def _h_i32_or(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) | _to_u32(b)))
    ctx.ip = ip + 1
    return None


@_handler(I32_XOR)
def _h_i32_xor(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) ^ _to_u32(b)))
    ctx.ip = ip + 1
    return None


@_handler(I32_SHL)
def _h_i32_shl(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) << (_to_u32(b) & 31)))
    ctx.ip = ip + 1
    return None


@_handler(I32_SHR_S)
def _h_i32_shr_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_i32(a) >> (_to_u32(b) & 31)))
    ctx.ip = ip + 1
    return None


@_handler(I32_SHR_U)
def _h_i32_shr_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) >> (_to_u32(b) & 31)))
    ctx.ip = ip + 1
    return None


@_handler(I32_ROTL)
def _h_i32_rotl(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.push_back(_to_i32(((v << n) | (v >> (32 - n))) & I32_MASK if n else v))
    ctx.ip = ip + 1
    return None


@_handler(I32_ROTR)
def _h_i32_rotr(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.push_back(_to_i32(((v >> n) | (v << (32 - n))) & I32_MASK if n else v))
    ctx.ip = ip + 1
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
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


def _h_i64_load_narrow(
    ctx: ExecutionContext,
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
    ctx.ip = next_ip
    return None


@_handler(I64_LOAD8_S)
def _h_i64_load8_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 1, True)


@_handler(I64_LOAD8_U)
def _h_i64_load8_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 1, False)


@_handler(I64_LOAD16_S)
def _h_i64_load16_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 2, True)


@_handler(I64_LOAD16_U)
def _h_i64_load16_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 2, False)


@_handler(I64_LOAD32_S)
def _h_i64_load32_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 4, True)


@_handler(I64_LOAD32_U)
def _h_i64_load32_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, sp, local_base, tos, 4, False)


@_handler(I64_STORE)
def _h_i64_store(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = next_ip
    return None


def _h_i64_store_narrow(
    ctx: ExecutionContext,
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
    ctx.ip = next_ip
    return None


@_handler(I64_STORE8)
def _h_i64_store8(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, sp, local_base, tos, 1)


@_handler(I64_STORE16)
def _h_i64_store16(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, sp, local_base, tos, 2)


@_handler(I64_STORE32)
def _h_i64_store32(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, sp, local_base, tos, 4)


@_handler(F32_LOAD)
def _h_f32_load(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    val = struct.unpack("<f", env.memory[addr : addr + 4])[0]
    assert frame.values.push_f32(val)
    ctx.ip = next_ip
    return None


@_handler(F32_STORE)
def _h_f32_store(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    val = frame.values.pop_f32()
    assert val is not None
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    env.memory[addr : addr + 4] = struct.pack("<f", val)
    ctx.ip = next_ip
    return None


@_handler(F64_LOAD)
def _h_f64_load(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    val = struct.unpack("<d", env.memory[addr : addr + 8])[0]
    assert frame.values.push_f64(val)
    ctx.ip = next_ip
    return None


@_handler(F64_STORE)
def _h_f64_store(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    val = frame.values.pop_f64()
    assert val is not None
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    env.memory[addr : addr + 8] = struct.pack("<d", val)
    ctx.ip = next_ip
    return None


# --- Const Handlers ---


@_handler(I64_CONST)
def _h_i64_const(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1, bits=64)
    assert frame.values.push_i64(_to_i64(val))
    ctx.ip = next_ip
    return None


@_handler(F32_CONST)
def _h_f32_const(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<f", frame.code[ip + 1 : ip + 5])[0]
    assert frame.values.push_f32(val)
    ctx.ip = ip + 5
    return None


@_handler(F64_CONST)
def _h_f64_const(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<d", frame.code[ip + 1 : ip + 9])[0]
    assert frame.values.push_f64(val)
    ctx.ip = ip + 9
    return None


# --- i64 Comparison & Arithmetic Handlers ---


@_handler(I64_EQZ)
def _h_i64_eqz(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = frame.values.pop_i64()
    assert v is not None
    frame.values.push_back(1 if v == 0 else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_EQ)
def _h_i64_eq(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) == _to_i64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_NE)
def _h_i64_ne(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) != _to_i64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_LT_S)
def _h_i64_lt_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) < _to_i64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_LT_U)
def _h_i64_lt_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) < _to_u64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_ADD)
def _h_i64_add(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a + b))
    ctx.ip = ip + 1
    return None


@_handler(I64_SUB)
def _h_i64_sub(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a - b))
    ctx.ip = ip + 1
    return None


@_handler(I64_MUL)
def _h_i64_mul(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a * b))
    ctx.ip = ip + 1
    return None


@_handler(I64_DIV_S)
def _h_i64_div_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(I64_DIV_U)
def _h_i64_div_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


# --- f32 Arithmetic Handlers ---


@_handler(F32_ADD)
def _h_f32_add(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a + b))
    ctx.ip = ip + 1
    return None


@_handler(F32_SUB)
def _h_f32_sub(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a - b))
    ctx.ip = ip + 1
    return None


@_handler(F32_MUL)
def _h_f32_mul(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a * b))
    ctx.ip = ip + 1
    return None


@_handler(F32_DIV)
def _h_f32_div(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_wasm_float_div(a, b)))
    ctx.ip = ip + 1
    return None


@_handler(F32_SQRT)
def _h_f32_sqrt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(math.sqrt(a) if a >= 0 else float("nan")))
    ctx.ip = ip + 1
    return None


@_handler(F32_MIN)
def _h_f32_min(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_f32_min(a, b)))
    ctx.ip = ip + 1
    return None


@_handler(F32_MAX)
def _h_f32_max(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_f32_max(a, b)))
    ctx.ip = ip + 1
    return None


@_handler(F32_LT)
def _h_f32_lt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F32_LE)
def _h_f32_le(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F32_GT)
def _h_f32_gt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F32_GE)
def _h_f32_ge(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.ip = ip + 1
    return None


# --- f64 Arithmetic Handlers ---


@_handler(F64_ADD)
def _h_f64_add(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a + b))
    ctx.ip = ip + 1
    return None


@_handler(F64_SUB)
def _h_f64_sub(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a - b))
    ctx.ip = ip + 1
    return None


@_handler(F64_MUL)
def _h_f64_mul(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a * b))
    ctx.ip = ip + 1
    return None


@_handler(F64_DIV)
def _h_f64_div(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(_wasm_float_div(a, b))
    ctx.ip = ip + 1
    return None


@_handler(F64_SQRT)
def _h_f64_sqrt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(math.sqrt(a) if a >= 0 else float("nan")))
    ctx.ip = ip + 1
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


def _truncate_saturating_float(value: float, bits: int, signed: bool) -> int:
    """Convert with WASM trunc_sat semantics, including NaN and infinities."""
    if math.isnan(value):
        return 0
    if signed:
        lower = -(1 << (bits - 1))
        upper = 1 << (bits - 1)
        if value <= lower:
            return lower
        if value >= upper:
            return upper - 1
    else:
        upper = 1 << bits
        if value <= 0.0:
            return 0
        if value >= upper:
            return upper - 1
    return math.trunc(value)


def _memory_range_is_valid(memory_size: int, offset: int, length: int) -> bool:
    """Check one wasm32 memory range without overflowing offset + length."""
    return offset <= memory_size and length <= memory_size - offset


def _linear_memory_address(address: int) -> bool:
    """Return whether the guest address belongs to the linear RAM window."""
    return (address & 0x8000_0000) == 0


def _guest_address_range_is_valid(address: int, length: int) -> bool:
    """Check that a guest virtual-address range does not wrap the wasm32 space."""
    return length <= (1 << 32) - address


def _vdma_address_is_supported(address: int) -> bool:
    """Accept guest RAM or one of the three vMMIO memory mapping classes."""
    if _linear_memory_address(address):
        return True
    function_code = address >> 28
    return function_code == FC_DYNAMIC or function_code == FC_SHM or function_code == FC_PASSTHROUGH


@_handler(FC_PREFIX)
def _h_fc_prefix(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    subopcode, next_ip = decode_unsigned(frame.code, ip + 1)

    if FC_I32_TRUNC_SAT_F32_S <= subopcode <= FC_I32_TRUNC_SAT_F64_U:
        source_is_f32 = subopcode == 0 or subopcode == 1
        value = frame.values.pop_f32() if source_is_f32 else frame.values.pop_f64()
        converted = _truncate_saturating_float(value, 32, subopcode % 2 == 0)
        assert frame.values.push_i32(converted)
        ctx.ip = next_ip
        return None

    if FC_I64_TRUNC_SAT_F32_S <= subopcode <= FC_I64_TRUNC_SAT_F64_U:
        source_is_f32 = subopcode == 4 or subopcode == 5
        value = frame.values.pop_f32() if source_is_f32 else frame.values.pop_f64()
        converted = _truncate_saturating_float(value, 64, subopcode % 2 == 0)
        assert frame.values.push_i64(converted)
        ctx.ip = next_ip
        return None

    assert env is not None
    memory = env.memory
    if memory is None:
        return Trap(TrapCode.MEMORY_SECTION_MISSING)

    if subopcode == FC_MEMORY_COPY:
        destination_memory, next_ip = decode_unsigned(frame.code, next_ip)
        source_memory, next_ip = decode_unsigned(frame.code, next_ip)
        assert destination_memory == 0 and source_memory == 0
        stack_size = len(frame.values)
        assert stack_size >= 3, "memory.copy requires three i32 operands"
        destination = frame.values.raw_at(stack_size - 3)
        source = frame.values.raw_at(stack_size - 2)
        length = frame.values.raw_at(stack_size - 1)
        memory_size = len(memory)
        destination_is_linear = _linear_memory_address(destination)
        source_is_linear = _linear_memory_address(source)
        if destination_is_linear and not _memory_range_is_valid(memory_size, destination, length):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, destination)
        if source_is_linear and not _memory_range_is_valid(memory_size, source, length):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, source)
        if not destination_is_linear and (
            not _vdma_address_is_supported(destination)
            or not _guest_address_range_is_valid(destination, length)
        ):
            return Trap(TrapCode.VMMIO_ACCESS, destination)
        if not source_is_linear and (
            not _vdma_address_is_supported(source)
            or not _guest_address_range_is_valid(source, length)
        ):
            return Trap(TrapCode.VMMIO_ACCESS, source)

        if not destination_is_linear or not source_is_linear:
            if env.vdma_transfer is None:
                return Trap(TrapCode.VMMIO_NOT_CONFIGURED)
            transfer_status = env.vdma_transfer(source, destination, length)
            if transfer_status != 0:
                return Trap(TrapCode.VMMIO_ACCESS, transfer_status)
        else:
            overlaps = (destination <= source and source - destination < length) or (
                source < destination and destination - source < length
            )
            if destination > source and overlaps:
                index = length
                while index > 0:
                    index -= 1
                    memory[destination + index] = memory[source + index]
            else:
                for index in range(length):
                    memory[destination + index] = memory[source + index]
        frame.values.truncate(stack_size - 3)
        ctx.ip = next_ip
        return None

    assert subopcode == FC_MEMORY_FILL
    memory_index, next_ip = decode_unsigned(frame.code, next_ip)
    assert memory_index == 0
    stack_size = len(frame.values)
    assert stack_size >= 3, "memory.fill requires three i32 operands"
    destination = frame.values.raw_at(stack_size - 3)
    value = frame.values.raw_at(stack_size - 2) & 0xFF
    length = frame.values.raw_at(stack_size - 1)
    memory_size = len(memory)
    if not _memory_range_is_valid(memory_size, destination, length):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, destination)
    for index in range(length):
        memory[destination + index] = value
    frame.values.truncate(stack_size - 3)
    ctx.ip = next_ip
    return None


@_handler(I32_TRUNC_F32_S)
def _h_i32_trunc_f32_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 32, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(converted))
    ctx.ip = ip + 1
    return None


@_handler(I32_TRUNC_F64_S)
def _h_i32_trunc_f64_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 32, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(converted))
    ctx.ip = ip + 1
    return None


@_handler(I64_TRUNC_F32_S)
def _h_i64_trunc_f32_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 64, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.ip = ip + 1
    return None


@_handler(I64_TRUNC_F32_U)
def _h_i64_trunc_f32_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 64, False)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.ip = ip + 1
    return None


@_handler(I64_TRUNC_F64_S)
def _h_i64_trunc_f64_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 64, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.ip = ip + 1
    return None


@_handler(I64_TRUNC_F64_U)
def _h_i64_trunc_f64_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 64, False)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    assert frame.values.push_i64(converted)
    ctx.ip = ip + 1
    return None


@_handler(F32_CONVERT_I32_S)
def _h_f32_convert_i32_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_i32(frame.values.pop_back())
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.ip = ip + 1
    return None


@_handler(F32_CONVERT_I32_U)
def _h_f32_convert_i32_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_u32(frame.values.pop_back())
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.ip = ip + 1
    return None


@_handler(F32_CONVERT_I64_S)
def _h_f32_convert_i64_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.ip = ip + 1
    return None


@_handler(F32_CONVERT_I64_U)
def _h_f32_convert_i64_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f32(_to_f32(float(_to_u64(a))))
    ctx.ip = ip + 1
    return None


@_handler(F64_CONVERT_I32_S)
def _h_f64_convert_i32_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_i32(frame.values.pop_back())
    assert frame.values.push_f64(float(a))
    ctx.ip = ip + 1
    return None


@_handler(F64_CONVERT_I32_U)
def _h_f64_convert_i32_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_u32(frame.values.pop_back())
    assert frame.values.push_f64(float(a))
    ctx.ip = ip + 1
    return None


@_handler(F64_CONVERT_I64_S)
def _h_f64_convert_i64_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f64(float(a))
    ctx.ip = ip + 1
    return None


@_handler(F64_CONVERT_I64_U)
def _h_f64_convert_i64_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    assert frame.values.push_f64(float(_to_u64(a)))
    ctx.ip = ip + 1
    return None


@_handler(F64_PROMOTE_F32)
def _h_f64_promote_f32(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f64(float("nan") if math.isnan(a) else float(a))
    ctx.ip = ip + 1
    return None


@_handler(F32_DEMOTE_F64)
def _h_f32_demote_f64(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f32(float("nan") if math.isnan(a) else _to_f32(float(a)))
    ctx.ip = ip + 1
    return None


@_handler(F32_ABS)
def _h_f32_abs(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(abs(a)))
    ctx.ip = ip + 1
    return None


@_handler(F32_NEG)
def _h_f32_neg(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(-a))
    ctx.ip = ip + 1
    return None


@_handler(F64_ABS)
def _h_f64_abs(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(abs(a)))
    ctx.ip = ip + 1
    return None


@_handler(F64_NEG)
def _h_f64_neg(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(-a))
    ctx.ip = ip + 1
    return None


@_handler(F32_EQ)
def _h_f32_eq(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F32_NE)
def _h_f32_ne(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F64_EQ)
def _h_f64_eq(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F64_NE)
def _h_f64_ne(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_REM_S)
def _h_i64_rem_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(I64_REM_U)
def _h_i64_rem_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(I64_AND)
def _h_i64_and(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a & b))
    ctx.ip = ip + 1
    return None


@_handler(I64_OR)
def _h_i64_or(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a | b))
    ctx.ip = ip + 1
    return None


@_handler(I64_XOR)
def _h_i64_xor(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a ^ b))
    ctx.ip = ip + 1
    return None


@_handler(I64_SHL)
def _h_i64_shl(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    assert frame.values.push_i64(_to_i64((v << (k % 64)) & I64_MASK))
    ctx.ip = ip + 1
    return None


@_handler(I64_SHR_S)
def _h_i64_shr_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    assert frame.values.push_i64(_to_i64(_to_i64(v) >> (k % 64)))
    ctx.ip = ip + 1
    return None


@_handler(I64_SHR_U)
def _h_i64_shr_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    assert frame.values.push_i64(_to_i64(_to_u64(v) >> (k % 64)))
    ctx.ip = ip + 1
    return None


@_handler(I64_ROTL)
def _h_i64_rotl(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    k %= 64
    v = _to_u64(v)
    rotated = ((v << k) | (v >> (64 - k))) & I64_MASK if k else v
    assert frame.values.push_i64(_to_i64(rotated))
    ctx.ip = ip + 1
    return None


@_handler(I64_ROTR)
def _h_i64_rotr(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    k %= 64
    v = _to_u64(v)
    rotated = ((v >> k) | (v << (64 - k))) & I64_MASK if k else v
    assert frame.values.push_i64(_to_i64(rotated))
    ctx.ip = ip + 1
    return None


@_handler(I64_CLZ)
def _h_i64_clz(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    if v == 0:
        assert frame.values.push_i64(64)
    else:
        assert frame.values.push_i64(64 - v.bit_length())
    ctx.ip = ip + 1
    return None


@_handler(I64_CTZ)
def _h_i64_ctz(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    if v == 0:
        assert frame.values.push_i64(64)
    else:
        assert frame.values.push_i64((v & -v).bit_length() - 1)
    ctx.ip = ip + 1
    return None


@_handler(I64_POPCNT)
def _h_i64_popcnt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    assert frame.values.push_i64(bin(v).count("1"))
    ctx.ip = ip + 1
    return None


@_handler(I64_GT_S)
def _h_i64_gt_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) > _to_i64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_GT_U)
def _h_i64_gt_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) > _to_u64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_LE_S)
def _h_i64_le_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) <= _to_i64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_LE_U)
def _h_i64_le_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) <= _to_u64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_GE_S)
def _h_i64_ge_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) >= _to_i64(b) else 0)
    ctx.ip = ip + 1
    return None


@_handler(I64_GE_U)
def _h_i64_ge_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) >= _to_u64(b) else 0)
    ctx.ip = ip + 1
    return None


# --- Additional F32 / F64 Math Handlers ---


@_handler(F32_CEIL)
def _h_f32_ceil(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_integral_round(a, "ceil")))
    ctx.ip = ip + 1
    return None


@_handler(F32_FLOOR)
def _h_f32_floor(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_integral_round(a, "floor")))
    ctx.ip = ip + 1
    return None


@_handler(F32_TRUNC)
def _h_f32_trunc(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_integral_round(a, "trunc")))
    ctx.ip = ip + 1
    return None


@_handler(F32_NEAREST)
def _h_f32_nearest(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(_wasm_nearest(a)))
    ctx.ip = ip + 1
    return None


@_handler(F32_COPYSIGN)
def _h_f32_copysign(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(math.copysign(a, b)))
    ctx.ip = ip + 1
    return None


@_handler(F64_CEIL)
def _h_f64_ceil(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_integral_round(a, "ceil"))
    ctx.ip = ip + 1
    return None


@_handler(F64_FLOOR)
def _h_f64_floor(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_integral_round(a, "floor"))
    ctx.ip = ip + 1
    return None


@_handler(F64_TRUNC)
def _h_f64_trunc(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_integral_round(a, "trunc"))
    ctx.ip = ip + 1
    return None


@_handler(F64_NEAREST)
def _h_f64_nearest(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(_wasm_nearest(a))
    ctx.ip = ip + 1
    return None


@_handler(F64_COPYSIGN)
def _h_f64_copysign(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(math.copysign(a, b)))
    ctx.ip = ip + 1
    return None


@_handler(F64_MIN)
def _h_f64_min(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(F64_MAX)
def _h_f64_max(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
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
    ctx.ip = ip + 1
    return None


@_handler(F64_LT)
def _h_f64_lt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F64_LE)
def _h_f64_le(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F64_GT)
def _h_f64_gt(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.ip = ip + 1
    return None


@_handler(F64_GE)
def _h_f64_ge(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.ip = ip + 1
    return None


# --- Conversion & Reinterpret Handlers ---


@_handler(I32_EXTEND8_S)
def _h_i32_extend8_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u32(frame.values.pop_back()) & 0xFF
    pushed = frame.values.push_back(_to_i32(value - 0x100 if value & 0x80 else value))
    assert pushed
    ctx.ip = ip + 1
    return None


@_handler(I32_EXTEND16_S)
def _h_i32_extend16_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u32(frame.values.pop_back()) & 0xFFFF
    pushed = frame.values.push_back(_to_i32(value - 0x10000 if value & 0x8000 else value))
    assert pushed
    ctx.ip = ip + 1
    return None


@_handler(I64_EXTEND8_S)
def _h_i64_extend8_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u64(frame.values.pop_i64()) & 0xFF
    assert frame.values.push_i64(_to_i64(value - 0x100 if value & 0x80 else value))
    ctx.ip = ip + 1
    return None


@_handler(I64_EXTEND16_S)
def _h_i64_extend16_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u64(frame.values.pop_i64()) & 0xFFFF
    assert frame.values.push_i64(_to_i64(value - 0x10000 if value & 0x8000 else value))
    ctx.ip = ip + 1
    return None


@_handler(I64_EXTEND32_S)
def _h_i64_extend32_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    value = _to_u64(frame.values.pop_i64()) & 0xFFFF_FFFF
    assert frame.values.push_i64(_to_i64(value - 0x1_0000_0000 if value & 0x8000_0000 else value))
    ctx.ip = ip + 1
    return None


@_handler(I32_WRAP_I64)
def _h_i32_wrap_i64(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    frame.values.push_back(_to_i32(a))
    ctx.ip = ip + 1
    return None


@_handler(I32_TRUNC_F32_U)
def _h_i32_trunc_f32_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(int(a)))
    ctx.ip = ip + 1
    return None


@_handler(I32_TRUNC_F64_U)
def _h_i32_trunc_f64_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(int(a)))
    ctx.ip = ip + 1
    return None


@_handler(I64_EXTEND_I32_S)
def _h_i64_extend_i32_s(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    assert frame.values.push_i64(_to_i64(a))
    ctx.ip = ip + 1
    return None


@_handler(I64_EXTEND_I32_U)
def _h_i64_extend_i32_u(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    assert frame.values.push_i64(_to_i64(_to_u32(a)))
    ctx.ip = ip + 1
    return None


@_handler(I32_REINTERPRET_F32)
def _h_i32_reinterpret_f32(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    u = struct.unpack("<i", struct.pack("<f", a))[0]
    frame.values.push_back(_to_i32(u))
    ctx.ip = ip + 1
    return None


@_handler(F32_REINTERPRET_I32)
def _h_f32_reinterpret_i32(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    a = _to_u32(a)
    f = struct.unpack("<f", struct.pack("<I", a))[0]
    assert frame.values.push_f32(_to_f32(f))
    ctx.ip = ip + 1
    return None


@_handler(I64_REINTERPRET_F64)
def _h_i64_reinterpret_f64(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    u = struct.unpack("<q", struct.pack("<d", a))[0]
    assert frame.values.push_i64(_to_i64(u))
    ctx.ip = ip + 1
    return None


@_handler(F64_REINTERPRET_I64)
def _h_f64_reinterpret_i64(
    ctx: ExecutionContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    a = _to_u64(a)
    d = struct.unpack("<d", struct.pack("<Q", a))[0]
    assert frame.values.push_f64(float(d))
    ctx.ip = ip + 1
    return None


# Complete the direct-indexed table after decorator registration without
# materializing an iterator or a temporary tuple. The table is then read by
# every interpreter dispatch step, so this storage is required runtime state.
while len(_HANDLERS) < 256:
    _HANDLERS.append(None)

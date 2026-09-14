"""
experiments/pysim/tier2_runtime/interpreter.py
A minimal reference interpreter for the wasm_opcodes subset, used as the
correctness oracle the JIT's output is checked against -- mirroring the
real project's own "interpreter + JIT, cross-checked" architecture
(docs/components/tier2_runtime/runtime_interpreter.md /
docs/components/tier3_jit/jit_compiler.md), just without the ARM/Copy-and-
Patch specifics.
Execution model: `docs/specs/wasm_instruction_set.md` §1 mandates a real
**threaded interpreter** (`{ThreadedInterpreter}`). Every handler in this
file receives the same `(ctx, sp, local_base, tos)` state and returns a
continuation with an optional trap, or `None` to end the call. The
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
independently-growing stacks (ADR-INTERP-03, runtime_interpreter.md §3.1):
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
from dataclasses import dataclass, field
from typing import Protocol

import cython
from control_flow import (
    FB_CONF_MAX_NESTING_DEPTH,
    OpcodeAttribute,
    build_control_map,
    opcode_has_attribute,
)
from interop_abi import (
    NATIVE_VALUE_STACK_CAPACITY,
    ExecutionContextNative,
    NativeValueStack,
)
from leb128 import decode_signed, decode_unsigned
from native_stacks import (
    ControlFrameKind,
    NativeControlStack,
    _ControlFrameWindow,
    _LocalStackWindow,
)
from system_containers import StaticVector
from vmmio import VMMIOController, VmmioStatus
from wasm_module import (
    F32,
    F64,
    I64,
    WASM_LOCAL_SLOT_WORDS,
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
    I64_EXTEND_I32_S,
    I64_EXTEND_I32_U,
    I64_GE_S,
    I64_GE_U,
    I64_GT_S,
    I64_GT_U,
    I64_LE_S,
    I64_LE_U,
    I64_LOAD,
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
    I64_SUB,
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

I32_MASK = 0xFFFFFFFF
PAGE_SIZE = 65536


@cython.locals(v=cython.longlong)
def _to_i32(v: int) -> int:
    v &= I32_MASK
    return v - (1 << 32) if v & 0x8000_0000 else v


@cython.locals(v=cython.longlong)
def _to_u32(v: int) -> int:
    return v & I32_MASK


def _to_f32(v: float) -> float:
    """Rounds a Python float (double) to IEEE 754 single-precision float32."""
    return struct.unpack("<f", struct.pack("<f", float(v)))[0]


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


class Trap(Exception):
    pass


class WasmNumber(Protocol):
    """Numeric host value accepted at the public interpreter boundary."""

    def __int__(self) -> int:
        ...

    def __float__(self) -> float:
        ...


FB_CONF_MAX_VALUE_STACK = 64
FB_CONF_MAX_LOCAL_STACK = NATIVE_VALUE_STACK_CAPACITY


def _encode_public_args(
    values: Sequence[WasmNumber], param_types: Sequence[str]
) -> StaticVector[int]:
    assert len(values) == len(param_types)
    raw_args: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
    for value, value_type in zip(values, param_types, strict=True):
        if value_type == I64:
            raw = int(value) & I64_MASK
            assert raw_args.push_back(raw & I32_MASK)
            assert raw_args.push_back(raw >> 32)
        elif value_type == F32:
            bits = struct.unpack("<I", struct.pack("<f", float(value)))[0]
            assert raw_args.push_back(bits)
        elif value_type == F64:
            bits = struct.unpack("<Q", struct.pack("<d", float(value)))[0]
            assert raw_args.push_back(bits & I32_MASK)
            assert raw_args.push_back(bits >> 32)
        else:
            assert raw_args.push_back(int(value) & I32_MASK)
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
    globals: list[int]
    tables: list[list[int | None]]
    host_functions: list[Callable[..., int | None] | None]
    vmmio: VMMIOController | None = None
    phys_mem: bytearray | None = None


class InterpreterContext:
    """Execution-context-owned stacks shared by the complete call chain."""

    __slots__ = (
        "_c_context",
        "_context_ptr",
        "call_frame_offsets",
        "call_frame_stack",
        "control_frame_stack",
        "local_offset",
        "local_stack",
        "module",
        "operand_stack",
    )

    def __init__(self, module: Module | None = None):
        # The interpreter and JIT share the same Native ABI record. Runtime
        # value, local, and control stacks are Native fixed-capacity records;
        # activation metadata remains a Python-side simulator detail.
        self._c_context = ExecutionContextNative()
        self._context_ptr = ctypes.cast(ctypes.pointer(self._c_context), ctypes.c_void_p)
        self.operand_stack: NativeValueStack = NativeValueStack(FB_CONF_MAX_VALUE_STACK)
        self.local_stack: NativeValueStack = NativeValueStack(FB_CONF_MAX_LOCAL_STACK)
        self.local_offset = 0
        self.module = module
        self.call_frame_offsets: StaticVector[int] = StaticVector(
            capacity=FB_CONF_MAX_NESTING_DEPTH
        )
        self.call_frame_stack: StaticVector[CallFrame] = StaticVector(
            capacity=FB_CONF_MAX_NESTING_DEPTH
        )
        self.control_frame_stack: NativeControlStack = NativeControlStack(FB_CONF_MAX_NESTING_DEPTH)

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        """Pointer to the fixed-size native execution-context backing store."""
        return self._context_ptr

    @property
    def native_context(self) -> ExecutionContextNative:
        """Return the shared Native ABI record used by interpreter and JIT."""
        return self._c_context

    def begin_call_frame(
        self,
        raw_args: StaticVector[int],
        func_index: int,
        env: ExecEnv,
    ) -> CallFrame:
        """Push one frame's locals into the context-owned Native local stack."""
        frame_offset = self.local_offset
        frame = CallFrame(
            self,
            func_index=func_index,
            frame_offset=frame_offset,
            env=env,
        )
        local_slot_count = len(frame.local_widths) * WASM_LOCAL_SLOT_WORDS
        assert len(raw_args) == local_slot_count
        if frame_offset + local_slot_count > self.local_stack.capacity:
            raise Trap("local stack capacity exceeded")
        if not self.call_frame_offsets.push_back(frame_offset):
            raise Trap("call-frame offset stack capacity exceeded")
        if not self.call_frame_stack.push_back(frame):
            self.call_frame_offsets.pop_back()
            raise Trap("call-frame stack capacity exceeded")
        if not self.local_stack.extend(raw_args):
            self.call_frame_stack.pop_back()
            self.call_frame_offsets.pop_back()
            raise Trap("local stack capacity exceeded")
        self.local_offset += local_slot_count
        return frame

    def end_call_frame(self, frame: CallFrame) -> None:
        """Pop the active frame and its locals from the context stacks."""
        assert self.call_frame_offsets
        assert self.call_frame_offsets[-1] == frame.frame_offset
        assert self.call_frame_stack
        assert self.call_frame_stack[-1] is frame
        self.local_stack.truncate(frame.frame_offset)
        self.local_offset = frame.frame_offset
        self.call_frame_offsets.pop_back()
        self.call_frame_stack.pop_back()

    def bind_handler_state(self, ip: int, frame: CallFrame) -> None:
        """Publish the current native handler state in the execution context."""
        assert self.call_frame_stack
        assert self.call_frame_stack[-1] is frame
        self._c_context.ip = ip


def _read_memarg(code: bytes, ip: int) -> tuple[int, int]:
    align, off = decode_unsigned(code, ip + 1)
    mem_offset, next_ip = decode_unsigned(code, off)
    return mem_offset, next_ip


class CallFrame:
    """
    Activation descriptor for one function. `values`, `locals`, and `frames`
    are windows into the three independent stacks owned by `InterpreterContext`;
    this object never owns a per-function stack.
    """

    __slots__ = (
        "_frames",
        "_locals",
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
        "local_i32_only",
        "local_widths",
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
        local_widths = function.local_widths_cache
        local_i32_only = function.local_i32_only_cache
        assert local_widths is not None
        assert local_i32_only is not None
        self.context = context
        self.values = context.operand_stack
        self.control_base = len(context.control_frame_stack)
        self._frames = _ControlFrameWindow(context.control_frame_stack, self.control_base)
        self.func_index = func_index
        self.has_nested_calls = function.has_nested_calls
        self.frame_offset = frame_offset
        self.local_count = len(local_widths)
        self.local_widths = local_widths
        self.local_i32_only = local_i32_only
        self._locals = _LocalStackWindow(
            context.local_stack,
            frame_offset,
            local_widths,
            len(local_widths) * WASM_LOCAL_SLOT_WORDS,
        )
        self.code = function.code
        assert function.control_map is not None
        self.control_map = function.control_map
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

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        return self.context.context_ptr

    @property
    def frames(self) -> _ControlFrameWindow:
        return self._frames

    @property
    def locals(self) -> _LocalStackWindow:
        return self._locals

# The interpreter call's resumable continuation: (next_ip, frame, local_base,
# tos), or None to end this call (RETURN, or branching past the outermost
# implicit block). `tos` remains a runtime/JIT boundary field; operand values
# themselves live only in the Native stack.
_Cont = tuple[int, CallFrame, _LocalStackWindow, int] | None


class DebuggerAttachment(Protocol):
    halted: bool
    stop_signal: int

# A per-opcode handler's return shape. The next instruction pointer is written
# to ctx, and the returned continuation carries the exact four arguments for
# the next handler: (ctx, sp, local_base, tos). The final field is the trap
# outcome; a Trap value makes the outcome observable without an exception-based
# adapter on the normal handler path.
_HandlerResult = tuple[
    InterpreterContext, NativeValueStack, _LocalStackWindow, int, Trap | None
] | None
_HandlerFn = Callable[
    [InterpreterContext, NativeValueStack, _LocalStackWindow, int], _HandlerResult
]

# Fixed 256-slot direct-indexed dispatch table for WASM byte opcodes (0x00..0xFF)
_HANDLERS: list[_HandlerFn | None] = [None] * 256
_BASIC_BLOCK_BOUNDARY: tuple[bool, ...] = tuple(
    opcode_has_attribute(opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY) for opcode in range(256)
)


def _handler(opcode: int) -> Callable[[_HandlerFn], _HandlerFn]:
    def register(fn: _HandlerFn) -> _HandlerFn:
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

    return frame.frames.branch(depth, frame.values)


@dataclass(slots=True)
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

    func_index: int
    context: InterpreterContext
    cont: _Cont
    call_stack: StaticVector[tuple[int, _Cont]] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_NESTING_DEPTH)
    )
    finished: bool = False
    results: StaticVector[WasmNumber] | None = None
    trap: Trap | None = None

    def current_pc(self) -> int:
        """
        Return this call's unified `(func_index, ip)` address.

        The runtime must finalize a call before asking for its PC. A missing
        continuation or an instruction pointer past the code is an invariant
        violation, not a valid address state.
        """
        assert self.cont is not None
        ip, frame, _, _ = self.cont
        assert 0 <= ip < len(frame.code)
        return (self.func_index << 16) | ip


class Interpreter:
    __slots__ = (
        "_env",
        "debugger",
        "globals",
        "host_functions",
        "memory",
        "module",
        "phys_mem",
        "tables",
        "vmmio",
    )

    def __init__(
        self,
        module: Module,
        memory: bytearray | None = None,
        host_functions: list[Callable[..., int | None] | None] | None = None,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
    ):
        self.module = module
        self.memory = memory

        self.host_functions = (
            host_functions if host_functions is not None else [None] * len(module.imports)
        )
        self.globals: list[int] = [g.init_value for g in module.globals]
        self.tables: list[list[int | None]] = [
            module.table_contents(i) for i in range(len(module.tables))
        ]
        self.debugger: DebuggerAttachment | None = None
        self.vmmio = vmmio
        self.phys_mem = phys_mem
        self._env = ExecEnv(
            module,
            memory,
            self.globals,
            self.tables,
            self.host_functions,
            vmmio=vmmio,
            phys_mem=phys_mem,
        )
        if self.module.start_function is not None:
            self.call(self.module.start_function, ())

    def attach_debugger(self, debugger: DebuggerAttachment) -> None:
        """
        Records the attached debugger. Unlike IntegratedHybridEngine's
        {DebuggerLabelTableSwitch}, the threaded interpreter's `_HANDLERS`
        dispatch table is a single fixed table with no separate debug/normal
        variant to switch between -- breakpoint/step behavior for
        interpreter-only execution is driven by DebuggerManager itself.
        """
        self.debugger = debugger

    def detach_debugger(self) -> None:
        self.debugger = None

    def register_vector_table(
        self, vector_table: Sequence[Callable[[int, int, bool], int | None] | None]
    ) -> None:
        """Register host syscall vectors used by static-vMMIO accesses."""
        assert self.vmmio is not None
        self.vmmio.register_vector_table(vector_table)

    def flush_jit_cache(self) -> None:
        """
        No-op: a bare `Interpreter` never owns a JIT cache -- only a
        `RuntimeEngine` wrapping one does. Exists so `DebuggerManager` can
        treat an `Interpreter` and an `IntegratedHybridEngine` uniformly.
        """

    def call(self, func_index: int, args: Sequence[int]) -> StaticVector[int]:
        """Runs a function to completion in one call."""
        call_state = self.start(func_index, args)
        if not call_state.finished:
            _, frame, _, _ = call_state.cont
            if not frame.has_nested_calls:
                results = self._call_without_nested_calls(call_state)
                if results is not None:
                    return results
                assert call_state.trap is not None
                raise call_state.trap
        while not call_state.finished:
            call_state = self._step(call_state, stop_at_boundary=False)
        if call_state.trap is not None:
            raise call_state.trap
        assert call_state.results is not None
        return call_state.results

    def _call_without_nested_calls(
        self, call_state: InterpreterCall
    ) -> StaticVector[WasmNumber] | None:
        """Complete a call without materializing CPS state at every instruction."""
        ip, frame, locals_arr, _ = call_state.cont
        while True:
            if ip >= len(frame.code):
                break
            opcode = frame.code[ip]
            handler = _HANDLERS[opcode]
            if handler is None:
                raise NotImplementedError(f"interpreter: unhandled opcode 0x{opcode:02X}")
            call_state.context.bind_handler_state(ip, frame)
            tos = frame.values.raw_top() if frame.values else 0
            result = handler(call_state.context, frame.values, locals_arr, tos)
            if result is None:
                break
            result_ctx, result_sp, locals_arr, tos, trap = result
            assert result_ctx is call_state.context
            assert result_sp is frame.values
            if trap is not None:
                self._abort_call(call_state, trap)
                return None
            ip = int(result_ctx.native_context.ip)

        func_type = self.module.func_type(call_state.func_index)
        while frame.frames:
            frame.frames.pop_back()
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
            assert results.push_back(result_value)
        call_state.cont = None
        call_state.finished = True
        call_state.results = results
        return results

    def _abort_call(self, call_state: InterpreterCall, trap: Trap) -> None:
        """Terminate every active frame and publish a runtime trap outcome."""
        while call_state.context.call_frame_stack:
            frame = call_state.context.call_frame_stack[-1]
            while frame.frames:
                frame.frames.pop_back()
            call_state.context.end_call_frame(frame)
        while call_state.call_stack:
            call_state.call_stack.pop_back()
        call_state.cont = None
        call_state.finished = True
        call_state.results = None
        call_state.trap = trap

    def run_iter(self, func_index: int, args: Sequence[int]) -> Iterator[InterpreterCall]:
        """
        Drives a call basic-block by basic-block, yielding the (possibly still-unfinished)
        `call_state` after every `step()` and once more after it finishes.
        """
        call_state = self.start(func_index, args)
        while not call_state.finished:
            override = yield call_state
            call_state = override if override is not None else self.step(call_state)
        yield call_state

    def start(self, func_index: int, args: Sequence[int]) -> InterpreterCall:
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
            imp = self.module.imports[func_index]
            return (
                StaticVector(capacity=4),
                Trap(f"no host handler registered for import {imp.module}.{imp.name}"),
            )
        result = handler(*[_to_i32(int(a)) for a in args])
        ft = self.module.func_type(func_index)
        results: StaticVector[WasmNumber] = StaticVector(capacity=4)
        if ft.results:
            result_type = ft.results[0]
            if result_type == I64:
                result_value: WasmNumber = _to_i64(int(result))
            elif result_type == F32:
                result_value = _to_f32(float(result))
            elif result_type == F64:
                result_value = float(result)
            else:
                result_value = _to_i32(int(result))
            assert results.push_back(result_value)
        return results, None

    def _build_frame(
        self, func_index: int, raw_args: StaticVector[int], context: InterpreterContext
    ) -> tuple[CallFrame, _LocalStackWindow]:
        """
        Builds the initial frame + locals for a WASM (non-import) function
        activation. `raw_args` contains the packed parameter values as
        32-bit slots; the load-time parameter offsets place them into the
        aligned local layout exactly once at frame creation.
        The internal call path obtains it directly from the operand stack;
        the public entry path encodes host values once at that boundary.

        The Native local slots are pushed into `context` by
        `InterpreterContext.begin_call_frame`; the operand stack is also owned
        by that same context and is shared across the complete call chain.
        """
        fn = self.module.functions[func_index - len(self.module.imports)]
        function_type = self.module.types[fn.type_index]
        local_slot_count = fn.local_slot_count_cache
        local_widths = fn.local_widths_cache
        param_packed_slot_count = fn.param_packed_slot_count_cache
        assert local_slot_count is not None
        assert local_widths is not None
        assert fn.local_i32_only_cache is not None
        assert param_packed_slot_count is not None
        assert len(raw_args) == param_packed_slot_count
        local_values: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_LOCAL_STACK)
        for _ in range(local_slot_count):
            assert local_values.push_back(0)
        raw_offset = 0
        for index, width in enumerate(local_widths[: len(function_type.params)]):
            for word in range(width):
                local_values[index * WASM_LOCAL_SLOT_WORDS + word] = raw_args[raw_offset]
                raw_offset += 1
        assert raw_offset == len(raw_args)
        if fn.control_map is None:
            fn.control_map = build_control_map(fn.code)
        assert fn.control_map is not None
        frame = context.begin_call_frame(
            local_values,
            func_index=func_index,
            env=self._env,
        )
        return frame, frame.locals

    @cython.locals(ip=cython.Py_ssize_t, op=cython.uchar)
    def step(self, call_state: InterpreterCall) -> InterpreterCall:
        """
        Executes one basic block (up to the next boundary instruction) and returns
        `call_state`, mutated in place: still `finished == False` with a resumable
        `.cont`, or `finished == True` with `.results` set once the outermost call
        actually returns.
        """
        return self._step(call_state, stop_at_boundary=True)

    @cython.locals(ip=cython.Py_ssize_t, op=cython.uchar, stop_at_boundary=cython.bint)
    def _step(self, call_state: InterpreterCall, stop_at_boundary: bool) -> InterpreterCall:
        """Run until a boundary or completion, depending on the driving caller."""
        while True:
            ip, frame, locals_arr, tos = call_state.cont
            if ip < len(frame.code):
                op = frame.code[ip]
                if op == CALL or op == CALL_INDIRECT:
                    trap = self._enter_or_resolve_call(
                        call_state, op, ip, frame, locals_arr, tos
                    )
                    if trap is not None:
                        self._abort_call(call_state, trap)
                        return call_state
                    if stop_at_boundary:
                        return call_state
                    continue
                is_boundary = _BASIC_BLOCK_BOUNDARY[op]
                handler = _HANDLERS[op]
                if handler is None:
                    raise NotImplementedError(f"interpreter: unhandled opcode 0x{op:02X}")
                call_state.context.bind_handler_state(ip, frame)
                result = handler(call_state.context, frame.values, locals_arr, tos)
                if result is None:
                    call_state.cont = None
                else:
                    result_ctx, result_sp, result_locals, next_tos, trap = result
                    assert result_ctx is call_state.context
                    assert result_sp is frame.values
                    if trap is not None:
                        self._abort_call(call_state, trap)
                        return call_state
                    next_ip = int(result_ctx.native_context.ip)
                    result_frame = result_ctx.call_frame_stack[-1]
                    assert result_frame is frame
                    call_state.cont = (next_ip, result_frame, result_locals, next_tos)
                if call_state.cont is not None:
                    if is_boundary and stop_at_boundary:
                        return call_state
                    continue
                # cont is None: this frame just ended (RETURN, or a branch
                # past the outermost block) -- always handle it immediately
                # below.

            ft = self.module.func_type(call_state.func_index)
            # The context-owned control-frame window is independent from the
            # LocalStack frame lifetime; discard any frames left by a JIT
            # boundary before releasing this call activation.
            while frame.frames:
                frame.frames.pop_back()
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
                    assert results.push_back(result_value)
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
            call_state.cont = (p_ip, p_frame, p_locals, 0)
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
                return Trap(
                    f"call_indirect: table index {table_slot} out of bounds (size {len(table)})"
                )
            callee_func_index = table[table_slot]
            if callee_func_index is None:
                return Trap(f"call_indirect: table slot {table_slot} is uninitialized")
            declared_type = self.module.types[typeidx]
            actual_type = self.module.func_type(callee_func_index)
            if declared_type != actual_type:
                return Trap(
                    f"call_indirect: type mismatch (declared {declared_type}, "
                    f"actual {actual_type} at table slot {table_slot})"
                )
            callee_ft = declared_type

        if self.module.is_import(callee_func_index):
            call_args: StaticVector[WasmNumber] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
            popped_args: StaticVector[WasmNumber] = StaticVector(
                capacity=FB_CONF_MAX_VALUE_STACK
            )
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
                assert popped_args.push_back(value)
            for index in range(len(popped_args) - 1, -1, -1):
                assert call_args.push_back(popped_args[index])
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
                    return Trap("operand stack capacity exceeded")
            resume_tos = frame.values[-1] if frame.values else 0
            call_state.cont = (next_ip, frame, locals_arr, resume_tos)
            return None

        popped_raw_args: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
        for value_type in reversed(callee_ft.params):
            for _ in range(value_slot_width(value_type)):
                value = frame.values.pop_back()
                assert value is not None
                assert popped_raw_args.push_back(value)
        raw_call_args: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
        for index in range(len(popped_raw_args) - 1, -1, -1):
            assert raw_call_args.push_back(popped_raw_args[index])
        resume_tos = frame.values[-1] if frame.values else 0
        resume_cont = (next_ip, frame, locals_arr, resume_tos)
        try:
            callee_frame, callee_locals = self._build_frame(
                callee_func_index, raw_call_args, call_state.context
            )
        except Trap as trap:
            return trap
        if not call_state.call_stack.push_back((call_state.func_index, resume_cont)):
            return Trap("call stack capacity exceeded")
        call_state.func_index = callee_func_index
        callee_tos = callee_frame.values[-1] if callee_frame.values else 0
        call_state.cont = (0, callee_frame, callee_locals, callee_tos)
        return None


# Per-opcode logical CPS handlers. Each receives the native CPS arguments and
# returns the exact arguments for the next handler, plus an optional trap.
# ---------------------------------------------------------------------------


@_handler(UNREACHABLE)
def _h_unreachable(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    return (ctx, sp, local_base, tos, Trap("unreachable instruction executed"))


@_handler(NOP)
def _h_nop(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(BLOCK)
def _h_block(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end = frame.control_map.block(ip)[0]
    if not frame.frames.push_back(ControlFrameKind.BLOCK, ip, match_end, len(frame.values)):
        return (ctx, sp, local_base, tos, Trap("control frame capacity exceeded"))
    ctx.native_context.ip = ip + 2
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(LOOP)
def _h_loop(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end = frame.control_map.block(ip)[0]
    if not frame.frames.push_back(ControlFrameKind.LOOP, ip, match_end, len(frame.values)):
        return (ctx, sp, local_base, tos, Trap("control frame capacity exceeded"))
    ctx.native_context.ip = ip + 2
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(IF)
def _h_if(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    match_end, else_off = frame.control_map.block(ip)
    cond = frame.values.pop_back()
    if cond == 0:
        if else_off is not None:
            if not frame.frames.push_back(ControlFrameKind.IF, ip, match_end, len(frame.values)):
                return (ctx, sp, local_base, tos, Trap("control frame capacity exceeded"))
            ctx.native_context.ip = else_off + 1
            return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)
        else:
            ctx.native_context.ip = match_end + 1
            return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)
    if not frame.frames.push_back(ControlFrameKind.IF, ip, match_end, len(frame.values)):
        return (ctx, sp, local_base, tos, Trap("control frame capacity exceeded"))
    ctx.native_context.ip = ip + 2
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(ELSE)
def _h_else(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    popped = frame.frames.pop_back() if frame.frames else None
    if frame.boundary_next_pc is not None:
        ctx.native_context.ip = frame.boundary_next_pc & 0xFFFF
        return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)
    if popped is not None:
        ctx.native_context.ip = popped.match_end + 1
        return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(END)
def _h_end(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    if frame.frames:
        frame.frames.pop_back()
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(BR)
@cython.locals(depth=cython.Py_ssize_t)
def _h_br(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    if frame.boundary_next_pc is not None:
        ctx.native_context.ip = frame.boundary_next_pc & 0xFFFF
        return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)
    depth, _ = decode_unsigned(frame.code, ip + 1)
    next_ip = _do_branch(depth, frame)
    if (next_ip) is None:
        return None
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(BR_IF)
@cython.locals(depth=cython.Py_ssize_t, next_ip=cython.Py_ssize_t, cond=cython.longlong)
def _h_br_if(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    depth, next_ip = decode_unsigned(frame.code, ip + 1)
    cond = frame.values.pop_back()
    if cond == 0:
        ctx.native_context.ip = next_ip
        return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)
    if frame.boundary_loops_to is not None:
        ctx.native_context.ip = frame.boundary_loops_to & 0xFFFF
        return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)
    target_ip = _do_branch(depth, frame)
    if (target_ip) is None:
        return None
    ctx.native_context.ip = target_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        return None
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(RETURN)
def _h_return(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
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
    frame.values.pop_back()
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(SELECT)
def _h_select(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    c = frame.values.pop_back()
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(a if c != 0 else b)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(LOCAL_GET)
@cython.locals(idx=cython.Py_ssize_t, next_ip=cython.Py_ssize_t)
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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(LOCAL_SET)
@cython.locals(idx=cython.Py_ssize_t, next_ip=cython.Py_ssize_t)
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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(LOCAL_TEE)
@cython.locals(idx=cython.Py_ssize_t, next_ip=cython.Py_ssize_t)
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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_CONST)
@cython.locals(val=cython.longlong, next_ip=cython.Py_ssize_t)
def _h_i32_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1)
    frame.values.push_back(_to_i32(val))
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_CONST)
def _h_i64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1)
    assert frame.values.push_i64(_to_i64(val))
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_CONST)
def _h_f32_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<f", frame.code[ip + 1 : ip + 5])[0]
    assert frame.values.push_f32(val)
    ctx.native_context.ip = ip + 5
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_CONST)
def _h_f64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<d", frame.code[ip + 1 : ip + 9])[0]
    assert frame.values.push_f64(val)
    ctx.native_context.ip = ip + 9
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(GLOBAL_GET)
def _h_global_get(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    frame.values.push_back(env.globals[idx])
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(GLOBAL_SET)
def _h_global_set(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    idx, next_ip = decode_unsigned(frame.code, ip + 1)
    env.globals[idx] = _to_i32(frame.values.pop_back())
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Loads (Dedicated per-opcode handlers with Bit 31 RAM Bypass) ---


def _vmmio_load(
    env: ExecEnv, addr: int, width: int, signed: bool
) -> tuple[int, Trap | None]:
    if env.vmmio is None:
        return 0, Trap(
            f"memory access out of bounds at addr={addr:#x} (no vMMIO configured)"
        )
    status, phys_addr = env.vmmio.access(addr, is_write=False, value=0)
    if status > VmmioStatus.OK_PHYSICAL:
        return 0, Trap(status)
    if status == VmmioStatus.OK_SYSCALL:
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
        return Trap(f"memory access out of bounds at addr={addr:#x} (no vMMIO configured)")
    status, phys_addr = env.vmmio.access(
        addr,
        is_write=True,
        value=int.from_bytes(val_bytes, "little"),
    )
    if status > VmmioStatus.OK_PHYSICAL:
        return Trap(status)
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
            return (ctx, sp, local_base, tos, trap)
        assert frame.values.push_back(value)
    else:
        if env.memory is None or addr + 4 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.load out of bounds at addr={addr}"))
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 4], "little", signed=True))
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
            return (ctx, sp, local_base, tos, trap)
        assert frame.values.push_back(value)
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.load8_s out of bounds at addr={addr}"))
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 1], "little", signed=True))
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
            return (ctx, sp, local_base, tos, trap)
        assert frame.values.push_back(value)
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.load8_u out of bounds at addr={addr}"))
        frame.values.push_back(env.memory[addr])
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
            return (ctx, sp, local_base, tos, trap)
        assert frame.values.push_back(value)
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.load16_s out of bounds at addr={addr}"))
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 2], "little", signed=True))
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
            return (ctx, sp, local_base, tos, trap)
        assert frame.values.push_back(value)
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.load16_u out of bounds at addr={addr}"))
        frame.values.push_back(int.from_bytes(env.memory[addr : addr + 2], "little", signed=False))
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
            return (ctx, sp, local_base, tos, trap)
    else:
        if env.memory is None or addr + 4 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.store out of bounds at addr={addr}"))
        env.memory[addr : addr + 4] = raw_val
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_STORE8)
def _h_i32_store8(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    mem_offset, next_ip = _read_memarg(frame.code, ip)
    value = frame.values.pop_back() & 0xFF
    addr = _to_u32(frame.values.pop_back()) + mem_offset
    raw_val = bytes([value])
    if addr & 0x8000_0000:
        trap = _vmmio_store(env, addr, raw_val)
        if trap is not None:
            return (ctx, sp, local_base, tos, trap)
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.store8 out of bounds at addr={addr}"))
        env.memory[addr] = value
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
            return (ctx, sp, local_base, tos, trap)
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap(f"i32.store16 out of bounds at addr={addr}"))
        env.memory[addr : addr + 2] = raw_val
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Memory Size / Grow ---


@_handler(MEMORY_SIZE)
def _h_memory_size(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    if env.memory is None:
        return (ctx, sp, local_base, tos, Trap("memory.size with no memory section"))
    frame.values.push_back(len(env.memory) // PAGE_SIZE)
    ctx.native_context.ip = ip + 2
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        env.memory.extend(bytes(delta_pages * PAGE_SIZE))
        frame.values.push_back(old_pages)
    ctx.native_context.ip = ip + 2
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Comparisons (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_EQZ)
def _h_i32_eqz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    frame.values.push_back(1 if frame.values.pop_back() == 0 else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_EQ)
def _h_i32_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) == _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_NE)
def _h_i32_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) != _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_LT_S)
def _h_i32_lt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) < _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_LT_U)
def _h_i32_lt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) < _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_GT_S)
def _h_i32_gt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) > _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_GT_U)
def _h_i32_gt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) > _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_LE_S)
def _h_i32_le_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) <= _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_LE_U)
def _h_i32_le_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) <= _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_GE_S)
@cython.locals(a=cython.longlong, b=cython.longlong)
def _h_i32_ge_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) >= _to_i32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_GE_U)
def _h_i32_ge_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) >= _to_u32(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Unary Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_CLZ)
def _h_i32_clz(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(32 if v == 0 else 32 - v.bit_length())
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_POPCNT)
def _h_i32_popcnt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(bin(v).count("1"))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Binary Arithmetic & Bitwise Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_ADD)
@cython.locals(a=cython.longlong, b=cython.longlong)
def _h_i32_add(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a + b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_SUB)
@cython.locals(a=cython.longlong, b=cython.longlong)
def _h_i32_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a - b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_MUL)
def _h_i32_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a * b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_DIV_S)
def _h_i32_div_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_i32(frame.values.pop_back())
    a = _to_i32(frame.values.pop_back())
    if b == 0:
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    if a == -2147483648 and b == -1:
        return (ctx, sp, local_base, tos, Trap("integer overflow"))
    q = abs(a) // abs(b)
    frame.values.push_back(_to_i32(-q if (a < 0) != (b < 0) else q))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_DIV_U)
def _h_i32_div_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    frame.values.push_back(_to_i32(a // b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_REM_S)
def _h_i32_rem_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_i32(frame.values.pop_back())
    a = _to_i32(frame.values.pop_back())
    if b == 0:
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    r = abs(a) % abs(b)
    frame.values.push_back(_to_i32(-r if a < 0 else r))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_REM_U)
def _h_i32_rem_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    frame.values.push_back(_to_i32(a % b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_AND)
def _h_i32_and(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) & _to_u32(b)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_OR)
def _h_i32_or(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) | _to_u32(b)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_XOR)
def _h_i32_xor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) ^ _to_u32(b)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_SHL)
def _h_i32_shl(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) << (_to_u32(b) & 31)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_SHR_S)
def _h_i32_shr_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_i32(a) >> (_to_u32(b) & 31)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_SHR_U)
def _h_i32_shr_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) >> (_to_u32(b) & 31)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# Helper conversion utilities
I64_MASK = 0xFFFF_FFFF_FFFF_FFFF


@cython.locals(v=cython.ulonglong)
def _to_i64(v: int) -> int:
    v &= I64_MASK
    return v - (1 << 64) if v & 0x8000_0000_0000_0000 else v


@cython.locals(v=cython.ulonglong)
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
            return (ctx, sp, local_base, tos, trap)
    else:
        if env.memory is None or addr + 8 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap("out of bounds memory access"))
        val = struct.unpack("<q", env.memory[addr : addr + 8])[0]
    assert frame.values.push_i64(val)
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
            return (ctx, sp, local_base, tos, trap)
    else:
        if env.memory is None or addr + 8 > len(env.memory):
            return (ctx, sp, local_base, tos, Trap("out of bounds memory access"))
        env.memory[addr : addr + 8] = raw_val
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_LOAD)
def _h_f32_load(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        return (ctx, sp, local_base, tos, Trap("out of bounds memory access"))
    val = struct.unpack("<f", env.memory[addr : addr + 4])[0]
    assert frame.values.push_f32(val)
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        return (ctx, sp, local_base, tos, Trap("out of bounds memory access"))
    env.memory[addr : addr + 4] = struct.pack("<f", val)
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_LOAD)
def _h_f64_load(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    offset, next_ip = _read_memarg(frame.code, ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        return (ctx, sp, local_base, tos, Trap("out of bounds memory access"))
    val = struct.unpack("<d", env.memory[addr : addr + 8])[0]
    assert frame.values.push_f64(val)
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        return (ctx, sp, local_base, tos, Trap("out of bounds memory access"))
    env.memory[addr : addr + 8] = struct.pack("<d", val)
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Const Handlers ---


@_handler(I64_CONST)
def _h_i64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val, next_ip = decode_signed(frame.code, ip + 1)
    assert frame.values.push_i64(_to_i64(val))
    ctx.native_context.ip = next_ip
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_CONST)
def _h_f32_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<f", frame.code[ip + 1 : ip + 5])[0]
    assert frame.values.push_f32(val)
    ctx.native_context.ip = ip + 5
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_CONST)
def _h_f64_const(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    val = struct.unpack("<d", frame.code[ip + 1 : ip + 9])[0]
    assert frame.values.push_f64(val)
    ctx.native_context.ip = ip + 9
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_EQ)
def _h_i64_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) == _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_NE)
def _h_i64_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) != _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_LT_S)
def _h_i64_lt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) < _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_LT_U)
def _h_i64_lt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) < _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_ADD)
def _h_i64_add(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a + b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_SUB)
def _h_i64_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a - b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_MUL)
def _h_i64_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a * b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    if a == -0x8000_0000_0000_0000 and b == -1:
        return (ctx, sp, local_base, tos, Trap("integer overflow"))
    q = abs(a) // abs(b)
    assert frame.values.push_i64(_to_i64(-q if (a < 0) != (b < 0) else q))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    assert frame.values.push_i64(_to_i64(a // b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_SUB)
def _h_f32_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a - b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_MUL)
def _h_f32_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(a * b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_DIV)
def _h_f32_div(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(
        _to_f32(
            a / b
            if b != 0
            else (
                float("nan")
                if a == 0
                else math.copysign(float("inf"), a)
                if b == 0
                else float("inf")
            )
        )
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_SQRT)
def _h_f32_sqrt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(math.sqrt(a) if a >= 0 else float("nan")))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_MIN)
def _h_f32_min(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_f32_min(a, b)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_MAX)
def _h_f32_max(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(_f32_max(a, b)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_LT)
def _h_f32_lt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_LE)
def _h_f32_le(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_GT)
def _h_f32_gt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_GE)
def _h_f32_ge(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_SUB)
def _h_f64_sub(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a - b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_MUL)
def _h_f64_mul(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a * b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_DIV)
def _h_f64_div(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(a / b if b != 0 else float("inf")))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_SQRT)
def _h_f64_sqrt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(math.sqrt(a) if a >= 0 else float("nan")))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Conversion Handlers ---


@_handler(I32_TRUNC_F32_S)
def _h_i32_trunc_f32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    frame.values.push_back(int(a) & I32_MASK)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_TRUNC_F64_S)
def _h_i32_trunc_f64_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    frame.values.push_back(int(a) & I32_MASK)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_CONVERT_I32_S)
def _h_f32_convert_i32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_i32(frame.values.pop_back())
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_CONVERT_I32_U)
def _h_f32_convert_i32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_u32(frame.values.pop_back())
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_CONVERT_I32_S)
def _h_f64_convert_i32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_i32(frame.values.pop_back())
    assert frame.values.push_f64(float(a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_CONVERT_I32_U)
def _h_f64_convert_i32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = _to_u32(frame.values.pop_back())
    assert frame.values.push_f64(float(a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_PROMOTE_F32)
def _h_f64_promote_f32(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f64(float(a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_DEMOTE_F64)
def _h_f32_demote_f64(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f32(_to_f32(float(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_ABS)
def _h_f32_abs(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(abs(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_NEG)
def _h_f32_neg(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(_to_f32(-a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_ABS)
def _h_f64_abs(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(abs(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_NEG)
def _h_f64_neg(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(-a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_EQ)
def _h_f32_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_NE)
def _h_f32_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_EQ)
def _h_f64_eq(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_NE)
def _h_f64_ne(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    r = abs(a) % abs(b)
    assert frame.values.push_i64(_to_i64(-r if a < 0 else r))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
        return (ctx, sp, local_base, tos, Trap("integer divide by zero"))
    assert frame.values.push_i64(_to_i64(a % b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_AND)
def _h_i64_and(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a & b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_OR)
def _h_i64_or(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a | b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_XOR)
def _h_i64_xor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    assert frame.values.push_i64(_to_i64(a ^ b))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_GT_S)
def _h_i64_gt_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) > _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_GT_U)
def _h_i64_gt_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) > _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_LE_S)
def _h_i64_le_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) <= _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_LE_U)
def _h_i64_le_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) <= _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_GE_S)
def _h_i64_ge_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) >= _to_i64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_GE_U)
def _h_i64_ge_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) >= _to_u64(b) else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Additional F32 / F64 Math Handlers ---


@_handler(F32_CEIL)
def _h_f32_ceil(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(
        _to_f32(math.ceil(a) if not math.isnan(a) and not math.isinf(a) else a)
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_FLOOR)
def _h_f32_floor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(
        _to_f32(math.floor(a) if not math.isnan(a) and not math.isinf(a) else a)
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_TRUNC)
def _h_f32_trunc(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(
        _to_f32(math.trunc(a) if not math.isnan(a) and not math.isinf(a) else a)
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_NEAREST)
def _h_f32_nearest(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    assert frame.values.push_f32(
        _to_f32(round(a) if not math.isnan(a) and not math.isinf(a) else a)
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F32_COPYSIGN)
def _h_f32_copysign(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    assert frame.values.push_f32(_to_f32(math.copysign(a, b)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_CEIL)
def _h_f64_ceil(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(
        float(math.ceil(a) if not math.isnan(a) and not math.isinf(a) else a)
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_FLOOR)
def _h_f64_floor(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(
        float(math.floor(a) if not math.isnan(a) and not math.isinf(a) else a)
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_TRUNC)
def _h_f64_trunc(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(
        float(math.trunc(a) if not math.isnan(a) and not math.isinf(a) else a)
    )
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_NEAREST)
def _h_f64_nearest(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    assert frame.values.push_f64(float(round(a) if not math.isnan(a) and not math.isinf(a) else a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_COPYSIGN)
def _h_f64_copysign(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    assert frame.values.push_f64(float(math.copysign(a, b)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_LT)
def _h_f64_lt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_LE)
def _h_f64_le(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_GT)
def _h_f64_gt(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(F64_GE)
def _h_f64_ge(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


# --- Conversion & Reinterpret Handlers ---


@_handler(I32_WRAP_I64)
def _h_i32_wrap_i64(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i64()
    assert a is not None
    frame.values.push_back(_to_i32(a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_TRUNC_F32_U)
def _h_i32_trunc_f32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f32()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return (ctx, sp, local_base, tos, Trap("invalid conversion to integer"))
    frame.values.push_back(_to_i32(int(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I32_TRUNC_F64_U)
def _h_i32_trunc_f64_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_f64()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return (ctx, sp, local_base, tos, Trap("invalid conversion to integer"))
    frame.values.push_back(_to_i32(int(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_EXTEND_I32_S)
def _h_i64_extend_i32_s(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    assert frame.values.push_i64(_to_i64(a))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


@_handler(I64_EXTEND_I32_U)
def _h_i64_extend_i32_u(
    ctx: InterpreterContext, sp: NativeValueStack, local_base: _LocalStackWindow, tos: int
) -> _HandlerResult:
    ip, frame, env = _handler_state(ctx, sp)
    a = frame.values.pop_i32()
    assert a is not None
    assert frame.values.push_i64(_to_i64(_to_u32(a)))
    ctx.native_context.ip = ip + 1
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)


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
    return (ctx, sp, local_base, sp.raw_top() if sp else 0, None)

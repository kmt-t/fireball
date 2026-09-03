"""
experiments/pysim/interpreter.py
A minimal reference interpreter for the wasm_opcodes subset, used as the
correctness oracle the JIT's output is checked against -- mirroring the
real project's own "interpreter + JIT, cross-checked" architecture
(docs/components/tier2_runtime/runtime_interpreter.md /
docs/components/tier3_jit/jit_compiler.md), just without the ARM/Copy-and-
Patch specifics.
Execution model: `docs/specs/wasm_instruction_set.md` §1 mandates a real
**threaded interpreter** (`{ThreadedInterpreter}`) -- every opcode is its
own CPS (continuation-passing) handler with a fixed 4-argument
`__fastcall` signature (`R0: ip`, `R1: stack_bot`, `R2: env`,
`R3: local_base`), not a central switch/if-elif loop a handler merely
falls back into. This file follows that shape for real: `_HANDLERS` maps
opcode -> handler function, each handler receives exactly those four
arguments and returns the *next* continuation itself (or `None` to end the
call) -- dispatch is never done by a shared loop deciding what comes next
on a handler's behalf.
The one adaptation from the literal ARM/native design: native code tail-
calls the next handler directly (or dispatches via a jump table with no
return address at all), which Python cannot do without unbounded
recursion depth for long-running loops. So the four-argument continuation
is *returned* rather than tail-called, and a small trampoline in `_run`
re-dispatches it -- indirect threading instead of direct threading, same
handler-per-opcode shape, no stack growth per WASM instruction.
`R1: stack_bot` addresses, in the real design, one combined region holding
both operand values and block/loop/if control frames (system_config.md's
`FB_CONF_INTERP_STACK_SIZE` comment: "execution_context + フレーム/オペ
ランド"). `CallFrame` is that combined region's Python equivalent, plus
this activation's own decoded instruction table (needed to fetch the next
instruction from `ip`) -- kept there rather than smuggled in as a 5th
argument outside the declared signature.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from control_flow import Instr, decode_all
from wasm_module import F32, F64, Module
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
    F32_CONST,
    F32_CONVERT_I32_S,
    F32_CONVERT_I32_U,
    F32_DEMOTE_F64,
    F32_DIV,
    F32_EQ,
    F32_GE,
    F32_GT,
    F32_LE,
    F32_LOAD,
    F32_LT,
    F32_MAX,
    F32_MIN,
    F32_MUL,
    F32_NE,
    F32_NEG,
    F32_SQRT,
    F32_STORE,
    F32_SUB,
    F64_ABS,
    F64_ADD,
    F64_CONST,
    F64_CONVERT_I32_S,
    F64_CONVERT_I32_U,
    F64_DIV,
    F64_EQ,
    F64_LOAD,
    F64_MUL,
    F64_NE,
    F64_NEG,
    F64_PROMOTE_F32,
    F64_SQRT,
    F64_STORE,
    F64_SUB,
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
    I32_TRUNC_F64_S,
    I32_XOR,
    I64_ADD,
    I64_CONST,
    I64_DIV_S,
    I64_DIV_U,
    I64_EQ,
    I64_EQZ,
    I64_LOAD,
    I64_LT_S,
    I64_LT_U,
    I64_MUL,
    I64_NE,
    I64_STORE,
    I64_SUB,
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


def _to_i32(v: int) -> int:
    v &= I32_MASK
    return v - (1 << 32) if v & 0x8000_0000 else v


def _to_u32(v: int) -> int:
    return v & I32_MASK


class Trap(Exception):
    pass


@dataclass
class _Frame:
    kind: str  # "block" | "loop" | "if"
    start: int  # opcode offset of the BLOCK/LOOP/IF
    match_end: int  # offset of the matching END
    stack_height: int  # operand-stack height at frame entry


@dataclass
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
    vmmio: object | None = None
    task_id: int = 1
    phys_mem: bytearray | None = None


class CallFrame:
    """
    R1 (`stack_bot` / execution_context): the combined operand-value +
        control-frame region for one function activation, including the embedded
        runtime environment (memory, globals, tables, etc.), decoded instruction
        table, and raw code.
    """

    __slots__ = ("code", "env", "frames", "instr_table", "instrs", "values")

    def __init__(
        self,
        instrs: Sequence[tuple[str, object]],
        code: bytes,
        values: list[int],
        env: ExecEnv | None = None,
    ):
        self.values = values
        self.frames: list[_Frame] = []
        self.instr_table = instrs
        self.instrs = instrs
        self.code = code
        self.env = env


# A handler's continuation: (next_ip, stack_bot, local_base, tos), or None
# to end this call (RETURN, or branching past the outermost implicit block).
_Cont = "tuple[int, CallFrame, list[int], int] | None"

# Fixed 256-slot direct-indexed dispatch table for WASM byte opcodes (0x00..0xFF)
_HANDLERS: list[Callable[[int, CallFrame, list[int], int], object] | None] = [None] * 256


def _handler(opcode: int):
    def register(fn):
        def wrapper(ip: int, frame: CallFrame, local_base: list[int], tos: int):
            env = frame.env
            res = fn(ip, frame, env, local_base)
            if res is None:
                return None
            next_ip, r_frame, _, r_locals = res
            r_tos = r_frame.values[-1] if r_frame.values else 0
            return (next_ip, r_frame, r_locals, r_tos)

        _HANDLERS[opcode] = wrapper
        return fn

    return register


def _do_branch(depth: int, frame: CallFrame) -> int | None:
    """
    Shared by BR/BR_IF/BR_TABLE: unwind `depth` control frames and
        compute where execution resumes -- a loop resumes its body, a
        block/if resumes just past its matching END. Returns None if the
        branch unwinds past the outermost implicit function block (== return).
    """

    cframes = frame.frames
    while depth > 0:
        cframes.pop()
        depth -= 1

    if not cframes:
        return None
    target = cframes[-1]
    del frame.values[target.stack_height :]
    if target.kind == "loop":
        return target.start + 2  # resume at the loop body (past opcode+blocktype)
    cframes.pop()
    return target.match_end + 1


@dataclass
class InterpreterCall:
    """
    Resumable state for one in-progress `Interpreter.call()`, stepped by
    `Interpreter.step()`. Crosses the interpreter/runtime boundary as plain
    data -- never as a Python coroutine -- so the runtime decides on its own
    terms what happens between steps.

    `call_stack` holds every caller frame currently suspended on a WASM
    `call`/`call_indirect` that has not yet returned, as `(func_index,
    resume_cont)` pairs, deepest-caller-last. Entering or returning from a
    nested WASM call is always a mandatory `step()` boundary (regardless of
    `quantum`): the interpreter hands the callee's freshly-entered frame
    straight back to the runtime rather than running it itself, so a JIT
    trace cache gets exactly the same chance to intercept a nested call as
    it gets for the outermost one -- tiering never depends on call depth.
    """

    func_index: int
    cont: _Cont
    call_stack: list[tuple[int, _Cont]] = field(default_factory=list)
    finished: bool = False
    results: list[int] | None = None

    def current_pc(self) -> int | None:
        """
        This call's unified `(func_index, ip)` address, or `None` if it has
        already finished, or is about to (fallen off the end of the code --
        the next `step()` will notice and finish it). A runtime driving a
        tiered JIT cache checks this between steps; the interpreter itself
        never looks at it.
        """
        if self.cont is None:
            return None
        ip, frame, _, _ = self.cont
        if ip >= len(frame.code):
            return None
        return (self.func_index << 16) | ip


class Interpreter:
    def __init__(
        self,
        module: Module,
        memory: bytearray | None = None,
        host_functions: list[Callable[..., int | None] | None] | None = None,
        vmmio: object | None = None,
        task_id: int = 1,
        phys_mem: bytearray | None = None,
    ):
        self.module = module
        self.memory = memory
        if self.memory is not None:
            self.module.init_memory_data(self.memory)

        self.host_functions = (
            host_functions if host_functions is not None else [None] * len(module.imports)
        )
        self.globals: list[int] = [g.init_value for g in module.globals]
        self.tables: list[list[int | None]] = [
            module.table_contents(i) for i in range(len(module.tables))
        ]
        self.debugger: object | None = None
        self.vmmio = vmmio
        self.task_id = task_id
        self.phys_mem = phys_mem
        self._env = ExecEnv(
            module,
            memory,
            self.globals,
            self.tables,
            self.host_functions,
            vmmio=vmmio,
            task_id=task_id,
            phys_mem=phys_mem,
        )
        if self.module.start_function is not None:
            self.call(self.module.start_function, [])

    def attach_debugger(self, debugger: object) -> None:
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

    def flush_jit_cache(self) -> None:
        """
        No-op: a bare `Interpreter` never owns a JIT cache -- only a
        `RuntimeEngine` wrapping one does. Exists so `DebuggerManager` can
        treat an `Interpreter` and an `IntegratedHybridEngine` uniformly.
        """

    def call(self, func_index: int, args: list[int]) -> list[int]:
        """Runs a function to completion in one step (no cooperative slicing)."""
        call_state = self.start(func_index, args)
        while not call_state.finished:
            call_state = self.step(call_state, quantum=0)
        return call_state.results

    def start(self, func_index: int, args: list[int]) -> InterpreterCall:
        """
        Sets up a resumable call, to be driven by repeated `step()` calls.
                A host import has nothing to step through: it resolves synchronously
                right here, so the returned call is already finished.
        """
        if self.module.is_import(func_index):
            results = self._call_import(func_index, args)
            return InterpreterCall(func_index, cont=None, finished=True, results=results)

        frame, locals_arr = self._build_frame(func_index, args)
        tos = frame.values[-1] if frame.values else 0
        return InterpreterCall(func_index, cont=(0, frame, locals_arr, tos))

    def _call_import(self, func_index: int, args: list[int]) -> list[int]:
        """Resolves a host import synchronously -- there is no bytecode to step through."""
        handler = self.host_functions[func_index] if func_index < len(self.host_functions) else None
        if handler is None:
            imp = self.module.imports[func_index]
            raise NotImplementedError(
                f"no host handler registered for import {imp.module}.{imp.name}"
            )
        result = handler(*[_to_i32(a) for a in args])
        ft = self.module.func_type(func_index)
        return [_to_i32(result)] if ft.results else []

    def _build_frame(self, func_index: int, args: list[int]) -> tuple[CallFrame, list[int]]:
        """Builds the initial frame + locals for a WASM (non-import) function activation."""
        fn = self.module.functions[func_index - len(self.module.imports)]
        layout = self.module.locals_layout(func_index)
        locals_arr = [0] * len(layout)
        for i, a in enumerate(args):
            locals_arr[i] = a if layout[i] in (F32, F64) else _to_i32(a)

        instrs = decode_all(fn.code)
        frame = CallFrame(instrs, fn.code, [], env=self._env)
        return frame, locals_arr

    def step(self, call_state: InterpreterCall, quantum: int = 64) -> InterpreterCall:
        """
        Executes up to `quantum` boundary instructions (Fuel/Quantum; 0 runs
                to completion in one call) and returns `call_state`, mutated in place:
                still `finished == False` with a resumable `.cont` if the quantum ran
                out, or `finished == True` with `.results` set once the outermost call
                actually returns. Entering or returning from a nested WASM call is
                always an immediate, mandatory return regardless of `quantum` -- see
                `InterpreterCall.call_stack`. The interpreter only ever executes-and-
                returns here -- it has no notion of a JIT cache or a scheduler at all;
                deciding what happens between non-finished returns (checking a JIT
                trace cache, a cooperative yield, draining a compile queue, or
                nothing at all) is entirely the runtime's job.
        """
        instr_step = 0
        while True:
            ip, frame, locals_arr, tos = call_state.cont
            if ip < len(frame.code):
                ins = frame.instrs[ip]
                if ins.opcode in (CALL, CALL_INDIRECT):
                    return self._enter_or_resolve_call(call_state, ins, frame, locals_arr, tos)
                is_boundary = ins.opcode in (
                    BLOCK,
                    LOOP,
                    IF,
                    ELSE,
                    END,
                    BR,
                    BR_IF,
                    BR_TABLE,
                    RETURN,
                )
                handler = _HANDLERS[ins.opcode]
                if handler is None:
                    raise NotImplementedError(f"interpreter: unhandled opcode 0x{ins.opcode:02X}")
                call_state.cont = handler(ip, frame, locals_arr, tos)
                if call_state.cont is not None:
                    if is_boundary:
                        instr_step += 1
                        if quantum > 0 and instr_step % quantum == 0:
                            return call_state
                    continue
                # cont is None: this frame just ended (RETURN, or a branch
                # past the outermost block) -- always handle it immediately
                # below, never defer it behind a quantum-exhaustion return.

            ft = self.module.func_type(call_state.func_index)
            results = [frame.values.pop()] if ft.results else []
            if not call_state.call_stack:
                call_state.cont = None
                call_state.finished = True
                call_state.results = results
                return call_state
            # Return to the suspended caller frame -- always a mandatory
            # boundary, so the runtime gets the same chance to check its JIT
            # trace cache here as it does entering any other frame.
            parent_func_index, parent_cont = call_state.call_stack.pop()
            _, parent_frame, _, _ = parent_cont
            parent_frame.values.extend(results)
            p_ip, p_frame, p_locals, _ = parent_cont
            p_tos = p_frame.values[-1] if p_frame.values else 0
            call_state.func_index = parent_func_index
            call_state.cont = (p_ip, p_frame, p_locals, p_tos)
            return call_state

    def _enter_or_resolve_call(
        self,
        call_state: InterpreterCall,
        ins: Instr,
        frame: CallFrame,
        locals_arr: list[int],
        tos: int,
    ) -> InterpreterCall:
        """
        Resolves the callee of a `call`/`call_indirect` and either invokes a
        host import synchronously (there is nothing to step through) or
        pushes this frame onto `call_state.call_stack` and hands control to
        the callee's freshly-built entry frame -- a mandatory boundary
        return, since the runtime must get a chance to check its JIT trace
        cache for the callee before this interpreter runs a single one of
        its instructions.
        """
        if ins.opcode == CALL:
            callee_func_index = ins.operand
            callee_ft = self.module.func_type(callee_func_index)
        else:
            table = frame.env.tables[ins.table_index]
            table_slot = _to_u32(frame.values.pop())
            if table_slot >= len(table):
                raise Trap(
                    f"call_indirect: table index {table_slot} out of bounds (size {len(table)})"
                )
            callee_func_index = table[table_slot]
            if callee_func_index is None:
                raise Trap(f"call_indirect: table slot {table_slot} is uninitialized")
            declared_type = self.module.types[ins.operand]
            actual_type = self.module.func_type(callee_func_index)
            if declared_type != actual_type:
                raise Trap(
                    f"call_indirect: type mismatch (declared {declared_type}, "
                    f"actual {actual_type} at table slot {table_slot})"
                )
            callee_ft = declared_type

        call_args = [frame.values.pop() for _ in range(len(callee_ft.params))][::-1]
        resume_tos = frame.values[-1] if frame.values else 0
        resume_cont = (ins.end_offset, frame, locals_arr, resume_tos)

        if self.module.is_import(callee_func_index):
            results = self._call_import(callee_func_index, call_args)
            frame.values.extend(results)
            r_tos = frame.values[-1] if frame.values else 0
            call_state.cont = (ins.end_offset, frame, locals_arr, r_tos)
            return call_state

        callee_frame, callee_locals = self._build_frame(callee_func_index, call_args)
        call_state.call_stack.append((call_state.func_index, resume_cont))
        call_state.func_index = callee_func_index
        callee_tos = callee_frame.values[-1] if callee_frame.values else 0
        call_state.cont = (0, callee_frame, callee_locals, callee_tos)
        return call_state


# ---------------------------------------------------------------------------
# Per-opcode CPS handlers. Each receives exactly (ip, stack_bot, env,
# local_base) and returns the next continuation itself.
# ---------------------------------------------------------------------------


@_handler(UNREACHABLE)
def _h_unreachable(ip, frame, env, local_base):
    raise Trap("unreachable instruction executed")


@_handler(NOP)
def _h_nop(ip, frame, env, local_base):
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(BLOCK)
def _h_block(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    frame.frames.append(_Frame("block", ins.offset, ins.match_offset, len(frame.values)))
    return (ins.end_offset, frame, env, local_base)


@_handler(LOOP)
def _h_loop(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    frame.frames.append(_Frame("loop", ins.offset, ins.match_offset, len(frame.values)))
    return (ins.end_offset, frame, env, local_base)


@_handler(IF)
def _h_if(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    cond = frame.values.pop()
    if cond == 0:
        if ins.else_offset is not None:
            frame.frames.append(_Frame("if", ins.offset, ins.match_offset, len(frame.values)))
            return (ins.else_offset + 1, frame, env, local_base)
        else:
            # Skip past matching END without leaving an unpopped frame
            return (ins.match_offset + 1, frame, env, local_base)
    frame.frames.append(_Frame("if", ins.offset, ins.match_offset, len(frame.values)))
    return (ins.end_offset, frame, env, local_base)


@_handler(ELSE)
def _h_else(ip, frame, env, local_base):
    # Reached only by falling out of a taken `if` branch's body: the branch
    # completed normally, so pop the if frame and skip past the matching END.
    if frame.frames:
        target = frame.frames.pop()
        return (target.match_end + 1, frame, env, local_base)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(END)
def _h_end(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    if frame.frames:
        frame.frames.pop()
    return (ins.end_offset, frame, env, local_base)


@_handler(BR)
def _h_br(ip, frame, env, local_base):
    next_ip = _do_branch(frame.instrs[ip].operand, frame)
    return None if next_ip is None else (next_ip, frame, env, local_base)


@_handler(BR_IF)
def _h_br_if(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    cond = frame.values.pop()
    if cond != 0:
        next_ip = _do_branch(ins.operand, frame)
        return None if next_ip is None else (next_ip, frame, env, local_base)
    return (ins.end_offset, frame, env, local_base)


@_handler(BR_TABLE)
def _h_br_table(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    index = _to_u32(frame.values.pop())
    depth = ins.br_table_labels[index] if index < len(ins.br_table_labels) else ins.operand
    next_ip = _do_branch(depth, frame)
    return None if next_ip is None else (next_ip, frame, env, local_base)


@_handler(RETURN)
def _h_return(ip, frame, env, local_base):
    return None


# CALL and CALL_INDIRECT are not in `_HANDLERS`: entering or returning from a
# nested WASM call is always a mandatory `step()` boundary (see
# `InterpreterCall.call_stack`), so `Interpreter.step()` intercepts both
# opcodes itself, before the generic dispatch table lookup, via
# `_enter_or_resolve_call()`.


@_handler(DROP)
def _h_drop(ip, frame, env, local_base):
    frame.values.pop()
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(SELECT)
def _h_select(ip, frame, env, local_base):
    c = frame.values.pop()
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(a if c != 0 else b)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(LOCAL_GET)
def _h_local_get(ip, frame, env, local_base):
    frame.values.append(local_base[frame.instrs[ip].operand])
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(LOCAL_SET)
def _h_local_set(ip, frame, env, local_base):
    local_base[frame.instrs[ip].operand] = frame.values.pop()
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(LOCAL_TEE)
def _h_local_tee(ip, frame, env, local_base):
    local_base[frame.instrs[ip].operand] = frame.values[-1]
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_CONST)
def _h_i32_const(ip, frame, env, local_base):
    frame.values.append(_to_i32(frame.instrs[ip].const_value))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(GLOBAL_GET)
def _h_global_get(ip, frame, env, local_base):
    frame.values.append(env.globals[frame.instrs[ip].operand])
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(GLOBAL_SET)
def _h_global_set(ip, frame, env, local_base):
    env.globals[frame.instrs[ip].operand] = _to_i32(frame.values.pop())
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- Loads (Dedicated per-opcode handlers with Bit 31 RAM Bypass) ---


def _vmmio_load(env: ExecEnv, addr: int, width: int, signed: bool) -> int:
    if env.vmmio is None:
        raise Trap(f"memory access out of bounds at addr={addr:#x} (no vMMIO configured)")
    status, detail = env.vmmio.access(addr, is_write=False, current_task_id=env.task_id)
    if status.startswith("TRAP_"):
        raise Trap(f"vMMIO load trap: {status} ({detail}) at addr={addr:#x}")
    if status == "OK_PHYSICAL" and env.phys_mem is not None:
        try:
            phys_offset = int(detail.split()[-1], 16)
            if phys_offset + width <= len(env.phys_mem):
                return int.from_bytes(
                    env.phys_mem[phys_offset : phys_offset + width], "little", signed=signed
                )
        except (ValueError, IndexError):
            pass
    return 0


def _vmmio_store(env: ExecEnv, addr: int, val_bytes: bytes) -> None:
    if env.vmmio is None:
        raise Trap(f"memory access out of bounds at addr={addr:#x} (no vMMIO configured)")
    status, detail = env.vmmio.access(addr, is_write=True, current_task_id=env.task_id)
    if status.startswith("TRAP_"):
        raise Trap(f"vMMIO store trap: {status} ({detail}) at addr={addr:#x}")
    if status == "OK_PHYSICAL" and env.phys_mem is not None:
        try:
            phys_offset = int(detail.split()[-1], 16)
            if phys_offset + len(val_bytes) <= len(env.phys_mem):
                env.phys_mem[phys_offset : phys_offset + len(val_bytes)] = val_bytes
        except (ValueError, IndexError):
            pass


@_handler(I32_LOAD)
def _h_i32_load(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if addr & 0x8000_0000:
        frame.values.append(_vmmio_load(env, addr, 4, signed=True))
    else:
        if env.memory is None or addr + 4 > len(env.memory):
            raise Trap(f"i32.load out of bounds at addr={addr}")
        frame.values.append(int.from_bytes(env.memory[addr : addr + 4], "little", signed=True))
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD8_S)
def _h_i32_load8_s(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if addr & 0x8000_0000:
        frame.values.append(_vmmio_load(env, addr, 1, signed=True))
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            raise Trap(f"i32.load8_s out of bounds at addr={addr}")
        frame.values.append(int.from_bytes(env.memory[addr : addr + 1], "little", signed=True))
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD8_U)
def _h_i32_load8_u(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if addr & 0x8000_0000:
        frame.values.append(_vmmio_load(env, addr, 1, signed=False))
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            raise Trap(f"i32.load8_u out of bounds at addr={addr}")
        frame.values.append(env.memory[addr])
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD16_S)
def _h_i32_load16_s(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if addr & 0x8000_0000:
        frame.values.append(_vmmio_load(env, addr, 2, signed=True))
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            raise Trap(f"i32.load16_s out of bounds at addr={addr}")
        frame.values.append(int.from_bytes(env.memory[addr : addr + 2], "little", signed=True))
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD16_U)
def _h_i32_load16_u(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if addr & 0x8000_0000:
        frame.values.append(_vmmio_load(env, addr, 2, signed=False))
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            raise Trap(f"i32.load16_u out of bounds at addr={addr}")
        frame.values.append(int.from_bytes(env.memory[addr : addr + 2], "little", signed=False))
    return (ins.end_offset, frame, env, local_base)


# --- Stores (Dedicated per-opcode handlers with Bit 31 RAM Bypass) ---


@_handler(I32_STORE)
def _h_i32_store(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    value = _to_i32(frame.values.pop())
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    raw_val = (value & 0xFFFFFFFF).to_bytes(4, "little")
    if addr & 0x8000_0000:
        _vmmio_store(env, addr, raw_val)
    else:
        if env.memory is None or addr + 4 > len(env.memory):
            raise Trap(f"i32.store out of bounds at addr={addr}")
        env.memory[addr : addr + 4] = raw_val
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_STORE8)
def _h_i32_store8(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    value = frame.values.pop() & 0xFF
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    raw_val = bytes([value])
    if addr & 0x8000_0000:
        _vmmio_store(env, addr, raw_val)
    else:
        if env.memory is None or addr + 1 > len(env.memory):
            raise Trap(f"i32.store8 out of bounds at addr={addr}")
        env.memory[addr] = value
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_STORE16)
def _h_i32_store16(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    value = frame.values.pop() & 0xFFFF
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    raw_val = value.to_bytes(2, "little")
    if addr & 0x8000_0000:
        _vmmio_store(env, addr, raw_val)
    else:
        if env.memory is None or addr + 2 > len(env.memory):
            raise Trap(f"i32.store16 out of bounds at addr={addr}")
        env.memory[addr : addr + 2] = raw_val
    return (ins.end_offset, frame, env, local_base)


# --- Memory Size / Grow ---


@_handler(MEMORY_SIZE)
def _h_memory_size(ip, frame, env, local_base):
    if env.memory is None:
        raise Trap("memory.size with no memory section")
    frame.values.append(len(env.memory) // PAGE_SIZE)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(MEMORY_GROW)
def _h_memory_grow(ip, frame, env, local_base):
    delta_pages = _to_u32(frame.values.pop())
    if env.memory is None:
        frame.values.append(_to_i32(0xFFFFFFFF))
    else:
        old_pages = len(env.memory) // PAGE_SIZE
        env.memory.extend(bytes(delta_pages * PAGE_SIZE))
        frame.values.append(old_pages)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- Comparisons (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_EQZ)
def _h_i32_eqz(ip, frame, env, local_base):
    frame.values.append(1 if frame.values.pop() == 0 else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_EQ)
def _h_i32_eq(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_i32(a) == _to_i32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_NE)
def _h_i32_ne(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_i32(a) != _to_i32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_LT_S)
def _h_i32_lt_s(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_i32(a) < _to_i32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_LT_U)
def _h_i32_lt_u(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_u32(a) < _to_u32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_GT_S)
def _h_i32_gt_s(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_i32(a) > _to_i32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_GT_U)
def _h_i32_gt_u(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_u32(a) > _to_u32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_LE_S)
def _h_i32_le_s(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_i32(a) <= _to_i32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_LE_U)
def _h_i32_le_u(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_u32(a) <= _to_u32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_GE_S)
def _h_i32_ge_s(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_i32(a) >= _to_i32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_GE_U)
def _h_i32_ge_u(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(1 if _to_u32(a) >= _to_u32(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- Unary Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_CLZ)
def _h_i32_clz(ip, frame, env, local_base):
    v = _to_u32(frame.values.pop())
    frame.values.append(32 if v == 0 else 32 - v.bit_length())
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_CTZ)
def _h_i32_ctz(ip, frame, env, local_base):
    v = _to_u32(frame.values.pop())
    if v == 0:
        res = 32
    else:
        n = 0
        while (v & 1) == 0:
            v >>= 1
            n += 1

        res = n

    frame.values.append(res)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_POPCNT)
def _h_i32_popcnt(ip, frame, env, local_base):
    v = _to_u32(frame.values.pop())
    frame.values.append(bin(v).count("1"))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- Binary Arithmetic & Bitwise Ops (Dedicated per-opcode handlers without if statements) ---


@_handler(I32_ADD)
def _h_i32_add(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(a + b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_SUB)
def _h_i32_sub(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(a - b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_MUL)
def _h_i32_mul(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(a * b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_DIV_S)
def _h_i32_div_s(ip, frame, env, local_base):
    b = _to_i32(frame.values.pop())
    a = _to_i32(frame.values.pop())
    if b == 0:
        raise Trap("integer divide by zero")
    if a == -2147483648 and b == -1:
        raise Trap("integer overflow")
    q = abs(a) // abs(b)
    frame.values.append(_to_i32(-q if (a < 0) != (b < 0) else q))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_DIV_U)
def _h_i32_div_u(ip, frame, env, local_base):
    b = _to_u32(frame.values.pop())
    a = _to_u32(frame.values.pop())
    if b == 0:
        raise Trap("integer divide by zero")
    frame.values.append(_to_i32(a // b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_REM_S)
def _h_i32_rem_s(ip, frame, env, local_base):
    b = _to_i32(frame.values.pop())
    a = _to_i32(frame.values.pop())
    if b == 0:
        raise Trap("integer divide by zero")
    r = abs(a) % abs(b)
    frame.values.append(_to_i32(-r if a < 0 else r))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_REM_U)
def _h_i32_rem_u(ip, frame, env, local_base):
    b = _to_u32(frame.values.pop())
    a = _to_u32(frame.values.pop())
    if b == 0:
        raise Trap("integer divide by zero")
    frame.values.append(_to_i32(a % b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_AND)
def _h_i32_and(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(_to_u32(a) & _to_u32(b)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_OR)
def _h_i32_or(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(_to_u32(a) | _to_u32(b)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_XOR)
def _h_i32_xor(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(_to_u32(a) ^ _to_u32(b)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_SHL)
def _h_i32_shl(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(_to_u32(a) << (_to_u32(b) & 31)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_SHR_S)
def _h_i32_shr_s(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(_to_i32(a) >> (_to_u32(b) & 31)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_SHR_U)
def _h_i32_shr_u(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    frame.values.append(_to_i32(_to_u32(a) >> (_to_u32(b) & 31)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_ROTL)
def _h_i32_rotl(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.append(_to_i32(((v << n) | (v >> (32 - n))) & I32_MASK if n else v))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_ROTR)
def _h_i32_rotr(ip, frame, env, local_base):
    b = frame.values.pop()
    a = frame.values.pop()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.append(_to_i32(((v >> n) | (v << (32 - n))) & I32_MASK if n else v))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


import math
import struct

# Helper conversion utilities
I64_MASK = 0xFFFF_FFFF_FFFF_FFFF


def _to_i64(v: int) -> int:
    v &= I64_MASK
    return v - (1 << 64) if v & 0x8000_0000_0000_0000 else v


def _to_u64(v: int) -> int:
    return v & I64_MASK


# --- i64 / f32 / f64 Memory Handlers ---


@_handler(I64_LOAD)
def _h_i64_load(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    _, offset = ins.memarg
    addr = _to_u32(frame.values.pop()) + offset
    if addr & 0x8000_0000:
        val = _vmmio_load(env, addr, 8, signed=True)
    else:
        if env.memory is None or addr + 8 > len(env.memory):
            raise Trap("out of bounds memory access")
        val = struct.unpack("<q", env.memory[addr : addr + 8])[0]
    frame.values.append(val)
    return (ins.end_offset, frame, env, local_base)


@_handler(I64_STORE)
def _h_i64_store(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    _, offset = ins.memarg
    val = frame.values.pop()
    addr = _to_u32(frame.values.pop()) + offset
    raw_val = struct.pack("<q", int(val))
    if addr & 0x8000_0000:
        _vmmio_store(env, addr, raw_val)
    else:
        if env.memory is None or addr + 8 > len(env.memory):
            raise Trap("out of bounds memory access")
        env.memory[addr : addr + 8] = raw_val
    return (ins.end_offset, frame, env, local_base)


@_handler(F32_LOAD)
def _h_f32_load(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    _, offset = ins.memarg
    addr = _to_u32(frame.values.pop()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        raise Trap("out of bounds memory access")
    val = struct.unpack("<f", env.memory[addr : addr + 4])[0]
    frame.values.append(val)
    return (ins.end_offset, frame, env, local_base)


@_handler(F32_STORE)
def _h_f32_store(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    _, offset = ins.memarg
    val = float(frame.values.pop())
    addr = _to_u32(frame.values.pop()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        raise Trap("out of bounds memory access")
    env.memory[addr : addr + 4] = struct.pack("<f", val)
    return (ins.end_offset, frame, env, local_base)


@_handler(F64_LOAD)
def _h_f64_load(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    _, offset = ins.memarg
    addr = _to_u32(frame.values.pop()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        raise Trap("out of bounds memory access")
    val = struct.unpack("<d", env.memory[addr : addr + 8])[0]
    frame.values.append(val)
    return (ins.end_offset, frame, env, local_base)


@_handler(F64_STORE)
def _h_f64_store(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    _, offset = ins.memarg
    val = float(frame.values.pop())
    addr = _to_u32(frame.values.pop()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        raise Trap("out of bounds memory access")
    env.memory[addr : addr + 8] = struct.pack("<d", val)
    return (ins.end_offset, frame, env, local_base)


# --- Const Handlers ---


@_handler(I64_CONST)
def _h_i64_const(ip, frame, env, local_base):
    frame.values.append(_to_i64(frame.instrs[ip].const_value))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_CONST)
def _h_f32_const(ip, frame, env, local_base):
    frame.values.append(float(frame.instrs[ip].const_value))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_CONST)
def _h_f64_const(ip, frame, env, local_base):
    frame.values.append(float(frame.instrs[ip].const_value))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- i64 Comparison & Arithmetic Handlers ---


@_handler(I64_EQZ)
def _h_i64_eqz(ip, frame, env, local_base):
    v = frame.values.pop()
    frame.values.append(1 if v == 0 else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_EQ)
def _h_i64_eq(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if _to_i64(a) == _to_i64(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_NE)
def _h_i64_ne(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if _to_i64(a) != _to_i64(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_LT_S)
def _h_i64_lt_s(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if _to_i64(a) < _to_i64(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_LT_U)
def _h_i64_lt_u(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if _to_u64(a) < _to_u64(b) else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_ADD)
def _h_i64_add(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(_to_i64(a + b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_SUB)
def _h_i64_sub(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(_to_i64(a - b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_MUL)
def _h_i64_mul(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(_to_i64(a * b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_DIV_S)
def _h_i64_div_s(ip, frame, env, local_base):
    b = _to_i64(frame.values.pop())
    a = _to_i64(frame.values.pop())
    if b == 0:
        raise Trap("integer divide by zero")
    if a == -0x8000_0000_0000_0000 and b == -1:
        raise Trap("integer overflow")
    q = abs(a) // abs(b)
    frame.values.append(_to_i64(-q if (a < 0) != (b < 0) else q))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I64_DIV_U)
def _h_i64_div_u(ip, frame, env, local_base):
    b = _to_u64(frame.values.pop())
    a = _to_u64(frame.values.pop())
    if b == 0:
        raise Trap("integer divide by zero")
    frame.values.append(_to_i64(a // b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- f32 Arithmetic Handlers ---


@_handler(F32_ADD)
def _h_f32_add(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a + b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_SUB)
def _h_f32_sub(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a - b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_MUL)
def _h_f32_mul(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a * b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_DIV)
def _h_f32_div(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a / b if b != 0 else float("inf")))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_SQRT)
def _h_f32_sqrt(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(math.sqrt(a) if a >= 0 else float("nan")))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_MIN)
def _h_f32_min(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(min(a, b)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_MAX)
def _h_f32_max(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(max(a, b)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_LT)
def _h_f32_lt(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a < b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_LE)
def _h_f32_le(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a <= b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_GT)
def _h_f32_gt(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a > b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_GE)
def _h_f32_ge(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a >= b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- f64 Arithmetic Handlers ---


@_handler(F64_ADD)
def _h_f64_add(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a + b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_SUB)
def _h_f64_sub(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a - b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_MUL)
def _h_f64_mul(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a * b))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_DIV)
def _h_f64_div(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(float(a / b if b != 0 else float("inf")))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_SQRT)
def _h_f64_sqrt(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(math.sqrt(a) if a >= 0 else float("nan")))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


# --- Conversion Handlers ---


@_handler(I32_TRUNC_F32_S)
def _h_i32_trunc_f32_s(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(int(a) & I32_MASK)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(I32_TRUNC_F64_S)
def _h_i32_trunc_f64_s(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(int(a) & I32_MASK)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_CONVERT_I32_S)
def _h_f32_convert_i32_s(ip, frame, env, local_base):
    a = _to_i32(frame.values.pop())
    frame.values.append(float(a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_CONVERT_I32_U)
def _h_f32_convert_i32_u(ip, frame, env, local_base):
    a = _to_u32(frame.values.pop())
    frame.values.append(float(a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_CONVERT_I32_S)
def _h_f64_convert_i32_s(ip, frame, env, local_base):
    a = _to_i32(frame.values.pop())
    frame.values.append(float(a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_CONVERT_I32_U)
def _h_f64_convert_i32_u(ip, frame, env, local_base):
    a = _to_u32(frame.values.pop())
    frame.values.append(float(a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_PROMOTE_F32)
def _h_f64_promote_f32(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_DEMOTE_F64)
def _h_f32_demote_f64(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_ABS)
def _h_f32_abs(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(abs(a)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_NEG)
def _h_f32_neg(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(-a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_ABS)
def _h_f64_abs(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(abs(a)))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_NEG)
def _h_f64_neg(ip, frame, env, local_base):
    a = frame.values.pop()
    frame.values.append(float(-a))
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_EQ)
def _h_f32_eq(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a == b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F32_NE)
def _h_f32_ne(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a != b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_EQ)
def _h_f64_eq(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a == b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


@_handler(F64_NE)
def _h_f64_ne(ip, frame, env, local_base):
    b, a = frame.values.pop(), frame.values.pop()
    frame.values.append(1 if a != b else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)

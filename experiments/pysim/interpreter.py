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

from dataclasses import dataclass
from typing import Callable

from control_flow import Instr, decode_all
from wasm_module import Module
from wasm_opcodes import (
    BLOCK, BR, BR_IF, BR_TABLE, CALL, CALL_INDIRECT, DROP, ELSE, END, GLOBAL_GET,
    GLOBAL_SET, I32_ADD, I32_AND, I32_CLZ, I32_CONST, I32_CTZ, I32_DIV_S, I32_DIV_U,
    I32_EQ, I32_EQZ, I32_GE_S, I32_GE_U, I32_GT_S, I32_GT_U, I32_LE_S, I32_LE_U,
    I32_LOAD, I32_LOAD8_S, I32_LOAD8_U, I32_LOAD16_S, I32_LOAD16_U, I32_LT_S, I32_LT_U,
    I32_MUL, I32_NE, I32_OR, I32_POPCNT, I32_REM_S, I32_REM_U, I32_ROTL, I32_ROTR,
    I32_SHL, I32_SHR_S, I32_SHR_U, I32_STORE, I32_STORE8, I32_STORE16, I32_SUB, I32_XOR,
    IF, LOCAL_GET, LOCAL_SET, LOCAL_TEE, LOOP, MEMORY_GROW, MEMORY_SIZE, NOP, RETURN,
    SELECT, UNREACHABLE,
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
    kind: str          # "block" | "loop" | "if"
    start: int          # opcode offset of the BLOCK/LOOP/IF
    match_end: int       # offset of the matching END
    stack_height: int    # operand-stack height at frame entry


@dataclass
class ExecEnv:
    """R2 (`env`): state shared across every call in this run -- the
    module, linear memory, globals, tables and host-import dispatch table.
    Never mutated per-instruction-dispatch, only by the opcodes that are
    specified to mutate it (global.set, stores, memory.grow)."""
    module: Module
    memory: bytearray | None
    globals: list[int]
    tables: list[list[int | None]]
    host_functions: dict[int, Callable[..., int | None]]
    interp: "Interpreter"


class CallFrame:
    """R1 (`stack_bot`): the combined operand-value + control-frame region
    for one function activation, plus that activation's own decoded
    instruction table and raw code (needed to fetch the instruction at a
    given `ip`)."""
    __slots__ = ("values", "frames", "instrs", "code")

    def __init__(self, instrs: dict[int, Instr], code: bytes, values: list[int]):
        self.values = values
        self.frames: list[_Frame] = []
        self.instrs = instrs
        self.code = code


# A handler's continuation: (next_ip, stack_bot, env, local_base), or None
# to end this call (RETURN, or branching past the outermost implicit block).
_Cont = "tuple[int, CallFrame, ExecEnv, list[int]] | None"

# Fixed 256-slot direct-indexed dispatch table for WASM byte opcodes (0x00..0xFF)
_HANDLERS: list[Callable[[int, CallFrame, ExecEnv, list[int]], object] | None] = [None] * 256


def _handler(opcode: int):
    def register(fn):
        _HANDLERS[opcode] = fn
        return fn
    return register


def _do_branch(depth: int, frame: CallFrame) -> int | None:
    """Shared by BR/BR_IF/BR_TABLE: unwind `depth` control frames and
    compute where execution resumes -- a loop resumes its body, a
    block/if resumes just past its matching END. Returns None if the
    branch unwinds past the outermost implicit function block (== return)."""
    cframes = frame.frames
    while depth > 0:
        cframes.pop()
        depth -= 1
    if not cframes:
        return None
    target = cframes[-1]
    del frame.values[target.stack_height:]
    if target.kind == "loop":
        return target.start + 2   # resume at the loop body (past opcode+blocktype)
    cframes.pop()
    return target.match_end + 1


class Interpreter:
    def __init__(self, module: Module, memory: bytearray | None = None,
                 host_functions: dict[int, Callable[..., int | None]] | None = None):
        self.module = module
        self.memory = memory
        if self.memory is not None:
            self.module.init_memory_data(self.memory)
        self.host_functions = host_functions or {}
        self.globals: list[int] = [g.init_value for g in module.globals]
        self.tables: list[list[int | None]] = [
            module.table_contents(i) for i in range(len(module.tables))
        ]
        self._env = ExecEnv(module, memory, self.globals, self.tables, self.host_functions, self)
        if self.module.start_function is not None:
            self.call(self.module.start_function, [])

    def call(self, func_index: int, args: list[int]) -> list[int]:
        gen = self.call_coroutine(func_index, args, yield_every=0)
        try:
            while True:
                next(gen)
        except StopIteration as e:
            return e.value or []

    def call_coroutine(self, func_index: int, args: list[int], yield_every: int = 64):
        """Executes a WASM function cooperatively as a Python generator.
        Yields every `yield_every` executed instructions (Fuel/Quantum),
        enabling Hoare CSP green-thread multitasking on COOS without starving other tasks."""
        if self.module.is_import(func_index):
            handler = self.host_functions.get(func_index)
            if handler is None:
                imp = self.module.imports[func_index]
                raise NotImplementedError(f"no host handler registered for import {imp.module}.{imp.name}")
            result = handler(*[_to_i32(a) for a in args])
            ft = self.module.func_type(func_index)
            return [_to_i32(result)] if ft.results else []

        fn = self.module.functions[func_index - len(self.module.imports)]
        layout = self.module.locals_layout(func_index)
        locals_arr = [0] * len(layout)
        for i, a in enumerate(args):
            locals_arr[i] = _to_i32(a)

        instrs = decode_all(fn.code)
        frame = CallFrame(instrs, fn.code, [])

        cont = (0, frame, self._env, locals_arr)
        instr_step = 0
        while cont is not None:
            ip, frame, env, locals_arr = cont
            if ip >= len(frame.code):
                break
            ins = frame.instrs[ip]
            handler = _HANDLERS[ins.opcode]
            if handler is None:
                raise NotImplementedError(f"interpreter: unhandled opcode 0x{ins.opcode:02X}")
            cont = handler(ip, frame, env, locals_arr)
            instr_step += 1
            if yield_every > 0 and (instr_step % yield_every == 0):
                yield  # Cooperative yield to COOS scheduler

        ft = self.module.func_type(func_index)
        if ft.results:
            return [frame.values.pop()]
        return []


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
    frame.frames.append(_Frame("if", ins.offset, ins.match_offset, len(frame.values)))
    if cond == 0:
        next_ip = ins.else_offset + 1 if ins.else_offset is not None else ins.match_offset + 1
        return (next_ip, frame, env, local_base)
    return (ins.end_offset, frame, env, local_base)


@_handler(ELSE)
def _h_else(ip, frame, env, local_base):
    # Reached only by falling out of a taken `if` branch's body: the branch
    # completed normally, so skip the else-body entirely.
    target = frame.frames[-1]
    return (target.match_end + 1, frame, env, local_base)


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


@_handler(CALL)
def _h_call(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    callee_ft = env.module.func_type(ins.operand)
    call_args = [frame.values.pop() for _ in range(len(callee_ft.params))][::-1]
    results = env.interp.call(ins.operand, call_args)
    frame.values.extend(results)
    return (ins.end_offset, frame, env, local_base)


@_handler(CALL_INDIRECT)
def _h_call_indirect(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    table = env.tables[ins.table_index]
    table_slot = _to_u32(frame.values.pop())
    if table_slot >= len(table):
        raise Trap(f"call_indirect: table index {table_slot} out of bounds (size {len(table)})")
    func_index = table[table_slot]
    if func_index is None:
        raise Trap(f"call_indirect: table slot {table_slot} is uninitialized")
    declared_type = env.module.types[ins.operand]
    actual_type = env.module.func_type(func_index)
    if declared_type != actual_type:
        raise Trap(
            f"call_indirect: type mismatch (declared {declared_type}, "
            f"actual {actual_type} at table slot {table_slot})"
        )
    call_args = [frame.values.pop() for _ in range(len(declared_type.params))][::-1]
    results = env.interp.call(func_index, call_args)
    frame.values.extend(results)
    return (ins.end_offset, frame, env, local_base)


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


# --- Loads (Dedicated per-opcode handlers without if statements) ---

@_handler(I32_LOAD)
def _h_i32_load(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 4 > len(env.memory):
        raise Trap(f"i32.load out of bounds at addr={addr}")
    frame.values.append(int.from_bytes(env.memory[addr:addr + 4], "little", signed=True))
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD8_S)
def _h_i32_load8_s(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 1 > len(env.memory):
        raise Trap(f"i32.load8_s out of bounds at addr={addr}")
    frame.values.append(int.from_bytes(env.memory[addr:addr + 1], "little", signed=True))
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD8_U)
def _h_i32_load8_u(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 1 > len(env.memory):
        raise Trap(f"i32.load8_u out of bounds at addr={addr}")
    frame.values.append(env.memory[addr])
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD16_S)
def _h_i32_load16_s(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 2 > len(env.memory):
        raise Trap(f"i32.load16_s out of bounds at addr={addr}")
    frame.values.append(int.from_bytes(env.memory[addr:addr + 2], "little", signed=True))
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_LOAD16_U)
def _h_i32_load16_u(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 2 > len(env.memory):
        raise Trap(f"i32.load16_u out of bounds at addr={addr}")
    frame.values.append(int.from_bytes(env.memory[addr:addr + 2], "little", signed=False))
    return (ins.end_offset, frame, env, local_base)


# --- Stores (Dedicated per-opcode handlers without if statements) ---

@_handler(I32_STORE)
def _h_i32_store(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    value = _to_i32(frame.values.pop())
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 4 > len(env.memory):
        raise Trap(f"i32.store out of bounds at addr={addr}")
    env.memory[addr:addr + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_STORE8)
def _h_i32_store8(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    value = frame.values.pop() & 0xFF
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 1 > len(env.memory):
        raise Trap(f"i32.store8 out of bounds at addr={addr}")
    env.memory[addr] = value
    return (ins.end_offset, frame, env, local_base)


@_handler(I32_STORE16)
def _h_i32_store16(ip, frame, env, local_base):
    ins = frame.instrs[ip]
    value = frame.values.pop() & 0xFFFF
    addr = _to_u32(frame.values.pop()) + ins.memarg[1]
    if env.memory is None or addr + 2 > len(env.memory):
        raise Trap(f"i32.store16 out of bounds at addr={addr}")
    env.memory[addr:addr + 2] = value.to_bytes(2, "little")
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


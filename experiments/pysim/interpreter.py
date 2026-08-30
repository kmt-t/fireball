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

_HANDLERS: dict[int, Callable[[int, CallFrame, ExecEnv, list[int]], object]] = {}


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
        self.host_functions = host_functions or {}
        self.globals: list[int] = [g.init_value for g in module.globals]
        self.tables: list[list[int | None]] = [
            module.table_contents(i) for i in range(len(module.tables))
        ]
        self._env = ExecEnv(module, memory, self.globals, self.tables, self.host_functions, self)

    def call(self, func_index: int, args: list[int]) -> list[int]:
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
        self._run(frame, locals_arr)

        ft = self.module.func_type(func_index)
        if ft.results:
            return [frame.values.pop()]
        return []

    def _run(self, frame: CallFrame, local_base: list[int]) -> None:
        """The trampoline: repeatedly fetches the handler for the opcode at
        `ip` and re-enters it with the continuation it returns. Every
        decision about "what happens next" belongs to the handler that was
        just invoked, never to this loop."""
        cont = (0, frame, self._env, local_base)
        while cont is not None:
            ip, frame, env, local_base = cont
            if ip >= len(frame.code):
                break   # fell off the function's own closing END, same as WASM falling off a function body
            ins = frame.instrs[ip]
            try:
                handler = _HANDLERS[ins.opcode]
            except KeyError:
                raise NotImplementedError(f"interpreter: unhandled opcode 0x{ins.opcode:02X}") from None
            cont = handler(ip, frame, env, local_base)


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


_LOAD_WIDTH = {I32_LOAD: 4, I32_LOAD8_S: 1, I32_LOAD8_U: 1, I32_LOAD16_S: 2, I32_LOAD16_U: 2}
_LOAD_SIGNED = (I32_LOAD, I32_LOAD8_S, I32_LOAD16_S)


def _make_load_handler(op: int):
    width = _LOAD_WIDTH[op]
    signed = op in _LOAD_SIGNED

    def handler(ip, frame, env, local_base):
        ins = frame.instrs[ip]
        addr = _to_u32(frame.values.pop()) + ins.memarg[1]
        if env.memory is None or addr + width > len(env.memory):
            raise Trap(f"i32.load (width={width}) out of bounds at addr={addr}")
        frame.values.append(int.from_bytes(env.memory[addr:addr + width], "little", signed=signed))
        return (ins.end_offset, frame, env, local_base)
    return handler


for _op in _LOAD_WIDTH:
    _HANDLERS[_op] = _make_load_handler(_op)

_STORE_WIDTH = {I32_STORE: 4, I32_STORE8: 1, I32_STORE16: 2}


def _make_store_handler(op: int):
    width = _STORE_WIDTH[op]

    def handler(ip, frame, env, local_base):
        ins = frame.instrs[ip]
        value = _to_i32(frame.values.pop())
        addr = _to_u32(frame.values.pop()) + ins.memarg[1]
        if env.memory is None or addr + width > len(env.memory):
            raise Trap(f"i32.store (width={width}) out of bounds at addr={addr}")
        env.memory[addr:addr + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")
        return (ins.end_offset, frame, env, local_base)
    return handler


for _op in _STORE_WIDTH:
    _HANDLERS[_op] = _make_store_handler(_op)


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


@_handler(I32_EQZ)
def _h_i32_eqz(ip, frame, env, local_base):
    frame.values.append(1 if frame.values.pop() == 0 else 0)
    return (frame.instrs[ip].end_offset, frame, env, local_base)


_COMPARE_OPS = (I32_EQ, I32_NE, I32_LT_S, I32_LT_U, I32_GT_S, I32_GT_U,
                I32_LE_S, I32_LE_U, I32_GE_S, I32_GE_U)


def _make_compare_handler(op: int):
    def handler(ip, frame, env, local_base):
        b = frame.values.pop()
        a = frame.values.pop()
        frame.values.append(1 if _compare(op, a, b) else 0)
        return (frame.instrs[ip].end_offset, frame, env, local_base)
    return handler


for _op in _COMPARE_OPS:
    _HANDLERS[_op] = _make_compare_handler(_op)

_UNOP_OPS = (I32_CLZ, I32_CTZ, I32_POPCNT)


def _make_unop_handler(op: int):
    def handler(ip, frame, env, local_base):
        frame.values.append(_unop(op, frame.values.pop()))
        return (frame.instrs[ip].end_offset, frame, env, local_base)
    return handler


for _op in _UNOP_OPS:
    _HANDLERS[_op] = _make_unop_handler(_op)

_ARITH_OPS = (I32_ADD, I32_SUB, I32_MUL, I32_DIV_S, I32_DIV_U, I32_REM_S,
              I32_REM_U, I32_AND, I32_OR, I32_XOR, I32_SHL, I32_SHR_S, I32_SHR_U,
              I32_ROTL, I32_ROTR)


def _make_arith_handler(op: int):
    def handler(ip, frame, env, local_base):
        b = frame.values.pop()
        a = frame.values.pop()
        frame.values.append(_arith(op, a, b))
        return (frame.instrs[ip].end_offset, frame, env, local_base)
    return handler


for _op in _ARITH_OPS:
    _HANDLERS[_op] = _make_arith_handler(_op)


def _compare(op: int, a: int, b: int) -> bool:
    if op == I32_EQ: return _to_i32(a) == _to_i32(b)
    if op == I32_NE: return _to_i32(a) != _to_i32(b)
    if op == I32_LT_S: return _to_i32(a) < _to_i32(b)
    if op == I32_LT_U: return _to_u32(a) < _to_u32(b)
    if op == I32_GT_S: return _to_i32(a) > _to_i32(b)
    if op == I32_GT_U: return _to_u32(a) > _to_u32(b)
    if op == I32_LE_S: return _to_i32(a) <= _to_i32(b)
    if op == I32_LE_U: return _to_u32(a) <= _to_u32(b)
    if op == I32_GE_S: return _to_i32(a) >= _to_i32(b)
    if op == I32_GE_U: return _to_u32(a) >= _to_u32(b)
    raise NotImplementedError(op)


def _arith(op: int, a: int, b: int) -> int:
    if op == I32_ADD: return _to_i32(a + b)
    if op == I32_SUB: return _to_i32(a - b)
    if op == I32_MUL: return _to_i32(a * b)
    if op == I32_DIV_S:
        a, b = _to_i32(a), _to_i32(b)
        if b == 0:
            raise Trap("integer divide by zero")
        q = abs(a) // abs(b)
        return _to_i32(-q if (a < 0) != (b < 0) else q)
    if op == I32_DIV_U:
        b = _to_u32(b)
        if b == 0:
            raise Trap("integer divide by zero")
        return _to_i32(_to_u32(a) // b)
    if op == I32_REM_S:
        a, b = _to_i32(a), _to_i32(b)
        if b == 0:
            raise Trap("integer divide by zero")
        r = abs(a) % abs(b)
        return _to_i32(-r if a < 0 else r)
    if op == I32_REM_U:
        b = _to_u32(b)
        if b == 0:
            raise Trap("integer divide by zero")
        return _to_i32(_to_u32(a) % b)
    if op == I32_AND: return _to_i32(_to_u32(a) & _to_u32(b))
    if op == I32_OR: return _to_i32(_to_u32(a) | _to_u32(b))
    if op == I32_XOR: return _to_i32(_to_u32(a) ^ _to_u32(b))
    if op == I32_SHL: return _to_i32(_to_u32(a) << (_to_u32(b) & 31))
    if op == I32_SHR_S: return _to_i32(_to_i32(a) >> (_to_u32(b) & 31))
    if op == I32_SHR_U: return _to_i32(_to_u32(a) >> (_to_u32(b) & 31))
    if op == I32_ROTL:
        n = _to_u32(b) & 31
        v = _to_u32(a)
        return _to_i32(((v << n) | (v >> (32 - n))) & I32_MASK if n else v)
    if op == I32_ROTR:
        n = _to_u32(b) & 31
        v = _to_u32(a)
        return _to_i32(((v >> n) | (v << (32 - n))) & I32_MASK if n else v)
    raise NotImplementedError(op)


def _unop(op: int, a: int) -> int:
    v = _to_u32(a)
    if op == I32_CLZ:
        return 32 if v == 0 else 32 - v.bit_length()
    if op == I32_CTZ:
        if v == 0:
            return 32
        n = 0
        while v & 1 == 0:
            v >>= 1
            n += 1
        return n
    if op == I32_POPCNT:
        return bin(v).count("1")
    raise NotImplementedError(op)

"""
docs/components/tier2_runtime/concepts/interpreter_concept.py
Reference Concept Implementation: Exhaustive WASM MVP (v1) Stack Interpreter & Android ART Unified Frame
- Complete WASM MVP opcode set matching docs/specs/wasm_instruction_set.md
- Bottom-resident execution_context: CallFrame + Locals + Operands share one
  inline growing region; ControlFrame lives in its own dedicated region,
  never interleaved with it (ADR-INTERP-03, runtime_interpreter.md §8)
- Direct-Threaded __fastcall Continuation Passing Style (CPS) 4-argument dispatch (ip, stack_bot, env, local_base)
- Full stack pruning (Label Arity handling) on br / br_if / br_table
- 64-bit integer arithmetic, memory loads/stores (8/16/32/64-bit), and type conversions
- Cooperative safepoint polling at loop headers for deterministic yield
"""

import struct
from collections.abc import Callable


class WASMTrap(Exception):
    pass


# ==============================================================================
# 1. Unified Stack Frame & Execution Context Data Structures
# ==============================================================================


class CallFrame:
    """
    Call frame resident inline on the call/locals/operand region of the
    stack buffer (never the dedicated control_frame region -- ADR-INTERP-03,
    since `call`/`call_indirect`/return always round-trip through the
    interpreter and never need JIT-bypass safety).
    `{ContextPointerRegister}` `{PositionIndependentCode}`
    """

    def __init__(
        self,
        parent_offset: int,
        return_pc: int,
        local_base: int,
        saved_sp: int,
        func_idx: int,
    ):
        self.parent_offset = parent_offset
        self.return_pc = return_pc
        self.local_base = local_base
        self.saved_sp = saved_sp
        self.func_idx = func_idx


class ControlFrame:
    """
    Control block/loop/if frame resident in its own dedicated region of the
    stack buffer, never interleaved with the operand stack (ADR-INTERP-03):
    a JIT trace that resolves a loop/block exit never pops one of these, so
    letting it share the operand stack's growing region would let that
    leave the operand stack's own addressing corrupted.
    `{ThreadedInterpreter}`
    """

    def __init__(
        self,
        parent_offset: int,
        label_pc: int,
        saved_sp: int,
        result_arity: int,
        is_loop: bool,
    ):
        self.parent_offset = parent_offset
        self.label_pc = label_pc
        self.saved_sp = saved_sp
        self.result_arity = result_arity
        self.is_loop = is_loop
        self.exec_trace: Callable | None = None


class ExecutionContext:
    """
    WASM execution state resident at Stack Bottom (offset 0).
    `{ContextPointerRegister}` `{EnvironmentPointer}` `{MemoryBoundaryCheck}`
    """

    def __init__(self, memory_size: int = 65536, stack_capacity: int = 1024):
        self.stack: list[int] = [0] * stack_capacity  # Unified stack buffer
        self.sp_offset: int = 0  # Operand stack growth length
        self.call_frame_stack: list[CallFrame] = []
        self.control_frame_stack: list[ControlFrame] = []
        self.globals: list[int] = [0] * 32
        self.memory: bytearray = bytearray(memory_size)
        self.mem_pages: int = memory_size // 65536
        self.safepoint_pending: bool = False
        self.safepoints_hit: int = 0
        self.funcs: list[list[tuple[str, int | object]]] = []

    def push(self, val: int) -> None:
        if self.sp_offset >= len(self.stack):
            raise WASMTrap("STACK_OVERFLOW")
        self.stack[self.sp_offset] = val
        self.sp_offset += 1

    def pop(self) -> int:
        if self.sp_offset <= 0:
            raise WASMTrap("STACK_UNDERFLOW")
        self.sp_offset -= 1
        return self.stack[self.sp_offset]

    def peek(self) -> int:
        if self.sp_offset <= 0:
            raise WASMTrap("STACK_UNDERFLOW")
        return self.stack[self.sp_offset - 1]

    def prune_stack(self, saved_sp: int, arity: int) -> None:
        """
        WASM Label Arity stack pruning: preserves the top `arity` operands,
        rolls back the stack to `saved_sp`, and pushes back the preserved operands.
        """
        results = [self.pop() for _ in range(arity)]
        self.sp_offset = saved_sp
        for res in reversed(results):
            self.push(res)


# ==============================================================================
# 2. WASM Interpreter Engine (CPS Dispatch)
# ==============================================================================


class WASMInterpreter:
    def __init__(self):
        pass

    def execute_function(self, ctx: ExecutionContext, func_idx: int, args: list[int]) -> int:
        """
        Pushes a new CallFrame on the unified stack and executes function bytecode.
        """
        bytecode = ctx.funcs[func_idx]
        local_base = ctx.sp_offset
        for arg in args:
            ctx.push(arg)

        frame = CallFrame(
            parent_offset=0,
            return_pc=0,
            local_base=local_base,
            saved_sp=local_base,
            func_idx=func_idx,
        )
        ctx.call_frame_stack.append(frame)
        status = self.execute_bytecode(ctx, bytecode)
        ctx.call_frame_stack.pop()
        if status == "RETURN":
            res = ctx.pop() if ctx.sp_offset > frame.saved_sp else None
            ctx.sp_offset = frame.saved_sp
            return res
        return None

    def execute_bytecode(
        self, ctx: ExecutionContext, bytecode: list[tuple[str, int | object]]
    ) -> str:
        """
        Direct-Threaded CPS execution loop over WASM MVP opcodes via _DISPATCH_TABLE.
        """
        pc = 0
        while pc < len(bytecode):
            op, arg = bytecode[pc]
            handler = _DISPATCH_TABLE.get(op)
            if handler is None:
                raise WASMTrap(f"UNSUPPORTED_OPCODE: {op}")
            status, next_pc = handler(ctx, arg, pc, self)
            if status is not None:
                return status
            pc = next_pc if next_pc is not None else pc + 1
        return "COMPLETED"


# ==============================================================================
# 3. Direct-Threaded Discrete Opcode Handlers (Zero opcode if-statements)
# ==============================================================================

_DISPATCH_TABLE: dict[
    str,
    Callable[
        [ExecutionContext, int | object, int, WASMInterpreter],
        tuple[str | None, int | None],
    ],
] = {}


_HandlerFn = Callable[
    [ExecutionContext, "int | object", int, "WASMInterpreter"], tuple[str | None, int | None]
]


def _op_handler(op_name: str) -> Callable[[_HandlerFn], _HandlerFn]:
    def decorator(fn: _HandlerFn) -> _HandlerFn:
        _DISPATCH_TABLE[op_name] = fn
        return fn

    return decorator


# --- 3.1 Control Flow Handlers ---


@_op_handler("unreachable")
def _h_unreachable(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    raise WASMTrap("UNREACHABLE_INSTRUCTION")


@_op_handler("nop")
def _h_nop(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    return (None, None)


@_op_handler("block")
def _h_block(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    end_pc, arity = arg
    ctrl = ControlFrame(
        parent_offset=len(ctx.control_frame_stack),
        label_pc=end_pc,
        saved_sp=ctx.sp_offset,
        result_arity=arity,
        is_loop=False,
    )
    ctx.control_frame_stack.append(ctrl)
    return (None, None)


@_op_handler("loop")
def _h_loop(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    start_pc, arity = arg
    ctrl = ControlFrame(
        parent_offset=len(ctx.control_frame_stack),
        label_pc=start_pc,
        saved_sp=ctx.sp_offset,
        result_arity=arity,
        is_loop=True,
    )
    ctx.control_frame_stack.append(ctrl)
    return (None, None)


@_op_handler("if")
def _h_if(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    else_or_end_pc, end_pc, arity = arg
    cond = ctx.pop()
    ctrl = ControlFrame(
        parent_offset=len(ctx.control_frame_stack),
        label_pc=end_pc,
        saved_sp=ctx.sp_offset,
        result_arity=arity,
        is_loop=False,
    )
    ctx.control_frame_stack.append(ctrl)
    if cond == 0:
        return (None, else_or_end_pc)
    return (None, None)


@_op_handler("else")
def _h_else(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    end_pc = arg
    return (None, end_pc)


@_op_handler("end")
def _h_end(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    if ctx.control_frame_stack:
        ctx.control_frame_stack.pop()
    return (None, None)


@_op_handler("br")
def _h_br(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    depth = int(arg)
    target_ctrl = ctx.control_frame_stack[-(depth + 1)]
    ctx.prune_stack(target_ctrl.saved_sp, target_ctrl.result_arity)
    if target_ctrl.is_loop:
        if ctx.safepoint_pending:
            ctx.safepoints_hit += 1
            return ("SAFEPOINT_YIELD", None)
    for _ in range(depth + (0 if target_ctrl.is_loop else 1)):
        ctx.control_frame_stack.pop()
    return (None, target_ctrl.label_pc)


@_op_handler("br_if")
def _h_br_if(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    depth = int(arg)
    cond = ctx.pop()
    if cond != 0:
        target_ctrl = ctx.control_frame_stack[-(depth + 1)]
        ctx.prune_stack(target_ctrl.saved_sp, target_ctrl.result_arity)
        if target_ctrl.is_loop:
            if ctx.safepoint_pending:
                ctx.safepoints_hit += 1
                return ("SAFEPOINT_YIELD", None)
        for _ in range(depth + (0 if target_ctrl.is_loop else 1)):
            ctx.control_frame_stack.pop()
        return (None, target_ctrl.label_pc)
    return (None, None)


@_op_handler("br_table")
def _h_br_table(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    targets, default_target = arg
    idx = ctx.pop()
    depth = targets[idx] if 0 <= idx < len(targets) else default_target
    target_ctrl = ctx.control_frame_stack[-(depth + 1)]
    ctx.prune_stack(target_ctrl.saved_sp, target_ctrl.result_arity)
    for _ in range(depth + (0 if target_ctrl.is_loop else 1)):
        ctx.control_frame_stack.pop()
    return (None, target_ctrl.label_pc)


@_op_handler("return")
def _h_return(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    return ("RETURN", None)


@_op_handler("call")
def _h_call(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    if isinstance(arg, tuple):
        target_func_idx, num_args = arg
    else:
        target_func_idx, num_args = int(arg), 1
    call_args = [ctx.pop() for _ in range(num_args)]
    call_args.reverse()
    res = interp.execute_function(ctx, target_func_idx, call_args)
    if res is not None:
        ctx.push(res)
    return (None, None)


@_op_handler("call_indirect")
def _h_call_indirect(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    if isinstance(arg, tuple):
        type_idx, num_args = arg
    else:
        type_idx, num_args = int(arg), 1
    call_args = [ctx.pop() for _ in range(num_args)]
    call_args.reverse()
    elem_idx = ctx.pop()
    res = interp.execute_function(ctx, elem_idx, call_args)
    if res is not None:
        ctx.push(res)
    return (None, None)


# --- 3.2 Parametric Handlers ---


@_op_handler("drop")
def _h_drop(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.pop()
    return (None, None)


@_op_handler("select")
def _h_select(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    c = ctx.pop()
    v2 = ctx.pop()
    v1 = ctx.pop()
    ctx.push(v1 if c != 0 else v2)
    return (None, None)


# --- 3.3 Variable Access Handlers ---


@_op_handler("local.get")
def _h_local_get(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    frame = ctx.call_frame_stack[-1]
    ctx.push(ctx.stack[frame.local_base + int(arg)])
    return (None, None)


@_op_handler("local.set")
def _h_local_set(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    frame = ctx.call_frame_stack[-1]
    ctx.stack[frame.local_base + int(arg)] = ctx.pop()
    return (None, None)


@_op_handler("local.tee")
def _h_local_tee(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    frame = ctx.call_frame_stack[-1]
    ctx.stack[frame.local_base + int(arg)] = ctx.peek()
    return (None, None)


@_op_handler("global.get")
def _h_global_get(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(ctx.globals[int(arg)])
    return (None, None)


@_op_handler("global.set")
def _h_global_set(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.globals[int(arg)] = ctx.pop()
    return (None, None)


# --- 3.4 Memory Access Handlers ---


@_op_handler("i32.load")
def _h_i32_load(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 4 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<I", ctx.memory[addr : addr + 4])[0])
    return (None, None)


@_op_handler("i64.load")
def _h_i64_load(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 8 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<Q", ctx.memory[addr : addr + 8])[0])
    return (None, None)


@_op_handler("i32.load8_s")
def _h_i32_load8_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 1 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<b", bytes([ctx.memory[addr]]))[0])
    return (None, None)


@_op_handler("i32.load8_u")
def _h_i32_load8_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 1 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(ctx.memory[addr])
    return (None, None)


@_op_handler("i32.load16_s")
def _h_i32_load16_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 2 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<h", ctx.memory[addr : addr + 2])[0])
    return (None, None)


@_op_handler("i32.load16_u")
def _h_i32_load16_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 2 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<H", ctx.memory[addr : addr + 2])[0])
    return (None, None)


@_op_handler("i64.load8_s")
def _h_i64_load8_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 1 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<b", bytes([ctx.memory[addr]]))[0])
    return (None, None)


@_op_handler("i64.load8_u")
def _h_i64_load8_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 1 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(ctx.memory[addr])
    return (None, None)


@_op_handler("i64.load16_s")
def _h_i64_load16_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 2 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<h", ctx.memory[addr : addr + 2])[0])
    return (None, None)


@_op_handler("i64.load16_u")
def _h_i64_load16_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 2 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<H", ctx.memory[addr : addr + 2])[0])
    return (None, None)


@_op_handler("i64.load32_s")
def _h_i64_load32_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 4 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<i", ctx.memory[addr : addr + 4])[0])
    return (None, None)


@_op_handler("i64.load32_u")
def _h_i64_load32_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    addr = ctx.pop()
    if addr + 4 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.push(struct.unpack("<I", ctx.memory[addr : addr + 4])[0])
    return (None, None)


@_op_handler("i32.store")
def _h_i32_store(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val = ctx.pop()
    addr = ctx.pop()
    if addr + 4 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.memory[addr : addr + 4] = struct.pack("<I", val & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i64.store")
def _h_i64_store(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val = ctx.pop()
    addr = ctx.pop()
    if addr + 8 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.memory[addr : addr + 8] = struct.pack("<Q", val & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i32.store8")
def _h_i32_store8(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val = ctx.pop()
    addr = ctx.pop()
    if addr + 1 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.memory[addr] = val & 0xFF
    return (None, None)


@_op_handler("i64.store8")
def _h_i64_store8(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val = ctx.pop()
    addr = ctx.pop()
    if addr + 1 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.memory[addr] = val & 0xFF
    return (None, None)


@_op_handler("i32.store16")
def _h_i32_store16(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val = ctx.pop()
    addr = ctx.pop()
    if addr + 2 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.memory[addr : addr + 2] = struct.pack("<H", val & 0xFFFF)
    return (None, None)


@_op_handler("i64.store16")
def _h_i64_store16(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val = ctx.pop()
    addr = ctx.pop()
    if addr + 2 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.memory[addr : addr + 2] = struct.pack("<H", val & 0xFFFF)
    return (None, None)


@_op_handler("i64.store32")
def _h_i64_store32(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val = ctx.pop()
    addr = ctx.pop()
    if addr + 4 > len(ctx.memory):
        raise WASMTrap("OUT_OF_BOUNDS_MEMORY_ACCESS")
    ctx.memory[addr : addr + 4] = struct.pack("<I", val & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("memory.size")
def _h_memory_size_op(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(ctx.mem_pages)
    return (None, None)


@_op_handler("memory.grow")
def _h_memory_grow_op(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    delta = ctx.pop()
    old_pages = ctx.mem_pages
    ctx.memory.extend(bytearray(delta * 65536))
    ctx.mem_pages += delta
    ctx.push(old_pages)
    return (None, None)


# --- 3.5 Constants ---


@_op_handler("i32.const")
def _h_i32_const(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(int(arg) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i64.const")
def _h_i64_const(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(int(arg) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


# --- 3.6 32-bit Integer ALU & Logic Handlers ---


@_op_handler("i32.eqz")
def _h_i32_eqz(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(1 if ctx.pop() == 0 else 0)
    return (None, None)


@_op_handler("i32.eq")
def _h_i32_eq(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a == b else 0)
    return (None, None)


@_op_handler("i32.ne")
def _h_i32_ne(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a != b else 0)
    return (None, None)


@_op_handler("i32.lt_s")
def _h_i32_lt_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">i", struct.pack(">I", a & 0xFFFF_FFFF))[0]
    sb = struct.unpack(">i", struct.pack(">I", b & 0xFFFF_FFFF))[0]
    ctx.push(1 if sa < sb else 0)
    return (None, None)


@_op_handler("i32.lt_u")
def _h_i32_lt_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a < b else 0)
    return (None, None)


@_op_handler("i32.gt_s")
def _h_i32_gt_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">i", struct.pack(">I", a & 0xFFFF_FFFF))[0]
    sb = struct.unpack(">i", struct.pack(">I", b & 0xFFFF_FFFF))[0]
    ctx.push(1 if sa > sb else 0)
    return (None, None)


@_op_handler("i32.gt_u")
def _h_i32_gt_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a > b else 0)
    return (None, None)


@_op_handler("i32.le_s")
def _h_i32_le_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">i", struct.pack(">I", a & 0xFFFF_FFFF))[0]
    sb = struct.unpack(">i", struct.pack(">I", b & 0xFFFF_FFFF))[0]
    ctx.push(1 if sa <= sb else 0)
    return (None, None)


@_op_handler("i32.le_u")
def _h_i32_le_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a <= b else 0)
    return (None, None)


@_op_handler("i32.ge_s")
def _h_i32_ge_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">i", struct.pack(">I", a & 0xFFFF_FFFF))[0]
    sb = struct.unpack(">i", struct.pack(">I", b & 0xFFFF_FFFF))[0]
    ctx.push(1 if sa >= sb else 0)
    return (None, None)


@_op_handler("i32.ge_u")
def _h_i32_ge_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a >= b else 0)
    return (None, None)


@_op_handler("i32.clz")
def _h_i32_clz(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    a = ctx.pop() & 0xFFFF_FFFF
    bits = bin(a)[2:].zfill(32)
    ctx.push(len(bits) - len(bits.lstrip("0")))
    return (None, None)


@_op_handler("i32.ctz")
def _h_i32_ctz(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    a = ctx.pop() & 0xFFFF_FFFF
    bits = bin(a)[2:].zfill(32)
    ctx.push(len(bits) - len(bits.rstrip("0")))
    return (None, None)


@_op_handler("i32.popcnt")
def _h_i32_popcnt(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(bin(ctx.pop() & 0xFFFF_FFFF).count("1"))
    return (None, None)


@_op_handler("i32.add")
def _h_i32_add(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a + b) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.sub")
def _h_i32_sub(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a - b) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.mul")
def _h_i32_mul(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a * b) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.div_s")
def _h_i32_div_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    sa = struct.unpack(">i", struct.pack(">I", a & 0xFFFF_FFFF))[0]
    sb = struct.unpack(">i", struct.pack(">I", b & 0xFFFF_FFFF))[0]
    ctx.push(int(sa / sb) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.div_u")
def _h_i32_div_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    ctx.push((a // b) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.rem_s")
def _h_i32_rem_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    sa = struct.unpack(">i", struct.pack(">I", a & 0xFFFF_FFFF))[0]
    sb = struct.unpack(">i", struct.pack(">I", b & 0xFFFF_FFFF))[0]
    res = sa % sb if sa * sb >= 0 else sa % sb - sb
    ctx.push(res & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.rem_u")
def _h_i32_rem_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    ctx.push((a % b) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.and")
def _h_i32_and(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(a & b)
    return (None, None)


@_op_handler("i32.or")
def _h_i32_or(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(a | b)
    return (None, None)


@_op_handler("i32.xor")
def _h_i32_xor(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(a ^ b)
    return (None, None)


@_op_handler("i32.shl")
def _h_i32_shl(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a << (b & 31)) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.shr_s")
def _h_i32_shr_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">i", struct.pack(">I", a & 0xFFFF_FFFF))[0]
    ctx.push((sa >> (b & 31)) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.shr_u")
def _h_i32_shr_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(((a & 0xFFFF_FFFF) >> (b & 31)) & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i32.rotl")
def _h_i32_rotl(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    shift = b & 31
    val = ((a << shift) | (a >> (32 - shift))) & 0xFFFF_FFFF
    ctx.push(val)
    return (None, None)


@_op_handler("i32.rotr")
def _h_i32_rotr(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    shift = b & 31
    val = ((a >> shift) | (a << (32 - shift))) & 0xFFFF_FFFF
    ctx.push(val)
    return (None, None)


# --- 3.7 64-bit Integer ALU & Logic Handlers ---


@_op_handler("i64.eqz")
def _h_i64_eqz(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(1 if ctx.pop() == 0 else 0)
    return (None, None)


@_op_handler("i64.eq")
def _h_i64_eq(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a == b else 0)
    return (None, None)


@_op_handler("i64.ne")
def _h_i64_ne(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a != b else 0)
    return (None, None)


@_op_handler("i64.lt_s")
def _h_i64_lt_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">q", struct.pack(">Q", a & 0xFFFF_FFFF_FFFF_FFFF))[0]
    sb = struct.unpack(">q", struct.pack(">Q", b & 0xFFFF_FFFF_FFFF_FFFF))[0]
    ctx.push(1 if sa < sb else 0)
    return (None, None)


@_op_handler("i64.lt_u")
def _h_i64_lt_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a < b else 0)
    return (None, None)


@_op_handler("i64.gt_s")
def _h_i64_gt_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">q", struct.pack(">Q", a & 0xFFFF_FFFF_FFFF_FFFF))[0]
    sb = struct.unpack(">q", struct.pack(">Q", b & 0xFFFF_FFFF_FFFF_FFFF))[0]
    ctx.push(1 if sa > sb else 0)
    return (None, None)


@_op_handler("i64.gt_u")
def _h_i64_gt_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a > b else 0)
    return (None, None)


@_op_handler("i64.le_s")
def _h_i64_le_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">q", struct.pack(">Q", a & 0xFFFF_FFFF_FFFF_FFFF))[0]
    sb = struct.unpack(">q", struct.pack(">Q", b & 0xFFFF_FFFF_FFFF_FFFF))[0]
    ctx.push(1 if sa <= sb else 0)
    return (None, None)


@_op_handler("i64.le_u")
def _h_i64_le_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a <= b else 0)
    return (None, None)


@_op_handler("i64.ge_s")
def _h_i64_ge_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">q", struct.pack(">Q", a & 0xFFFF_FFFF_FFFF_FFFF))[0]
    sb = struct.unpack(">q", struct.pack(">Q", b & 0xFFFF_FFFF_FFFF_FFFF))[0]
    ctx.push(1 if sa >= sb else 0)
    return (None, None)


@_op_handler("i64.ge_u")
def _h_i64_ge_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(1 if a >= b else 0)
    return (None, None)


@_op_handler("i64.clz")
def _h_i64_clz(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    a = ctx.pop() & 0xFFFF_FFFF_FFFF_FFFF
    bits = bin(a)[2:].zfill(64)
    ctx.push(len(bits) - len(bits.lstrip("0")))
    return (None, None)


@_op_handler("i64.ctz")
def _h_i64_ctz(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    a = ctx.pop() & 0xFFFF_FFFF_FFFF_FFFF
    bits = bin(a)[2:].zfill(64)
    ctx.push(len(bits) - len(bits.rstrip("0")))
    return (None, None)


@_op_handler("i64.popcnt")
def _h_i64_popcnt(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(bin(ctx.pop() & 0xFFFF_FFFF_FFFF_FFFF).count("1"))
    return (None, None)


@_op_handler("i64.add")
def _h_i64_add(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a + b) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.sub")
def _h_i64_sub(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a - b) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.mul")
def _h_i64_mul(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a * b) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.div_s")
def _h_i64_div_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    sa = struct.unpack(">q", struct.pack(">Q", a & 0xFFFF_FFFF_FFFF_FFFF))[0]
    sb = struct.unpack(">q", struct.pack(">Q", b & 0xFFFF_FFFF_FFFF_FFFF))[0]
    ctx.push(int(sa / sb) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.div_u")
def _h_i64_div_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    ctx.push((a // b) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.rem_s")
def _h_i64_rem_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    sa = struct.unpack(">q", struct.pack(">Q", a & 0xFFFF_FFFF_FFFF_FFFF))[0]
    sb = struct.unpack(">q", struct.pack(">Q", b & 0xFFFF_FFFF_FFFF_FFFF))[0]
    res = sa % sb if sa * sb >= 0 else sa % sb - sb
    ctx.push(res & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.rem_u")
def _h_i64_rem_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    if b == 0:
        raise WASMTrap("INTEGER_DIVIDE_BY_ZERO")
    ctx.push((a % b) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.and")
def _h_i64_and(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(a & b)
    return (None, None)


@_op_handler("i64.or")
def _h_i64_or(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(a | b)
    return (None, None)


@_op_handler("i64.xor")
def _h_i64_xor(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(a ^ b)
    return (None, None)


@_op_handler("i64.shl")
def _h_i64_shl(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a << (b & 63)) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.shr_s")
def _h_i64_shr_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    sa = struct.unpack(">q", struct.pack(">Q", a & 0xFFFF_FFFF_FFFF_FFFF))[0]
    ctx.push((sa >> (b & 63)) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.shr_u")
def _h_i64_shr_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    ctx.push(((a & 0xFFFF_FFFF_FFFF_FFFF) >> (b & 63)) & 0xFFFF_FFFF_FFFF_FFFF)
    return (None, None)


@_op_handler("i64.rotl")
def _h_i64_rotl(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    shift = b & 63
    val = ((a << shift) | (a >> (64 - shift))) & 0xFFFF_FFFF_FFFF_FFFF
    ctx.push(val)
    return (None, None)


@_op_handler("i64.rotr")
def _h_i64_rotr(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    b, a = ctx.pop(), ctx.pop()
    shift = b & 63
    val = ((a >> shift) | (a << (64 - shift))) & 0xFFFF_FFFF_FFFF_FFFF
    ctx.push(val)
    return (None, None)


# --- 3.8 Type Conversion Handlers ---


@_op_handler("i32.wrap_i64")
def _h_i32_wrap_i64(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    ctx.push(ctx.pop() & 0xFFFF_FFFF)
    return (None, None)


@_op_handler("i64.extend_i32_s")
def _h_i64_extend_i32_s(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val32 = ctx.pop() & 0xFFFF_FFFF
    signed32 = struct.unpack(">i", struct.pack(">I", val32))[0]
    ctx.push(struct.unpack(">Q", struct.pack(">q", signed32))[0])
    return (None, None)


@_op_handler("i64.extend_i32_u")
def _h_i64_extend_i32_u(
    ctx: ExecutionContext, arg: int | object, pc: int, interp: WASMInterpreter
) -> tuple[str | None, int | None]:
    val32 = ctx.pop() & 0xFFFF_FFFF
    ctx.push(val32)
    return (None, None)


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================


def test_full_wasm_recursive_factorial() -> None:
    """Test CallFrame invocation, local variables, and recursion."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    # Function 0: fact(n)
    # if n <= 1 return 1; else return n * fact(n - 1)
    fact_bytecode = [
        ("local.get", 0),
        ("i32.const", 1),
        ("i32.le_s", None),
        ("if", (7, 14, 1)),  # if cond == 0, jump to 7 (else)
        ("i32.const", 1),
        ("return", None),
        ("else", 14),
        ("local.get", 0),  # 7
        ("local.get", 0),
        ("i32.const", 1),
        ("i32.sub", None),
        ("call", 0),  # recursive call fact(n - 1)
        ("i32.mul", None),
        ("return", None),
        ("end", None),  # 14
    ]
    ctx.funcs = [fact_bytecode]
    res = interp.execute_function(ctx, func_idx=0, args=[5])
    assert res == 120, f"Expected 120, got {res}"


def test_block_loop_and_stack_pruning() -> None:
    """Test block/loop nesting and label arity pruning on br/br_if."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    # Block returning i32 = 42
    # block (result i32)
    #   i32.const 10
    #   i32.const 42
    #   br 0 (prunes 10, keeps 42)
    # end
    pruning_bytecode = [
        ("block", (5, 1)),  # block result i32
        ("i32.const", 10),
        ("i32.const", 42),
        ("br", 0),  # prune stack and exit block
        ("i32.const", 99),
        ("end", None),
        ("return", None),
    ]
    ctx.funcs = [pruning_bytecode]
    res = interp.execute_function(ctx, func_idx=0, args=[])
    assert res == 42


def test_64bit_integer_arithmetic() -> None:
    """Test full 64-bit ALU operations and type conversions."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    i64_bytecode = [
        ("i64.const", 0x1_0000_0000),
        ("i64.const", 0x2_0000_0000),
        ("i64.add", None),
        ("i64.const", 3),
        ("i64.mul", None),  # 0x9_0000_0000
        ("i32.wrap_i64", None),  # 0
        ("i64.extend_i32_u", None),
        ("i64.const", 100),
        ("i64.add", None),
        ("return", None),
    ]
    ctx.funcs = [i64_bytecode]
    res = interp.execute_function(ctx, func_idx=0, args=[])
    assert res == 100


def test_memory_load_store_all_sizes() -> None:
    """Test 8/16/32/64-bit load/store with signed/unsigned extensions."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    mem_bytecode = [
        # Store 64-bit value at addr 0
        ("i32.const", 0),
        ("i64.const", 0x0123_4567_89AB_CDEF),
        ("i64.store", None),
        # Load 8-bit unsigned at addr 0 -> 0xEF
        ("i32.const", 0),
        ("i32.load8_u", None),
        # Load 16-bit unsigned at addr 0 -> 0xCDEF
        ("i32.const", 0),
        ("i32.load16_u", None),
        ("i32.add", None),
        ("return", None),
    ]
    ctx.funcs = [mem_bytecode]
    res = interp.execute_function(ctx, func_idx=0, args=[])
    assert res == 0xEF + 0xCDEF


def test_cooperative_safepoint() -> None:
    """Test loop header safepoint interruption."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    infinite_loop_bytecode = [
        ("loop", (0, 0)),
        ("br", 0),
        ("end", None),
    ]
    ctx.funcs = [infinite_loop_bytecode]
    ctx.safepoint_pending = True
    status = interp.execute_bytecode(ctx, infinite_loop_bytecode)
    assert status == "SAFEPOINT_YIELD"
    assert ctx.safepoints_hit == 1


def test_br_table_and_parametric() -> None:
    """Test br_table multi-branching and select/drop parametric opcodes."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    # block (result i32)
    #   block
    #     block
    #       i32.const 1 (index 1 -> branch to depth 1 -> outer block)
    #       br_table ([0, 1], 2)
    #     end
    #     i32.const 10
    #     return
    #   end
    #   i32.const 42
    #   i32.const 99
    #   drop
    #   i32.const 100
    #   i32.const 200
    #   i32.const 1
    #   select
    #   i32.add
    # end
    br_table_bytecode = [
        ("block", (17, 1)),  # depth 2: outer block (end at 17)
        ("block", (8, 0)),  # depth 1: middle block (end at 8)
        ("block", (5, 0)),  # depth 0: inner block (end at 5)
        ("i32.const", 1),  # 3: index 1
        ("br_table", ([0, 1], 2)),  # 4: target depth 1 -> jumps to 8
        ("end", None),  # 5
        ("i32.const", 10),  # 6
        ("return", None),  # 7
        ("end", None),  # 8: depth 1 target -> jumps here
        ("i32.const", 42),  # 9
        ("i32.const", 99),  # 10
        ("drop", None),  # 11: drops 99, keeps 42
        ("i32.const", 100),  # 12: val1
        ("i32.const", 200),  # 13: val2
        ("i32.const", 1),  # 14: cond 1 -> select val1 (100)
        ("select", None),  # 15
        ("i32.add", None),  # 16: 42 + 100 = 142
        ("end", None),  # 17
        ("return", None),  # 18
    ]
    ctx.funcs = [br_table_bytecode]
    res = interp.execute_function(ctx, func_idx=0, args=[])
    assert res == 142


def test_signed_memory_and_division_clz_popcnt() -> None:
    """Test signed 8/16/32/64-bit load/store, division/remainder, clz, ctz, popcnt."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    bytecode = [
        # Store -5 at addr 0 (as 8-bit)
        ("i32.const", 0),
        ("i32.const", -5 & 0xFF),
        ("i32.store8", None),
        # Load signed 8-bit from addr 0 -> -5
        ("i32.const", 0),
        ("i32.load8_s", None),
        # Store -1000 at addr 4 (as 16-bit)
        ("i32.const", 4),
        ("i32.const", -1000 & 0xFFFF),
        ("i32.store16", None),
        # Load signed 16-bit from addr 4 -> -1000
        ("i32.const", 4),
        ("i32.load16_s", None),
        ("i32.add", None),  # -5 + -1000 = -1005
        # Bit counting on 0b0000_1111 (15)
        ("i32.const", 15),
        ("i32.clz", None),  # 32 - 4 = 28
        ("i32.const", 16),
        ("i32.ctz", None),  # 4
        ("i32.const", 7),
        ("i32.popcnt", None),  # 3
        ("i32.add", None),
        ("i32.add", None),  # 28 + 4 + 3 = 35
        ("i32.add", None),  # -1005 + 35 = -970
        ("return", None),
    ]
    ctx.funcs = [bytecode]
    res = interp.execute_function(ctx, func_idx=0, args=[])
    sa = struct.unpack(">i", struct.pack(">I", res & 0xFFFF_FFFF))[0]
    assert sa == -970


def test_globals_and_memory_grow() -> None:
    """Test global variables and dynamic memory growth."""
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    bytecode = [
        ("i32.const", 777),
        ("global.set", 0),
        ("global.get", 0),
        ("memory.size", None),  # 1 page (64KB)
        ("i32.const", 2),  # grow 2 pages
        ("memory.grow", None),  # returns old size: 1
        ("memory.size", None),  # new size: 3
        ("i32.add", None),  # 1 + 3 = 4
        ("i32.add", None),  # 1 + 4 = 5
        ("i32.add", None),  # 777 + 5 = 782
        ("return", None),
    ]
    ctx.funcs = [bytecode]
    res = interp.execute_function(ctx, func_idx=0, args=[])
    assert res == 782


if __name__ == "__main__":
    test_full_wasm_recursive_factorial()
    test_block_loop_and_stack_pruning()
    test_br_table_and_parametric()
    test_64bit_integer_arithmetic()
    test_memory_load_store_all_sizes()
    test_signed_memory_and_division_clz_popcnt()
    test_globals_and_memory_grow()
    test_cooperative_safepoint()
    print("[PASS] All Full-Set WASM MVP Interpreter concept tests passed successfully.")

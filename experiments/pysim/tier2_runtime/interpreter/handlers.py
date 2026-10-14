"""Static direct-indexed WASM opcode handlers for the reference interpreter."""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from system_containers import SequenceView, StaticVector, freeze_sequence
from tier2_runtime.abi.interpreter_abi import NativeValueStack, UInt32Pointer
from tier2_runtime.interpreter.control_flow import (
    OpcodeAttribute,
    opcode_has_attribute,
)
from tier2_runtime.vmmio.controller import (
    FC_DYNAMIC,
    FC_PASSTHROUGH,
    FC_SHM,
    Tier3PTE,
    VmmioAddress,
    VmmioStatus,
)
from tier2_runtime.wasm.leb128 import (
    decode_signed_full,
    decode_unsigned_full,
)
from tier2_runtime.wasm.module import F64, I64
from tier2_runtime.wasm.opcodes import (
    BLOCK,
    BR,
    BR_IF,
    BR_TABLE,
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

from .errors import RETURN_SENTINEL_IP, Trap, TrapCode
from .utilities import (
    I32_MASK,
    I64_MASK,
    _f32_max,
    _f32_min,
    _memory_range_is_valid,
    _read_memarg,
    _to_f32,
    _to_i32,
    _to_i64,
    _to_u32,
    _to_u64,
    _truncate_float,
    _truncate_saturating_float,
    _wasm_float_div,
    _wasm_integral_round,
    _wasm_nearest,
)

if TYPE_CHECKING:
    from .interpreter import CallFrame, ExecEnv, ExecutionContext


_HandlerResult = Trap | None
_HandlerFn = Callable[
    ["ExecutionContext", UInt32Pointer, NativeValueStack],
    _HandlerResult,
]
_BASIC_BLOCK_BOUNDARY: bytes = bytes(
    opcode_has_attribute(opcode, OpcodeAttribute.BASIC_BLOCK_BOUNDARY) for opcode in range(256)
)


def _do_branch(ip: int, edge_index: int, frame: "CallFrame") -> int | None:
    """Apply a loader-resolved branch edge and restore its operand height."""
    target_ip, stack_base, result_words = frame.control_map.branch_edges(ip)[edge_index]
    values = frame.values
    assert 0 <= result_words <= 2
    operand_base_address = int(frame.native.operand_stack_base or 0)
    assert operand_base_address >= values.address
    operand_base = (operand_base_address - values.address) // 4
    stack_base += operand_base
    assert stack_base + result_words <= len(values)
    result_start = len(values) - result_words
    result0 = values.raw_at(result_start) if result_words >= 1 else 0
    result1 = values.raw_at(result_start + 1) if result_words >= 2 else 0
    values.truncate(stack_base)
    if result_words >= 1:
        pushed = values.push_back(result0)
        assert pushed
    if result_words >= 2:
        pushed = values.push_back(result1)
        assert pushed
    return None if target_ip == 0xFFFF_FFFF else target_ip


# Per-opcode logical CPS handlers. Each receives ctx, ip*, and sp*, updates
# the shared state in place, and returns only an optional trap.
# ---------------------------------------------------------------------------


def _h_unreachable(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return Trap(TrapCode.UNREACHABLE)


def _h_nop(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    instruction_ip = int(ip[0])
    ctx.ip = instruction_ip + 1
    return None


def _h_block(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    instruction_ip = int(ip[0])
    ctx.ip = instruction_ip + 2
    return None


def _h_loop(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    instruction_ip = int(ip[0])
    ctx.ip = instruction_ip + 2
    return None


def _h_if(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    match_end, else_off, _ = frame.control_map.block(instruction_ip)
    cond = frame.values.pop_back()
    if cond == 0:
        if else_off is not None:
            ctx.ip = else_off + 1
            return None
        else:
            ctx.ip = match_end + 1
            return None
    ctx.ip = instruction_ip + 2
    return None


def _h_else(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    ctx.ip = frame.control_map.else_target(instruction_ip)
    return None


def _h_end(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    instruction_ip = int(ip[0])
    ctx.ip = instruction_ip + 1
    return None


def _h_br(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    next_ip = _do_branch(instruction_ip, 0, frame)
    if (next_ip) is None:
        ctx.ip = RETURN_SENTINEL_IP
        return None
    ctx.ip = next_ip
    return None


def _h_br_if(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    _, next_ip = decode_unsigned_full(frame.code, instruction_ip + 1)
    cond = frame.values.pop_back()
    if cond == 0:
        ctx.ip = next_ip
        return None
    target_ip = _do_branch(instruction_ip, 0, frame)
    if target_ip is None:
        ctx.ip = RETURN_SENTINEL_IP
        return None
    ctx.ip = target_ip
    return None


def _h_br_table(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    index = _to_u32(frame.values.pop_back())
    edges = frame.control_map.branch_edges(instruction_ip)
    target_index = index if index < len(edges) - 1 else len(edges) - 1
    next_ip = _do_branch(instruction_ip, target_index, frame)
    if (next_ip) is None:
        ctx.ip = RETURN_SENTINEL_IP
        return None
    ctx.ip = next_ip
    return None


def _h_return(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    ctx.ip = RETURN_SENTINEL_IP
    return None


# CALL and CALL_INDIRECT are not in `_HANDLERS`. Python `Interpreter.step()`
# handles them before its table lookup. `NativeInterpreter.step_native()` and
# `RuntimeEngine` resolve the C++ dispatch boundary through the same call
# resolver; no guest opcode is sent to the Python handler table.


def _h_drop(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    module = frame.context.module
    assert module is not None
    function = module.functions[frame.func_index - len(module.imports)]
    width = (
        function.drop_widths.view().find(instruction_ip)
        if function.drop_widths is not None
        else None
    )
    frame.values.pop_back()
    if width == 2:
        frame.values.pop_back()
    ctx.ip = instruction_ip + 1
    return None


def _h_select(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    c = frame.values.pop_back()
    assert c is not None
    module = frame.context.module
    assert module is not None
    function = module.functions[frame.func_index - len(module.imports)]
    width = (
        function.select_widths.view().find(instruction_ip)
        if function.select_widths is not None
        else None
    )
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
    ctx.ip = instruction_ip + 1
    return None


def _h_local_get(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    idx, next_ip = decode_unsigned_full(frame.code, instruction_ip + 1)
    raw_slot, raw_width = frame.locals.raw_span(idx)
    pushed = frame.values.push_raw_from(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    assert pushed
    ctx.ip = next_ip
    return None


def _h_local_set(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    idx, next_ip = decode_unsigned_full(frame.code, instruction_ip + 1)
    raw_slot, raw_width = frame.locals.raw_span(idx)
    frame.values.pop_raw_to(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.ip = next_ip
    return None


def _h_local_tee(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    idx, next_ip = decode_unsigned_full(frame.code, instruction_ip + 1)
    raw_slot, raw_width = frame.locals.raw_span(idx)
    frame.values.copy_raw_to(
        frame.context.local_stack,
        raw_slot,
        raw_width,
    )
    ctx.ip = next_ip
    return None


def _h_i32_const(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    val, next_ip = decode_signed_full(frame.code, instruction_ip + 1, bits=32)
    frame.values.push_back(_to_i32(val))
    ctx.ip = next_ip
    return None


def _h_global_get(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    idx, next_ip = decode_unsigned_full(frame.code, instruction_ip + 1)
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


def _h_global_set(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    idx, next_ip = decode_unsigned_full(frame.code, instruction_ip + 1)
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
    assert status == VmmioStatus.OK_PHYSICAL, "unexpected vMMIO success status"
    address = VmmioAddress(addr)
    function_class = address.fc()
    if function_class == FC_DYNAMIC or function_class == FC_SHM:
        pte = env.vmmio.ptes.view().find(address.vpn())
        assert pte is not None
        # FC=13/14 mappings are installed with Tier3PTE backing records.
        mapped_pte = cast(Tier3PTE, pte)
        storage = mapped_pte.mapped_storage
        if storage is not None:
            return int.from_bytes(
                storage[address.offset() : address.offset() + width], "little", signed=signed
            ), None
        assert function_class == FC_SHM, "DYNAMIC mappings require backing storage"
    assert env.phys_mem is not None
    assert phys_addr + width <= len(env.phys_mem)
    return (
        int.from_bytes(env.phys_mem[phys_addr : phys_addr + width], "little", signed=signed),
        None,
    )


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
            # FC=13/14 mappings are installed with Tier3PTE backing records.
            mapped_pte = cast(Tier3PTE, pte)
            if address.fc() == FC_DYNAMIC:
                assert mapped_pte.mapped_storage is not None
            if mapped_pte.mapped_storage is not None:
                mapped_pte.mapped_storage[address.offset() : address.offset() + len(val_bytes)] = (
                    val_bytes
                )
                return None
        assert env.phys_mem is not None
        assert phys_addr + len(val_bytes) <= len(env.phys_mem)
        env.phys_mem[phys_addr : phys_addr + len(val_bytes)] = val_bytes
    return None


def _h_i32_load(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i32_load8_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i32_load8_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i32_load16_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i32_load16_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i32_store(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i32_store8(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i32_store16(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    mem_offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_memory_size(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    page_count = env.runtime_api.memory_size()
    if page_count is None:
        return Trap(TrapCode.MEMORY_SECTION_MISSING)
    frame.values.push_back(page_count)
    ctx.ip = instruction_ip + 2
    return None


def _h_memory_grow(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    delta_pages = _to_u32(frame.values.pop_back())
    frame.values.push_back(env.runtime_api.grow_memory(delta_pages))
    ctx.ip = instruction_ip + 2
    return None


# --- Comparisons (Dedicated per-opcode handlers without if statements) ---


def _h_i32_eqz(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    frame.values.push_back(1 if frame.values.pop_back() == 0 else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_eq(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) == _to_i32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_ne(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) != _to_i32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_lt_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) < _to_i32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_lt_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) < _to_u32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_gt_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) > _to_i32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_gt_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) > _to_u32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_le_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) <= _to_i32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_le_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) <= _to_u32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_ge_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_i32(a) >= _to_i32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_ge_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(1 if _to_u32(a) >= _to_u32(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


# --- Unary Ops (Dedicated per-opcode handlers without if statements) ---


def _h_i32_clz(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(32 if v == 0 else 32 - v.bit_length())
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_ctz(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
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
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_popcnt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    v = _to_u32(frame.values.pop_back())
    frame.values.push_back(bin(v).count("1"))
    ctx.ip = instruction_ip + 1
    return None


# --- Binary Arithmetic & Bitwise Ops (Dedicated per-opcode handlers without if statements) ---


def _h_i32_add(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a + b))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_sub(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a - b))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_mul(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(a * b))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_div_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = _to_i32(frame.values.pop_back())
    a = _to_i32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    if a == -2147483648 and b == -1:
        return Trap(TrapCode.INTEGER_OVERFLOW)
    q = abs(a) // abs(b)
    frame.values.push_back(_to_i32(-q if (a < 0) != (b < 0) else q))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_div_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    frame.values.push_back(_to_i32(a // b))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_rem_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = _to_i32(frame.values.pop_back())
    a = _to_i32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    r = abs(a) % abs(b)
    frame.values.push_back(_to_i32(-r if a < 0 else r))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_rem_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = _to_u32(frame.values.pop_back())
    a = _to_u32(frame.values.pop_back())
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    frame.values.push_back(_to_i32(a % b))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_and(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) & _to_u32(b)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_or(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) | _to_u32(b)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_xor(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) ^ _to_u32(b)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_shl(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) << (_to_u32(b) & 31)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_shr_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_i32(a) >> (_to_u32(b) & 31)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_shr_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    frame.values.push_back(_to_i32(_to_u32(a) >> (_to_u32(b) & 31)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_rotl(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.push_back(_to_i32(((v << n) | (v >> (32 - n))) & I32_MASK if n else v))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_rotr(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_back()
    a = frame.values.pop_back()
    n = _to_u32(b) & 31
    v = _to_u32(a)
    frame.values.push_back(_to_i32(((v >> n) | (v << (32 - n))) & I32_MASK if n else v))
    ctx.ip = instruction_ip + 1
    return None


# Helper conversion utilities


# --- i64 / f32 / f64 Memory Handlers ---


def _h_i64_load(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if addr & 0x8000_0000:
        val, trap = _vmmio_load(env, addr, 8, signed=True)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + 8 > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        val = struct.unpack("<q", env.memory[addr : addr + 8])[0]
    pushed = frame.values.push_i64(val)
    assert pushed
    ctx.ip = next_ip
    return None


def _h_i64_load_narrow(
    ctx: ExecutionContext,
    ip: UInt32Pointer,
    sp: NativeValueStack,
    width: int,
    signed: bool,
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if addr & 0x8000_0000:
        val, trap = _vmmio_load(env, addr, width, signed=signed)
        if trap is not None:
            return trap
    else:
        if env.memory is None or addr + width > len(env.memory):
            return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
        val = int.from_bytes(env.memory[addr : addr + width], "little", signed=signed)
    pushed = frame.values.push_i64(_to_i64(val))
    assert pushed
    ctx.ip = next_ip
    return None


def _h_i64_load8_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, ip, sp, 1, True)


def _h_i64_load8_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, ip, sp, 1, False)


def _h_i64_load16_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, ip, sp, 2, True)


def _h_i64_load16_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, ip, sp, 2, False)


def _h_i64_load32_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, ip, sp, 4, True)


def _h_i64_load32_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_load_narrow(ctx, ip, sp, 4, False)


def _h_i64_store(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
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
    ip: UInt32Pointer,
    sp: NativeValueStack,
    width: int,
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
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


def _h_i64_store8(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, ip, sp, 1)


def _h_i64_store16(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, ip, sp, 2)


def _h_i64_store32(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    return _h_i64_store_narrow(ctx, ip, sp, 4)


def _h_f32_load(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    bits = int.from_bytes(env.memory[addr : addr + 4], "little")
    pushed = frame.values.push_back(bits)
    assert pushed
    ctx.ip = next_ip
    return None


def _h_f32_store(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
    bits = frame.values.pop_back()
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 4 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    env.memory[addr : addr + 4] = (bits & 0xFFFF_FFFF).to_bytes(4, "little")
    ctx.ip = next_ip
    return None


def _h_f64_load(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    bits = int.from_bytes(env.memory[addr : addr + 8], "little")
    pushed = frame.values.push_i64(_to_i64(bits))
    assert pushed
    ctx.ip = next_ip
    return None


def _h_f64_store(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    offset, next_ip = _read_memarg(frame.code, instruction_ip)
    bits = frame.values.pop_i64()
    assert bits is not None
    addr = _to_u32(frame.values.pop_back()) + offset
    if env.memory is None or addr + 8 > len(env.memory):
        return Trap(TrapCode.MEMORY_OUT_OF_BOUNDS, addr)
    env.memory[addr : addr + 8] = _to_u64(bits).to_bytes(8, "little")
    ctx.ip = next_ip
    return None


# --- Const Handlers ---


def _h_i64_const(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    val, next_ip = decode_signed_full(frame.code, instruction_ip + 1, bits=64)
    pushed = frame.values.push_i64(_to_i64(val))
    assert pushed
    ctx.ip = next_ip
    return None


def _h_f32_const(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = int.from_bytes(frame.code[instruction_ip + 1 : instruction_ip + 5], "little")
    pushed = frame.values.push_back(bits)
    assert pushed
    ctx.ip = instruction_ip + 5
    return None


def _h_f64_const(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = int.from_bytes(frame.code[instruction_ip + 1 : instruction_ip + 9], "little")
    pushed = frame.values.push_i64(_to_i64(bits))
    assert pushed
    ctx.ip = instruction_ip + 9
    return None


# --- i64 Comparison & Arithmetic Handlers ---


def _h_i64_eqz(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    v = frame.values.pop_i64()
    assert v is not None
    frame.values.push_back(1 if v == 0 else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_eq(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) == _to_i64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_ne(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) != _to_i64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_lt_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) < _to_i64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_lt_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) < _to_u64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_add(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    pushed = frame.values.push_i64(_to_i64(a + b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_sub(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    pushed = frame.values.push_i64(_to_i64(a - b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_mul(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    pushed = frame.values.push_i64(_to_i64(a * b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_div_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
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
    pushed = frame.values.push_i64(_to_i64(-q if (a < 0) != (b < 0) else q))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_div_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_i64()
    a = frame.values.pop_i64()
    assert b is not None and a is not None
    b = _to_u64(b)
    a = _to_u64(a)
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    pushed = frame.values.push_i64(_to_i64(a // b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


# --- f32 Arithmetic Handlers ---


def _h_f32_add(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    pushed = frame.values.push_f32(_to_f32(a + b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_sub(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    pushed = frame.values.push_f32(_to_f32(a - b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_mul(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    pushed = frame.values.push_f32(_to_f32(a * b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_div(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    pushed = frame.values.push_f32(_to_f32(_wasm_float_div(a, b)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_sqrt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    pushed = frame.values.push_f32(_to_f32(math.sqrt(a) if a >= 0 else float("nan")))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_min(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    pushed = frame.values.push_f32(_to_f32(_f32_min(a, b)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_max(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    pushed = frame.values.push_f32(_to_f32(_f32_max(a, b)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_lt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_le(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_gt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_ge(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.ip = instruction_ip + 1
    return None


# --- f64 Arithmetic Handlers ---


def _h_f64_add(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    pushed = frame.values.push_f64(float(a + b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_sub(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    pushed = frame.values.push_f64(float(a - b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_mul(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    pushed = frame.values.push_f64(float(a * b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_div(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    pushed = frame.values.push_f64(_wasm_float_div(a, b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_sqrt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    pushed = frame.values.push_f64(float(math.sqrt(a) if a >= 0 else float("nan")))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


# --- Conversion Handlers ---


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


def _h_fc_prefix(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    env = frame.env
    subopcode, next_ip = decode_unsigned_full(frame.code, instruction_ip + 1)

    if FC_I32_TRUNC_SAT_F32_S <= subopcode <= FC_I32_TRUNC_SAT_F64_U:
        source_is_f32 = subopcode == 0 or subopcode == 1
        value = frame.values.pop_f32() if source_is_f32 else frame.values.pop_f64()
        converted = _truncate_saturating_float(value, 32, subopcode % 2 == 0)
        pushed = frame.values.push_i32(converted)
        assert pushed
        ctx.ip = next_ip
        return None

    if FC_I64_TRUNC_SAT_F32_S <= subopcode <= FC_I64_TRUNC_SAT_F64_U:
        source_is_f32 = subopcode == 4 or subopcode == 5
        value = frame.values.pop_f32() if source_is_f32 else frame.values.pop_f64()
        converted = _truncate_saturating_float(value, 64, subopcode % 2 == 0)
        pushed = frame.values.push_i64(converted)
        assert pushed
        ctx.ip = next_ip
        return None

    assert env is not None
    memory = env.memory
    if memory is None:
        return Trap(TrapCode.MEMORY_SECTION_MISSING)

    if subopcode == FC_MEMORY_COPY:
        destination_memory, next_ip = decode_unsigned_full(frame.code, next_ip)
        source_memory, next_ip = decode_unsigned_full(frame.code, next_ip)
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
    memory_index, next_ip = decode_unsigned_full(frame.code, next_ip)
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


def _h_i32_trunc_f32_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 32, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(converted))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_trunc_f64_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 32, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(converted))
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_trunc_f32_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 64, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    pushed = frame.values.push_i64(converted)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_trunc_f32_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    converted = _truncate_float(a, 64, False)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    pushed = frame.values.push_i64(converted)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_trunc_f64_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 64, True)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    pushed = frame.values.push_i64(converted)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_trunc_f64_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    converted = _truncate_float(a, 64, False)
    if converted is None:
        return Trap(TrapCode.INVALID_CONVERSION)
    pushed = frame.values.push_i64(converted)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_convert_i32_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = _to_i32(frame.values.pop_back())
    pushed = frame.values.push_f32(_to_f32(float(a)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_convert_i32_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = _to_u32(frame.values.pop_back())
    pushed = frame.values.push_f32(_to_f32(float(a)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_convert_i64_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_i64()
    assert a is not None
    pushed = frame.values.push_f32(_to_f32(float(a)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_convert_i64_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_i64()
    assert a is not None
    pushed = frame.values.push_f32(_to_f32(float(_to_u64(a))))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_convert_i32_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = _to_i32(frame.values.pop_back())
    pushed = frame.values.push_f64(float(a))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_convert_i32_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = _to_u32(frame.values.pop_back())
    pushed = frame.values.push_f64(float(a))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_convert_i64_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_i64()
    assert a is not None
    pushed = frame.values.push_f64(float(a))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_convert_i64_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_i64()
    assert a is not None
    pushed = frame.values.push_f64(float(_to_u64(a)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_promote_f32(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    pushed = frame.values.push_f64(float("nan") if math.isnan(a) else float(a))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_demote_f64(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    pushed = frame.values.push_f32(float("nan") if math.isnan(a) else _to_f32(float(a)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_abs(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_back()
    pushed = frame.values.push_back(bits & 0x7FFF_FFFF)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_neg(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_back()
    pushed = frame.values.push_back(bits ^ 0x8000_0000)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_abs(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_i64()
    assert bits is not None
    pushed = frame.values.push_i64(_to_i64(_to_u64(bits) & 0x7FFF_FFFF_FFFF_FFFF))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_neg(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_i64()
    assert bits is not None
    pushed = frame.values.push_i64(_to_i64(_to_u64(bits) ^ 0x8000_0000_0000_0000))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_eq(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_ne(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f32(), frame.values.pop_f32()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_eq(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a == b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_ne(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a != b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_rem_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_i64()
    a = frame.values.pop_i64()
    assert b is not None and a is not None
    b = _to_i64(b)
    a = _to_i64(a)
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    r = abs(a) % abs(b)
    pushed = frame.values.push_i64(_to_i64(-r if a < 0 else r))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_rem_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b = frame.values.pop_i64()
    a = frame.values.pop_i64()
    assert b is not None and a is not None
    b = _to_u64(b)
    a = _to_u64(a)
    if b == 0:
        return Trap(TrapCode.INTEGER_DIVIDE_BY_ZERO)
    pushed = frame.values.push_i64(_to_i64(a % b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_and(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    pushed = frame.values.push_i64(_to_i64(a & b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_or(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    pushed = frame.values.push_i64(_to_i64(a | b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_xor(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    pushed = frame.values.push_i64(_to_i64(a ^ b))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_shl(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    pushed = frame.values.push_i64(_to_i64((v << (k % 64)) & I64_MASK))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_shr_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    pushed = frame.values.push_i64(_to_i64(_to_i64(v) >> (k % 64)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_shr_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    pushed = frame.values.push_i64(_to_i64(_to_u64(v) >> (k % 64)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_rotl(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    k %= 64
    v = _to_u64(v)
    rotated = ((v << k) | (v >> (64 - k))) & I64_MASK if k else v
    pushed = frame.values.push_i64(_to_i64(rotated))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_rotr(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    k = frame.values.pop_i64()
    v = frame.values.pop_i64()
    assert k is not None and v is not None
    k %= 64
    v = _to_u64(v)
    rotated = ((v >> k) | (v << (64 - k))) & I64_MASK if k else v
    pushed = frame.values.push_i64(_to_i64(rotated))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_clz(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    if v == 0:
        pushed = frame.values.push_i64(64)
        assert pushed
    else:
        pushed = frame.values.push_i64(64 - v.bit_length())
        assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_ctz(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    if v == 0:
        pushed = frame.values.push_i64(64)
        assert pushed
    else:
        pushed = frame.values.push_i64((v & -v).bit_length() - 1)
        assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_popcnt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = frame.values.pop_i64()
    assert value is not None
    v = _to_u64(value)
    pushed = frame.values.push_i64(bin(v).count("1"))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_gt_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) > _to_i64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_gt_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) > _to_u64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_le_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) <= _to_i64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_le_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) <= _to_u64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_ge_s(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_i64(a) >= _to_i64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_ge_u(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    frame.values.push_back(1 if _to_u64(a) >= _to_u64(b) else 0)
    ctx.ip = instruction_ip + 1
    return None


# --- Additional F32 / F64 Math Handlers ---


def _h_f32_ceil(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    pushed = frame.values.push_f32(_to_f32(_wasm_integral_round(a, "ceil")))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_floor(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    pushed = frame.values.push_f32(_to_f32(_wasm_integral_round(a, "floor")))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_trunc(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    pushed = frame.values.push_f32(_to_f32(_wasm_integral_round(a, "trunc")))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_nearest(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    pushed = frame.values.push_f32(_to_f32(_wasm_nearest(a)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_copysign(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_back(), frame.values.pop_back()
    pushed = frame.values.push_back((a & 0x7FFF_FFFF) | (b & 0x8000_0000))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_ceil(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    pushed = frame.values.push_f64(_wasm_integral_round(a, "ceil"))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_floor(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    pushed = frame.values.push_f64(_wasm_integral_round(a, "floor"))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_trunc(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    pushed = frame.values.push_f64(_wasm_integral_round(a, "trunc"))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_nearest(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    pushed = frame.values.push_f64(_wasm_nearest(a))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_copysign(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_i64(), frame.values.pop_i64()
    assert b is not None and a is not None
    bits_a = _to_u64(a)
    bits_b = _to_u64(b)
    bits = (bits_a & 0x7FFF_FFFF_FFFF_FFFF) | (bits_b & 0x8000_0000_0000_0000)
    pushed = frame.values.push_i64(_to_i64(bits))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_min(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    if math.isnan(a) or math.isnan(b):
        res = float("nan")
    elif a == b == 0.0:
        res = -0.0 if math.copysign(1.0, a) < 0 or math.copysign(1.0, b) < 0 else 0.0
    else:
        res = min(a, b)
    pushed = frame.values.push_f64(float(res))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_max(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    if math.isnan(a) or math.isnan(b):
        res = float("nan")
    elif a == b == 0.0:
        res = 0.0 if math.copysign(1.0, a) > 0 or math.copysign(1.0, b) > 0 else -0.0
    else:
        res = max(a, b)
    pushed = frame.values.push_f64(float(res))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_lt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a < b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_le(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a <= b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_gt(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a > b else 0)
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_ge(ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    b, a = frame.values.pop_f64(), frame.values.pop_f64()
    assert b is not None and a is not None
    frame.values.push_back(1 if a >= b else 0)
    ctx.ip = instruction_ip + 1
    return None


# --- Conversion & Reinterpret Handlers ---


def _h_i32_extend8_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = _to_u32(frame.values.pop_back()) & 0xFF
    pushed = frame.values.push_back(_to_i32(value - 0x100 if value & 0x80 else value))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_extend16_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = _to_u32(frame.values.pop_back()) & 0xFFFF
    pushed = frame.values.push_back(_to_i32(value - 0x10000 if value & 0x8000 else value))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_extend8_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = _to_u64(frame.values.pop_i64()) & 0xFF
    pushed = frame.values.push_i64(_to_i64(value - 0x100 if value & 0x80 else value))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_extend16_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = _to_u64(frame.values.pop_i64()) & 0xFFFF
    pushed = frame.values.push_i64(_to_i64(value - 0x10000 if value & 0x8000 else value))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_extend32_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    value = _to_u64(frame.values.pop_i64()) & 0xFFFF_FFFF
    pushed = frame.values.push_i64(_to_i64(value - 0x1_0000_0000 if value & 0x8000_0000 else value))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_wrap_i64(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_i64()
    assert a is not None
    frame.values.push_back(_to_i32(a))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_trunc_f32_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f32()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(int(a)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_trunc_f64_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_f64()
    assert a is not None
    if math.isnan(a) or a <= -1.0 or a >= 4294967296.0:
        return Trap(TrapCode.INVALID_CONVERSION)
    frame.values.push_back(_to_i32(int(a)))
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_extend_i32_s(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_i32()
    assert a is not None
    pushed = frame.values.push_i64(_to_i64(a))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_extend_i32_u(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    a = frame.values.pop_i32()
    assert a is not None
    pushed = frame.values.push_i64(_to_i64(_to_u32(a)))
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i32_reinterpret_f32(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_back()
    pushed = frame.values.push_back(bits)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f32_reinterpret_i32(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_back()
    pushed = frame.values.push_back(bits)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_i64_reinterpret_f64(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_i64()
    assert bits is not None
    pushed = frame.values.push_i64(bits)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _h_f64_reinterpret_i64(
    ctx: ExecutionContext, ip: UInt32Pointer, sp: NativeValueStack
) -> _HandlerResult:
    frame = ctx.call_frame_stack[-1]
    instruction_ip = int(ip[0])
    bits = frame.values.pop_i64()
    assert bits is not None
    pushed = frame.values.push_i64(bits)
    assert pushed
    ctx.ip = instruction_ip + 1
    return None


def _build_opcode_dispatch() -> SequenceView[_HandlerFn | None]:
    """Freeze the dispatch catalog and release its mutable setup storage."""
    handlers = StaticVector[_HandlerFn | None](capacity=256)
    for _ in range(256):
        handlers.append(None)
    handlers[UNREACHABLE] = _h_unreachable
    handlers[NOP] = _h_nop
    handlers[BLOCK] = _h_block
    handlers[LOOP] = _h_loop
    handlers[IF] = _h_if
    handlers[ELSE] = _h_else
    handlers[END] = _h_end
    handlers[BR] = _h_br
    handlers[BR_IF] = _h_br_if
    handlers[BR_TABLE] = _h_br_table
    handlers[RETURN] = _h_return
    handlers[DROP] = _h_drop
    handlers[SELECT] = _h_select
    handlers[LOCAL_GET] = _h_local_get
    handlers[LOCAL_SET] = _h_local_set
    handlers[LOCAL_TEE] = _h_local_tee
    handlers[I32_CONST] = _h_i32_const
    handlers[GLOBAL_GET] = _h_global_get
    handlers[GLOBAL_SET] = _h_global_set
    handlers[I32_LOAD] = _h_i32_load
    handlers[I32_LOAD8_S] = _h_i32_load8_s
    handlers[I32_LOAD8_U] = _h_i32_load8_u
    handlers[I32_LOAD16_S] = _h_i32_load16_s
    handlers[I32_LOAD16_U] = _h_i32_load16_u
    handlers[I32_STORE] = _h_i32_store
    handlers[I32_STORE8] = _h_i32_store8
    handlers[I32_STORE16] = _h_i32_store16
    handlers[MEMORY_SIZE] = _h_memory_size
    handlers[MEMORY_GROW] = _h_memory_grow
    handlers[I32_EQZ] = _h_i32_eqz
    handlers[I32_EQ] = _h_i32_eq
    handlers[I32_NE] = _h_i32_ne
    handlers[I32_LT_S] = _h_i32_lt_s
    handlers[I32_LT_U] = _h_i32_lt_u
    handlers[I32_GT_S] = _h_i32_gt_s
    handlers[I32_GT_U] = _h_i32_gt_u
    handlers[I32_LE_S] = _h_i32_le_s
    handlers[I32_LE_U] = _h_i32_le_u
    handlers[I32_GE_S] = _h_i32_ge_s
    handlers[I32_GE_U] = _h_i32_ge_u
    handlers[I32_CLZ] = _h_i32_clz
    handlers[I32_CTZ] = _h_i32_ctz
    handlers[I32_POPCNT] = _h_i32_popcnt
    handlers[I32_ADD] = _h_i32_add
    handlers[I32_SUB] = _h_i32_sub
    handlers[I32_MUL] = _h_i32_mul
    handlers[I32_DIV_S] = _h_i32_div_s
    handlers[I32_DIV_U] = _h_i32_div_u
    handlers[I32_REM_S] = _h_i32_rem_s
    handlers[I32_REM_U] = _h_i32_rem_u
    handlers[I32_AND] = _h_i32_and
    handlers[I32_OR] = _h_i32_or
    handlers[I32_XOR] = _h_i32_xor
    handlers[I32_SHL] = _h_i32_shl
    handlers[I32_SHR_S] = _h_i32_shr_s
    handlers[I32_SHR_U] = _h_i32_shr_u
    handlers[I32_ROTL] = _h_i32_rotl
    handlers[I32_ROTR] = _h_i32_rotr
    handlers[I64_LOAD] = _h_i64_load
    handlers[I64_LOAD8_S] = _h_i64_load8_s
    handlers[I64_LOAD8_U] = _h_i64_load8_u
    handlers[I64_LOAD16_S] = _h_i64_load16_s
    handlers[I64_LOAD16_U] = _h_i64_load16_u
    handlers[I64_LOAD32_S] = _h_i64_load32_s
    handlers[I64_LOAD32_U] = _h_i64_load32_u
    handlers[I64_STORE] = _h_i64_store
    handlers[I64_STORE8] = _h_i64_store8
    handlers[I64_STORE16] = _h_i64_store16
    handlers[I64_STORE32] = _h_i64_store32
    handlers[F32_LOAD] = _h_f32_load
    handlers[F32_STORE] = _h_f32_store
    handlers[F64_LOAD] = _h_f64_load
    handlers[F64_STORE] = _h_f64_store
    handlers[I64_CONST] = _h_i64_const
    handlers[F32_CONST] = _h_f32_const
    handlers[F64_CONST] = _h_f64_const
    handlers[I64_EQZ] = _h_i64_eqz
    handlers[I64_EQ] = _h_i64_eq
    handlers[I64_NE] = _h_i64_ne
    handlers[I64_LT_S] = _h_i64_lt_s
    handlers[I64_LT_U] = _h_i64_lt_u
    handlers[I64_ADD] = _h_i64_add
    handlers[I64_SUB] = _h_i64_sub
    handlers[I64_MUL] = _h_i64_mul
    handlers[I64_DIV_S] = _h_i64_div_s
    handlers[I64_DIV_U] = _h_i64_div_u
    handlers[F32_ADD] = _h_f32_add
    handlers[F32_SUB] = _h_f32_sub
    handlers[F32_MUL] = _h_f32_mul
    handlers[F32_DIV] = _h_f32_div
    handlers[F32_SQRT] = _h_f32_sqrt
    handlers[F32_MIN] = _h_f32_min
    handlers[F32_MAX] = _h_f32_max
    handlers[F32_LT] = _h_f32_lt
    handlers[F32_LE] = _h_f32_le
    handlers[F32_GT] = _h_f32_gt
    handlers[F32_GE] = _h_f32_ge
    handlers[F64_ADD] = _h_f64_add
    handlers[F64_SUB] = _h_f64_sub
    handlers[F64_MUL] = _h_f64_mul
    handlers[F64_DIV] = _h_f64_div
    handlers[F64_SQRT] = _h_f64_sqrt
    handlers[FC_PREFIX] = _h_fc_prefix
    handlers[I32_TRUNC_F32_S] = _h_i32_trunc_f32_s
    handlers[I32_TRUNC_F64_S] = _h_i32_trunc_f64_s
    handlers[I64_TRUNC_F32_S] = _h_i64_trunc_f32_s
    handlers[I64_TRUNC_F32_U] = _h_i64_trunc_f32_u
    handlers[I64_TRUNC_F64_S] = _h_i64_trunc_f64_s
    handlers[I64_TRUNC_F64_U] = _h_i64_trunc_f64_u
    handlers[F32_CONVERT_I32_S] = _h_f32_convert_i32_s
    handlers[F32_CONVERT_I32_U] = _h_f32_convert_i32_u
    handlers[F32_CONVERT_I64_S] = _h_f32_convert_i64_s
    handlers[F32_CONVERT_I64_U] = _h_f32_convert_i64_u
    handlers[F64_CONVERT_I32_S] = _h_f64_convert_i32_s
    handlers[F64_CONVERT_I32_U] = _h_f64_convert_i32_u
    handlers[F64_CONVERT_I64_S] = _h_f64_convert_i64_s
    handlers[F64_CONVERT_I64_U] = _h_f64_convert_i64_u
    handlers[F64_PROMOTE_F32] = _h_f64_promote_f32
    handlers[F32_DEMOTE_F64] = _h_f32_demote_f64
    handlers[F32_ABS] = _h_f32_abs
    handlers[F32_NEG] = _h_f32_neg
    handlers[F64_ABS] = _h_f64_abs
    handlers[F64_NEG] = _h_f64_neg
    handlers[F32_EQ] = _h_f32_eq
    handlers[F32_NE] = _h_f32_ne
    handlers[F64_EQ] = _h_f64_eq
    handlers[F64_NE] = _h_f64_ne
    handlers[I64_REM_S] = _h_i64_rem_s
    handlers[I64_REM_U] = _h_i64_rem_u
    handlers[I64_AND] = _h_i64_and
    handlers[I64_OR] = _h_i64_or
    handlers[I64_XOR] = _h_i64_xor
    handlers[I64_SHL] = _h_i64_shl
    handlers[I64_SHR_S] = _h_i64_shr_s
    handlers[I64_SHR_U] = _h_i64_shr_u
    handlers[I64_ROTL] = _h_i64_rotl
    handlers[I64_ROTR] = _h_i64_rotr
    handlers[I64_CLZ] = _h_i64_clz
    handlers[I64_CTZ] = _h_i64_ctz
    handlers[I64_POPCNT] = _h_i64_popcnt
    handlers[I64_GT_S] = _h_i64_gt_s
    handlers[I64_GT_U] = _h_i64_gt_u
    handlers[I64_LE_S] = _h_i64_le_s
    handlers[I64_LE_U] = _h_i64_le_u
    handlers[I64_GE_S] = _h_i64_ge_s
    handlers[I64_GE_U] = _h_i64_ge_u
    handlers[F32_CEIL] = _h_f32_ceil
    handlers[F32_FLOOR] = _h_f32_floor
    handlers[F32_TRUNC] = _h_f32_trunc
    handlers[F32_NEAREST] = _h_f32_nearest
    handlers[F32_COPYSIGN] = _h_f32_copysign
    handlers[F64_CEIL] = _h_f64_ceil
    handlers[F64_FLOOR] = _h_f64_floor
    handlers[F64_TRUNC] = _h_f64_trunc
    handlers[F64_NEAREST] = _h_f64_nearest
    handlers[F64_COPYSIGN] = _h_f64_copysign
    handlers[F64_MIN] = _h_f64_min
    handlers[F64_MAX] = _h_f64_max
    handlers[F64_LT] = _h_f64_lt
    handlers[F64_LE] = _h_f64_le
    handlers[F64_GT] = _h_f64_gt
    handlers[F64_GE] = _h_f64_ge
    handlers[I32_EXTEND8_S] = _h_i32_extend8_s
    handlers[I32_EXTEND16_S] = _h_i32_extend16_s
    handlers[I64_EXTEND8_S] = _h_i64_extend8_s
    handlers[I64_EXTEND16_S] = _h_i64_extend16_s
    handlers[I64_EXTEND32_S] = _h_i64_extend32_s
    handlers[I32_WRAP_I64] = _h_i32_wrap_i64
    handlers[I32_TRUNC_F32_U] = _h_i32_trunc_f32_u
    handlers[I32_TRUNC_F64_U] = _h_i32_trunc_f64_u
    handlers[I64_EXTEND_I32_S] = _h_i64_extend_i32_s
    handlers[I64_EXTEND_I32_U] = _h_i64_extend_i32_u
    handlers[I32_REINTERPRET_F32] = _h_i32_reinterpret_f32
    handlers[F32_REINTERPRET_I32] = _h_f32_reinterpret_i32
    handlers[I64_REINTERPRET_F64] = _h_i64_reinterpret_f64
    handlers[F64_REINTERPRET_I64] = _h_f64_reinterpret_i64
    return freeze_sequence(handlers)


_HANDLERS = _build_opcode_dispatch()

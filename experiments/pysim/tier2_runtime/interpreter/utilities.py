"""Low-level WASM value and immediate helpers shared by the interpreter."""

from __future__ import annotations

import math
import struct

from system_containers import SequenceView as Sequence
from tier2_runtime.wasm.leb128 import decode_unsigned

I32_MASK = 0xFFFFFFFF


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


def _read_memarg(code: Sequence[int], ip: int) -> tuple[int, int]:
    align, off = decode_unsigned(code, ip + 1)
    mem_offset, next_ip = decode_unsigned(code, off)
    return mem_offset, next_ip


I64_MASK = 0xFFFF_FFFF_FFFF_FFFF


def _to_i64(v: int) -> int:
    v &= I64_MASK
    return v - (1 << 64) if v & 0x8000_0000_0000_0000 else v


def _to_u64(v: int) -> int:
    return v & I64_MASK


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

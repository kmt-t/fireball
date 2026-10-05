"""Concept model for linear-memory CPU operations and saturating conversions."""

import math
from typing import Literal

BACKS = ["components/tier2_runtime/interpreter.md", "specs/wasm_instruction_set.md"]


class WASMTrap(Exception):
    """A trap required by the WASM instruction semantics."""


def _u32(value: int) -> int:
    if value < -(1 << 31) or value > 0xFFFF_FFFF:
        raise WASMTrap("invalid i32 bit pattern")
    return value & 0xFFFF_FFFF


def _check_range(memory_size: int, offset: int, length: int) -> None:
    """Check an unsigned wasm32 range without computing an overflowing sum."""
    if offset > memory_size or length > memory_size - offset:
        raise WASMTrap("out of bounds linear memory access")


def memory_copy(
    memory: bytearray,
    destination: int,
    source: int,
    length: int,
) -> Literal["cpu"]:
    """Implement memory.copy; all bounds are checked before any mutation."""
    destination = _u32(destination)
    source = _u32(source)
    length = _u32(length)
    _check_range(len(memory), destination, length)
    _check_range(len(memory), source, length)

    if length == 0 or destination == source:
        return "cpu"

    overlaps = destination < source + length and source < destination + length
    if destination > source and overlaps:
        for index in range(length - 1, -1, -1):
            memory[destination + index] = memory[source + index]
    else:
        for index in range(length):
            memory[destination + index] = memory[source + index]
    return "cpu"


def memory_fill(memory: bytearray, destination: int, value: int, length: int) -> None:
    """Implement memory.fill using the low byte of the i32 value."""
    destination = _u32(destination)
    value = _u32(value)
    length = _u32(length)
    _check_range(len(memory), destination, length)
    byte_value = value & 0xFF
    for index in range(length):
        memory[destination + index] = byte_value


def trunc_sat_signed(value: float, bits: int) -> int:
    """Saturating float-to-signed conversion with truncation toward zero."""
    if bits not in (32, 64):
        raise ValueError("destination width must be 32 or 64")
    if math.isnan(value):
        return 0

    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    if value <= minimum:
        return minimum
    if value >= 1 << (bits - 1):
        return maximum
    return math.trunc(value)


def trunc_sat_unsigned(value: float, bits: int) -> int:
    """Saturating float-to-unsigned conversion with truncation toward zero."""
    if bits not in (32, 64):
        raise ValueError("destination width must be 32 or 64")
    if math.isnan(value) or value <= 0.0:
        return 0

    maximum = (1 << bits) - 1
    if value >= 1 << bits:
        return maximum
    return math.trunc(value)


def _check_concept_examples() -> None:
    memory = bytearray(range(8))
    memory_copy(memory, 2, 0, 6)
    assert memory == bytearray([0, 1, 0, 1, 2, 3, 4, 5])

    memory = bytearray(range(8))
    memory_copy(memory, 0, 2, 6)
    assert memory == bytearray([2, 3, 4, 5, 6, 7, 6, 7])

    memory = bytearray(4)
    memory_fill(memory, 1, 0x1234_ABCD, 2)
    assert memory == bytearray([0, 0xCD, 0xCD, 0])

    memory = bytearray(2)
    memory_fill(memory, 0, -1, 2)
    assert memory == bytearray([0xFF, 0xFF])

    memory = bytearray(range(16))
    assert memory_copy(memory, 8, 0, 8) == "cpu"
    assert memory == bytearray(list(range(8)) + list(range(8)))

    assert trunc_sat_signed(float("nan"), 32) == 0
    assert trunc_sat_signed(float("inf"), 32) == (1 << 31) - 1
    assert trunc_sat_signed(float("-inf"), 64) == -(1 << 63)
    assert trunc_sat_unsigned(-0.5, 32) == 0
    assert trunc_sat_unsigned(float("inf"), 64) == (1 << 64) - 1
    assert trunc_sat_signed(-1.9, 32) == -1

    memory = bytearray([7, 8, 9, 10])
    try:
        memory_copy(memory, 3, 0, 2)
    except WASMTrap:
        assert memory == bytearray([7, 8, 9, 10])
    else:
        raise AssertionError("out-of-bounds copy did not trap")

    try:
        memory_copy(memory, -1, 0, 0)
    except WASMTrap:
        assert memory == bytearray([7, 8, 9, 10])
    else:
        raise AssertionError("out-of-bounds zero-length copy did not trap")


if __name__ == "__main__":
    _check_concept_examples()

"""Bounded LEB128 decoding for the ROM-backed WASM binary reader."""

from __future__ import annotations

from system_containers import SequenceView


def decode_unsigned(
    data: SequenceView[int],
    offset: int,
    end: int | None = None,
    bits: int = 32,
) -> tuple[int, int]:
    """Decode a bounded unsigned LEB128 integer and return (value, new_offset)."""
    limit = len(data) if end is None else end
    assert bits == 32 or bits == 64, "unsigned LEB128 width must be 32 or 64 bits"
    assert 0 <= offset <= limit <= len(data), "invalid unsigned LEB128 bounds"
    result = 0
    shift = 0
    max_bytes = (bits + 6) // 7
    for _ in range(max_bytes):
        assert offset < limit, "truncated unsigned LEB128 integer"
        byte = data[offset]
        offset += 1
        payload = byte & 0x7F
        remaining_bits = bits - shift
        if remaining_bits < 7:
            assert payload < (1 << remaining_bits), "unsigned LEB128 integer exceeds width"
        result |= payload << shift
        if byte & 0x80 == 0:
            return result, offset
        shift += 7
    assert False, f"unsigned LEB128 integer exceeds maximum {max_bytes} bytes"


def decode_signed(
    data: SequenceView[int],
    offset: int,
    end: int | None = None,
    bits: int = 32,
) -> tuple[int, int]:
    """Decode a bounded signed LEB128 integer and return (value, new_offset)."""
    limit = len(data) if end is None else end
    assert bits == 32 or bits == 64, "signed LEB128 width must be 32 or 64 bits"
    assert 0 <= offset <= limit <= len(data), "invalid signed LEB128 bounds"
    result = 0
    shift = 0
    max_bytes = (bits + 6) // 7
    for _ in range(max_bytes):
        assert offset < limit, "truncated signed LEB128 integer"
        byte = data[offset]
        offset += 1
        payload = byte & 0x7F
        remaining_bits = bits - shift
        contributed_bits = min(7, remaining_bits)
        sign_bit = (payload >> (contributed_bits - 1)) & 1
        if remaining_bits < 7:
            unused_bits = payload >> contributed_bits
            expected_unused = (1 << (7 - contributed_bits)) - 1 if sign_bit else 0
            assert unused_bits == expected_unused, "signed LEB128 integer exceeds width"
            payload &= (1 << contributed_bits) - 1
        result |= payload << shift
        if byte & 0x80 == 0:
            consumed_bits = shift + contributed_bits
            if sign_bit:
                result |= -(1 << consumed_bits)
            minimum = -(1 << (bits - 1))
            maximum = (1 << (bits - 1)) - 1
            assert minimum <= result <= maximum, "signed LEB128 integer exceeds width"
            return result, offset
        assert remaining_bits > 7, f"signed LEB128 integer exceeds maximum {max_bytes} bytes"
        shift += 7
    assert False, f"signed LEB128 integer exceeds maximum {max_bytes} bytes"

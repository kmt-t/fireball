"""FNV-1a hash used for bounded URI and symbol lookup keys."""

from __future__ import annotations


def fnv1a_32(data: str) -> int:
    """Return the 32-bit FNV-1a hash of a UTF-8 string."""
    value = 0x811C9DC5
    for byte in data.encode("utf-8"):
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value

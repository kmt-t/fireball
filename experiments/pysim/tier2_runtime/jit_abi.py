"""Shared x64 JIT execution-context ABI constants."""

from __future__ import annotations

JIT_LOOP_JUMP_COUNT_OFFSET_BYTES: int = 0x74
JIT_LOOP_JUMP_THRESHOLD_OFFSET_BYTES: int = 0x78
JIT_CONTEXT_SIZE_BYTES: int = 128

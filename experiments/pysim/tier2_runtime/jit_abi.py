"""Shared x64 JIT execution-context ABI constants.

The Tier 2 runtime owns this boundary.  The Tier 3 JIT only consumes it:
the context is the only data channel used to select a complex-operation
helper, and JIT code never embeds the process address in its PIC stream.
"""

from __future__ import annotations

JIT_CONTEXT_WORD_BYTES: int = 8
JIT_CONTEXT_HELPER_PTR_WORD: int = 8
JIT_CONTEXT_HELPER_PTR_OFFSET: int = (
    JIT_CONTEXT_HELPER_PTR_WORD * JIT_CONTEXT_WORD_BYTES
)
JIT_CONTEXT_SIZE_BYTES: int = 9 * JIT_CONTEXT_WORD_BYTES

assert JIT_CONTEXT_HELPER_PTR_OFFSET + JIT_CONTEXT_WORD_BYTES <= JIT_CONTEXT_SIZE_BYTES


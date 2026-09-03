"""
experiments/pysim/tests/test_support.py
Test-only helpers that have no business shipping inside the production
runtime modules (runtime_engine.py, x64_jit.py, ...) -- kept in their own
module so nothing test-specific is importable from, or bloats, the actual
simulated-runtime code path.
"""

from __future__ import annotations

from collections.abc import Callable

import wasmtime
from runtime_engine import BasicBlock, JITTrace, TraceBlock


def wat_to_wasm(wat_text: str) -> bytes:
    """Compiles WAT source to a real WASM binary via wasmtime -- the standard
    way this test suite feeds real bytecode into `RuntimeEngine.load_wasm` /
    `IntegratedHybridEngine.load_wasm` instead of hand-building op tuples."""
    return bytes(wasmtime.wat2wasm(wat_text))


class PcOnlyCompiler:
    """
    Adapts a simple `(pc) -> JITTrace | None` callable into the
    `compile_trace(pc, block)` shape `RuntimeEngine.idle_hook()` always
    calls, so idle_hook never has to branch on what shape `jit_compiler` is.
    For tests exercising the cache/bitmap/compile-queue machinery in
    isolation from real JIT compilation -- it never inspects `block`.
    """

    def __init__(self, fn: Callable[[int], JITTrace | None]):
        self._fn = fn

    def compile_trace(self, pc: int, block: BasicBlock | TraceBlock | None) -> JITTrace | None:
        return self._fn(pc)

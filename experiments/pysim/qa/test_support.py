"""
experiments/pysim/qa/test_support.py
Test-only helpers that have no business shipping inside the production
runtime modules (runtime_engine.py, x64_jit.py, ...) -- kept in their own
module so nothing test-specific is importable from, or bloats, the actual
simulated-runtime code path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from control_flow import iter_block_ops
from runtime_engine import BasicBlock, JITTrace
from system_containers import (
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    bswap32,
    build_radix_table,
)
from wasm_module import Function, FuncType, Module, WasmOperand
from x64_jit import TraceCompiler


class PcOnlyCompiler:
    """
    Adapts a simple `(pc) -> JITTrace | None` callable into the complete
    compile interface used by `RuntimeEngine.idle_hook()`. The test double
    deliberately receives the real caller-provided metadata even though the
    cache-only tests do not inspect it.
    """

    def __init__(self, fn: Callable[[int], JITTrace | None]):
        self._fn = fn

    def compile_trace(
        self,
        pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_span: int,
        local_widths: Sequence[int],
    ) -> JITTrace | None:
        return self._fn(pc)


def compile_test_block(
    compiler: TraceCompiler,
    code: bytes,
    block: BasicBlock,
    local_widths: Sequence[int],
) -> JITTrace | None:
    """Test-only adapter that prepares the production compiler's full inputs."""

    return compiler.compile_trace(
        block.head_pc,
        iter_block_ops(code, block.head_pc & 0xFFFF, block.byte_span),
        block.next_pc,
        block.loops_to,
        block.byte_span,
        local_widths,
    )


def compile_module_block(
    compiler: TraceCompiler, module: Module, block: BasicBlock
) -> JITTrace | None:
    """Test-only adapter deriving local metadata from the loaded function."""

    function_index = block.head_pc >> 16
    function = module.functions[function_index - len(module.imports)]
    assert function.local_widths_cache is not None
    return compile_test_block(compiler, module.code_for(function_index), block, function.local_widths_cache)


def make_pc_only_module(pcs: tuple[int, ...]) -> Module:
    """Build test-only loader metadata for cache tests with synthetic PCs."""

    assert pcs
    assert max(pcs) < 0x1_0000
    code = bytes(max(pcs) + 1)
    module = Module(
        types=(FuncType(params=(), results=()),),
        functions=(Function(type_index=0, locals_extra=(), code=code),),
    )
    blocks = StaticVector.of(
        tuple(
            BasicBlock(
                head_pc=pc,
                next_pc=pc + 1,
                loops_to=None,
                frame_depth=0,
                byte_span=1,
                jit_score=0,
            )
            for pc in pcs
        ),
        capacity=len(pcs),
    )
    inverse_keys = tuple(bswap32(block.head_pc) for block in blocks)
    module.blocks = blocks
    module.block_storage = ReadOnlyRadixBinaryTreeStorage(
        keys=inverse_keys,
        values=blocks,
        radix_table=build_radix_table(inverse_keys, radix_shift=28),
        radix_shift=28,
        entries=tuple(zip(inverse_keys, blocks, strict=True)),
    )
    return module

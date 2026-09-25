"""
experiments/pysim/qa/test_support.py
Test-only helpers that have no business shipping inside the production
runtime modules (runtime_engine.py, x64_jit.py, ...) -- kept in their own
module so nothing test-specific is importable from, or bloats, the actual
simulated-runtime code path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from config import (
    FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    FB_CONF_JIT_AGING_STEP_UNITS,
    JIT_CARD_SHIFT,
)
from control_flow import iter_block_ops
from jit_scoring import JIT_CANDIDATE_THRESHOLD
from system_containers import (
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    build_radix_table,
    fold_mix32,
)
from tier3_executer.jit.jit_cache import JITTrace
from tier3_executer.jit.jit_manager import JITCompiler, JITRuntimeManager
from tier3_executer.jit.runtime_engine import RuntimeDriveMode, RuntimeEngine
from tier3_executer.jit.x64_jit import TraceCompiler
from wasm_module import BasicBlock, Function, FuncType, LocalWidthMap, Module, WasmOperand


def make_runtime_engine(
    jit_compiler: JITCompiler | None = None,
    debug: bool = False,
    yield_threshold: int = 16,
    card_shift: int = JIT_CARD_SHIFT,
    code_lengths: Sequence[int] = (),
    min_trace_bytes: int | None = None,
    candidate_threshold: int = JIT_CANDIDATE_THRESHOLD,
    compile_queue_capacity: int = 4,
    aging_step_units: int = FB_CONF_JIT_AGING_STEP_UNITS,
    aging_scan_bytes: int = FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    drive_mode: RuntimeDriveMode = RuntimeDriveMode.SYNCHRONOUS,
    collect_runtime_stats: bool = True,
) -> RuntimeEngine:
    """Compose a Tier 3 runtime engine with the Tier 3 JIT manager for tests."""

    if (
        jit_compiler is None
        and not code_lengths
        and yield_threshold == 16
        and card_shift == JIT_CARD_SHIFT
        and min_trace_bytes is None
        and candidate_threshold == JIT_CANDIDATE_THRESHOLD
        and compile_queue_capacity == 4
        and aging_step_units == FB_CONF_JIT_AGING_STEP_UNITS
        and aging_scan_bytes == FB_CONF_JIT_AGING_STEP_SCAN_BYTES
        and drive_mode == RuntimeDriveMode.SYNCHRONOUS
    ):
        return RuntimeEngine(debug=debug, collect_runtime_stats=collect_runtime_stats)
    manager = JITRuntimeManager(
        jit_compiler=jit_compiler,
        yield_threshold=yield_threshold,
        card_shift=card_shift,
        code_lengths=code_lengths,
        min_trace_bytes=min_trace_bytes,
        candidate_threshold=candidate_threshold,
        compile_queue_capacity=compile_queue_capacity,
        aging_step_units=aging_step_units,
        aging_scan_bytes=aging_scan_bytes,
    )
    return RuntimeEngine(
        jit_runtime=manager,
        debug=debug,
        drive_mode=drive_mode,
        collect_runtime_stats=collect_runtime_stats,
    )


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
        local_types: Sequence[int],
        *,
        loop_backedge_kind: int = 0,
    ) -> JITTrace | None:
        return self._fn(pc)


def compile_test_block(
    compiler: TraceCompiler,
    code: bytes,
    block: BasicBlock,
    local_types: Sequence[int],
) -> JITTrace | None:
    """Test-only adapter: `local_types` are the locals' value types, params first."""

    return compiler.compile_trace(
        block.head_pc,
        iter_block_ops(code, block.head_pc & 0xFFFF, block.byte_span),
        block.next_pc,
        block.loops_to,
        block.byte_span,
        LocalWidthMap(local_types),
    )


def compile_module_block(
    compiler: TraceCompiler, module: Module, block: BasicBlock
) -> JITTrace | None:
    """Test-only adapter deriving local metadata from the loaded function."""

    function_index = block.head_pc >> 16
    function = module.functions[function_index - len(module.imports)]
    assert function.local_width_map_cache is not None
    return compiler.compile_trace(
        block.head_pc,
        iter_block_ops(module.code_for(function_index), block.head_pc & 0xFFFF, block.byte_span),
        block.next_pc,
        block.loops_to,
        block.byte_span,
        function.local_width_map_cache,
    )


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
    module.blocks = blocks
    # block_storage's own arrays are sorted by the radix key -- independent
    # of `blocks`' caller-given order, matching wasm_module.py's real
    # construction (see build_basic_block_index).
    radix_sorted = tuple(sorted(blocks, key=lambda block: fold_mix32(block.head_pc)))
    inverse_keys = tuple(fold_mix32(block.head_pc) for block in radix_sorted)
    module.block_storage = ReadOnlyRadixBinaryTreeStorage(
        keys=inverse_keys,
        values=radix_sorted,
        radix_table=build_radix_table(inverse_keys, radix_shift=28),
        radix_shift=28,
        entries=tuple(zip(inverse_keys, radix_sorted, strict=True)),
    )
    return module


def make_pc_only_functions_module(blocks_per_function: tuple[tuple[int, ...], ...]) -> Module:
    """Build test-only loader metadata for tests that need several functions.

    Function `f` owns one block at every code offset in `blocks_per_function[f]`;
    the tuple may be empty (a function without blocks).
    """

    assert blocks_per_function
    functions = []
    heads = []
    for func_index, offsets in enumerate(blocks_per_function):
        assert all(offset < 0x1_0000 for offset in offsets)
        functions.append(
            Function(type_index=0, locals_extra=(), code=bytes(max(offsets, default=0) + 1))
        )
        heads.extend((func_index << 16) | offset for offset in offsets)
    assert heads
    module = Module(
        types=(FuncType(params=(), results=()),),
        functions=tuple(functions),
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
            for pc in sorted(heads)
        ),
        capacity=len(heads),
    )
    module.blocks = blocks
    radix_sorted = tuple(sorted(blocks, key=lambda block: fold_mix32(block.head_pc)))
    inverse_keys = tuple(fold_mix32(block.head_pc) for block in radix_sorted)
    module.block_storage = ReadOnlyRadixBinaryTreeStorage(
        keys=inverse_keys,
        values=radix_sorted,
        radix_table=build_radix_table(inverse_keys, radix_shift=28),
        radix_shift=28,
        entries=tuple(zip(inverse_keys, radix_sorted, strict=True)),
    )
    return module

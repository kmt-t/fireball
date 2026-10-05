"""Shared JIT/runtime test adapters; kept outside every product tier."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from config import (
    FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    FB_CONF_JIT_AGING_STEP_UNITS,
    FB_CONF_RUNTIME_YIELD_THRESHOLD,
    JIT_CARD_SHIFT,
)
from system_containers import (
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    fold_mix32,
)
from tier2_runtime.interpreter.control_flow import iter_block_ops
from tier2_runtime.runtime.engine import RuntimeDriveMode, RuntimeEngine
from tier2_runtime.wasm.jit_scoring import JIT_CANDIDATE_THRESHOLD
from tier2_runtime.wasm.module import BasicBlock, Function, FuncType, LocalWidthMap, Module
from tier3_plugins.jit.jit_cache import JitRuntimeBoundary, JITTrace
from tier3_plugins.jit.jit_manager import JITCompiler, JITRuntimeManager
from tier3_plugins.jit.x64_jit import TraceCompiler

PC_ONLY_FUNCTION_STRIDE = 0x2000
PC_ONLY_FUNCTION_BASE = 0x100


def make_runtime_engine(
    jit_compiler: JITCompiler | None = None,
    debug: bool = False,
    yield_threshold: int = FB_CONF_RUNTIME_YIELD_THRESHOLD,
    card_shift: int = JIT_CARD_SHIFT,
    code_lengths: Sequence[int] = (),
    min_trace_bytes: int | None = None,
    candidate_threshold: int = JIT_CANDIDATE_THRESHOLD,
    compile_queue_capacity: int = 4,
    aging_step_units: int = FB_CONF_JIT_AGING_STEP_UNITS,
    aging_scan_bytes: int = FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    drive_mode: RuntimeDriveMode = RuntimeDriveMode.SYNCHRONOUS,
    collect_runtime_stats: bool = True,
    hotspot_profiling_enabled: bool = True,
    retire_observer: Callable[[int, int], None] | None = None,
) -> RuntimeEngine:
    """Compose a Tier 3 runtime engine with the Tier 3 JIT manager for tests."""

    if (
        jit_compiler is None
        and not code_lengths
        and yield_threshold == FB_CONF_RUNTIME_YIELD_THRESHOLD
        and card_shift == JIT_CARD_SHIFT
        and min_trace_bytes is None
        and candidate_threshold == JIT_CANDIDATE_THRESHOLD
        and compile_queue_capacity == 4
        and aging_step_units == FB_CONF_JIT_AGING_STEP_UNITS
        and aging_scan_bytes == FB_CONF_JIT_AGING_STEP_SCAN_BYTES
        and drive_mode == RuntimeDriveMode.SYNCHRONOUS
        and hotspot_profiling_enabled
        and retire_observer is None
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
        hotspot_profiling_enabled=hotspot_profiling_enabled,
        retire_observer=retire_observer,
    )
    return RuntimeEngine(
        jit_runtime=manager,
        debug=debug,
        drive_mode=drive_mode,
        collect_runtime_stats=collect_runtime_stats,
    )


class RecordingTraceCompiler(TraceCompiler):
    """Observe the native compiler's queue order without replacing compilation."""

    __slots__ = ("compiled_pcs",)

    def __init__(self) -> None:
        super().__init__()
        self.compiled_pcs: list[int] = []
        self.compile_observer = self._observe_compile

    def _observe_compile(self, pc: int, success: bool, elapsed_ns: int) -> None:
        if success:
            self.compiled_pcs.append(pc)


def compile_test_block(
    compiler: TraceCompiler,
    code: bytes,
    block: BasicBlock,
    local_types: Sequence[int],
) -> JITTrace | None:
    """Test-only adapter: `local_types` are the locals' value types, params first."""

    return compiler.compile_instructions(
        head_pc=block.head_pc,
        instructions=iter_block_ops(code, block.head_pc, block.byte_span),
        next_pc=block.next_pc,
        loops_to=block.loops_to,
        byte_length=block.byte_span,
        local_layout=LocalWidthMap(local_types),
    )


def compile_module_block(
    compiler: TraceCompiler, module: Module, block: BasicBlock
) -> JITTrace | None:
    """Test-only adapter deriving local metadata from the loaded function."""

    function_index = block.func_index
    function = module.functions[function_index - len(module.imports)]
    assert function.local_width_map_cache is not None
    return compiler.compile_instructions(
        head_pc=block.head_pc,
        instructions=iter_block_ops(
            module.code_for(function_index),
            block.head_pc - module.function_pc_offset(function_index),
            block.byte_span,
        ),
        next_pc=block.next_pc,
        loops_to=block.loops_to,
        byte_length=block.byte_span,
        local_layout=function.local_width_map_cache,
    )


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
            Function(
                type_index=0,
                locals_extra=(),
                code=bytes(max(offsets, default=0) + 1),
                code_pc_offset=PC_ONLY_FUNCTION_BASE + func_index * PC_ONLY_FUNCTION_STRIDE,
            )
        )
        heads.extend((func_index, offset) for offset in offsets)
    assert heads
    module = Module(
        types=(FuncType(params=(), results=()),),
        functions=tuple(functions),
    )
    blocks = StaticVector.of(
        tuple(
            BasicBlock(
                head_pc=module.function_pc_offset(func_index) + offset,
                func_index=func_index,
                next_pc=module.function_pc_offset(func_index) + offset + 1,
                loops_to=None,
                frame_depth=0,
                byte_span=1,
                jit_score=0,
            )
            for func_index, offset in sorted(heads)
        ),
        capacity=len(heads),
    )
    module.blocks = blocks
    radix_sorted = tuple(sorted(blocks, key=lambda block: fold_mix32(block.head_pc)))
    entries = tuple((fold_mix32(block.head_pc), block) for block in radix_sorted)
    module.block_storage = ReadOnlyRadixBinaryTreeStorage.from_sorted_entries(
        entries,
        radix_shift=28,
    )
    return module


def compile_runtime_block(manager: JITRuntimeManager, block: BasicBlock) -> JITTrace | None:
    """Prepare a Loader block for the standalone compiler in explicit cache tests."""
    assert manager.jit_compiler is not None
    return TraceCompiler.compile_wasm(manager._block_input(block.head_pc, block))


def resident_module_traces(
    cache: JitRuntimeBoundary, module: Module
) -> tuple[tuple[int, JITTrace], ...]:
    """Observe each known Loader PC through the external trace boundary."""
    result = []
    for block in module.blocks:
        trace = cache.find_trace(block.head_pc)
        if trace is not None:
            result.append((block.head_pc, trace))
    assert len(result) == cache.resident_count
    return tuple(result)

"""Shared JIT/runtime test adapters; kept outside every product tier."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from bump_allocator import BumpAllocator
from qa.private import jit_native_abi as native_abi
from qa.shared.jit_cache import JitRuntimeBoundary, JITTrace
from qa.shared.jit_manager import JITCompiler, JITRuntimeManager
from qa.shared.runtime_stats import RuntimeStatsEngine
from qa.shared.x64_jit import TraceCompiler
from system_containers import (
    fold_mix32,
)
from tier2_runtime.abi.native_abi import BufferLease
from tier2_runtime.runtime.engine import RuntimeDriveMode
from tier2_runtime.wasm.module import (
    BasicBlock,
    Module,
)


def make_runtime_engine(
    jit_compiler: JITCompiler | None = None,
    debug: bool = False,
    code_lengths: Sequence[int] = (),
    drive_mode: RuntimeDriveMode = RuntimeDriveMode.SYNCHRONOUS,
    collect_runtime_stats: bool = True,
    hotspot_profiling_enabled: bool = True,
    retire_observer: Callable[[int, int], None] | None = None,
    bump_allocator: BumpAllocator | None = None,
) -> RuntimeStatsEngine:
    """Compose a Tier 3 runtime engine with the Tier 3 JIT manager for tests.

    The JIT manager always runs with the product configuration; only the presence of a
    compiler, the code lengths and the observers select a manager.
    """

    if (
        jit_compiler is None
        and not code_lengths
        and drive_mode == RuntimeDriveMode.SYNCHRONOUS
        and hotspot_profiling_enabled
        and retire_observer is None
    ):
        return RuntimeStatsEngine(
            debug=debug, collect_runtime_stats=collect_runtime_stats, bump_allocator=bump_allocator
        )
    manager = JITRuntimeManager(
        jit_compiler=jit_compiler,
        code_lengths=code_lengths,
        hotspot_profiling_enabled=hotspot_profiling_enabled,
        retire_observer=retire_observer,
    )
    return RuntimeStatsEngine(
        jit_runtime=manager,
        debug=debug,
        drive_mode=drive_mode,
        collect_runtime_stats=collect_runtime_stats,
        bump_allocator=bump_allocator,
    )


def compile_module_block(
    compiler: TraceCompiler, module: Module, block: BasicBlock
) -> JITTrace | None:
    """Compile one loader block with its complete native ABI metadata."""

    assert block.byte_span > 0, "a direct JIT body must contain instructions"
    assert module.block_storage is not None
    function_index = block.func_index
    function = module.functions[function_index - len(module.imports)]
    assert function.local_layout_cache is not None
    annotations = function.interpreter_annotation_pool
    block_index = module.block_storage.view().find(fold_mix32(block.head_pc))
    assert block_index is not None
    assert module.blocks[block_index].head_pc == block.head_pc
    code = module.code_for(function_index)
    code_lease = BufferLease(memoryview(code))
    layout = function.local_layout_cache
    module.build_native_global_widths()
    return compiler.compile_wasm_standalone(
        native_abi.WasmBlock(
            head_pc=block.head_pc,
            code=native_abi.NativeByteView(code_lease.address, len(code)),
            offset=block.head_pc - module.function_pc_offset(function_index),
            byte_length=block.byte_span,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            frame_depth=block.frame_depth,
            locals=native_abi.NativeLocalLayout(
                offsets=native_abi.NativeByteView(layout.offsets_address, layout.count * 2),
                types=native_abi.NativeByteView(layout.types_address, layout.count),
                local_count=layout.count,
                total_words=layout.total_words,
            ),
            block_index=block_index,
            next_block_index=block.next_block_index,
            loops_to_block_index=block.loops_to_block_index,
            interpreter_annotations=native_abi.NativeByteView(
                0 if annotations is None else annotations.native_address,
                0 if annotations is None else len(annotations),
            ),
            interpreter_annotation_byte_offset=block.interpreter_annotation_byte_offset,
            entry_stack_words=block.entry_stack_words,
            global_widths=native_abi.NativeByteView(
                module.global_widths_address, len(module.globals)
            ),
        )
    )


def instruction_blocks(module: Module) -> tuple[BasicBlock, ...]:
    """Return nonempty blocks, excluding loader entries for structural boundaries."""

    blocks = tuple(block for block in module.blocks if block.byte_span > 0)
    assert blocks, "module has no nonempty basic blocks"
    return blocks


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

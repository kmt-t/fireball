"""
Test-only compatibility engine for the retired block-at-a-time API.

The production runtime is ``tier2_runtime.runtime_engine.RuntimeEngine``.
This helper remains only for tests and debugger scenarios that still exercise
the old ``WASMContext``/``run_step`` harness while those tests are migrated.
"""

from __future__ import annotations

from collections.abc import Callable

from config import JIT_CARD_SHIFT
from control_flow import iter_block_ops
from execution_context import WASMContext
from runtime_engine import (
    BasicBlock,
    BlockCardMask,
    CardState,
    HistoryRing,
    HotspotBitmap,
    JITMultiBufferCache,
    JITTrace,
    _Debugger,
    _JitCompiler,
    _module_code_lengths,
)
from system_containers import StaticVector
from wasm_module import Module, WasmOperand
from wasm_opcodes import (
    I32_ADD,
    I32_CONST,
    I32_MUL,
    I32_SUB,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)
from x64_jit import TraceCompiler


def _interp_i32_const(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    ctx.push(arg)


def _interp_i32_add(ctx: WASMContext, _arg: WasmOperand) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a + b) & 0xFFFF_FFFF)


def _interp_i32_sub(ctx: WASMContext, _arg: WasmOperand) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a - b) & 0xFFFF_FFFF)


def _interp_i32_mul(ctx: WASMContext, _arg: WasmOperand) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a * b) & 0xFFFF_FFFF)


def _interp_local_get(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    ctx.push(ctx.locals[arg])


def _interp_local_set(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    ctx.locals[arg] = ctx.pop()


def _interp_local_tee(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    val = ctx.stack[-1] & 0xFFFF_FFFF if ctx.stack else 0
    ctx.locals[arg] = val


def _build_interp_handlers() -> tuple[Callable[[WASMContext, WasmOperand], None] | None, ...]:
    handlers: StaticVector[Callable[[WASMContext, WasmOperand], None] | None] = StaticVector.of(
        tuple(None for _ in range(256)), capacity=256
    )
    handlers[I32_CONST] = _interp_i32_const
    handlers[I32_ADD] = _interp_i32_add
    handlers[I32_SUB] = _interp_i32_sub
    handlers[I32_MUL] = _interp_i32_mul
    handlers[LOCAL_GET] = _interp_local_get
    handlers[LOCAL_SET] = _interp_local_set
    handlers[LOCAL_TEE] = _interp_local_tee
    return tuple(handlers)


_INTERP_HANDLERS = _build_interp_handlers()


class IntegratedHybridEngine:
    """Legacy test harness for the pre-``RuntimeEngine`` block API."""

    __slots__ = (
        "_dispatch",
        "bitmap",
        "blocks",
        "cache",
        "candidate_threshold",
        "compilations",
        "compile_queue",
        "compile_queue_capacity",
        "compiler",
        "debugger",
        "exec_counter",
        "history",
        "interp_blocks",
        "jit_traces",
        "min_trace_bytes",
        "module",
        "trackable",
        "yield_threshold",
        "yields",
    )

    def __init__(
        self,
        yield_threshold: int = 4,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: tuple[int, ...] = (),
        compiler: _JitCompiler | None = None,
        min_trace_bytes: int | None = None,
        candidate_threshold: int = 0,
        compile_queue_capacity: int = 4,
    ):
        self.bitmap = HotspotBitmap(card_shift=card_shift, code_lengths=code_lengths)
        assert candidate_threshold >= 0
        self.candidate_threshold = candidate_threshold
        self.trackable = BlockCardMask(card_shift=card_shift, code_lengths=code_lengths)
        self.history = HistoryRing(capacity=32)
        self.cache = JITMultiBufferCache()
        self.compiler = compiler or TraceCompiler()
        self.compile_queue_capacity = compile_queue_capacity
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.yield_threshold = yield_threshold
        self.exec_counter = 0
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)
        self.blocks: StaticVector[tuple[int, BasicBlock]] = StaticVector(capacity=0)
        self.interp_blocks = 0
        self.jit_traces = 0
        self.compilations = 0
        self.yields = 0
        self.debugger: _Debugger | None = None
        self._dispatch = self._dispatch_normal
        self.cache.on_evict = self._handle_eviction

    def _handle_eviction(self, purged_pcs: StaticVector[int]) -> None:
        for pc in purged_pcs:
            self.bitmap.mark_evicted(pc)

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        from wasm_reader import parse

        module = parse(wasm_bytes)
        self.register_module_blocks(module)
        return module

    def register_module_blocks(self, module: Module) -> None:
        if module.block_storage is None:
            module.build_basic_block_index()
        self.module = module
        code_lengths = _module_code_lengths(module)
        card_shift = self.bitmap.card_shift
        self.bitmap = HotspotBitmap(card_shift=card_shift, code_lengths=code_lengths)
        self.trackable = BlockCardMask(card_shift=card_shift, code_lengths=code_lengths)
        self.blocks = StaticVector(capacity=len(module.blocks))
        for block in module.blocks:
            self.blocks.append((block.head_pc, block))
        self.trackable.clear()
        for block in module.blocks:
            if (
                block.next_pc is not None
                and block.byte_span >= self.min_trace_bytes
                and block.jit_score >= self.candidate_threshold
            ):
                self.trackable.mark(block.head_pc)

    @property
    def handler_table(self) -> str:
        return "debug" if self._dispatch == self._dispatch_debug else "normal"

    def attach_debugger(self, debugger: _Debugger) -> None:
        self.debugger = debugger
        self._dispatch = self._dispatch_debug

    def detach_debugger(self) -> None:
        self.debugger = None
        self._dispatch = self._dispatch_normal

    def flush_jit_cache(self) -> None:
        self.cache.flush_all()

    def get_block(self, pc: int) -> BasicBlock | None:
        if self.module is not None:
            return self.module.get_block(pc)
        for block_pc, block in self.blocks:
            if block_pc == pc:
                return block
        return None

    def _compile_trace(self, pc: int, block: BasicBlock) -> JITTrace | None:
        assert self.module is not None
        function_index = pc >> 16
        function = self.module.functions[function_index - len(self.module.imports)]
        assert function.local_widths_cache is not None
        return self.compiler.compile_trace(
            pc,
            iter_block_ops(self.module.code_for(function_index), pc & 0xFFFF, block.byte_span),
            block.next_pc,
            block.loops_to,
            block.byte_span,
            function.local_widths_cache,
        )

    def on_yield(self) -> None:
        for pc in self.history.drain():
            if self.bitmap.get_state(pc) == CardState.HOT and not self.compile_queue.contains(pc):
                self.compile_queue.push_back(pc)
                if len(self.compile_queue) >= self.compile_queue_capacity:
                    self.drain_compile_queue()

    def idle_hook(self, budget: int = 4) -> int:
        compiled = 0
        while self.compile_queue and compiled < budget:
            head_pc = self.compile_queue.pop_back()
            if self.bitmap.get_state(head_pc) == CardState.COMPILED:
                continue
            if self.cache.find_trace(head_pc) is not None:
                self.bitmap.mark_compiled(head_pc)
                continue
            block = self.get_block(head_pc)
            assert block is not None
            trace = self._compile_trace(head_pc, block)
            if trace is not None and self.cache.insert(trace):
                self.bitmap.mark_compiled(head_pc)
                self.compilations += 1
                compiled += 1
            else:
                self.trackable.unmark(head_pc)
        return compiled

    def drain_compile_queue(self) -> int:
        return self.idle_hook(budget=len(self.compile_queue) or 1000)

    def _interpret_block(self, block: BasicBlock, ctx: WASMContext) -> None:
        assert self.module is not None
        function_index = block.head_pc >> 16
        code = self.module.code_for(function_index)
        for op, arg in iter_block_ops(code, block.head_pc & 0xFFFF, block.byte_span):
            handler = _INTERP_HANDLERS[op]
            if handler is not None:
                handler(ctx, arg)
                if ctx.fault is not None:
                    break

    def _next_pc(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        if ctx.fault is not None:
            return None
        if block.loops_to is not None:
            cond = ctx.pop()
            target = block.loops_to if cond != 0 else block.next_pc
        else:
            target = block.next_pc
        return target

    def run_block_interpret(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        self._interpret_block(block, ctx)
        return self._next_pc(block, ctx)

    def _dispatch_normal(self, pc: int, block: BasicBlock, ctx: WASMContext) -> int | None:
        trace = self.cache.lookup(pc) if self.bitmap.get_state(pc) == CardState.COMPILED else None
        if trace is not None:
            self.jit_traces += 1
            trace.invoke(ctx)
            next_pc = trace.chain_next if trace.chain_next is not None else self._next_pc(block, ctx)
        else:
            if self.trackable.is_marked(pc):
                self.bitmap.touch(pc)
                self.history.record(pc)
            self.interp_blocks += 1
            self._interpret_block(block, ctx)
            next_pc = self._next_pc(block, ctx)
        self.exec_counter += 1
        if self.exec_counter >= self.yield_threshold:
            self.exec_counter = 0
            self.yields += 1
            self.on_yield()
        return next_pc

    def _dispatch_debug(self, pc: int, block: BasicBlock, ctx: WASMContext) -> int | None:
        dbg = self.debugger
        if dbg is not None and dbg.has_breakpoint(pc):
            dbg.halted = True
            dbg.stop_signal = 5
            return pc
        if dbg is not None:
            dbg.sample_pc(pc)
        self.interp_blocks += 1
        self._interpret_block(block, ctx)
        if dbg is not None:
            dbg.verify_assertions(ctx.memory)
        next_pc = self._next_pc(block, ctx)
        if next_pc is not None and dbg is not None and dbg.has_breakpoint(next_pc):
            dbg.halted = True
            dbg.stop_signal = 5
        return next_pc

    def run_step(self, pc: int, ctx: WASMContext) -> int | None:
        block = self.get_block(pc)
        if block is None:
            return None
        return self._dispatch(pc, block, ctx)


__all__ = ("IntegratedHybridEngine", "WASMContext")

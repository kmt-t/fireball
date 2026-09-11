"""
experiments/pysim/tier2_runtime/runtime_engine.py
Integrated WASM Tiered Tracing Runtime Engine for pysim.
Coordinates the Tier 2 interpreter, vSoC execution, and the Tier 3 JIT
service through the 2-bit card-marking, history, and cache interfaces.
mirroring docs/components/tier2_runtime/runtime_vsoc.md and
docs/components/tier3_jit/jit_compiler.md.
Execution model:
  Interpreter execution:
    -> at basic-block head PCs: record card index into HistoryRing
    -> 2-bit card state: UNEXECUTED (00) -> EXECUTED (01) -> HOT (10) -> COMPILED (11)
    -> on yield/idle: drain HistoryRing, promote HOT cards, push trace heads to LIFO compile queue
    -> async/batch JIT compilation into Active cache bank
  JIT trace execution:
    -> lookup in 3-bank cache (Active, Warm, Oldest)
    -> Oldest bank hit triggers immediate Promotion to Active bank
    -> trace chaining with inbound-source unlinking on bank eviction
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable, Iterator

from control_flow import iter_block_ops
from interpreter import Interpreter, InterpreterCall
from recovery import Result
from system_containers import (
    FlatMapView,
    RadixBinaryTreeView,
    ReadOnlyFlatMapStorage,
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    bswap32,
)
from virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
)
from wasm_module import BasicBlock, Module, TraceBlock
from wasm_opcodes import (
    I32_ADD,
    I32_CONST,
    I32_MUL,
    I32_SUB,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)

try:
    import native_trace_call as _native_trace_call
except ImportError:
    # Optional accelerator (see tier3_jit/native_trace_call.pyx and build scripts):
    # not built -- _invoke_trace falls back to the ctypes.CFUNCTYPE path below.
    _native_trace_call = None

from tier3_jit.jit_cache import (
    _CARD_STATE_NAMES,
    BlockCardMask,
    CardState,
    HistoryRing,
    HotspotBitmap,
    JITCacheBank,
    JITMultiBufferCache,
    JITTrace,
    JITTraceHeader,
)
from tier3_jit.trace_compiler import WASMTraceCompiler

__all__ = [
    "BlockCardMask",
    "CardState",
    "HistoryRing",
    "HotspotBitmap",
    "JITCacheBank",
    "JITMultiBufferCache",
    "JITTrace",
    "JITTraceHeader",
    "RuntimeEngine",
]


class RuntimeEngine:
    """Integrated Tiered Tracing Runtime Engine combining Interpreter and JIT."""

    __slots__ = (
        "__dict__",
        "_fast_block_slots",
        "_n_locals_by_func",
        "_virq",
        "_virq_interp",
        "bitmap",
        "cache",
        "compile_queue",
        "compile_queue_capacity",
        "control_skip_tree",
        "debug",
        "exec_counter",
        "jit_compiler",
        "min_trace_bytes",
        "module",
        "ring",
        "stat_chain_hits",
        "stat_interp_steps",
        "stat_jit_invocations",
        "stat_trace_exits_to_interp",
        "trackable",
        "yield_threshold",
    )

    def __init__(
        self,
        jit_compiler: object | None = None,
        yield_threshold: int = 16,
        card_shift: int = 2,
        min_trace_bytes: int | None = None,
        compile_queue_capacity: int = 4,
        block_capacity: int = 64,
        debug: bool = False,
    ):
        self.debug = debug or (os.environ.get("FIREBALL_DEBUG", "").lower() in ("1", "true", "yes"))
        self.stat_interp_steps: int = 0
        self.stat_jit_invocations: int = 0
        self.stat_chain_hits: int = 0
        self.stat_trace_exits_to_interp: int = 0
        self.bitmap = HotspotBitmap(card_shift=card_shift)
        self.trackable = BlockCardMask(card_shift=card_shift)
        self.ring = HistoryRing()
        self.cache = JITMultiBufferCache()
        self.cache.on_evict = self._handle_eviction
        self.jit_compiler = jit_compiler
        self.compile_queue_capacity = compile_queue_capacity
        # LIFO queue: drain_compile_queue() (below) always empties it again
        # the moment it reaches compile_queue_capacity, so that's this
        # StaticVector's exact fixed capacity, never exceeded.
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.module: Module | None = None
        self._fast_block_slots: list[tuple[int, BasicBlock | None] | None] = [None] * 16
        self.control_skip_tree: RadixBinaryTreeView[int] | None = None
        self.yield_threshold = yield_threshold
        self.exec_counter = 0
        # A card's 2-bit state can only ever describe ONE block: if two
        # distinct block heads shared a card, compiling one would falsely
        # read back as "already compiled" for the other (or evicting one
        # would falsely reset the other's still-resident COMPILED state).
        # Never tracking a block shorter than one card's worth of bytes
        # guarantees every tracked block's next sibling starts at least a
        # full card away, so no two tracked blocks can ever land on the
        # same card -- and it also skips JIT-compiling blocks so short that
        # the interpreter is already faster than a compiled-trace dispatch
        # would be.
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)
        # Loader-known local counts per function index (params + declared
        # locals), a fixed table built once in register_module_blocks --
        # never derived per JIT call via len(locals_arr)/max(): the loader
        # already knows every function's exact local count at module-load
        # time, so there is nothing to defensively recompute at runtime.
        self._n_locals_by_func: list[int] = []
        self._virq: VirqDispatcher | None = None
        self._virq_interp: Interpreter | None = None

    def _handle_eviction(self, purged_pcs: list[int]) -> None:
        for pc in purged_pcs:
            self.bitmap.mark_evicted(pc)

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from wasm_reader import parse

        module = parse(wasm_bytes)
        self.register_module_blocks(module)
        return module

    def get_block(self, pc: int) -> BasicBlock | None:
        slot = ((pc >> 24) ^ (pc >> 16) ^ (pc >> 8) ^ pc) & 0x0F
        cached = self._fast_block_slots[slot]
        if cached is not None and cached[0] == pc:
            return cached[1]
        blk = self.module.get_block(pc) if self.module is not None else None
        self._fast_block_slots[slot] = (pc, blk)
        return blk

    def resolve_trace_block(self, pc: int) -> TraceBlock | None:
        """
        Builds this compile's transient `TraceBlock` from the persisted
        `BasicBlock`'s PC metadata plus the owning function's raw bytecode --
        `BasicBlock` itself never stores the op stream (see
        `wasm_module.BasicBlock`); `self.blocks` here only ever comes from a
        real parsed `Module` (`register_module_blocks`), so `self.module` is
        always available whenever `get_block` finds something.
        """
        block = self.get_block(pc)
        if block is None or self.module is None:
            return None
        code = self.module.code_for(pc >> 16)
        ops = iter_block_ops(code, pc & 0xFFFF, block.byte_span)
        return TraceBlock(
            head_pc=pc,
            ops=ops,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            byte_span=block.byte_span,
        )

    def register_module_blocks(self, module: Module) -> None:
        """Binds loader-owned basic blocks and control skip Radix tree from a parsed WASM Module."""
        if module.block_tree is None:
            module.build_basic_block_index()
        self.module = module
        self._virq = VirqDispatcher(module, self._invoke_virq)
        self.control_skip_tree = module.control_skip_tree
        self.cache.control_skip_tree = module.control_skip_tree
        self._fast_block_slots = [None] * 16
        # `next_pc is not None and byte_span >= min_trace_bytes` is a pure
        # function of static BasicBlock properties + this engine's own
        # min_trace_bytes, both already known here -- decided once per block,
        # not re-derived on every dispatch in record_block_head.
        self.trackable.clear()
        for b in module.blocks:
            if b.next_pc is not None and b.byte_span >= self.min_trace_bytes:
                self.trackable.mark(b.head_pc)
        total_funcs = len(module.imports) + len(module.functions)
        self._n_locals_by_func = [
            max(len(module.locals_layout(idx)), 16) for idx in range(total_funcs)
        ]

    def record_block_head(self, pc: int) -> bool:
        """
        Called by `run()` at each basic-block head that has no compiled
        trace yet. Blocks shorter than `min_trace_bytes` (or with no real
        successor) are never recorded here at all -- see the invariant this
        protects in `__init__` -- decided once, in `register_module_blocks`,
        via `self.trackable` rather than re-derived here per call.
        Returns True if exec_counter reached yield_threshold and triggered on_yield.
        """
        if not self.trackable.is_marked(pc):
            return False
        self.ring.record(pc)
        self.exec_counter += 1
        if self.exec_counter >= self.yield_threshold:
            self.on_yield()
            return True
        return False

    def register_virq_dispatcher(
        self, node_id: int, function_index: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Validate a static vIRQ registration for the next safepoint."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.register_dispatcher(node_id, function_index)

    def commit_virq_safepoint(self) -> None:
        """Publish validated vIRQ registrations at the execution boundary."""
        if self._virq is not None:
            self._virq.commit_safepoint()

    def dispatch_interrupt_event(self, event: InterruptEvent) -> DispatchResult:
        """Dispatch one COOS event through the vIRQ hierarchy."""
        if self._virq is None:
            return DispatchResult(VirqDispatchResult.REJECT, "VIRQ_UNAVAILABLE")
        return self._virq.dispatch_interrupt_event(event)

    def _invoke_virq(
        self,
        function_index: int,
        vector_id: int,
        source_id: int,
        cause_code: int,
        payload0: int,
        payload1: int,
    ) -> int:
        if self._virq_interp is None:
            return int(VirqDispatchResult.REJECT)
        results = self._virq_interp.call(
            function_index,
            (vector_id, source_id, cause_code, payload0, payload1),
        )
        if not results:
            return int(VirqDispatchResult.REJECT)
        return results[0] & 0xFFFF_FFFF

    def on_yield(self) -> None:
        """Scans history ring, updates 2-bit card bitmap, and queues HOT traces."""
        self.exec_counter = 0
        drained_pcs = self.ring.drain()
        for pc in drained_pcs:
            new_state = self.bitmap.touch(pc)
            if new_state == CardState.HOT and pc not in self.compile_queue:
                self.compile_queue.push_back(pc)
                # JIT compile queue overflow: compile all on the spot!
                if len(self.compile_queue) >= self.compile_queue_capacity:
                    self.drain_compile_queue()

    def idle_hook(self, budget: int = 4) -> int:
        """
        Drains the LIFO compile queue during COOS idle_hook. {JIT_ReverseCompilationOrder}
                Compiling in reverse order increases immediate chaining probability.
        """

        compiled_count = 0
        while self.compile_queue and compiled_count < budget:
            pc = self.compile_queue.pop_back()
            if self.bitmap.get_state(pc) == CardState.COMPILED:
                continue
            if self.cache.find_trace(pc) is not None:
                # Already resident under this exact pc (e.g. queued twice
                # before the first compile's mark_compiled() landed) -- the
                # cache, not the coarse per-card bitmap, is the authority on
                # whether *this* pc specifically already has a trace.
                self.bitmap.mark_compiled(pc)
                continue
            trace = None
            if self.jit_compiler is not None:
                trace_block = self.resolve_trace_block(pc)
                trace = self.jit_compiler.compile_trace(pc, trace_block)

            if trace is not None and self.cache.insert(trace):
                self.bitmap.mark_compiled(pc)
                compiled_count += 1
            else:
                # Mark as COMPILED in bitmap so uncompilable / failed blocks do not thrash compile_queue,
                # and unmark from trackable so we never pay JIT lookup cost for them again.
                self.bitmap.mark_compiled(pc)
                self.trackable.unmark(pc)
        return compiled_count

    def drain_compile_queue(self) -> int:
        return self.idle_hook(budget=len(self.compile_queue) or 1000)

    def reset_stats(self) -> None:
        """Resets execution statistics counters."""
        self.stat_interp_steps = 0
        self.stat_jit_invocations = 0
        self.stat_chain_hits = 0
        self.stat_trace_exits_to_interp = 0
        for bank in self.cache.banks:
            for _, t in bank.traces:
                t.exec_count = 0

    def dump_internal_state(self, file: object | None = None) -> str:
        """
        Dumps runtime internal state including execution stats, JIT cache banks,
        and chaining diagnostics for all compiled traces.
        """
        lines: list[str] = []
        lines.append("=" * 80)
        lines.append(
            "                  RuntimeEngine Internal State & Chaining Dump                  "
        )
        lines.append("=" * 80)

        total_blocks = self.stat_interp_steps + self.stat_jit_invocations
        jit_pct = (self.stat_jit_invocations / total_blocks * 100.0) if total_blocks > 0 else 0.0
        chain_pct = (
            (self.stat_chain_hits / self.stat_jit_invocations * 100.0)
            if self.stat_jit_invocations > 0
            else 0.0
        )

        lines.append("[1. Execution Summary]")
        lines.append(f"  * Total Block Executions:    {total_blocks:,}")
        lines.append(
            f"    - Interpreter Steps:       {self.stat_interp_steps:,} ({(100.0 - jit_pct):.1f}%)"
        )
        lines.append(
            f"    - JIT Invocations:         {self.stat_jit_invocations:,} ({jit_pct:.1f}%)"
        )
        lines.append("  * JIT Chaining Performance:")
        lines.append(
            f"    - Chained Invocations:     {self.stat_chain_hits:,} ({chain_pct:.1f}% of JIT runs)"
        )
        lines.append(f"    - Exits to Interpreter:    {self.stat_trace_exits_to_interp:,}")
        lines.append("")

        lines.append("[2. JIT Multi-Buffer Cache]")
        lines.append("  * Cache Banks:")
        for idx, bname in [
            (self.cache.active_idx, "Active"),
            (self.cache.warm_idx, "Warm"),
            (self.cache.oldest_idx, "Oldest"),
        ]:
            bank = self.cache.banks[idx]
            cap = max(bank.capacity_bytes, 1)
            pct = (bank.used_bytes / cap) * 100.0
            lines.append(
                f"    - {bname:<7}: {len(bank.traces):3d} traces, {bank.used_bytes:6,d} / {bank.capacity_bytes:6,d} bytes ({pct:.1f}%), {len(bank.inbound_sources)} inbound sources"
            )
        lines.append(f"  * Promotions (Oldest->Active): {self.cache.promotions}")
        lines.append(f"  * Evictions (Oldest Purges):   {self.cache.evictions}")
        lines.append("")

        lines.append("[3. Compiled Traces & Chaining Analysis]")
        all_traces: list[tuple[str, JITTrace]] = []
        for bname, bank in [
            ("Active", self.cache.active),
            ("Warm", self.cache.warm),
            ("Oldest", self.cache.oldest),
        ]:
            for _, t in bank.traces:
                all_traces.append((bname, t))
        all_traces.sort(key=lambda x: x[1].head_pc)

        if not all_traces:
            lines.append("  (No compiled traces resident in JIT cache)")
        else:
            lines.append(
                f"  {'Bank':<7} {'Head PC':<10} {'Next PC':<10} {'LoopsTo':<10} {'ChainNext':<10} {'Execs':<8} {'Chaining Diagnostic'}"
            )
            lines.append(
                f"  {'-' * 7} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 30}"
            )
            for bname, t in all_traces:
                execs = t.exec_count
                h_pc_str = f"0x{t.head_pc:04X}"
                n_pc_str = f"0x{t.next_pc:04X}" if t.next_pc is not None else "None"
                l_pc_str = f"0x{t.loops_to:04X}" if t.loops_to is not None else "None"
                c_pc_str = f"0x{t.chain_next:04X}" if t.chain_next is not None else "None"

                # Diagnose chaining state
                if t.chain_next is not None:
                    target_bank = self.cache.find_bank(t.chain_next)
                    if target_bank is self.cache.active:
                        tb_name = "Active"
                    elif target_bank is self.cache.warm:
                        tb_name = "Warm"
                    else:
                        tb_name = "Oldest"
                    diag = f"[CHAINED] -> 0x{t.chain_next:04X} in {tb_name}"
                else:
                    if t.loops_to is not None and t.next_pc is None:
                        diag = f"[UNLINKED] Loop backedge only (loops_to=0x{t.loops_to:04X})"
                    elif t.next_pc is None:
                        diag = "[UNLINKED] Function Return / Terminal block"
                    else:
                        succ = t.next_pc
                        skipped_note = ""
                        if self.control_skip_tree is not None:
                            skipped = self.control_skip_tree.find(bswap32(succ))
                            if skipped is not None:
                                skipped_note = f" (skipped to 0x{skipped:04X})"
                                succ = skipped
                        target_trace = self.cache.find_trace(succ)
                        if target_trace is not None:
                            t_bank = self.cache.find_bank(succ)
                            if t_bank is self.cache.oldest:
                                diag = f"[UNLINKED] Target 0x{succ:04X} in Oldest bank (prohibited)"
                            else:
                                diag = f"[UNLINKED] Target 0x{succ:04X} resident{skipped_note} but unlinked"
                        else:
                            if not self.trackable.is_marked(succ):
                                diag = f"[UNLINKED] Target 0x{succ:04X} uncompilable (unsupported stencil / non-trackable){skipped_note}"
                            else:
                                st = self.bitmap.get_state(succ)
                                st_name = (
                                    _CARD_STATE_NAMES[st]
                                    if 0 <= st < len(_CARD_STATE_NAMES)
                                    else f"STATE_{st}"
                                )
                                diag = f"[UNLINKED] Target 0x{succ:04X} not compiled ({st_name}){skipped_note}"

                lines.append(
                    f"  {bname:<7} {h_pc_str:<10} {n_pc_str:<10} {l_pc_str:<10} {c_pc_str:<10} {execs:<8,d} {diag}"
                )
        lines.append("")

        lines.append("[4. Hotspot Cards & Block Classification]")
        compiled_cards = 0
        hot_cards = 0
        executed_cards = 0
        unexecuted_cards = 0
        if self.module is not None:
            for b in self.module.blocks:
                st = self.bitmap.get_state(b.head_pc)
                if st == CardState.COMPILED:
                    compiled_cards += 1
                elif st == CardState.HOT:
                    hot_cards += 1
                elif st == CardState.EXECUTED:
                    executed_cards += 1
                else:
                    unexecuted_cards += 1
            lines.append(f"  * Total Blocks in Module:    {len(self.module.blocks)}")
            trackable_count = sum(
                1 for b in self.module.blocks if self.trackable.is_marked(b.head_pc)
            )
            lines.append(f"  * Trackable JIT Candidates:  {trackable_count}")
            lines.append(
                f"  * Card Status Distribution:  COMPILED={compiled_cards}, HOT={hot_cards}, EXECUTED={executed_cards}, UNEXECUTED={unexecuted_cards}"
            )
        lines.append("=" * 80)

        output_str = "\n".join(lines) + "\n"
        target_file = file if file is not None else sys.stderr
        try:
            target_file.write(output_str)  # type: ignore[union-attr]
            target_file.flush()  # type: ignore[union-attr]
        except (AttributeError, TypeError):
            pass
        return output_str

    def run(
        self,
        interp: Interpreter,
        func_index: int,
        args: list[int],
        idle_budget: int = 4,
    ) -> list[int]:
        """
        Drives `interp` to completion. Execution proceeds block-by-block for
        interpretation, or in a continuous native loop until chaining ends for JIT traces.
        """
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp

        call_state = interp.start(func_index, args)
        COMPILED = CardState.COMPILED

        while not call_state.finished:
            pc = call_state.current_pc()
            if pc is not None and call_state.cont is not None:
                block_here = self.get_block(pc)
                frame_here = call_state.cont[1]
                if block_here is not None and len(frame_here.frames) > block_here.frame_depth:
                    frame_here.frames.truncate(block_here.frame_depth)
                frame_here.boundary_next_pc = block_here.next_pc if block_here is not None else None
                frame_here.boundary_loops_to = (
                    block_here.loops_to if block_here is not None else None
                )

            trace = None
            if pc is not None and self.trackable.is_marked(pc):
                if self.bitmap.get_state(pc) == COMPILED:
                    trace = self.cache.lookup(pc)

            if trace is not None:
                # JIT trace execution: loop until reaching the end of the trace chain
                in_chain = False
                while trace is not None:
                    if in_chain:
                        self.stat_chain_hits += 1
                    self.stat_jit_invocations += 1
                    if self.debug:
                        trace.exec_count += 1
                    call_state = self._invoke_trace(interp, call_state, trace)
                    pc = call_state.current_pc()
                    if pc is None or call_state.finished:
                        break
                    if self.trackable.is_marked(pc) and self.bitmap.get_state(pc) == COMPILED:
                        trace = self.cache.lookup(pc)
                        in_chain = trace is not None
                    else:
                        trace = None
                        break

                if not call_state.finished:
                    self.stat_trace_exits_to_interp += 1

                # After trace chain ends, synchronize frame before returning to interpreter
                if not call_state.finished and pc is not None and call_state.cont is not None:
                    block_here = self.get_block(pc)
                    frame_here = call_state.cont[1]
                    if block_here is not None and len(frame_here.frames) > block_here.frame_depth:
                        frame_here.frames.truncate(block_here.frame_depth)
                    frame_here.boundary_next_pc = (
                        block_here.next_pc if block_here is not None else None
                    )
                    frame_here.boundary_loops_to = (
                        block_here.loops_to if block_here is not None else None
                    )
            else:
                self.stat_interp_steps += 1
                if pc is not None and self.trackable.is_marked(pc):
                    if self.record_block_head(pc):
                        self.idle_hook(budget=idle_budget)
                call_state = interp.step(call_state)

        self.idle_hook(budget=idle_budget)
        if self.debug:
            self.dump_internal_state()

        return call_state.results

    def _invoke_trace(
        self, interp: Interpreter, call_state: InterpreterCall, trace: JITTrace
    ) -> InterpreterCall:
        """
        Executes one compiled native x64 JIT trace and advances `call_state`
        past it. Calls the trace directly on this frame's cached locals
        buffer rather than building a fresh `WASMContext` (ctypes array
        type + instance) per call -- the buffer's address is stable across
        every trace invoked against the same frame, so allocating it once
        and reusing it turns a per-call ctypes array construction into a
        per-call O(locals) value copy on the hot path
        (`{ADR_TraceBoundaryYield}`'s per-block dispatch). When the optional
        `native_trace_call` accelerator (jit/native_trace_call.pyx) is
        built, its raw C function pointer call replaces `trace.fn`'s
        `ctypes.CFUNCTYPE` libffi trampoline, which otherwise dominates this
        call's cost; the fallback keeps this correct on a plain-Python
        checkout. A trace's residual value is VM operand-stack state, not a
        C return value ({ExecutionContext_Layout}): `SPILL_RESULT_TO_STACK_BOT`
        writes it to `frame.jit_result_slot()`'s buffer (passed as `stack_bot`,
        R12) instead of returning it, and every trace always returns void.
        """
        ip, frame, locals_arr, tos = call_state.cont
        try:
            n_locals = self._n_locals_by_func[call_state.func_index]
        except IndexError:
            n_locals = max(len(locals_arr), 16)
        c_locals, locals_ptr = frame.jit_locals_buffer(n_locals)
        for i, v in enumerate(locals_arr):
            c_locals[i] = v
        c_result, result_ptr = frame.jit_result_slot()
        if _native_trace_call is not None and trace.raw_addr is not None:
            _native_trace_call.invoke_trace(
                trace.raw_addr, trace.head_pc, result_ptr.value, locals_ptr.value, 0
            )
        else:
            trace.fn(trace.head_pc, result_ptr, locals_ptr, 0)
        for i in range(len(locals_arr)):
            locals_arr[i] = c_locals[i] & 0xFFFF_FFFF
        res = c_result[0]

        if trace.loops_to is not None:
            # Terminator was BR_IF against a loop backedge: the trace's
            # residual value is the branch condition, consumed here -- it
            # never reaches the WASM operand stack. Mirrors
            # IntegratedHybridEngine._next_pc.
            cond = res if res is not None else 0
            next_unified = trace.loops_to if cond != 0 else trace.next_pc
        else:
            if trace.has_return_val and res is not None:
                frame.values.push_back(res & 0xFFFF_FFFF)
            next_unified = trace.next_pc

        # next_unified is None only for a JIT-compiled block whose
        # terminator is RETURN (or the rare malformed-tail case) -- the
        # function is ending, so signal "past the end of code" via O(1)
        # `len(frame.code)`, the exact sentinel `current_pc()` already
        # checks for. This never decodes an `Instr` at runtime to find this
        # out: {DirectBytecodeExecution} bans runtime instruction-object
        # generation, and the interpreter's own existing "frame just ended"
        # handling (step(), ip >= len(code))
        # is what must process the actual return-to-caller mechanics next.
        next_ip = (next_unified & 0xFFFF) if next_unified is not None else len(frame.code)
        new_tos = frame.values[-1] if frame.values else 0
        call_state.cont = (next_ip, frame, locals_arr, new_tos)
        return call_state


class WASMContext:
    """Execution context for hybrid Tiered Interpreter/JIT execution with direct ctypes backing."""

    __slots__ = (
        "_c_locals",
        "_c_mem",
        "_c_result",
        "_cached_locals_view",
        "_n_locals",
        "fault",
        "memory",
        "stack",
        "stack_capacity",
    )

    def __init__(
        self,
        memory: bytearray | None = None,
        stack_capacity: int = 64,
    ):
        n_locals = 16
        self._c_locals = (ctypes.c_int64 * n_locals)()

        self._n_locals = n_locals
        self.fault: str | None = None
        self.stack_capacity = stack_capacity
        self.stack: StaticVector[int] = StaticVector(capacity=stack_capacity)
        self.memory = memory
        if memory is not None:
            self._c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
        else:
            self._c_mem = None
        # A trace's residual value is VM operand-stack state, not a C return
        # value ({ExecutionContext_Layout}), so `SPILL_RESULT_TO_STACK_BOT`
        # writes it here (via R12, the CPS `stack_bot` argument) instead of
        # in the call's return value.
        self._c_result = ctypes.c_int64()
        self._cached_locals_view = self._LocalsView(self)

    @property
    def stack_bot_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(ctypes.pointer(self._c_result), ctypes.c_void_p)

    @property
    def locals_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(self._c_locals, ctypes.c_void_p)

    @property
    def mem_ptr(self) -> ctypes.c_void_p:
        if self._c_mem is not None:
            return ctypes.c_void_p(ctypes.addressof(self._c_mem))
        return ctypes.c_void_p(0)

    class _LocalsView:
        __slots__ = ("_ctx",)

        def __init__(self, ctx: WASMContext):
            self._ctx = ctx

        def __getitem__(self, idx: int) -> int:
            return self._ctx._c_locals[idx] & 0xFFFF_FFFF

        def __setitem__(self, idx: int, val: int) -> None:
            self._ctx._c_locals[idx] = val & 0xFFFF_FFFF

        def __len__(self) -> int:
            return self._ctx._n_locals

        def __iter__(self) -> Iterator[int]:
            for i in range(self._ctx._n_locals):
                yield self._ctx._c_locals[i] & 0xFFFF_FFFF

    @property
    def locals(self) -> WASMContext._LocalsView:
        return self._cached_locals_view

    @locals.setter
    def locals(self, values: tuple[int, ...]) -> None:
        if len(values) > self._n_locals:
            self.fault = "WASM_LOCAL_STACK_CAPACITY"
            return
        for i, v in enumerate(values):
            self._c_locals[i] = v & 0xFFFF_FFFF

    def push(self, val: int) -> bool:
        if not self.stack.push_back(val & 0xFFFF_FFFF):
            self.fault = "WASM_EXECUTION_STACK_OVERFLOW"
            return False
        return True

    def pop(self) -> int:
        val = self.stack.pop_back()
        if val is None:
            self.fault = "WASM_EXECUTION_STACK_UNDERFLOW"
            return 0
        return val




def _interp_i32_const(ctx: WASMContext, arg: object) -> None:
    ctx.push(int(arg))  # type: ignore[arg-type]


def _interp_i32_add(ctx: WASMContext, _arg: object) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a + b) & 0xFFFF_FFFF)


def _interp_i32_sub(ctx: WASMContext, _arg: object) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a - b) & 0xFFFF_FFFF)


def _interp_i32_mul(ctx: WASMContext, _arg: object) -> None:
    b, a = ctx.pop(), ctx.pop()
    ctx.push((a * b) & 0xFFFF_FFFF)


def _interp_local_get(ctx: WASMContext, arg: object) -> None:
    ctx.push(ctx.locals[arg])  # type: ignore[index]


def _interp_local_set(ctx: WASMContext, arg: object) -> None:
    ctx.locals[arg] = ctx.pop()  # type: ignore[index]


def _interp_local_tee(ctx: WASMContext, arg: object) -> None:
    val = ctx.stack[-1] & 0xFFFF_FFFF if ctx.stack else 0
    ctx.locals[arg] = val  # type: ignore[index]


_INTERP_BLOCK_STORAGE: ReadOnlyFlatMapStorage[int, Callable[[WASMContext, object], None]] = (
    ReadOnlyFlatMapStorage.create(
        [
            (I32_CONST, _interp_i32_const),
            (I32_ADD, _interp_i32_add),
            (I32_SUB, _interp_i32_sub),
            (I32_MUL, _interp_i32_mul),
            (LOCAL_GET, _interp_local_get),
            (LOCAL_SET, _interp_local_set),
            (LOCAL_TEE, _interp_local_tee),
        ]
    )
)
_INTERP_BLOCK_MAP: FlatMapView[int, Callable[[WASMContext, object], None]] = (
    _INTERP_BLOCK_STORAGE.view()
)


class IntegratedHybridEngine:
    """
    Full Tiered Runtime Engine: Interpreter execution -> 2-bit card tracking ->
        Cooperative Yield -> Idle-Hook Batch Compilation -> Trace Chaining -> JIT execution.
    """

    __slots__ = (
        "__dict__",
        "_dispatch",
        "bitmap",
        "blocks",
        "cache",
        "compilations",
        "compile_queue",
        "compile_queue_capacity",
        "compiler",
        "control_skip_storage",
        "control_skip_tree",
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
        card_shift: int = 2,
        compiler: object | None = None,
        min_trace_bytes: int | None = None,
        compile_queue_capacity: int = 4,
    ):
        self.bitmap = HotspotBitmap(card_shift=card_shift)
        self.trackable = BlockCardMask(card_shift=card_shift)
        self.history = HistoryRing(capacity=32)
        self.cache = JITMultiBufferCache()
        self.compiler = compiler or WASMTraceCompiler()
        self.compile_queue_capacity = compile_queue_capacity
        # LIFO queue: on_yield below drops a promotion that doesn't fit
        # rather than growing past this -- the card stays HOT, so it's
        # simply retried on the next on_yield() once idle_hook() drains room.
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.yield_threshold = yield_threshold
        self.exec_counter = 0
        # See RuntimeEngine.min_trace_bytes: a card's 2-bit state can only
        # ever describe one block, so a block shorter than one card's worth
        # of bytes is never touched/tracked here at all -- this guarantees
        # every tracked block's next sibling starts at least a full card
        # away, so no two tracked blocks can ever land on the same card.
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)
        self.blocks: list[tuple[int, BasicBlock]] = []  # Flat slot list instead of dynamic dict
        self.control_skip_storage: ReadOnlyRadixBinaryTreeStorage[int] | None = None
        self.control_skip_tree: RadixBinaryTreeView[int] | None = None
        self.interp_blocks = 0
        self.jit_traces = 0
        self.compilations = 0
        self.yields = 0
        # Handler table dispatch pointer ({DebuggerLabelTableSwitch})
        # Default is normal zero-overhead handler table.
        self.debugger: object | None = None
        self._dispatch = self._dispatch_normal
        self.cache.on_evict = lambda pcs: [self.bitmap.mark_evicted(pc) for pc in pcs]

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from wasm_reader import parse

        module = parse(wasm_bytes)
        self.register_module_blocks(module)
        return module

    def register_module_blocks(self, module: Module) -> None:
        """Binds loader-owned basic blocks and control skip Radix tree from a parsed WASM Module."""
        if module.block_tree is None:
            module.build_basic_block_index()
        self.module = module
        self.control_skip_storage = module.control_skip_storage
        self.control_skip_tree = module.control_skip_tree
        self.cache.control_skip_tree = module.control_skip_tree
        self.blocks = [(b.head_pc, b) for b in module.blocks]
        self.trackable.clear()
        for b in module.blocks:
            if b.next_pc is not None and b.byte_span >= self.min_trace_bytes:
                self.trackable.mark(b.head_pc)

    @property
    def handler_table(self) -> str:
        return "debug" if self._dispatch == self._dispatch_debug else "normal"

    def attach_debugger(self, debugger: object) -> None:
        """Switches handler table pointer to debug dispatch with ZERO per-step overhead in normal mode ({DebuggerLabelTableSwitch})."""
        self.debugger = debugger
        self._dispatch = self._dispatch_debug

    def detach_debugger(self) -> None:
        """Restores handler table pointer to normal fast dispatch ({DebuggerLabelTableSwitch})."""
        self.debugger = None
        self._dispatch = self._dispatch_normal

    def flush_jit_cache(self) -> None:
        """Invalidates all JIT cache banks ({Debugger_Jit_Flush})."""
        self.cache.flush_all()

    def get_block(self, pc: int) -> BasicBlock | None:
        if self.module is not None:
            return self.module.get_block(pc)
        for b_pc, block in self.blocks:
            if b_pc == pc:
                return block
        return None

    def on_yield(self) -> None:
        """Promotes HOT cards in history ring to LIFO compile queue."""
        for pc in self.history.drain():
            if self.bitmap.get_state(pc) == CardState.HOT and pc not in self.compile_queue:
                self.compile_queue.push_back(pc)

    def idle_hook(self, budget: int = 4) -> int:
        """Drains compile queue in LIFO reverse order and chains resident successors."""
        compiled = 0
        while self.compile_queue and compiled < budget:
            head_pc = self.compile_queue.pop_back()
            if self.bitmap.get_state(head_pc) == CardState.COMPILED:
                continue
            if self.cache.find_trace(head_pc) is not None:
                # Already resident under this exact pc (e.g. queued twice
                # before the first compile's mark_compiled() landed) -- the
                # cache, not the coarse per-card bitmap, is the authority on
                # whether *this* pc specifically already has a trace.
                self.bitmap.mark_compiled(head_pc)
                continue
            trace_block = self.resolve_trace_block(head_pc)
            if trace_block is None:
                continue
            trace = self.compiler.compile_trace(head_pc, trace_block)
            if trace is not None:
                self.cache.insert(trace)
                self.bitmap.mark_compiled(head_pc)
                self.compilations += 1
                compiled += 1
        return compiled

    def resolve_trace_block(self, pc: int, block: BasicBlock | None = None) -> TraceBlock | None:
        """
        Builds this compile/interpret call's transient `TraceBlock` from the
        persisted `BasicBlock`'s PC metadata plus the owning function's raw
        bytecode -- `BasicBlock` itself never stores the op stream (see
        `wasm_module.BasicBlock`). Decoded fresh every call, never cached:
        matches `interpreter.py`'s direct-bytecode dispatch, which redecodes
        LEB128 operands on every step rather than persisting `Instr` objects.
        `block`, when the caller already has it (`_interpret_block`'s hot
        dispatch path), skips this engine's uncached `get_block` -- unlike
        `RuntimeEngine.get_block`, this one has no `_fast_block_slots` cache,
        so re-deriving `block` from `pc` here would redo a full Radix tree
        search on every single non-JIT block dispatch.
        """
        if block is None:
            block = self.get_block(pc)
        if block is None or self.module is None:
            return None
        code = self.module.code_for(pc >> 16)
        ops = iter_block_ops(code, pc & 0xFFFF, block.byte_span)
        return TraceBlock(
            head_pc=pc,
            ops=ops,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            byte_span=block.byte_span,
        )

    def _interpret_block(self, block: BasicBlock, ctx: WASMContext) -> None:
        trace_block = self.resolve_trace_block(block.head_pc, block=block)
        if trace_block is None:
            return
        for op, arg in trace_block.ops:
            handler = _INTERP_BLOCK_MAP.find(op)
            if handler is not None:
                handler(ctx, arg)
                if ctx.fault is not None:
                    break

    def _next_pc(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        if ctx.fault is not None:
            return None
        if block.loops_to is not None:
            # Condition at TOS: if non-zero, loop back; else fallthrough
            cond = ctx.pop()
            target = block.loops_to if cond != 0 else block.next_pc
        else:
            target = block.next_pc
        if target is not None and self.control_skip_tree is not None:
            skipped = self.control_skip_tree.find(bswap32(target))
            if skipped is not None:
                return skipped
        return target

    def run_block_interpret(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        """Executes a single basic block strictly in Interpreter mode (for debugging / fallback)."""
        self._interpret_block(block, ctx)
        return self._next_pc(block, ctx)

    def _dispatch_normal(self, pc: int, block: BasicBlock, ctx: WASMContext) -> int | None:
        """Normal handler table: Pure zero-overhead execution (JIT or Fast Interpreter)."""
        # O(1) card check first: most blocks are never compiled, so this
        # must reject them without ever touching the cache's per-bank
        # search, or the miss penalty on the overwhelmingly common path
        # would dwarf the win a hit gets.
        trace = self.cache.lookup(pc) if self.bitmap.get_state(pc) == CardState.COMPILED else None
        if trace is not None:
            # Tier 3 JIT Trace Direct C-Call via ctypes
            self.jit_traces += 1
            trace.invoke(ctx)
            # Trace chaining or fallback to interpreter
            next_pc = (
                trace.chain_next if trace.chain_next is not None else self._next_pc(block, ctx)
            )
        else:
            # Tier 2 Interpreter Execution with 2-bit hotspot tracking.
            # Blocks shorter than min_trace_bytes are never tracked (see
            # __init__): compiling them would cost more than the
            # interpreter dispatch it replaces, and it keeps every tracked
            # block's card unambiguously single-owned. `trackable` is sized
            # by the block's own byte_span, never `next_pc - pc`: a backward
            # branch (a loop body's own `br` to its loop head) makes
            # `next_pc - pc` negative, which would wrongly disqualify a
            # compilable block.
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
        """Debug handler table: JIT bypass, breakpoint check, PC sampling, dynamic assertion verification."""
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
        """
        Executes a single basic block by directly calling the active handler table dispatcher.
                Zero overhead when debugger is detached ({DebuggerLabelTableSwitch}).
        """

        block = self.get_block(pc)
        if block is None:
            return None
        return self._dispatch(pc, block, ctx)

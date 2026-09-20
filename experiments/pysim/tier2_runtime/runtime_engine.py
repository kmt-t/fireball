"""
experiments/pysim/tier2_runtime/runtime_engine.py
Integrated WASM Tiered Tracing Runtime Engine for pysim.
Coordinates the Tier 2 interpreter, vSoC execution, and the Tier 3 JIT
service through the 2-bit card-marking, history, and cache interfaces.
mirroring docs/components/tier2_runtime/runtime_vsoc.md and
docs/components/tier3_executer/jit_compiler.md.
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

import os
import sys
from collections.abc import Generator, Iterable, Sequence
from typing import Protocol, TextIO

from config import (
    FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    FB_CONF_JIT_AGING_STEP_UNITS,
    JIT_CARD_SHIFT,
    RUNTIME_BLOCK_CACHE_SLOT_COUNT,
    RUNTIME_DEBUG_REPORT_LINE_CAPACITY,
    RUNTIME_DEBUG_TRACE_REPORT_CAPACITY,
)
from control_flow import iter_block_ops
from interop_abi import NativeValueStack
from interpreter import (
    RETURN_SENTINEL_IP,
    CallFrame,
    Interpreter,
    InterpreterBindings,
    InterpreterCall,
    WasmNumber,
)
from jit_scoring import JIT_CANDIDATE_THRESHOLD
from logger import Logger
from recovery import Result
from system_containers import StaticVector
from virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
)
from wasm_module import BasicBlock, LocalWidthMap, Module, WasmOperand
from wasm_opcodes import BLOCK, END, IF, LOOP
from vmmio import VMMIOController

try:
    import native_trace_call as _native_trace_call
except ImportError:
    # Optional accelerator (see tier3_jit/native_trace_call.pyx and build scripts):
    # not built -- _invoke_trace falls back to the ctypes.CFUNCTYPE path below.
    _native_trace_call = None


def _module_code_lengths(module: Module) -> StaticVector[int]:
    """Build the bounded per-function code-length vector incrementally."""

    lengths: StaticVector[int] = StaticVector(capacity=len(module.imports) + len(module.functions))
    for _ in module.imports:
        lengths.append(0)
    for index in range(len(module.functions)):
        lengths.append(len(module.code_for(len(module.imports) + index)))
    return lengths


def _empty_block_slots() -> StaticVector[tuple[int, BasicBlock | None] | None]:
    """Create the fixed direct-mapped block cache without materializing an iterator."""

    slots: StaticVector[tuple[int, BasicBlock | None] | None] = StaticVector(
        capacity=RUNTIME_BLOCK_CACHE_SLOT_COUNT
    )
    for _ in range(RUNTIME_BLOCK_CACHE_SLOT_COUNT):
        slots.append(None)
    return slots


class _JitCompiler(Protocol):
    def compile_trace(
        self,
        head_pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_span: int,
        local_widths: LocalWidthMap,
    ) -> JITTrace | None: ...


class _Debugger(Protocol):
    halted: bool
    stop_signal: int

    def has_breakpoint(self, pc: int) -> bool: ...

    def sample_pc(self, pc: int) -> None: ...

    def verify_assertions(self, memory: bytearray) -> None: ...


class _RescheduleObserver(Protocol):
    """Tier-neutral callback supplied by the owning COOS scheduler."""

    def observe_reschedule_generation(self) -> bool: ...


from tier3_jit.jit_cache import (
    _CARD_STATE_NAMES,
    BlockCardMask,
    CardState,
    FunctionUpdateBitmap,
    HistoryRing,
    HotspotBitmap,
    JITCacheBank,
    JITCodeCacheRegion,
    JITMultiBufferCache,
    JITTrace,
    JITTraceHeader,
)

__all__ = (
    "BlockCardMask",
    "CardState",
    "FunctionUpdateBitmap",
    "HistoryRing",
    "HotspotBitmap",
    "JITCacheBank",
    "JITCodeCacheRegion",
    "JITMultiBufferCache",
    "JITTrace",
    "JITTraceHeader",
    "JITInterpreter",
    "RuntimeEngine",
)


class RuntimeEngine:
    """Integrated Tiered Tracing Runtime Engine combining Interpreter and JIT."""

    __slots__ = (
        "_fast_block_slots",
        "_virq",
        "_virq_interp",
        "aging_bytes_scanned",
        "aging_scan_bytes",
        "aging_step_units",
        "aging_steps",
        "aging_units_processed",
        "bitmap",
        "cache",
        "candidate_threshold",
        "compile_queue",
        "compile_queue_capacity",
        "debug",
        "exec_counter",
        "jit_compiler",
        "min_trace_bytes",
        "module",
        "reschedule_observer",
        "ring",
        "stat_chain_hits",
        "stat_interp_steps",
        "stat_jit_invocations",
        "stat_trace_exits_to_interp",
        "trackable",
        "update_bitmap",
        "yield_threshold",
    )

    def __init__(
        self,
        jit_compiler: _JitCompiler | None = None,
        yield_threshold: int = 16,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: Sequence[int] = (),
        min_trace_bytes: int | None = None,
        candidate_threshold: int = JIT_CANDIDATE_THRESHOLD,
        compile_queue_capacity: int = 4,
        block_capacity: int = 64,
        debug: bool = False,
        reschedule_observer: _RescheduleObserver | None = None,
        aging_step_units: int = FB_CONF_JIT_AGING_STEP_UNITS,
        aging_scan_bytes: int = FB_CONF_JIT_AGING_STEP_SCAN_BYTES,
    ):
        debug_env = os.environ.get("FIREBALL_DEBUG", "").lower()
        self.debug = debug or debug_env == "1" or debug_env == "true" or debug_env == "yes"
        self.stat_interp_steps: int = 0
        self.stat_jit_invocations: int = 0
        self.stat_chain_hits: int = 0
        self.stat_trace_exits_to_interp: int = 0
        self.bitmap = HotspotBitmap(card_shift=card_shift, code_lengths=code_lengths)
        assert candidate_threshold >= 0
        self.candidate_threshold = candidate_threshold
        self.trackable = BlockCardMask(card_shift=card_shift, code_lengths=code_lengths)
        assert aging_step_units >= 1 and aging_scan_bytes >= 1
        self.aging_step_units = aging_step_units
        self.aging_scan_bytes = aging_scan_bytes
        self.aging_steps: int = 0
        self.aging_units_processed: int = 0
        self.aging_bytes_scanned: int = 0
        self.update_bitmap = FunctionUpdateBitmap(function_count=len(code_lengths))
        self.ring = HistoryRing()
        self.reschedule_observer = reschedule_observer
        self.cache = JITMultiBufferCache()
        self.cache.on_evict = self._handle_eviction
        self.cache.on_rotate = self.age_step
        self.jit_compiler = jit_compiler
        self.compile_queue_capacity = compile_queue_capacity
        # LIFO queue: drain_compile_queue() (below) always empties it again
        # the moment it reaches compile_queue_capacity, so that's this
        # StaticVector's exact fixed capacity, never exceeded.
        self.compile_queue: StaticVector[int] = StaticVector(capacity=compile_queue_capacity)
        self.module: Module | None = None
        self._fast_block_slots = _empty_block_slots()
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
        self._virq: VirqDispatcher | None = None
        self._virq_interp: Interpreter | None = None

    def set_reschedule_observer(self, observer: _RescheduleObserver | None) -> None:
        """Attach the scheduler-owned generation observer without a Tier import."""
        self.reschedule_observer = observer

    def _handle_eviction(self, purged_pcs: StaticVector[int]) -> None:
        for pc in purged_pcs:
            self.bitmap.mark_evicted(pc)

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from wasm_reader import parse

        module = parse(wasm_bytes)
        self.register_module_blocks(module)
        return module

    def get_block(self, pc: int) -> BasicBlock | None:
        # Fold UnifiedPC 32 -> 16 -> 8 -> 4 with exactly three XORs for the
        # configured locality cache.
        temp = pc ^ (pc >> 16)
        temp = temp ^ (temp >> 8)
        temp = temp ^ (temp >> 4)
        slot = temp & (RUNTIME_BLOCK_CACHE_SLOT_COUNT - 1)
        cached = self._fast_block_slots[slot]
        if cached is not None and cached[0] == pc:
            return cached[1]
        blk = self.module.get_block(pc) if self.module is not None else None
        self._fast_block_slots[slot] = (pc, blk)
        return blk

    def _compile_trace(self, pc: int, block: BasicBlock) -> JITTrace | None:
        """Compile directly from loader metadata and a one-shot bytecode iterator."""
        assert self.module is not None
        function_index = pc >> 16
        function = self.module.functions[function_index - len(self.module.imports)]
        assert function.local_width_map_cache is not None
        code = self.module.code_for(function_index)
        next_pc = block.next_pc
        loops_to = block.loops_to
        # block/loop/if push a control frame that a trace never pushes.  A trace that
        # ends at one is terminal: it neither chains nor skips the opcode, so the
        # interpreter runs it and the frame stack stays an exact prefix ({GOTCHA-INTP-06}).
        # An `if` condition stays on the operand stack as the trace's residual value.
        terminator_offset = (pc & 0xFFFF) + block.byte_span
        terminator = code[terminator_offset] if terminator_offset < len(code) else END
        if terminator == BLOCK or terminator == LOOP or terminator == IF:
            next_pc = None
            loops_to = None
        return self.jit_compiler.compile_trace(
            pc,
            iter_block_ops(code, pc & 0xFFFF, block.byte_span),
            next_pc,
            loops_to,
            block.byte_span,
            function.local_width_map_cache,
        )

    def register_module_blocks(self, module: Module) -> None:
        """Binds the loader-owned immutable block index."""
        if module.block_storage is None:
            module.build_basic_block_index()
        self.module = module
        code_lengths = _module_code_lengths(module)
        card_shift = self.bitmap.card_shift
        self.bitmap = HotspotBitmap(card_shift=card_shift, code_lengths=code_lengths)
        self.trackable = BlockCardMask(card_shift=card_shift, code_lengths=code_lengths)
        self.update_bitmap = FunctionUpdateBitmap(function_count=len(code_lengths))
        self._virq = VirqDispatcher(module, self._invoke_virq)
        self._fast_block_slots = _empty_block_slots()
        # `byte_span >= min_trace_bytes` and the static score are pure
        # functions of BasicBlock properties + this engine's own threshold,
        # both already known here -- decided once per block, not re-derived on
        # every dispatch in record_block_head.  A terminal block is eligible
        # too: its compiled trace exits through RETURN_SENTINEL_IP instead of
        # requiring a fallthrough successor.
        self.trackable.clear()
        for b in module.blocks:
            if b.byte_span >= self.min_trace_bytes and b.jit_score >= self.candidate_threshold:
                self.trackable.mark(b.head_pc)

    def record_block_head(self, pc: int) -> bool:
        """
        Called by `run()` at each basic-block head that has no compiled
        trace yet. Blocks shorter than `min_trace_bytes` are never recorded
        here at all -- see the invariant this protects in `__init__` --
        decided once, in `register_module_blocks`, via `self.trackable` rather
        than re-derived here per call. Terminal blocks remain eligible because
        a compiled return exits through RETURN_SENTINEL_IP.
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

    def unregister_virq_dispatcher(
        self, node_id: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Stage removal of a vIRQ registration for the next safepoint."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.unregister_dispatcher(node_id)

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
            if new_state == CardState.EXECUTED:
                # UNEXECUTED -> EXECUTED is the only way a card becomes EXECUTED.
                self.update_bitmap.mark(self.bitmap.function_of(pc))
            if new_state == CardState.HOT and not self.compile_queue.contains(pc):
                self.compile_queue.push_back(pc)
                # JIT compile queue overflow: compile all on the spot!
                if len(self.compile_queue) >= self.compile_queue_capacity:
                    self.drain_compile_queue()

    def age_step(self) -> int:
        """
        One aging step {JIT_CardAgingSweep}, run once per bank rotation.

        Walk the function update bitmap from the cursor one byte (8 functions)
        at a time. A zero byte is skipped and does not count; a non-zero byte
        is processed (every function whose bit is set has its EXECUTED cards
        decayed to UNEXECUTED, and its bit is cleared) and counts as one unit.
        The step ends after `aging_step_units` units, after `aging_scan_bytes`
        bytes have been scanned (zero bytes included), or after one full pass
        over the table, whichever comes first. HOT and COMPILED cards are never
        modified (GOTCHA-JITR-09). Returns the number of cards decayed.
        """
        update = self.update_bitmap
        self.aging_steps += 1
        if update.unit_count == 0:
            return 0
        decayed = 0
        units = 0
        scanned = 0
        scan_limit = min(self.aging_scan_bytes, update.unit_count)
        while units < self.aging_step_units and scanned < scan_limit:
            bits = update.unit(update.cursor)
            if bits != 0:
                for bit in range(FunctionUpdateBitmap.UNIT_FUNCTIONS):
                    if (bits >> bit) & 1:
                        func_idx = update.cursor * FunctionUpdateBitmap.UNIT_FUNCTIONS + bit
                        decayed += self.bitmap.decay_executed_function(func_idx)
                        update.unmark(func_idx)
                units += 1
            update.cursor = 0 if update.cursor + 1 == update.unit_count else update.cursor + 1
            scanned += 1
        self.aging_units_processed += units
        self.aging_bytes_scanned += scanned
        return decayed

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
                assert self.module is not None
                block = self.get_block(pc)
                assert block is not None
                trace = self._compile_trace(pc, block)

            if trace is not None and self.cache.insert(trace):
                self.bitmap.mark_compiled(pc)
                compiled_count += 1
            else:
                # A failed compile is not a resident trace and must never be
                # represented as COMPILED.  Permanently clear only the
                # candidate bit: repeated compilation cannot change the
                # outcome, while the card state remains non-COMPILED and
                # therefore cannot produce a false JIT lookup hit.
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

    def dump_internal_state(self, file: TextIO | None = None) -> str:
        """
        Dumps runtime internal state including execution stats, JIT cache banks,
        and chaining diagnostics for all compiled traces.
        """
        lines: StaticVector[str] = StaticVector(capacity=RUNTIME_DEBUG_REPORT_LINE_CAPACITY)
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
        for idx, bname in (
            (self.cache.active_idx, "Active"),
            (self.cache.warm_idx, "Warm"),
            (self.cache.oldest_idx, "Oldest"),
        ):
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
        all_traces: StaticVector[tuple[str, JITTrace]] = StaticVector(
            capacity=RUNTIME_DEBUG_TRACE_REPORT_CAPACITY
        )
        for bname, bank in (
            ("Active", self.cache.active),
            ("Warm", self.cache.warm),
            ("Oldest", self.cache.oldest),
        ):
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
                        target_trace = self.cache.find_trace(succ)
                        if target_trace is not None:
                            t_bank = self.cache.find_bank(succ)
                            if t_bank is self.cache.oldest:
                                diag = f"[UNLINKED] Target 0x{succ:04X} in Oldest bank (prohibited)"
                            else:
                                diag = f"[UNLINKED] Target 0x{succ:04X} resident but unlinked"
                        else:
                            if not self.trackable.is_marked(succ):
                                diag = f"[UNLINKED] Target 0x{succ:04X} uncompilable (unsupported stencil / non-trackable)"
                            else:
                                st = self.bitmap.get_state(succ)
                                st_name = (
                                    _CARD_STATE_NAMES[st]
                                    if 0 <= st < len(_CARD_STATE_NAMES)
                                    else f"STATE_{st}"
                                )
                                diag = f"[UNLINKED] Target 0x{succ:04X} not compiled ({st_name})"

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
        target_file.write(output_str)
        target_file.flush()
        return output_str

    def run(
        self,
        interp: Interpreter,
        func_index: int,
        args: Sequence[int],
        idle_budget: int = 4,
    ) -> StaticVector[WasmNumber]:
        """Compatibility entry point for callers that still own the Interpreter."""
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp
        return self._drive_call(interp, interp.start(func_index, args), idle_budget)

    def _drive_call(
        self, interp: Interpreter, call_state: InterpreterCall, idle_budget: int
    ) -> StaticVector[WasmNumber]:
        """Template execution driver shared by RuntimeEngine and JITInterpreter."""
        COMPILED = CardState.COMPILED

        while not call_state.finished:
            assert call_state._frame is not None
            current_ip = call_state._ip
            current_frame = call_state._frame
            assert current_frame is not None
            if current_ip == RETURN_SENTINEL_IP:
                call_state = interp.step(call_state)
                continue
            pc = call_state.current_pc()
            block_here = self.get_block(pc)
            frame_here = call_state._frame
            assert frame_here is not None
            if block_here is not None and len(frame_here.frames) > block_here.frame_depth:
                frame_here.frames.truncate(block_here.frame_depth)
            frame_here.boundary_next_pc = block_here.next_pc if block_here is not None else None
            frame_here.boundary_loops_to = block_here.loops_to if block_here is not None else None

            trace = None
            if block_here is not None and self.trackable.is_marked(pc):
                if self.bitmap.get_state(pc) == COMPILED:
                    trace = self.cache.lookup(pc)
                    if trace is not None and not self._trace_fits_operand_stack(
                        call_state._frame.values, trace
                    ):
                        trace = None

            if trace is not None:
                # Native x64 chaining follows the linked bodies without
                # returning to this loop between every successor.  Count the
                # resident chain for diagnostics, then invoke its first body
                # exactly once.
                chain_count = 1
                chain_trace = trace
                while chain_trace.chain_next is not None:
                    successor = self.cache.find_trace(chain_trace.chain_next)
                    assert successor is not None
                    chain_trace = successor
                    chain_count += 1
                    assert chain_count <= 1024
                self.stat_jit_invocations += chain_count
                self.stat_chain_hits += chain_count - 1
                if self.debug:
                    chain_trace.exec_count += 1
                    if chain_count > 1:
                        trace.exec_count += 1
                call_state = self._invoke_trace(interp, call_state, trace)
                if not call_state.finished:
                    self.stat_trace_exits_to_interp += 1
            else:
                self.stat_interp_steps += 1
                # Only a real block head may enter the hot-block history: a resume point
                # inside a block (e.g. after a call returns) can share a 4-byte card with a
                # trackable head, but it has no block to compile.
                if block_here is not None and self.trackable.is_marked(pc):
                    if self.record_block_head(pc):
                        self.idle_hook(budget=idle_budget)
                call_state = interp.step(call_state)

        self.idle_hook(budget=idle_budget)
        if self.debug:
            self.dump_internal_state()

        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def run_cooperative(
        self,
        interp: Interpreter,
        func_index: int,
        args: Sequence[int],
        idle_budget: int = 4,
    ) -> Generator[None, None, StaticVector[WasmNumber]]:
        """Run a resumable vSoC slice, yielding at trace boundaries on request.

        The scheduler callback is intentionally injected as a protocol so Tier 2
        does not import Tier 1. A yielded ``None`` is the handoff point at which
        the caller's coroutine returns to COOS and later resumes this generator.
        """
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp

        call_state = interp.start(func_index, args)
        compiled = CardState.COMPILED
        while not call_state.finished:
            if (
                self.reschedule_observer is not None
                and self.reschedule_observer.observe_reschedule_generation()
            ):
                self.on_yield()
                yield None
                continue
            assert call_state._frame is not None
            current_ip = call_state._ip
            if current_ip == RETURN_SENTINEL_IP:
                call_state = interp.step(call_state)
                continue
            pc = call_state.current_pc()
            block_here = self.get_block(pc)
            frame_here = call_state._frame
            assert frame_here is not None
            if block_here is not None and len(frame_here.frames) > block_here.frame_depth:
                frame_here.frames.truncate(block_here.frame_depth)
            frame_here.boundary_next_pc = block_here.next_pc if block_here is not None else None
            frame_here.boundary_loops_to = block_here.loops_to if block_here is not None else None

            trace = None
            if block_here is not None and self.trackable.is_marked(pc):
                if self.bitmap.get_state(pc) == compiled:
                    trace = self.cache.lookup(pc)
                    if trace is not None and not self._trace_fits_operand_stack(
                        call_state._frame.values, trace
                    ):
                        trace = None

            if trace is not None:
                chain_count = 1
                chain_trace = trace
                while chain_trace.chain_next is not None:
                    successor = self.cache.find_trace(chain_trace.chain_next)
                    assert successor is not None
                    chain_trace = successor
                    chain_count += 1
                    assert chain_count <= 1024
                self.stat_jit_invocations += chain_count
                self.stat_chain_hits += chain_count - 1
                if self.debug:
                    chain_trace.exec_count += 1
                    if chain_count > 1:
                        trace.exec_count += 1
                call_state = self._invoke_trace(interp, call_state, trace)
            else:
                self.stat_interp_steps += 1
                # Only a real block head may enter the hot-block history: a resume point
                # inside a block (e.g. after a call returns) can share a 4-byte card with a
                # trackable head, but it has no block to compile.
                if block_here is not None and self.trackable.is_marked(pc):
                    if self.record_block_head(pc):
                        self.idle_hook(budget=idle_budget)
                        yield None
                call_state = interp.step(call_state)

        self.idle_hook(budget=idle_budget)
        if self.debug:
            self.dump_internal_state()
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def _trace_fits_operand_stack(self, values: NativeValueStack, trace: JITTrace) -> bool:
        """Whether every trace of the chain can spill and store within the operand stack.

        A native trace writes raw words upward from `sp` with no bound check of its own, so
        the engine checks the widest trace of the chain against the remaining capacity and
        leaves the block to the interpreter, which traps an overflow, when it would not fit.
        """
        words = trace.stack_words
        chained = trace
        while chained.chain_next is not None:
            successor = self.cache.find_trace(chained.chain_next)
            assert successor is not None
            chained = successor
            if chained.stack_words > words:
                words = chained.stack_words
        return len(values) + words <= values.capacity

    def _resume_frame_depth(self, frame: CallFrame, function_index: int, ip: int) -> int:
        """Control-frame count the interpreter must hold when it resumes at `ip`.

        A block head records it.  Any other position is enclosed by every structured
        opener that starts before it and whose matching `end` has not yet executed.
        """
        block = self.get_block((function_index << 16) | ip)
        if block is not None:
            return block.frame_depth
        depth = 0
        for start, control in frame.control_map.blocks.view().entries:
            if start >= ip:
                break
            if ip <= control[0]:
                depth += 1
        return depth

    def _invoke_trace(
        self, interp: Interpreter, call_state: InterpreterCall, trace: JITTrace
    ) -> InterpreterCall:
        """
        Executes one compiled native x64 JIT trace and advances `call_state`
        past it. The trace receives the interpreter's Native operand and
        local stacks directly; no JIT-only ctypes buffers or typed copy-back
        path is allowed. A residual value is written by native code into the
        next raw operand-stack slot passed as `sp` and then committed by
        advancing that same stack's pointer.
        """
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        # Every local owns one fixed 8-byte slot regardless of its type, so a trace
        # addresses locals by index in any frame.  The compiler rejects blocks that
        # touch an i64/f64 local, so a resident trace only reads and writes i32 locals.
        result_slot = len(frame.values)
        locals_ptr = frame.context.local_stack.value_ptr(frame.frame_offset)
        result_ptr = frame.values.value_ptr(result_slot)
        if _native_trace_call is not None and trace.raw_addr is not None:
            _native_trace_call.invoke_trace(
                trace.raw_addr,
                frame.context_ptr.value,
                result_ptr.value,
                locals_ptr.value,
                0,
            )
        else:
            trace.execute(frame.context_ptr, result_ptr, locals_ptr, 0)
        # The native entry may have traversed several successor bodies before
        # returning through the common epilogue.  Resolve the same resident
        # chain in metadata so result width and the final WASM continuation
        # belong to the body that actually returned.
        terminal_trace = trace
        chain_depth = 1
        while terminal_trace.chain_next is not None:
            successor = self.cache.find_trace(terminal_trace.chain_next)
            assert successor is not None
            terminal_trace = successor
            chain_depth += 1
            assert chain_depth <= 1024

        res = frame.values.raw_at(result_slot) if terminal_trace.has_return_val else 0
        if terminal_trace.has_return_val and terminal_trace.loops_to is None:
            frame.values.set_size(result_slot + terminal_trace.result_words)

        if terminal_trace.loops_to is not None:
            # Terminator was BR_IF against a loop backedge: the trace's
            # residual value is the branch condition, consumed here -- it
            # never reaches the WASM operand stack. This is the same
            # continuation rule used by the interpreter boundary.
            cond = res if res is not None else 0
            next_unified = terminal_trace.loops_to if cond != 0 else terminal_trace.next_pc
        else:
            next_unified = terminal_trace.next_pc

        # A terminal trace stops before its WASM boundary opcode. Resume at
        # that raw bytecode position so Interpreter's return/branch/end handler
        # owns sentinel publication and the corresponding frame transition.
        if next_unified is None:
            terminal_block = self.get_block(terminal_trace.head_pc)
            assert terminal_block is not None
            next_ip = (terminal_trace.head_pc & 0xFFFF) + terminal_block.byte_span
            assert next_ip < len(frame.code)
        else:
            next_ip = next_unified & 0xFFFF
            if next_ip >= len(frame.code):
                # A loader-resolved fallthrough past the function body is the
                # same implicit return boundary as Interpreter.step() reaches
                # after executing the final END opcode.
                next_ip = RETURN_SENTINEL_IP
        # A trace never pops the control frames of the `end`/`br` it skips, so the stack
        # may be deeper than the resume point requires.  Drop the stale innermost frames
        # here: a resume point that is not a block head gets no other truncation.
        if next_ip != RETURN_SENTINEL_IP:
            depth = self._resume_frame_depth(frame, call_state.func_index, next_ip)
            if depth < len(frame.frames):
                frame.frames.truncate(depth)
        call_state._ip = next_ip
        call_state._frame = frame
        call_state._locals = locals_arr
        call_state._tos = frame.values.raw_top() if frame.values else 0
        return call_state


class JITInterpreter(Interpreter):
    """Interpreter with the RuntimeEngine execution driver substituted at the template hook."""

    __slots__ = ("idle_budget", "runtime_engine")

    def __init__(
        self,
        module: Module,
        bindings: InterpreterBindings,
        runtime_engine: RuntimeEngine,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: Logger | None = None,
        idle_budget: int = 4,
    ):
        assert idle_budget >= 1
        self.runtime_engine = runtime_engine
        self.idle_budget = idle_budget
        if runtime_engine.module is None:
            runtime_engine.register_module_blocks(module)
        else:
            assert runtime_engine.module is module
        super().__init__(module, bindings, vmmio=vmmio, phys_mem=phys_mem, logger=logger)

    def _complete_call(self, call_state: InterpreterCall) -> StaticVector[WasmNumber]:
        """Run the same call state through the tiered driver and preserve Interpreter.call's contract."""

        self.runtime_engine._virq_interp = self
        return self.runtime_engine._drive_call(self, call_state, self.idle_budget)

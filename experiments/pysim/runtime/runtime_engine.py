"""
experiments/pysim/runtime_engine.py
Integrated WASM Tiered Tracing Runtime Engine for pysim.
Implements the 2-bit card-marking hotspot profiler, history ring,
3-bank rotating JIT code cache, and dynamic tiering execution loop
mirroring docs/components/tier2_runtime/runtime_engine.md and
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

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import ctypes
from collections.abc import Callable
from typing import Any

from system_containers import BitView, RingBuffer


class CardState:
    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2  # Queued for compilation
    COMPILED = 3


class HotspotBitmap:
    """Per-Function 2-bit state per CARD (8 bytes per card) backed by BitView<2>.
    `func_tables` is a static list of BitViews indexed by `func_idx` (0 <= func_idx < num_functions).
    Each function's BitView is sized strictly to its code length at module load time:
    card_count = (func_code_len + (1 << card_shift) - 1) >> card_shift
    storage = bytearray((card_count + 3) // 4)
    """

    def __init__(self, card_shift: int = 3, default_func_code_len: int = 64):
        self.card_shift = card_shift
        self.default_func_code_len = default_func_code_len
        # Static list of BitView indexed directly by func_idx
        self.func_tables: list[BitView | None] = []

    def allocate_functions(self, num_functions: int) -> None:
        """Allocates static slot array for known number of functions at load time."""
        if len(self.func_tables) < num_functions:
            self.func_tables.extend([None] * (num_functions - len(self.func_tables)))

    def register_function(self, func_idx: int, code_len: int) -> BitView:
        """Allocates a dedicated BitView<2> matching the exact function code length."""
        if func_idx >= len(self.func_tables):
            self.func_tables.extend([None] * (func_idx + 1 - len(self.func_tables)))
        card_count = max(1, (code_len + (1 << self.card_shift) - 1) >> self.card_shift)
        storage = bytearray((card_count + 3) // 4)
        view = BitView(storage, bits=2, origin=0, count=card_count)
        self.func_tables[func_idx] = view
        return view

    def _get_or_create_view(self, func_idx: int) -> BitView:
        if func_idx < len(self.func_tables) and self.func_tables[func_idx] is not None:
            return self.func_tables[func_idx]  # type: ignore[return-value]
        return self.register_function(func_idx, self.default_func_code_len)

    def _split_pc(self, pc: int) -> tuple[int, int]:
        if pc > 0xFFFF:
            func_idx = (pc >> 16) & 0xFFFF
            offset = pc & 0xFFFF
        else:
            func_idx = 0
            offset = pc
        return func_idx, offset

    def card_of(self, pc: int) -> int:
        _, offset = self._split_pc(pc)
        return offset >> self.card_shift

    def get_state(self, pc: int) -> int:
        func_idx, offset = self._split_pc(pc)
        view = self._get_or_create_view(func_idx)
        card = offset >> self.card_shift
        if card >= view.size():
            return CardState.UNEXECUTED
        return view.at(card)

    def touch(self, pc: int) -> int:
        """2-bit state machine transition: UNEXECUTED -> EXECUTED -> HOT."""
        func_idx, offset = self._split_pc(pc)
        view = self._get_or_create_view(func_idx)
        card = offset >> self.card_shift
        if card >= view.size():
            view = self.register_function(func_idx, (card + 1) << self.card_shift)
        s = view.at(card)
        if s == CardState.COMPILED:
            return s
        if s == CardState.UNEXECUTED:
            s = CardState.EXECUTED
        elif s == CardState.EXECUTED:
            s = CardState.HOT
        view.put(card, s)
        return s

    def mark_compiled(self, pc: int) -> None:
        func_idx, offset = self._split_pc(pc)
        view = self._get_or_create_view(func_idx)
        card = offset >> self.card_shift
        if card >= view.size():
            view = self.register_function(func_idx, (card + 1) << self.card_shift)
        view.put(card, CardState.COMPILED)

    def mark_evicted(self, pc: int) -> None:
        """Evicted trace resets card state to EXECUTED (01)."""
        func_idx, offset = self._split_pc(pc)
        if func_idx < len(self.func_tables) and self.func_tables[func_idx] is not None:
            view = self.func_tables[func_idx]
            card = offset >> self.card_shift
            if card < view.size():  # type: ignore[union-attr]
                view.put(card, CardState.EXECUTED)  # type: ignore[union-attr]


class HistoryRing:
    """Fixed-size ring of recently executed basic-block head PCs backed by RingBuffer."""

    def __init__(self, capacity: int = 32):
        self.ring: RingBuffer[int] = RingBuffer(capacity)

    @property
    def capacity(self) -> int:
        return self.ring.capacity

    @property
    def dropped(self) -> int:
        return self.ring.dropped

    def record(self, pc: int) -> None:
        self.ring.push(pc)

    def drain(self) -> list[int]:
        return self.ring.drain()


class JITTraceHeader:
    """
    16-byte fixed physical memory layout:
        +0x00 head_wasm_pc(u32)
        +0x04 trace_byte_size(u16)
        +0x06 flags(u8) [0x01: PROMOTED, 0x02: LOOP_HEADER]
        +0x07 variant_id(u8)
        +0x08 chain_next_pc(u32)
        +0x0C chain_target_addr(u32)
    """

    FLAG_PROMOTED = 0x01
    FLAG_LOOP_HEADER = 0x02

    def __init__(
        self,
        head_wasm_pc: int,
        trace_byte_size: int = 64,
        flags: int = 0,
        variant_id: int = 0,
    ):

        self.head_wasm_pc = head_wasm_pc & 0xFFFF_FFFF
        self.trace_byte_size = trace_byte_size & 0xFFFF
        self.flags = flags & 0xFF
        self.variant_id = variant_id & 0xFF
        self.chain_next_pc: int | None = None
        self.chain_target_addr: int | None = None

    def pack(self) -> bytes:
        import struct

        return struct.pack(
            "<IHBBII",
            self.head_wasm_pc,
            self.trace_byte_size,
            self.flags,
            self.variant_id,
            self.chain_next_pc or 0,
            self.chain_target_addr or 0,
        )


class JITTrace:
    """Compiled native trace descriptor backed by JITTraceHeader and native ctypes function pointer."""

    def __init__(
        self,
        head_pc: int,
        fn: Any = None,
        size_bytes: int = 64,
        next_pc: int | None = None,
        loops_to: int | None = None,
        has_return_val: bool = False,
        buf: Any = None,
        native_fn: Any = None,
    ):

        self.head_pc = head_pc
        self.fn = fn or native_fn  # Direct ctypes CFUNCTYPE function pointer or callable
        self.size_bytes = size_bytes
        self.next_pc = next_pc  # Unconditional fallthrough successor
        self.loops_to = loops_to  # Conditional loop backedge (never auto-chained)
        self.has_return_val = has_return_val
        self.header = JITTraceHeader(head_wasm_pc=head_pc, trace_byte_size=size_bytes)
        self.chain_next: int | None = None
        self._exec_buf = buf  # Keeps executable buffer alive in memory

    @property
    def native_fn(self) -> Any:
        return self.fn

    @property
    def flags(self) -> int:
        return self.header.flags

    @flags.setter
    def flags(self, val: int) -> None:
        self.header.flags = val

    def __call__(
        self,
        ip_or_locals: Any,
        stack_bot_or_mem: Any = 0,
        env: Any = 0,
        local_base: Any = 0,
    ) -> int:
        """
        Invokes the native JIT trace directly via ctypes CPS 4-argument calling convention:
                (uint32_t ip, void* stack_bot, void* env, void* local_base)
        """

        try:
            # 4-argument call
            return self.fn(ip_or_locals, stack_bot_or_mem, env, local_base)

        except TypeError:
            # 2-argument fallback
            return self.fn(ip_or_locals, stack_bot_or_mem)

    def invoke(self, ctx: Any) -> int:
        """Helper to invoke trace directly on WASMContext via CPS 4-argument calling convention."""
        try:
            res = self.fn(self.head_pc, ctx.stack_bot_ptr, ctx.mem_ptr, ctx.locals_ptr)

        except TypeError:
            try:
                res = self.fn(ctx.locals_ptr, ctx.mem_ptr)

            except TypeError:
                res = self.fn(ctx)

        if self.has_return_val and isinstance(res, int):
            ctx.push(res & 0xFFFF_FFFF)

        return res


class JITCacheBank:
    def __init__(self, bank_id: int, capacity_bytes: int = 2048):
        self.bank_id = bank_id
        self.capacity_bytes = capacity_bytes
        self.used_bytes = 0
        # Flat list of (head_pc, JITTrace) pairs instead of dynamic dict
        self.traces: list[tuple[int, JITTrace]] = []
        self.inbound_sources: list[int] = []

    def get_trace(self, head_pc: int) -> JITTrace | None:
        for pc, trace in self.traces:
            if pc == head_pc:
                return trace

        return None

    def has_trace(self, head_pc: int) -> bool:
        return self.get_trace(head_pc) is not None

    def remove_trace(self, head_pc: int) -> JITTrace | None:
        for i, (pc, trace) in enumerate(self.traces):
            if pc == head_pc:
                self.traces.pop(i)
                return trace

        return None

    def clear(self) -> list[int]:
        purged = [pc for pc, _ in self.traces]
        self.traces.clear()
        self.inbound_sources.clear()
        self.used_bytes = 0
        return purged

    def allocate(self, trace: JITTrace) -> bool:
        prev = self.get_trace(trace.head_pc)
        delta = trace.size_bytes - (prev.size_bytes if prev else 0)
        if self.used_bytes + delta > self.capacity_bytes:
            return False

        if prev is not None:
            self.remove_trace(trace.head_pc)

        self.traces.append((trace.head_pc, trace))
        self.used_bytes += delta
        return True


class JITMultiBufferCache:
    """3-bank rotating JIT code cache: Active / Warm / Oldest with O(k) bounded unlinking."""

    def __init__(self, bank_capacity: int = 2048):
        self.banks = [JITCacheBank(i, bank_capacity) for i in range(3)]
        self.active_idx, self.warm_idx, self.oldest_idx = 0, 1, 2
        self.promotions = 0
        self.evictions = 0
        self.on_evict: Callable[[list[int]], None] | None = None

    @property
    def active(self) -> JITCacheBank:
        return self.banks[self.active_idx]

    @property
    def warm(self) -> JITCacheBank:
        return self.banks[self.warm_idx]

    @property
    def oldest(self) -> JITCacheBank:
        return self.banks[self.oldest_idx]

    def find_bank(self, head_pc: int) -> JITCacheBank | None:
        for bank in self.banks:
            if bank.has_trace(head_pc):
                return bank

        return None

    def find_trace(self, head_pc: int) -> JITTrace | None:
        for bank in self.banks:
            trace = bank.get_trace(head_pc)
            if trace is not None:
                return trace

        return None

    def register_chain(self, source_pc: int, target_pc: int) -> None:
        target_bank = self.find_bank(target_pc)
        if target_bank is not None and source_pc not in target_bank.inbound_sources:
            target_bank.inbound_sources.append(source_pc)

    def lookup(self, head_pc: int) -> JITTrace | None:
        trace = self.active.get_trace(head_pc)
        if trace is not None:
            return trace

        trace = self.warm.get_trace(head_pc)
        if trace is not None:
            return trace

        trace = self.oldest.get_trace(head_pc)
        if trace is None:
            return None

        # Oldest bank hit: promote to Active bank immediately
        self.oldest.remove_trace(head_pc)
        self.oldest.used_bytes -= trace.size_bytes
        trace.flags |= JITTraceHeader.FLAG_PROMOTED
        if not self.active.allocate(trace):
            self.rotate()
            self.active.allocate(trace)

        self.promotions += 1
        return trace

    def insert(self, trace: JITTrace) -> bool:
        if not self.active.allocate(trace):
            self.rotate()
            if not self.active.allocate(trace):
                return False

        # Chain into active/warm successor if resident (never oldest, never loops_to)
        succ = trace.next_pc
        if succ is not None and (self.active.has_trace(succ) or self.warm.has_trace(succ)):
            trace.chain_next = succ
            self.register_chain(trace.head_pc, succ)

        return True

    def rotate(self) -> list[int]:
        """
        Rotates Active -> Warm -> Oldest -> Active and purges the old Oldest bank.
                Performs O(k) bounded unlinking on purged inbound sources.
        """

        new_active = self.oldest_idx
        new_warm = self.active_idx
        new_oldest = self.warm_idx
        old_oldest_bank = self.banks[new_active]
        # O(k) Unlink inbound chains pointing to traces in the bank being purged
        for src_pc in old_oldest_bank.inbound_sources:
            src_trace = self.find_trace(src_pc)
            if src_trace is not None and src_trace.chain_next in [
                pc for pc, _ in old_oldest_bank.traces
            ]:
                # Check if target was promoted to Active
                target_in_active = self.banks[new_warm].get_trace(
                    src_trace.chain_next
                ) or self.banks[self.active_idx].get_trace(src_trace.chain_next)
                if target_in_active is None:
                    src_trace.chain_next = None  # Unlink to interpreter fallback

        purged_pcs = old_oldest_bank.clear()
        self.evictions += len(purged_pcs)
        self.active_idx = new_active
        self.warm_idx = new_warm
        self.oldest_idx = new_oldest
        if self.on_evict and purged_pcs:
            self.on_evict(purged_pcs)

        return purged_pcs

    def flush_all(self) -> None:
        """Invalidates all JIT cache banks and unlinks chains ({Debugger_Jit_Flush})."""
        for bank in self.banks:
            purged = bank.clear()
            self.evictions += len(purged)
            if self.on_evict and purged:
                self.on_evict(purged)


class RuntimeEngine:
    """Integrated Tiered Tracing Runtime Engine combining Interpreter and JIT."""

    def __init__(self, jit_compiler: Any | None = None, yield_threshold: int = 16):
        self.bitmap = HotspotBitmap()
        self.ring = HistoryRing()
        self.cache = JITMultiBufferCache()
        self.cache.on_evict = self._handle_eviction
        self.jit_compiler = jit_compiler
        self.compile_queue: list[int] = []  # LIFO queue
        self.blocks: list[tuple[int, BasicBlock]] = []  # Flat slot list instead of dynamic dict
        self.yield_threshold = yield_threshold
        self.exec_counter = 0

    def _handle_eviction(self, purged_pcs: list[int]) -> None:
        for pc in purged_pcs:
            self.bitmap.mark_evicted(pc)

    def register_block(self, block: BasicBlock) -> None:
        for i, (pc, _) in enumerate(self.blocks):
            if pc == block.head_pc:
                self.blocks[i] = (block.head_pc, block)
                return

        self.blocks.append((block.head_pc, block))

    def get_block(self, pc: int) -> BasicBlock | None:
        for b_pc, block in self.blocks:
            if b_pc == pc:
                return block

        return None

    def register_module_blocks(self, module: Any) -> None:
        """Automatically extracts and registers all BasicBlocks from a parsed WASM Module."""
        from control_flow import extract_basic_blocks

        n_imports = len(getattr(module, "imports", []))
        for idx, fn in enumerate(getattr(module, "functions", [])):
            func_idx = n_imports + idx
            extracted = extract_basic_blocks(fn.code, func_index=func_idx)
            for head_pc, ops, next_pc in extracted:
                if ops:
                    self.register_block(BasicBlock(head_pc=head_pc, ops=ops, next_pc=next_pc))

    def record_block_head(self, pc: int) -> None:
        """Called at each basic-block head by the interpreter."""
        self.ring.record(pc)
        self.exec_counter += 1
        if self.exec_counter >= self.yield_threshold:
            self.on_yield()

    def on_yield(self) -> None:
        """Scans history ring, updates 2-bit card bitmap, and queues HOT traces."""
        self.exec_counter = 0
        drained_pcs = self.ring.drain()
        for pc in drained_pcs:
            new_state = self.bitmap.touch(pc)
            if new_state == CardState.HOT and pc not in self.compile_queue:
                self.compile_queue.append(pc)

    def idle_hook(self, budget: int = 4) -> int:
        """
        Drains the LIFO compile queue during COOS idle_hook. {JIT_ReverseCompilationOrder}
                Compiling in reverse order increases immediate chaining probability.
        """

        compiled_count = 0
        while self.compile_queue and compiled_count < budget:
            pc = self.compile_queue.pop()
            if self.bitmap.get_state(pc) == CardState.COMPILED:
                continue

            block = self.get_block(pc)
            trace = None
            if hasattr(self.jit_compiler, "compile_trace") and block is not None:
                trace = self.jit_compiler.compile_trace(pc, block)

            elif callable(self.jit_compiler):
                trace = self.jit_compiler(pc)

            if trace is not None and self.cache.insert(trace):
                self.bitmap.mark_compiled(pc)
                compiled_count += 1

        return compiled_count

    def drain_compile_queue(self) -> int:
        return self.idle_hook(budget=len(self.compile_queue) or 1000)

    def run_wasm_coroutine(
        self, interp: Any, func_index: int, args: list[int], yield_every: int = 64
    ):
        """
        Runs a WASM function as a cooperative coroutine on COOS.
                Yields every `yield_every` instructions, draining history ring to compile queue on each yield.
        """

        gen = interp.call_coroutine(func_index, args, yield_every=yield_every)
        try:
            while True:
                next(gen)
                self.on_yield()
                yield  # Cooperative yield to scheduler

        except StopIteration as e:
            self.on_yield()
            return e.value or []


class WASMContext:
    """Execution context for hybrid Tiered Interpreter/JIT execution with direct ctypes backing."""

    def __init__(
        self,
        locals_values: list[int] | None = None,
        memory: bytearray | None = None,
        stack_capacity: int = 64,
    ):

        n_locals = max(len(locals_values or []), 16)
        self._c_locals = (ctypes.c_int64 * n_locals)()
        if locals_values:
            for i, v in enumerate(locals_values):
                self._c_locals[i] = v

        self._n_locals = n_locals
        self.stack: list[int] = []
        self.stack_capacity = stack_capacity
        self.memory = memory
        if memory is not None:
            self._c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)

        else:
            self._c_mem = None

    @property
    def stack_bot_ptr(self) -> ctypes.c_void_p:
        return ctypes.c_void_p(0)

    @property
    def locals_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(self._c_locals, ctypes.c_void_p)

    @property
    def mem_ptr(self) -> ctypes.c_void_p:
        if self._c_mem is not None:
            return ctypes.c_void_p(ctypes.addressof(self._c_mem))

        return ctypes.c_void_p(0)

    class _LocalsView:
        def __init__(self, ctx: WASMContext):
            self._ctx = ctx

        def __getitem__(self, idx: int) -> int:
            return self._ctx._c_locals[idx] & 0xFFFF_FFFF

        def __setitem__(self, idx: int, val: int) -> None:
            self._ctx._c_locals[idx] = val & 0xFFFF_FFFF

        def __len__(self) -> int:
            return self._ctx._n_locals

        def __iter__(self):
            for i in range(self._ctx._n_locals):
                yield self._ctx._c_locals[i] & 0xFFFF_FFFF

    @property
    def locals(self):
        return self._LocalsView(self)

    @locals.setter
    def locals(self, values: list[int]):
        for i, v in enumerate(values):
            self._c_locals[i] = v & 0xFFFF_FFFF

    def push(self, val: int) -> None:
        if len(self.stack) >= self.stack_capacity:
            raise RuntimeError("WASM execution stack overflow")

        self.stack.append(val & 0xFFFF_FFFF)

    def pop(self) -> int:
        if not self.stack:
            raise RuntimeError("WASM execution stack underflow")

        return self.stack.pop()


class BasicBlock:
    """A straight-line sequence of WASM instructions ending with branch/return."""

    def __init__(
        self,
        head_pc: int,
        ops: list[tuple[str, Any]],
        next_pc: int | None = None,
        loops_to: int | None = None,
    ):

        self.head_pc = head_pc
        self.ops = ops
        self.next_pc = next_pc
        self.loops_to = loops_to


class WASMTraceCompiler:
    """Compiles a BasicBlock into a fast callable native JITTrace."""

    def compile_trace(self, head_pc: int, block: BasicBlock) -> JITTrace:
        ops = list(block.ops)
        has_ret = any(op.startswith("i32.") for op, _ in ops)

        def trace_fn(ip: int, stack_bot: Any, env: Any, local_base: Any) -> int:
            # Emulated handler matching CPS 4-argument C signature
            c_arr = ctypes.cast(local_base, ctypes.POINTER(ctypes.c_int64))
            stk: list[int] = []
            for op, arg in ops:
                if op == "i32.const":
                    stk.append(arg)

                elif op == "i32.add":
                    b, a = stk.pop(), stk.pop()
                    stk.append((a + b) & 0xFFFF_FFFF)

                elif op == "i32.sub":
                    b, a = stk.pop(), stk.pop()
                    stk.append((a - b) & 0xFFFF_FFFF)

                elif op == "i32.mul":
                    b, a = stk.pop(), stk.pop()
                    stk.append((a * b) & 0xFFFF_FFFF)

                elif op == "local.get":
                    stk.append(c_arr[arg] & 0xFFFF_FFFF)

                elif op == "local.set":
                    c_arr[arg] = stk.pop() & 0xFFFF_FFFF

            return stk[-1] if stk else 0

        c_fn = ctypes.CFUNCTYPE(
            ctypes.c_int64,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(trace_fn)
        trace = JITTrace(
            head_pc=head_pc,
            fn=c_fn,
            size_bytes=len(ops) * 4,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            has_return_val=has_ret,
        )
        trace._keepalive = c_fn
        return trace


class IntegratedHybridEngine:
    """
    Full Tiered Runtime Engine: Interpreter execution -> 2-bit card tracking ->
        Cooperative Yield -> Idle-Hook Batch Compilation -> Trace Chaining -> JIT execution.
    """

    def __init__(self, yield_threshold: int = 4, card_shift: int = 4, compiler: Any = None):
        self.bitmap = HotspotBitmap(card_shift=card_shift)
        self.history = HistoryRing(capacity=32)
        self.cache = JITMultiBufferCache()
        self.compiler = compiler or WASMTraceCompiler()
        self.compile_queue: list[int] = []
        self.yield_threshold = yield_threshold
        self.exec_counter = 0
        self.blocks: list[tuple[int, BasicBlock]] = []  # Flat slot list instead of dynamic dict
        self.interp_blocks = 0
        self.jit_traces = 0
        self.compilations = 0
        self.yields = 0
        # Handler table dispatch pointer ({DebuggerLabelTableSwitch})
        # Default is normal zero-overhead handler table.
        self.debugger: Any | None = None
        self._dispatch = self._dispatch_normal
        self.cache.on_evict = lambda pcs: [self.bitmap.mark_evicted(pc) for pc in pcs]

    @property
    def handler_table(self) -> str:
        return "debug" if self._dispatch == self._dispatch_debug else "normal"

    def attach_debugger(self, debugger: Any) -> None:
        """Switches handler table pointer to debug dispatch with ZERO per-step overhead in normal mode ({DebuggerLabelTableSwitch})."""
        self.debugger = debugger
        self._dispatch = self._dispatch_debug

    def detach_debugger(self) -> None:
        """Restores handler table pointer to normal fast dispatch ({DebuggerLabelTableSwitch})."""
        self.debugger = None
        self._dispatch = self._dispatch_normal

    def register_block(self, block: BasicBlock) -> None:
        for i, (pc, _) in enumerate(self.blocks):
            if pc == block.head_pc:
                self.blocks[i] = (block.head_pc, block)
                return

        self.blocks.append((block.head_pc, block))

    def get_block(self, pc: int) -> BasicBlock | None:
        for b_pc, block in self.blocks:
            if b_pc == pc:
                return block

        return None

    def on_yield(self) -> None:
        """Promotes HOT cards in history ring to LIFO compile queue."""
        for pc in self.history.drain():
            if self.bitmap.get_state(pc) == CardState.HOT and pc not in self.compile_queue:
                self.compile_queue.append(pc)

    def idle_hook(self, budget: int = 4) -> int:
        """Drains compile queue in LIFO reverse order and chains resident successors."""
        compiled = 0
        while self.compile_queue and compiled < budget:
            head_pc = self.compile_queue.pop()
            block = self.get_block(head_pc)
            if block is None:
                continue

            trace = self.compiler.compile_trace(head_pc, block)
            if trace is not None:
                self.cache.insert(trace)
                self.bitmap.mark_compiled(head_pc)
                self.compilations += 1
                compiled += 1

        return compiled

    def _interpret_block(self, block: BasicBlock, ctx: WASMContext) -> None:
        for op, arg in block.ops:
            if op == "i32.const":
                ctx.push(arg)

            elif op == "i32.add":
                b, a = ctx.pop(), ctx.pop()
                ctx.push((a + b) & 0xFFFF_FFFF)

            elif op == "i32.sub":
                b, a = ctx.pop(), ctx.pop()
                ctx.push((a - b) & 0xFFFF_FFFF)

            elif op == "i32.mul":
                b, a = ctx.pop(), ctx.pop()
                ctx.push((a * b) & 0xFFFF_FFFF)

            elif op == "local.get":
                ctx.push(ctx.locals[arg])

            elif op == "local.set":
                ctx.locals[arg] = ctx.pop()

    def _next_pc(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        if block.loops_to is not None:
            # Condition at TOS: if non-zero, loop back; else fallthrough
            cond = ctx.pop()
            return block.loops_to if cond != 0 else block.next_pc

        return block.next_pc

    def run_block_interpret(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        """Executes a single basic block strictly in Interpreter mode (for debugging / fallback)."""
        self._interpret_block(block, ctx)
        return self._next_pc(block, ctx)

    def _dispatch_normal(self, pc: int, block: BasicBlock, ctx: WASMContext) -> int | None:
        """Normal handler table: Pure zero-overhead execution (JIT or Fast Interpreter)."""
        trace = self.cache.lookup(pc)
        if trace is not None:
            # Tier 3 JIT Trace Direct C-Call via ctypes
            self.jit_traces += 1
            trace.invoke(ctx)
            # Trace chaining or fallback to interpreter
            next_pc = (
                trace.chain_next if trace.chain_next is not None else self._next_pc(block, ctx)
            )

        else:
            # Tier 2 Interpreter Execution with 2-bit hotspot tracking
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

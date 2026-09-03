"""
docs/components/tier2_runtime/concepts/runtime_engine_concept.py
Reference Concept Implementation: Integrated WASM Tiered Tracing Runtime Engine

Execution model (per jit_compiler.md §4.1 / runtime_interpreter.md §4.1,
ADR-INTERP-01 {ADR_TraceBoundaryYield}): the Interpreter is never a coroutine
and never touches the JIT cache or history ring itself -- it only executes
and returns a PC. vSoC (this RuntimeEngine, driving `step()`) owns all of
the below.

  vSoC's step() loop, driving the Interpreter
    -> calls exec_trace(pc); the Interpreter/JIT trace runs until the next
       basic block head and returns control here -- a plain function
       return, never a yield issued by the callee itself
    -> at each BASIC BLOCK HEAD returned to it: records card index into the
       history ring (there is no branch inside a basic block, so
       intermediate PCs carry no scheduling information and are not
       recorded)
    -> trace counter reaches `yield_threshold` -> vSoC itself decides to
       co_yield {Interpreter_LazyJITSwitch}
         -> at yield: scan history ring, promote cards to HOT,
            push their trace-head PCs onto the LIFO compile queue
    -> at idle / periodic: drain the LIFO queue and batch-compile
       (compilation never blocks the executing task)

  JIT trace execution
    -> trace tail holds `chain_next`, defaulting to the interpreter-return stub
    -> backward edges inside a JIT trace carry a Safepoint (async interrupt only,
       distinct from vSoC's own cooperative yield decision above)

The compilation unit is a TRACE identified by its head WASM PC. This is a
tracing JIT: there is no function/method-level unit anywhere in the design.
"""

from collections.abc import Callable

# ==============================================================================
# 1. Hardware protection & traps
# ==============================================================================


class MPUAttribute:
    RO_X = "RO_X"  # Read-Only + Executable (native trace execution)
    RW_XN = "RW_XN"  # Read-Write + Non-Executable (patching / promotion)


class MPUFault(Exception):
    """Cortex-M33 PMSAv8 MPU access violation."""


class WASMTrap(Exception):
    """WASM runtime trap (stack overflow/underflow, unsupported opcode, ...)."""


# ==============================================================================
# 2. Card-granular hotspot bitmap  [jit_compiler.md §3.1]
# ==============================================================================


class CardState:
    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2  # queued for compilation
    COMPILED = 3


class BitView:
    """bit_view<2>: a dense, index-addressed table of 2-bit states (4 cards per byte)."""

    def __init__(self, storage: bytearray, bits: int = 2, origin: int = 0, count: int = 0):
        self.storage = storage
        self.bits = bits
        self.origin = origin
        self.count = count

    def size(self) -> int:
        return self.count

    def _bit_pos(self, i: int) -> int:
        assert 0 <= i < self.count, f"index {i} outside view of size {self.count}"
        return self.origin + i * self.bits

    def at(self, i: int) -> int:
        bit = self._bit_pos(i)
        mask = (1 << self.bits) - 1
        return (self.storage[bit >> 3] >> (bit & 7)) & mask

    def put(self, i: int, value: int):
        mask = (1 << self.bits) - 1
        assert 0 <= value <= mask, "value does not fit in 2 bits"
        bit = self._bit_pos(i)
        byte, shift = bit >> 3, bit & 7
        cleared = self.storage[byte] & ~(mask << shift) & 0xFF
        self.storage[byte] = cleared | (value << shift)


class HotspotBitmap:
    """Per-Function 2-bit state per CARD (8 bytes per card) backed by BitView<2>.
    `func_tables` is a static list of BitViews indexed by `func_idx` (0 <= func_idx < num_functions).
    Each function's BitView is sized strictly to its code length at module load time:
    card_count = (func_code_len + (1 << card_shift) - 1) >> card_shift
    storage = bytearray((card_count + 3) // 4)
    """

    def __init__(self, card_shift: int = 2, default_func_code_len: int = 64):
        self.card_shift = card_shift
        self.default_func_code_len = default_func_code_len
        # Static array of BitView indexed by func_idx
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
        """Records execution using pure 2-bit state machine (UNEXECUTED -> EXECUTED -> HOT)."""
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

    def mark_compiled(self, pc: int):
        func_idx, offset = self._split_pc(pc)
        view = self._get_or_create_view(func_idx)
        card = offset >> self.card_shift
        if card >= view.size():
            view = self.register_function(func_idx, (card + 1) << self.card_shift)
        view.put(card, CardState.COMPILED)

    def mark_evicted(self, pc: int):
        """
        Trace was purged from the cache: the card becomes UNEXECUTED (00),
        not EXECUTED -- it fell out of cache favor once already, so it must
        re-earn hotness through the full warm-up cycle again rather than
        jumping straight back to HOT after a single touch, or a
        marginally-hot card would thrash between compile and evict forever.
        """
        func_idx, offset = self._split_pc(pc)
        if func_idx < len(self.func_tables) and self.func_tables[func_idx] is not None:
            view = self.func_tables[func_idx]
            card = offset >> self.card_shift
            if card < view.size():  # type: ignore[union-attr]
                view.put(card, CardState.UNEXECUTED)  # type: ignore[union-attr]


class HistoryRing:
    """
    Fixed-size ring of recently executed basic-block head PCs. `{HistoryBuffer}`
        The interpreter only appends here; no scanning happens on the hot path.
        Scanning is deferred to the yield handler.
    """

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self.buf: list[int] = []
        self.dropped = 0

    def record(self, pc: int):
        if len(self.buf) >= self.capacity:
            self.buf.pop(0)
            self.dropped += 1
        self.buf.append(pc)

    def drain(self) -> list[int]:
        out = self.buf
        self.buf = []
        return out


# ==============================================================================
# 3. 3-bank JIT code cache with MPU W^X  [jit_compiler.md §3.1 / §4.1-5]
# ==============================================================================


class JITTrace:
    """Compiled native trace with inlined 16-byte JIT trace header. [master_physical_design.md §2.3.1]"""

    HEADER_SIZE_BYTES = 16

    def __init__(self, head_pc: int, native_fn: Callable, size_bytes: int):
        self.head_pc = head_pc
        self.native_fn = native_fn
        self.size_bytes = size_bytes
        self.chain_next: int | None = None  # head_pc of the next trace, or None


class JITCacheBank:
    def __init__(self, bank_id: int, capacity_bytes: int = 2048):
        self.bank_id = bank_id
        self.capacity_bytes = capacity_bytes
        self.used_bytes = 0
        self.traces: dict[int, JITTrace] = {}  # head_pc -> JITTrace
        # Inbound chains: sources (traces in any bank) that point into THIS bank.
        # When this bank transitions Warm -> Oldest (or Oldest is purged),
        # only these registered sources need to be unlinked, eliminating O(N) full-scans.
        self.inbound_sources: set[int] = set()  # set of source head_pcs

    def clear(self) -> list[int]:
        """Purges the bank and returns the head PCs that were discarded."""
        purged = list(self.traces.keys())
        self.traces.clear()
        self.inbound_sources.clear()
        self.used_bytes = 0
        return purged

    def allocate(self, trace: JITTrace) -> bool:
        prev = self.traces.get(trace.head_pc)
        delta = trace.size_bytes - (prev.size_bytes if prev else 0)
        if self.used_bytes + delta > self.capacity_bytes:
            return False
        self.traces[trace.head_pc] = trace
        self.used_bytes += delta
        return True


class JITMultiBufferCache:
    """Active / Warm / Oldest, 2KB each = 6KB [FB_CONF_JIT_CACHE_SIZE].
    Promotion happens ON AN OLDEST-BANK HIT, not at rotation time
    (jit_compiler.md §4.1-4: "Oldest バンクでヒットし、かつ実行カウンタが
    閾値に達している真の Hot コードのみを新 Active バンクへ Promote").
    The Warm bank is a free observation window: a hit there copies nothing.
    """

    def __init__(self, bank_capacity: int = 2048):
        self.banks = [JITCacheBank(i, bank_capacity) for i in range(3)]
        self.active_idx, self.warm_idx, self.oldest_idx = 0, 1, 2
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes = 0
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
            if head_pc in bank.traces:
                return bank
        return None

    def find_trace(self, head_pc: int) -> JITTrace | None:
        for bank in self.banks:
            if head_pc in bank.traces:
                return bank.traces[head_pc]
        return None

    def register_chain(self, source_pc: int, target_pc: int):
        """
        Registers a direct chain link from source_pc to target_pc.
                The target's resident bank records source_pc in its inbound_sources table.
        """
        target_bank = self.find_bank(target_pc)
        if target_bank is not None:
            target_bank.inbound_sources.add(source_pc)

    # --- MPU W^X transaction ---
    def begin_patch(self):
        self.mpu_attr = MPUAttribute.RW_XN

    def commit_patch(self):
        assert self.mpu_attr == MPUAttribute.RW_XN, "commit without begin"
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes += 1  # __DSB(); __ISB();

    def _require_writable(self):
        if self.mpu_attr != MPUAttribute.RW_XN:
            raise MPUFault("W^X VIOLATION: write to RO_X JIT cache")

    def require_executable(self):
        if self.mpu_attr != MPUAttribute.RO_X:
            raise MPUFault("W^X VIOLATION: execute on RW_XN JIT cache")

    # --- Lookup with Oldest-Only Promotion ---
    def lookup(self, head_pc: int) -> JITTrace | None:
        if head_pc in self.active.traces:
            return self.active.traces[head_pc]
        if head_pc in self.warm.traces:
            # Free observation window: execute in place, copy nothing.
            return self.warm.traces[head_pc]
        trace = self.oldest.traces.get(head_pc)
        if trace is None:
            return None
        # Oldest-Only Promotion: a hit in Oldest promotes immediately to Active.
        old_oldest = self.oldest
        self.begin_patch()
        try:
            del old_oldest.traces[head_pc]
            old_oldest.used_bytes -= trace.size_bytes
            # Any inbound chain sources registered against the bank this
            # trace used to live in must follow it to wherever it lands --
            # captured now, before a possible rotate() below clears
            # old_oldest's own inbound_sources as a side effect of purging a
            # *different* bank's worth of traces into it. Without this, a
            # later rotate() looks for these sources in the bank that used
            # to hold the promoted trace, never finds them there anymore,
            # and never unlinks them to the interpreter fallback once this
            # trace is eventually purged for real.
            following_sources = set()
            for src_pc in old_oldest.inbound_sources:
                src_trace = self.find_trace(src_pc)
                if src_trace is not None and src_trace.chain_next == head_pc:
                    following_sources.add(src_pc)
            old_oldest.inbound_sources -= following_sources

            if not self.active.allocate(trace):
                self.rotate()
                self.active.allocate(trace)
            target_bank = self.find_bank(head_pc)
            if target_bank is not None:
                target_bank.inbound_sources.update(following_sources)
            self.promotions += 1
        finally:
            self.commit_patch()
        return trace

    # --- Insertion & rotation ---
    def insert(self, trace: JITTrace) -> bool:
        self._require_writable()
        if self.active.allocate(trace):
            return True
        self.rotate()
        return self.active.allocate(trace)

    def find_bank_excluding(self, head_pc: int, excluding_bank_id: int) -> JITCacheBank | None:
        for bank in self.banks:
            if bank.bank_id != excluding_bank_id and head_pc in bank.traces:
                return bank
        return None

    def rotate(self):
        """Oldest is purged and becomes the new Active; Active->Warm, Warm->Oldest.
        Resolves incoming chains targeting Oldest in O(k) bounded time via
        Oldest's inbound_sources right before Oldest is cleared and reused as Active:
        - If the target was promoted to Active, it is re-chained to the promoted trace.
        - If the target was not promoted and is evicted, it is unlinked to fallback.
        """
        self._require_writable()
        # 1. Resolve incoming chains that pointed into Oldest (re-chain if promoted, unlink if evicted).
        self._resolve_bank_inbound(self.oldest)
        purged = self.banks[self.oldest_idx].clear()
        self.evictions += len(purged)
        self.active_idx, self.warm_idx, self.oldest_idx = (
            self.oldest_idx,
            self.active_idx,
            self.warm_idx,
        )
        if purged and self.on_evict:
            self.on_evict(purged)  # let the bitmap mark the cards re-compilable

    def _resolve_bank_inbound(self, bank: JITCacheBank):
        """
        Resolves inbound chains pointing to `bank` right before `bank` is purged.
                If a target was promoted to Active (or still alive elsewhere), re-chains
                the source to the promoted target without dropping to interpreter fallback.
                Only if the target is completely evicted is the source unlinked.
        """
        for src_pc in list(bank.inbound_sources):
            src_trace = self.find_trace(src_pc)
            if src_trace is not None and src_trace.chain_next is not None:
                target_pc = src_trace.chain_next
                promoted_bank = self.find_bank_excluding(target_pc, excluding_bank_id=bank.bank_id)
                if promoted_bank is not None:
                    # Target was promoted: keep link and transfer inbound tracking!
                    promoted_bank.inbound_sources.add(src_pc)
                else:
                    # Target was not promoted and is evicted: unpatch to interpreter fallback
                    src_trace.chain_next = None
        bank.inbound_sources.clear()


# ==============================================================================
# 4. Copy-and-Patch compiler  [jit_compiler.md §4.1]
# ==============================================================================


class Stencil:
    """Pre-compiled native byte template with relocation holes."""

    def __init__(self, name: str, code: list[str], holes: tuple[str, ...] = ()):
        self.name = name
        self.code = code
        self.holes = holes

    def emit(self, **patch: object) -> list[str]:
        for h in self.holes:
            if h not in patch:
                raise KeyError(f"stencil '{self.name}' requires relocation '{h}'")
        return [ln.format(**patch) for ln in self.code]


class CopyPatchCompiler:
    """
    Concatenates stencils and patches relocation holes. No IR, single pass.
        `{JIT_CopyAndPatch}` `{SinglePassCompilation}`
    """

    BYTES_PER_INSTRUCTION = 4  # Thumb-2 wide instruction

    def __init__(self):
        self.stencils = {
            "i32.const": Stencil(
                "i32.const",
                ["MOVW R4, #{imm_lo}", "MOVT R4, #{imm_hi}", "PUSH R4"],
                ("imm_lo", "imm_hi"),
            ),
            "i32.add": Stencil("i32.add", ["POP R5", "POP R4", "ADD R4, R4, R5", "PUSH R4"]),
            "i32.sub": Stencil("i32.sub", ["POP R5", "POP R4", "SUB R4, R4, R5", "PUSH R4"]),
            "i32.mul": Stencil("i32.mul", ["POP R5", "POP R4", "MUL R4, R4, R5", "PUSH R4"]),
            "local.get": Stencil("local.get", ["LDR R4, [R3, #{slot}]", "PUSH R4"], ("slot",)),
            "local.set": Stencil("local.set", ["POP R4", "STR R4, [R3, #{slot}]"], ("slot",)),
            "backedge": Stencil(
                "backedge",
                ["LDR R12, [R10, #SAFEPOINT]", "CBNZ R12, __safepoint", "B #{target}"],
                ("target",),
            ),
            "chain": Stencil("chain", ["B #{chain_next}"], ("chain_next",)),
        }

    def compile_trace(self, head_pc: int, block: "BasicBlock") -> JITTrace:
        """
        Emits one straight-line trace starting at `block.head_pc`.
                Returns a JITTrace whose native_fn REPLAYS THE EMITTED NATIVE LISTING,
                not the WASM operand list — so a stencil bug shows up as a wrong result
                rather than being masked by re-interpreting the source.
        """
        listing: list[str] = []
        for op, arg in block.ops:
            st = self.stencils.get(op)
            if st is None:
                raise WASMTrap(f"NO_STENCIL_FOR: {op}")
            if op == "i32.const":
                listing += st.emit(imm_lo=arg & 0xFFFF, imm_hi=(arg >> 16) & 0xFFFF)
            elif op in ("local.get", "local.set"):
                listing += st.emit(slot=arg * 4)
            else:
                listing += st.emit()

        # A Safepoint is emitted at the backward edge only. `{JIT_Safepoint}`
        if block.loops_to is not None:
            listing += self.stencils["backedge"].emit(target=block.loops_to)

        size = len(listing) * self.BYTES_PER_INSTRUCTION
        return JITTrace(head_pc, native_fn=make_native_executor(listing), size_bytes=size)


def make_native_executor(listing: list[str]) -> Callable:
    """
    Simulates the CPU executing the emitted instruction listing.
        Deliberately a machine over the NATIVE listing (R3/R4/R5/R12/stack), so this is
        a genuinely different execution path from the interpreter. If a stencil is
        wrong, the result diverges.
    """

    def run(ctx: "WASMContext") -> str:
        R = {"R3": 0, "R4": 0, "R5": 0, "R10": 0, "R12": 0}
        i = 0
        while i < len(listing):
            ins = listing[i]
            head = ins.split()[0]
            if head == "MOVW":
                dst = ins.replace(",", "").split()[1]
                R[dst] = int(ins.split("#")[1])
            elif head == "MOVT":
                dst = ins.replace(",", "").split()[1]
                R[dst] = (R[dst] & 0xFFFF) | (int(ins.split("#")[1]) << 16)
            elif head == "PUSH":
                ctx.push(R[ins.split()[1]])
            elif head == "POP":
                R[ins.split()[1]] = ctx.pop()
            elif head in ("ADD", "SUB", "MUL"):
                d, a, b = ins.replace(",", "").split()[1:4]
                x, y = R[a], R[b]
                R[d] = (
                    (x + y) if head == "ADD" else (x - y) if head == "SUB" else (x * y)
                ) & 0xFFFF_FFFF
            elif head == "LDR":
                if "SAFEPOINT" in ins:
                    R["R12"] = 1 if ctx.interrupt_flag else 0
                elif "[R3" in ins:
                    slot = int(ins.split("#")[1].rstrip("]")) // 4
                    if not 0 <= slot < len(ctx.locals):
                        raise WASMTrap("LOCAL_INDEX_OUT_OF_RANGE")
                    dst = ins.replace(",", "").split()[1]
                    R[dst] = ctx.locals[slot]
            elif head == "STR":
                if "[R3" in ins:
                    slot = int(ins.split("#")[1].rstrip("]")) // 4
                    if not 0 <= slot < len(ctx.locals):
                        raise WASMTrap("LOCAL_INDEX_OUT_OF_RANGE")
                    src = ins.replace(",", "").split()[1]
                    ctx.locals[slot] = R[src]
            elif head == "CBNZ":
                # Safepoint check emitted at every backward edge. `{JIT_Safepoint}`
                if ctx.poll_safepoint():
                    return "SAFEPOINT_YIELD"
            elif head in ("B", "LDR_SAFEPOINT"):
                pass  # backward branch target handled by the caller loop
            i += 1
        return "COMPLETED"

    return run


# ==============================================================================
# 5. Execution context  [runtime_interpreter.md §3.3]
# ==============================================================================


class WASMContext:
    MAX_STACK_SLOTS = 64

    def __init__(self, memory_size: int = 8192):  # FB_CONF_GUEST_RAM_SIZE
        self.stack: list[int] = []
        self.locals: list[int] = []
        self.memory = bytearray(memory_size)
        self.interrupt_flag = False  # set by an ISR; polled at Safepoints
        self.safepoints_hit = 0

    def push(self, val: int):
        if len(self.stack) >= self.MAX_STACK_SLOTS:
            raise WASMTrap("STACK_OVERFLOW")
        self.stack.append(val & 0xFFFF_FFFF)

    def pop(self) -> int:
        if not self.stack:
            raise WASMTrap("STACK_UNDERFLOW")
        return self.stack.pop()

    def raise_interrupt(self):
        self.interrupt_flag = True

    def poll_safepoint(self) -> bool:
        if self.interrupt_flag:
            self.safepoints_hit += 1
            return True
        return False


# ==============================================================================
# 6. Tiered tracing runtime engine
# ==============================================================================


class BasicBlock:
    """
    Straight-line WASM code starting at `head_pc`.
        `next_pc` is the fallthrough / backward-branch target head PC, or None to
        end execution. No branches occur inside `ops`, which is exactly why only
        `head_pc` is recorded in the history ring.
    """

    def __init__(
        self,
        head_pc: int,
        ops: list[tuple[str, object]],
        next_pc: int | None = None,
        loops_to: int | None = None,
    ):
        self.head_pc = head_pc
        self.ops = ops
        self.next_pc = next_pc
        self.loops_to = loops_to  # backward edge target, if this block ends a loop


class IntegratedRuntimeEngine:
    """Interpreter -> card history -> yield-time triage -> LIFO batch compile -> JIT."""

    def __init__(
        self,
        yield_threshold: int = 8,
        card_shift: int = 2,
        min_trace_bytes: int | None = None,
    ):
        self.bitmap = HotspotBitmap(card_shift=card_shift)
        self.history = HistoryRing(capacity=32)
        self.cache = JITMultiBufferCache()
        self.compiler = CopyPatchCompiler()
        self.compile_queue: list[int] = []  # LIFO of trace head PCs
        self.yield_threshold = yield_threshold
        self.trace_counter = 0
        self.blocks: dict[int, BasicBlock] = {}
        self.interp_blocks = 0
        self.jit_traces = 0
        self.compilations = 0
        self.yields = 0
        # Purged traces must make their cards re-compilable again.
        self.cache.on_evict = lambda pcs: [self.bitmap.mark_evicted(pc) for pc in pcs]
        # A card's 2-bit state can only ever describe ONE block: if two
        # distinct block heads shared a card, compiling one would falsely
        # read back as "already compiled" for the other, and evicting one
        # would falsely reset the other's still-resident COMPILED state.
        # Never tracking a block shorter than one card's worth of bytes
        # guarantees every tracked block's next sibling starts at least a
        # full card away, so no two tracked blocks can ever land on the
        # same card -- and it also skips JIT-compiling blocks so short that
        # the interpreter is already faster than a compiled-trace dispatch
        # would be.
        self.min_trace_bytes = min_trace_bytes if min_trace_bytes is not None else (1 << card_shift)

    def register_block(self, block: BasicBlock):
        self.blocks[block.head_pc] = block

    # --- Cooperative yield  [runtime_interpreter.md §4.1 概算Yield] ---
    def _tick_and_maybe_yield(self) -> bool:
        self.trace_counter += 1
        if self.trace_counter < self.yield_threshold:
            return False
        self.trace_counter = 0
        self.yields += 1
        self.on_yield()
        return True

    def on_yield(self):
        """Scan the history ring, promote HOT cards, enqueue their head PCs (LIFO)."""
        for pc in self.history.drain():
            if self.bitmap.get_state(pc) == CardState.HOT and pc not in self.compile_queue:
                self.compile_queue.append(pc)

    # --- Batch compilation  [set_idle_hook / register_periodic_callback] ---
    def idle_hook(self, budget: int = 4) -> int:
        """
        Drains the compile queue LIFO. `{JIT_ReverseCompilationOrder}`
                Compiling later traces first raises the chance that a preceding trace
                can be chained directly at patch time.
        """
        compiled = 0
        self.cache.begin_patch()
        try:
            while self.compile_queue and compiled < budget:
                head_pc = self.compile_queue.pop()  # LIFO
                if self.bitmap.get_state(head_pc) == CardState.COMPILED:
                    continue
                if self.cache.find_trace(head_pc) is not None:
                    # Already resident under this exact pc (e.g. queued
                    # twice before the first compile's mark_compiled()
                    # landed) -- the cache, not the coarse per-card bitmap,
                    # is the authority on whether *this* pc has a trace.
                    self.bitmap.mark_compiled(head_pc)
                    continue
                block = self.blocks.get(head_pc)
                if block is None:
                    continue
                trace = self.compiler.compile_trace(head_pc, block)
                self.cache.insert(trace)
                # Immediate chaining only for the unconditional fallthrough
                # (`next_pc`). `loops_to` is a CONDITIONAL backward branch --
                # the compiled backedge only emits a Safepoint poll, never a
                # compare-and-branch, so the taken/not-taken decision still
                # belongs to `_next_pc()` (which pops the WASM stack). Chaining
                # straight into loops_to would skip that pop and that test,
                # turning every loop into an unconditional infinite one.
                # Checked AFTER insert(): insert() may itself rotate the
                # banks, which would stale-date a pre-insert membership check.
                succ = block.next_pc
                if succ is not None and (
                    succ in self.cache.active.traces or succ in self.cache.warm.traces
                ):
                    trace.chain_next = succ
                    self.cache.register_chain(head_pc, succ)
                self.bitmap.mark_compiled(head_pc)
                self.compilations += 1
                compiled += 1
        finally:
            self.cache.commit_patch()
        return compiled

    # --- Main execution loop ---
    def run(self, entry_pc: int, ctx: WASMContext, max_blocks: int = 1000) -> str:
        pc: int | None = entry_pc
        executed = 0
        while pc is not None and executed < max_blocks:
            executed += 1
            block = self.blocks.get(pc)
            if block is None:
                raise WASMTrap(f"NO_BLOCK_AT_PC: {pc}")
            # O(1) card check first: most blocks are never compiled, so this
            # must reject them without ever touching the cache's per-bank
            # search, or the miss penalty on the overwhelmingly common path
            # would dwarf the win a hit gets.
            trace = (
                self.cache.lookup(pc) if self.bitmap.get_state(pc) == CardState.COMPILED else None
            )
            if trace is not None:
                self.cache.require_executable()
                self.jit_traces += 1
                status = trace.native_fn(ctx)
                if status == "SAFEPOINT_YIELD":
                    return "SAFEPOINT_YIELD"
                # chain_next is None -> return to the interpreter (dispatcher stub)
                pc = trace.chain_next if trace.chain_next is not None else self._next_pc(block, ctx)
            else:
                # Record ONLY the basic-block head. `{HistoryBuffer}` Blocks
                # shorter than min_trace_bytes are never tracked at all --
                # see the invariant this protects in __init__. Estimated in
                # the same units `CopyPatchCompiler.compile_trace` itself
                # uses for a compiled trace's `size_bytes`, since that is
                # what actually determines whether compiling is worthwhile.
                est_bytes = len(block.ops) * CopyPatchCompiler.BYTES_PER_INSTRUCTION
                if est_bytes >= self.min_trace_bytes:
                    self.bitmap.touch(pc)
                    self.history.record(pc)
                self.interp_blocks += 1
                self._interpret(block, ctx)
                pc = self._next_pc(block, ctx)

            if self._tick_and_maybe_yield():
                self.idle_hook()
        return "COMPLETED"

    @staticmethod
    def _next_pc(block: BasicBlock, ctx: WASMContext) -> int | None:
        if block.loops_to is not None:
            return block.loops_to if ctx.pop() != 0 else block.next_pc
        return block.next_pc

    def _interpret(self, block: BasicBlock, ctx: WASMContext):
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
                if not 0 <= arg < len(ctx.locals):
                    raise WASMTrap("LOCAL_INDEX_OUT_OF_RANGE")
                ctx.push(ctx.locals[arg])
            elif op == "local.set":
                if not 0 <= arg < len(ctx.locals):
                    raise WASMTrap("LOCAL_INDEX_OUT_OF_RANGE")
                ctx.locals[arg] = ctx.pop()
            else:
                raise WASMTrap(f"UNSUPPORTED_OPCODE: {op}")


# ==============================================================================
# 7. Verification tests
# ==============================================================================


def _countdown_engine(**kw) -> tuple[IntegratedRuntimeEngine, WASMContext]:
    """loop: acc *= n; n -= 1; branch back while n != 0."""
    eng = IntegratedRuntimeEngine(**kw)
    eng.register_block(
        BasicBlock(
            head_pc=0x100,
            ops=[
                ("local.get", 1),
                ("local.get", 0),
                ("i32.mul", None),
                ("local.set", 1),
                ("local.get", 0),
                ("i32.const", 1),
                ("i32.sub", None),
                ("local.set", 0),
                ("local.get", 0),
            ],
            next_pc=None,
            loops_to=0x100,
        )
    )
    ctx = WASMContext()
    return eng, ctx


def test_only_basic_block_heads_are_recorded():
    """A block with 9 instructions must contribute exactly one history entry."""
    eng, ctx = _countdown_engine(yield_threshold=1000)
    ctx.locals = [3, 1]
    eng.run(0x100, ctx)
    assert all(pc == 0x100 for pc in eng.history.buf), eng.history.buf
    assert len(eng.history.buf) == eng.interp_blocks


def test_card_granularity_not_function_granularity():
    """Two distinct head PCs inside one card share a single bitmap slot."""
    bm = HotspotBitmap(card_shift=6)
    assert bm.card_of(0x100) == bm.card_of(0x120), "same 64-byte card"
    bm.touch(0x100)
    assert bm.get_state(0x120) == CardState.EXECUTED
    bm.touch(0x120)
    assert bm.get_state(0x100) == CardState.HOT, "state advances per card (2-bit FSM)"


def test_compilation_is_deferred_to_the_yield_and_idle_hook():
    """Detection must not compile inline: the executing task is never blocked."""
    eng, ctx = _countdown_engine(yield_threshold=1000)
    ctx.locals = [6, 1]
    eng.run(0x100, ctx)
    assert eng.bitmap.get_state(0x100) == CardState.HOT
    assert eng.compilations == 0, "no compilation may happen during execution"
    assert eng.compile_queue == [], "queue is only filled at yield time"
    eng.on_yield()  # yield handler scans the ring
    assert eng.compile_queue == [0x100]
    assert eng.compilations == 0
    eng.idle_hook()  # batch compile
    assert eng.compilations == 1
    assert eng.bitmap.get_state(0x100) == CardState.COMPILED


def test_lifo_compile_queue_order():
    eng = IntegratedRuntimeEngine()
    for pc in (0x100, 0x200, 0x300):
        eng.register_block(BasicBlock(pc, [("i32.const", 1)], next_pc=None))
    eng.compile_queue = [0x100, 0x200, 0x300]
    order: list[int] = []
    real = eng.compiler.compile_trace

    def spy(head_pc, block):
        order.append(head_pc)
        return real(head_pc, block)

    eng.compiler.compile_trace = spy
    eng.idle_hook(budget=3)
    assert order == [0x300, 0x200, 0x100], f"LIFO expected, got {order}"


def test_jit_result_matches_interpreter_via_native_listing():
    """The JIT path executes the emitted native listing, not the WASM ops."""
    eng, ctx = _countdown_engine(yield_threshold=4)
    ctx.locals = [5, 1]
    eng.run(0x100, ctx)
    assert ctx.locals[1] == 120, f"5! expected, got {ctx.locals[1]}"
    assert eng.compilations >= 1, "the loop must have been compiled"
    assert eng.jit_traces >= 1, "the compiled trace must have been executed"


def test_chain_next_defaults_to_interpreter_return():
    eng = IntegratedRuntimeEngine()
    eng.register_block(BasicBlock(0x100, [("i32.const", 7)], next_pc=None))
    eng.compile_queue = [0x100]
    eng.idle_hook()
    trace = eng.cache.active.traces[0x100]
    assert trace.chain_next is None, "unlinked trace must fall back to the stub"


def test_idle_hook_chains_into_a_warm_resident_successor():
    """Warm is still resident code (only Oldest gets cleared by rotate()),
    so a freshly compiled trace may chain directly into a Warm successor."""
    eng = IntegratedRuntimeEngine()
    eng.register_block(BasicBlock(0x200, [("i32.const", 1)], next_pc=None))
    eng.register_block(BasicBlock(0x100, [("i32.const", 2)], next_pc=0x200))
    eng.compile_queue = [0x200]
    eng.idle_hook()
    assert 0x200 in eng.cache.active.traces
    eng.cache.begin_patch()
    eng.cache.rotate()
    eng.cache.commit_patch()  # 0x200: Active -> Warm
    assert 0x200 in eng.cache.warm.traces
    eng.compile_queue = [0x100]
    eng.idle_hook()
    trace = eng.cache.active.traces[0x100]
    assert trace.chain_next == 0x200, "a Warm-resident successor must still be a valid chain target"


def test_idle_hook_never_chains_into_the_oldest_bank():
    """Oldest can be purged by the very next rotate(); a raw chain into it
    would also skip the promotion bookkeeping in `lookup()`."""
    eng = IntegratedRuntimeEngine()
    eng.register_block(BasicBlock(0x200, [("i32.const", 1)], next_pc=None))
    eng.register_block(BasicBlock(0x100, [("i32.const", 2)], next_pc=0x200))
    eng.compile_queue = [0x200]
    eng.idle_hook()
    eng.cache.begin_patch()
    eng.cache.rotate()
    eng.cache.rotate()
    eng.cache.commit_patch()
    assert 0x200 in eng.cache.oldest.traces
    eng.compile_queue = [0x100]
    eng.idle_hook()
    trace = eng.cache.active.traces[0x100]
    assert trace.chain_next is None, "Oldest is never a valid chain target"


def test_rotate_unlinks_chains_when_oldest_is_purged():
    """A link to a target in Warm remains valid when the target moves into Oldest
    (the code is still cached and executable). The link is unlinked only when
    Oldest is purged and rotated into the new Active bank."""
    cache = JITMultiBufferCache()
    cache.begin_patch()
    target = JITTrace(0x200, lambda ctx: "COMPLETED", 64)
    cache.insert(target)
    cache.rotate()  # target: Active -> Warm
    source = JITTrace(0x100, lambda ctx: "COMPLETED", 64)
    source.chain_next = 0x200  # simulate idle_hook having chained into Warm
    cache.register_chain(0x100, 0x200)
    cache.insert(source)
    cache.commit_patch()
    assert source.chain_next == 0x200, "sanity: link established while target is in Warm"
    cache.begin_patch()
    cache.rotate()
    cache.commit_patch()  # target: Warm -> Oldest
    assert source.chain_next == 0x200, (
        "target in Oldest is still valid and executable; link must remain intact"
    )
    cache.begin_patch()
    cache.rotate()
    cache.commit_patch()  # target: Oldest -> PURGED into Active
    assert source.chain_next is None, (
        "target was purged on rotation; inbound link must be unlinked to interpreter fallback"
    )


def test_rotate_rechains_when_target_was_promoted_to_active():
    """If a target in Oldest was promoted to Active before Oldest is purged,
    the inbound chain must be RE-CHAINED to the promoted trace rather than
    unlinked to the interpreter fallback."""
    cache = JITMultiBufferCache()
    cache.begin_patch()
    target = JITTrace(0x200, lambda ctx: "COMPLETED", 64)
    cache.insert(target)
    cache.rotate()  # target: Active -> Warm
    source = JITTrace(0x100, lambda ctx: "COMPLETED", 64)
    source.chain_next = 0x200  # link established
    cache.register_chain(0x100, 0x200)
    cache.insert(source)
    cache.commit_patch()
    cache.begin_patch()
    cache.rotate()
    cache.commit_patch()  # target: Warm -> Oldest
    assert 0x200 in cache.oldest.traces
    # Simulate execution of target while in Oldest -> triggers Oldest-Only Promotion to Active!
    promoted = cache.lookup(0x200)
    assert promoted is not None
    assert 0x200 in cache.active.traces
    # Now rotate again: Oldest is purged. Since target was promoted to Active, source must RE-CHAIN!
    cache.begin_patch()
    cache.rotate()
    cache.commit_patch()
    assert source.chain_next == 0x200, "source must be re-chained to promoted target in Active"
    assert 0x100 in cache.warm.inbound_sources, "inbound tracking must follow the promoted bank"


def test_eviction_makes_the_card_recompilable():
    """Purging a trace must reset its card, or the code is permanently deoptimised."""
    eng = IntegratedRuntimeEngine()
    eng.register_block(BasicBlock(0x100, [("i32.const", 1)], next_pc=None))
    eng.compile_queue = [0x100]
    eng.idle_hook()
    assert eng.bitmap.get_state(0x100) == CardState.COMPILED
    eng.cache.begin_patch()
    eng.cache.rotate()  # Active -> Warm
    eng.cache.rotate()  # Warm  -> Oldest
    eng.cache.rotate()  # Oldest purged
    eng.cache.commit_patch()
    assert 0x100 not in eng.cache.active.traces
    assert eng.bitmap.get_state(0x100) == CardState.UNEXECUTED, (
        "card must be re-compilable after its trace was purged, starting a full "
        "re-warm-up rather than jumping straight back to HOT after one touch"
    )


def test_short_blocks_never_tracked_avoiding_card_aliasing():
    """
    A card's 2-bit state can only ever describe one block. Two distinct
    block heads sharing a card would otherwise let compiling one falsely
    read back as "already compiled" for the other, or let evicting one
    falsely reset the other's still-resident COMPILED state. Blocks whose
    estimated compiled size is under one card's worth of bytes must never
    be recorded at all, so two tracked blocks can never land on the same
    card.
    """
    eng = IntegratedRuntimeEngine(card_shift=6)  # min_trace_bytes == 64
    assert eng.bitmap.card_of(0x100) == eng.bitmap.card_of(0x120), "same 64-byte card"
    # A single i32.const emits 3 native instructions * 4 bytes == 12 bytes, well under 64.
    eng.register_block(BasicBlock(0x100, [("i32.const", 1)], next_pc=0x120))
    eng.register_block(BasicBlock(0x120, [("i32.const", 2)], next_pc=0x140))
    ctx = WASMContext()
    for _ in range(eng.yield_threshold * 2):
        eng.run(0x100, ctx, max_blocks=1)
        eng.run(0x120, ctx, max_blocks=1)
    assert eng.bitmap.get_state(0x100) == CardState.UNEXECUTED
    assert eng.bitmap.get_state(0x120) == CardState.UNEXECUTED
    assert eng.compile_queue == [], "short blocks must never reach the compile queue"


def test_idle_hook_skips_recompiling_an_already_resident_trace():
    """
    If a pc is queued for compilation while a trace already resides in the
    cache under that exact pc (e.g. re-queued before an earlier compile's
    mark_compiled() landed), idle_hook must trust the cache -- the
    authority on whether *this* pc has a trace -- over the coarse per-card
    bitmap, and skip recompiling it.
    """
    eng = IntegratedRuntimeEngine()
    eng.register_block(BasicBlock(0x100, [("i32.const", 1)] * 4, next_pc=None))
    eng.cache.begin_patch()
    eng.cache.insert(JITTrace(0x100, lambda ctx: "COMPLETED", 64))
    eng.cache.commit_patch()
    eng.compile_queue = [0x100]

    compile_calls = []
    real = eng.compiler.compile_trace

    def spy(head_pc, block):
        compile_calls.append(head_pc)
        return real(head_pc, block)

    eng.compiler.compile_trace = spy

    compiled = eng.idle_hook()

    assert compiled == 0, "a pc already resident in the cache must not be recompiled"
    assert compile_calls == []
    assert eng.bitmap.get_state(0x100) == CardState.COMPILED


def test_used_bytes_does_not_leak_on_overwrite():
    bank = JITCacheBank(0, capacity_bytes=2048)
    for _ in range(5):
        bank.allocate(JITTrace(0x100, lambda ctx: "COMPLETED", 100))
    assert len(bank.traces) == 1
    assert bank.used_bytes == 100, f"overwrite must not accumulate: {bank.used_bytes}"


def test_warm_hit_does_not_promote_but_oldest_hit_does():
    cache = JITMultiBufferCache()
    cache.begin_patch()
    cache.insert(JITTrace(0x100, lambda ctx: "COMPLETED", 64))
    cache.commit_patch()
    cache.begin_patch()
    cache.rotate()
    cache.commit_patch()  # Active -> Warm
    assert 0x100 in cache.warm.traces
    before = cache.promotions
    cache.lookup(0x100)
    assert cache.promotions == before, "a Warm hit is a free observation window"
    cache.begin_patch()
    cache.rotate()
    cache.commit_patch()  # Warm -> Oldest
    assert 0x100 in cache.oldest.traces
    cache.lookup(0x100)
    assert cache.promotions == before + 1, "an Oldest hit promotes immediately to Active"
    assert 0x100 in cache.active.traces


def test_hotspot_bitmap_pure_2bit_state_transitions():
    """Pure 2-bit FSM: UNEXECUTED -> EXECUTED -> HOT -> COMPILED -> UNEXECUTED (evict)."""
    bm = HotspotBitmap(card_shift=6)
    assert bm.get_state(0x100) == CardState.UNEXECUTED
    assert bm.touch(0x100) == CardState.EXECUTED
    assert bm.touch(0x100) == CardState.HOT
    # Remains HOT until compilation
    assert bm.touch(0x100) == CardState.HOT
    bm.mark_compiled(0x100)
    assert bm.get_state(0x100) == CardState.COMPILED
    assert bm.touch(0x100) == CardState.COMPILED
    bm.mark_evicted(0x100)
    assert bm.get_state(0x100) == CardState.UNEXECUTED, (
        "eviction must force a full re-warm-up, not just one touch back to HOT"
    )


def test_mpu_wx_is_enforced_in_both_directions():
    cache = JITMultiBufferCache()
    try:
        cache.insert(JITTrace(0x100, lambda ctx: "COMPLETED", 64))
        raise AssertionError("write under RO_X must fault")
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)

    cache.begin_patch()
    try:
        cache.require_executable()
        raise AssertionError("execute under RW_XN must fault")
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)


def test_safepoint_is_distinct_from_cooperative_yield():
    """A yield is trace-count driven; a Safepoint is interrupt driven."""
    eng, ctx = _countdown_engine(yield_threshold=2)
    ctx.locals = [50, 1]
    eng.run(0x100, ctx, max_blocks=10)
    assert eng.yields >= 1, "cooperative yields fire on the trace counter alone"
    assert ctx.safepoints_hit == 0, "no interrupt was raised, so no Safepoint fired"
    eng2, ctx2 = _countdown_engine(yield_threshold=2)
    ctx2.locals = [50, 1]
    eng2.run(0x100, ctx2, max_blocks=6)
    eng2.on_yield()
    eng2.idle_hook()
    ctx2.raise_interrupt()
    status = eng2.run(0x100, ctx2, max_blocks=10)
    assert status == "SAFEPOINT_YIELD"
    assert ctx2.safepoints_hit == 1


def test_stencil_requires_its_relocation_holes():
    c = CopyPatchCompiler()
    try:
        c.stencils["i32.const"].emit(imm_lo=1)
        raise AssertionError("missing relocation must be rejected")
    except KeyError as e:
        assert "imm_hi" in str(e)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("[PASS] All integrated tracing runtime concept tests passed.")

"""Tier 3 JIT profiling state and rotating code-cache components.

The Tier 2 runtime owns execution orchestration. C++ owns JIT algorithms;
this module retains borrowed buffers and trace wrappers at the Python boundary.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from bump_allocator import BumpAllocator
from config import (
    JIT_CACHE_ACTIVE_OFFSET_BYTES,
    JIT_CACHE_BANK_CAPACITY_BYTES,
    JIT_CACHE_BANK_COUNT,
    JIT_CACHE_BANK_ENTRY_CAPACITY,
    JIT_CACHE_OLDEST_OFFSET_BYTES,
    JIT_CACHE_WARM_OFFSET_BYTES,
    JIT_CARD_SHIFT,
    JIT_TRACE_DEFAULT_BYTES,
    JIT_X64_TRACE_HEADER_BYTES,
)
from system_containers import (
    MutableBitStorage,
    StaticVector,
)
from tier2_runtime.wasm.module import IntegerSequence

from . import native_abi
from .common_code import (
    COMMON_HELPER_OFFSET,
    JITCodeCacheRegion,
)
from .exec_memory import ExecutableBuffer
from .native_abi import CacheField

if TYPE_CHECKING:
    from tier2_runtime.interpreter.interpreter import ExecutionContext


NativeTraceFn = Callable[[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, int], int | None]
TraceArgument = ctypes.c_void_p


class CardState:
    __slots__ = ()

    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2  # Queued for compilation
    COMPILED = 3


class _CodeRegionLayout:
    """Borrow loader lengths/bases to obtain the native code-region layout."""

    __slots__ = ("byte_length",)

    def __init__(self, code_lengths: IntegerSequence, function_pc_bases: IntegerSequence) -> None:
        assert all(0 <= value <= 0xFFFF_FFFF for value in code_lengths)
        assert all(0 <= value <= 0xFFFF_FFFF for value in function_pc_bases)
        assert not function_pc_bases or len(function_pc_bases) == len(code_lengths)
        lengths = (ctypes.c_uint32 * len(code_lengths))(*code_lengths)
        bases = (
            (ctypes.c_uint32 * len(function_pc_bases))(*function_pc_bases)
            if function_pc_bases
            else None
        )
        size = ctypes.c_uint64()
        assert native_abi.REGION_SIZE(lengths, bases, len(lengths), ctypes.byref(size)) == 1
        self.byte_length = size.value

    def card_index(self, pc: int, card_shift: int) -> int:
        assert 0 <= pc <= 0xFFFF_FFFF
        index = int(native_abi.CARD_INDEX(self.byte_length, card_shift, pc))
        assert index >= 0
        return index

    def card_count(self, shift: int) -> int:
        count = int(native_abi.CARD_COUNT(self.byte_length, shift))
        assert count >= 0
        return count


class _NativeBits:
    """Ctypes view over caller-owned packed bit storage."""

    __slots__ = ("_bits", "_count", "_data")

    def __init__(self, storage: MutableBitStorage, count: int, bits: int) -> None:
        self._data = (ctypes.c_uint8 * len(storage.buffer)).from_buffer(storage.buffer)
        self._count, self._bits = count, bits

    def operation(self, index: int = 0, operation: int = 0, value: int = 0) -> int:
        assert 0 <= index <= 0xFFFF_FFFF and 0 <= value <= 0xFFFF_FFFF
        result = int(native_abi.BITS(self._data, self._count, self._bits, index, operation, value))
        assert result >= 0, "native packed-card contract violation"
        return result


class HotspotBitmap:
    """Boundary view of the native two-bit hotspot state machine."""

    __slots__ = ("_bits", "card_shift", "region", "storage")

    def __init__(
        self,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: IntegerSequence = (),
        function_pc_bases: IntegerSequence = (),
    ) -> None:
        assert 0 <= card_shift < 32
        self.card_shift = card_shift
        self.region = _CodeRegionLayout(code_lengths, function_pc_bases)
        self.storage = MutableBitStorage(count=self.card_count, bits=2)
        self._bits = _NativeBits(self.storage, self.card_count, 2)

    @property
    def card_count(self) -> int:
        return self.region.card_count(self.card_shift)

    def card_of(self, pc: int) -> int:
        return self.region.card_index(pc, self.card_shift)

    def get_card_state(self, card_index: int) -> int:
        return self._bits.operation(card_index)

    def get_state(self, pc: int) -> int:
        return self.get_card_state(self.card_of(pc))

    def touch(self, pc: int) -> int:
        return self._bits.operation(self.card_of(pc), 2)

    def mark_compiled(self, pc: int) -> None:
        self._bits.operation(self.card_of(pc), 1, CardState.COMPILED)

    def mark_evicted(self, pc: int) -> None:
        self._bits.operation(self.card_of(pc), 1, CardState.UNEXECUTED)

    def decay_executed_card(self, card_index: int) -> int:
        return self._bits.operation(card_index, 3)


class CardUpdateBitmap:
    """Boundary view of the native dirty-card bitmap and aging cursor."""

    __slots__ = ("_bits", "_cursor", "card_count", "storage")
    UNIT_CARDS: ClassVar[int] = 8

    def __init__(self, card_count: int = 0) -> None:
        assert 0 <= card_count <= 0xFFFF_FFFF
        self.card_count = card_count
        self.storage = MutableBitStorage(count=card_count, bits=1)
        self._bits = _NativeBits(self.storage, card_count, 1)
        self._cursor = ctypes.c_uint32(0)

    @property
    def cursor(self) -> int:
        return self._cursor.value

    @cursor.setter
    def cursor(self, value: int) -> None:
        self._cursor.value = self._bits.operation(operation=6, value=value)

    @property
    def unit_count(self) -> int:
        return int(native_abi.CARD_COUNT(self.card_count, 3))

    def mark(self, card_index: int) -> None:
        self._bits.operation(card_index, 1, 1)

    def unmark(self, card_index: int) -> None:
        self._bits.operation(card_index, 1, 0)

    def is_marked(self, card_index: int) -> bool:
        return self._bits.operation(card_index) != 0

    def unit(self, byte: int) -> int:
        return self._bits.operation(byte, 5)


class BlockCardMask:
    """Boundary view of native candidate/suppression bits."""

    __slots__ = ("_bits", "card_shift", "region", "storage")

    def __init__(
        self,
        card_shift: int = JIT_CARD_SHIFT,
        code_lengths: IntegerSequence = (),
        function_pc_bases: IntegerSequence = (),
    ) -> None:
        assert 0 <= card_shift < 32
        self.card_shift = card_shift
        self.region = _CodeRegionLayout(code_lengths, function_pc_bases)
        self.storage = MutableBitStorage(count=self.card_count, bits=1)
        self._bits = _NativeBits(self.storage, self.card_count, 1)

    @property
    def card_count(self) -> int:
        return self.region.card_count(self.card_shift)

    def mark(self, pc: int) -> None:
        self._bits.operation(self.region.card_index(pc, self.card_shift), 1, 1)

    def is_marked(self, pc: int) -> bool:
        return self._bits.operation(self.region.card_index(pc, self.card_shift)) != 0

    def unmark(self, pc: int) -> None:
        self._bits.operation(self.region.card_index(pc, self.card_shift), 1, 0)

    def clear(self) -> None:
        self._bits.operation(operation=4)


class JITTraceHeader:
    """Code-adjacent x64 targets read by generated/common native code.

    +0x00 chain_target_addr(u64), +0x08 helper_target_addr(u64).
    Installation-time relocation metadata is not serialized into the header.
    """

    __slots__ = ("_native", "helper_target_addr")

    def __init__(self, helper_target_addr: int = 0):
        self._native = native_abi.NativeCacheTrace(
            next_pc=0xFFFF_FFFF,
            loops_to=0xFFFF_FFFF,
            dispatch_next_pc=0xFFFF_FFFF,
            dispatch_loops_to=0xFFFF_FFFF,
            code_offset=0xFFFF_FFFF,
            chain_next_pc=0xFFFF_FFFF,
        )
        assert 0 <= helper_target_addr <= 0xFFFF_FFFF_FFFF_FFFF
        self.helper_target_addr = helper_target_addr

    @property
    def chain_target_addr(self) -> int:
        return self._native.chain_target_address

    @chain_target_addr.setter
    def chain_target_addr(self, value: int) -> None:
        assert 0 <= value <= 0xFFFF_FFFF_FFFF_FFFF
        self._native.chain_target_address = value

    def pack(self) -> bytes:
        return native_abi.pack_trace_header(self.chain_target_addr, self.helper_target_addr)


class JITTrace:
    """Compiled native trace descriptor backed by JITTraceHeader and native ctypes function pointer."""

    __slots__ = (
        "_blob_storage",
        "_exec_buf",
        "_exec_count",
        "_native_owner",
        "chain_dispatch_patch_offset",
        "code_blob",
        "common_helper_offset",
        "entry_body_patch_offset",
        "entry_prologue_patch_offset",
        "exit_patch_offset",
        "fn",
        "header",
        "helper_exit_patch_offset",
        "helper_header_patch_offset",
    )

    FLAG_PROMOTED = 0x01

    def __init__(
        self,
        head_pc: int,
        fn: NativeTraceFn | None = None,
        size_bytes: int = JIT_TRACE_DEFAULT_BYTES,
        next_pc: int | None = None,
        loops_to: int | None = None,
        has_return_val: bool = False,
        result_words: int = 1,
        stack_words: int = 1,
        buf: ExecutableBuffer | None = None,
        native_fn: NativeTraceFn | None = None,
        raw_addr: int | None = None,
        code_blob: bytes | None = None,
        entry_body_patch_offset: int = -1,
        entry_prologue_patch_offset: int = -1,
        exit_patch_offset: int = -1,
        helper_header_patch_offset: int = -1,
        helper_exit_patch_offset: int = -1,
        chain_dispatch_patch_offset: int = -1,
        helper_target_addr: int = 0,
    ):
        self.header = JITTraceHeader(helper_target_addr)
        self.head_pc = head_pc
        self.fn = fn or native_fn  # Direct ctypes CFUNCTYPE function pointer or callable
        self.raw_addr = raw_addr  # Entry point address consumed by native dispatch
        self.code_blob = code_blob
        self._blob_storage: ctypes.c_char_p | None = None
        self._native_owner: native_abi.NativeRuntimeStorage | None = None
        self.code_offset = None
        self.common_helper_offset = COMMON_HELPER_OFFSET
        self.entry_body_patch_offset = entry_body_patch_offset
        self.entry_prologue_patch_offset = entry_prologue_patch_offset
        self.exit_patch_offset = exit_patch_offset
        self.helper_header_patch_offset = helper_header_patch_offset
        self.helper_exit_patch_offset = helper_exit_patch_offset
        self.chain_dispatch_patch_offset = chain_dispatch_patch_offset
        assert 0 <= head_pc < 0xFFFF_FFFF
        assert JIT_X64_TRACE_HEADER_BYTES <= size_bytes <= 0xFFFF_FFFF
        self.size_bytes = size_bytes
        self.next_pc = next_pc  # Unconditional fallthrough successor
        self.loops_to = loops_to  # Conditional loop backedge (never auto-chained)
        self.has_return_val = has_return_val
        assert result_words > 0
        self.result_words = result_words
        # Raw words the trace may write from `sp` upward: spilled cache entries, raw
        # constants for helpers, and the residual value. The C++ dispatcher checks the
        # resident descriptor's chain requirement before entering the trace.
        assert stack_words >= 1
        self.stack_words = stack_words
        self.flags = 0
        self.chain_next = None
        self._exec_count = ctypes.c_uint32(0)
        self._exec_buf = buf  # Keeps executable buffer alive in memory
        self._native.exec_count = ctypes.pointer(self._exec_count)
        self.bind_code()

    def bind_code(self) -> None:
        """Keep external code bytes alive across the registration boundary."""
        if self.code_blob is not None:
            self._blob_storage = ctypes.c_char_p(self.code_blob)
            self._native.code_blob = ctypes.cast(self._blob_storage, ctypes.POINTER(ctypes.c_uint8))
            self._native.blob_bytes = len(self.code_blob)
        self._native.fixups = native_abi.NativeTraceFixups(
            self.entry_body_patch_offset,
            self.entry_prologue_patch_offset,
            self.exit_patch_offset,
            self.helper_header_patch_offset,
            self.helper_exit_patch_offset,
            self.chain_dispatch_patch_offset,
            self.common_helper_offset,
        )

    @classmethod
    def borrow(
        cls,
        pointer: ctypes._Pointer[native_abi.NativeCacheTrace],
        owner: native_abi.NativeRuntimeStorage,
    ) -> JITTrace:
        """Expose a resident native descriptor without copying its state or code."""
        trace = cls.__new__(cls)
        trace.header = JITTraceHeader.__new__(JITTraceHeader)
        trace.header._native = pointer.contents
        trace.header.helper_target_addr = 0
        trace._native_owner = owner
        trace._blob_storage = None
        trace.code_blob = None
        trace._exec_buf = None
        trace.fn = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
        )(pointer.contents.entry_address)
        trace.common_helper_offset = pointer.contents.fixups.helper_offset
        trace.entry_body_patch_offset = pointer.contents.fixups.entry_body
        trace.entry_prologue_patch_offset = pointer.contents.fixups.entry_prologue
        trace.exit_patch_offset = pointer.contents.fixups.exit
        trace.helper_header_patch_offset = pointer.contents.fixups.helper_header
        trace.helper_exit_patch_offset = pointer.contents.fixups.helper_exit
        trace.chain_dispatch_patch_offset = pointer.contents.fixups.chain_dispatch
        return trace

    @property
    def _native(self) -> native_abi.NativeCacheTrace:
        return self.header._native

    @property
    def head_pc(self) -> int:
        return self._native.head_pc

    @head_pc.setter
    def head_pc(self, value: int) -> None:
        assert 0 <= value < 0xFFFF_FFFF
        self._native.head_pc = value

    @property
    def next_pc(self) -> int | None:
        value = self._native.next_pc
        return None if value == 0xFFFF_FFFF else value

    @next_pc.setter
    def next_pc(self, value: int | None) -> None:
        assert value is None or 0 <= value < 0xFFFF_FFFF
        self._native.next_pc = 0xFFFF_FFFF if value is None else value

    @property
    def loops_to(self) -> int | None:
        value = self._native.loops_to
        return None if value == 0xFFFF_FFFF else value

    @loops_to.setter
    def loops_to(self, value: int | None) -> None:
        assert value is None or 0 <= value < 0xFFFF_FFFF
        self._native.loops_to = 0xFFFF_FFFF if value is None else value

    @property
    def size_bytes(self) -> int:
        return self._native.size_bytes

    @size_bytes.setter
    def size_bytes(self, value: int) -> None:
        self._native.size_bytes = value

    @property
    def has_return_val(self) -> bool:
        return bool(self._native.has_return_value)

    @has_return_val.setter
    def has_return_val(self, value: bool) -> None:
        self._native.has_return_value = int(value)

    @property
    def result_words(self) -> int:
        return self._native.result_words

    @result_words.setter
    def result_words(self, value: int) -> None:
        self._native.result_words = value

    @property
    def stack_words(self) -> int:
        return self._native.stack_words

    @stack_words.setter
    def stack_words(self, value: int) -> None:
        assert 1 <= value <= 0xFFFF_FFFF
        self._native.stack_words = value

    @property
    def code_offset(self) -> int | None:
        value = self._native.code_offset
        return None if value == 0xFFFF_FFFF else value

    @code_offset.setter
    def code_offset(self, value: int | None) -> None:
        self._native.code_offset = 0xFFFF_FFFF if value is None else value

    @property
    def raw_addr(self) -> int | None:
        return self._native.entry_address or None

    @raw_addr.setter
    def raw_addr(self, value: int | None) -> None:
        self._native.entry_address = value or 0

    @property
    def flags(self) -> int:
        return self._native.flags

    @flags.setter
    def flags(self, value: int) -> None:
        self._native.flags = value

    @property
    def chain_next(self) -> int | None:
        value = self._native.chain_next_pc
        return None if value == 0xFFFF_FFFF else value

    @chain_next.setter
    def chain_next(self, value: int | None) -> None:
        self._native.chain_next_pc = 0xFFFF_FFFF if value is None else value

    @property
    def exec_count(self) -> int:
        """Runtime-dispatched body executions in the current measurement interval."""

        return self._native.exec_count.contents.value

    @exec_count.setter
    def exec_count(self, value: int) -> None:
        assert 0 <= value <= 0xFFFF_FFFF
        self._native.exec_count.contents.value = value

    @property
    def exec_count_address(self) -> int:
        """Borrow the trace-owned counter without copying it into a snapshot."""

        return ctypes.addressof(self._native.exec_count.contents)

    @property
    def native_fn(self) -> NativeTraceFn | None:
        return self.fn

    def __call__(
        self,
        ctx_or_locals: TraceArgument,
        sp_or_mem: TraceArgument,
        local_base: TraceArgument,
        tos: int,
    ) -> int | None:
        """
        Invokes the native JIT trace directly via ctypes CPS 4-argument calling convention:
                (void* ctx, void* sp, void* local_base, uint32_t tos)
        """

        assert self.fn is not None
        return self.fn(ctx_or_locals, sp_or_mem, local_base, tos)

    def invoke(self, ctx: ExecutionContext) -> int:
        """Invoke a trace directly on the shared context through its CPS ABI."""
        result_slot = len(ctx.stack)
        # The interpreter owns the shared stack before entry.  A trace that
        # needs an input operand is rejected during compilation; therefore no
        # entry value is popped or copied into a JIT-owned stack.
        self.execute(ctx.context_ptr, ctx.sp_ptr, ctx.locals_ptr, 0)
        if not self.has_return_val:
            return 0
        ctx.stack.set_size(result_slot + self.result_words)
        result = ctx.stack[result_slot]
        return result

    def execute(
        self, ctx: TraceArgument, sp: TraceArgument, local_base: TraceArgument, tos: int
    ) -> None:
        """Execute the compiled PIC entry point with the shared CPS arguments."""

        if self.fn is not None:
            self.fn(ctx, sp, local_base, tos)
            return
        assert self.raw_addr is not None
        ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)(
            self.raw_addr
        )(ctx, sp, local_base, tos)


class JitRuntimeBoundary:
    """Host adapter for the Tier 3 C++ JitRuntime's residency operations.

    Python anchors only descriptors explicitly supplied through this boundary.
    Native-owned traces need no Python reference registry.
    C++ owns all bank, index, chain, promotion, rotation and aging decisions.
    Native compilation, installation and patching stay inside the C++ owner.
    """

    __slots__ = (
        "_busy",
        "_callback_error",
        "_code_region",
        "_compile_observer",
        "_host",
        "_native",
        "_references",
        "_retire_observer",
        "metadata_provider",
    )

    def __init__(
        self,
        bank_capacity: int = JIT_CACHE_BANK_CAPACITY_BYTES,
        allocator: BumpAllocator | None = None,
        entry_capacity: int = JIT_CACHE_BANK_ENTRY_CAPACITY,
        compile_observer: Callable[[int, bool, int], None] | None = None,
        retire_observer: Callable[[int, int], None] | None = None,
    ) -> None:
        assert JIT_X64_TRACE_HEADER_BYTES <= bank_capacity <= JIT_CACHE_BANK_CAPACITY_BYTES
        assert 0 < entry_capacity <= JIT_CACHE_BANK_ENTRY_CAPACITY
        self._references: StaticVector[JITTrace | None] | None = None
        self._callback_error: BaseException | None = None
        self._busy = False
        self._retire_observer = retire_observer
        self.metadata_provider: Callable[[JITTrace], None] | None = None
        self._compile_observer = compile_observer
        self._host = native_abi.NativeRuntimeHost(
            native_abi.CacheReleaseExternal(self._release_external),
            native_abi.CacheRetired(self._retired)
            if retire_observer is not None
            else native_abi.CacheRetired(),
            native_abi.CacheCompiled(self._compiled)
            if compile_observer is not None
            else native_abi.CacheCompiled(),
        )
        self._native = native_abi.NativeRuntimeStorage(
            bank_capacity,
            entry_capacity,
            (
                JIT_CACHE_ACTIVE_OFFSET_BYTES,
                JIT_CACHE_WARM_OFFSET_BYTES,
                JIT_CACHE_OLDEST_OFFSET_BYTES,
            ),
            self._host,
        )
        self._code_region: JITCodeCacheRegion | None = None
        if allocator is not None:
            self.bind_allocator(allocator)

    def _begin(self) -> None:
        assert not self._busy, "cache collection callbacks must not re-enter residency operations"
        self._busy = True
        self._callback_error = None

    def _check(self) -> None:
        self._busy = False
        # ctypes callbacks cannot propagate Python exceptions through C++. The
        # callback returns failure; this boundary immediately restores fail-fast.
        assert self._callback_error is None, self._callback_error
        assert native_abi.RUNTIME_ERROR(self._native.pointer) == 0, (
            "native cache contract violation"
        )

    def _reference(self, token: int) -> JITTrace | None:
        if token == 0:
            return None
        if token >> 63:
            pointer = native_abi.RUNTIME_TRACE(self._native.pointer, token)
            assert bool(pointer)
            return JITTrace.borrow(pointer, self._native)
        assert self._references is not None
        assert 0 < token <= len(self._references)
        trace = self._references[token - 1]
        assert trace is not None, "native descriptor outlived its owning trace wrapper"
        return trace

    def _keep(self, trace: JITTrace) -> tuple[int, bool]:
        if self._references is None:
            self._references = StaticVector(
                capacity=JIT_CACHE_BANK_COUNT * JIT_CACHE_BANK_ENTRY_CAPACITY + 1
            )
            for _ in range(self._references.capacity):
                self._references.append(None)
        empty = -1
        for index in range(len(self._references)):
            if self._references[index] is trace:
                return index + 1, False
            if self._references[index] is None and empty < 0:
                empty = index
        assert empty >= 0, "fixed trace lifetime registry is full"
        self._references[empty] = trace
        return empty + 1, True

    def _compiled(self, pc: int, status: int, elapsed_ns: int) -> int:
        try:
            assert self._compile_observer is not None
            self._compile_observer(pc, status == 1, elapsed_ns)
            return 1
        except BaseException as error:
            self._callback_error = error
            return 0

    def _release_external(self, token: int) -> int:
        try:
            assert self._references is not None
            assert 0 < token <= len(self._references)
            assert self._references[token - 1] is not None
            self._references[token - 1] = None
            return 1
        except BaseException as error:
            self._callback_error = error
            return 0

    def _retired(self, pc: int, count: int) -> int:
        try:
            assert self._retire_observer is not None
            self._retire_observer(pc, count)
            return 1
        except BaseException as error:
            self._callback_error = error
            return 0

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        self._native.bind_allocator(allocator)

    def bind_cards(
        self, bitmap: HotspotBitmap, update: CardUpdateBitmap, units: int, scan_bytes: int
    ) -> None:
        assert bitmap.card_count == update.card_count
        self._native.bind_cards(
            bitmap.storage.buffer,
            update.storage.buffer,
            bitmap.card_count,
            bitmap.card_shift,
            update._cursor,
            units,
            scan_bytes,
        )

    def enable_automatic_aging(self, enabled: bool) -> None:
        native_abi.RUNTIME_AUTO_AGE(self._native.pointer, int(enabled))

    def age_step(self) -> int:
        result = int(native_abi.RUNTIME_AGE(self._native.pointer))
        assert result >= 0 and native_abi.RUNTIME_ERROR(self._native.pointer) == 0
        return result

    @property
    def active_idx(self) -> int:
        return self._native.scalar(CacheField.ACTIVE_BANK)

    @property
    def warm_idx(self) -> int:
        return self._native.scalar(CacheField.WARM_BANK)

    @property
    def oldest_idx(self) -> int:
        return self._native.scalar(CacheField.OLDEST_BANK)

    @property
    def promotions(self) -> int:
        return self._native.scalar(CacheField.PROMOTIONS)

    @property
    def evictions(self) -> int:
        return self._native.scalar(CacheField.EVICTIONS)

    @property
    def generation(self) -> int:
        return self._native.scalar(CacheField.GENERATION)

    @property
    def resident_count(self) -> int:
        return self._native.scalar(CacheField.RESIDENT_COUNT, JIT_CACHE_BANK_COUNT)

    @property
    def resident_bytes(self) -> int:
        return self._native.scalar(CacheField.BANK_USED, JIT_CACHE_BANK_COUNT)

    @property
    def entry_capacity(self) -> int:
        return self._native.scalar(CacheField.BANK_ENTRY_CAPACITY, JIT_CACHE_BANK_COUNT)

    @property
    def rotations(self) -> int:
        return self._native.scalar(CacheField.ROTATIONS)

    @property
    def aging_ns(self) -> int:
        return self._native.scalar(CacheField.AGING_NS)

    @property
    def compile_attempts(self) -> int:
        return self._native.scalar(CacheField.COMPILE_ATTEMPTS)

    @property
    def compile_ns(self) -> int:
        return self._native.scalar(CacheField.COMPILE_NS)

    @property
    def common_code(self) -> JITCodeCacheRegion:
        if self._code_region is None:
            self._code_region = JITCodeCacheRegion(
                ExecutableBuffer.borrow(
                    native_abi.RUNTIME_MEMORY(self._native.pointer), self._native
                )
            )
        return self._code_region

    def find_trace(self, head_pc: int) -> JITTrace | None:
        return self._reference(int(native_abi.RUNTIME_FIND(self._native.pointer, head_pc)))

    def find_bank(self, head_pc: int) -> int | None:
        bank_id = int(native_abi.RUNTIME_BANK(self._native.pointer, head_pc))
        assert -1 <= bank_id < JIT_CACHE_BANK_COUNT
        return None if bank_id < 0 else bank_id

    def insert(self, trace: JITTrace) -> bool:
        if self.metadata_provider is not None:
            self.metadata_provider(trace)
        trace.bind_code()
        self._begin()
        token, added = self._keep(trace)
        assert self._references is not None
        result = native_abi.RUNTIME_INSERT(self._native.pointer, ctypes.byref(trace._native), token)
        if not result and added and not native_abi.RUNTIME_HAS_TOKEN(self._native.pointer, token):
            self._references[token - 1] = None
        self._check()
        return result == 1

    def lookup(self, head_pc: int) -> JITTrace | None:
        self._begin()
        token = int(native_abi.RUNTIME_LOOKUP(self._native.pointer, head_pc))
        self._check()
        return self._reference(token)

    def promote(self, head_pc: int) -> None:
        """Resume native dispatch after its Oldest boundary without creating a trace wrapper."""
        self._begin()
        token = int(native_abi.RUNTIME_LOOKUP(self._native.pointer, head_pc))
        self._check()
        assert token != 0, "native dispatch stopped at a missing trace"

    def rotate(self) -> None:
        self._begin()
        result = native_abi.RUNTIME_ROTATE(self._native.pointer)
        self._check()
        assert result == 1

    def flush_all(self) -> None:
        self._begin()
        result = native_abi.RUNTIME_FLUSH(self._native.pointer)
        self._check()
        assert result == 1

    def build_snapshot(self, entries: ctypes.Array) -> int:
        count = int(native_abi.RUNTIME_SNAPSHOT(self._native.pointer, entries, len(entries)))
        assert count >= 0 and native_abi.RUNTIME_ERROR(self._native.pointer) == 0
        return count

    def reset_execution_counts(self) -> None:
        native_abi.RUNTIME_RESET_COUNTS(self._native.pointer)

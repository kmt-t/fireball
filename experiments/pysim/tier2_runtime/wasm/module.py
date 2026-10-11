"""
experiments/pysim/tier2_runtime/wasm/module.py
In-memory representation of a parsed WASM module: the MVP binary format
needed to run real, non-trivial exported functions (arithmetic, locals,
globals, structured control flow, direct and indirect calls, linear
memory, and imported host functions -- Fireball's `fireball_call` syscall
bridge in miniature). No multi-value returns.
Function index space (per the WASM spec): imported functions occupy
indices [0, len(imports)), and locally-defined functions occupy
[len(imports), len(imports)+len(functions)). `call` targets, exports, and
the Code section's implicit numbering are all in this unified space.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import tier2_runtime.wasm.opcodes as op
from bump_allocator import BumpAllocator
from config import FB_CONF_MAX_LOCALS
from system_containers import (
    CtypesU32Buffer,
    ReadOnlyFlatMapStorage,
    ReadOnlyRadixBinaryTreeStorage,
    SequenceView,
    StaticVector,
    fold_mix32,
)
from tier2_runtime.wasm.leb128 import decode_signed, decode_unsigned

if TYPE_CHECKING:
    from tier2_runtime.interpreter.control_flow import ControlMap


WasmOperand = int | None
ElementInitializer = Callable[[int, int, int], None]
DataInitializer = Callable[[int, memoryview], None]


class BasicBlock(ctypes.Structure):
    """
    A straight-line run of WASM instructions ending with branch/return, as PC
    range + control-flow metadata only. Decoded instructions are never stored
    in the module; consumers obtain a one-shot iterator over raw bytecode. A
    zero-length entry marks a call-resume PC that needs a constant-pool start
    but contains no JIT-eligible instructions. The pool start is stored here
    so every block owns the metadata needed to initialize `rel`.
    """

    __slots__ = ()
    _fields_ = (
        ("head_pc", ctypes.c_uint32),
        ("function_index", ctypes.c_uint32),
        ("_next_pc", ctypes.c_uint32),
        ("_loops_to", ctypes.c_uint32),
        ("frame_depth", ctypes.c_uint16),
        ("jit_cache_reference", ctypes.c_uint8),
        ("_reserved0", ctypes.c_uint8),
        ("byte_span", ctypes.c_uint32),
        ("interpreter_constant_byte_offset", ctypes.c_uint32),
    )

    def __init__(
        self,
        head_pc: int,
        func_index: int = 0,
        next_pc: int | None = None,
        loops_to: int | None = None,
        frame_depth: int = 0,
        byte_span: int = 0,
        interpreter_constant_byte_offset: int = 0,
    ) -> None:
        self.head_pc = head_pc
        self.function_index = func_index
        self.next_pc = next_pc
        self.loops_to = loops_to
        assert 0 <= frame_depth <= 0xFFFF
        self.frame_depth = frame_depth
        self.jit_cache_reference = 0xFF
        self._reserved0 = 0
        self.byte_span = byte_span
        self.interpreter_constant_byte_offset = interpreter_constant_byte_offset

    @property
    def func_index(self) -> int:
        return int(self.function_index)

    @property
    def next_pc(self) -> int | None:
        value = int(self._next_pc)
        return None if value == 0xFFFF_FFFF else value

    @next_pc.setter
    def next_pc(self, value: int | None) -> None:
        self._next_pc = 0xFFFF_FFFF if value is None else value

    @property
    def loops_to(self) -> int | None:
        value = int(self._loops_to)
        return None if value == 0xFFFF_FFFF else value

    @loops_to.setter
    def loops_to(self, value: int | None) -> None:
        self._loops_to = 0xFFFF_FFFF if value is None else value


class BasicBlockStorage:
    """Bounded contiguous BasicBlock storage shared with the native ABI."""

    __slots__ = ("_arena_offset", "_arena_size", "_entries", "_native", "capacity")

    def __init__(
        self,
        capacity: int,
        allocator: BumpAllocator | None = None,
        arena_offset: int | None = None,
        arena_size: int | None = None,
    ) -> None:
        assert capacity >= 0
        self.capacity = capacity
        self._native = (BasicBlock * capacity)()
        # Keep stable ctypes views for Python identity semantics. The views
        # point into _native; record bytes exist only in that ABI array.
        self._entries: StaticVector[BasicBlock] = StaticVector(capacity=capacity)
        self._arena_offset = arena_offset
        self._arena_size = (
            capacity * ctypes.sizeof(BasicBlock) if arena_size is None else arena_size
        )
        assert self._arena_size == capacity * ctypes.sizeof(BasicBlock)
        if allocator is not None and self._arena_size:
            assert self._arena_offset is None
            self._arena_offset = allocator.allocate(self._arena_size, ctypes.alignment(BasicBlock))

    @property
    def native_address(self) -> int:
        """Return the first ABI record in the contiguous loader-owned array."""

        return 0 if not self._entries else ctypes.addressof(self._native)

    @property
    def arena_size(self) -> int:
        return self._arena_size

    def append(self, block: BasicBlock) -> None:
        assert len(self._entries) < self.capacity
        index = len(self._entries)
        self._native[index] = block
        self._entries.append(self._native[index])

    def relocate_arena(self, offset_delta: int) -> None:
        if self._arena_offset is not None:
            relocated_offset = self._arena_offset + offset_delta
            assert relocated_offset >= 0
            self._arena_offset = relocated_offset

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index: int) -> BasicBlock:
        return self._entries[index]

    def __iter__(self) -> Iterator[BasicBlock]:
        return iter(self._entries)


class InterpreterConstantPool:
    """Loader-owned packed constants and instruction widths exposed as bytes."""

    __slots__ = ("_arena_offset", "_bytes", "_size", "capacity")

    def __init__(self, capacity: int, allocator: BumpAllocator | None = None) -> None:
        assert capacity >= 0
        self.capacity = capacity
        self._size = 0
        self._bytes = (ctypes.c_uint8 * capacity)()
        self._arena_offset = (
            allocator.allocate(ctypes.sizeof(self._bytes), alignment=1)
            if allocator is not None and capacity
            else None
        )

    @property
    def native_address(self) -> int:
        return ctypes.addressof(self._bytes) if self._size else 0

    def append_byte(self, value: int) -> None:
        assert self._size < self.capacity
        assert 0 <= value <= 0xFF
        self._bytes[self._size] = value
        self._size += 1

    def append(self, value: int) -> None:
        assert 0 <= value <= 0xFFFF_FFFF
        assert self.capacity - self._size >= WASM_RAW_WORD_BYTES
        for byte_index in range(WASM_RAW_WORD_BYTES):
            self.append_byte((value >> (byte_index * 8)) & 0xFF)

    def __len__(self) -> int:
        return self._size

    def __getitem__(self, index: int) -> int:
        if index < 0:
            index += self._size
        assert 0 <= index < self._size
        return int(self._bytes[index])

    def __iter__(self) -> Iterator[int]:
        for index in range(self._size):
            yield self[index]

    def relocate_arena(self, offset_delta: int) -> None:
        if self._arena_offset is not None:
            relocated_offset = self._arena_offset + offset_delta
            assert relocated_offset >= 0
            self._arena_offset = relocated_offset


class NativeControlMapEntry(ctypes.Structure):
    """Flat control opener metadata shared with the native interpreter ABI."""

    __slots__ = ()
    _fields_ = (
        ("match_end", ctypes.c_uint32),
        ("else_offset", ctypes.c_uint32),
        ("next_pc", ctypes.c_uint32),
        ("result_arity", ctypes.c_uint32),
        ("operand_width", ctypes.c_uint32),
    )


# WASM value types are stored as their one-byte binary encoding.  Keeping the
# encoding avoids a per-type string object and matches the module's ROM bytes.
I32 = 0x7F
I64 = 0x7E
F32 = 0x7D
F64 = 0x7C
WASM_RAW_WORD_BYTES = 4
WASM_VALUE_SLOT_BYTES = 8
UNINITIALIZED_TABLE_FUNCTION = 0xFFFF_FFFF


class FunctionTable:
    """Fixed-width function table shared directly with the native interpreter.

    The 32-bit storage is the single source of truth for both Python and C++.
    `UNINITIALIZED_TABLE_FUNCTION` represents a Python `None` slot in the ABI.
    """

    __slots__ = ("_sealed", "_values")

    def __init__(self, capacity: int, allocator: BumpAllocator | None = None) -> None:
        assert capacity >= 0
        self._values = CtypesU32Buffer(capacity, allocator)
        self._sealed = False
        for index in range(len(self._values)):
            self._values.put(index, UNINITIALIZED_TABLE_FUNCTION)

    @classmethod
    def from_sequence(
        cls, entries: SequenceView[int | None], allocator: BumpAllocator | None = None
    ) -> FunctionTable:
        """Create a fixed table initialized from a bounded sequence."""

        table = cls(len(entries), allocator)
        for index in range(len(entries)):
            table[index] = entries[index]
        return table

    @property
    def native_address(self) -> int:
        """Address of the stable table array borrowed by the native ABI."""

        return self._values.native_address

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int) -> int | None:
        if index < 0:
            index += len(self)
        assert 0 <= index < len(self)
        value = self._values.at(index)
        return None if value == UNINITIALIZED_TABLE_FUNCTION else value

    def __setitem__(self, index: int, value: int | None) -> None:
        assert not self._sealed, "linked function table is immutable"
        if index < 0:
            index += len(self)
        assert 0 <= index < len(self)
        if value is None:
            self._values.put(index, UNINITIALIZED_TABLE_FUNCTION)
        else:
            assert 0 <= value < UNINITIALIZED_TABLE_FUNCTION
            self._values.put(index, value)

    def seal(self) -> None:
        """Freeze table contents after interpreter linking validates call_indirect sites."""

        self._sealed = True

    def __iter__(self) -> Iterator[int | None]:
        for index in range(len(self)):
            yield self[index]


class IntegerSequence(Protocol):
    """Read-only integer indexing shared by parsed views and fixed vectors."""

    def __len__(self) -> int: ...
    def __getitem__(self, index: int, /) -> int: ...
    def __iter__(self) -> Iterator[int]: ...


# Fixed reference-layout charges for loader-owned records. These sizes model
# the 32-bit target records; final C++ sizeof values remain part of the target
# ABI review and are not inferred from CPython object sizes.
LOADER_IMPORT_ENTRY_BYTES = 32
LOADER_FUNCTION_ENTRY_BYTES = 40
LOADER_EXPORT_ENTRY_BYTES = 4 * WASM_RAW_WORD_BYTES
LOADER_GLOBAL_ENTRY_BYTES = 20
LOADER_TABLE_ENTRY_BYTES = 12
LOADER_BASIC_BLOCK_ENTRY_BYTES = ctypes.sizeof(BasicBlock)
LOADER_BLOCK_INDEX_ENTRY_BYTES = 8
LOADER_BLOCK_INDEX_ENTRY_WORDS = LOADER_BLOCK_INDEX_ENTRY_BYTES // WASM_RAW_WORD_BYTES
LOADER_BLOCK_RADIX_SHIFT = 28


def _allocate_loader_vector[T](
    allocator: BumpAllocator, capacity: int, entry_bytes: int, vector_type: type[StaticVector[T]]
) -> StaticVector[T]:
    """Create a bounded vector and reserve its target backing from the loader arena."""

    assert capacity >= 0 and entry_bytes > 0
    storage_size = capacity * entry_bytes
    offset = allocator.allocate(storage_size)
    return vector_type(capacity=capacity, arena_offset=offset, arena_size=storage_size)


def value_slot_width(value_type: int) -> int:
    """Return the raw 32-bit slot width of one WASM value."""

    assert value_type == I32 or value_type == I64 or value_type == F32 or value_type == F64
    return 2 if value_type == I64 or value_type == F64 else 1


class LocalLayout:
    """Load-time packed offsets, sizes, and WASM types for locals, parameters first."""

    __slots__ = (
        "_arena_offset",
        "_arena_size",
        "_offsets",
        "_sizes",
        "_storage",
        "_types",
        "count",
        "total_words",
    )

    def __init__(
        self,
        params: IntegerSequence,
        extra: IntegerSequence = (),
        allocator: BumpAllocator | None = None,
    ) -> None:
        count = len(params) + len(extra)
        self.count = count
        self._storage = bytearray(count * 4)
        self._offsets = (
            (ctypes.c_uint16 * count).from_buffer(self._storage)
            if count
            else (ctypes.c_uint16 * 0)()
        )
        self._sizes = (
            (ctypes.c_uint8 * count).from_buffer(self._storage, count * 2)
            if count
            else (ctypes.c_uint8 * 0)()
        )
        self._types = (
            (ctypes.c_uint8 * count).from_buffer(self._storage, count * 3)
            if count
            else (ctypes.c_uint8 * 0)()
        )
        total_words = 0
        index = 0
        for value_type in params:
            total_words = self._record(index, value_type, total_words)
            index += 1
        for value_type in extra:
            total_words = self._record(index, value_type, total_words)
            index += 1
        self.total_words = total_words
        self._arena_offset: int | None = None
        self._arena_size = len(self._storage)
        if allocator is not None and self._arena_size:
            self._arena_offset = allocator.allocate(self._arena_size, alignment=2)

    @property
    def arena_offset(self) -> int | None:
        return self._arena_offset

    @property
    def arena_size(self) -> int:
        return self._arena_size

    def relocate_arena(self, offset_delta: int) -> None:
        """Move the recorded local-layout span with its owning module."""

        if self._arena_offset is not None:
            relocated_offset = self._arena_offset + offset_delta
            assert relocated_offset >= 0
            self._arena_offset = relocated_offset

    def _record(self, index: int, value_type: int, offset: int) -> int:
        width = value_slot_width(value_type)
        self._offsets[index] = offset
        self._sizes[index] = width
        self._types[index] = value_type
        return offset + width

    def offset(self, index: int) -> int:
        """Return the precomputed raw-word offset of local `index`."""
        assert 0 <= index < self.count
        return int(self._offsets[index])

    @property
    def sizes_address(self) -> int:
        """Address of the contiguous per-local raw-word size table."""
        return ctypes.addressof(self._sizes) if self.count else 0

    @property
    def types_address(self) -> int:
        """Address of the contiguous per-local WASM value-type table."""
        return ctypes.addressof(self._types) if self.count else 0

    @property
    def offsets_address(self) -> int:
        """Address of the contiguous per-local raw-word offset table."""
        return ctypes.addressof(self._offsets) if self.count else 0

    def size_words(self, index: int) -> int:
        """Return the precomputed raw-word size of local `index`."""
        assert 0 <= index < self.count
        return int(self._sizes[index])

    def value_type(self, index: int) -> int:
        """Return the original WASM value type of local `index`."""
        assert 0 <= index < self.count
        return int(self._types[index])

    def __len__(self) -> int:
        return self.count


class FuncTypeRecord(ctypes.Structure):
    """Loader-owned ABI signature view into parameter and result type bytes."""

    __slots__ = ()
    _fields_ = (
        ("param_offset", ctypes.c_uint32),
        ("param_count", ctypes.c_uint32),
        ("result_offset", ctypes.c_uint32),
        ("result_count", ctypes.c_uint32),
    )

    def __init__(
        self,
        param_offset: int,
        param_count: int,
        result_offset: int,
        result_count: int,
    ) -> None:
        self.param_offset = param_offset
        self.param_count = param_count
        self.result_offset = result_offset
        self.result_count = result_count


class FuncTypeStorage:
    """Fixed contiguous function type records owned and shared by the Loader."""

    __slots__ = ("_arena_offset", "_entries", "_native", "capacity")

    def __init__(
        self,
        capacity: int,
        allocator: BumpAllocator | None = None,
        arena_offset: int | None = None,
    ) -> None:
        assert capacity >= 0
        self.capacity = capacity
        self._native = (FuncTypeRecord * capacity)()
        # Keep stable ctypes views for Python callers; _native is the only
        # storage for the record fields shared with the native ABI.
        self._entries: StaticVector[FuncTypeRecord] = StaticVector(capacity=capacity)
        self._arena_offset = arena_offset
        if allocator is not None and capacity:
            assert self._arena_offset is None
            self._arena_offset = allocator.allocate(
                ctypes.sizeof(self._native), ctypes.alignment(FuncTypeRecord)
            )

    @property
    def native_address(self) -> int:
        return ctypes.addressof(self._native) if self._entries else 0

    def append(self, record: FuncTypeRecord) -> None:
        assert len(self._entries) < self.capacity
        index = len(self._entries)
        self._native[index] = record
        self._entries.append(self._native[index])

    def relocate_arena(self, offset_delta: int) -> None:
        if self._arena_offset is not None:
            relocated_offset = self._arena_offset + offset_delta
            assert relocated_offset >= 0
            self._arena_offset = relocated_offset

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index: int) -> FuncTypeRecord:
        return self._entries[index]

    def __iter__(self) -> Iterator[FuncTypeRecord]:
        return iter(self._entries)


@dataclass(slots=True)
class FuncType:
    """Temporary decoded signature borrowing the module ROM bytes."""

    params: memoryview
    results: memoryview
    params_source_offset: int
    results_source_offset: int


@dataclass(slots=True)
class Function:
    type_index: int
    locals_extra: StaticVector[int]  # declared (non-parameter) locals, in order
    code_offset: int
    code_size: int
    code_pc_offset: int
    control_map: ControlMap | None = None
    interpreter_constant_pool: InterpreterConstantPool | None = None
    select_widths: ReadOnlyFlatMapStorage[int, int] | None = None
    drop_widths: ReadOnlyFlatMapStorage[int, int] | None = None
    # Per-local raw-word offsets and sizes are immutable loader metadata.
    local_layout_cache: LocalLayout | None = None
    local_slot_count_cache: int | None = None
    param_packed_slot_count_cache: int | None = None
    param_count_cache: int | None = None
    result_arity_cache: int | None = None


@dataclass(slots=True)
class Export:
    name_offset: int
    name_size: int
    kind: int  # 0=func, 1=table, 2=mem, 3=global
    index: int


@dataclass(slots=True)
class Import:
    module_offset: int
    module_size: int
    name_offset: int
    name_size: int
    type_index: int
    value_type: int | None = None
    mutable: bool = False
    min_limit: int = 0
    max_limit: int | None = None


@dataclass(slots=True)
class Memory:
    min_pages: int
    max_pages: int | None
    imported: bool = False


@dataclass(slots=True)
class Global:
    vtype: int
    mutable: bool
    init_value: int
    imported: bool = False
    init_global_index: int | None = None


@dataclass(slots=True)
class Table:
    min_size: int
    max_size: int | None
    imported: bool = False


@dataclass(slots=True)
class Module:
    types: FuncTypeStorage = field(default_factory=lambda: FuncTypeStorage(capacity=0))
    imports: StaticVector[Import] = field(default_factory=lambda: StaticVector(capacity=0))
    global_import_count: int = 0
    functions: StaticVector[Function] = field(default_factory=lambda: StaticVector(capacity=0))
    exports: StaticVector[Export] = field(default_factory=lambda: StaticVector(capacity=0))
    memory: Memory | None = None
    memory_import: Import | None = None
    globals: StaticVector[Global] = field(default_factory=lambda: StaticVector(capacity=0))
    tables: StaticVector[Table] = field(default_factory=lambda: StaticVector(capacity=0))
    table_import_count: int = 0
    start_function: int | None = None
    element_section_offset: int = 0
    element_section_size: int = 0
    data_section_offset: int = 0
    data_section_size: int = 0
    block_storage: ReadOnlyRadixBinaryTreeStorage[int] | None = None
    blocks: BasicBlockStorage = field(default_factory=lambda: BasicBlockStorage(capacity=0))
    block_index_entries: CtypesU32Buffer | None = field(default=None, repr=False)
    block_radix_table: CtypesU32Buffer | None = field(default=None, repr=False)
    block_radix_shift: int = LOADER_BLOCK_RADIX_SHIFT
    source: memoryview | None = None
    allocator: BumpAllocator | None = field(default=None, repr=False)
    _arena_start: int = field(default=0, repr=False)
    _arena_size: int = field(default=0, repr=False)
    _global_widths: ctypes.Array | None = field(default=None, repr=False)
    _global_widths_arena_offset: int | None = field(default=None, repr=False)

    @property
    def arena_size(self) -> int:
        """Return the bytes reserved while loading this module."""

        return self._arena_size

    def configure_section_capacities(
        self,
        type_count: int,
        function_import_count: int,
        function_count: int,
        export_count: int,
        global_count: int,
        table_count: int,
        allocator: BumpAllocator,
    ) -> None:
        """Set exact section capacities before the loader starts appending."""

        assert self.allocator is None or self.allocator is allocator
        self.allocator = allocator
        self._arena_start = allocator.offset
        assert len(self.types) == 0
        assert len(self.imports) == 0
        assert self.global_import_count == 0
        assert len(self.functions) == 0
        assert len(self.exports) == 0
        assert len(self.globals) == 0
        assert len(self.tables) == 0
        self.types = FuncTypeStorage(type_count, allocator)
        self.imports = _allocate_loader_vector(
            allocator, function_import_count, LOADER_IMPORT_ENTRY_BYTES, StaticVector[Import]
        )
        self.functions = _allocate_loader_vector(
            allocator, function_count, LOADER_FUNCTION_ENTRY_BYTES, StaticVector[Function]
        )
        self.exports = _allocate_loader_vector(
            allocator, export_count, LOADER_EXPORT_ENTRY_BYTES, StaticVector[Export]
        )
        self.globals = _allocate_loader_vector(
            allocator, global_count, LOADER_GLOBAL_ENTRY_BYTES, StaticVector[Global]
        )
        self.tables = _allocate_loader_vector(
            allocator, table_count, LOADER_TABLE_ENTRY_BYTES, StaticVector[Table]
        )

    def relocate_to(self, allocator: BumpAllocator) -> None:
        """Relocate loaded module storage and offsets into the destination arena."""

        if self.allocator is allocator:
            return
        if self._arena_size:
            new_start = allocator.allocate(self._arena_size)
            offset_delta = new_start - self._arena_start
            self.types.relocate_arena(offset_delta)
            self.imports.relocate_arena(offset_delta)
            self.functions.relocate_arena(offset_delta)
            self.exports.relocate_arena(offset_delta)
            self.globals.relocate_arena(offset_delta)
            self.tables.relocate_arena(offset_delta)
            self.blocks.relocate_arena(offset_delta)
            if self.block_index_entries is not None:
                self.block_index_entries.relocate_arena(offset_delta)
            if self.block_radix_table is not None:
                self.block_radix_table.relocate_arena(offset_delta)
            if self._global_widths_arena_offset is not None:
                self._global_widths_arena_offset += offset_delta
            for function in self.functions:
                function.locals_extra.relocate_arena(offset_delta)
                assert function.local_layout_cache is not None
                function.local_layout_cache.relocate_arena(offset_delta)
            self._arena_start = new_start
        else:
            self.build_native_global_widths(allocator)
            if self._global_widths_arena_offset is None and len(self.globals):
                assert self._global_widths is not None
                self._global_widths_arena_offset = allocator.allocate(
                    ctypes.sizeof(self._global_widths), ctypes.alignment(ctypes.c_uint8)
                )
            for function in self.functions:
                assert function.local_layout_cache is not None
                layout = function.local_layout_cache
                layout._arena_offset = (
                    allocator.allocate(layout.arena_size, alignment=2)
                    if layout.arena_size
                    else None
                )
        self.allocator = allocator

    def finish_loading(self, allocator: BumpAllocator) -> None:
        """Record the arena span reserved while parsing this module."""

        assert self.allocator is allocator
        assert self._arena_size == 0
        self._arena_size = allocator.offset - self._arena_start

    def prepare_function_layouts(self) -> None:
        """Precompute each local's packed word span when loading the module."""

        for function in self.functions:
            assert 0 <= function.type_index < len(self.types), (
                "function type index is outside the type section"
            )
            function_type = self.type_at(function.type_index)
            local_count = len(function_type.params) + len(function.locals_extra)
            assert local_count <= FB_CONF_MAX_LOCALS
            layout = LocalLayout(function_type.params, function.locals_extra, self.allocator)
            function.local_layout_cache = layout
            function.local_slot_count_cache = layout.total_words
            function.param_count_cache = len(function_type.params)
            function.param_packed_slot_count_cache = sum(
                value_slot_width(value_type) for value_type in function_type.params
            )
            function.result_arity_cache = sum(
                value_slot_width(value_type) for value_type in function_type.results
            )

    def stream_element_initializers(
        self,
        callback: ElementInitializer,
        global_values: SequenceView[int],
        resolve_globals: bool = True,
    ) -> None:
        """Stream active element entries to a callback without retaining them."""

        if self.element_section_size == 0:
            return

        assert self.source is not None
        data = self.source
        off = self.element_section_offset
        end = off + self.element_section_size
        segment_count, off = decode_unsigned(data, off, end)
        for _ in range(segment_count):
            flags, off = decode_unsigned(data, off, end)
            assert flags == 0 or flags == 2, f"unsupported element segment flags={flags}"
            table_index = 0
            if flags == 2:
                table_index, off = decode_unsigned(data, off, end)
            offset, off = _read_init_offset(
                data, off, end, global_values, self, "element segment", resolve_globals
            )
            if flags == 2:
                elem_kind, off = decode_unsigned(data, off, end)
                assert elem_kind == 0, "only funcref element segments are supported"
            function_count, off = decode_unsigned(data, off, end)
            for index in range(function_count):
                function_index, off = decode_unsigned(data, off, end)
                callback(table_index, offset + index, function_index)
        assert off == end, "element section length mismatch"

    def stream_data_initializers(
        self,
        callback: DataInitializer,
        global_values: SequenceView[int],
        resolve_globals: bool = True,
    ) -> None:
        """Stream active data segments to a callback without retaining them."""

        if self.data_section_size == 0:
            return

        assert self.source is not None
        data = self.source
        off = self.data_section_offset
        end = off + self.data_section_size
        segment_count, off = decode_unsigned(data, off, end)
        for _ in range(segment_count):
            flags, off = decode_unsigned(data, off, end)
            assert flags == 0 or flags == 2, f"unsupported data segment flags={flags}"
            memory_index = 0
            if flags == 2:
                memory_index, off = decode_unsigned(data, off, end)
            assert memory_index == 0, "only memory index 0 is supported"
            offset, off = _read_init_offset(
                data, off, end, global_values, self, "data segment", resolve_globals
            )
            data_size, off = decode_unsigned(data, off, end)
            data_end = off + data_size
            assert data_end <= end, "data segment exceeds section bounds"
            callback(offset, data[off:data_end])
            off = data_end
        assert off == end, "data section length mismatch"

    def init_memory_data(self, memory: bytearray, global_values: SequenceView[int]) -> None:
        """Initializes memory with active data segments."""

        def write_data(offset: int, data: memoryview) -> None:
            assert offset + len(data) <= len(memory)
            memory[offset : offset + len(data)] = data

        self.stream_data_initializers(write_data, global_values)

    def table_contents(
        self,
        table_index: int,
        global_values: SequenceView[int],
        initial: FunctionTable | None = None,
        allocator: BumpAllocator | None = None,
    ) -> FunctionTable:
        """
        Materializes table `table_index` as fixed-width unified function indices
        (or None for an uninitialized slot), applying active element segments
        in section order. Imported tables keep their original shared storage.
        """

        table = self.tables[table_index]
        if initial is None:
            slots = FunctionTable(capacity=table.min_size, allocator=allocator)
        else:
            assert len(initial) >= table.min_size
            slots = initial

        def write_element(segment_table_index: int, slot: int, function_index: int) -> None:
            if segment_table_index == table_index:
                assert 0 <= slot < len(slots), "element segment exceeds table bounds"
                slots[slot] = function_index

        self.stream_element_initializers(write_element, global_values)
        return slots

    def is_import(self, func_index: int) -> bool:
        return func_index < len(self.imports)

    def code_for(self, func_index: int) -> memoryview:
        """Raw bytecode for a locally-defined function, by unified function index."""
        function = self.functions[func_index - len(self.imports)]
        assert self.source is not None
        return self.source[function.code_offset : function.code_offset + function.code_size]

    def function_pc_offset(self, func_index: int) -> int:
        """Return the Code-section PC of a defined function's first instruction byte."""
        assert not self.is_import(func_index)
        function = self.functions[func_index - len(self.imports)]
        return function.code_pc_offset

    def function_index_for_pc(self, pc: int) -> int | None:
        """Resolve a module-local Code-section PC to its defined function index."""
        assert 0 <= pc <= 0xFFFF_FFFF
        imports = len(self.imports)
        low = 0
        high = len(self.functions)
        while low < high:
            middle = low + (high - low) // 2
            function_index = imports + middle
            start = self.function_pc_offset(function_index)
            end = start + len(self.code_for(function_index))
            if pc < start:
                high = middle
            elif pc >= end:
                low = middle + 1
            else:
                return function_index
        return None

    def func_type(self, func_index: int) -> FuncType:
        if self.is_import(func_index):
            type_index = self.imports[func_index].type_index
        else:
            local = self.functions[func_index - len(self.imports)]
            type_index = local.type_index
        return self.type_at(type_index)

    def type_at(self, type_index: int) -> FuncType:
        """Return a function type whose parsed signature bytes borrow Module.source."""
        assert 0 <= type_index < len(self.types)
        record = self.types[type_index]
        assert self.source is not None
        params_end = record.param_offset + record.param_count
        results_end = record.result_offset + record.result_count
        return FuncType(
            params=self.source[record.param_offset : params_end],
            results=self.source[record.result_offset : results_end],
            params_source_offset=record.param_offset,
            results_source_offset=record.result_offset,
        )

    def export_func_index(self, name: str) -> int:
        assert self.source is not None
        name_bytes = name.encode("utf-8")
        for exp in self.exports:
            if (
                exp.kind == 0
                and self.source[exp.name_offset : exp.name_offset + exp.name_size] == name_bytes
            ):
                return exp.index
        assert False, f"no exported function named {name!r}"

    def import_module_name(self, import_index: int) -> str:
        assert self.source is not None
        imp = self.imports[import_index]
        return (
            self.source[imp.module_offset : imp.module_offset + imp.module_size]
            .tobytes()
            .decode("utf-8")
        )

    def import_field_name(self, import_index: int) -> str:
        assert self.source is not None
        imp = self.imports[import_index]
        return (
            self.source[imp.name_offset : imp.name_offset + imp.name_size].tobytes().decode("utf-8")
        )

    def local_types(self, func_index: int) -> IntegerSequence:
        """
        Params followed by declared locals -- WASM addresses both with a
                single local index space starting at 0. Imports have no body, so
                their locals are just their parameters.  The vector is built for the
                validator and dropped afterwards: execution never needs a local's type.
        """

        ft = self.func_type(func_index)
        assert ft.params is not None
        if self.is_import(func_index):
            return ft.params
        local = self.functions[func_index - len(self.imports)]
        types: StaticVector[int] = StaticVector(capacity=len(ft.params) + len(local.locals_extra))
        extended = types.extend(ft.params)
        assert extended
        extended = types.extend(local.locals_extra)
        assert extended
        return types

    def build_native_global_widths(self, allocator: BumpAllocator | None = None) -> None:
        """Build the ABI width table once from loader-owned global declarations."""

        if self._global_widths is None:
            widths_type = ctypes.c_uint8 * len(self.globals)
            self._global_widths = widths_type()
            for index, global_value in enumerate(self.globals):
                self._global_widths[index] = value_slot_width(global_value.vtype)
        if allocator is not None and len(self.globals):
            if self._global_widths_arena_offset is not None:
                return
            self._global_widths_arena_offset = allocator.allocate(
                ctypes.sizeof(self._global_widths), ctypes.alignment(ctypes.c_uint8)
            )

    @property
    def global_widths_address(self) -> int:
        return (
            0
            if self._global_widths is None or len(self.globals) == 0
            else ctypes.addressof(self._global_widths)
        )

    def build_basic_block_index(self, allocator: BumpAllocator | None = None) -> None:
        """Build the immutable block and instruction indexes during loading."""
        from tier2_runtime.interpreter.control_flow import (
            build_interpreter_constant_pool,
            extract_basic_blocks,
        )

        n_imports = len(self.imports)
        block_capacity = self.total_basic_blocks
        all_blocks = BasicBlockStorage(block_capacity, allocator)
        for idx, _fn in enumerate(self.functions):
            func_idx = n_imports + idx
            code = self.code_for(func_idx)
            function_pc_offset = self.function_pc_offset(func_idx)
            function = self.functions[idx]
            assert function.control_map is not None
            extracted = extract_basic_blocks(code, function.control_map, pc_base=function_pc_offset)
            block_heads: StaticVector[int] = StaticVector(capacity=len(extracted))
            for block in extracted:
                block_heads.append(block[0] - function_pc_offset)
            constants, block_constant_offsets = build_interpreter_constant_pool(
                code, block_heads, allocator
            )
            function.interpreter_constant_pool = constants
            assert len(block_constant_offsets) == len(extracted)
            for block_index, (head_pc, next_pc, loops_to, frame_depth, byte_span) in enumerate(
                extracted
            ):
                all_blocks.append(
                    BasicBlock(
                        head_pc=head_pc,
                        func_index=func_idx,
                        next_pc=next_pc,
                        loops_to=loops_to,
                        frame_depth=frame_depth,
                        byte_span=byte_span,
                        interpreter_constant_byte_offset=block_constant_offsets[block_index],
                    )
                )

        self.build_native_global_widths(allocator)

        # `all_blocks` is already in ascending head_pc order here (functions
        # walked in order, extract_basic_blocks yields blocks in bytecode
        # order within each). `self.blocks` keeps that natural, meaningful
        # order for callers that index or iterate it directly -- it must
        # not be re-ordered by an internal cache detail. `block_storage`'s
        # own key/entry arrays are sorted separately, only for its radix
        # lookup's internal use.
        self.blocks = all_blocks
        if not all_blocks:
            self.block_storage = None
            self.block_index_entries = None
            self.block_radix_table = None
            self.block_radix_shift = LOADER_BLOCK_RADIX_SHIFT
            return

        entries: StaticVector[tuple[int, int]] = StaticVector(capacity=len(all_blocks))
        for block_index, block in enumerate(all_blocks):
            entries.append((fold_mix32(block.head_pc), block_index))
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        self.block_storage = ReadOnlyRadixBinaryTreeStorage.from_sorted_static_entries(
            entries,
            radix_shift=LOADER_BLOCK_RADIX_SHIFT,
        )
        self.block_index_entries = CtypesU32Buffer(
            len(entries) * LOADER_BLOCK_INDEX_ENTRY_WORDS,
            allocator,
        )
        for entry_index, (folded_pc, block_index) in enumerate(entries):
            word_index = entry_index * LOADER_BLOCK_INDEX_ENTRY_WORDS
            self.block_index_entries.put(word_index, folded_pc)
            self.block_index_entries.put(word_index + 1, block_index)
        self.block_radix_table = CtypesU32Buffer(
            len(self.block_storage.radix_table),
            allocator,
        )
        for table_index, entry_index in enumerate(self.block_storage.radix_table):
            self.block_radix_table.put(table_index, entry_index)
        self.block_radix_shift = LOADER_BLOCK_RADIX_SHIFT

    def get_block(self, pc: int) -> BasicBlock | None:
        """Looks up a BasicBlock by UnifiedPC via the loader's Radix tree (O(1) + O(log n))."""
        if self.block_storage is None:
            return None
        block_index = self.block_storage.view().find(fold_mix32(pc))
        return None if block_index is None else self.blocks[block_index]

    @property
    def total_basic_blocks(self) -> int:
        """Returns the exact total number of basic blocks across all functions in the module."""
        if self.blocks:
            return len(self.blocks)
        from tier2_runtime.interpreter.control_flow import count_basic_blocks

        n_imports = len(self.imports)
        count = 0
        for idx, _function in enumerate(self.functions):
            function_index = n_imports + idx
            count += count_basic_blocks(self.code_for(function_index))
        return count


def _read_init_offset(
    data: memoryview,
    off: int,
    end: int,
    global_values: SequenceView[int],
    module: Module,
    expression_name: str,
    resolve_globals: bool,
) -> tuple[int, int]:
    opcode = data[off]
    off += 1
    if opcode == op.I32_CONST:
        offset, off = decode_signed(data, off, end, bits=32)
        offset &= 0xFFFF_FFFF
    elif opcode == op.GLOBAL_GET:
        global_index, off = decode_unsigned(data, off, end)
        if not resolve_globals:
            assert global_index < len(module.globals), (
                f"{expression_name} global index out of range"
            )
            global_ref = module.globals[global_index]
            assert global_ref.imported and not global_ref.mutable and global_ref.vtype == I32, (
                f"{expression_name} global.get must reference an imported immutable i32 global"
            )
            offset = 0
        else:
            assert global_index < len(global_values), f"{expression_name} global index out of range"
            offset = global_values[global_index] & 0xFFFF_FFFF
    else:
        assert False, f"{expression_name} offset must use i32.const or global.get"
    assert off < end and data[off] == op.END, (
        f"{expression_name} offset expression must end with 0x0B"
    )
    return offset, off + 1

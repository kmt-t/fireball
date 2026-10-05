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

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import tier2_runtime.wasm.opcodes as op
from bump_allocator import BumpAllocator
from config import FB_CONF_MAX_BASIC_BLOCKS, FB_CONF_MAX_LOCALS
from system_containers import (
    BitView,
    CtypesU32Buffer,
    MutableBitStorage,
    ReadOnlyFlatMapStorage,
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    fold_mix32,
)
from tier2_runtime.wasm.jit_scoring import OPCODE_BENEFIT_TABLE, score_opcodes
from tier2_runtime.wasm.leb128 import decode_signed, decode_unsigned

if TYPE_CHECKING:
    from tier2_runtime.interpreter.control_flow import ControlMap


WasmOperand = int | None
ElementInitializer = Callable[[int, int, int], None]
DataInitializer = Callable[[int, memoryview], None]


@dataclass(slots=True)
class BasicBlock:
    """
    A straight-line run of WASM instructions ending with branch/return, as PC
    range + control-flow metadata only. Decoded instructions are never stored
    in the module; consumers obtain a one-shot iterator over raw bytecode.
    """

    head_pc: int
    func_index: int = 0
    next_pc: int | None = None
    loops_to: int | None = None
    frame_depth: int = 0
    byte_span: int = 0
    jit_score: int = 0


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

    __slots__ = ("_values",)

    def __init__(self, capacity: int, allocator: BumpAllocator | None = None) -> None:
        assert capacity >= 0
        self._values = CtypesU32Buffer(capacity, allocator)
        for index in range(len(self._values)):
            self._values.put(index, UNINITIALIZED_TABLE_FUNCTION)

    @classmethod
    def from_sequence(
        cls, entries: Sequence[int | None], allocator: BumpAllocator | None = None
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

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        self._values.bind_allocator(allocator)

    def __getitem__(self, index: int) -> int | None:
        if index < 0:
            index += len(self)
        assert 0 <= index < len(self)
        value = self._values.at(index)
        return None if value == UNINITIALIZED_TABLE_FUNCTION else value

    def __setitem__(self, index: int, value: int | None) -> None:
        if index < 0:
            index += len(self)
        assert 0 <= index < len(self)
        if value is None:
            self._values.put(index, UNINITIALIZED_TABLE_FUNCTION)
        else:
            assert 0 <= value < UNINITIALIZED_TABLE_FUNCTION
            self._values.put(index, value)

    def __iter__(self) -> Iterator[int | None]:
        for index in range(len(self)):
            yield self[index]


class IntegerSequence(Protocol):
    """Read-only integer indexing shared by parsed views and fixed vectors."""

    def __len__(self) -> int: ...
    def __getitem__(self, index: int, /) -> int: ...
    def __iter__(self) -> Iterator[int]: ...


def _uleb_size(value: int) -> int:
    assert value >= 0
    size = 1
    while value >= 0x80:
        value >>= 7
        size += 1
    return size


# Fixed reference-layout charges for loader-owned records. These sizes model
# the 32-bit target records; final C++ sizeof values remain part of the target
# ABI review and are not inferred from CPython object sizes.
LOADER_TYPE_ENTRY_BYTES = 16
LOADER_IMPORT_ENTRY_BYTES = 32
LOADER_FUNCTION_ENTRY_BYTES = 40
LOADER_EXPORT_ENTRY_BYTES = 4 * WASM_RAW_WORD_BYTES
LOADER_GLOBAL_ENTRY_BYTES = 20
LOADER_TABLE_ENTRY_BYTES = 12
LOADER_BASIC_BLOCK_ENTRY_BYTES = 24
LOADER_BLOCK_INDEX_ENTRY_BYTES = 8


def _allocate_loader_vector[T](
    allocator: BumpAllocator, capacity: int, entry_bytes: int
) -> StaticVector[T]:
    """Create a bounded vector and reserve its target backing from the loader arena."""

    assert capacity >= 0 and entry_bytes > 0
    storage_size = capacity * entry_bytes
    offset = allocator.allocate(storage_size)
    return StaticVector(capacity=capacity, arena_offset=offset, arena_size=storage_size)


def value_slot_width(value_type: int) -> int:
    """Return the raw 32-bit slot width of one WASM value."""

    assert value_type == I32 or value_type == I64 or value_type == F32 or value_type == F64
    return 2 if value_type == I64 or value_type == F64 else 1


LOCAL_WIDTH_BITS = 2


class LocalWidthMap:
    """Raw width of every local of one function, packed at 2 bits per local.

    A code `c` stands for `1 << c` raw 32-bit words: 0 is 4 bytes (i32/f32), 1 is 8 bytes
    (i64/f64), and 2 is 16 bytes (v128).  The widest local decides `slot_words`, the stride of
    every local slot in the frame; widths are never mixed inside a frame, so a local's address
    is `base + index * slot_words`.  No per-local type is kept: opcodes carry the types.
    """

    __slots__ = (
        "_allocator",
        "_arena_offset",
        "_arena_size",
        "_storage",
        "_view",
        "count",
        "slot_words",
    )

    def __init__(self, params: IntegerSequence, extra: IntegerSequence = ()) -> None:
        count = len(params) + len(extra)
        self._storage = MutableBitStorage(count, bits=LOCAL_WIDTH_BITS)
        self.count = count
        slot_words = 1
        index = 0
        for value_type in params:
            slot_words = self._record(index, value_type, slot_words)
            index += 1
        for value_type in extra:
            slot_words = self._record(index, value_type, slot_words)
            index += 1
        self.slot_words = slot_words
        self._view: BitView = self._storage.view()
        self._allocator: BumpAllocator | None = None
        self._arena_offset: int | None = None
        self._arena_size = len(self._storage.buffer)

    @property
    def arena_offset(self) -> int | None:
        return self._arena_offset

    @property
    def arena_size(self) -> int:
        return self._arena_size

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Record packed local widths in the module's runtime arena."""

        if self._allocator is allocator:
            return
        self._arena_offset = (
            allocator.allocate(self._arena_size, alignment=1) if self._arena_size else None
        )
        self._allocator = allocator

    def relocate_arena(self, offset_delta: int) -> None:
        """Move the recorded packed-width span with its owning module."""

        if self._arena_offset is not None:
            relocated_offset = self._arena_offset + offset_delta
            assert relocated_offset >= 0
            self._arena_offset = relocated_offset

    def _record(self, index: int, value_type: int, slot_words: int) -> int:
        width = value_slot_width(value_type)
        self._storage.put(index, width.bit_length() - 1)
        return width if width > slot_words else slot_words

    def words(self, index: int) -> int:
        """Raw words of local `index`: its own width, not the frame's slot stride."""
        assert 0 <= index < self.count
        return 1 << self._view.at(index)

    @property
    def raw_view(self) -> memoryview:
        """Return the packed two-bit local-width map as a zero-copy byte view."""

        return memoryview(self._storage.buffer)

    def __len__(self) -> int:
        return self.count


@dataclass(slots=True)
class FuncType:
    # Parsed modules keep only the raw type record.  Directly-constructed
    # concept modules may still provide materialized vectors. Parsed signatures
    # are views into Module.source and are shared with the native interpreter.
    params: IntegerSequence | None
    results: IntegerSequence | None
    offset: int = 0
    size: int = 0
    params_source_offset: int | None = None
    results_source_offset: int | None = None


@dataclass(slots=True)
class Function:
    type_index: int
    locals_extra: StaticVector[int]  # declared (non-parameter) locals, in order
    code: memoryview | None  # direct-construction fallback; loaded modules use source offsets
    code_offset: int = 0
    code_size: int = 0
    code_pc_offset: int | None = None
    control_map: ControlMap | None = None
    select_widths: ReadOnlyFlatMapStorage[int, int] | None = None
    drop_widths: ReadOnlyFlatMapStorage[int, int] | None = None
    # The width map (2 bits per local, params then locals_extra) and the slot count are
    # immutable load-time metadata; a local's own type is not kept.  The execution path uses the
    # map's frame slot stride (`slot_words`) directly; no per-call offset table is needed.
    local_width_map_cache: LocalWidthMap | None = None
    local_slot_count_cache: int | None = None
    param_packed_slot_count_cache: int | None = None
    param_count_cache: int | None = None
    result_arity_cache: int | None = None

    def __post_init__(self) -> None:
        if self.code is not None:
            self.code = memoryview(self.code)


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
class Element:
    table_index: int
    offset: int
    offset_global_index: int | None = None
    func_indices_offset: int = 0
    func_indices_size: int = 0
    func_count: int = 0
    func_indices: StaticVector[int] | None = None  # direct-construction fallback


@dataclass(slots=True)
class DataSegment:
    memory_index: int
    offset: int
    offset_global_index: int | None = None
    data_offset: int = 0
    data_size: int = 0
    data: memoryview | None = None  # direct-construction fallback

    def __post_init__(self) -> None:
        if self.data is not None:
            self.data = memoryview(self.data)


@dataclass(slots=True)
class Module:
    types: StaticVector[FuncType] = field(default_factory=lambda: StaticVector(capacity=0))
    imports: StaticVector[Import] = field(default_factory=lambda: StaticVector(capacity=0))
    global_import_count: int = 0
    functions: StaticVector[Function] = field(default_factory=lambda: StaticVector(capacity=0))
    exports: StaticVector[Export] = field(default_factory=lambda: StaticVector(capacity=0))
    memory: Memory | None = None
    memory_import: Import | None = None
    globals: StaticVector[Global] = field(default_factory=lambda: StaticVector(capacity=0))
    tables: StaticVector[Table] = field(default_factory=lambda: StaticVector(capacity=0))
    table_import_count: int = 0
    elements: StaticVector[Element] = field(default_factory=lambda: StaticVector(capacity=0))
    data_segments: StaticVector[DataSegment] = field(
        default_factory=lambda: StaticVector(capacity=0)
    )
    start_function: int | None = None
    element_section_offset: int = 0
    element_section_size: int = 0
    data_section_offset: int = 0
    data_section_size: int = 0
    block_storage: ReadOnlyRadixBinaryTreeStorage[BasicBlock] | None = None
    blocks: StaticVector[BasicBlock] = field(default_factory=lambda: StaticVector(capacity=0))
    source: memoryview | None = None
    allocator: BumpAllocator | None = field(default=None, repr=False)
    _arena_start: int = field(default=0, repr=False)
    _arena_size: int = field(default=0, repr=False)

    @property
    def arena_size(self) -> int:
        """Return the bytes reserved while loading this module."""

        return self._arena_size

    def __post_init__(self) -> None:
        # Parsed modules replace every section with its exact two-pass capacity
        # in configure_section_capacities() before appending any records.
        # Direct concept modules may provide already-materialized sequences;
        # their metadata is read directly without a second full copy.
        self.prepare_function_layouts()
        if self.functions and all(function.code_pc_offset is None for function in self.functions):
            payload_offset = _uleb_size(len(self.functions))
            for function in self.functions:
                assert function.code is not None
                local_group_count = 0
                locals_size = _uleb_size(0)
                previous_type: int | None = None
                previous_count = 0
                for value_type in function.locals_extra:
                    if value_type == previous_type:
                        previous_count += 1
                    else:
                        if previous_type is not None:
                            locals_size += _uleb_size(previous_count) + 1
                        local_group_count += 1
                        previous_type = value_type
                        previous_count = 1
                if previous_type is not None:
                    locals_size += _uleb_size(previous_count) + 1
                locals_size += _uleb_size(local_group_count) - 1
                body_size = locals_size + len(function.code)
                body_size_prefix = _uleb_size(body_size)
                function.code_pc_offset = payload_offset + body_size_prefix + locals_size
                payload_offset += body_size_prefix + body_size
        previous_code_end = 0
        for function in self.functions:
            assert function.code_pc_offset is not None
            code_size = function.code_size if function.code is None else len(function.code)
            assert function.code_pc_offset + code_size <= 0x1_0000_0000
            assert previous_code_end <= function.code_pc_offset
            previous_code_end = function.code_pc_offset + code_size

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
        self.types = _allocate_loader_vector(allocator, type_count, LOADER_TYPE_ENTRY_BYTES)
        self.imports = _allocate_loader_vector(
            allocator, function_import_count, LOADER_IMPORT_ENTRY_BYTES
        )
        self.functions = _allocate_loader_vector(
            allocator, function_count, LOADER_FUNCTION_ENTRY_BYTES
        )
        self.exports = _allocate_loader_vector(allocator, export_count, LOADER_EXPORT_ENTRY_BYTES)
        self.globals = _allocate_loader_vector(allocator, global_count, LOADER_GLOBAL_ENTRY_BYTES)
        self.tables = _allocate_loader_vector(allocator, table_count, LOADER_TABLE_ENTRY_BYTES)

    def bind_allocator(self, allocator: BumpAllocator) -> None:
        """Account loaded module storage in the runtime's owning arena."""

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
            self.elements.relocate_arena(offset_delta)
            self.data_segments.relocate_arena(offset_delta)
            self.blocks.relocate_arena(offset_delta)
            for function in self.functions:
                function.locals_extra.relocate_arena(offset_delta)
                assert function.local_width_map_cache is not None
                function.local_width_map_cache.relocate_arena(offset_delta)
            self._arena_start = new_start
        else:
            for function in self.functions:
                assert function.local_width_map_cache is not None
                function.local_width_map_cache.bind_allocator(allocator)
        self.allocator = allocator

    def finish_loading(self, allocator: BumpAllocator) -> None:
        """Record the arena span reserved while parsing this module."""

        assert self.allocator is allocator
        assert self._arena_size == 0
        self._arena_size = allocator.offset - self._arena_start

    def prepare_function_layouts(self) -> None:
        """Precompute each frame's local-slot width and the parameter widths at module load."""

        for function in self.functions:
            assert 0 <= function.type_index < len(self.types), (
                "function type index is outside the type section"
            )
            function_type = self.type_at(function.type_index)
            local_count = len(function_type.params) + len(function.locals_extra)
            assert local_count <= FB_CONF_MAX_LOCALS
            width_map = LocalWidthMap(function_type.params, function.locals_extra)
            if self.allocator is not None:
                width_map.bind_allocator(self.allocator)
            function.local_width_map_cache = width_map
            function.local_slot_count_cache = local_count * width_map.slot_words
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
        global_values: Sequence[int],
        resolve_globals: bool = True,
    ) -> None:
        """Stream active element entries to a callback without retaining them."""

        if self.element_section_size == 0:
            for elem in self.elements:
                offset = elem.offset
                if elem.offset_global_index is not None:
                    assert resolve_globals
                    assert elem.offset_global_index < len(global_values)
                    offset = global_values[elem.offset_global_index] & 0xFFFF_FFFF
                if elem.func_indices is not None:
                    for index, function_index in enumerate(elem.func_indices):
                        callback(elem.table_index, offset + index, function_index)
                else:
                    assert self.source is not None
                    off = elem.func_indices_offset
                    end = off + elem.func_indices_size
                    for index in range(elem.func_count):
                        function_index, off = decode_unsigned(self.source, off, end)
                        callback(elem.table_index, offset + index, function_index)
                    assert off == end
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
        global_values: Sequence[int],
        resolve_globals: bool = True,
    ) -> None:
        """Stream active data segments to a callback without retaining them."""

        if self.data_section_size == 0:
            for seg in self.data_segments:
                data = seg.data
                if data is None:
                    assert self.source is not None
                    data = self.source[seg.data_offset : seg.data_offset + seg.data_size]
                offset = seg.offset
                if seg.offset_global_index is not None:
                    assert resolve_globals
                    assert seg.offset_global_index < len(global_values)
                    offset = global_values[seg.offset_global_index] & 0xFFFF_FFFF
                callback(offset, data)
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

    def init_memory_data(self, memory: bytearray, global_values: Sequence[int]) -> None:
        """Initializes memory with active data segments."""

        def write_data(offset: int, data: memoryview) -> None:
            assert offset + len(data) <= len(memory)
            memory[offset : offset + len(data)] = data

        self.stream_data_initializers(write_data, global_values)

    def table_contents(
        self,
        table_index: int,
        global_values: Sequence[int],
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
            if allocator is not None:
                slots.bind_allocator(allocator)

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
        if function.code_size == 0:
            assert function.code is not None
            return function.code
        assert self.source is not None
        return self.source[function.code_offset : function.code_offset + function.code_size]

    def function_pc_offset(self, func_index: int) -> int:
        """Return the Code-section PC of a defined function's first instruction byte."""
        assert not self.is_import(func_index)
        function = self.functions[func_index - len(self.imports)]
        assert function.code_pc_offset is not None
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
        raw = self.types[type_index]
        if raw.params is not None and raw.results is not None:
            return raw
        assert self.source is not None
        return _read_func_type(self.source, raw.offset, raw.size)

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
        assert types.extend(ft.params)
        assert types.extend(local.locals_extra)
        return types

    def build_basic_block_index(self, allocator: BumpAllocator | None = None) -> None:
        """Build the immutable block and instruction indexes during loading."""
        from tier2_runtime.interpreter.control_flow import extract_basic_blocks, iter_block_ops

        n_imports = len(self.imports)
        block_capacity = self.total_basic_blocks
        assert block_capacity <= FB_CONF_MAX_BASIC_BLOCKS
        if allocator is None:
            all_blocks: StaticVector[BasicBlock] = StaticVector(capacity=block_capacity)
        else:
            all_blocks = _allocate_loader_vector(
                allocator, block_capacity, LOADER_BASIC_BLOCK_ENTRY_BYTES
            )
        for idx, _fn in enumerate(self.functions):
            func_idx = n_imports + idx
            code = self.code_for(func_idx)
            function_pc_offset = self.function_pc_offset(func_idx)
            extracted = extract_basic_blocks(code, pc_base=function_pc_offset)
            for head_pc, next_pc, loops_to, frame_depth, byte_span in extracted:
                if byte_span > 0:
                    all_blocks.append(
                        BasicBlock(
                            head_pc=head_pc,
                            func_index=func_idx,
                            next_pc=next_pc,
                            loops_to=loops_to,
                            frame_depth=frame_depth,
                            byte_span=byte_span,
                            jit_score=score_opcodes(
                                (
                                    opcode
                                    for opcode, _ in iter_block_ops(
                                        code, head_pc - function_pc_offset, byte_span
                                    )
                                ),
                                OPCODE_BENEFIT_TABLE,
                            ),
                        )
                    )

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
            return

        if allocator is None:
            entries: StaticVector[tuple[int, BasicBlock]] = StaticVector(capacity=len(all_blocks))
        else:
            entries = _allocate_loader_vector(
                allocator, len(all_blocks), LOADER_BLOCK_INDEX_ENTRY_BYTES
            )
        for block in all_blocks:
            entries.append((fold_mix32(block.head_pc), block))
        entries.sort(key=lambda entry: (entry[0], entry[1].head_pc))
        radix_shift = 28
        self.block_storage = ReadOnlyRadixBinaryTreeStorage.from_sorted_static_entries(
            entries,
            radix_shift=radix_shift,
        )
        if allocator is not None:
            allocator.allocate(len(self.block_storage.radix_table) * WASM_RAW_WORD_BYTES)

    def get_block(self, pc: int) -> BasicBlock | None:
        """Looks up a BasicBlock by UnifiedPC via the loader's Radix tree (O(1) + O(log n))."""
        if self.block_storage is None:
            return None
        return self.block_storage.view().find(fold_mix32(pc))

    @property
    def total_basic_blocks(self) -> int:
        """Returns the exact total number of basic blocks across all functions in the module."""
        if self.blocks:
            return len(self.blocks)
        from tier2_runtime.interpreter.control_flow import extract_basic_blocks

        n_imports = len(self.imports)
        count = 0
        for idx, _fn in enumerate(self.functions):
            function_index = n_imports + idx
            extracted = extract_basic_blocks(
                self.code_for(function_index), pc_base=self.function_pc_offset(function_index)
            )
            # Matches build_basic_block_index's own filter exactly, so this
            # fallback path (self.blocks not yet built) agrees with the fast
            # `len(self.blocks)` path above once it has been.
            count += sum(1 for entry in extracted if entry[4] > 0)
        return count


def _read_func_type(data: memoryview, offset: int, size: int) -> FuncType:
    end = offset + size
    off = offset
    assert data[off] == 0x60
    off += 1
    nparams, off = decode_unsigned(data, off, end)
    assert nparams <= FB_CONF_MAX_LOCALS, "WASM function parameter count exceeds configured maximum"
    params_offset = off
    for _ in range(nparams):
        assert data[off] == I32 or data[off] == I64 or data[off] == F32 or data[off] == F64
        off += 1
    params = data[params_offset:off]
    nresults, off = decode_unsigned(data, off, end)
    assert nresults <= 1, "MVP functions have at most one result"
    results_offset = off
    for _ in range(nresults):
        assert data[off] == I32 or data[off] == I64 or data[off] == F32 or data[off] == F64
        off += 1
    assert off == end
    results = data[results_offset:off]
    return FuncType(
        params=params,
        results=results,
        offset=offset,
        size=size,
        params_source_offset=params_offset,
        results_source_offset=results_offset,
    )


def _read_init_offset(
    data: memoryview,
    off: int,
    end: int,
    global_values: Sequence[int],
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

"""
experiments/pysim/tier2_runtime/wasm_module.py
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

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import wasm_opcodes as op
from config import (
    FB_CONF_MAX_BASIC_BLOCKS,
    FB_CONF_MAX_LOCALS,
)
from jit_scoring import OpcodeBenefitTable
from leb128 import decode_signed, decode_unsigned
from system_containers import (
    BitView,
    MutableBitStorage,
    ReadOnlyFlatMapStorage,
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    build_radix_table,
    fold_mix32,
)

if TYPE_CHECKING:
    from control_flow import ControlMap


WasmOperand = int | None
ElementInitializer = Callable[[int, int, int], None]
DataInitializer = Callable[[int, memoryview], None]


@dataclass
class BasicBlock:
    """
    A straight-line run of WASM instructions ending with branch/return, as PC
    range + control-flow metadata only. Decoded instructions are never stored
    in the module; consumers obtain a one-shot iterator over raw bytecode.
    """

    head_pc: int
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

    __slots__ = ("_storage", "_view", "count", "slot_words")

    def __init__(self, params: Sequence[int], extra: Sequence[int] = ()) -> None:
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

    def _record(self, index: int, value_type: int, slot_words: int) -> int:
        width = value_slot_width(value_type)
        self._storage.put(index, width.bit_length() - 1)
        return width if width > slot_words else slot_words

    def words(self, index: int) -> int:
        """Raw words of local `index`: its own width, not the frame's slot stride."""
        assert 0 <= index < self.count
        return 1 << self._view.at(index)

    def __len__(self) -> int:
        return self.count


@dataclass
class FuncType:
    # Parsed modules keep only the raw type record.  Directly-constructed
    # concept modules may still provide materialized vectors.
    params: StaticVector[int] | None
    results: StaticVector[int] | None
    offset: int = 0
    size: int = 0


@dataclass
class Function:
    type_index: int
    locals_extra: StaticVector[int]  # declared (non-parameter) locals, in order
    code: memoryview | None  # direct-construction fallback; loaded modules use source offsets
    code_offset: int = 0
    code_size: int = 0
    # Determined by the loader from decoded instructions. CallFrame uses this
    # metadata to select the non-nested-call fast path without rescanning code.
    has_nested_calls: bool = False
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


@dataclass
class Export:
    name_offset: int
    name_size: int
    kind: int  # 0=func, 1=table, 2=mem, 3=global
    index: int


@dataclass
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


@dataclass
class Memory:
    min_pages: int
    max_pages: int | None
    imported: bool = False


@dataclass
class Global:
    vtype: int
    mutable: bool
    init_value: int
    imported: bool = False
    init_global_index: int | None = None


@dataclass
class Table:
    min_size: int
    max_size: int | None
    imported: bool = False


@dataclass
class Element:
    table_index: int
    offset: int
    offset_global_index: int | None = None
    func_indices_offset: int = 0
    func_indices_size: int = 0
    func_count: int = 0
    func_indices: StaticVector[int] | None = None  # direct-construction fallback


@dataclass
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


@dataclass
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
    opcode_benefit_table: OpcodeBenefitTable | None = None
    source: memoryview | None = None

    def __post_init__(self) -> None:
        # Parsed modules replace every section with its exact two-pass capacity
        # in configure_section_capacities() before appending any records.
        # Direct concept modules may provide already-materialized sequences;
        # their metadata is read directly without a second full copy.
        self.prepare_function_layouts()

    def configure_section_capacities(
        self,
        type_count: int,
        import_count: int,
        function_count: int,
        export_count: int,
        global_count: int,
        table_count: int,
    ) -> None:
        """Set exact section capacities before the loader starts appending."""

        assert len(self.types) == 0
        assert len(self.imports) == 0
        assert self.global_import_count == 0
        assert len(self.functions) == 0
        assert len(self.exports) == 0
        assert len(self.globals) == 0
        assert len(self.tables) == 0
        self.types = StaticVector(capacity=type_count)
        self.imports = StaticVector(capacity=import_count)
        self.functions = StaticVector(capacity=function_count)
        self.exports = StaticVector(capacity=export_count)
        self.globals = StaticVector(capacity=global_count)
        self.tables = StaticVector(capacity=table_count + import_count)

    def prepare_function_layouts(self) -> None:
        """Precompute each frame's local-slot width and the parameter widths at module load."""

        for function in self.functions:
            assert 0 <= function.type_index < len(self.types)
            function_type = self.type_at(function.type_index)
            local_count = len(function_type.params) + len(function.locals_extra)
            assert local_count <= FB_CONF_MAX_LOCALS
            width_map = LocalWidthMap(function_type.params, function.locals_extra)
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
                        function_index, off = decode_unsigned(self.source, off)
                        callback(elem.table_index, offset + index, function_index)
                    assert off == end
            return

        assert self.source is not None
        data = self.source
        off = self.element_section_offset
        end = off + self.element_section_size
        segment_count, off = decode_unsigned(data, off)
        for _ in range(segment_count):
            flags, off = decode_unsigned(data, off)
            assert flags == 0 or flags == 2, f"unsupported element segment flags={flags}"
            table_index = 0
            if flags == 2:
                table_index, off = decode_unsigned(data, off)
            offset, off = _read_init_offset(
                data, off, end, global_values, self, "element segment", resolve_globals
            )
            if flags == 2:
                elem_kind, off = decode_unsigned(data, off)
                assert elem_kind == 0, "only funcref element segments are supported"
            function_count, off = decode_unsigned(data, off)
            for index in range(function_count):
                function_index, off = decode_unsigned(data, off)
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
        segment_count, off = decode_unsigned(data, off)
        for _ in range(segment_count):
            flags, off = decode_unsigned(data, off)
            assert flags == 0 or flags == 2, f"unsupported data segment flags={flags}"
            memory_index = 0
            if flags == 2:
                memory_index, off = decode_unsigned(data, off)
            assert memory_index == 0, "only memory index 0 is supported"
            offset, off = _read_init_offset(
                data, off, end, global_values, self, "data segment", resolve_globals
            )
            data_size, off = decode_unsigned(data, off)
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

        if self.data_section_size != 0:
            self.stream_data_initializers(write_data, global_values)
            return

        for seg in self.data_segments:
            if seg.data is None:
                assert self.source is not None
                data = self.source[seg.data_offset : seg.data_offset + seg.data_size]
            else:
                data = seg.data
            offset = seg.offset
            if seg.offset_global_index is not None:
                assert seg.offset_global_index < len(global_values)
                offset = global_values[seg.offset_global_index] & 0xFFFF_FFFF
            assert offset + len(data) <= len(memory)
            memory[offset : offset + len(data)] = data

    def table_contents(
        self,
        table_index: int,
        global_values: Sequence[int],
        initial: StaticVector[int | None] | None = None,
    ) -> StaticVector[int | None]:
        """
        Materializes table `table_index` as a flat list of unified
                function indices (or None for an uninitialized slot), applying
                every active element segment targeting it in section order.
        """

        table = self.tables[table_index]
        if initial is None:
            slots = StaticVector[int | None](capacity=table.min_size)
            for _ in range(table.min_size):
                slots.append(None)
        else:
            assert len(initial) >= table.min_size
            slots = initial

        if self.element_section_size != 0:

            def write_element(segment_table_index: int, slot: int, function_index: int) -> None:
                if segment_table_index == table_index:
                    assert 0 <= slot < len(slots), "element segment exceeds table bounds"
                    slots[slot] = function_index

            self.stream_element_initializers(write_element, global_values)
            return slots

        for elem in self.elements:
            if elem.table_index != table_index:
                continue
            if elem.func_indices is not None:
                func_indices: Iterable[int] = elem.func_indices
            else:
                assert self.source is not None
                func_indices = _read_u32_vector(
                    self.source, elem.func_indices_offset, elem.func_count
                )
            for i, func_index in enumerate(func_indices):
                offset = elem.offset
                if elem.offset_global_index is not None:
                    assert elem.offset_global_index < len(global_values)
                    offset = global_values[elem.offset_global_index] & 0xFFFF_FFFF
                slots[offset + i] = func_index
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

    def func_type(self, func_index: int) -> FuncType:
        if self.is_import(func_index):
            type_index = self.imports[func_index].type_index
        else:
            local = self.functions[func_index - len(self.imports)]
            type_index = local.type_index
        return self.type_at(type_index)

    def type_at(self, type_index: int) -> FuncType:
        """Read one function type record without retaining its vectors."""
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

    def local_types(self, func_index: int) -> StaticVector[int]:
        """
        Params followed by declared locals -- WASM addresses both with a
                single local index space starting at 0. Imports have no body, so
                their locals are just their parameters.  The vector is built for the
                validator and dropped afterwards: execution never needs a local's type.
        """

        ft = self.func_type(func_index)
        if self.is_import(func_index):
            return ft.params
        local = self.functions[func_index - len(self.imports)]
        types: StaticVector[int] = StaticVector(capacity=len(ft.params) + len(local.locals_extra))
        assert types.extend(ft.params)
        assert types.extend(local.locals_extra)
        return types

    def build_basic_block_index(self) -> None:
        """Build the immutable block and instruction indexes during loading."""
        from control_flow import extract_basic_blocks, iter_block_ops
        from jit_scoring import score_opcodes

        self.opcode_benefit_table = OpcodeBenefitTable()

        n_imports = len(self.imports)
        block_capacity = max(1, self.total_basic_blocks)
        assert block_capacity <= FB_CONF_MAX_BASIC_BLOCKS
        all_blocks: StaticVector[BasicBlock] = StaticVector(capacity=block_capacity)
        for idx, _fn in enumerate(self.functions):
            func_idx = n_imports + idx
            code = self.code_for(func_idx)
            extracted = extract_basic_blocks(code, func_index=func_idx)
            for head_pc, next_pc, loops_to, frame_depth, byte_span in extracted:
                if byte_span > 0:
                    all_blocks.append(
                        BasicBlock(
                            head_pc=head_pc,
                            next_pc=next_pc,
                            loops_to=loops_to,
                            frame_depth=frame_depth,
                            byte_span=byte_span,
                            jit_score=score_opcodes(
                                (
                                    opcode
                                    for opcode, _ in iter_block_ops(
                                        code, head_pc & 0xFFFF, byte_span
                                    )
                                ),
                                self.opcode_benefit_table,
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

        radix_sorted_blocks: StaticVector[BasicBlock] = StaticVector.of(
            sorted(all_blocks, key=lambda block: fold_mix32(block.head_pc)),
            capacity=len(all_blocks),
        )
        inv_keys: StaticVector[int] = StaticVector(capacity=len(radix_sorted_blocks))
        entries: StaticVector[tuple[int, BasicBlock]] = StaticVector(
            capacity=len(radix_sorted_blocks)
        )
        for block in radix_sorted_blocks:
            inverse_key = fold_mix32(block.head_pc)
            inv_keys.append(inverse_key)
            entries.append((inverse_key, block))
        radix_shift = 28
        radix_table = build_radix_table(inv_keys, radix_shift=radix_shift)
        self.block_storage = ReadOnlyRadixBinaryTreeStorage[BasicBlock](
            keys=inv_keys,
            values=radix_sorted_blocks,
            radix_table=radix_table,
            radix_shift=radix_shift,
            entries=entries,
        )

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
        from control_flow import extract_basic_blocks

        n_imports = len(self.imports)
        count = 0
        for idx, _fn in enumerate(self.functions):
            extracted = extract_basic_blocks(
                self.code_for(n_imports + idx), func_index=n_imports + idx
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
    nparams, off = decode_unsigned(data, off)
    params = StaticVector[int](capacity=nparams)
    for _ in range(nparams):
        params.append(data[off])
        off += 1
    nresults, off = decode_unsigned(data, off)
    results = StaticVector[int](capacity=nresults)
    for _ in range(nresults):
        results.append(data[off])
        off += 1
    assert off == end
    return FuncType(params=params, results=results, offset=offset, size=size)


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
        offset, off = decode_signed(data, off)
        offset &= 0xFFFF_FFFF
    elif opcode == op.GLOBAL_GET:
        global_index, off = decode_unsigned(data, off)
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


def _read_u32_vector(data: memoryview, offset: int, count: int) -> StaticVector[int]:
    values = StaticVector[int](capacity=count)
    off = offset
    for _ in range(count):
        value, off = decode_unsigned(data, off)
        values.append(value)
    return values

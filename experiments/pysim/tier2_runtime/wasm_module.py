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

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config import (
    FB_CONF_MAX_BASIC_BLOCKS,
    FB_CONF_MAX_DATA_SEGMENTS,
    FB_CONF_MAX_ELEMENTS,
    FB_CONF_MAX_EXPORTS,
    FB_CONF_MAX_FUNCTIONS,
    FB_CONF_MAX_GLOBALS,
    FB_CONF_MAX_IMPORTS,
    FB_CONF_MAX_LOCALS,
    FB_CONF_MAX_TABLES,
    FB_CONF_MAX_TYPES,
)
from jit_scoring import OpcodeBenefitTable
from leb128 import decode_unsigned
from system_containers import (
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
    bswap32,
    build_radix_table,
)

if TYPE_CHECKING:
    from control_flow import ControlMap


WasmOperand = int | None


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
WASM_LOCAL_ALIGNMENT_BYTES = WASM_VALUE_SLOT_BYTES
WASM_LOCAL_SLOT_BYTES = WASM_LOCAL_ALIGNMENT_BYTES
WASM_LOCAL_SLOT_WORDS = WASM_LOCAL_SLOT_BYTES // WASM_RAW_WORD_BYTES
assert WASM_LOCAL_SLOT_BYTES % WASM_RAW_WORD_BYTES == 0


def value_slot_width(value_type: int) -> int:
    """Return the raw 32-bit slot width of one WASM value."""

    assert value_type == I32 or value_type == I64 or value_type == F32 or value_type == F64
    return 2 if value_type == I64 or value_type == F64 else 1


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
    # Params + locals_extra and their raw widths are immutable load-time
    # metadata. The execution path uses the fixed local-slot stride directly;
    # no per-call physical-offset table is needed.
    locals_layout_cache: StaticVector[int] | None = None
    local_widths_cache: StaticVector[int] | None = None
    local_slot_count_cache: int | None = None
    local_i32_only_cache: bool | None = None
    param_packed_slot_count_cache: int | None = None

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
    type_index: int  # only function imports (kind=0) are supported


@dataclass
class Memory:
    min_pages: int
    max_pages: int | None


@dataclass
class Global:
    vtype: int
    mutable: bool
    init_value: int  # this experiment only supports a plain i32.const init expr


@dataclass
class Table:
    min_size: int
    max_size: int | None


@dataclass
class Element:
    table_index: int
    offset: int  # this experiment only supports a plain i32.const offset expr
    func_indices_offset: int = 0
    func_indices_size: int = 0
    func_count: int = 0
    func_indices: StaticVector[int] | None = None  # direct-construction fallback


@dataclass
class DataSegment:
    memory_index: int
    offset: int  # i32.const offset expr
    data_offset: int = 0
    data_size: int = 0
    data: memoryview | None = None  # direct-construction fallback

    def __post_init__(self) -> None:
        if self.data is not None:
            self.data = memoryview(self.data)


@dataclass
class Module:
    types: StaticVector[FuncType] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_TYPES)
    )
    imports: StaticVector[Import] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_IMPORTS)
    )
    functions: StaticVector[Function] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_FUNCTIONS)
    )
    exports: StaticVector[Export] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_EXPORTS)
    )
    memory: Memory | None = None
    globals: StaticVector[Global] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_GLOBALS)
    )
    tables: StaticVector[Table] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_TABLES)
    )
    elements: StaticVector[Element] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_ELEMENTS)
    )
    data_segments: StaticVector[DataSegment] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_DATA_SEGMENTS)
    )
    start_function: int | None = None
    block_storage: ReadOnlyRadixBinaryTreeStorage[BasicBlock] | None = None
    blocks: StaticVector[BasicBlock] = field(
        default_factory=lambda: StaticVector(capacity=FB_CONF_MAX_BASIC_BLOCKS)
    )
    opcode_benefit_table: OpcodeBenefitTable | None = None
    source: memoryview | None = None

    def __post_init__(self) -> None:
        # Directly constructed concept modules are already complete at
        # construction time. Parsed modules call this same operation after all
        # sections have been decoded.
        self.types = StaticVector.of(tuple(self.types), capacity=FB_CONF_MAX_TYPES)
        self.imports = StaticVector.of(tuple(self.imports), capacity=FB_CONF_MAX_IMPORTS)
        self.functions = StaticVector.of(tuple(self.functions), capacity=FB_CONF_MAX_FUNCTIONS)
        self.exports = StaticVector.of(tuple(self.exports), capacity=FB_CONF_MAX_EXPORTS)
        self.globals = StaticVector.of(tuple(self.globals), capacity=FB_CONF_MAX_GLOBALS)
        self.tables = StaticVector.of(tuple(self.tables), capacity=FB_CONF_MAX_TABLES)
        self.elements = StaticVector.of(tuple(self.elements), capacity=FB_CONF_MAX_ELEMENTS)
        self.data_segments = StaticVector.of(
            tuple(self.data_segments), capacity=FB_CONF_MAX_DATA_SEGMENTS
        )
        self.blocks = StaticVector.of(tuple(self.blocks), capacity=FB_CONF_MAX_BASIC_BLOCKS)
        self.prepare_function_layouts()

    def configure_section_capacities(
        self,
        type_count: int,
        import_count: int,
        function_count: int,
        export_count: int,
        global_count: int,
        table_count: int,
        element_count: int,
        data_segment_count: int,
    ) -> None:
        """Set exact section capacities before the loader starts appending."""

        assert len(self.types) == 0
        assert len(self.imports) == 0
        assert len(self.functions) == 0
        assert len(self.exports) == 0
        assert len(self.globals) == 0
        assert len(self.tables) == 0
        assert len(self.elements) == 0
        assert len(self.data_segments) == 0
        self.types = StaticVector(capacity=type_count)
        self.imports = StaticVector(capacity=import_count)
        self.functions = StaticVector(capacity=function_count)
        self.exports = StaticVector(capacity=export_count)
        self.globals = StaticVector(capacity=global_count)
        self.tables = StaticVector(capacity=table_count)
        self.elements = StaticVector(capacity=element_count)
        self.data_segments = StaticVector(capacity=data_segment_count)

    def prepare_function_layouts(self) -> None:
        """Precompute fixed-width local slots and parameter widths at module load."""

        for function in self.functions:
            assert 0 <= function.type_index < len(self.types)
            function_type = self.type_at(function.type_index)
            local_count = len(function_type.params) + len(function.locals_extra)
            assert local_count <= FB_CONF_MAX_LOCALS
            local_layout_storage: StaticVector[int] = StaticVector(capacity=local_count)
            assert local_layout_storage.extend(function_type.params)
            assert local_layout_storage.extend(function.locals_extra)
            local_widths: StaticVector[int] = StaticVector(capacity=local_count)
            for value_type in local_layout_storage:
                local_widths.append(value_slot_width(value_type))
            local_slot_count = len(local_layout_storage) * WASM_LOCAL_SLOT_WORDS
            function.locals_layout_cache = local_layout_storage
            function.local_widths_cache = local_widths
            function.local_slot_count_cache = local_slot_count
            function.local_i32_only_cache = all(width == 1 for width in local_widths)
            function.param_packed_slot_count_cache = sum(
                value_slot_width(value_type) for value_type in function_type.params
            )

    def init_memory_data(self, memory: bytearray) -> None:
        """Initializes memory with active data segments."""
        for seg in self.data_segments:
            if seg.data is None:
                assert self.source is not None
                data = self.source[seg.data_offset : seg.data_offset + seg.data_size]
            else:
                data = seg.data
            assert seg.offset + len(data) <= len(memory)
            memory[seg.offset : seg.offset + len(data)] = data

    def table_contents(self, table_index: int) -> StaticVector[int | None]:
        """
        Materializes table `table_index` as a flat list of unified
                function indices (or None for an uninitialized slot), applying
                every active element segment targeting it in section order.
        """

        table = self.tables[table_index]
        slots: StaticVector[int | None] = StaticVector.of(
            tuple(None for _ in range(table.min_size)), capacity=table.min_size
        )
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
                slots[elem.offset + i] = func_index
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
                and self.source[exp.name_offset : exp.name_offset + exp.name_size]
                == name_bytes
            ):
                return exp.index
        assert False, f"no exported function named {name!r}"

    def import_module_name(self, import_index: int) -> str:
        assert self.source is not None
        imp = self.imports[import_index]
        return self.source[imp.module_offset : imp.module_offset + imp.module_size].tobytes().decode(
            "utf-8"
        )

    def import_field_name(self, import_index: int) -> str:
        assert self.source is not None
        imp = self.imports[import_index]
        return self.source[imp.name_offset : imp.name_offset + imp.name_size].tobytes().decode(
            "utf-8"
        )

    def locals_layout(self, func_index: int) -> StaticVector[int]:
        """
        Params followed by declared locals -- WASM addresses both with a
                single local index space starting at 0. Imports have no body, so
                their "layout" is just their parameters.
        """

        ft = self.func_type(func_index)
        if self.is_import(func_index):
            return ft.params
        local = self.functions[func_index - len(self.imports)]
        assert local.locals_layout_cache is not None
        return local.locals_layout_cache

    def build_basic_block_index(self) -> None:
        """Build the immutable block and instruction indexes during loading."""
        from control_flow import extract_basic_blocks, iter_block_ops
        from jit_scoring import score_opcodes

        self.opcode_benefit_table = OpcodeBenefitTable()

        n_imports = len(self.imports)
        block_capacity = max(
            1,
            sum(
                len(self.code_for(n_imports + index))
                for index in range(len(self.functions))
            ),
        )
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
                                (opcode for opcode, _ in iter_block_ops(
                                    code, head_pc & 0xFFFF, byte_span
                                )),
                                self.opcode_benefit_table,
                            ),
                        )
                    )

        all_blocks.sort(key=lambda block: bswap32(block.head_pc))
        self.blocks = all_blocks
        if not all_blocks:
            self.block_storage = None
            return

        sorted_blocks = all_blocks
        inv_keys = tuple(bswap32(block.head_pc) for block in sorted_blocks)
        radix_shift = 28
        radix_table = build_radix_table(inv_keys, radix_shift=radix_shift)
        self.block_storage = ReadOnlyRadixBinaryTreeStorage[BasicBlock](
            keys=inv_keys,
            values=sorted_blocks,
            radix_table=radix_table,
            radix_shift=radix_shift,
            entries=tuple(zip(inv_keys, sorted_blocks, strict=False)),
        )

    def get_block(self, pc: int) -> BasicBlock | None:
        """Looks up a BasicBlock by UnifiedPC via the loader's Radix tree (O(1) + O(log n))."""
        if self.block_storage is None:
            return None
        return self.block_storage.view().find(bswap32(pc))

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


def _read_u32_vector(data: memoryview, offset: int, count: int) -> StaticVector[int]:
    values = StaticVector[int](capacity=count)
    off = offset
    for _ in range(count):
        value, off = decode_unsigned(data, off)
        values.append(value)
    return values

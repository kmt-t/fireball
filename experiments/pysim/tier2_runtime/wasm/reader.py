"""
experiments/pysim/tier2_runtime/wasm/reader.py
Binary .wasm parser. Supports Type(1), Import(2), Function(3), Table(4),
Memory(5), Global(6), Export(7), Element(9), Code(10). Data(11) and custom
sections are validated and decoded without copying their payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import tier2_runtime.wasm.opcodes as op
from bump_allocator import BumpAllocator
from config import (
    FB_CONF_MAX_DATA_SEGMENTS,
    FB_CONF_MAX_ELEMENTS,
    FB_CONF_MAX_FUNCTIONS,
    FB_CONF_MAX_GLOBALS,
    FB_CONF_MAX_IMPORTS,
    FB_CONF_MAX_MEMORIES,
    FB_CONF_MAX_NESTING_DEPTH,
    FB_CONF_MAX_TABLES,
    FB_CONF_MAX_TYPES,
    FB_CONF_MAX_VALUE_STACK,
    FB_CONF_MAX_WASM_PAGES,
)
from system_containers import ReadOnlyFlatMapStorage, SequenceView, StaticVector, freeze_sequence
from tier2_runtime.abi.integer_types import (
    code_offset_t,
    function_index_t,
    operand_words_t,
    type_index_t,
    wasm_pc_t,
)
from tier2_runtime.wasm.binary import WasmSectionReader
from tier2_runtime.wasm.builder import WasmModuleBuilder
from tier2_runtime.wasm.leb128 import decode_signed, decode_unsigned
from tier2_runtime.wasm.module import (
    F32,
    F64,
    FB_CONF_MAX_LOCALS,
    I32,
    I64,
    WASM_RAW_WORD_BYTES,
    Export,
    Function,
    FuncTypeRecord,
    Global,
    Import,
    Module,
)
from tier2_runtime.wasm.validation import reject_wasm, require_wasm

SEC_TYPE = 1
SEC_IMPORT = 2
SEC_FUNCTION = 3
SEC_TABLE = 4
SEC_MEMORY = 5
SEC_GLOBAL = 6
SEC_EXPORT = 7
SEC_START = 8
SEC_ELEMENT = 9
SEC_CODE = 10
SEC_DATA = 11
ELEM_TYPE_FUNCREF = 0x70


def _read_value_type(data: memoryview, off: int, end: int | None = None) -> int:
    limit = len(data) if end is None else end
    require_wasm(0 <= off < limit <= len(data), "value type exceeds section bounds")
    value_type = data[off]
    require_wasm(
        value_type == I32 or value_type == I64 or value_type == F32 or value_type == F64,
        "invalid WASM binary: value_type == I32 or value_type == I64 or value_type == F32 or value_type == F64",
    )
    return value_type


def _validate_utf8_name(
    data: memoryview, offset: int, size: int, end: int, field_name: str
) -> None:
    name_end = offset + size
    require_wasm(name_end <= end, f"{field_name} exceeds section bounds")
    try:
        data[offset:name_end].tobytes().decode("utf-8")
    except UnicodeDecodeError:
        reject_wasm(f"{field_name} is not valid UTF-8")


class _SectionCounts:
    __slots__ = (
        "basic_blocks",
        "data_segments",
        "elements",
        "exports",
        "functions",
        "globals",
        "imported_functions",
        "imported_globals",
        "imported_memories",
        "imported_tables",
        "imports",
        "max_control_depth",
        "max_local_count",
        "max_parameter_count",
        "memories",
        "tables",
        "types",
    )

    def __init__(self) -> None:
        self.types = 0
        self.imports = 0
        self.imported_functions = 0
        self.imported_tables = 0
        self.imported_memories = 0
        self.imported_globals = 0
        self.functions = 0
        self.tables = 0
        self.memories = 0
        self.globals = 0
        self.max_control_depth = 0
        self.max_local_count = 0
        self.max_parameter_count = 0
        self.exports = 0
        self.elements = 0
        self.data_segments = 0
        self.basic_blocks = 0


def _skip_limits_descriptor(data: memoryview, offset: int, end: int) -> int:
    require_wasm(offset < end, "truncated limits descriptor")
    flags = data[offset]
    offset += 1
    require_wasm(flags == 0 or flags == 1, f"invalid limits flag=0x{flags:02X}")
    _, offset = decode_unsigned(data, offset, end)
    if flags == 1:
        _, offset = decode_unsigned(data, offset, end)
    return offset


def _count_import_kinds(data: memoryview, offset: int, end: int, counts: _SectionCounts) -> None:
    import_count, offset = decode_unsigned(data, offset, end)
    counts.imports = import_count
    for _ in range(import_count):
        module_size, offset = decode_unsigned(data, offset, end)
        require_wasm(offset + module_size <= end, "import module name exceeds section bounds")
        offset += module_size
        field_size, offset = decode_unsigned(data, offset, end)
        require_wasm(offset + field_size <= end, "import field name exceeds section bounds")
        offset += field_size
        require_wasm(offset < end, "truncated import kind")
        import_kind = data[offset]
        offset += 1
        if import_kind == 0:
            counts.imported_functions += 1
            _, offset = decode_unsigned(data, offset, end)
        elif import_kind == 1:
            counts.imported_tables += 1
            require_wasm(offset < end, "truncated table import element type")
            offset += 1
            offset = _skip_limits_descriptor(data, offset, end)
        elif import_kind == 2:
            counts.imported_memories += 1
            offset = _skip_limits_descriptor(data, offset, end)
        elif import_kind == 3:
            counts.imported_globals += 1
            require_wasm(offset + 2 <= end, "truncated global import descriptor")
            offset += 2
        else:
            reject_wasm(f"unsupported import kind={import_kind}")
    require_wasm(offset == end, "import section length mismatch")


def _count_type_parameters(data: memoryview, offset: int, end: int, counts: _SectionCounts) -> None:
    type_count, cursor = decode_unsigned(data, offset, end)
    counts.types = type_count
    if type_count > FB_CONF_MAX_TYPES:
        return
    for _ in range(type_count):
        require_wasm(cursor < end, "truncated function type")
        tag = data[cursor]
        cursor += 1
        require_wasm(tag == 0x60, f"expected functype tag 0x60, got 0x{tag:02X}")
        parameter_count, cursor = decode_unsigned(data, cursor, end)
        counts.max_parameter_count = max(counts.max_parameter_count, parameter_count)
        require_wasm(cursor + parameter_count <= end, "truncated function parameter types")
        cursor += parameter_count
        result_count, cursor = decode_unsigned(data, cursor, end)
        require_wasm(cursor + result_count <= end, "truncated function result types")
        cursor += result_count
    require_wasm(cursor == end, "type section length mismatch")


def _read_section_counts(data: memoryview) -> _SectionCounts:
    counts = _SectionCounts()
    sections = WasmSectionReader(data)
    section = sections.next_section()
    while section is not None:
        section_id = section.section_id
        off = section.payload_offset
        section_end = off + section.payload_size
        if section_id == SEC_TYPE:
            _count_type_parameters(data, off, section_end, counts)
        elif section_id == SEC_IMPORT:
            _count_import_kinds(data, off, section_end, counts)
        elif section_id == SEC_FUNCTION:
            count, _ = decode_unsigned(data, off, section_end)
            counts.functions = count
        elif section_id == SEC_TABLE:
            count, _ = decode_unsigned(data, off, section_end)
            counts.tables = count
        elif section_id == SEC_MEMORY:
            count, _ = decode_unsigned(data, off, section_end)
            counts.memories = count
        elif section_id == SEC_GLOBAL:
            count, _ = decode_unsigned(data, off, section_end)
            counts.globals = count
        elif section_id == SEC_EXPORT:
            count, _ = decode_unsigned(data, off, section_end)
            counts.exports = count
        elif section_id == SEC_ELEMENT:
            count, _ = decode_unsigned(data, off, section_end)
            counts.elements = count
        elif section_id == SEC_DATA:
            count, _ = decode_unsigned(data, off, section_end)
            counts.data_segments = count
        elif section_id == SEC_CODE:
            from tier2_runtime.interpreter.control_flow import (
                count_basic_blocks_and_max_control_depth,
            )

            body_count, body_cursor = decode_unsigned(data, off, section_end)
            for _ in range(body_count):
                body_size, body_cursor = decode_unsigned(data, body_cursor, section_end)
                body_end = body_cursor + body_size
                require_wasm(body_end <= section_end, "function body exceeds code section bounds")
                local_groups, code_cursor = decode_unsigned(data, body_cursor, body_end)
                local_count = 0
                for _ in range(local_groups):
                    count, code_cursor = decode_unsigned(data, code_cursor, body_end)
                    require_wasm(code_cursor < body_end, "truncated local type")
                    local_count += count
                    code_cursor += 1
                counts.max_local_count = max(counts.max_local_count, local_count)
                body_blocks, body_control_depth = count_basic_blocks_and_max_control_depth(
                    memoryview(data[code_cursor:body_end])
                )
                counts.basic_blocks += body_blocks
                counts.max_control_depth = max(counts.max_control_depth, body_control_depth)
                body_cursor = body_end
            require_wasm(body_cursor == section_end, "code section length mismatch")
        section = sections.next_section()
    return counts


def _validate_section_counts(counts: _SectionCounts) -> None:
    limits = (
        ("types", counts.types, FB_CONF_MAX_TYPES),
        ("imports", counts.imports, FB_CONF_MAX_IMPORTS),
        ("functions", counts.functions, FB_CONF_MAX_FUNCTIONS),
        ("function parameter count", counts.max_parameter_count, FB_CONF_MAX_VALUE_STACK),
        ("local count per function", counts.max_local_count, FB_CONF_MAX_LOCALS),
        ("tables", counts.tables + counts.imported_tables, FB_CONF_MAX_TABLES),
        ("memories", counts.memories + counts.imported_memories, FB_CONF_MAX_MEMORIES),
        ("globals", counts.globals + counts.imported_globals, FB_CONF_MAX_GLOBALS),
        ("elements", counts.elements, FB_CONF_MAX_ELEMENTS),
        ("data segments", counts.data_segments, FB_CONF_MAX_DATA_SEGMENTS),
    )
    for label, count, capacity in limits:
        require_wasm(
            count <= capacity,
            "ERR_WASM_UNSUPPORTED_FEATURE: "
            f"WASM {label} count {count} exceeds configured maximum {capacity}",
        )
    require_wasm(
        counts.imported_functions + counts.functions <= FB_CONF_MAX_IMPORTS + FB_CONF_MAX_FUNCTIONS,
        "ERR_WASM_UNSUPPORTED_FEATURE: WASM function index space exceeds u16 capacity",
    )
    require_wasm(
        counts.basic_blocks <= 0xFFFF,
        "ERR_WASM_UNSUPPORTED_FEATURE: basic block count exceeds u16 capacity",
    )
    require_wasm(
        counts.max_control_depth <= FB_CONF_MAX_NESTING_DEPTH,
        "ERR_WASM_UNSUPPORTED_FEATURE: block/loop/if nesting exceeds "
        f"FB_CONF_MAX_NESTING_DEPTH={FB_CONF_MAX_NESTING_DEPTH}",
    )
    require_wasm(
        counts.memories + counts.imported_memories <= 1, "only single linear memory is supported"
    )


def _parse_functype(data: memoryview, off: int, end: int) -> tuple[FuncTypeRecord, int]:
    require_wasm(off < end, "truncated function type")
    tag = data[off]
    off += 1
    if tag != 0x60:
        reject_wasm(f"expected functype tag 0x60, got 0x{tag:02X}")
    nparams, off = decode_unsigned(data, off, end)
    assert nparams <= FB_CONF_MAX_VALUE_STACK, "section-count prevalidation must cap parameters"
    params_offset = off
    for _ in range(nparams):
        _read_value_type(data, off, end)
        off += 1

    nresults, off = decode_unsigned(data, off, end)
    require_wasm(nresults <= 1, "MVP functions have at most one result")
    results_offset = off
    for _ in range(nresults):
        _read_value_type(data, off, end)
        off += 1
    require_wasm(off <= len(data), "invalid WASM binary: off <= len(data)")
    return FuncTypeRecord(params_offset, nparams, results_offset, nresults), off


def _parse_type_section(
    data: memoryview,
    off: int,
    end: int,
    callbacks: WasmModuleBuilder,
) -> None:
    n, off = decode_unsigned(data, off, end)
    for _ in range(n):
        ft, off = _parse_functype(data, off, end)
        callbacks.on_type(ft)

    require_wasm(off == end, "type section length mismatch")


def _parse_import_section(
    data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder
) -> None:
    n, off = decode_unsigned(data, off, end)
    for _ in range(n):
        mod_len, off = decode_unsigned(data, off, end)
        module_offset = off
        _validate_utf8_name(data, off, mod_len, end, "import module name")
        off += mod_len
        field_len, off = decode_unsigned(data, off, end)
        name_offset = off
        _validate_utf8_name(data, off, field_len, end, "import field name")
        off += field_len
        require_wasm(off < end, "truncated import descriptor")
        kind = data[off]
        off += 1
        if kind == 0:
            type_index, off = decode_unsigned(data, off, end)
            callbacks.on_function_import(
                Import(
                    module_offset=module_offset,
                    module_size=mod_len,
                    name_offset=name_offset,
                    name_size=field_len,
                    type_index=type_index,
                )
            )
        elif kind == 3:
            value_type = _read_value_type(data, off, end)
            off += 1
            require_wasm(off < end, "truncated global import descriptor")
            require_wasm(data[off] <= 1, "invalid global mutability")
            mutable = data[off] == 0x01
            off += 1
            callbacks.on_global_import(value_type, mutable)
        elif kind == 2:
            minimum, maximum, off = _parse_limits(data, off, end, is_memory=True)
            descriptor = Import(
                module_offset=module_offset,
                module_size=mod_len,
                name_offset=name_offset,
                name_size=field_len,
                type_index=0,
                min_limit=minimum,
                max_limit=maximum,
            )
            callbacks.on_memory_import(descriptor, minimum, maximum)
        elif kind == 1:
            require_wasm(off < end, "truncated table import descriptor")
            elem_type = data[off]
            off += 1
            require_wasm(
                elem_type == ELEM_TYPE_FUNCREF,
                f"only funcref table imports are supported, got 0x{elem_type:02X}",
            )
            minimum, maximum, off = _parse_limits(data, off, end)
            callbacks.on_table_import(minimum, maximum)
        else:
            reject_wasm(f"unsupported import kind={kind}")

    require_wasm(off == end, "import section length mismatch")


def _parse_function_section(
    data: memoryview, off: int, end: int, allocator: BumpAllocator
) -> StaticVector[int]:
    n, off = decode_unsigned(data, off, end)
    storage_size = n * WASM_RAW_WORD_BYTES
    storage_offset = allocator.allocate(storage_size)
    type_indices = StaticVector[int](
        capacity=n,
        arena_offset=storage_offset,
        arena_size=storage_size,
    )
    for _ in range(n):
        idx, off = decode_unsigned(data, off, end)
        type_indices.append(idx)

    require_wasm(off == end, "function section length mismatch")
    return type_indices


def _parse_limits(
    data: memoryview, off: int, end: int, *, is_memory: bool = False
) -> tuple[int, int | None, int]:
    require_wasm(off < end, "truncated limits descriptor")
    flag = data[off]
    off += 1
    require_wasm(flag == 0x00 or flag == 0x01, f"invalid limits flag=0x{flag:02X}")
    minimum, off = decode_unsigned(data, off, end)
    maximum: int | None = None
    if flag == 0x01:
        maximum, off = decode_unsigned(data, off, end)
        require_wasm(minimum <= maximum, "limits minimum exceeds maximum")
    if is_memory:
        require_wasm(minimum <= 65536, "memory minimum exceeds 65536 pages")
        require_wasm(maximum is None or maximum <= 65536, "memory maximum exceeds 65536 pages")
        require_wasm(
            minimum <= FB_CONF_MAX_WASM_PAGES, "memory minimum exceeds FB_CONF_MAX_WASM_PAGES"
        )
        require_wasm(
            maximum is None or maximum <= FB_CONF_MAX_WASM_PAGES,
            "memory maximum exceeds FB_CONF_MAX_WASM_PAGES",
        )
    return minimum, maximum, off


def _parse_memory_section(
    data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder
) -> None:
    n, off = decode_unsigned(data, off, end)
    require_wasm(n <= 1, "only single linear memory is supported")
    for _ in range(n):
        mn, mx, off = _parse_limits(data, off, end, is_memory=True)
        callbacks.on_memory(mn, mx)

    require_wasm(off == end, "memory section length mismatch")


def _parse_table_section(
    data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder
) -> None:
    n, off = decode_unsigned(data, off, end)
    for _ in range(n):
        require_wasm(off < end, "truncated table descriptor")
        elem_type = data[off]
        off += 1
        require_wasm(
            elem_type == ELEM_TYPE_FUNCREF,
            f"only funcref tables are supported, got 0x{elem_type:02X}",
        )
        mn, mx, off = _parse_limits(data, off, end)
        callbacks.on_table(mn, mx)

    require_wasm(off == end, "table section length mismatch")


def _parse_element_section(
    data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder
) -> None:
    callbacks.on_element_section(off, end - off)


def _parse_global_section(
    data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder
) -> None:
    n, off = decode_unsigned(data, off, end)
    for _ in range(n):
        vtype = _read_value_type(data, off, end)
        off += 1
        require_wasm(off < end, "truncated global mutability field")
        require_wasm(data[off] <= 1, "invalid global mutability")
        mutable = data[off] == 0x01
        off += 1
        require_wasm(off < end, "truncated global initializer")
        opcode = data[off]
        off += 1
        init_global_index: int | None = None
        if opcode == op.I32_CONST:
            init_type = I32
            init_value, off = decode_signed(data, off, end, bits=32)
            init_value &= 0xFFFF_FFFF
        elif opcode == op.I64_CONST:
            init_type = I64
            init_value, off = decode_signed(data, off, end, bits=64)
            init_value &= 0xFFFF_FFFF_FFFF_FFFF
        elif opcode == op.F32_CONST:
            init_type = F32
            require_wasm(off + 4 <= end, "truncated f32 global initializer")
            init_value = int.from_bytes(data[off : off + 4], "little")
            off += 4
        elif opcode == op.F64_CONST:
            init_type = F64
            require_wasm(off + 8 <= end, "truncated f64 global initializer")
            init_value = int.from_bytes(data[off : off + 8], "little")
            off += 8
        else:
            if opcode == op.GLOBAL_GET:
                init_global_index, off = decode_unsigned(data, off, end)
                init_type = callbacks.imported_global_type(init_global_index)
                init_value = 0
            else:
                reject_wasm(f"unsupported global initializer opcode 0x{opcode:02X}")
        require_wasm(init_type == vtype, "global initializer type must match global type")
        require_wasm(off < end and data[off] == 0x0B, "global init expr must end with 0x0B")
        off += 1
        callbacks.on_global(
            Global(
                vtype=vtype,
                mutable=mutable,
                init_value=init_value,
                init_global_index=init_global_index if opcode == op.GLOBAL_GET else None,
            )
        )

    require_wasm(off == end, "global section length mismatch")


def _parse_export_section(
    data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder
) -> None:
    n, off = decode_unsigned(data, off, end)
    for _ in range(n):
        name_len, off = decode_unsigned(data, off, end)
        name_offset = off
        _validate_utf8_name(data, off, name_len, end, "export name")
        off += name_len
        require_wasm(off < end, "truncated export descriptor")
        kind = data[off]
        off += 1
        idx, off = decode_unsigned(data, off, end)
        callbacks.on_export(
            Export(name_offset=name_offset, name_size=name_len, kind=kind, index=idx)
        )

    require_wasm(off == end, "export section length mismatch")


def _parse_code_section(
    data: memoryview,
    off: int,
    end: int,
    type_indices: StaticVector[int],
    callbacks: WasmModuleBuilder,
    allocator: BumpAllocator,
) -> None:
    payload_start = off
    n, off = decode_unsigned(data, off, end)
    require_wasm(n == len(type_indices), "code section entry count must match function section")
    for i in range(n):
        body_size, off = decode_unsigned(data, off, end)
        body_start = off
        body_end = off + body_size
        require_wasm(body_end <= end, "function body exceeds code section bounds")
        n_local_groups, local_scan = decode_unsigned(data, body_start, body_end)
        local_count = 0
        for _ in range(n_local_groups):
            count, local_scan = decode_unsigned(data, local_scan, body_end)
            require_wasm(local_scan < body_end, "truncated local type")
            local_scan += 1
            local_count += count
        assert local_count <= FB_CONF_MAX_LOCALS, "section-count prevalidation must cap locals"
        _, loff = decode_unsigned(data, body_start, body_end)
        locals_offset = allocator.allocate(local_count)
        locals_extra = StaticVector[int](
            capacity=local_count,
            arena_offset=locals_offset,
            arena_size=local_count,
        )
        for _ in range(n_local_groups):
            count, loff = decode_unsigned(data, loff, body_end)
            vtype = _read_value_type(data, loff, body_end)
            loff += 1
            for _ in range(count):
                locals_extra.append(vtype)

        callbacks.on_function(
            Function(
                type_index=type_index_t(type_indices[i]),
                locals_extra=locals_extra,
                code_offset=code_offset_t(loff),
                code_size=code_offset_t(body_end - loff),
                code_pc_offset=wasm_pc_t(loff - payload_start),
            )
        )
        off = body_end

    require_wasm(off == end, "code section length mismatch")


def _parse_start_section(
    data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder
) -> None:
    func_idx, off = decode_unsigned(data, off, end)
    callbacks.on_start(func_idx)
    require_wasm(off == end, "start section length mismatch")


def _parse_data_section(data: memoryview, off: int, end: int, callbacks: WasmModuleBuilder) -> None:
    callbacks.on_data_section(off, end - off)


def _parse_custom_section(data: memoryview, off: int, end: int) -> None:
    name_size, off = decode_unsigned(data, off, end)
    _validate_utf8_name(data, off, name_size, end, "custom section name")


class _AnalysisControlFrame:
    __slots__ = (
        "else_seen",
        "height",
        "label_type",
        "opcode",
        "parent_unreachable",
        "result_type",
        "stack_word_height",
        "unreachable",
    )

    def __init__(
        self,
        height: int,
        result_type: int | None,
        label_type: int | None,
        opcode: int,
        parent_unreachable: bool,
        stack_word_height: int,
    ) -> None:
        self.height = height
        self.result_type = result_type
        self.stack_word_height = stack_word_height
        self.label_type = label_type
        self.opcode = opcode
        self.parent_unreachable = parent_unreachable
        self.unreachable = parent_unreachable
        self.else_seen = False


class _StackTypeAnalysisState:
    __slots__ = (
        "branch_layouts",
        "code",
        "controls",
        "drop_widths",
        "ended",
        "function",
        "locals_types",
        "max_stack_words",
        "module",
        "select_widths",
        "stack_words",
        "values",
    )

    def __init__(self, module: Module, function_index: int) -> None:
        self.module = module
        self.function = module.functions[function_index - len(module.imports)]
        self.code = module.code_for(function_index)
        self.locals_types = module.local_types(function_index)
        self.values: StaticVector[int | None] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
        self.controls: StaticVector[_AnalysisControlFrame] = StaticVector(
            capacity=min(33, len(self.code) + 1)
        )
        self.drop_widths: StaticVector[tuple[int, int]] = StaticVector(capacity=len(self.code))
        self.select_widths: StaticVector[tuple[int, int]] = StaticVector(capacity=len(self.code))
        self.branch_layouts: StaticVector[tuple[int, tuple[tuple[int, int], ...]]] = StaticVector(
            capacity=len(self.code)
        )
        self.ended = False
        self.stack_words = 0
        self.max_stack_words = 0
        function_type = module.func_type(function_index)
        assert function_type.results is not None and len(function_type.results) <= 1
        function_result = function_type.results[0] if function_type.results else None
        outer = _AnalysisControlFrame(0, function_result, function_result, -1, False, 0)
        self.controls.append(outer)

    @staticmethod
    def _label_words(value_type: int | None) -> int:
        return 0 if value_type is None else (2 if value_type == I64 or value_type == F64 else 1)

    def record_branch_layouts(self, offset: int, depths: Sequence[int]) -> None:
        layouts: StaticVector[tuple[int, int]] = StaticVector(capacity=len(depths))
        for depth in depths:
            require_wasm(0 <= depth < len(self.controls), "WASM branch depth out of range")
            target = self.controls[len(self.controls) - depth - 1]
            layouts.append((target.stack_word_height, self._label_words(target.label_type)))
        self.branch_layouts.append((offset, freeze_sequence(layouts)))

    def pop(self, expected_type: int | None = None) -> int | None:
        frame = self.controls[-1]
        if len(self.values) == frame.height and frame.unreachable:
            return expected_type
        require_wasm(len(self.values) > frame.height, "WASM operand stack underflow")
        actual_type = self.values.pop_back()
        self.stack_words -= 2 if actual_type == I64 or actual_type == F64 else 1
        assert self.stack_words >= 0
        if expected_type is not None and actual_type is not None:
            require_wasm(actual_type == expected_type, "WASM operand type mismatch")
        return actual_type

    def push(self, value_type: int) -> None:
        self.values.append(value_type)
        self.stack_words += 2 if value_type == I64 or value_type == F64 else 1
        self.max_stack_words = max(self.max_stack_words, self.stack_words)

    def pop_label(self, depth: int) -> int | None:
        require_wasm(0 <= depth < len(self.controls), "WASM branch depth out of range")
        return self.controls[len(self.controls) - depth - 1].label_type

    def mark_unreachable(self) -> None:
        frame = self.controls[-1]
        self.trim(frame.height)
        frame.unreachable = True

    def finish_arm(self, frame: _AnalysisControlFrame) -> None:
        if frame.result_type is not None:
            self.pop(frame.result_type)
        require_wasm(len(self.values) == frame.height, "WASM control result stack mismatch")
        self.trim(frame.height)

    def trim(self, height: int) -> None:
        while len(self.values) > height:
            value_type = self.values.pop_back()
            self.stack_words -= 2 if value_type == I64 or value_type == F64 else 1
        assert self.stack_words >= 0


_StackAnalysisHandler = Callable[[_StackTypeAnalysisState, int, int, int], None]


def _analyze_control(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode == op.UNREACHABLE:
        state.mark_unreachable()
        return
    if opcode == op.BLOCK or opcode == op.LOOP or opcode == op.IF:
        assert len(state.controls) - 1 < FB_CONF_MAX_NESTING_DEPTH, (
            "section-count prevalidation must cap control nesting"
        )
        if opcode == op.IF:
            state.pop(I32)
        blocktype = state.code[offset + 1]
        require_wasm(
            blocktype == 0x40
            or blocktype == I32
            or blocktype == I64
            or (blocktype == F32 or blocktype == F64),
            "invalid MVP block type",
        )
        result_type = None if blocktype == 0x40 else blocktype
        frame = _AnalysisControlFrame(
            len(state.values),
            result_type,
            None if opcode == op.LOOP else result_type,
            opcode,
            False,
            state.stack_words,
        )
        state.controls.append(frame)
        return
    if opcode == op.ELSE:
        frame = state.controls[-1]
        require_wasm(frame.opcode == op.IF and not frame.else_seen, "unexpected ELSE")
        state.finish_arm(frame)
        frame.else_seen = True
        frame.unreachable = frame.parent_unreachable
        return
    if opcode == op.END:
        require_wasm(bool(state.controls), "unexpected END")
        frame = state.controls[-1]
        require_wasm(
            frame.opcode != op.IF or frame.result_type is None or frame.else_seen,
            "result-producing IF requires ELSE",
        )
        state.finish_arm(frame)
        state.controls.pop_back()
        if not state.controls:
            state.ended = True
        elif frame.result_type is not None:
            state.push(frame.result_type)
        return
    if opcode == op.BR_IF:
        state.record_branch_layouts(offset, (operand,))
        state.pop(I32)
        label_type = state.pop_label(operand)
        if label_type is not None:
            state.pop(label_type)
            state.push(label_type)
        return
    if opcode == op.BR:
        state.record_branch_layouts(offset, (operand,))
        label_type = state.pop_label(operand)
        if label_type is not None:
            state.pop(label_type)
        state.mark_unreachable()
        return
    if opcode == op.BR_TABLE:
        state.pop(I32)
        label_count, cursor = decode_unsigned(state.code, offset + 1)
        default_depth = operand
        depths: StaticVector[int] = StaticVector(capacity=label_count + 1)
        default_type = state.pop_label(default_depth)
        for _ in range(label_count):
            depth, cursor = decode_unsigned(state.code, cursor)
            depths.append(depth)
            label_type = state.pop_label(depth)
            require_wasm(label_type == default_type, "br_table label types differ")
        depths.append(default_depth)
        state.record_branch_layouts(offset, freeze_sequence(depths))
        if default_type is not None:
            state.pop(default_type)
        state.mark_unreachable()
        return
    assert opcode == op.RETURN
    result_type = state.controls[0].label_type
    if result_type is not None:
        state.pop(result_type)
    state.mark_unreachable()


def _analyze_stack_ops(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode == op.DROP:
        value_type = state.pop()
        if value_type == I64 or value_type == F64:
            state.drop_widths.append((offset, 2))
        return
    state.pop(I32)
    right_type = state.pop()
    left_type = state.pop()
    require_wasm(
        left_type is None or right_type is None or left_type == right_type,
        "SELECT operands have different types",
    )
    selected_type = left_type if left_type is not None else right_type
    if selected_type is None:
        selected_type = I32
    if selected_type == I64 or selected_type == F64:
        state.select_widths.append((offset, 2))
    state.push(selected_type)


def _analyze_variables(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode == op.LOCAL_GET:
        require_wasm(
            operand < len(state.locals_types),
            "invalid WASM binary: operand < len(state.locals_types)",
        )
        state.push(state.locals_types[operand])
    elif opcode == op.LOCAL_SET:
        require_wasm(
            operand < len(state.locals_types),
            "invalid WASM binary: operand < len(state.locals_types)",
        )
        state.pop(state.locals_types[operand])
    elif opcode == op.LOCAL_TEE:
        require_wasm(
            operand < len(state.locals_types),
            "invalid WASM binary: operand < len(state.locals_types)",
        )
        value_type = state.locals_types[operand]
        state.pop(value_type)
        state.push(value_type)
    elif opcode == op.GLOBAL_GET:
        require_wasm(
            operand < len(state.module.globals),
            "invalid WASM binary: operand < len(state.module.globals)",
        )
        state.push(state.module.globals[operand].vtype)
    else:
        require_wasm(
            operand < len(state.module.globals),
            "invalid WASM binary: operand < len(state.module.globals)",
        )
        global_entry = state.module.globals[operand]
        require_wasm(global_entry.mutable, "GLOBAL_SET targets immutable global")
        state.pop(global_entry.vtype)


def _analyze_constants(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    value_type = (
        I32
        if opcode == op.I32_CONST
        else I64
        if opcode == op.I64_CONST
        else (F32 if opcode == op.F32_CONST else F64)
    )
    state.push(value_type)


def _memory_alignment_exponent(opcode: int) -> int:
    if (
        opcode == op.I32_LOAD
        or opcode == op.F32_LOAD
        or opcode == op.I32_STORE
        or opcode == op.F32_STORE
    ):
        return 2
    if (
        opcode == op.I64_LOAD
        or opcode == op.F64_LOAD
        or opcode == op.I64_STORE
        or opcode == op.F64_STORE
    ):
        return 3
    if (
        opcode == op.I32_LOAD8_S
        or opcode == op.I32_LOAD8_U
        or opcode == op.I64_LOAD8_S
        or opcode == op.I64_LOAD8_U
        or opcode == op.I32_STORE8
        or opcode == op.I64_STORE8
    ):
        return 0
    if (
        opcode == op.I32_LOAD16_S
        or opcode == op.I32_LOAD16_U
        or opcode == op.I64_LOAD16_S
        or opcode == op.I64_LOAD16_U
        or opcode == op.I32_STORE16
        or opcode == op.I64_STORE16
    ):
        return 1
    assert opcode == op.I64_LOAD32_S or opcode == op.I64_LOAD32_U or opcode == op.I64_STORE32
    return 2


def _validate_memory_alignment(state: _StackTypeAnalysisState, opcode: int, offset: int) -> None:
    alignment, _ = decode_unsigned(state.code, offset + 1)
    require_wasm(
        alignment <= _memory_alignment_exponent(opcode),
        "memory alignment exceeds the natural alignment",
    )


def _analyze_load(state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int) -> None:
    require_wasm(state.module.memory is not None, "load requires linear memory")
    _validate_memory_alignment(state, opcode, offset)
    state.pop(I32)
    if opcode == op.I32_LOAD or op.I32_LOAD8_S <= opcode <= op.I32_LOAD16_U:
        state.push(I32)
    elif opcode == op.I64_LOAD or op.I64_LOAD8_S <= opcode <= op.I64_LOAD32_U:
        state.push(I64)
    elif opcode == op.F32_LOAD:
        state.push(F32)
    else:
        assert opcode == op.F64_LOAD
        state.push(F64)


def _analyze_store(state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int) -> None:
    require_wasm(state.module.memory is not None, "store requires linear memory")
    _validate_memory_alignment(state, opcode, offset)
    if opcode == op.I32_STORE or opcode == op.I32_STORE8 or opcode == op.I32_STORE16:
        state.pop(I32)
    elif (
        opcode == op.I64_STORE
        or opcode == op.I64_STORE8
        or opcode == op.I64_STORE16
        or (opcode == op.I64_STORE32)
    ):
        state.pop(I64)
    elif opcode == op.F32_STORE:
        state.pop(F32)
    else:
        assert opcode == op.F64_STORE
        state.pop(F64)
    state.pop(I32)


def _analyze_memory_size_grow(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    require_wasm(state.module.memory is not None, "memory instruction requires linear memory")
    if opcode == op.MEMORY_GROW:
        state.pop(I32)
    state.push(I32)


def _analyze_fc_instruction(
    state: _StackTypeAnalysisState, opcode: int, offset: int, subopcode: int
) -> None:
    assert opcode == op.FC_PREFIX
    if op.FC_I32_TRUNC_SAT_F32_S <= subopcode <= op.FC_I64_TRUNC_SAT_F64_U:
        source_is_f32 = (
            subopcode == op.FC_I32_TRUNC_SAT_F32_S
            or subopcode == op.FC_I32_TRUNC_SAT_F32_U
            or subopcode == op.FC_I64_TRUNC_SAT_F32_S
            or subopcode == op.FC_I64_TRUNC_SAT_F32_U
        )
        source_type = F32 if source_is_f32 else F64
        state.pop(source_type)
        state.push(I32 if subopcode <= op.FC_I32_TRUNC_SAT_F64_U else I64)
        return

    require_wasm(state.module.memory is not None, "bulk memory instruction requires linear memory")
    _, cursor = decode_unsigned(state.code, offset + 1)
    if subopcode == op.FC_MEMORY_COPY:
        destination_memory, cursor = decode_unsigned(state.code, cursor)
        source_memory, _ = decode_unsigned(state.code, cursor)
        require_wasm(
            destination_memory == 0 and source_memory == 0,
            "invalid WASM binary: destination_memory == 0 and source_memory == 0",
        )
        state.pop(I32)  # length
        state.pop(I32)  # source
        state.pop(I32)  # destination
        return

    require_wasm(
        subopcode == op.FC_MEMORY_FILL, "invalid WASM binary: subopcode == op.FC_MEMORY_FILL"
    )
    memory_index, _ = decode_unsigned(state.code, cursor)
    require_wasm(memory_index == 0, "invalid WASM binary: memory_index == 0")
    state.pop(I32)  # length
    state.pop(I32)  # fill value
    state.pop(I32)  # destination


def _analyze_call(state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int) -> None:
    count = (
        len(state.module.imports) + len(state.module.functions)
        if opcode == op.CALL
        else len(state.module.types)
    )
    require_wasm(operand < count, "call function/type index is out of range")
    function_type = (
        state.module.func_type(operand) if opcode == op.CALL else state.module.type_at(operand)
    )
    if opcode == op.CALL_INDIRECT:
        require_wasm(len(state.module.tables) > 0, "CALL_INDIRECT requires a table")
        _, table_index_end = decode_unsigned(state.code, offset + 1)
        table_index, _ = decode_unsigned(state.code, table_index_end)
        require_wasm(table_index == 0, "only table index zero is supported")
        state.pop(I32)
    assert function_type.params is not None and function_type.results is not None
    parameter_index = len(function_type.params)
    while parameter_index > 0:
        parameter_index -= 1
        state.pop(function_type.params[parameter_index])
    if function_type.results:
        state.push(function_type.results[0])


def _analyze_comparison(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode == op.I32_EQZ:
        state.pop(I32)
    elif opcode >= 0x46 and opcode <= 0x4F:
        state.pop(I32)
        state.pop(I32)
    elif opcode == op.I64_EQZ:
        state.pop(I64)
    elif opcode >= 0x51 and opcode <= 0x5A:
        state.pop(I64)
        state.pop(I64)
    elif opcode >= 0x5B and opcode <= 0x60:
        state.pop(F32)
        state.pop(F32)
    else:
        state.pop(F64)
        state.pop(F64)
    state.push(I32)


def _analyze_i32_operation(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode <= 0x69 or opcode == op.I32_EXTEND8_S or opcode == op.I32_EXTEND16_S:
        state.pop(I32)
    else:
        state.pop(I32)
        state.pop(I32)
    state.push(I32)


def _analyze_i64_operation(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode <= 0x7B or opcode >= op.I64_EXTEND8_S:
        state.pop(I64)
    else:
        state.pop(I64)
        state.pop(I64)
    state.push(I64)


def _analyze_f32_unary(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    state.pop(F32)
    state.push(F32)


def _analyze_f32_binary(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    state.pop(F32)
    state.pop(F32)
    state.push(F32)


def _analyze_f64_unary(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    state.pop(F64)
    state.push(F64)


def _analyze_f64_binary(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    state.pop(F64)
    state.pop(F64)
    state.push(F64)


def _analyze_conversion(
    state: _StackTypeAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode == op.I32_WRAP_I64:
        state.pop(I64)
        state.push(I32)
    elif opcode <= op.I32_TRUNC_F64_U:
        state.pop(F32 if opcode == op.I32_TRUNC_F32_S or opcode == op.I32_TRUNC_F32_U else F64)
        state.push(I32)
    elif opcode == op.I64_EXTEND_I32_S or opcode == op.I64_EXTEND_I32_U:
        state.pop(I32)
        state.push(I64)
    elif opcode <= op.I64_TRUNC_F64_U:
        source_type = F32 if opcode == op.I64_TRUNC_F32_S or opcode == op.I64_TRUNC_F32_U else F64
        state.pop(source_type)
        state.push(I64)
    elif opcode == op.F32_CONVERT_I32_S or opcode == op.F32_CONVERT_I32_U:
        state.pop(I32)
        state.push(F32)
    elif opcode == op.F32_CONVERT_I64_S or opcode == op.F32_CONVERT_I64_U:
        state.pop(I64)
        state.push(F32)
    elif opcode == op.F32_DEMOTE_F64:
        state.pop(F64)
        state.push(F32)
    elif opcode == op.F64_CONVERT_I32_S or opcode == op.F64_CONVERT_I32_U:
        state.pop(I32)
        state.push(F64)
    elif opcode == op.F64_CONVERT_I64_S or opcode == op.F64_CONVERT_I64_U:
        state.pop(I64)
        state.push(F64)
    elif opcode == op.F64_PROMOTE_F32:
        state.pop(F32)
        state.push(F64)
    elif opcode == op.I32_REINTERPRET_F32:
        state.pop(F32)
        state.push(I32)
    elif opcode == op.I64_REINTERPRET_F64:
        state.pop(F64)
        state.push(I64)
    elif opcode == op.F32_REINTERPRET_I32:
        state.pop(I32)
        state.push(F32)
    else:
        assert opcode == op.F64_REINTERPRET_I64
        state.pop(I64)
        state.push(F64)


def _build_stack_analysis_dispatch() -> SequenceView[_StackAnalysisHandler | None]:
    """Build the immutable opcode dispatch table; mutable setup storage is temporary."""
    handlers = StaticVector[_StackAnalysisHandler | None](capacity=256)
    for _ in range(256):
        handlers.append(None)

    def assign(handler: _StackAnalysisHandler, *opcodes: int) -> None:
        for opcode in opcodes:
            handlers[opcode] = handler

    assign(
        _analyze_control,
        op.UNREACHABLE,
        op.BLOCK,
        op.LOOP,
        op.IF,
        op.ELSE,
        op.END,
        op.BR,
        op.BR_IF,
        op.BR_TABLE,
        op.RETURN,
    )
    assign(_analyze_stack_ops, op.DROP, op.SELECT)
    assign(
        _analyze_variables, op.LOCAL_GET, op.LOCAL_SET, op.LOCAL_TEE, op.GLOBAL_GET, op.GLOBAL_SET
    )
    assign(_analyze_constants, op.I32_CONST, op.I64_CONST, op.F32_CONST, op.F64_CONST)
    assign(_analyze_load, *range(op.I32_LOAD, op.I64_LOAD32_U + 1))
    assign(_analyze_store, *range(op.I32_STORE, op.I64_STORE32 + 1))
    assign(_analyze_memory_size_grow, op.MEMORY_SIZE, op.MEMORY_GROW)
    assign(_analyze_fc_instruction, op.FC_PREFIX)
    assign(_analyze_call, op.CALL, op.CALL_INDIRECT)
    assign(_analyze_comparison, *range(0x45, 0x67))
    assign(_analyze_i32_operation, *range(0x67, 0x79), op.I32_EXTEND8_S, op.I32_EXTEND16_S)
    assign(
        _analyze_i64_operation,
        *range(0x79, 0x8B),
        op.I64_EXTEND8_S,
        op.I64_EXTEND16_S,
        op.I64_EXTEND32_S,
    )
    assign(_analyze_f32_unary, *range(0x8B, 0x92))
    assign(_analyze_f32_binary, *range(0x92, 0x99))
    assign(_analyze_f64_unary, *range(0x99, 0xA0))
    assign(_analyze_f64_binary, *range(0xA0, 0xA7))
    assign(_analyze_conversion, *range(0xA7, 0xC0))
    return freeze_sequence(handlers)


_STACK_ANALYSIS_DISPATCH = _build_stack_analysis_dispatch()


def _index_stack_value_widths(module: Module, function_index: function_index_t) -> None:
    """Record wide DROP and SELECT operands by direct-indexed type analysis."""
    from tier2_runtime.interpreter.control_flow import iter_scan_instrs

    state = _StackTypeAnalysisState(module, function_index)
    for instruction in iter_scan_instrs(state.code):
        require_wasm(not state.ended, "instructions follow the function END")
        handler = _STACK_ANALYSIS_DISPATCH[instruction.opcode]
        if handler is not None:
            operand = instruction.operand if instruction.operand is not None else 0
            handler(state, instruction.opcode, instruction.offset, operand)
    require_wasm(state.ended, "function body is missing END")
    state.function.select_widths = ReadOnlyFlatMapStorage.create(state.select_widths)
    state.function.drop_widths = ReadOnlyFlatMapStorage.create(state.drop_widths)
    state.function.branch_layouts = ReadOnlyFlatMapStorage.create(state.branch_layouts)
    state.function.max_operand_stack_words = operand_words_t(state.max_stack_words)


def _parse_with_allocator(data: memoryview, allocator: BumpAllocator) -> Module:
    data = memoryview(data)
    section_counts = _read_section_counts(data)
    _validate_section_counts(section_counts)
    builder = WasmModuleBuilder(
        data,
        allocator,
        type_count=section_counts.types,
        function_import_count=section_counts.imported_functions,
        function_count=section_counts.functions,
        export_count=section_counts.exports,
        global_count=section_counts.globals + section_counts.imported_globals,
        table_count=section_counts.tables + section_counts.imported_tables,
    )
    type_indices = StaticVector[int](capacity=0)
    sections = WasmSectionReader(data)
    section = sections.next_section()
    while section is not None:
        sec_id = section.section_id
        off = section.payload_offset
        sec_end = off + section.payload_size
        if sec_id == 0:
            _parse_custom_section(data, off, sec_end)
        if sec_id == SEC_TYPE:
            _parse_type_section(data, off, sec_end, builder)
        elif sec_id == SEC_IMPORT:
            _parse_import_section(data, off, sec_end, builder)
        elif sec_id == SEC_FUNCTION:
            type_indices = _parse_function_section(data, off, sec_end, allocator)
        elif sec_id == SEC_TABLE:
            _parse_table_section(data, off, sec_end, builder)
        elif sec_id == SEC_MEMORY:
            _parse_memory_section(data, off, sec_end, builder)
        elif sec_id == SEC_GLOBAL:
            _parse_global_section(data, off, sec_end, builder)
        elif sec_id == SEC_EXPORT:
            _parse_export_section(data, off, sec_end, builder)
        elif sec_id == SEC_START:
            _parse_start_section(data, off, sec_end, builder)
        elif sec_id == SEC_ELEMENT:
            _parse_element_section(data, off, sec_end, builder)
        elif sec_id == SEC_CODE:
            _parse_code_section(data, off, sec_end, type_indices, builder, allocator)
        elif sec_id == SEC_DATA:
            _parse_data_section(data, off, sec_end, builder)
        elif sec_id == 0:
            pass
        else:
            reject_wasm(f"unsupported MVP section id={sec_id}")
        section = sections.next_section()

    return builder.finish(_index_stack_value_widths)


def parse(data: memoryview, allocator: BumpAllocator | None = None) -> Module:
    """Parse a module transactionally using its loader metadata arena."""

    if allocator is None:
        allocator = BumpAllocator()
    watermark = allocator.save()
    loaded = False
    try:
        module = _parse_with_allocator(data, allocator)
        module.finish_loading(allocator)
        loaded = True
        return module
    finally:
        if not loaded:
            allocator.restore(watermark)

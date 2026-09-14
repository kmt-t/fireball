"""
experiments/pysim/tier2_runtime/wasm_reader.py
Binary .wasm parser. Supports Type(1), Import(2), Function(3), Table(4),
Memory(5), Global(6), Export(7), Element(9), Code(10). Data(11) and custom
sections are skipped by length rather than rejected, so a real-world
module carrying them still loads.
"""

from __future__ import annotations

from leb128 import decode_signed, decode_unsigned
from system_containers import StaticVector
from wasm_module import (
    F32,
    F64,
    FB_CONF_MAX_LOCALS,
    I32,
    I64,
    DataSegment,
    Element,
    Export,
    Function,
    FuncType,
    Global,
    Import,
    Memory,
    Module,
    Table,
)
from wasm_opcodes import CALL, CALL_INDIRECT

MAGIC = b"\x00asm"
VERSION = b"\x01\x00\x00\x00"
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


def _read_value_type(data: memoryview, off: int) -> int:
    value_type = data[off]
    assert value_type == I32 or value_type == I64 or value_type == F32 or value_type == F64
    return value_type


class _SectionCounts:
    __slots__ = (
        "data_segments",
        "elements",
        "exports",
        "functions",
        "globals",
        "imports",
        "memories",
        "tables",
        "types",
    )

    def __init__(self) -> None:
        self.types = 0
        self.imports = 0
        self.functions = 0
        self.tables = 0
        self.memories = 0
        self.globals = 0
        self.exports = 0
        self.elements = 0
        self.data_segments = 0


def _read_section_counts(data: memoryview) -> _SectionCounts:
    counts = _SectionCounts()
    off = 8
    while off < len(data):
        section_id = data[off]
        off += 1
        section_length, off = decode_unsigned(data, off)
        section_end = off + section_length
        if section_id == SEC_TYPE:
            count, _ = decode_unsigned(data, off)
            counts.types = count
        elif section_id == SEC_IMPORT:
            count, _ = decode_unsigned(data, off)
            counts.imports = count
        elif section_id == SEC_FUNCTION:
            count, _ = decode_unsigned(data, off)
            counts.functions = count
        elif section_id == SEC_TABLE:
            count, _ = decode_unsigned(data, off)
            counts.tables = count
        elif section_id == SEC_MEMORY:
            count, _ = decode_unsigned(data, off)
            counts.memories = count
        elif section_id == SEC_GLOBAL:
            count, _ = decode_unsigned(data, off)
            counts.globals = count
        elif section_id == SEC_EXPORT:
            count, _ = decode_unsigned(data, off)
            counts.exports = count
        elif section_id == SEC_ELEMENT:
            count, _ = decode_unsigned(data, off)
            counts.elements = count
        elif section_id == SEC_DATA:
            count, _ = decode_unsigned(data, off)
            counts.data_segments = count
        off = section_end
    return counts


class WasmParseError(Exception):
    pass


class WasmUnsupportedFeatureError(WasmParseError):
    def __init__(self, message: str = "ERR_WASM_UNSUPPORTED_FEATURE"):
        super().__init__(message)
        self.error_code = "ERR_WASM_UNSUPPORTED_FEATURE"


def _has_nested_calls(code: memoryview) -> bool:
    """Return whether decoded function instructions contain a call opcode."""
    from control_flow import iter_scan_instrs

    try:
        for instruction in iter_scan_instrs(code):
            if instruction.opcode == CALL or instruction.opcode == CALL_INDIRECT:
                return True
    except WasmUnsupportedFeatureError:
        # Keep parsing unsupported modules for the existing execution-time
        # rejection contract. Do not select the no-call fast path when the
        # instruction stream could not be fully classified.
        return True
    return False


def _parse_functype(data: memoryview, off: int) -> tuple[FuncType, int]:
    record_offset = off
    tag = data[off]
    off += 1
    if tag != 0x60:
        assert False, f"expected functype tag 0x60, got 0x{tag:02X}"
    nparams, off = decode_unsigned(data, off)
    off += nparams

    nresults, off = decode_unsigned(data, off)
    off += nresults
    assert off <= len(data)
    return FuncType(params=None, results=None, offset=record_offset, size=off - record_offset), off


def _parse_type_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        ft, off = _parse_functype(data, off)
        module.types.append(ft)

    assert off == end, "type section length mismatch"


def _parse_import_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        mod_len, off = decode_unsigned(data, off)
        module_offset = off
        off += mod_len
        field_len, off = decode_unsigned(data, off)
        name_offset = off
        off += field_len
        kind = data[off]
        off += 1
        if kind != 0:
            assert False, f"only function imports (kind=0) are supported, got kind={kind}"
        type_index, off = decode_unsigned(data, off)
        module.imports.append(
            Import(
                module_offset=module_offset,
                module_size=mod_len,
                name_offset=name_offset,
                name_size=field_len,
                type_index=type_index,
            )
        )

    assert off == end, "import section length mismatch"


def _parse_function_section(data: memoryview, off: int, end: int) -> StaticVector[int]:
    n, off = decode_unsigned(data, off)
    type_indices = StaticVector[int](capacity=n)
    for _ in range(n):
        idx, off = decode_unsigned(data, off)
        type_indices.append(idx)

    assert off == end, "function section length mismatch"
    return type_indices


def _parse_limits(data: memoryview, off: int) -> tuple[int, int | None, int]:
    flag = data[off]
    off += 1
    minimum, off = decode_unsigned(data, off)
    if flag == 0x01:
        maximum, off = decode_unsigned(data, off)
        return minimum, maximum, off
    return minimum, None, off


def _parse_memory_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    assert n <= 1, "only single linear memory is supported"
    for _ in range(n):
        mn, mx, off = _parse_limits(data, off)
        module.memory = Memory(min_pages=mn, max_pages=mx)

    assert off == end, "memory section length mismatch"


def _parse_table_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        elem_type = data[off]
        off += 1
        assert elem_type == ELEM_TYPE_FUNCREF, (
            f"only funcref tables are supported, got 0x{elem_type:02X}"
        )
        mn, mx, off = _parse_limits(data, off)
        module.tables.append(Table(min_size=mn, max_size=mx))

    assert off == end, "table section length mismatch"


def _parse_element_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        table_index, off = decode_unsigned(data, off)
        # Offset expr: this experiment only supports `i32.const N end`.
        assert data[off] == 0x41, (
            "only i32.const offset expressions are supported for element segments"
        )
        off += 1
        offset, off = decode_signed(data, off)
        assert data[off] == 0x0B, "element offset expr must end with 0x0B"
        off += 1
        n_funcs, off = decode_unsigned(data, off)
        func_indices_offset = off
        for _ in range(n_funcs):
            _, off = decode_unsigned(data, off)

        module.elements.append(
            Element(
                table_index=table_index,
                offset=offset,
                func_indices_offset=func_indices_offset,
                func_indices_size=off - func_indices_offset,
                func_count=n_funcs,
            )
        )

    assert off == end, "element section length mismatch"


def _parse_global_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        vtype = _read_value_type(data, off)
        off += 1
        mutable = data[off] == 0x01
        off += 1
        # Init expr: this experiment only supports `i32.const N end`.
        assert data[off] == 0x41, "only i32.const init expressions are supported for globals"
        off += 1
        init_value, off = decode_signed(data, off)
        assert data[off] == 0x0B, "global init expr must end with 0x0B"
        off += 1
        module.globals.append(Global(vtype=vtype, mutable=mutable, init_value=init_value))

    assert off == end, "global section length mismatch"


def _parse_export_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        name_len, off = decode_unsigned(data, off)
        name_offset = off
        off += name_len
        kind = data[off]
        off += 1
        idx, off = decode_unsigned(data, off)
        module.exports.append(
            Export(name_offset=name_offset, name_size=name_len, kind=kind, index=idx)
        )

    assert off == end, "export section length mismatch"


def _parse_code_section(
    data: memoryview, off: int, end: int, type_indices: StaticVector[int], module: Module
) -> None:

    n, off = decode_unsigned(data, off)
    assert n == len(type_indices), "code section entry count must match function section"
    for i in range(n):
        body_size, off = decode_unsigned(data, off)
        body_start = off
        body_end = off + body_size
        n_local_groups, local_scan = decode_unsigned(data, body_start)
        local_count = 0
        for _ in range(n_local_groups):
            count, local_scan = decode_unsigned(data, local_scan)
            local_scan += 1
            local_count += count
        assert local_count <= FB_CONF_MAX_LOCALS
        _, loff = decode_unsigned(data, body_start)
        locals_extra = StaticVector[int](capacity=local_count)
        for _ in range(n_local_groups):
            count, loff = decode_unsigned(data, loff)
            vtype = _read_value_type(data, loff)
            loff += 1
            for _ in range(count):
                locals_extra.append(vtype)

        code = data[loff:body_end]  # instruction stream, including the trailing 0x0B (end)
        module.functions.append(
            Function(
                type_index=type_indices[i],
                locals_extra=locals_extra,
                code=None,
                code_offset=loff,
                code_size=body_end - loff,
                has_nested_calls=_has_nested_calls(code),
            )
        )
        off = body_end

    assert off == end, "code section length mismatch"


def _parse_start_section(data: memoryview, off: int, end: int, module: Module) -> None:
    func_idx, off = decode_unsigned(data, off)
    module.start_function = func_idx
    assert off == end, "start section length mismatch"


def _parse_data_section(data: memoryview, off: int, end: int, module: Module) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        mem_idx, off = decode_unsigned(data, off)
        # Offset expr: only i32.const N end
        assert data[off] == 0x41, (
            "only i32.const offset expressions are supported for data segments"
        )
        off += 1
        offset, off = decode_signed(data, off)
        assert data[off] == 0x0B, "data offset expr must end with 0x0B"
        off += 1
        data_len, off = decode_unsigned(data, off)
        data_offset = off
        off += data_len
        module.data_segments.append(
            DataSegment(
                memory_index=mem_idx,
                offset=offset,
                data_offset=data_offset,
                data_size=data_len,
            )
        )

    assert off == end, "data section length mismatch"


def parse(data: memoryview) -> Module:
    data = memoryview(data)
    if data[0:4] != MAGIC:
        assert False, "missing \\0asm magic header"
    if data[4:8] != VERSION:
        assert False, f"unsupported wasm version {data[4:8]!r}"
    section_counts = _read_section_counts(data)
    assert section_counts.memories <= 1
    module = Module()
    module.source = data
    module.configure_section_capacities(
        type_count=section_counts.types,
        import_count=section_counts.imports,
        function_count=section_counts.functions,
        export_count=section_counts.exports,
        global_count=section_counts.globals,
        table_count=section_counts.tables,
        element_count=section_counts.elements,
        data_segment_count=section_counts.data_segments,
    )
    type_indices = StaticVector[int](capacity=section_counts.functions)
    off = 8
    while off < len(data):
        sec_id = data[off]
        off += 1
        sec_len, off = decode_unsigned(data, off)
        sec_end = off + sec_len
        if sec_id == SEC_TYPE:
            _parse_type_section(data, off, sec_end, module)
        elif sec_id == SEC_IMPORT:
            _parse_import_section(data, off, sec_end, module)
        elif sec_id == SEC_FUNCTION:
            type_indices = _parse_function_section(data, off, sec_end)
        elif sec_id == SEC_TABLE:
            _parse_table_section(data, off, sec_end, module)
        elif sec_id == SEC_MEMORY:
            _parse_memory_section(data, off, sec_end, module)
        elif sec_id == SEC_GLOBAL:
            _parse_global_section(data, off, sec_end, module)
        elif sec_id == SEC_EXPORT:
            _parse_export_section(data, off, sec_end, module)
        elif sec_id == SEC_START:
            _parse_start_section(data, off, sec_end, module)
        elif sec_id == SEC_ELEMENT:
            _parse_element_section(data, off, sec_end, module)
        elif sec_id == SEC_CODE:
            _parse_code_section(data, off, sec_end, type_indices, module)
        elif sec_id == SEC_DATA:
            _parse_data_section(data, off, sec_end, module)

        # else: custom section -- skip its bytes.
        off = sec_end

    module.prepare_function_layouts()
    module.build_basic_block_index()
    return module

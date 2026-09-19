"""
experiments/pysim/tier2_runtime/wasm_reader.py
Binary .wasm parser. Supports Type(1), Import(2), Function(3), Table(4),
Memory(5), Global(6), Export(7), Element(9), Code(10). Data(11) and custom
sections are validated and decoded without copying their payloads.
"""

from __future__ import annotations

from collections.abc import Callable

import wasm_opcodes as op
from leb128 import decode_signed, decode_unsigned
from system_containers import ReadOnlyFlatMapStorage, StaticVector
from wasm_module import (
    F32,
    F64,
    FB_CONF_MAX_LOCALS,
    I32,
    I64,
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


def _validate_utf8_name(
    data: memoryview, offset: int, size: int, end: int, field_name: str
) -> None:
    name_end = offset + size
    assert name_end <= end, f"{field_name} exceeds section bounds"
    try:
        data[offset:name_end].tobytes().decode("utf-8")
    except UnicodeDecodeError:
        assert False, f"{field_name} is not valid UTF-8"


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


class _ParseCallbacks:
    """Parse-time event sink used to keep ROM parsing separate from storage."""

    __slots__ = ("module",)

    def __init__(self, module: Module) -> None:
        self.module = module

    def on_type(self, function_type: FuncType) -> None:
        self.module.types.append(function_type)

    def on_function_import(self, import_entry: Import) -> None:
        self.module.imports.append(import_entry)

    def on_global_import(self, value_type: int, mutable: bool) -> None:
        self.module.global_import_count += 1
        self.module.globals.append(
            Global(vtype=value_type, mutable=mutable, init_value=0, imported=True)
        )

    def on_memory_import(self, import_entry: Import, minimum: int, maximum: int | None) -> None:
        assert self.module.memory is None, "multiple imported/defined memories are unsupported"
        self.module.memory_import = import_entry
        self.module.memory = Memory(min_pages=minimum, max_pages=maximum, imported=True)

    def on_table_import(self, minimum: int, maximum: int | None) -> None:
        self.module.table_import_count += 1
        self.module.tables.append(Table(min_size=minimum, max_size=maximum, imported=True))

    def on_table(self, minimum: int, maximum: int | None) -> None:
        self.module.tables.append(Table(min_size=minimum, max_size=maximum))

    def on_memory(self, minimum: int, maximum: int | None) -> None:
        assert self.module.memory is None, "multiple imported/defined memories are unsupported"
        self.module.memory = Memory(min_pages=minimum, max_pages=maximum)

    def on_global(self, global_value: Global) -> None:
        self.module.globals.append(global_value)

    def on_export(self, export: Export) -> None:
        self.module.exports.append(export)

    def on_function(self, function: Function) -> None:
        self.module.functions.append(function)

    def on_start(self, function_index: int) -> None:
        self.module.start_function = function_index

    def on_element_section(self, offset: int, size: int) -> None:
        self.module.element_section_offset = offset
        self.module.element_section_size = size

    def on_data_section(self, offset: int, size: int) -> None:
        self.module.data_section_offset = offset
        self.module.data_section_size = size


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
    params = StaticVector[int](capacity=nparams)
    for _ in range(nparams):
        params.append(_read_value_type(data, off))
        off += 1

    nresults, off = decode_unsigned(data, off)
    assert nresults <= 1, "MVP functions have at most one result"
    results = StaticVector[int](capacity=nresults)
    for _ in range(nresults):
        results.append(_read_value_type(data, off))
        off += 1
    assert off <= len(data)
    return FuncType(
        params=params, results=results, offset=record_offset, size=off - record_offset
    ), off


def _parse_type_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        ft, off = _parse_functype(data, off)
        callbacks.on_type(ft)

    assert off == end, "type section length mismatch"


def _parse_import_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        mod_len, off = decode_unsigned(data, off)
        module_offset = off
        _validate_utf8_name(data, off, mod_len, end, "import module name")
        off += mod_len
        field_len, off = decode_unsigned(data, off)
        name_offset = off
        _validate_utf8_name(data, off, field_len, end, "import field name")
        off += field_len
        kind = data[off]
        off += 1
        if kind == 0:
            type_index, off = decode_unsigned(data, off)
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
            value_type = _read_value_type(data, off)
            off += 1
            mutable = data[off] == 0x01
            off += 1
            callbacks.on_global_import(value_type, mutable)
        elif kind == 2:
            minimum, maximum, off = _parse_limits(data, off, is_memory=True)
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
            elem_type = data[off]
            off += 1
            assert elem_type == ELEM_TYPE_FUNCREF, (
                f"only funcref table imports are supported, got 0x{elem_type:02X}"
            )
            minimum, maximum, off = _parse_limits(data, off)
            callbacks.on_table_import(minimum, maximum)
        else:
            assert False, f"unsupported import kind={kind}"

    assert off == end, "import section length mismatch"


def _parse_function_section(data: memoryview, off: int, end: int) -> StaticVector[int]:
    n, off = decode_unsigned(data, off)
    type_indices = StaticVector[int](capacity=n)
    for _ in range(n):
        idx, off = decode_unsigned(data, off)
        type_indices.append(idx)

    assert off == end, "function section length mismatch"
    return type_indices


def _parse_limits(
    data: memoryview, off: int, *, is_memory: bool = False
) -> tuple[int, int | None, int]:
    flag = data[off]
    off += 1
    assert flag == 0x00 or flag == 0x01, f"invalid limits flag=0x{flag:02X}"
    minimum, off = decode_unsigned(data, off)
    maximum: int | None = None
    if flag == 0x01:
        maximum, off = decode_unsigned(data, off)
        assert minimum <= maximum, "limits minimum exceeds maximum"
    if is_memory:
        assert minimum <= 65536, "memory minimum exceeds 65536 pages"
        assert maximum is None or maximum <= 65536, "memory maximum exceeds 65536 pages"
    return minimum, maximum, off


def _parse_memory_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    module = callbacks.module
    n, off = decode_unsigned(data, off)
    assert n <= 1, "only single linear memory is supported"
    for _ in range(n):
        assert module.memory is None, "multiple imported/defined memories are unsupported"
        mn, mx, off = _parse_limits(data, off, is_memory=True)
        callbacks.on_memory(mn, mx)

    assert off == end, "memory section length mismatch"


def _parse_table_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        elem_type = data[off]
        off += 1
        assert elem_type == ELEM_TYPE_FUNCREF, (
            f"only funcref tables are supported, got 0x{elem_type:02X}"
        )
        mn, mx, off = _parse_limits(data, off)
        callbacks.on_table(mn, mx)

    assert off == end, "table section length mismatch"


def _parse_element_section(
    data: memoryview, off: int, end: int, callbacks: _ParseCallbacks
) -> None:
    module = callbacks.module
    callbacks.on_element_section(off, end - off)

    def validate_element(table_index: int, _slot: int, _function_index: int) -> None:
        assert table_index < len(module.tables), "element segment table index out of range"

    module.stream_element_initializers(validate_element, (), resolve_globals=False)


def _parse_global_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    module = callbacks.module
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        vtype = _read_value_type(data, off)
        off += 1
        mutable = data[off] == 0x01
        off += 1
        opcode = data[off]
        off += 1
        init_global_index: int | None = None
        if opcode == op.I32_CONST:
            init_type = I32
            init_value, off = decode_signed(data, off)
            init_value &= 0xFFFF_FFFF
        elif opcode == op.I64_CONST:
            init_type = I64
            init_value, off = decode_signed(data, off)
            init_value &= 0xFFFF_FFFF_FFFF_FFFF
        elif opcode == op.F32_CONST:
            init_type = F32
            init_value = int.from_bytes(data[off : off + 4], "little")
            off += 4
        elif opcode == op.F64_CONST:
            init_type = F64
            init_value = int.from_bytes(data[off : off + 8], "little")
            off += 8
        else:
            if opcode == op.GLOBAL_GET:
                init_global_index, off = decode_unsigned(data, off)
                assert init_global_index < len(module.globals)
                imported_global = module.globals[init_global_index]
                assert imported_global.imported and not imported_global.mutable, (
                    "global initializer global.get must reference an imported immutable global"
                )
                init_type = imported_global.vtype
                init_value = 0
            else:
                assert False, f"unsupported global initializer opcode 0x{opcode:02X}"
        assert init_type == vtype, "global initializer type must match global type"
        assert data[off] == 0x0B, "global init expr must end with 0x0B"
        off += 1
        callbacks.on_global(
            Global(
                vtype=vtype,
                mutable=mutable,
                init_value=init_value,
                init_global_index=init_global_index if opcode == op.GLOBAL_GET else None,
            )
        )

    assert off == end, "global section length mismatch"


def _parse_export_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    n, off = decode_unsigned(data, off)
    for _ in range(n):
        name_len, off = decode_unsigned(data, off)
        name_offset = off
        _validate_utf8_name(data, off, name_len, end, "export name")
        off += name_len
        kind = data[off]
        off += 1
        idx, off = decode_unsigned(data, off)
        callbacks.on_export(
            Export(name_offset=name_offset, name_size=name_len, kind=kind, index=idx)
        )

    assert off == end, "export section length mismatch"


def _parse_code_section(
    data: memoryview,
    off: int,
    end: int,
    type_indices: StaticVector[int],
    callbacks: _ParseCallbacks,
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
        callbacks.on_function(
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


def _parse_start_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    func_idx, off = decode_unsigned(data, off)
    callbacks.on_start(func_idx)
    assert off == end, "start section length mismatch"


def _parse_data_section(data: memoryview, off: int, end: int, callbacks: _ParseCallbacks) -> None:
    module = callbacks.module
    callbacks.on_data_section(off, end - off)
    assert module.memory is not None, "data segment requires linear memory"

    def validate_data(_offset: int, _data: memoryview) -> None:
        return None

    module.stream_data_initializers(validate_data, (), resolve_globals=False)


def _parse_custom_section(data: memoryview, off: int, end: int) -> None:
    name_size, off = decode_unsigned(data, off)
    name_end = off + name_size
    assert name_end <= end, "custom section name exceeds section bounds"
    data[off:name_end].tobytes().decode("utf-8")


class _AnalysisControlFrame:
    __slots__ = (
        "else_seen",
        "height",
        "label_type",
        "opcode",
        "parent_unreachable",
        "result_type",
        "unreachable",
    )

    def __init__(
        self,
        height: int,
        result_type: int | None,
        label_type: int | None,
        opcode: int,
        parent_unreachable: bool,
    ) -> None:
        self.height = height
        self.result_type = result_type
        self.label_type = label_type
        self.opcode = opcode
        self.parent_unreachable = parent_unreachable
        self.unreachable = parent_unreachable
        self.else_seen = False


class _SelectAnalysisState:
    __slots__ = (
        "code",
        "controls",
        "drop_widths",
        "ended",
        "function",
        "locals_types",
        "module",
        "select_widths",
        "values",
    )

    def __init__(self, module: Module, function_index: int) -> None:
        self.module = module
        self.function = module.functions[function_index - len(module.imports)]
        self.code = module.code_for(function_index)
        self.locals_types = module.local_types(function_index)
        self.values: StaticVector[int | None] = StaticVector(capacity=len(self.code) + 1)
        self.controls: StaticVector[_AnalysisControlFrame] = StaticVector(
            capacity=min(33, len(self.code) + 1)
        )
        self.drop_widths: StaticVector[tuple[int, int]] = StaticVector(capacity=len(self.code))
        self.select_widths: StaticVector[tuple[int, int]] = StaticVector(capacity=len(self.code))
        self.ended = False
        function_type = module.func_type(function_index)
        assert function_type.results is not None and len(function_type.results) <= 1
        function_result = function_type.results[0] if function_type.results else None
        outer = _AnalysisControlFrame(0, function_result, function_result, -1, False)
        self.controls.append(outer)

    def pop(self, expected_type: int | None = None) -> int | None:
        frame = self.controls[-1]
        if len(self.values) == frame.height and frame.unreachable:
            return expected_type
        assert len(self.values) > frame.height, "WASM operand stack underflow"
        actual_type = self.values.pop_back()
        if expected_type is not None and actual_type is not None:
            assert actual_type == expected_type, "WASM operand type mismatch"
        return actual_type

    def push(self, value_type: int) -> None:
        self.values.append(value_type)

    def pop_label(self, depth: int) -> int | None:
        assert 0 <= depth < len(self.controls), "WASM branch depth out of range"
        return self.controls[len(self.controls) - depth - 1].label_type

    def mark_unreachable(self) -> None:
        frame = self.controls[-1]
        self.trim(frame.height)
        frame.unreachable = True

    def finish_arm(self, frame: _AnalysisControlFrame) -> None:
        if frame.result_type is not None:
            self.pop(frame.result_type)
        assert len(self.values) == frame.height, "WASM control result stack mismatch"
        self.trim(frame.height)

    def trim(self, height: int) -> None:
        while len(self.values) > height:
            self.values.pop_back()


_SelectAnalysisHandler = Callable[[_SelectAnalysisState, int, int, int], None]
_SELECT_ANALYSIS_HANDLERS: StaticVector[_SelectAnalysisHandler | None] = StaticVector(capacity=256)
for _ in range(256):
    _SELECT_ANALYSIS_HANDLERS.append(None)


def _select_analysis_handler(
    *opcodes: int,
) -> Callable[[_SelectAnalysisHandler], _SelectAnalysisHandler]:
    def register(handler: _SelectAnalysisHandler) -> _SelectAnalysisHandler:
        for opcode in opcodes:
            _SELECT_ANALYSIS_HANDLERS[opcode] = handler
        return handler

    return register


@_select_analysis_handler(
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
def _analyze_control(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    if opcode == op.UNREACHABLE:
        state.mark_unreachable()
        return
    if opcode == op.BLOCK or opcode == op.LOOP or opcode == op.IF:
        if opcode == op.IF:
            state.pop(I32)
        blocktype = state.code[offset + 1]
        assert (
            blocktype == 0x40
            or blocktype == I32
            or blocktype == I64
            or (blocktype == F32 or blocktype == F64)
        ), "invalid MVP block type"
        result_type = None if blocktype == 0x40 else blocktype
        frame = _AnalysisControlFrame(
            len(state.values),
            result_type,
            None if opcode == op.LOOP else result_type,
            opcode,
            False,
        )
        state.controls.append(frame)
        return
    if opcode == op.ELSE:
        frame = state.controls[-1]
        assert frame.opcode == op.IF and not frame.else_seen, "unexpected ELSE"
        state.finish_arm(frame)
        frame.else_seen = True
        frame.unreachable = frame.parent_unreachable
        return
    if opcode == op.END:
        assert state.controls, "unexpected END"
        frame = state.controls[-1]
        assert frame.opcode != op.IF or frame.result_type is None or frame.else_seen, (
            "result-producing IF requires ELSE"
        )
        state.finish_arm(frame)
        state.controls.pop_back()
        if not state.controls:
            state.ended = True
        elif frame.result_type is not None:
            state.push(frame.result_type)
        return
    if opcode == op.BR_IF:
        state.pop(I32)
        label_type = state.pop_label(operand)
        if label_type is not None:
            state.pop(label_type)
            state.push(label_type)
        return
    if opcode == op.BR:
        label_type = state.pop_label(operand)
        if label_type is not None:
            state.pop(label_type)
        state.mark_unreachable()
        return
    if opcode == op.BR_TABLE:
        state.pop(I32)
        label_count, cursor = decode_unsigned(state.code, offset + 1)
        default_depth = operand
        default_type = state.pop_label(default_depth)
        for _ in range(label_count):
            depth, cursor = decode_unsigned(state.code, cursor)
            assert state.pop_label(depth) == default_type, "br_table label types differ"
        if default_type is not None:
            state.pop(default_type)
        state.mark_unreachable()
        return
    assert opcode == op.RETURN
    result_type = state.controls[0].label_type
    if result_type is not None:
        state.pop(result_type)
    state.mark_unreachable()


@_select_analysis_handler(op.DROP, op.SELECT)
def _analyze_stack_ops(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    if opcode == op.DROP:
        value_type = state.pop()
        if value_type == I64 or value_type == F64:
            state.drop_widths.append((offset, 2))
        return
    state.pop(I32)
    right_type = state.pop()
    left_type = state.pop()
    assert left_type is None or right_type is None or left_type == right_type, (
        "SELECT operands have different types"
    )
    selected_type = left_type if left_type is not None else right_type
    if selected_type is None:
        selected_type = I32
    if selected_type == I64 or selected_type == F64:
        state.select_widths.append((offset, 2))
    state.push(selected_type)


@_select_analysis_handler(op.LOCAL_GET, op.LOCAL_SET, op.LOCAL_TEE, op.GLOBAL_GET, op.GLOBAL_SET)
def _analyze_variables(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    if opcode == op.LOCAL_GET:
        assert operand < len(state.locals_types)
        state.push(state.locals_types[operand])
    elif opcode == op.LOCAL_SET:
        assert operand < len(state.locals_types)
        state.pop(state.locals_types[operand])
    elif opcode == op.LOCAL_TEE:
        assert operand < len(state.locals_types)
        value_type = state.locals_types[operand]
        state.pop(value_type)
        state.push(value_type)
    elif opcode == op.GLOBAL_GET:
        assert operand < len(state.module.globals)
        state.push(state.module.globals[operand].vtype)
    else:
        assert operand < len(state.module.globals)
        global_entry = state.module.globals[operand]
        assert global_entry.mutable, "GLOBAL_SET targets immutable global"
        state.pop(global_entry.vtype)


@_select_analysis_handler(op.I32_CONST, op.I64_CONST, op.F32_CONST, op.F64_CONST)
def _analyze_constants(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
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


def _validate_memory_alignment(state: _SelectAnalysisState, opcode: int, offset: int) -> None:
    alignment, _ = decode_unsigned(state.code, offset + 1)
    assert alignment <= _memory_alignment_exponent(opcode), (
        "memory alignment exceeds the natural alignment"
    )


@_select_analysis_handler(*range(op.I32_LOAD, op.I64_LOAD32_U + 1))
def _analyze_load(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    assert state.module.memory is not None, "load requires linear memory"
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


@_select_analysis_handler(*range(op.I32_STORE, op.I64_STORE32 + 1))
def _analyze_store(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    assert state.module.memory is not None, "store requires linear memory"
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


@_select_analysis_handler(op.MEMORY_SIZE, op.MEMORY_GROW)
def _analyze_memory_size_grow(
    state: _SelectAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    assert state.module.memory is not None, "memory instruction requires linear memory"
    if opcode == op.MEMORY_GROW:
        state.pop(I32)
    state.push(I32)


@_select_analysis_handler(op.CALL, op.CALL_INDIRECT)
def _analyze_call(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    function_type = (
        state.module.func_type(operand) if opcode == op.CALL else state.module.type_at(operand)
    )
    if opcode == op.CALL_INDIRECT:
        assert len(state.module.tables) > 0, "CALL_INDIRECT requires a table"
        _, table_index_end = decode_unsigned(state.code, offset + 1)
        table_index, _ = decode_unsigned(state.code, table_index_end)
        assert table_index == 0, "only table index zero is supported"
        state.pop(I32)
    assert function_type.params is not None and function_type.results is not None
    parameter_index = len(function_type.params)
    while parameter_index > 0:
        parameter_index -= 1
        state.pop(function_type.params[parameter_index])
    if function_type.results:
        state.push(function_type.results[0])


@_select_analysis_handler(*range(0x45, 0x67))
def _analyze_comparison(
    state: _SelectAnalysisState, opcode: int, offset: int, operand: int
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


@_select_analysis_handler(*range(0x67, 0x79), op.I32_EXTEND8_S, op.I32_EXTEND16_S)
def _analyze_i32_operation(
    state: _SelectAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode <= 0x69 or opcode == op.I32_EXTEND8_S or opcode == op.I32_EXTEND16_S:
        state.pop(I32)
    else:
        state.pop(I32)
        state.pop(I32)
    state.push(I32)


@_select_analysis_handler(
    *range(0x79, 0x8B), op.I64_EXTEND8_S, op.I64_EXTEND16_S, op.I64_EXTEND32_S
)
def _analyze_i64_operation(
    state: _SelectAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    if opcode <= 0x7B or opcode >= op.I64_EXTEND8_S:
        state.pop(I64)
    else:
        state.pop(I64)
        state.pop(I64)
    state.push(I64)


@_select_analysis_handler(*range(0x8B, 0x92))
def _analyze_f32_unary(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    state.pop(F32)
    state.push(F32)


@_select_analysis_handler(*range(0x92, 0x99))
def _analyze_f32_binary(
    state: _SelectAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    state.pop(F32)
    state.pop(F32)
    state.push(F32)


@_select_analysis_handler(*range(0x99, 0xA0))
def _analyze_f64_unary(state: _SelectAnalysisState, opcode: int, offset: int, operand: int) -> None:
    state.pop(F64)
    state.push(F64)


@_select_analysis_handler(*range(0xA0, 0xA7))
def _analyze_f64_binary(
    state: _SelectAnalysisState, opcode: int, offset: int, operand: int
) -> None:
    state.pop(F64)
    state.pop(F64)
    state.push(F64)


@_select_analysis_handler(*range(0xA7, 0xC0))
def _analyze_conversion(
    state: _SelectAnalysisState, opcode: int, offset: int, operand: int
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


def _index_stack_value_widths(module: Module, function_index: int) -> None:
    """Record wide DROP and SELECT operands by direct-indexed type analysis."""
    from control_flow import iter_scan_instrs

    state = _SelectAnalysisState(module, function_index)
    for instruction in iter_scan_instrs(state.code):
        assert not state.ended, "instructions follow the function END"
        handler = _SELECT_ANALYSIS_HANDLERS[instruction.opcode]
        if handler is not None:
            operand = instruction.operand if instruction.operand is not None else 0
            handler(state, instruction.opcode, instruction.offset, operand)
    assert state.ended, "function body is missing END"
    state.function.select_widths = ReadOnlyFlatMapStorage.create(state.select_widths)
    state.function.drop_widths = ReadOnlyFlatMapStorage.create(state.drop_widths)


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
        global_count=section_counts.globals + section_counts.imports,
        table_count=section_counts.tables,
    )
    type_indices = StaticVector[int](capacity=section_counts.functions)
    callbacks = _ParseCallbacks(module)
    off = 8
    last_section_id = 0
    while off < len(data):
        sec_id = data[off]
        off += 1
        sec_len, off = decode_unsigned(data, off)
        sec_end = off + sec_len
        assert sec_end <= len(data), "section length exceeds module bounds"
        if sec_id == 0:
            _parse_custom_section(data, off, sec_end)
        else:
            assert 1 <= sec_id <= SEC_DATA, f"unsupported MVP section id={sec_id}"
            assert sec_id > last_section_id, "WASM sections are duplicated or out of order"
            last_section_id = sec_id
        if sec_id == SEC_TYPE:
            _parse_type_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_IMPORT:
            _parse_import_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_FUNCTION:
            type_indices = _parse_function_section(data, off, sec_end)
        elif sec_id == SEC_TABLE:
            _parse_table_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_MEMORY:
            _parse_memory_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_GLOBAL:
            _parse_global_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_EXPORT:
            _parse_export_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_START:
            _parse_start_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_ELEMENT:
            _parse_element_section(data, off, sec_end, callbacks)
        elif sec_id == SEC_CODE:
            _parse_code_section(data, off, sec_end, type_indices, callbacks)
        elif sec_id == SEC_DATA:
            _parse_data_section(data, off, sec_end, callbacks)
        elif sec_id == 0:
            pass
        else:
            assert False, f"unsupported MVP section id={sec_id}"
        off = sec_end

    for import_entry in module.imports:
        module.type_at(import_entry.type_index)
    if module.start_function is not None:
        start_type = module.func_type(module.start_function)
        assert start_type.params is not None and len(start_type.params) == 0, (
            "start function must not have parameters"
        )
        assert start_type.results is not None and len(start_type.results) == 0, (
            "start function must not have results"
        )
    module.prepare_function_layouts()
    for function_index in range(len(module.imports), len(module.imports) + len(module.functions)):
        _index_stack_value_widths(module, function_index)
    module.build_basic_block_index()
    return module

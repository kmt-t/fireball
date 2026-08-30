"""
experiments/pysim/wasm_reader.py
Binary .wasm parser. Supports Type(1), Import(2), Function(3), Table(4),
Memory(5), Global(6), Export(7), Element(9), Code(10). Data(11) and custom
sections are skipped by length rather than rejected, so a real-world
module carrying them still loads.
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

import sys

from pathlib import Path

from leb128 import decode_signed, decode_unsigned

from wasm_module import (
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
    VALTYPE_BYTES,
)

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


class WasmParseError(Exception):
    pass


class WasmUnsupportedFeatureError(WasmParseError):
    def __init__(self, message: str = "ERR_WASM_UNSUPPORTED_FEATURE"):

        super().__init__(message)

        self.error_code = "ERR_WASM_UNSUPPORTED_FEATURE"


def _read_vec_len(data: bytes, off: int) -> tuple[int, int]:

    return decode_unsigned(data, off)


def _parse_functype(data: bytes, off: int) -> tuple[FuncType, int]:

    tag = data[off]

    off += 1

    if tag != 0x60:
        raise WasmParseError(f"expected functype tag 0x60, got 0x{tag:02X}")

    nparams, off = decode_unsigned(data, off)

    params = []

    for _ in range(nparams):
        params.append(VALTYPE_BYTES[data[off]])

        off += 1

    nresults, off = decode_unsigned(data, off)

    results = []

    for _ in range(nresults):
        results.append(VALTYPE_BYTES[data[off]])

        off += 1

    return FuncType(tuple(params), tuple(results)), off


def _parse_type_section(data: bytes, off: int, end: int, module: Module) -> None:

    n, off = decode_unsigned(data, off)

    for _ in range(n):
        ft, off = _parse_functype(data, off)

        module.types.append(ft)

    assert off == end, "type section length mismatch"


def _parse_import_section(data: bytes, off: int, end: int, module: Module) -> None:

    n, off = decode_unsigned(data, off)

    for _ in range(n):
        mod_len, off = decode_unsigned(data, off)

        mod_name = data[off : off + mod_len].decode("utf-8")

        off += mod_len

        field_len, off = decode_unsigned(data, off)

        field_name = data[off : off + field_len].decode("utf-8")

        off += field_len

        kind = data[off]

        off += 1

        if kind != 0:
            raise WasmParseError(
                f"only function imports (kind=0) are supported, got kind={kind}"
            )

        type_index, off = decode_unsigned(data, off)

        module.imports.append(
            Import(module=mod_name, name=field_name, type_index=type_index)
        )

    assert off == end, "import section length mismatch"


def _parse_function_section(data: bytes, off: int, end: int) -> list[int]:

    n, off = decode_unsigned(data, off)

    type_indices = []

    for _ in range(n):
        idx, off = decode_unsigned(data, off)

        type_indices.append(idx)

    assert off == end, "function section length mismatch"

    return type_indices


def _parse_limits(data: bytes, off: int) -> tuple[int, int | None, int]:

    flag = data[off]

    off += 1

    minimum, off = decode_unsigned(data, off)

    if flag == 0x01:
        maximum, off = decode_unsigned(data, off)

        return minimum, maximum, off

    return minimum, None, off


def _parse_memory_section(data: bytes, off: int, end: int, module: Module) -> None:

    n, off = decode_unsigned(data, off)

    assert n <= 1, "only single linear memory is supported"

    for _ in range(n):
        mn, mx, off = _parse_limits(data, off)

        module.memory = Memory(min_pages=mn, max_pages=mx)

    assert off == end, "memory section length mismatch"


def _parse_table_section(data: bytes, off: int, end: int, module: Module) -> None:

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


def _parse_element_section(data: bytes, off: int, end: int, module: Module) -> None:

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

        func_indices = []

        for _ in range(n_funcs):
            func_index, off = decode_unsigned(data, off)

            func_indices.append(func_index)

        module.elements.append(
            Element(table_index=table_index, offset=offset, func_indices=func_indices)
        )

    assert off == end, "element section length mismatch"


def _parse_global_section(data: bytes, off: int, end: int, module: Module) -> None:

    n, off = decode_unsigned(data, off)

    for _ in range(n):
        vtype = VALTYPE_BYTES[data[off]]

        off += 1

        mutable = data[off] == 0x01

        off += 1

        # Init expr: this experiment only supports `i32.const N end`.

        assert data[off] == 0x41, (
            "only i32.const init expressions are supported for globals"
        )

        off += 1

        init_value, off = decode_signed(data, off)

        assert data[off] == 0x0B, "global init expr must end with 0x0B"

        off += 1

        module.globals.append(
            Global(vtype=vtype, mutable=mutable, init_value=init_value)
        )

    assert off == end, "global section length mismatch"


def _parse_export_section(data: bytes, off: int, end: int, module: Module) -> None:

    n, off = decode_unsigned(data, off)

    for _ in range(n):
        name_len, off = decode_unsigned(data, off)

        name = data[off : off + name_len].decode("utf-8")

        off += name_len

        kind = data[off]

        off += 1

        idx, off = decode_unsigned(data, off)

        module.exports.append(Export(name=name, kind=kind, index=idx))

    assert off == end, "export section length mismatch"


def _parse_code_section(
    data: bytes, off: int, end: int, type_indices: list[int], module: Module
) -> None:

    n, off = decode_unsigned(data, off)

    assert n == len(type_indices), (
        "code section entry count must match function section"
    )

    for i in range(n):
        body_size, off = decode_unsigned(data, off)

        body_start = off

        body_end = off + body_size

        n_local_groups, loff = decode_unsigned(data, body_start)

        locals_extra: list[str] = []

        for _ in range(n_local_groups):
            count, loff = decode_unsigned(data, loff)

            vtype = VALTYPE_BYTES[data[loff]]

            loff += 1

            locals_extra.extend([vtype] * count)

        code = data[
            loff:body_end
        ]  # instruction stream, including the trailing 0x0B (end)

        module.functions.append(
            Function(type_index=type_indices[i], locals_extra=locals_extra, code=code)
        )

        off = body_end

    assert off == end, "code section length mismatch"


def _parse_start_section(data: bytes, off: int, end: int, module: Module) -> None:

    func_idx, off = decode_unsigned(data, off)

    module.start_function = func_idx

    assert off == end, "start section length mismatch"


def _parse_data_section(data: bytes, off: int, end: int, module: Module) -> None:

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

        seg_data = data[off : off + data_len]

        off += data_len

        module.data_segments.append(
            DataSegment(memory_index=mem_idx, offset=offset, data=seg_data)
        )

    assert off == end, "data section length mismatch"


def parse(data: bytes) -> Module:

    if data[0:4] != MAGIC:
        raise WasmParseError("missing \\0asm magic header")

    if data[4:8] != VERSION:
        raise WasmParseError(f"unsupported wasm version {data[4:8]!r}")

    module = Module()

    type_indices: list[int] = []

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

    return module

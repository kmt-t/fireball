"""
experiments/pysim/wasm_builder.py

A minimal WASM module *encoder*, used only to synthesize test .wasm
binaries -- there is no wat2wasm or wasmtime in this sandbox, so the test
fixtures in main.py/tests.py are built directly against this encoder
instead of being hand-copied hex blobs. This is test-fixture tooling, not
part of the design being validated: wasm_reader.py is the real parser under
test, and it only ever sees genuine binary-format bytes produced here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from leb128 import encode_signed, encode_unsigned
from wasm_module import VALTYPE_CODES
from wasm_reader import (
    ELEM_TYPE_FUNCREF, MAGIC, SEC_CODE, SEC_DATA, SEC_ELEMENT, SEC_EXPORT, SEC_FUNCTION, SEC_GLOBAL,
    SEC_IMPORT, SEC_MEMORY, SEC_START, SEC_TABLE, SEC_TYPE, VERSION,
)

EXPORT_KIND_FUNC = 0


def _vec(items: list[bytes]) -> bytes:
    return encode_unsigned(len(items)) + b"".join(items)


def _section(sec_id: int, body: bytes) -> bytes:
    return bytes([sec_id]) + encode_unsigned(len(body)) + body


def _name(s: str) -> bytes:
    b = s.encode("utf-8")
    return encode_unsigned(len(b)) + b


@dataclass
class FuncBuilder:
    params: tuple[str, ...]
    results: tuple[str, ...]
    locals_extra: list[str] = field(default_factory=list)
    code: bytearray = field(default_factory=bytearray)
    export_name: str | None = None

    # --- structured control flow -------------------------------------------------
    def unreachable(self):
        self.code.append(0x00); return self

    def nop(self):
        self.code.append(0x01); return self

    def block(self):
        self.code += bytes([0x02, 0x40]); return self

    def loop(self):
        self.code += bytes([0x03, 0x40]); return self

    def if_(self):
        self.code += bytes([0x04, 0x40]); return self

    def else_(self):
        self.code.append(0x05); return self

    def end(self):
        self.code.append(0x0B); return self

    def br(self, depth: int):
        self.code.append(0x0C); self.code += encode_unsigned(depth); return self

    def br_if(self, depth: int):
        self.code.append(0x0D); self.code += encode_unsigned(depth); return self

    def br_table(self, labels: list[int], default: int):
        self.code.append(0x0E)
        self.code += encode_unsigned(len(labels))
        for label in labels:
            self.code += encode_unsigned(label)
        self.code += encode_unsigned(default)
        return self

    def return_(self):
        self.code.append(0x0F); return self

    def call(self, func_index: int):
        self.code.append(0x10); self.code += encode_unsigned(func_index); return self

    def call_indirect(self, type_index: int, table_index: int = 0):
        self.code.append(0x11)
        self.code += encode_unsigned(type_index)
        self.code += encode_unsigned(table_index)
        return self

    def drop(self):
        self.code.append(0x1A); return self

    def select(self):
        self.code.append(0x1B); return self

    # --- locals --------------------------------------------------------------
    def local_get(self, idx: int):
        self.code.append(0x20); self.code += encode_unsigned(idx); return self

    def local_set(self, idx: int):
        self.code.append(0x21); self.code += encode_unsigned(idx); return self

    def local_tee(self, idx: int):
        self.code.append(0x22); self.code += encode_unsigned(idx); return self

    def global_get(self, idx: int):
        self.code.append(0x23); self.code += encode_unsigned(idx); return self

    def global_set(self, idx: int):
        self.code.append(0x24); self.code += encode_unsigned(idx); return self

    # --- memory ---------------------------------------------------------------
    def _memarg_op(self, opcode: int, align: int, offset: int):
        self.code.append(opcode); self.code += encode_unsigned(align); self.code += encode_unsigned(offset)
        return self

    def i32_load(self, align: int = 2, offset: int = 0): return self._memarg_op(0x28, align, offset)
    def i32_load8_s(self, align: int = 0, offset: int = 0): return self._memarg_op(0x2C, align, offset)
    def i32_load8_u(self, align: int = 0, offset: int = 0): return self._memarg_op(0x2D, align, offset)
    def i32_load16_s(self, align: int = 1, offset: int = 0): return self._memarg_op(0x2E, align, offset)
    def i32_load16_u(self, align: int = 1, offset: int = 0): return self._memarg_op(0x2F, align, offset)
    def i32_store(self, align: int = 2, offset: int = 0): return self._memarg_op(0x36, align, offset)
    def i32_store8(self, align: int = 0, offset: int = 0): return self._memarg_op(0x3A, align, offset)
    def i32_store16(self, align: int = 1, offset: int = 0): return self._memarg_op(0x3B, align, offset)

    def memory_size(self):
        self.code += bytes((0x3F, 0x00)); return self

    def memory_grow(self):
        self.code += bytes((0x40, 0x00)); return self

    # --- const / compare / arithmetic -----------------------------------------
    def i32_const(self, value: int):
        self.code.append(0x41); self.code += encode_signed(value); return self

    def _op(self, opcode: int):
        self.code.append(opcode); return self

    def i32_eqz(self): return self._op(0x45)
    def i32_eq(self): return self._op(0x46)
    def i32_ne(self): return self._op(0x47)
    def i32_lt_s(self): return self._op(0x48)
    def i32_lt_u(self): return self._op(0x49)
    def i32_gt_s(self): return self._op(0x4A)
    def i32_gt_u(self): return self._op(0x4B)
    def i32_le_s(self): return self._op(0x4C)
    def i32_le_u(self): return self._op(0x4D)
    def i32_ge_s(self): return self._op(0x4E)
    def i32_ge_u(self): return self._op(0x4F)

    def i32_add(self): return self._op(0x6A)
    def i32_sub(self): return self._op(0x6B)
    def i32_mul(self): return self._op(0x6C)
    def i32_div_s(self): return self._op(0x6D)
    def i32_div_u(self): return self._op(0x6E)
    def i32_rem_s(self): return self._op(0x6F)
    def i32_rem_u(self): return self._op(0x70)
    def i32_and(self): return self._op(0x71)
    def i32_or(self): return self._op(0x72)
    def i32_xor(self): return self._op(0x73)
    def i32_shl(self): return self._op(0x74)
    def i32_shr_s(self): return self._op(0x75)
    def i32_shr_u(self): return self._op(0x76)
    def i32_rotl(self): return self._op(0x77)
    def i32_rotr(self): return self._op(0x78)
    def i32_clz(self): return self._op(0x67)
    def i32_ctz(self): return self._op(0x68)
    def i32_popcnt(self): return self._op(0x69)

    def declare_local(self, vtype: str):
        self.locals_extra.append(vtype)
        return self

    def build_body(self) -> bytes:
        if not self.code or self.code[-1] != 0x0B:
            self.code.append(0x0B)
        return bytes(self.code)


@dataclass
class ImportSpec:
    module: str
    name: str
    params: tuple[str, ...]
    results: tuple[str, ...]


@dataclass
class GlobalSpec:
    vtype: str
    mutable: bool
    init_value: int


@dataclass
class ElementSpec:
    table_index: int
    offset: int
    func_indices: list[int]


@dataclass
class DataSegmentSpec:
    memory_index: int
    offset: int
    data: bytes


class ModuleBuilder:
    def __init__(self):
        self._funcs: list[FuncBuilder] = []
        self._imports: list[ImportSpec] = []
        self._globals: list[GlobalSpec] = []
        self._tables: list[tuple[int, int | None]] = []
        self._elements: list[ElementSpec] = []
        self._data_segments: list[DataSegmentSpec] = []
        self._start_function: int | None = None
        self._memory_pages: int | None = None

    def add_memory(self, min_pages: int, max_pages: int | None = None) -> None:
        self._memory_pages = (min_pages, max_pages)

    def set_start_function(self, func_idx: int) -> None:
        self._start_function = func_idx

    def add_global(self, vtype: str, mutable: bool, init_value: int) -> int:
        idx = len(self._globals)
        self._globals.append(GlobalSpec(vtype=vtype, mutable=mutable, init_value=init_value))
        return idx

    def add_table(self, min_size: int, max_size: int | None = None) -> int:
        idx = len(self._tables)
        self._tables.append((min_size, max_size))
        return idx

    def add_element(self, table_index: int, offset: int, func_indices: list[int]) -> None:
        self._elements.append(ElementSpec(table_index=table_index, offset=offset, func_indices=func_indices))

    def add_data_segment(self, offset: int, data: bytes, memory_index: int = 0) -> None:
        self._data_segments.append(DataSegmentSpec(memory_index=memory_index, offset=offset, data=data))

    def add_import(self, module: str, name: str, params: tuple[str, ...],
                    results: tuple[str, ...]) -> int:
        """Registers a host-function import and returns its function index
        in the unified function index space (imports always come first, so
        this is simply its position among prior add_import() calls)."""
        idx = len(self._imports)
        self._imports.append(ImportSpec(module=module, name=name, params=params, results=results))
        return idx

    def add_function(self, params: tuple[str, ...] = (), results: tuple[str, ...] = (),
                     locals_extra: list[str] | None = None,
                     export_name: str | None = None) -> FuncBuilder:
        fb = FuncBuilder(params=params, results=results, locals_extra=locals_extra or [], export_name=export_name)
        self._funcs.append(fb)
        return fb

    def function_index(self, fb: FuncBuilder) -> int:
        """The unified function index a locally-defined function will get,
        i.e. what a `call` to it (including a self-call, for recursion)
        must use as its operand."""
        return len(self._imports) + self._funcs.index(fb)

    def build(self) -> bytes:
        out = bytearray()
        out += MAGIC
        out += VERSION

        def functype_bytes(params: tuple[str, ...], results: tuple[str, ...]) -> bytes:
            params_b = b"".join(bytes([VALTYPE_CODES[p]]) for p in params)
            results_b = b"".join(bytes([VALTYPE_CODES[r]]) for r in results)
            return bytes([0x60]) + encode_unsigned(len(params)) + params_b \
                + encode_unsigned(len(results)) + results_b

        # Import types occupy the low type indices, in import order, so
        # import k's type_index is simply k; local function i's type_index
        # follows right after at len(imports)+i.
        type_entries = [functype_bytes(imp.params, imp.results) for imp in self._imports]
        type_entries += [functype_bytes(fb.params, fb.results) for fb in self._funcs]
        out += _section(SEC_TYPE, _vec(type_entries))

        if self._imports:
            import_entries = [
                _name(imp.module) + _name(imp.name) + bytes([EXPORT_KIND_FUNC]) + encode_unsigned(i)
                for i, imp in enumerate(self._imports)
            ]
            out += _section(SEC_IMPORT, _vec(import_entries))

        func_entries = [encode_unsigned(len(self._imports) + i) for i in range(len(self._funcs))]
        out += _section(SEC_FUNCTION, _vec(func_entries))

        if self._tables:
            table_entries = []
            for mn, mx in self._tables:
                if mx is None:
                    table_entries.append(bytes([ELEM_TYPE_FUNCREF, 0x00]) + encode_unsigned(mn))
                else:
                    table_entries.append(bytes([ELEM_TYPE_FUNCREF, 0x01]) + encode_unsigned(mn) + encode_unsigned(mx))
            out += _section(SEC_TABLE, _vec(table_entries))

        if self._memory_pages is not None:
            mn, mx = self._memory_pages
            if mx is None:
                mem_body = bytes([0x00]) + encode_unsigned(mn)
            else:
                mem_body = bytes([0x01]) + encode_unsigned(mn) + encode_unsigned(mx)
            out += _section(SEC_MEMORY, _vec([mem_body]))

        if self._globals:
            global_entries = []
            for g in self._globals:
                gtype = bytes([VALTYPE_CODES[g.vtype], 0x01 if g.mutable else 0x00])
                init_expr = bytes([0x41]) + encode_signed(g.init_value) + bytes([0x0B])
                global_entries.append(gtype + init_expr)
            out += _section(SEC_GLOBAL, _vec(global_entries))

        export_entries = []
        for i, fb in enumerate(self._funcs):
            if fb.export_name is not None:
                func_index = len(self._imports) + i
                export_entries.append(_name(fb.export_name) + bytes([EXPORT_KIND_FUNC]) + encode_unsigned(func_index))
        if export_entries:
            out += _section(SEC_EXPORT, _vec(export_entries))

        if self._start_function is not None:
            out += _section(SEC_START, encode_unsigned(self._start_function))

        if self._elements:
            element_entries = []
            for e in self._elements:
                offset_expr = bytes([0x41]) + encode_signed(e.offset) + bytes([0x0B])
                func_vec = encode_unsigned(len(e.func_indices)) + b"".join(
                    encode_unsigned(fi) for fi in e.func_indices
                )
                element_entries.append(encode_unsigned(e.table_index) + offset_expr + func_vec)
            out += _section(SEC_ELEMENT, _vec(element_entries))

        code_entries = []
        for fb in self._funcs:
            # group consecutive identical-type locals into (count, type) runs
            groups: list[tuple[int, str]] = []
            for vtype in fb.locals_extra:
                if groups and groups[-1][1] == vtype:
                    groups[-1] = (groups[-1][0] + 1, vtype)
                else:
                    groups.append((1, vtype))
            locals_b = encode_unsigned(len(groups))
            for count, vtype in groups:
                locals_b += encode_unsigned(count) + bytes([VALTYPE_CODES[vtype]])
            body = locals_b + fb.build_body()
            code_entries.append(encode_unsigned(len(body)) + body)
        out += _section(SEC_CODE, _vec(code_entries))

        if self._data_segments:
            data_entries = []
            for d in self._data_segments:
                offset_expr = bytes([0x41]) + encode_signed(d.offset) + bytes([0x0B])
                data_entries.append(encode_unsigned(d.memory_index) + offset_expr + encode_unsigned(len(d.data)) + d.data)
            out += _section(SEC_DATA, _vec(data_entries))

        return bytes(out)

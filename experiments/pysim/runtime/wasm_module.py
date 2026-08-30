"""
experiments/pysim/wasm_module.py
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

from dataclasses import dataclass, field

# WASM value types we support (MVP i32 only for now; i64/f32/f64 are parsed
# but not compiled).
I32 = "i32"
I64 = "i64"
F32 = "f32"
F64 = "f64"
VALTYPE_BYTES = {
    0x7F: I32,
    0x7E: I64,
    0x7D: F32,
    0x7C: F64,
}

VALTYPE_CODES = {v: k for k, v in VALTYPE_BYTES.items()}


@dataclass(frozen=True)
class FuncType:
    params: tuple[str, ...]
    results: tuple[str, ...]


@dataclass
class Function:
    type_index: int
    locals_extra: list[str]  # declared (non-parameter) locals, in order
    code: bytes  # raw instruction bytes (the function body, sans locals decl)
    name: str = ""


@dataclass
class Export:
    name: str
    kind: int  # 0=func, 1=table, 2=mem, 3=global
    index: int


@dataclass
class Import:
    module: str
    name: str
    type_index: int  # only function imports (kind=0) are supported


@dataclass
class Memory:
    min_pages: int
    max_pages: int | None


@dataclass
class Global:
    vtype: str
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
    func_indices: list[int]  # unified function index space


@dataclass
class DataSegment:
    memory_index: int
    offset: int  # i32.const offset expr
    data: bytes


@dataclass
class Module:
    types: list[FuncType] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    functions: list[Function] = field(default_factory=list)
    exports: list[Export] = field(default_factory=list)
    memory: Memory | None = None
    globals: list[Global] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    data_segments: list[DataSegment] = field(default_factory=list)
    start_function: int | None = None

    def init_memory_data(self, memory: bytearray) -> None:
        """Initializes memory with active data segments."""
        for seg in self.data_segments:
            if seg.offset + len(seg.data) <= len(memory):
                memory[seg.offset : seg.offset + len(seg.data)] = seg.data

    def table_contents(self, table_index: int) -> list[int | None]:
        """
        Materializes table `table_index` as a flat list of unified
                function indices (or None for an uninitialized slot), applying
                every active element segment targeting it in section order.
        """

        table = self.tables[table_index]
        slots: list[int | None] = [None] * table.min_size
        for elem in self.elements:
            if elem.table_index != table_index:
                continue
            for i, func_index in enumerate(elem.func_indices):
                slots[elem.offset + i] = func_index
        return slots

    def is_import(self, func_index: int) -> bool:
        return func_index < len(self.imports)

    def func_type(self, func_index: int) -> FuncType:
        if self.is_import(func_index):
            return self.types[self.imports[func_index].type_index]
        local = self.functions[func_index - len(self.imports)]
        return self.types[local.type_index]

    def export_func_index(self, name: str) -> int:
        for exp in self.exports:
            if exp.kind == 0 and exp.name == name:
                return exp.index
        raise KeyError(f"no exported function named {name!r}")

    def locals_layout(self, func_index: int) -> list[str]:
        """
        Params followed by declared locals -- WASM addresses both with a
                single local index space starting at 0. Imports have no body, so
                their "layout" is just their parameters.
        """

        ft = self.func_type(func_index)
        if self.is_import(func_index):
            return list(ft.params)
        local = self.functions[func_index - len(self.imports)]
        return list(ft.params) + list(local.locals_extra)

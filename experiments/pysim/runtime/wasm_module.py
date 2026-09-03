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

from dataclasses import dataclass, field

from system_containers import (
    RadixBinaryTreeView,
    ReadOnlyRadixBinaryTreeStorage,
    bswap32,
    build_radix_table,
)


@dataclass
class BasicBlock:
    """
    A straight-line run of WASM instructions ending with branch/return, as PC
    range + control-flow metadata ONLY. Deliberately holds no decoded op
    stream: `Module.blocks` keeps one of these per basic block in the program
    for the module's whole lifetime, and a real embedded target (32KB RAM)
    has no memory to spend on a redundant decoded-instruction copy per block
    on top of the raw bytecode it already holds. See `TraceBlock` for the
    transient, decode-on-demand input JIT compilation / block interpretation
    actually consumes.
    """

    head_pc: int
    next_pc: int | None = None
    loops_to: int | None = None
    frame_depth: int = 0
    byte_span: int = 0


@dataclass
class TraceBlock:
    """
    Transient compiler/interpreter input: a `(opcode, arg)` op stream for ONE
    basic block plus its control-flow successors. Never stored per block like
    `BasicBlock` -- built on demand, either by `control_flow.decode_block_ops`
    scoped to one block actually being compiled/interpreted right now (production),
    or directly by tests exercising `TraceCompiler`/`WASMTraceCompiler` in
    isolation.
    """

    head_pc: int
    ops: list[tuple[int, object]]
    next_pc: int | None = None
    loops_to: int | None = None


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
    control_map: object | None = None


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
    block_storage: ReadOnlyRadixBinaryTreeStorage[BasicBlock] | None = None
    block_tree: RadixBinaryTreeView[BasicBlock] | None = None
    control_skip_storage: ReadOnlyRadixBinaryTreeStorage[int] | None = None
    control_skip_tree: RadixBinaryTreeView[int] | None = None
    blocks: list[BasicBlock] = field(default_factory=list)

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

    def code_for(self, func_index: int) -> bytes:
        """Raw bytecode for a locally-defined function, by unified function index."""
        return self.functions[func_index - len(self.imports)].code

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

    def build_basic_block_index(self) -> None:
        """Extracts basic blocks and builds ReadOnlyRadixBinaryTreeStorage indexes on the loader side."""
        from control_flow import build_control_skip_storage, extract_basic_blocks

        n_imports = len(self.imports)
        try:
            self.control_skip_storage = build_control_skip_storage(
                self.functions, n_imports=n_imports
            )
            self.control_skip_tree = (
                self.control_skip_storage.view() if self.control_skip_storage is not None else None
            )
        except Exception:
            self.control_skip_storage = None
            self.control_skip_tree = None

        all_blocks: list[BasicBlock] = []
        for idx, fn in enumerate(self.functions):
            func_idx = n_imports + idx
            try:
                extracted = extract_basic_blocks(fn.code, func_index=func_idx)
                for head_pc, next_pc, loops_to, frame_depth, byte_span in extracted:
                    if byte_span > 0:
                        all_blocks.append(
                            BasicBlock(
                                head_pc=head_pc,
                                next_pc=next_pc,
                                loops_to=loops_to,
                                frame_depth=frame_depth,
                                byte_span=byte_span,
                            )
                        )
            except Exception:
                continue

        self.blocks = all_blocks
        if not all_blocks:
            self.block_storage = None
            self.block_tree = None
            return

        sorted_blocks = sorted(all_blocks, key=lambda b: bswap32(b.head_pc))
        inv_keys = [bswap32(b.head_pc) for b in sorted_blocks]
        radix_shift = 28
        radix_table = build_radix_table(inv_keys, radix_shift=radix_shift)
        self.block_storage = ReadOnlyRadixBinaryTreeStorage[BasicBlock](
            keys=inv_keys,
            values=sorted_blocks,
            radix_table=radix_table,
            radix_shift=radix_shift,
            entries=list(zip(inv_keys, sorted_blocks, strict=False)),
        )
        self.block_tree = self.block_storage.view()

    def get_block(self, pc: int) -> BasicBlock | None:
        """Looks up a BasicBlock by UnifiedPC via the loader's Radix tree (O(1) + O(log n))."""
        if self.block_tree is None:
            return None
        return self.block_tree.find(bswap32(pc))

    @property
    def total_basic_blocks(self) -> int:
        """Returns the exact total number of basic blocks across all functions in the module."""
        if self.blocks:
            return len(self.blocks)
        from control_flow import extract_basic_blocks

        n_imports = len(self.imports)
        count = 0
        for idx, fn in enumerate(self.functions):
            extracted = extract_basic_blocks(fn.code, func_index=n_imports + idx)
            count += sum(1 for _, ops, _, _, _, _ in extracted if ops)
        return count

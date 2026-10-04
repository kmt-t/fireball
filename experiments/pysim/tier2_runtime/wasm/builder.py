"""Apply decoded WASM section data to the runtime module model.

The byte reader owns bounds and format decoding. This builder owns module
storage, cross-section invariants, and the final module indexes.
"""

from __future__ import annotations

from collections.abc import Callable

from bump_allocator import BumpAllocator
from config import FB_CONF_MAX_FUNCTIONS, FB_CONF_MAX_GLOBALS, FB_CONF_MAX_TABLES
from tier2_runtime.wasm.module import (
    Export,
    Function,
    FuncType,
    Global,
    Import,
    Memory,
    Module,
    Table,
)

StackWidthIndexer = Callable[[Module, int], None]


class WasmModuleBuilder:
    """Collect decoded records and finalize one runtime ``Module``."""

    __slots__ = ("_allocator", "_module")

    def __init__(
        self,
        source: memoryview,
        allocator: BumpAllocator,
        *,
        type_count: int,
        function_import_count: int,
        function_count: int,
        export_count: int,
        global_count: int,
        table_count: int,
    ) -> None:
        module = Module()
        module.source = source
        module.configure_section_capacities(
            type_count=type_count,
            function_import_count=function_import_count,
            function_count=function_count,
            export_count=export_count,
            global_count=global_count,
            table_count=table_count,
            allocator=allocator,
        )
        self._module = module
        self._allocator = allocator

    def on_type(self, function_type: FuncType) -> None:
        self._module.types.append(function_type)

    def on_function_import(self, import_entry: Import) -> None:
        self._module.imports.append(import_entry)

    def on_global_import(self, value_type: int, mutable: bool) -> None:
        assert len(self._module.globals) < FB_CONF_MAX_GLOBALS, (
            "WASM global count exceeds configured maximum"
        )
        self._module.global_import_count += 1
        self._module.globals.append(
            Global(vtype=value_type, mutable=mutable, init_value=0, imported=True)
        )

    def on_memory_import(
        self, import_entry: Import, minimum: int, maximum: int | None
    ) -> None:
        assert self._module.memory is None, "multiple imported/defined memories are unsupported"
        self._module.memory_import = import_entry
        self._module.memory = Memory(min_pages=minimum, max_pages=maximum, imported=True)

    def on_table_import(self, minimum: int, maximum: int | None) -> None:
        assert len(self._module.tables) < FB_CONF_MAX_TABLES, (
            "WASM table count exceeds configured maximum"
        )
        self._module.table_import_count += 1
        self._module.tables.append(Table(min_size=minimum, max_size=maximum, imported=True))

    def on_table(self, minimum: int, maximum: int | None) -> None:
        assert len(self._module.tables) < FB_CONF_MAX_TABLES, (
            "WASM table count exceeds configured maximum"
        )
        self._module.tables.append(Table(min_size=minimum, max_size=maximum))

    def on_memory(self, minimum: int, maximum: int | None) -> None:
        assert self._module.memory is None, "multiple imported/defined memories are unsupported"
        self._module.memory = Memory(min_pages=minimum, max_pages=maximum)

    def on_global(self, global_value: Global) -> None:
        assert len(self._module.globals) < FB_CONF_MAX_GLOBALS, (
            "WASM global count exceeds configured maximum"
        )
        self._module.globals.append(global_value)

    def imported_global_type(self, global_index: int) -> int:
        assert 0 <= global_index < len(self._module.globals), (
            "global initializer global.get index is out of range"
        )
        global_value = self._module.globals[global_index]
        assert global_value.imported and not global_value.mutable, (
            "global initializer global.get must reference an imported immutable global"
        )
        return global_value.vtype

    def on_export(self, export: Export) -> None:
        self._module.exports.append(export)

    def on_function(self, function: Function) -> None:
        assert len(self._module.functions) < FB_CONF_MAX_FUNCTIONS, (
            "WASM function count exceeds configured maximum"
        )
        self._module.functions.append(function)

    def on_start(self, function_index: int) -> None:
        self._module.start_function = function_index

    def on_element_section(self, offset: int, size: int) -> None:
        self._module.element_section_offset = offset
        self._module.element_section_size = size

        def validate_element(table_index: int, _slot: int, _function_index: int) -> None:
            assert table_index < len(self._module.tables), (
                "element segment table index out of range"
            )

        self._module.stream_element_initializers(
            validate_element, (), resolve_globals=False
        )

    def on_data_section(self, offset: int, size: int) -> None:
        self._module.data_section_offset = offset
        self._module.data_section_size = size
        assert self._module.memory is not None, "data segment requires linear memory"

        def validate_data(_offset: int, _data: memoryview) -> None:
            return None

        self._module.stream_data_initializers(validate_data, (), resolve_globals=False)

    def finish(self, index_stack_value_widths: StackWidthIndexer) -> Module:
        """Validate cross-section references and build runtime lookup metadata."""
        for import_entry in self._module.imports:
            self._module.type_at(import_entry.type_index)

        if self._module.start_function is not None:
            start_type = self._module.func_type(self._module.start_function)
            assert start_type.params is not None and len(start_type.params) == 0, (
                "start function must not have parameters"
            )
            assert start_type.results is not None and len(start_type.results) == 0, (
                "start function must not have results"
            )

        self._module.prepare_function_layouts()
        function_start = len(self._module.imports)
        function_end = function_start + len(self._module.functions)
        for function_index in range(function_start, function_end):
            index_stack_value_widths(self._module, function_index)
        self._module.build_basic_block_index(self._allocator)
        return self._module

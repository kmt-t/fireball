"""
experiments/pysim/tier2_runtime/loader.py
WASM Loader & Zero-Copy Indexing Engine with hash-indexed radix-binary-tree views.
Conforms strictly to docs/components/tier2_runtime/runtime_loader.md
and docs/components/tier1_core/system_containers.md.
Implements:
1. Zero-Copy ROM-resident WASM32 parsing ({ROMParsing}, {ZeroCopyIndexing})
2. Transactional memory rollback via BumpAllocator ({META_BumpAllocator})
3. ReadOnlyRadixBinaryTreeView interval indexing for file offset reverse-lookup ({META_BinarySearch})
4. Hash + ReadOnlyRadixBinaryTreeView symbol and import lookup in O(k) ({META_AccessDictionary}, {META_BinarySearch})
5. Lightweight Verification Scope (V1-V6) ({LightweightVerifier})
6. Multi-module registry & import resolution ({MultiModule_Support})
"""

from __future__ import annotations

import struct
from enum import IntEnum
from typing import TypeVar

from bump_allocator import BumpAllocator
from config import FB_CONF_MAX_WASM_PAGES
from system_containers import (
    MutableFlatMapStorage,
    ReadOnlyFlatMapStorage,
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
)
from wasm_module import (
    LOADER_BASIC_BLOCK_ENTRY_BYTES,
    LOADER_EXPORT_ENTRY_BYTES,
    LOADER_GLOBAL_ENTRY_BYTES,
    LOADER_IMPORT_ENTRY_BYTES,
    LOADER_TABLE_ENTRY_BYTES,
    LOADER_TYPE_ENTRY_BYTES,
    WASM_RAW_WORD_BYTES,
)

# Configuration Constants
FB_CONF_MAX_MODULES = 4
FB_CONF_MAX_FUNCTIONS = 256
FB_CONF_MAX_TYPES = 256
FB_CONF_MAX_GLOBALS = 32
FB_CONF_MAX_IMPORTS = 32
FB_CONF_MAX_TABLES = 16
FB_CONF_MAX_MEMORIES = 4
FB_CONF_MAX_ENTITIES = 512
FB_CONF_MAX_FUNCTION_PARAMS = 16

ItemT = TypeVar("ItemT")


def _push_or_assert(target: StaticVector[ItemT], item: ItemT, label: str) -> None:
    pushed = target.push_back(item)
    assert pushed, f"{label} capacity exceeded"


class _SectionCounts:
    __slots__ = (
        "code",
        "data",
        "elements",
        "exports",
        "functions",
        "globals",
        "import_globals",
        "import_memories",
        "import_tables",
        "imports",
        "memories",
        "sections",
        "tables",
        "types",
    )

    def __init__(self) -> None:
        self.sections = 0
        self.types = 0
        self.imports = 0
        self.import_tables = 0
        self.import_memories = 0
        self.import_globals = 0
        self.functions = 0
        self.tables = 0
        self.memories = 0
        self.globals = 0
        self.exports = 0
        self.elements = 0
        self.code = 0
        self.data = 0


def _scan_section_counts(wasm_binary: memoryview) -> _SectionCounts:
    """Read section entry counts before reserving exact loader vector capacities."""

    stream = BinaryStream(wasm_binary)
    assert bytes(stream.read_bytes(4)) == b"\x00asm", "invalid WASM magic"
    assert stream.read_u32_le() == 1, "unsupported WASM version"
    counts = _SectionCounts()
    while stream.remaining() > 0:
        section_id = stream.read_u8()
        section_size = stream.read_leb128_u32()
        section_start = stream.tell()
        section_end = section_start + section_size
        assert section_end <= stream.limit, "section length exceeds module bounds"
        counts.sections += 1
        if section_id == SectionID.CUSTOM or section_id == SectionID.START:
            stream.seek(section_end)
            continue
        section = BinaryStream(wasm_binary, offset=section_start, length=section_size)
        if section_id == SectionID.TYPE:
            counts.types = section.read_leb128_u32()
        elif section_id == SectionID.IMPORT:
            counts.imports = section.read_leb128_u32()
            for _ in range(counts.imports):
                section.read_string_range()
                section.read_string_range()
                import_kind = section.read_u8()
                if import_kind == ExternalKind.FUNCTION:
                    section.read_leb128_u32()
                elif import_kind == ExternalKind.TABLE:
                    counts.import_tables += 1
                    section.read_u8()
                    flags = section.read_leb128_u32()
                    section.read_leb128_u32()
                    if flags & 1:
                        section.read_leb128_u32()
                elif import_kind == ExternalKind.MEMORY:
                    counts.import_memories += 1
                    flags = section.read_leb128_u32()
                    section.read_leb128_u32()
                    if flags & 1:
                        section.read_leb128_u32()
                elif import_kind == ExternalKind.GLOBAL:
                    counts.import_globals += 1
                    section.read_bytes(2)
                else:
                    assert False, f"unsupported WASM import kind={import_kind}"
            assert section.remaining() == 0, "import section length mismatch"
        elif section_id == SectionID.FUNCTION:
            counts.functions = section.read_leb128_u32()
        elif section_id == SectionID.TABLE:
            counts.tables = section.read_leb128_u32()
        elif section_id == SectionID.MEMORY:
            counts.memories = section.read_leb128_u32()
        elif section_id == SectionID.GLOBAL:
            counts.globals = section.read_leb128_u32()
        elif section_id == SectionID.EXPORT:
            counts.exports = section.read_leb128_u32()
        elif section_id == SectionID.ELEMENT:
            counts.elements = section.read_leb128_u32()
        elif section_id == SectionID.CODE:
            counts.code = section.read_leb128_u32()
        elif section_id == SectionID.DATA:
            counts.data = section.read_leb128_u32()
        else:
            assert False, f"unsupported WASM section id={section_id}"
        stream.seek(section_end)
    return counts


def _reserve_loader_vector[T](
    allocator: BumpAllocator, capacity: int, entry_bytes: int
) -> StaticVector[T]:
    assert capacity >= 0 and entry_bytes > 0
    storage_size = capacity * entry_bytes
    offset = allocator.allocate(storage_size)
    return StaticVector(capacity=capacity, arena_offset=offset, arena_size=storage_size)


FB_CONF_WASM_PAGE_SIZE = 65536


class WasmParseError(Exception):
    pass


class WasmVerifyError(Exception):
    pass


class WasmLinkError(Exception):
    pass


class SectionID:
    __slots__ = ()
    CUSTOM = 0
    TYPE = 1
    IMPORT = 2
    FUNCTION = 3
    TABLE = 4
    MEMORY = 5
    GLOBAL = 6
    EXPORT = 7
    START = 8
    ELEMENT = 9
    CODE = 10
    DATA = 11
    DATA_COUNT = 12


class DecodedEntityKind(IntEnum):
    """Fixed identifiers for ROM-backed decoded entity metadata."""

    SECTION = 0
    FUNCTION = 1
    GLOBAL = 2
    DATA = 3


class ValType:
    __slots__ = ()
    I32 = 0x7F
    I64 = 0x7E
    F32 = 0x7D
    F64 = 0x7C
    FUNC_REF = 0x70
    EXTERN_REF = 0x6F


class ExternalKind:
    __slots__ = ()
    FUNCTION = 0x00
    TABLE = 0x01
    MEMORY = 0x02
    GLOBAL = 0x03


def fnv1a_32(data: str) -> int:
    """FNV-1a 32-bit hash for fast zero-copy symbol lookup."""
    return fnv1a_32_bytes(memoryview(data.encode("utf-8")))


def fnv1a_32_bytes(data: memoryview) -> int:
    """FNV-1a 32-bit hash over a borrowed byte range."""
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def fnv1a_32_name(data: memoryview, offset: int, size: int) -> int:
    """Hash a name in the original WASM image without storing a decoded copy."""
    return fnv1a_32_bytes(data[offset : offset + size])


def fnv1a_32_name_pair(
    data: memoryview,
    first_offset: int,
    first_size: int,
    separator: memoryview,
    second_offset: int,
    second_size: int,
) -> int:
    """Hash two ROM names with their public-key separator bytes."""
    h = 0x811C9DC5
    for value in data[first_offset : first_offset + first_size]:
        h = ((h ^ value) * 0x01000193) & 0xFFFFFFFF
    for value in separator:
        h = ((h ^ value) * 0x01000193) & 0xFFFFFFFF
    for value in data[second_offset : second_offset + second_size]:
        h = ((h ^ value) * 0x01000193) & 0xFFFFFFFF
    return h


class BinaryStream:
    """Stream reader over ROM data with bounds check and LEB128 guard ({ROMParsing})."""

    __slots__ = ("cursor", "limit", "view")

    def __init__(
        self,
        data: memoryview,
        offset: int = 0,
        length: int | None = None,
    ):
        self.view = memoryview(data)
        self.cursor = offset
        self.limit = len(self.view) if length is None else offset + length
        if self.limit > len(self.view):
            assert False, (
                f"Stream limit {self.limit} exceeds underlying buffer size {len(self.view)}"
            )

    def remaining(self) -> int:
        return max(0, self.limit - self.cursor)

    def tell(self) -> int:
        return self.cursor

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > self.limit:
            assert False, f"Seek position {pos} out of range [0, {self.limit}]"
        self.cursor = pos

    def read_bytes(self, n: int) -> memoryview:
        if self.cursor + n > self.limit:
            assert False, (
                f"Unexpected end of stream: requested {n} bytes, {self.remaining()} remaining"
            )

        res = self.view[self.cursor : self.cursor + n]
        self.cursor += n
        return res

    def read_u8(self) -> int:
        return self.read_bytes(1)[0]

    def read_leb128_u32(self) -> int:
        result = 0
        shift = 0
        count = 0
        while True:
            if count >= 5:
                assert False, "LEB128 u32 exceeded maximum 5 bytes"
            if self.cursor >= self.limit:
                assert False, "Truncated LEB128 u32 integer"
            b = self.read_u8()
            count += 1
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                break
            shift += 7

        if result > 0xFFFFFFFF:
            assert False, "LEB128 u32 value out of 32-bit range"
        return result

    def read_leb128_s32(self) -> int:
        result = 0
        shift = 0
        count = 0
        while True:
            if count >= 5:
                assert False, "LEB128 s32 exceeded maximum 5 bytes"
            if self.cursor >= self.limit:
                assert False, "Truncated LEB128 s32 integer"
            b = self.read_u8()
            count += 1
            result |= (b & 0x7F) << shift
            shift += 7
            if (b & 0x80) == 0:
                if shift < 32 and (b & 0x40):
                    result |= ~0 << shift
                break
        result &= 0xFFFFFFFF
        if result & 0x80000000:
            result -= 0x100000000
        return result

    def read_string(self) -> str:
        length = self.read_leb128_u32()
        raw_bytes = self.read_bytes(length)
        try:
            return bytes(raw_bytes).decode("utf-8")
        except UnicodeDecodeError as e:
            assert False, f"Invalid UTF-8 string: {e}"

    def read_string_range(self) -> tuple[int, int]:
        """Validate and return a WASM name's ROM offset and byte length."""
        length = self.read_leb128_u32()
        offset = self.cursor
        raw_bytes = self.read_bytes(length)
        try:
            bytes(raw_bytes).decode("utf-8")
        except UnicodeDecodeError as e:
            assert False, f"Invalid UTF-8 string: {e}"
        return offset, length


class FuncType:
    __slots__ = ("params", "results")

    def __init__(self, params: StaticVector[int], results: StaticVector[int]):
        self.params = params
        self.results = results

    def __eq__(self, other: "FuncType") -> bool:
        return self.params == other.params and self.results == other.results


class ImportEntry:
    __slots__ = (
        "desc",
        "field_name_offset",
        "field_name_size",
        "kind",
        "module_name_offset",
        "module_name_size",
    )

    def __init__(
        self,
        module_name_offset: int,
        module_name_size: int,
        field_name_offset: int,
        field_name_size: int,
        kind: int,
        desc: int,
    ):
        self.module_name_offset = module_name_offset
        self.module_name_size = module_name_size
        self.field_name_offset = field_name_offset
        self.field_name_size = field_name_size
        self.kind = kind
        self.desc = desc


class ExportEntry:
    __slots__ = ("index", "kind", "name_offset", "name_size")

    def __init__(self, name_offset: int, name_size: int, kind: int, index: int):
        self.name_offset = name_offset
        self.name_size = name_size
        self.kind = kind
        self.index = index


class GlobalEntry:
    __slots__ = ("init_expr_offset", "init_expr_size", "mutable", "valtype")

    def __init__(self, valtype: int, mutable: bool, init_expr_offset: int, init_expr_size: int):
        self.valtype = valtype
        self.mutable = mutable
        self.init_expr_offset = init_expr_offset
        self.init_expr_size = init_expr_size


class MemoryEntry:
    __slots__ = ("initial_pages", "maximum_pages")

    def __init__(self, initial_pages: int, maximum_pages: int | None = None):
        self.initial_pages = initial_pages
        self.maximum_pages = maximum_pages


class TableEntry:
    __slots__ = ("elemtype", "initial", "maximum")

    def __init__(self, elemtype: int, initial: int, maximum: int | None = None):
        self.elemtype = elemtype
        self.initial = initial
        self.maximum = maximum


class SectionView:
    __slots__ = ("offset", "payload_offset", "payload_size", "section_id", "size")

    def __init__(
        self,
        section_id: int,
        offset: int,
        size: int,
        payload_offset: int,
        payload_size: int,
    ):
        self.section_id = section_id
        self.offset = offset
        self.size = size
        self.payload_offset = payload_offset
        self.payload_size = payload_size


class FunctionAccessor:
    __slots__ = ("_code_offset", "_code_size", "_rom_data", "func_idx", "type_idx", "type_sig")

    def __init__(
        self,
        func_idx: int,
        type_idx: int,
        type_sig: FuncType,
        rom_data: memoryview,
        code_offset: int,
        code_size: int,
    ):
        self.func_idx = func_idx
        self.type_idx = type_idx
        self.type_sig = type_sig
        self._rom_data = rom_data
        self._code_offset = code_offset
        self._code_size = code_size

    def get_type_index(self) -> int:
        return self.type_idx

    def get_signature(self) -> FuncType:
        return self.type_sig

    def get_locals_stream(self) -> BinaryStream:
        return BinaryStream(self._rom_data, offset=self._code_offset, length=self._code_size)

    def get_code_stream(self) -> BinaryStream:
        stream = self.get_locals_stream()
        local_vec_count = stream.read_leb128_u32()
        for _ in range(local_vec_count):
            stream.read_leb128_u32()
            stream.read_u8()
        return BinaryStream(self._rom_data, offset=stream.tell(), length=stream.remaining())


class GlobalAccessor:
    __slots__ = ("_rom_data", "entry", "global_idx")

    def __init__(
        self,
        global_idx: int,
        entry: GlobalEntry,
        rom_data: memoryview,
    ):
        self.global_idx = global_idx
        self.entry = entry
        self._rom_data = rom_data

    def get_metadata(self) -> tuple[int, bool]:
        return (self.entry.valtype, self.entry.mutable)

    def get_init_expr_stream(self) -> BinaryStream:
        return BinaryStream(
            self._rom_data,
            offset=self.entry.init_expr_offset,
            length=self.entry.init_expr_size,
        )


class DecodedEntity:
    """Decoded entity residing at file offset interval [start_offset, end_offset)."""

    __slots__ = ("end_offset", "index", "kind", "start_offset")

    def __init__(
        self,
        kind: DecodedEntityKind,
        start_offset: int,
        end_offset: int,
        index: int,
    ):
        self.kind = kind
        self.start_offset = start_offset
        self.end_offset = end_offset
        self.index = index


class ModuleView:
    """
    Read-only structured index window over ROM WASM binary.
    `{ROMParsing}` `{ZeroCopyIndexing}` `{META_AccessDictionary}` `{META_BinarySearch}`
    """

    __slots__ = (
        "code_offsets",
        "entity_offset_storage",
        "entity_registry",
        "export_storage",
        "exports_dict",
        "functions",
        "globals",
        "import_storage",
        "imports",
        "is_ready",
        "memories",
        "resolved_import_entries",
        "resolved_imports",
        "rom_binary",
        "sections",
        "start_func_idx",
        "tables",
        "types",
    )

    def __init__(self, rom_binary: memoryview, allocator: BumpAllocator, counts: _SectionCounts):
        self.rom_binary = memoryview(rom_binary)
        # Section IDs are SectionID.CUSTOM(0)..DATA_COUNT(12): a fixed, dense
        # WASM-spec-defined range, so a fixed-size array indexed by ID -- not
        # a dict -- is the direct fit.
        self.sections: StaticVector[SectionView | None] = _reserve_loader_vector(
            allocator, SectionID.DATA_COUNT + 1, 16
        )
        for _ in range(SectionID.DATA_COUNT + 1):
            self.sections.append(None)
        self.types: StaticVector[FuncType] = _reserve_loader_vector(
            allocator, counts.types, LOADER_TYPE_ENTRY_BYTES
        )
        self.imports: StaticVector[ImportEntry] = _reserve_loader_vector(
            allocator, counts.imports, LOADER_IMPORT_ENTRY_BYTES
        )
        self.functions: StaticVector[int] = _reserve_loader_vector(
            allocator, counts.functions, WASM_RAW_WORD_BYTES
        )
        self.tables: StaticVector[TableEntry] = _reserve_loader_vector(
            allocator,
            counts.tables + counts.import_tables,
            LOADER_TABLE_ENTRY_BYTES,
        )
        self.memories: StaticVector[MemoryEntry] = _reserve_loader_vector(
            allocator,
            counts.memories + counts.import_memories,
            LOADER_TABLE_ENTRY_BYTES,
        )
        self.globals: StaticVector[GlobalEntry] = _reserve_loader_vector(
            allocator,
            counts.globals + counts.import_globals,
            LOADER_GLOBAL_ENTRY_BYTES,
        )
        self.exports_dict: StaticVector[ExportEntry] = _reserve_loader_vector(
            allocator, counts.exports, LOADER_EXPORT_ENTRY_BYTES
        )
        self.resolved_import_entries: StaticVector[tuple[int, ExportEntry]] = (
            _reserve_loader_vector(allocator, counts.imports, 2 * WASM_RAW_WORD_BYTES)
        )
        self.code_offsets: StaticVector[tuple[int, int]] = _reserve_loader_vector(
            allocator, counts.functions, 2 * WASM_RAW_WORD_BYTES
        )
        self.start_func_idx: int | None = None
        self.resolved_imports: ReadOnlyFlatMapStorage[int, ExportEntry] = (
            ReadOnlyFlatMapStorage.create(())
        )
        self.is_ready: bool = False
        # Decoded entity registry & radix-binary-tree indexes ({META_BinarySearch})
        entity_capacity = min(
            FB_CONF_MAX_ENTITIES,
            counts.sections + counts.functions + counts.globals,
        )
        self.entity_registry: StaticVector[DecodedEntity] = _reserve_loader_vector(
            allocator, entity_capacity, LOADER_BASIC_BLOCK_ENTRY_BYTES
        )
        self.export_storage: ReadOnlyRadixBinaryTreeStorage[ExportEntry] | None = None
        self.import_storage: ReadOnlyRadixBinaryTreeStorage[ImportEntry] | None = None
        self.entity_offset_storage: ReadOnlyRadixBinaryTreeStorage[DecodedEntity] | None = None

    def register_entity(
        self,
        kind: DecodedEntityKind,
        start_offset: int,
        end_offset: int,
        index: int,
    ) -> DecodedEntity:
        entity = DecodedEntity(kind, start_offset, end_offset, index)
        self.entity_registry.append(entity)
        return entity

    def build_indexes(self, allocator: BumpAllocator) -> None:
        """Constructs read-only radix-binary-tree indexes for exports, imports, and entity offsets."""
        exp_keys: StaticVector[int] = _reserve_loader_vector(
            allocator, len(self.exports_dict), WASM_RAW_WORD_BYTES
        )
        for exp in self.exports_dict:
            exp_keys.append(fnv1a_32_name(self.rom_binary, exp.name_offset, exp.name_size))
        self.export_storage = ReadOnlyRadixBinaryTreeStorage.create(
            exp_keys, self.exports_dict, radix_shift=28
        )

        imp_keys: StaticVector[int] = _reserve_loader_vector(
            allocator, len(self.imports), WASM_RAW_WORD_BYTES
        )
        for imp in self.imports:
            imp_keys.append(
                fnv1a_32_name_pair(
                    self.rom_binary,
                    imp.module_name_offset,
                    imp.module_name_size,
                    memoryview(b"::"),
                    imp.field_name_offset,
                    imp.field_name_size,
                )
            )
        self.import_storage = ReadOnlyRadixBinaryTreeStorage.create(
            imp_keys, self.imports, radix_shift=28
        )

        ent_keys: StaticVector[int] = _reserve_loader_vector(
            allocator, len(self.entity_registry), WASM_RAW_WORD_BYTES
        )
        for entity in self.entity_registry:
            ent_keys.append(entity.start_offset)
        self.entity_offset_storage = ReadOnlyRadixBinaryTreeStorage.create(
            ent_keys, self.entity_registry, radix_shift=4
        )

    def lookup_export(self, name: str) -> ExportEntry | None:
        """Hash + read-only radix lookup with zero-copy string verification in O(k)."""
        return self.lookup_export_bytes(memoryview(name.encode("utf-8")))

    def lookup_export_bytes(self, name: memoryview) -> ExportEntry | None:
        """Lookup a borrowed UTF-8 name against ROM-backed export ranges."""
        if self.export_storage is None:
            return None
        h = fnv1a_32_bytes(name)
        return self.export_storage.view().find_matching(
            h,
            lambda candidate: (
                self.rom_binary[candidate.name_offset : candidate.name_offset + candidate.name_size]
                == name
            ),
        )

    def find_import(self, module_name: str, field_name: str) -> ImportEntry | None:
        """Hash + read-only radix import table lookup in O(k)."""
        return self.find_import_bytes(
            memoryview(module_name.encode("utf-8")), memoryview(field_name.encode("utf-8"))
        )

    def find_import_bytes(
        self, module_name: memoryview, field_name: memoryview
    ) -> ImportEntry | None:
        """Lookup borrowed UTF-8 names against ROM-backed import ranges."""
        if self.import_storage is None:
            return None
        h = fnv1a_32_bytes(module_name)
        for value in b"::":
            h = ((h ^ value) * 0x01000193) & 0xFFFFFFFF
        for value in field_name:
            h = ((h ^ value) * 0x01000193) & 0xFFFFFFFF
        return self.import_storage.view().find_matching(
            h,
            lambda candidate: (
                self.rom_binary[
                    candidate.module_name_offset : candidate.module_name_offset
                    + candidate.module_name_size
                ]
                == module_name
                and self.rom_binary[
                    candidate.field_name_offset : candidate.field_name_offset
                    + candidate.field_name_size
                ]
                == field_name
            ),
        )

    def decode_name(self, offset: int, size: int) -> str:
        """Materialize a ROM name only for diagnostics or test inspection."""
        return bytes(self.rom_binary[offset : offset + size]).decode("utf-8")

    def export_name(self, entry: ExportEntry) -> str:
        return self.decode_name(entry.name_offset, entry.name_size)

    def import_names(self, entry: ImportEntry) -> tuple[str, str]:
        return (
            self.decode_name(entry.module_name_offset, entry.module_name_size),
            self.decode_name(entry.field_name_offset, entry.field_name_size),
        )

    def sort_exports(self) -> None:
        """Keep the public export sequence ordered without retaining decoded names."""
        for index in range(1, len(self.exports_dict)):
            entry = self.exports_dict[index]
            entry_start = entry.name_offset
            entry_size = entry.name_size
            position = index
            while position > 0:
                previous = self.exports_dict[position - 1]
                left_size = previous.name_size if previous.name_size < entry_size else entry_size
                compare_index = 0
                ordering = 0
                while compare_index < left_size:
                    left_byte = self.rom_binary[previous.name_offset + compare_index]
                    right_byte = self.rom_binary[entry_start + compare_index]
                    if left_byte != right_byte:
                        ordering = -1 if left_byte < right_byte else 1
                        break
                    compare_index += 1
                if ordering == 0:
                    if previous.name_size < entry_size:
                        ordering = -1
                    elif previous.name_size > entry_size:
                        ordering = 1
                    else:
                        assert False, "WASM export names must be unique"
                if ordering <= 0:
                    break
                self.exports_dict[position] = previous
                position -= 1
            self.exports_dict[position] = entry

    def lookup_export_func(self, name: str) -> int | None:
        exp = self.lookup_export(name)
        if exp is not None and exp.kind == ExternalKind.FUNCTION:
            return exp.index
        return None

    def lookup_by_file_offset(self, file_offset: int) -> DecodedEntity | None:
        """Looks up a decoded entity containing the given file byte offset using a read-only radix view in O(k)."""
        if self.entity_offset_storage is None:
            return None
        return self.entity_offset_storage.view().find_interval(file_offset)

    def num_imported_functions(self) -> int:
        return sum(1 for imp in self.imports if imp.kind == ExternalKind.FUNCTION)

    def get_function(self, func_idx: int) -> FunctionAccessor:
        num_imported = self.num_imported_functions()
        if func_idx < num_imported:
            assert False, f"Cannot get code accessor for imported function index {func_idx}"
        internal_idx = func_idx - num_imported
        if internal_idx >= len(self.functions):
            assert False, f"Function index {func_idx} out of range"
        type_idx = self.functions[internal_idx]
        type_sig = self.types[type_idx]
        code_offset, code_size = self.code_offsets[internal_idx]
        return FunctionAccessor(
            func_idx=func_idx,
            type_idx=type_idx,
            type_sig=type_sig,
            rom_data=self.rom_binary,
            code_offset=code_offset,
            code_size=code_size,
        )

    def get_global(self, global_idx: int) -> GlobalAccessor:
        if global_idx < 0 or global_idx >= len(self.globals):
            assert False, f"Global index {global_idx} out of range"
        return GlobalAccessor(global_idx, self.globals[global_idx], self.rom_binary)


class WasmLoader:
    """
    WASM Loader & Lightweight Verifier (V1-V6) with transactional rollback.
    `{ROMParsing}` `{LightweightVerifier}` `{MultiModule_Support}` `{META_BumpAllocator}`
    """

    __slots__ = ("_allocator", "_registry", "max_modules", "max_wasm_pages")

    def __init__(
        self,
        allocator: BumpAllocator,
        max_modules: int = FB_CONF_MAX_MODULES,
        max_wasm_pages: int = FB_CONF_MAX_WASM_PAGES,
    ):
        self._allocator = allocator
        self._registry: MutableFlatMapStorage[bytes, ModuleView] = MutableFlatMapStorage(
            capacity=max_modules
        )
        self.max_modules = max_modules
        self.max_wasm_pages = max_wasm_pages

    def lookup(self, name: str) -> ModuleView | None:
        return self._registry.view().find(name.encode("utf-8"))

    def _lookup_name_bytes(self, name: memoryview) -> ModuleView | None:
        """Resolve a transient ROM name against the bounded module registry."""
        return self._registry.view().find(bytes(name))

    def prepare(self, module_name: str, wasm_binary: memoryview) -> ModuleView:
        if len(self._registry) >= self.max_modules:
            assert False, f"Module registry capacity ({self.max_modules}) exceeded"
        module_key = module_name.encode("utf-8")
        assert self._registry.view().find(module_key) is None, (
            f"Module name {module_name!r} is already registered"
        )
        watermark = self._allocator.save()
        try:
            counts = _scan_section_counts(wasm_binary)
            assert counts.types <= FB_CONF_MAX_TYPES
            assert counts.imports <= FB_CONF_MAX_IMPORTS
            assert counts.functions <= FB_CONF_MAX_FUNCTIONS
            assert counts.tables + counts.import_tables <= FB_CONF_MAX_TABLES
            assert counts.memories + counts.import_memories <= FB_CONF_MAX_MEMORIES
            assert counts.globals + counts.import_globals <= FB_CONF_MAX_GLOBALS
            assert counts.elements <= FB_CONF_MAX_ENTITIES
            assert counts.data <= FB_CONF_MAX_ENTITIES
            view = ModuleView(wasm_binary, self._allocator, counts)
            stream = BinaryStream(wasm_binary)
            # V1: Magic Number Check
            magic = bytes(stream.read_bytes(4))
            if magic != b"\x00asm":
                assert False, (
                    f"V1 Verification Failed: Invalid magic number {magic!r}, expected b'\\x00asm'"
                )

            # V2: Version Check
            ver_raw = stream.read_bytes(4)
            version = struct.unpack("<I", ver_raw)[0]
            if version != 1:
                assert False, (
                    f"V2 Verification Failed: Unsupported WASM version {version}, expected 1"
                )

            last_section_id = -1
            while stream.remaining() > 0:
                sec_start = stream.tell()
                sec_id = stream.read_u8()
                sec_size = stream.read_leb128_u32()
                payload_start = stream.tell()
                # V3: Section bounds check
                if payload_start + sec_size > stream.limit:
                    assert False, (
                        f"V3 Verification Failed: Section {sec_id} size {sec_size} exceeds binary end"
                    )

                # V4: Section order check
                if sec_id != SectionID.CUSTOM:
                    if sec_id <= last_section_id:
                        assert False, (
                            f"V4 Verification Failed: Section ID {sec_id} appears out of order after {last_section_id}"
                        )

                    last_section_id = sec_id

                sec_total_size = (payload_start - sec_start) + sec_size
                sec_view = SectionView(sec_id, sec_start, sec_total_size, payload_start, sec_size)
                view.sections[sec_id] = sec_view
                view.register_entity(
                    DecodedEntityKind.SECTION,
                    sec_start,
                    sec_start + sec_total_size,
                    sec_id,
                )
                sec_stream = BinaryStream(wasm_binary, offset=payload_start, length=sec_size)
                self._parse_section_content(sec_id, sec_stream, view, self._allocator)
                stream.seek(payload_start + sec_size)

            # V5: Type signature consistency
            num_types = len(view.types)
            for ftype_idx in view.functions:
                if ftype_idx >= num_types:
                    assert False, (
                        f"V5 Verification Failed: Function with invalid type index {ftype_idx}"
                    )

            for imp in view.imports:
                if imp.kind == ExternalKind.FUNCTION and imp.desc >= num_types:
                    assert False, (
                        f"V5 Verification Failed: Import with invalid type index {imp.desc}"
                    )

            if len(view.functions) != len(view.code_offsets):
                assert False, (
                    f"Function count ({len(view.functions)}) != Code count ({len(view.code_offsets)})"
                )

            # V6: Memory page budget
            for mem in view.memories:
                if mem.initial_pages > self.max_wasm_pages:
                    assert False, (
                        f"V6 Verification Failed: Memory pages {mem.initial_pages} > budget {self.max_wasm_pages}"
                    )

            view.sort_exports()
            view.build_indexes(self._allocator)
            if not view.imports:
                view.is_ready = True

            assert self._registry.insert(module_key, view)
            return view
        except Exception:
            self._allocator.restore(watermark)
            assert False, "WASM module preparation failed after allocator rollback"

    def _parse_section_content(
        self, sec_id: int, stream: BinaryStream, view: ModuleView, allocator: BumpAllocator
    ) -> None:
        if sec_id == SectionID.TYPE:
            count = stream.read_leb128_u32()
            for _ in range(count):
                form = stream.read_u8()
                if form != 0x60:
                    assert False, f"Invalid type form 0x{form:02X}"
                p_count = stream.read_leb128_u32()
                if p_count > FB_CONF_MAX_FUNCTION_PARAMS:
                    assert False, "Function parameter count exceeds fixed capacity"
                params = _reserve_loader_vector(allocator, p_count, 1)
                for _ in range(p_count):
                    _push_or_assert(params, stream.read_u8(), "function parameter")
                r_count = stream.read_leb128_u32()
                if r_count > FB_CONF_MAX_FUNCTION_PARAMS:
                    assert False, "Function result count exceeds fixed capacity"
                results = _reserve_loader_vector(allocator, r_count, 1)
                for _ in range(r_count):
                    _push_or_assert(results, stream.read_u8(), "function result")
                _push_or_assert(view.types, FuncType(params, results), "type")
        elif sec_id == SectionID.IMPORT:
            count = stream.read_leb128_u32()
            for _ in range(count):
                mod_name_offset, mod_name_size = stream.read_string_range()
                field_name_offset, field_name_size = stream.read_string_range()
                kind = stream.read_u8()
                if kind == ExternalKind.FUNCTION:
                    type_idx = stream.read_leb128_u32()
                    _push_or_assert(
                        view.imports,
                        ImportEntry(
                            mod_name_offset,
                            mod_name_size,
                            field_name_offset,
                            field_name_size,
                            kind,
                            type_idx,
                        ),
                        "import",
                    )
                elif kind == ExternalKind.TABLE:
                    elemtype = stream.read_u8()
                    flags = stream.read_leb128_u32()
                    initial = stream.read_leb128_u32()
                    maximum = stream.read_leb128_u32() if (flags & 1) else None
                    _push_or_assert(view.tables, TableEntry(elemtype, initial, maximum), "table")
                    _push_or_assert(
                        view.imports,
                        ImportEntry(
                            mod_name_offset,
                            mod_name_size,
                            field_name_offset,
                            field_name_size,
                            kind,
                            0,
                        ),
                        "import",
                    )
                elif kind == ExternalKind.MEMORY:
                    flags = stream.read_leb128_u32()
                    initial = stream.read_leb128_u32()
                    maximum = stream.read_leb128_u32() if (flags & 1) else None
                    _push_or_assert(view.memories, MemoryEntry(initial, maximum), "memory")
                    _push_or_assert(
                        view.imports,
                        ImportEntry(
                            mod_name_offset,
                            mod_name_size,
                            field_name_offset,
                            field_name_size,
                            kind,
                            0,
                        ),
                        "import",
                    )
                elif kind == ExternalKind.GLOBAL:
                    valtype = stream.read_u8()
                    mutable = stream.read_u8() == 1
                    _push_or_assert(view.globals, GlobalEntry(valtype, mutable, 0, 0), "global")
                    _push_or_assert(
                        view.imports,
                        ImportEntry(
                            mod_name_offset,
                            mod_name_size,
                            field_name_offset,
                            field_name_size,
                            kind,
                            0,
                        ),
                        "import",
                    )
        elif sec_id == SectionID.FUNCTION:
            count = stream.read_leb128_u32()
            if count > FB_CONF_MAX_FUNCTIONS:
                assert False, "Function count exceeds FB_CONF_MAX_FUNCTIONS"
            for _ in range(count):
                _push_or_assert(view.functions, stream.read_leb128_u32(), "function")
        elif sec_id == SectionID.TABLE:
            count = stream.read_leb128_u32()
            for _ in range(count):
                elemtype = stream.read_u8()
                flags = stream.read_leb128_u32()
                initial = stream.read_leb128_u32()
                maximum = stream.read_leb128_u32() if (flags & 1) else None
                _push_or_assert(view.tables, TableEntry(elemtype, initial, maximum), "table")
        elif sec_id == SectionID.MEMORY:
            count = stream.read_leb128_u32()
            for _ in range(count):
                flags = stream.read_leb128_u32()
                initial = stream.read_leb128_u32()
                maximum = stream.read_leb128_u32() if (flags & 1) else None
                _push_or_assert(view.memories, MemoryEntry(initial, maximum), "memory")
        elif sec_id == SectionID.GLOBAL:
            count = stream.read_leb128_u32()
            for g_idx in range(count):
                valtype = stream.read_u8()
                mutable = stream.read_u8() == 1
                init_start = stream.tell()
                while stream.remaining() > 0 and stream.read_u8() != 0x0B:
                    pass
                init_size = stream.tell() - init_start
                g_entry = GlobalEntry(valtype, mutable, init_start, init_size)
                _push_or_assert(view.globals, g_entry, "global")
                view.register_entity(
                    DecodedEntityKind.GLOBAL,
                    init_start,
                    init_start + init_size,
                    g_idx,
                )
        elif sec_id == SectionID.EXPORT:
            count = stream.read_leb128_u32()
            for _ in range(count):
                name_offset, name_size = stream.read_string_range()
                kind = stream.read_u8()
                index = stream.read_leb128_u32()
                _push_or_assert(
                    view.exports_dict, ExportEntry(name_offset, name_size, kind, index), "export"
                )
        elif sec_id == SectionID.START:
            view.start_func_idx = stream.read_leb128_u32()
        elif sec_id == SectionID.CODE:
            count = stream.read_leb128_u32()
            for c_idx in range(count):
                body_size = stream.read_leb128_u32()
                body_start = stream.tell()
                _push_or_assert(view.code_offsets, (body_start, body_size), "code body")
                func_idx = view.num_imported_functions() + c_idx
                view.register_entity(
                    DecodedEntityKind.FUNCTION,
                    body_start,
                    body_start + body_size,
                    func_idx,
                )
                stream.seek(body_start + body_size)

    def resolve_imports(self, module: ModuleView) -> bool:
        entries = module.resolved_import_entries
        assert not entries, "module imports have already been resolved"
        for imp in module.imports:
            module_name = module.rom_binary[
                imp.module_name_offset : imp.module_name_offset + imp.module_name_size
            ]
            field_name = module.rom_binary[
                imp.field_name_offset : imp.field_name_offset + imp.field_name_size
            ]
            target_mod = self._lookup_name_bytes(module_name)
            if target_mod is None:
                assert False, f"Dependency module '{bytes(module_name).decode('utf-8')}' not found"
            export_entry = target_mod.lookup_export_bytes(field_name)
            if export_entry is None or export_entry.kind != imp.kind:
                assert False, (
                    "Unresolved import "
                    f"'{bytes(module_name).decode('utf-8')}.{bytes(field_name).decode('utf-8')}'"
                )
            _push_or_assert(
                entries,
                (
                    fnv1a_32_name_pair(
                        module.rom_binary,
                        imp.module_name_offset,
                        imp.module_name_size,
                        memoryview(b"."),
                        imp.field_name_offset,
                        imp.field_name_size,
                    ),
                    export_entry,
                ),
                "resolved import",
            )

        entries.sort(key=lambda e: e[0])
        module.resolved_imports = ReadOnlyFlatMapStorage.from_sorted_static_entries(entries)
        module.is_ready = True
        return True

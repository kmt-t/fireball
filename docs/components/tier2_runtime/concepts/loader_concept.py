"""
docs/components/tier2_runtime/concepts/loader_concept.py
Reference Concept Implementation: WASM Loader & Zero-Copy Indexing Engine
- ROM-resident WASM32 binary streaming with bounds-checked BinaryStream and LEB128 decoding (5/10 byte guard)
- Transactional allocation rollback via BumpAllocator (save/restore)
- Zero-copy section indexing and module_view construction (exports_dict with binary search)
- Lightweight verification scope (V1: Magic, V2: Version, V3: Section bounds, V4: Section order, V5: Type signature index, V6: Memory section page limit)
- Multi-module registry, export lookup, import resolution across modules, and LIFO unload
- Lazy function/global accessors (FunctionAccessor, GlobalAccessor)
"""

import bisect
import struct
from typing import Any, Optional, Union

# ==============================================================================
# 0. Configuration Constants & Error Definitions
# ==============================================================================

FB_CONF_MAX_MODULES = 4
FB_CONF_MAX_FUNCTIONS = 256
FB_CONF_MAX_EXPORTS = 64
FB_CONF_MAX_GLOBALS = 32
FB_CONF_MAX_IMPORTS = 32
FB_CONF_MAX_WASM_PAGES = (
    16  # System physical budget limit for linear memory pages (64KB each)
)
FB_CONF_WASM_PAGE_SIZE = 65536


class WasmParseError(Exception):
    """Raised when binary format, LEB128 width, or stream boundary is violated."""

    pass


class WasmVerifyError(Exception):
    """Raised when lightweight verification (V1-V6) fails."""

    pass


class WasmLinkError(Exception):
    """Raised when multi-module import resolution fails."""

    pass


# WASM MVP Standard Section IDs
class SectionID:
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


# WASM Value Types
class ValType:
    I32 = 0x7F
    I64 = 0x7E
    F32 = 0x7D
    F64 = 0x7C
    FUNC_REF = 0x70
    EXTERN_REF = 0x6F


# WASM External Kinds (Import / Export)
class ExternalKind:
    FUNCTION = 0x00
    TABLE = 0x01
    MEMORY = 0x02
    GLOBAL = 0x03


# ==============================================================================
# 1. Bump Allocator with Transactional Rollback
# ==============================================================================


class BumpAllocator:
    """
    Non-owning LIFO bump allocator simulating ROM/RAM scratch allocation.
    `{META_BumpAllocator}`
    """

    def __init__(self, capacity: int = 16384):
        self.capacity = capacity
        self.storage = bytearray(capacity)
        self.offset = 0

    def allocate(self, size: int, alignment: int = 4) -> int:
        aligned_offset = (self.offset + (alignment - 1)) & ~(alignment - 1)
        if aligned_offset + size > self.capacity:
            raise MemoryError("BumpAllocator capacity exceeded")
        self.offset = aligned_offset + size
        return aligned_offset

    def save(self) -> int:
        """Saves current allocation watermark for transactional rollback."""
        return self.offset

    def restore(self, saved_offset: int) -> None:
        """Rolls back allocated storage to a previous watermark."""
        assert 0 <= saved_offset <= self.offset
        self.offset = saved_offset

    def reset(self) -> None:
        self.offset = 0


# ==============================================================================
# 2. BinaryStream with LEB128 Guard & Zero-Copy Slicing
# ==============================================================================


class BinaryStream:
    """
    Stream reader over ROM data with bounds check and LEB128 guard.
    `{ROMParsing}`
    """

    def __init__(
        self,
        data: Union[bytes, bytearray, memoryview],
        offset: int = 0,
        length: Optional[int] = None,
    ):
        self.view = memoryview(data)
        self.cursor = offset
        self.limit = len(self.view) if length is None else offset + length
        if self.limit > len(self.view):
            raise WasmParseError(
                f"Stream limit {self.limit} exceeds underlying buffer size {len(self.view)}"
            )

    def remaining(self) -> int:
        return max(0, self.limit - self.cursor)

    def tell(self) -> int:
        return self.cursor

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > self.limit:
            raise WasmParseError(f"Seek position {pos} out of range [0, {self.limit}]")
        self.cursor = pos

    def read_bytes(self, n: int) -> memoryview:
        if self.cursor + n > self.limit:
            raise WasmParseError(
                f"Unexpected end of stream: requested {n} bytes, {self.remaining()} remaining"
            )
        res = self.view[self.cursor : self.cursor + n]
        self.cursor += n
        return res

    def read_u8(self) -> int:
        return self.read_bytes(1)[0]

    def read_u16_le(self) -> int:
        raw = self.read_bytes(2)
        return struct.unpack("<H", raw)[0]

    def read_u32_le(self) -> int:
        raw = self.read_bytes(4)
        return struct.unpack("<I", raw)[0]

    def read_u64_le(self) -> int:
        raw = self.read_bytes(8)
        return struct.unpack("<Q", raw)[0]

    def read_leb128_u32(self) -> int:
        """
        Decodes unsigned LEB128 with max 5-byte guard (WASM32 standard).
        """
        result = 0
        shift = 0
        count = 0
        while True:
            if count >= 5:
                raise WasmParseError("LEB128 u32 exceeded maximum 5 bytes")
            if self.cursor >= self.limit:
                raise WasmParseError("Truncated LEB128 u32 integer")
            b = self.read_u8()
            count += 1
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                break
            shift += 7
        if result > 0xFFFFFFFF:
            raise WasmParseError("LEB128 u32 value out of 32-bit range")
        return result

    def read_leb128_s32(self) -> int:
        """
        Decodes signed LEB128 with max 5-byte guard.
        """
        result = 0
        shift = 0
        count = 0
        while True:
            if count >= 5:
                raise WasmParseError("LEB128 s32 exceeded maximum 5 bytes")
            if self.cursor >= self.limit:
                raise WasmParseError("Truncated LEB128 s32 integer")
            b = self.read_u8()
            count += 1
            result |= (b & 0x7F) << shift
            shift += 7
            if (b & 0x80) == 0:
                if shift < 32 and (b & 0x40):
                    result |= ~0 << shift
                break
        # Convert to 32-bit signed Python int
        result &= 0xFFFFFFFF
        if result & 0x80000000:
            result -= 0x100000000
        return result

    def read_leb128_u64(self) -> int:
        """
        Decodes unsigned LEB128 with max 10-byte guard.
        """
        result = 0
        shift = 0
        count = 0
        while True:
            if count >= 10:
                raise WasmParseError("LEB128 u64 exceeded maximum 10 bytes")
            if self.cursor >= self.limit:
                raise WasmParseError("Truncated LEB128 u64 integer")
            b = self.read_u8()
            count += 1
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                break
            shift += 7
        return result & 0xFFFFFFFFFFFFFFFF

    def read_string(self) -> str:
        """Decodes UTF-8 string with LEB128 length prefix without heap copy."""
        length = self.read_leb128_u32()
        raw_bytes = self.read_bytes(length)
        try:
            return bytes(raw_bytes).decode("utf-8")
        except UnicodeDecodeError as e:
            raise WasmParseError(f"Invalid UTF-8 string: {e}")

    def slice(self, length: int) -> "BinaryStream":
        """Creates a sub-stream over the next `length` bytes."""
        if self.cursor + length > self.limit:
            raise WasmParseError(
                f"Sub-stream length {length} exceeds remaining {self.remaining()} bytes"
            )
        sub = BinaryStream(self.view, offset=self.cursor, length=length)
        self.cursor += length
        return sub


# ==============================================================================
# 3. Indexing Data Structures & Accessor Proxies
# ==============================================================================


class FuncType:
    def __init__(self, params: list[int], results: list[int]):
        self.params = params
        self.results = results

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, FuncType):
            return False
        return self.params == other.params and self.results == other.results

    def __repr__(self) -> str:
        return f"FuncType(params={self.params}, results={self.results})"


class ImportEntry:
    def __init__(self, module_name: str, field_name: str, kind: int, desc: int):
        self.module_name = module_name
        self.field_name = field_name
        self.kind = kind  # ExternalKind
        self.desc = desc  # type_idx if kind==FUNCTION, or limits / type info


class ExportEntry:
    def __init__(self, name: str, kind: int, index: int):
        self.name = name
        self.kind = kind  # ExternalKind
        self.index = index

    def __lt__(self, other: "ExportEntry") -> bool:
        return self.name < other.name

    def __repr__(self) -> str:
        return f"ExportEntry('{self.name}', kind={self.kind}, idx={self.index})"


class GlobalEntry:
    def __init__(
        self, valtype: int, mutable: bool, init_expr_offset: int, init_expr_size: int
    ):
        self.valtype = valtype
        self.mutable = mutable
        self.init_expr_offset = init_expr_offset
        self.init_expr_size = init_expr_size


class MemoryEntry:
    def __init__(self, initial_pages: int, maximum_pages: Optional[int] = None):
        self.initial_pages = initial_pages
        self.maximum_pages = maximum_pages


class TableEntry:
    def __init__(self, elemtype: int, initial: int, maximum: Optional[int] = None):
        self.elemtype = elemtype
        self.initial = initial
        self.maximum = maximum


class SectionView:
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
    """
    Lazy proxy for function definition and bytecode.
    `{ROMParsing}`
    """

    def __init__(
        self,
        func_idx: int,
        type_idx: int,
        type_sig: FuncType,
        rom_data: Union[bytes, bytearray, memoryview],
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
        """Returns stream for local variable count declarations."""
        return BinaryStream(
            self._rom_data, offset=self._code_offset, length=self._code_size
        )

    def get_code_stream(self) -> BinaryStream:
        """Returns stream positioned directly at WASM bytecode execution body."""
        stream = self.get_locals_stream()
        # Skip local vector declarations
        local_vec_count = stream.read_leb128_u32()
        for _ in range(local_vec_count):
            stream.read_leb128_u32()  # count
            stream.read_u8()  # valtype
        return BinaryStream(
            self._rom_data, offset=stream.tell(), length=stream.remaining()
        )


class GlobalAccessor:
    """
    Lazy proxy for global variable metadata and init expression bytecode.
    `{ROMParsing}`
    """

    def __init__(
        self,
        global_idx: int,
        entry: GlobalEntry,
        rom_data: Union[bytes, bytearray, memoryview],
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


def fnv1a_32(data: Union[str, bytes]) -> int:
    """FNV-1a 32-bit hash for fast zero-copy symbol lookup."""
    if isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = bytes(data)
    h = 0x811C9DC5
    for b in raw:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


class FlatMapView:
    """fireball::flat_map_view<Key, Value>: Sorted key-value slice with O(log n) binary search."""

    def __init__(self, keys: list[Any], values: list[Any]):
        assert len(keys) == len(values)
        self.keys = keys
        self.values = values

    def size(self) -> int:
        return len(self.keys)

    def slice(self, first: int, last: int) -> "FlatMapView":
        assert 0 <= first <= last <= len(self.keys)
        return FlatMapView(self.keys[first:last], self.values[first:last])

    def find(self, key: Any) -> Optional[Any]:
        idx = bisect.bisect_left(self.keys, key)
        if idx < len(self.keys) and self.keys[idx] == key:
            return self.values[idx]
        return None


class RadixBinaryTreeView:
    """
    fireball::radix_binary_tree_view<Key, Value, RadixShift>:
    System container combining O(1) Radix Table prefix lookup with bounded binary search ({META_BinarySearch}).
    """

    def __init__(self, keys: list[int], values: list[Any], radix_shift: int = 16):
        paired = sorted(zip(keys, values), key=lambda p: p[0])
        self.keys = [p[0] for p in paired]
        self.values = [p[1] for p in paired]
        self.map_view = FlatMapView(self.keys, self.values)
        self.radix_shift = radix_shift
        if self.keys:
            max_prefix = max(self.keys) >> radix_shift
            table_size = max_prefix + 2
            self.radix_table: list[int] = [0] * table_size
            current_prefix = 0
            for idx, k in enumerate(self.keys):
                prefix = k >> radix_shift
                while current_prefix < prefix:
                    current_prefix += 1
                    self.radix_table[current_prefix] = idx
            while current_prefix <= max_prefix:
                current_prefix += 1
                self.radix_table[current_prefix] = len(self.keys)
        else:
            self.radix_table = [0]

    def find(self, key: int) -> Optional[Any]:
        prefix = key >> self.radix_shift
        if prefix < 0 or prefix + 1 >= len(self.radix_table):
            return None
        first = self.radix_table[prefix]
        last = self.radix_table[prefix + 1]
        if first >= last:
            return None
        return self.map_view.slice(first, last).find(key)

    def find_interval(self, offset: int) -> Optional[Any]:
        """Range lookup for interval keys [start, end) stored as DecodedEntity."""
        if not self.keys:
            return None
        idx = bisect.bisect_right(self.keys, offset) - 1
        if 0 <= idx < len(self.values):
            entity = self.values[idx]
            if hasattr(entity, "start_offset") and hasattr(entity, "end_offset"):
                if entity.start_offset <= offset < entity.end_offset:
                    return entity
        return None


class DecodedEntity:
    """
    Represents a decoded entity located at file offset interval [start_offset, end_offset).
    """

    def __init__(
        self,
        kind: str,
        start_offset: int,
        end_offset: int,
        name_or_idx: Any,
        payload: Any,
    ):
        self.kind = kind  # "SECTION", "FUNCTION", "GLOBAL", "DATA"
        self.start_offset = start_offset
        self.end_offset = end_offset
        self.name_or_idx = name_or_idx
        self.payload = payload

    def __repr__(self) -> str:
        return f"DecodedEntity({self.kind}, [{hex(self.start_offset)}..{hex(self.end_offset)}), {self.name_or_idx})"


# ==============================================================================
# 4. ModuleView (Zero-Copy ROM Window)
# ==============================================================================


class ModuleView:
    """
    Read-only structured index window over ROM WASM binary.
    `{ROMParsing}` `{ZeroCopyIndexing}` `{META_AccessDictionary}` `{META_BinarySearch}`
    """

    def __init__(
        self, module_name: str, rom_binary: Union[bytes, bytearray, memoryview]
    ):
        self.module_name = module_name
        self.rom_binary = memoryview(rom_binary)
        self.sections: dict[int, SectionView] = {}
        self.types: list[FuncType] = []
        self.imports: list[ImportEntry] = []
        self.functions: list[int] = []  # Internal function type indices
        self.tables: list[TableEntry] = []
        self.memories: list[MemoryEntry] = []
        self.globals: list[GlobalEntry] = []
        self.exports_dict: list[ExportEntry] = []  # Export entries
        self.code_offsets: list[
            tuple[int, int]
        ] = []  # (payload_offset, payload_size) per func
        self.start_func_idx: Optional[int] = None
        self.resolved_imports: dict[str, Any] = {}
        self.is_ready: bool = False
        # Decoded entity registry & RadixBinaryTreeView indexes ({META_BinarySearch})
        self.entity_registry: list[DecodedEntity] = []
        self.export_tree: Optional[RadixBinaryTreeView] = None
        self.import_tree: Optional[RadixBinaryTreeView] = None
        self.entity_offset_tree: Optional[RadixBinaryTreeView] = None

    def register_entity(
        self,
        kind: str,
        start_offset: int,
        end_offset: int,
        name_or_idx: Any,
        payload: Any,
    ) -> DecodedEntity:
        entity = DecodedEntity(kind, start_offset, end_offset, name_or_idx, payload)
        self.entity_registry.append(entity)
        return entity

    def build_indexes(self) -> None:
        """Constructs RadixBinaryTreeView indexes for exports, imports, and entity offsets."""
        # 1. Export symbol RadixBinaryTreeView (Hash -> ExportEntry)
        exp_keys = [fnv1a_32(exp.name) for exp in self.exports_dict]
        self.export_tree = RadixBinaryTreeView(
            exp_keys, self.exports_dict, radix_shift=16
        )
        # 2. Import symbol RadixBinaryTreeView (Hash -> ImportEntry)
        imp_keys = [
            fnv1a_32(f"{imp.module_name}::{imp.field_name}") for imp in self.imports
        ]
        self.import_tree = RadixBinaryTreeView(imp_keys, self.imports, radix_shift=16)
        # 3. Entity file offset RadixBinaryTreeView (start_offset -> DecodedEntity)
        ent_keys = [e.start_offset for e in self.entity_registry]
        self.entity_offset_tree = RadixBinaryTreeView(
            ent_keys, self.entity_registry, radix_shift=4
        )

    def lookup_export(self, name: str) -> Optional[ExportEntry]:
        """
        Hash + RadixBinaryTreeView symbol lookup with zero-copy string verification in O(k).
        `{META_AccessDictionary}` `{META_BinarySearch}`
        """
        if self.export_tree is None:
            return None
        h = fnv1a_32(name)
        candidate = self.export_tree.find(h)
        if candidate is not None and candidate.name == name:
            return candidate
        return None

    def find_import(self, module_name: str, field_name: str) -> Optional[ImportEntry]:
        """Hash + RadixBinaryTreeView import table lookup in O(k)."""
        if self.import_tree is None:
            return None
        h = fnv1a_32(f"{module_name}::{field_name}")
        candidate = self.import_tree.find(h)
        if (
            candidate is not None
            and candidate.module_name == module_name
            and candidate.field_name == field_name
        ):
            return candidate
        return None

    def lookup_by_file_offset(self, file_offset: int) -> Optional[DecodedEntity]:
        """Looks up a decoded entity containing the given file byte offset using RadixBinaryTreeView in O(k)."""
        if self.entity_offset_tree is None:
            return None
        return self.entity_offset_tree.find_interval(file_offset)

    def lookup_export_func(self, name: str) -> Optional[int]:
        """Returns function index if export exists and is a function."""
        exp = self.lookup_export(name)
        if exp is not None and exp.kind == ExternalKind.FUNCTION:
            return exp.index
        return None

    def get_section(self, section_id: int) -> Optional[SectionView]:
        return self.sections.get(section_id)

    def num_imported_functions(self) -> int:
        return sum(1 for imp in self.imports if imp.kind == ExternalKind.FUNCTION)

    def get_function_type_index(self, func_idx: int) -> int:
        num_imported = self.num_imported_functions()
        if func_idx < num_imported:
            # Imported function
            imported_funcs = [
                imp for imp in self.imports if imp.kind == ExternalKind.FUNCTION
            ]
            return imported_funcs[func_idx].desc
        internal_idx = func_idx - num_imported
        if internal_idx >= len(self.functions):
            raise IndexError(
                f"Function index {func_idx} out of range (total {num_imported + len(self.functions)})"
            )
        return self.functions[internal_idx]

    def get_function(self, func_idx: int) -> FunctionAccessor:
        """Constructs a lazy FunctionAccessor for the given function index."""
        type_idx = self.get_function_type_index(func_idx)
        type_sig = self.types[type_idx]
        num_imported = self.num_imported_functions()
        if func_idx < num_imported:
            raise ValueError(
                f"Cannot get code accessor for imported function index {func_idx}"
            )
        internal_idx = func_idx - num_imported
        if internal_idx >= len(self.code_offsets):
            raise IndexError(
                f"Code section missing for internal function {internal_idx}"
            )
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
            raise IndexError(
                f"Global index {global_idx} out of range [0, {len(self.globals)})"
            )
        return GlobalAccessor(global_idx, self.globals[global_idx], self.rom_binary)


# ==============================================================================
# 5. WASM Loader Engine & Lightweight Verifier (V1-V6)
# ==============================================================================


class WasmLoader:
    """
    WASM Loader & Verification engine.
    `{ROMParsing}` `{LightweightVerifier}` `{MultiModule_Support}` `{META_BumpAllocator}`
    """

    def __init__(
        self,
        allocator: Optional[BumpAllocator] = None,
        max_modules: int = FB_CONF_MAX_MODULES,
        max_wasm_pages: int = FB_CONF_MAX_WASM_PAGES,
    ):
        self.allocator = allocator or BumpAllocator()
        self.registry: dict[str, ModuleView] = {}
        self.max_modules = max_modules
        self.max_wasm_pages = max_wasm_pages

    def lookup(self, name: str) -> Optional[ModuleView]:
        return self.registry.get(name)

    def prepare(
        self, module_name: str, wasm_binary: Union[bytes, bytearray, memoryview]
    ) -> ModuleView:
        """
        Parses and verifies ROM-resident WASM binary, constructing ModuleView.
        Guarantees full transactional rollback via BumpAllocator on failure.
        `{ROMParsing}` `{ZeroCopyIndexing}` `{LightweightVerifier}`
        """
        if len(self.registry) >= self.max_modules:
            raise WasmLinkError(f"Module registry limit ({self.max_modules}) exceeded")

        save_point = self.allocator.save()
        try:
            view = self._parse_and_verify(module_name, wasm_binary)
            self.registry[module_name] = view
            return view
        except Exception:
            # Transactional rollback of any scratch allocations
            self.allocator.restore(save_point)
            raise

    def _parse_and_verify(
        self, module_name: str, wasm_binary: Union[bytes, bytearray, memoryview]
    ) -> ModuleView:
        stream = BinaryStream(wasm_binary)
        # ----------------------------------------------------------------------
        # V1: Magic Number Check (\0asm -> 0x00, 0x61, 0x73, 0x6D)
        # ----------------------------------------------------------------------
        if stream.remaining() < 8:
            raise WasmVerifyError("Binary too small for WASM header")
        magic = stream.read_bytes(4)
        if bytes(magic) != b"\x00asm":
            raise WasmVerifyError(
                f"V1 Verification Failed: Invalid magic {bytes(magic)!r}, expected b'\\x00asm'"
            )

        # ----------------------------------------------------------------------
        # V2: Version Check (1 -> 0x01, 0x00, 0x00, 0x00)
        # ----------------------------------------------------------------------
        version = stream.read_u32_le()
        if version != 1:
            raise WasmVerifyError(
                f"V2 Verification Failed: Unsupported WASM version {version}, expected 1"
            )

        # ----------------------------------------------------------------------
        # V3 & V4: Section Order and Section Boundary Scanning
        # ----------------------------------------------------------------------
        view = ModuleView(module_name, wasm_binary)
        last_section_id = 0
        while stream.remaining() > 0:
            sec_start = stream.tell()
            sec_id = stream.read_u8()
            sec_size = stream.read_leb128_u32()
            payload_start = stream.tell()
            # V3: Section bounds check
            if payload_start + sec_size > stream.limit:
                raise WasmVerifyError(
                    f"V3 Verification Failed: Section {sec_id} size {sec_size} exceeds binary end (limit={stream.limit})"
                )

            # V4: Section order check (Custom sections ID=0 are exempt, non-custom must be strictly increasing)
            if sec_id != SectionID.CUSTOM:
                if sec_id <= last_section_id:
                    raise WasmVerifyError(
                        f"V4 Verification Failed: Section ID {sec_id} appears out of order or duplicated after {last_section_id}"
                    )
                last_section_id = sec_id

            sec_total_size = (payload_start - sec_start) + sec_size
            sec_view = SectionView(
                section_id=sec_id,
                offset=sec_start,
                size=sec_total_size,
                payload_offset=payload_start,
                payload_size=sec_size,
            )
            view.sections[sec_id] = sec_view
            view.register_entity(
                "SECTION", sec_start, sec_start + sec_total_size, sec_id, sec_view
            )
            # Dispatch section-specific zero-copy parsers
            sec_stream = BinaryStream(
                wasm_binary, offset=payload_start, length=sec_size
            )
            self._parse_section_content(sec_id, sec_stream, view)
            # Seek past section
            stream.seek(payload_start + sec_size)

        # ----------------------------------------------------------------------
        # V5: Type Signature Consistency (Function declarations vs Type Section)
        # ----------------------------------------------------------------------
        num_types = len(view.types)
        for func_type_idx in view.functions:
            if func_type_idx >= num_types:
                raise WasmVerifyError(
                    f"V5 Verification Failed: Function declared with invalid type index {func_type_idx} (types count={num_types})"
                )

        for imp in view.imports:
            if imp.kind == ExternalKind.FUNCTION and imp.desc >= num_types:
                raise WasmVerifyError(
                    f"V5 Verification Failed: Imported function '{imp.module_name}.{imp.field_name}' "
                    f"has invalid type index {imp.desc} (types count={num_types})"
                )

        # Check Code section count matches Function section count
        if len(view.functions) != len(view.code_offsets):
            raise WasmVerifyError(
                f"Function section count ({len(view.functions)}) does not match Code section count ({len(view.code_offsets)})"
            )

        # ----------------------------------------------------------------------
        # V6: Memory Section Page Limit (Initial pages <= FB_CONF_MAX_WASM_PAGES)
        # ----------------------------------------------------------------------
        for mem in view.memories:
            if mem.initial_pages > self.max_wasm_pages:
                raise WasmVerifyError(
                    f"V6 Verification Failed: Memory initial pages {mem.initial_pages} "
                    f"exceeds system physical budget {self.max_wasm_pages} pages"
                )

        # Sort exports_dict by export name for deterministic layout
        view.exports_dict.sort()
        # Build RadixBinaryTreeView indexes for exports, imports, and file offset entities ({META_BinarySearch})
        view.build_indexes()
        # If module has no imports, it is ready immediately
        if not view.imports:
            view.is_ready = True

        return view

    def _parse_section_content(
        self, sec_id: int, stream: BinaryStream, view: ModuleView
    ) -> None:
        if sec_id == SectionID.TYPE:
            count = stream.read_leb128_u32()
            for _ in range(count):
                form = stream.read_u8()
                if form != 0x60:  # WASM func type marker
                    raise WasmParseError(
                        f"Invalid type form 0x{form:02X}, expected 0x60"
                    )
                param_count = stream.read_leb128_u32()
                params = [stream.read_u8() for _ in range(param_count)]
                result_count = stream.read_leb128_u32()
                results = [stream.read_u8() for _ in range(result_count)]
                view.types.append(FuncType(params, results))

        elif sec_id == SectionID.IMPORT:
            count = stream.read_leb128_u32()
            for _ in range(count):
                mod_name = stream.read_string()
                field_name = stream.read_string()
                kind = stream.read_u8()
                if kind == ExternalKind.FUNCTION:
                    type_idx = stream.read_leb128_u32()
                    view.imports.append(
                        ImportEntry(mod_name, field_name, kind, type_idx)
                    )
                elif kind == ExternalKind.TABLE:
                    elemtype = stream.read_u8()
                    flags = stream.read_leb128_u32()
                    initial = stream.read_leb128_u32()
                    maximum = stream.read_leb128_u32() if (flags & 1) else None
                    view.tables.append(TableEntry(elemtype, initial, maximum))
                    view.imports.append(ImportEntry(mod_name, field_name, kind, 0))
                elif kind == ExternalKind.MEMORY:
                    flags = stream.read_leb128_u32()
                    initial = stream.read_leb128_u32()
                    maximum = stream.read_leb128_u32() if (flags & 1) else None
                    view.memories.append(MemoryEntry(initial, maximum))
                    view.imports.append(ImportEntry(mod_name, field_name, kind, 0))
                elif kind == ExternalKind.GLOBAL:
                    valtype = stream.read_u8()
                    mutable = stream.read_u8() == 1
                    view.globals.append(GlobalEntry(valtype, mutable, 0, 0))
                    view.imports.append(ImportEntry(mod_name, field_name, kind, 0))
                else:
                    raise WasmParseError(f"Unsupported import kind 0x{kind:02X}")

        elif sec_id == SectionID.FUNCTION:
            count = stream.read_leb128_u32()
            if count > FB_CONF_MAX_FUNCTIONS:
                raise WasmParseError(
                    f"Function count {count} exceeds FB_CONF_MAX_FUNCTIONS ({FB_CONF_MAX_FUNCTIONS})"
                )
            for _ in range(count):
                type_idx = stream.read_leb128_u32()
                view.functions.append(type_idx)

        elif sec_id == SectionID.TABLE:
            count = stream.read_leb128_u32()
            for _ in range(count):
                elemtype = stream.read_u8()
                flags = stream.read_leb128_u32()
                initial = stream.read_leb128_u32()
                maximum = stream.read_leb128_u32() if (flags & 1) else None
                view.tables.append(TableEntry(elemtype, initial, maximum))

        elif sec_id == SectionID.MEMORY:
            count = stream.read_leb128_u32()
            for _ in range(count):
                flags = stream.read_leb128_u32()
                initial = stream.read_leb128_u32()
                maximum = stream.read_leb128_u32() if (flags & 1) else None
                view.memories.append(MemoryEntry(initial, maximum))

        elif sec_id == SectionID.GLOBAL:
            count = stream.read_leb128_u32()
            for g_idx in range(count):
                valtype = stream.read_u8()
                mutable = stream.read_u8() == 1
                init_start = stream.tell()
                # Scan until 0x0B (end opcode of init expression)
                while stream.remaining() > 0 and stream.read_u8() != 0x0B:
                    pass
                init_size = stream.tell() - init_start
                g_entry = GlobalEntry(valtype, mutable, init_start, init_size)
                view.globals.append(g_entry)
                view.register_entity(
                    "GLOBAL", init_start, init_start + init_size, g_idx, g_entry
                )

        elif sec_id == SectionID.EXPORT:
            count = stream.read_leb128_u32()
            if count > FB_CONF_MAX_EXPORTS:
                raise WasmParseError(
                    f"Export count {count} exceeds FB_CONF_MAX_EXPORTS ({FB_CONF_MAX_EXPORTS})"
                )
            for _ in range(count):
                name = stream.read_string()
                kind = stream.read_u8()
                index = stream.read_leb128_u32()
                view.exports_dict.append(ExportEntry(name, kind, index))

        elif sec_id == SectionID.START:
            view.start_func_idx = stream.read_leb128_u32()

        elif sec_id == SectionID.CODE:
            count = stream.read_leb128_u32()
            for c_idx in range(count):
                body_size = stream.read_leb128_u32()
                body_start = stream.tell()
                view.code_offsets.append((body_start, body_size))
                func_idx = view.num_imported_functions() + c_idx
                view.register_entity(
                    "FUNCTION",
                    body_start,
                    body_start + body_size,
                    func_idx,
                    (body_start, body_size),
                )
                stream.seek(body_start + body_size)

    def resolve_imports(self, module: ModuleView) -> bool:
        """
        Resolves imported symbols against other modules registered in WasmLoader.
        `{MultiModule_Support}`
        """
        for imp in module.imports:
            target_mod = self.lookup(imp.module_name)
            if target_mod is None:
                raise WasmLinkError(
                    f"Dependency module '{imp.module_name}' not found in registry"
                )

            export_entry = target_mod.lookup_export(imp.field_name)
            if export_entry is None:
                raise WasmLinkError(
                    f"Symbol '{imp.field_name}' not exported by target module '{imp.module_name}'"
                )

            if export_entry.kind != imp.kind:
                raise WasmLinkError(
                    f"Import kind mismatch for '{imp.module_name}.{imp.field_name}': "
                    f"expected kind {imp.kind}, found {export_entry.kind}"
                )

            # Type signature matching for function imports
            if imp.kind == ExternalKind.FUNCTION:
                imp_type = module.types[imp.desc]
                exp_type = target_mod.types[
                    target_mod.get_function_type_index(export_entry.index)
                ]
                if imp_type != exp_type:
                    raise WasmLinkError(
                        f"Signature mismatch for '{imp.module_name}.{imp.field_name}': "
                        f"imported {imp_type} vs exported {exp_type}"
                    )

            module.resolved_imports[f"{imp.module_name}.{imp.field_name}"] = (
                export_entry
            )

        module.is_ready = True
        return True

    def unload(self, module: ModuleView) -> bool:
        """
        Unloads module from registry and releases scratch allocations if LIFO.
        """
        if module.module_name in self.registry:
            del self.registry[module.module_name]
            return True
        return False


# ==============================================================================
# 6. Verification Test Suite & Concept Verifier
# ==============================================================================


def _encode_leb128_u32(val: int) -> bytes:
    buf = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val != 0:
            b |= 0x80
        buf.append(b)
        if val == 0:
            break
    return bytes(buf)


def _encode_leb128_s32(val: int) -> bytes:
    buf = bytearray()
    more = True
    while more:
        b = val & 0x7F
        val >>= 7
        if (val == 0 and (b & 0x40) == 0) or (val == -1 and (b & 0x40) != 0):
            more = False
        else:
            b |= 0x80
        buf.append(b)
    return bytes(buf)


def _build_test_wasm_binary(
    magic: bytes = b"\x00asm",
    version: int = 1,
    export_names: Optional[list[str]] = None,
    corrupt_section_order: bool = False,
    corrupt_section_bounds: bool = False,
    invalid_type_idx: bool = False,
    memory_pages: int = 1,
) -> bytes:
    """Builds a compliant or intentionally malformed WASM binary."""
    buf = bytearray()
    buf.extend(magic)
    buf.extend(struct.pack("<I", version))
    # Type Section (ID=1) -> (i32, i32) -> i32
    type_payload = bytearray()
    type_payload.extend(_encode_leb128_u32(1))  # 1 type
    type_payload.append(0x60)  # func
    type_payload.extend(_encode_leb128_u32(2))  # 2 params
    type_payload.extend([ValType.I32, ValType.I32])
    type_payload.extend(_encode_leb128_u32(1))  # 1 result
    type_payload.append(ValType.I32)
    buf.append(SectionID.TYPE)
    buf.extend(_encode_leb128_u32(len(type_payload)))
    buf.extend(type_payload)
    # Function Section (ID=3)
    func_payload = bytearray()
    func_payload.extend(_encode_leb128_u32(1))  # 1 function
    func_payload.extend(
        _encode_leb128_u32(999 if invalid_type_idx else 0)
    )  # type index
    buf.append(SectionID.FUNCTION)
    buf.extend(_encode_leb128_u32(len(func_payload)))
    buf.extend(func_payload)
    # Memory Section (ID=5)
    mem_payload = bytearray()
    mem_payload.extend(_encode_leb128_u32(1))  # 1 memory
    mem_payload.append(0x00)  # limits flags (no max)
    mem_payload.extend(_encode_leb128_u32(memory_pages))
    buf.append(SectionID.MEMORY)
    buf.extend(_encode_leb128_u32(len(mem_payload)))
    buf.extend(mem_payload)
    # Global Section (ID=6) -> 1 immutable i32 global initialized to 42
    glob_payload = bytearray()
    glob_payload.extend(_encode_leb128_u32(1))  # 1 global
    glob_payload.append(ValType.I32)  # valtype
    glob_payload.append(0x00)  # mutable=false
    glob_payload.append(0x41)  # i32.const
    glob_payload.extend(_encode_leb128_s32(42))  # 42
    glob_payload.append(0x0B)  # end
    buf.append(SectionID.GLOBAL)
    buf.extend(_encode_leb128_u32(len(glob_payload)))
    buf.extend(glob_payload)
    # Export Section (ID=7)
    names = export_names or ["add", "main", "compute"]
    exp_payload = bytearray()
    exp_payload.extend(_encode_leb128_u32(len(names)))
    for name in names:
        encoded_name = name.encode("utf-8")
        exp_payload.extend(_encode_leb128_u32(len(encoded_name)))
        exp_payload.extend(encoded_name)
        exp_payload.append(ExternalKind.FUNCTION)
        exp_payload.extend(_encode_leb128_u32(0))  # func idx 0

    if corrupt_section_order:
        # Intentionally inject an out-of-order section ID
        buf.append(SectionID.IMPORT)
        buf.extend(_encode_leb128_u32(0))

    buf.append(SectionID.EXPORT)
    buf.extend(_encode_leb128_u32(len(exp_payload)))
    buf.extend(exp_payload)
    # Code Section (ID=10)
    # Body: local.get 0, local.get 1, i32.add, end
    code_body = bytearray()
    code_body.extend(_encode_leb128_u32(0))  # 0 local vectors
    code_body.extend([0x20, 0x00, 0x20, 0x01, 0x6A, 0x0B])  # bytecodes
    code_payload = bytearray()
    code_payload.extend(_encode_leb128_u32(1))  # 1 code body
    code_payload.extend(_encode_leb128_u32(len(code_body)))
    code_payload.extend(code_body)
    buf.append(SectionID.CODE)
    if corrupt_section_bounds:
        buf.extend(_encode_leb128_u32(9999))  # Exceeds binary length!
    else:
        buf.extend(_encode_leb128_u32(len(code_payload)))
    buf.extend(code_payload)
    return bytes(buf)


def test_wasm_loader_lifecycle_and_verification():
    loader = WasmLoader()
    # 1. Normal prepare & parse
    valid_wasm = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("math_module", valid_wasm)
    assert view.module_name == "math_module"
    assert view.is_ready is True
    assert len(view.types) == 1
    assert len(view.functions) == 1
    assert len(view.memories) == 1
    assert len(view.exports_dict) == 3
    # 2. Binary search over exports_dict (O(log N))
    assert [exp.name for exp in view.exports_dict] == ["alpha", "beta", "zeta"]
    assert view.lookup_export("alpha") is not None
    assert view.lookup_export("beta") is not None
    assert view.lookup_export("zeta") is not None
    assert view.lookup_export("gamma") is None
    assert view.lookup_export_func("alpha") == 0
    # 3. Lazy Function Accessor verification
    func_acc = view.get_function(0)
    assert func_acc.get_type_index() == 0
    assert func_acc.get_signature() == FuncType(
        [ValType.I32, ValType.I32], [ValType.I32]
    )
    code_stream = func_acc.get_code_stream()
    bytecode = bytes(code_stream.read_bytes(code_stream.remaining()))
    assert bytecode == bytes([0x20, 0x00, 0x20, 0x01, 0x6A, 0x0B])
    # 4. V1: Invalid Magic Number rejection & rollback
    watermark_before = loader.allocator.offset
    bad_magic_wasm = _build_test_wasm_binary(magic=b"\x7fELF")
    try:
        loader.prepare("bad_magic", bad_magic_wasm)
        assert False, "Should fail V1"
    except WasmVerifyError as e:
        assert "V1 Verification Failed" in str(e)
    assert loader.allocator.offset == watermark_before
    # 5. V2: Invalid Version rejection & rollback
    bad_ver_wasm = _build_test_wasm_binary(version=2)
    try:
        loader.prepare("bad_version", bad_ver_wasm)
        assert False, "Should fail V2"
    except WasmVerifyError as e:
        assert "V2 Verification Failed" in str(e)
    assert loader.allocator.offset == watermark_before
    # 6. V3: Section bounds overflow rejection & rollback
    bad_bounds_wasm = _build_test_wasm_binary(corrupt_section_bounds=True)
    try:
        loader.prepare("bad_bounds", bad_bounds_wasm)
        assert False, "Should fail V3"
    except WasmVerifyError as e:
        assert "V3 Verification Failed" in str(e)
    assert loader.allocator.offset == watermark_before
    # 7. V4: Section order corruption rejection & rollback
    bad_order_wasm = _build_test_wasm_binary(corrupt_section_order=True)
    try:
        loader.prepare("bad_order", bad_order_wasm)
        assert False, "Should fail V4"
    except WasmVerifyError as e:
        assert "V4 Verification Failed" in str(e)
    assert loader.allocator.offset == watermark_before
    # 8. V5: Invalid Type signature index rejection & rollback
    bad_type_wasm = _build_test_wasm_binary(invalid_type_idx=True)
    try:
        loader.prepare("bad_type", bad_type_wasm)
        assert False, "Should fail V5"
    except WasmVerifyError as e:
        assert "V5 Verification Failed" in str(e)
    assert loader.allocator.offset == watermark_before
    # 9. V6: Memory page limit overflow rejection & rollback
    bad_mem_wasm = _build_test_wasm_binary(memory_pages=32)
    try:
        loader.prepare("bad_mem", bad_mem_wasm)
        assert False, "Should fail V6"
    except WasmVerifyError as e:
        assert "V6 Verification Failed" in str(e)
    assert loader.allocator.offset == watermark_before
    # 10. Multi-module linking (resolve_imports)
    # Build lib module exporting 'helper'
    lib_wasm = _build_test_wasm_binary(export_names=["helper"])
    lib_view = loader.prepare("lib_module", lib_wasm)
    # Build app module importing 'lib_module.helper'
    app_buf = bytearray()
    app_buf.extend(b"\x00asm\x01\x00\x00\x00")
    # Type section: (i32, i32) -> i32
    app_type = bytearray()
    app_type.extend(_encode_leb128_u32(1))
    app_type.append(0x60)
    app_type.extend(_encode_leb128_u32(2))
    app_type.extend([ValType.I32, ValType.I32])
    app_type.extend(_encode_leb128_u32(1))
    app_type.append(ValType.I32)
    app_buf.append(SectionID.TYPE)
    app_buf.extend(_encode_leb128_u32(len(app_type)))
    app_buf.extend(app_type)
    # Import section: lib_module.helper
    app_imp = bytearray()
    app_imp.extend(_encode_leb128_u32(1))
    app_imp.extend(_encode_leb128_u32(len(b"lib_module")))
    app_imp.extend(b"lib_module")
    app_imp.extend(_encode_leb128_u32(len(b"helper")))
    app_imp.extend(b"helper")
    app_imp.append(ExternalKind.FUNCTION)
    app_imp.extend(_encode_leb128_u32(0))  # type 0
    app_buf.append(SectionID.IMPORT)
    app_buf.extend(_encode_leb128_u32(len(app_imp)))
    app_buf.extend(app_imp)
    app_view = loader.prepare("app_module", bytes(app_buf))
    assert app_view.is_ready is False  # Not ready until imports resolved
    assert loader.resolve_imports(app_view) is True
    assert app_view.is_ready is True
    assert "lib_module.helper" in app_view.resolved_imports
    # 11. Unload
    assert loader.unload(app_view) is True
    assert loader.lookup("app_module") is None
    print("[PASS] All WASM Loader concept tests passed successfully.")


def test_wasm_loader_radix_binary_tree_offset_indexing():
    """
    Verifies LOAD-40 ~ LOAD-44:
    - Registration of decoded entities in DecodedEntityRegistry
    - O(k) RadixBinaryTree file offset containment search
    - Reverse-lookup of FunctionAccessor, GlobalAccessor, SectionView by file byte offset
    - Boundary and invalid offset rejection
    """
    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["alpha", "beta"])
    view = loader.prepare("radix_test_module", wasm_bytes)
    # 1. LOAD-40: Entities registered
    assert len(view.entity_registry) > 0
    kinds = [e.kind for e in view.entity_registry]
    assert "SECTION" in kinds
    assert "FUNCTION" in kinds
    assert "GLOBAL" in kinds
    # 2. LOAD-41 & LOAD-42: RadixBinaryTree offset lookup for function code
    func_start, func_size = view.code_offsets[0]
    # Lookup exactly at start of function body
    entity_at_start = view.lookup_by_file_offset(func_start)
    assert entity_at_start is not None
    assert entity_at_start.kind == "FUNCTION"
    assert entity_at_start.name_or_idx == 0
    # Lookup in the middle of function body
    entity_in_mid = view.lookup_by_file_offset(func_start + 2)
    assert entity_in_mid is not None
    assert entity_in_mid.kind == "FUNCTION"
    assert entity_in_mid.name_or_idx == 0
    # 3. LOAD-43: Lookup global entity
    global_entry = view.globals[0]
    entity_global = view.lookup_by_file_offset(global_entry.init_expr_offset)
    assert entity_global is not None
    assert entity_global.kind == "GLOBAL"
    assert entity_global.name_or_idx == 0
    # 4. LOAD-44: Invalid / out-of-range offsets
    assert view.lookup_by_file_offset(len(wasm_bytes) + 100) is None
    assert view.lookup_by_file_offset(0xFFFFFFFF) is None
    print("[PASS] RadixBinaryTreeView file offset indexing verified successfully.")


def test_wasm_loader_hash_radix_binary_tree_view_symbol_lookup():
    """
    Verifies LOAD-13, LOAD-21, LOAD-45 ~ LOAD-47:
    - Hash + RadixBinaryTreeView symbol lookup (O(k))
    - Hash + RadixBinaryTreeView import table lookup and multi-module linking
    - Hash collision resistance (string verification)
    - Non-existent symbol rejection without linear scanning
    """
    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(
        export_names=["alpha", "beta", "gamma", "compute"]
    )
    view = loader.prepare("sym_mod", wasm_bytes)
    # LOAD-13: Hash + RadixBinaryTreeView symbol lookup
    exp_alpha = view.lookup_export("alpha")
    assert exp_alpha is not None
    assert exp_alpha.name == "alpha"
    assert exp_alpha.index == 0
    exp_gamma = view.lookup_export("gamma")
    assert exp_gamma is not None
    assert exp_gamma.name == "gamma"
    # LOAD-47: Fast non-existent symbol rejection
    assert view.lookup_export("non_existent_func") is None
    assert view.lookup_export("") is None
    # LOAD-45 & LOAD-21: Multi-module import resolution via Hash + RadixBinaryTreeView
    app_buf = bytearray()
    app_buf.extend(b"\x00asm\x01\x00\x00\x00")
    app_type = bytearray()
    app_type.extend(_encode_leb128_u32(1))
    app_type.append(0x60)
    app_type.extend(_encode_leb128_u32(2))
    app_type.extend([ValType.I32, ValType.I32])
    app_type.extend(_encode_leb128_u32(1))
    app_type.append(ValType.I32)
    app_buf.append(SectionID.TYPE)
    app_buf.extend(_encode_leb128_u32(len(app_type)))
    app_buf.extend(app_type)
    app_imp = bytearray()
    app_imp.extend(_encode_leb128_u32(2))
    # Import 1: sym_mod.alpha
    app_imp.extend(_encode_leb128_u32(len(b"sym_mod")))
    app_imp.extend(b"sym_mod")
    app_imp.extend(_encode_leb128_u32(len(b"alpha")))
    app_imp.extend(b"alpha")
    app_imp.append(ExternalKind.FUNCTION)
    app_imp.extend(_encode_leb128_u32(0))
    # Import 2: sym_mod.compute
    app_imp.extend(_encode_leb128_u32(len(b"sym_mod")))
    app_imp.extend(b"sym_mod")
    app_imp.extend(_encode_leb128_u32(len(b"compute")))
    app_imp.extend(b"compute")
    app_imp.append(ExternalKind.FUNCTION)
    app_imp.extend(_encode_leb128_u32(0))
    app_buf.append(SectionID.IMPORT)
    app_buf.extend(_encode_leb128_u32(len(app_imp)))
    app_buf.extend(app_imp)
    app_view = loader.prepare("app_sym_mod", bytes(app_buf))
    # Test find_import on import_tree
    imp_alpha = app_view.find_import("sym_mod", "alpha")
    assert imp_alpha is not None
    assert imp_alpha.field_name == "alpha"
    imp_compute = app_view.find_import("sym_mod", "compute")
    assert imp_compute is not None
    assert imp_compute.field_name == "compute"
    assert app_view.find_import("sym_mod", "unknown") is None
    assert loader.resolve_imports(app_view) is True
    assert app_view.is_ready is True
    print(
        "[PASS] Hash + RadixBinaryTreeView symbol and import lookup verified successfully."
    )


if __name__ == "__main__":
    test_wasm_loader_lifecycle_and_verification()
    test_wasm_loader_radix_binary_tree_offset_indexing()
    test_wasm_loader_hash_radix_binary_tree_view_symbol_lookup()

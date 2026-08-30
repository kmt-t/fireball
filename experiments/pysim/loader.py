"""
experiments/pysim/loader.py

WASM Loader & Zero-Copy Indexing Engine with Hash + RadixBinaryTreeView Indexes.
Conforms strictly to docs/components/tier2_runtime/runtime_loader.md
and docs/components/tier1_core/system_containers.md.

Implements:
1. Zero-Copy ROM-resident WASM32 parsing ({ROMParsing}, {ZeroCopyIndexing})
2. Transactional memory rollback via BumpAllocator ({META_BumpAllocator})
3. RadixBinaryTreeView interval indexing for file offset reverse-lookup ({META_BinarySearch})
4. Hash + RadixBinaryTreeView symbol and import lookup in O(k) ({META_AccessDictionary}, {META_BinarySearch})
5. Lightweight Verification Scope (V1-V6) ({LightweightVerifier})
6. Multi-module registry & import resolution ({MultiModule_Support})
"""

from __future__ import annotations

import bisect
import struct
from typing import Any, Optional, Sequence, Union


# Configuration Constants
FB_CONF_MAX_MODULES = 4
FB_CONF_MAX_FUNCTIONS = 256
FB_CONF_MAX_EXPORTS = 64
FB_CONF_MAX_GLOBALS = 32
FB_CONF_MAX_IMPORTS = 32
FB_CONF_MAX_WASM_PAGES = 16
FB_CONF_WASM_PAGE_SIZE = 65536


class WasmParseError(Exception):
    pass


class WasmVerifyError(Exception):
    pass


class WasmLinkError(Exception):
    pass


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


class ValType:
    I32 = 0x7F
    I64 = 0x7E
    F32 = 0x7D
    F64 = 0x7C
    FUNC_REF = 0x70
    EXTERN_REF = 0x6F


class ExternalKind:
    FUNCTION = 0x00
    TABLE = 0x01
    MEMORY = 0x02
    GLOBAL = 0x03


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
    def __init__(self, keys: Sequence[Any], values: Sequence[Any]):
        assert len(keys) == len(values)
        self.keys = list(keys)
        self.values = list(values)

    def size(self) -> int:
        return len(self.keys)

    def slice(self, first: int, last: int) -> FlatMapView:
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
    def __init__(self, keys: Sequence[int], values: Sequence[Any], radix_shift: int = 16):
        paired = sorted(zip(keys, values), key=lambda p: p[0])
        self.keys = [p[0] for p in paired]
        self.values = [p[1] for p in paired]
        self.map_view = FlatMapView(self.keys, self.values)
        self.radix_shift = radix_shift

        if self.keys:
            max_prefix = max(self.keys) >> radix_shift
            table_size = max_prefix + 1
            self.radix_table: list[tuple[int, int]] = [(0, 0)] * table_size
            current_prefix = 0
            first_idx = 0
            for idx, k in enumerate(self.keys):
                prefix = k >> radix_shift
                while current_prefix < prefix:
                    self.radix_table[current_prefix] = (first_idx, idx)
                    current_prefix += 1
                    first_idx = idx
            self.radix_table[current_prefix] = (first_idx, len(self.keys))
        else:
            self.radix_table = []

    def find(self, key: int) -> Optional[Any]:
        prefix = key >> self.radix_shift
        if prefix < 0 or prefix >= len(self.radix_table):
            return None
        first, last = self.radix_table[prefix]
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


class BumpAllocator:
    """Non-owning LIFO bump allocator simulating scratch allocation ({META_BumpAllocator})."""
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
        return self.offset

    def restore(self, saved_offset: int) -> None:
        assert 0 <= saved_offset <= self.offset
        self.offset = saved_offset

    def reset(self) -> None:
        self.offset = 0


class BinaryStream:
    """Stream reader over ROM data with bounds check and LEB128 guard ({ROMParsing})."""
    def __init__(self, data: Union[bytes, bytearray, memoryview], offset: int = 0, length: Optional[int] = None):
        self.view = memoryview(data)
        self.cursor = offset
        self.limit = len(self.view) if length is None else offset + length
        if self.limit > len(self.view):
            raise WasmParseError(f"Stream limit {self.limit} exceeds underlying buffer size {len(self.view)}")

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
            raise WasmParseError(f"Unexpected end of stream: requested {n} bytes, {self.remaining()} remaining")
        res = self.view[self.cursor:self.cursor + n]
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
                    result |= (~0 << shift)
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
            raise WasmParseError(f"Invalid UTF-8 string: {e}")


class FuncType:
    def __init__(self, params: list[int], results: list[int]):
        self.params = params
        self.results = results

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, FuncType):
            return False
        return self.params == other.params and self.results == other.results


class ImportEntry:
    def __init__(self, module_name: str, field_name: str, kind: int, desc: int):
        self.module_name = module_name
        self.field_name = field_name
        self.kind = kind
        self.desc = desc


class ExportEntry:
    def __init__(self, name: str, kind: int, index: int):
        self.name = name
        self.kind = kind
        self.index = index

    def __lt__(self, other: ExportEntry) -> bool:
        return self.name < other.name


class GlobalEntry:
    def __init__(self, valtype: int, mutable: bool, init_expr_offset: int, init_expr_size: int):
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
    def __init__(self, section_id: int, offset: int, size: int, payload_offset: int, payload_size: int):
        self.section_id = section_id
        self.offset = offset
        self.size = size
        self.payload_offset = payload_offset
        self.payload_size = payload_size


class FunctionAccessor:
    def __init__(self, func_idx: int, type_idx: int, type_sig: FuncType,
                 rom_data: Union[bytes, bytearray, memoryview], code_offset: int, code_size: int):
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
    def __init__(self, global_idx: int, entry: GlobalEntry, rom_data: Union[bytes, bytearray, memoryview]):
        self.global_idx = global_idx
        self.entry = entry
        self._rom_data = rom_data

    def get_metadata(self) -> tuple[int, bool]:
        return (self.entry.valtype, self.entry.mutable)

    def get_init_expr_stream(self) -> BinaryStream:
        return BinaryStream(self._rom_data, offset=self.entry.init_expr_offset, length=self.entry.init_expr_size)


class DecodedEntity:
    """Decoded entity residing at file offset interval [start_offset, end_offset)."""
    def __init__(self, kind: str, start_offset: int, end_offset: int, name_or_idx: Any, payload: Any):
        self.kind = kind  # "SECTION", "FUNCTION", "GLOBAL", "DATA"
        self.start_offset = start_offset
        self.end_offset = end_offset
        self.name_or_idx = name_or_idx
        self.payload = payload


class ModuleView:
    """
    Read-only structured index window over ROM WASM binary.
    `{ROMParsing}` `{ZeroCopyIndexing}` `{META_AccessDictionary}` `{META_BinarySearch}`
    """
    def __init__(self, module_name: str, rom_binary: Union[bytes, bytearray, memoryview]):
        self.module_name = module_name
        self.rom_binary = memoryview(rom_binary)
        self.sections: dict[int, SectionView] = {}
        self.types: list[FuncType] = []
        self.imports: list[ImportEntry] = []
        self.functions: list[int] = []
        self.tables: list[TableEntry] = []
        self.memories: list[MemoryEntry] = []
        self.globals: list[GlobalEntry] = []
        self.exports_dict: list[ExportEntry] = []
        self.code_offsets: list[tuple[int, int]] = []
        self.start_func_idx: Optional[int] = None
        self.resolved_imports: dict[str, Any] = {}
        self.is_ready: bool = False

        # Decoded entity registry & RadixBinaryTreeView indexes ({META_BinarySearch})
        self.entity_registry: list[DecodedEntity] = []
        self.export_tree: Optional[RadixBinaryTreeView] = None
        self.import_tree: Optional[RadixBinaryTreeView] = None
        self.entity_offset_tree: Optional[RadixBinaryTreeView] = None

    def register_entity(self, kind: str, start_offset: int, end_offset: int, name_or_idx: Any, payload: Any) -> DecodedEntity:
        entity = DecodedEntity(kind, start_offset, end_offset, name_or_idx, payload)
        self.entity_registry.append(entity)
        return entity

    def build_indexes(self) -> None:
        """Constructs RadixBinaryTreeView indexes for exports, imports, and entity offsets."""
        exp_keys = [fnv1a_32(exp.name) for exp in self.exports_dict]
        self.export_tree = RadixBinaryTreeView(exp_keys, self.exports_dict, radix_shift=16)

        imp_keys = [fnv1a_32(f"{imp.module_name}::{imp.field_name}") for imp in self.imports]
        self.import_tree = RadixBinaryTreeView(imp_keys, self.imports, radix_shift=16)

        ent_keys = [e.start_offset for e in self.entity_registry]
        self.entity_offset_tree = RadixBinaryTreeView(ent_keys, self.entity_registry, radix_shift=4)

    def lookup_export(self, name: str) -> Optional[ExportEntry]:
        """Hash + RadixBinaryTreeView symbol lookup with zero-copy string verification in O(k)."""
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
        if candidate is not None and candidate.module_name == module_name and candidate.field_name == field_name:
            return candidate
        return None

    def lookup_export_func(self, name: str) -> Optional[int]:
        exp = self.lookup_export(name)
        if exp is not None and exp.kind == ExternalKind.FUNCTION:
            return exp.index
        return None

    def lookup_by_file_offset(self, file_offset: int) -> Optional[DecodedEntity]:
        """Looks up a decoded entity containing the given file byte offset using RadixBinaryTreeView in O(k)."""
        if self.entity_offset_tree is None:
            return None
        return self.entity_offset_tree.find_interval(file_offset)

    def num_imported_functions(self) -> int:
        return sum(1 for imp in self.imports if imp.kind == ExternalKind.FUNCTION)

    def get_function(self, func_idx: int) -> FunctionAccessor:
        num_imported = self.num_imported_functions()
        if func_idx < num_imported:
            raise ValueError(f"Cannot get code accessor for imported function index {func_idx}")
        internal_idx = func_idx - num_imported
        if internal_idx >= len(self.functions):
            raise IndexError(f"Function index {func_idx} out of range")
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
            raise IndexError(f"Global index {global_idx} out of range")
        return GlobalAccessor(global_idx, self.globals[global_idx], self.rom_binary)


class WasmLoader:
    """
    WASM Loader & Lightweight Verifier (V1-V6) with transactional rollback.
    `{ROMParsing}` `{LightweightVerifier}` `{MultiModule_Support}` `{META_BumpAllocator}`
    """
    def __init__(self, allocator: Optional[BumpAllocator] = None,
                 max_modules: int = FB_CONF_MAX_MODULES,
                 max_wasm_pages: int = FB_CONF_MAX_WASM_PAGES):
        self.allocator = allocator or BumpAllocator()
        self.registry: dict[str, ModuleView] = {}
        self.max_modules = max_modules
        self.max_wasm_pages = max_wasm_pages

    def lookup(self, name: str) -> Optional[ModuleView]:
        return self.registry.get(name)

    def prepare(self, module_name: str, wasm_binary: Union[bytes, bytearray, memoryview]) -> ModuleView:
        if len(self.registry) >= self.max_modules:
            raise WasmLinkError(f"Module registry capacity ({self.max_modules}) exceeded")

        watermark = self.allocator.save()
        try:
            view = ModuleView(module_name, wasm_binary)
            stream = BinaryStream(wasm_binary)

            # V1: Magic Number Check
            magic = bytes(stream.read_bytes(4))
            if magic != b"\x00asm":
                raise WasmVerifyError(f"V1 Verification Failed: Invalid magic number {magic!r}, expected b'\\x00asm'")

            # V2: Version Check
            ver_raw = stream.read_bytes(4)
            version = struct.unpack("<I", ver_raw)[0]
            if version != 1:
                raise WasmVerifyError(f"V2 Verification Failed: Unsupported WASM version {version}, expected 1")

            last_section_id = -1
            while stream.remaining() > 0:
                sec_start = stream.tell()
                sec_id = stream.read_u8()
                sec_size = stream.read_leb128_u32()
                payload_start = stream.tell()

                # V3: Section bounds check
                if payload_start + sec_size > stream.limit:
                    raise WasmVerifyError(f"V3 Verification Failed: Section {sec_id} size {sec_size} exceeds binary end")

                # V4: Section order check
                if sec_id != SectionID.CUSTOM:
                    if sec_id <= last_section_id:
                        raise WasmVerifyError(f"V4 Verification Failed: Section ID {sec_id} appears out of order after {last_section_id}")
                    last_section_id = sec_id

                sec_total_size = (payload_start - sec_start) + sec_size
                sec_view = SectionView(sec_id, sec_start, sec_total_size, payload_start, sec_size)
                view.sections[sec_id] = sec_view
                view.register_entity("SECTION", sec_start, sec_start + sec_total_size, sec_id, sec_view)

                sec_stream = BinaryStream(wasm_binary, offset=payload_start, length=sec_size)
                self._parse_section_content(sec_id, sec_stream, view)

                stream.seek(payload_start + sec_size)

            # V5: Type signature consistency
            num_types = len(view.types)
            for ftype_idx in view.functions:
                if ftype_idx >= num_types:
                    raise WasmVerifyError(f"V5 Verification Failed: Function with invalid type index {ftype_idx}")

            for imp in view.imports:
                if imp.kind == ExternalKind.FUNCTION and imp.desc >= num_types:
                    raise WasmVerifyError(f"V5 Verification Failed: Import with invalid type index {imp.desc}")

            if len(view.functions) != len(view.code_offsets):
                raise WasmVerifyError(f"Function count ({len(view.functions)}) != Code count ({len(view.code_offsets)})")

            # V6: Memory page budget
            for mem in view.memories:
                if mem.initial_pages > self.max_wasm_pages:
                    raise WasmVerifyError(f"V6 Verification Failed: Memory pages {mem.initial_pages} > budget {self.max_wasm_pages}")

            view.exports_dict.sort()
            view.build_indexes()

            if not view.imports:
                view.is_ready = True

            self.registry[module_name] = view
            return view

        except Exception:
            self.allocator.restore(watermark)
            raise

    def _parse_section_content(self, sec_id: int, stream: BinaryStream, view: ModuleView) -> None:
        if sec_id == SectionID.TYPE:
            count = stream.read_leb128_u32()
            for _ in range(count):
                form = stream.read_u8()
                if form != 0x60:
                    raise WasmParseError(f"Invalid type form 0x{form:02X}")
                p_count = stream.read_leb128_u32()
                params = [stream.read_u8() for _ in range(p_count)]
                r_count = stream.read_leb128_u32()
                results = [stream.read_u8() for _ in range(r_count)]
                view.types.append(FuncType(params, results))

        elif sec_id == SectionID.IMPORT:
            count = stream.read_leb128_u32()
            for _ in range(count):
                mod_name = stream.read_string()
                field_name = stream.read_string()
                kind = stream.read_u8()
                if kind == ExternalKind.FUNCTION:
                    type_idx = stream.read_leb128_u32()
                    view.imports.append(ImportEntry(mod_name, field_name, kind, type_idx))
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
                    mutable = (stream.read_u8() == 1)
                    view.globals.append(GlobalEntry(valtype, mutable, 0, 0))
                    view.imports.append(ImportEntry(mod_name, field_name, kind, 0))

        elif sec_id == SectionID.FUNCTION:
            count = stream.read_leb128_u32()
            if count > FB_CONF_MAX_FUNCTIONS:
                raise WasmParseError("Function count exceeds FB_CONF_MAX_FUNCTIONS")
            for _ in range(count):
                view.functions.append(stream.read_leb128_u32())

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
                mutable = (stream.read_u8() == 1)
                init_start = stream.tell()
                while stream.remaining() > 0 and stream.read_u8() != 0x0B:
                    pass
                init_size = stream.tell() - init_start
                g_entry = GlobalEntry(valtype, mutable, init_start, init_size)
                view.globals.append(g_entry)
                view.register_entity("GLOBAL", init_start, init_start + init_size, g_idx, g_entry)

        elif sec_id == SectionID.EXPORT:
            count = stream.read_leb128_u32()
            if count > FB_CONF_MAX_EXPORTS:
                raise WasmParseError("Export count exceeds FB_CONF_MAX_EXPORTS")
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
                view.register_entity("FUNCTION", body_start, body_start + body_size, func_idx, (body_start, body_size))
                stream.seek(body_start + body_size)

    def resolve_imports(self, module: ModuleView) -> bool:
        for imp in module.imports:
            target_mod = self.lookup(imp.module_name)
            if target_mod is None:
                raise WasmLinkError(f"Dependency module '{imp.module_name}' not found")
            export_entry = target_mod.lookup_export(imp.field_name)
            if export_entry is None or export_entry.kind != imp.kind:
                raise WasmLinkError(f"Unresolved import '{imp.module_name}.{imp.field_name}'")
            module.resolved_imports[f"{imp.module_name}.{imp.field_name}"] = export_entry
        module.is_ready = True
        return True

    def unload(self, module: ModuleView) -> bool:
        if module.module_name in self.registry:
            del self.registry[module.module_name]
            return True
        return False

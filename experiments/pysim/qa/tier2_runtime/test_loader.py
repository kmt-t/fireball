from __future__ import annotations

from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent


"""
experiments/pysim/qa/tier2_runtime/test_loader.py
Tests for WASM Loader, Zero-Copy Indexing, and Hash + ReadOnlyRadixBinaryTreeView Symbol/Import/Offset Indexes.
Case IDs identify the contracts asserted; unimplemented ranges remain explicit in the test specification.
"""

import pytest
from config import FB_CONF_MAX_WASM_PAGES
from helpers import (
    _build_test_wasm_binary,
    _encode_leb128_u32,
    expect_assertion,
    wat_to_wasm,
)
from loader import (
    FB_CONF_MAX_FUNCTIONS,
    FB_CONF_MAX_MODULES,
    BumpAllocator,
    DecodedEntityKind,
    ExternalKind,
    SectionID,
    ValType,
    WasmLoader,
    fnv1a_32,
)


def _assert_prepare_rejected_without_mutation(binary: bytes) -> None:
    allocator = BumpAllocator()
    loader = WasmLoader(allocator)
    kept = loader.prepare("kept", _build_test_wasm_binary())
    watermark = allocator.offset
    for _ in range(2):
        with expect_assertion("allocator rollback"):
            loader.prepare("rejected", binary)
        assert allocator.offset == watermark
        assert loader.lookup("rejected") is None
        assert loader.lookup("kept") is kept
        assert kept.is_ready


def test_load_01_invalid_magic_rejects_and_rolls_back() -> None:
    """TEST-LOAD-01, TEST-LOAD-07: A non-WASM magic cannot enter the registry or consume the arena."""
    _assert_prepare_rejected_without_mutation(_build_test_wasm_binary(magic=b"\x7fELF"))


def test_load_02_unsupported_version_rejects_and_rolls_back() -> None:
    """TEST-LOAD-02, TEST-LOAD-07: A version other than 1 cannot enter the registry."""
    _assert_prepare_rejected_without_mutation(_build_test_wasm_binary(version=2))


def test_load_03_section_past_binary_end_rejects_and_rolls_back() -> None:
    """TEST-LOAD-03, TEST-LOAD-07: A code section longer than the binary is rejected."""
    _assert_prepare_rejected_without_mutation(_build_test_wasm_binary(corrupt_section_bounds=True))


@pytest.mark.parametrize("duplicate", (False, True))
def test_load_04_decreasing_or_duplicate_section_rejects_and_rolls_back(duplicate: bool) -> None:
    """TEST-LOAD-04, TEST-LOAD-07: Otherwise valid TYPE payloads violate ordering by decrease or duplication."""
    binary = _build_test_wasm_binary()
    # The fixture's complete TYPE section starts after the eight-byte WASM header.
    type_section = b"\x01\x07\x01\x60\x02\x7f\x7f\x01\x7f"
    assert binary[8:17] == type_section
    malformed = binary[:17] + binary[8:] if duplicate else binary + type_section
    _assert_prepare_rejected_without_mutation(malformed)


def test_load_04_custom_sections_are_exempt_from_type_ordering() -> None:
    """TEST-LOAD-04: CUSTOM after CODE is accepted; duplicate CUSTOM sections remain legal."""
    loader = WasmLoader(BumpAllocator())
    binary = _build_test_wasm_binary() + b"\x00\x02\x01x\x00\x02\x01y"
    view = loader.prepare("custom", binary)
    assert view.is_ready
    assert view.lookup_export_func("add") == 0


def test_load_05_invalid_type_index_rejects_and_rolls_back() -> None:
    """TEST-LOAD-05, TEST-LOAD-07: A function's type must reference the declared TYPE vector."""
    _assert_prepare_rejected_without_mutation(_build_test_wasm_binary(invalid_type_idx=True))


def test_load_06_memory_page_limit_rejects_and_rolls_back() -> None:
    """TEST-LOAD-06, TEST-LOAD-07: Initial memory one page above the configured limit is rejected."""
    _assert_prepare_rejected_without_mutation(
        _build_test_wasm_binary(memory_pages=FB_CONF_MAX_WASM_PAGES + 1)
    )


def test_load_07_arena_exhaustion_rolls_back_partial_metadata_allocations() -> None:
    """TEST-LOAD-07 / GOTCHA-LOAD-02: Retrying a failed allocation cannot leak arena capacity."""
    allocator = BumpAllocator(capacity=2048)
    allocator.allocate(1748, alignment=1)
    watermark = allocator.offset
    loader = WasmLoader(allocator)
    for _ in range(3):
        with expect_assertion("allocator rollback"):
            loader.prepare("overflow", _build_test_wasm_binary())
        assert allocator.offset == watermark
        assert loader.lookup("overflow") is None


def test_load_10_to_15_zero_copy_and_accessors():
    """TEST-LOAD-10, TEST-LOAD-11, TEST-LOAD-12, TEST-LOAD-13, TEST-LOAD-14, TEST-LOAD-15: Verifies ROM direct references, Hash + ReadOnlyRadixBinaryTreeView export lookup, and lazy accessors."""
    loader = WasmLoader(BumpAllocator())
    wasm_bytes = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("zc_mod", wasm_bytes)
    # TEST-LOAD-10, TEST-LOAD-11: The original ROM bytes own section/code/name backing.
    assert view.rom_binary.obj is wasm_bytes
    section = view.sections[SectionID.CODE]
    assert section is not None
    assert (
        bytes(
            view.rom_binary[section.payload_offset : section.payload_offset + section.payload_size]
        )
        == b"\x01\x07\x00\x20\x00\x20\x01\x6a\x0b"
    )
    for entry in view.exports_dict:
        assert bytes(
            view.rom_binary[entry.name_offset : entry.name_offset + entry.name_size]
        ) == view.export_name(entry).encode("utf-8")
    # Exports sorted
    exp_names = [view.export_name(entry) for entry in view.exports_dict]
    assert exp_names == ["alpha", "beta", "zeta"]
    # Hash + ReadOnlyRadixBinaryTreeView lookup (TEST-LOAD-13)
    assert view.lookup_export_func("alpha") == 0
    assert view.lookup_export_func("beta") == 0
    assert view.lookup_export_func("zeta") == 0
    assert view.lookup_export_func("nonexistent") is None
    # Function Accessor
    func_acc = view.get_function(0)
    assert func_acc.get_type_index() == 0
    signature = func_acc.get_signature()
    assert tuple(signature.params) == (ValType.I32, ValType.I32)
    assert tuple(signature.results) == (ValType.I32,)
    code_stream = func_acc.get_code_stream()
    bytecode = bytes(code_stream.read_bytes(code_stream.remaining()))
    assert bytecode == bytes([0x20, 0x00, 0x20, 0x01, 0x6A, 0x0B])
    # Global Accessor
    glob_acc = view.get_global(0)
    valtype, mutable = glob_acc.get_metadata()
    assert valtype == ValType.I32
    assert mutable is False


def _function_import_binary(
    field: str = "helper", signature: str = "(param i32 i32) (result i32)"
) -> bytes:
    return wat_to_wasm(f'(module (import "lib_mod" "{field}" (func {signature})))')


def test_load_20_unresolved_dependency_keeps_module_unready() -> None:
    """TEST-LOAD-20: Preparing an import cannot mark the module executable before linking."""
    loader = WasmLoader(BumpAllocator())
    view = loader.prepare("app_mod", _function_import_binary())
    assert loader.lookup("app_mod") is view
    assert not view.is_ready
    assert len(view.resolved_import_entries) == 0
    with expect_assertion("Dependency module"):
        loader.resolve_imports(view)
    assert not view.is_ready
    assert len(view.resolved_import_entries) == 0


def test_load_21_matching_signatures_link_to_the_exact_export() -> None:
    """TEST-LOAD-21: Matching signatures link across different module-local type indexes."""
    loader = WasmLoader(BumpAllocator())
    library = loader.prepare(
        "lib_mod",
        wat_to_wasm(
            '(module (type (func)) (func (export "helper") (param i32 i32) (result i32) local.get 0 local.get 1 i32.add))'
        ),
    )
    view = loader.prepare("app_mod", _function_import_binary())
    assert not view.is_ready
    assert library.functions[0] != view.imports[0].desc
    assert loader.resolve_imports(view)
    assert view.is_ready
    resolved = view.resolved_imports.view().find(fnv1a_32("lib_mod.helper"))
    assert resolved is library.lookup_export("helper")
    assert resolved.kind == ExternalKind.FUNCTION
    assert resolved.index == 0
    assert len(view.resolved_import_entries) == 1


def test_load_22_missing_symbol_rejects_without_partial_resolution() -> None:
    """TEST-LOAD-22: A missing second symbol leaves every import unresolved and permits retry."""
    loader = WasmLoader(BumpAllocator())
    loader.prepare("lib_mod", _build_test_wasm_binary(export_names=["helper"]))
    view = loader.prepare(
        "app_mod",
        wat_to_wasm(
            '(module (import "lib_mod" "helper" (func (param i32 i32) (result i32))) (import "lib_mod" "missing" (func)))'
        ),
    )
    with expect_assertion("Unresolved import"):
        loader.resolve_imports(view)
    assert not view.is_ready
    assert len(view.resolved_import_entries) == 0
    assert len(view.resolved_imports.view()) == 0
    assert loader.lookup("app_mod") is view
    with expect_assertion("Unresolved import"):
        loader.resolve_imports(view)


@pytest.mark.parametrize(
    "signature",
    (
        "(param i64 i32) (result i32)",
        "(param i32) (result i32)",
        "(param i32 i32) (result i64)",
        "(param i32 i32)",
    ),
)
def test_load_23_function_signature_mismatch_is_rejected(signature: str) -> None:
    """TEST-LOAD-23: Same symbol/kind cannot hide parameter/result type or arity mismatches."""
    loader = WasmLoader(BumpAllocator())
    loader.prepare("lib_mod", _build_test_wasm_binary(export_names=["helper"]))
    view = loader.prepare("app_mod", _function_import_binary(signature=signature))
    with expect_assertion("signature"):
        loader.resolve_imports(view)
    assert not view.is_ready
    assert len(view.resolved_import_entries) == 0
    assert len(view.resolved_imports.view()) == 0
    assert loader.lookup("app_mod") is view


def test_load_23_late_type_mismatch_preserves_all_unresolved_imports() -> None:
    """TEST-LOAD-23: Failure after a matching import never commits a partial link table."""
    loader = WasmLoader(BumpAllocator())
    loader.prepare("lib_mod", _build_test_wasm_binary(export_names=["helper", "other"]))
    view = loader.prepare(
        "app_mod",
        wat_to_wasm(
            '(module (import "lib_mod" "helper" (func (param i32 i32) (result i32))) (import "lib_mod" "other" (func (param i64) (result i64))))'
        ),
    )
    with expect_assertion("signature"):
        loader.resolve_imports(view)
    assert not view.is_ready
    assert len(view.resolved_import_entries) == 0
    assert len(view.resolved_imports.view()) == 0


def test_load_24_module_capacity_rejects_without_registry_or_allocator_mutation() -> None:
    """TEST-LOAD-24: The configured registry limit preserves every accepted module on rejection."""
    allocator = BumpAllocator()
    loader = WasmLoader(allocator)
    binary = _build_test_wasm_binary()
    accepted = [loader.prepare(f"module_{index}", binary) for index in range(FB_CONF_MAX_MODULES)]
    watermark = allocator.offset
    with expect_assertion("Module registry capacity"):
        loader.prepare("overflow", binary)
    assert allocator.offset == watermark
    assert loader.lookup("overflow") is None
    for index, view in enumerate(accepted):
        assert loader.lookup(f"module_{index}") is view
        assert view.is_ready


def test_load_40_to_45_and_47_radix_binary_tree_view_indexes():
    """TEST-LOAD-40, TEST-LOAD-41, TEST-LOAD-42, TEST-LOAD-43, TEST-LOAD-44, TEST-LOAD-45, TEST-LOAD-47: Verify entity kinds, containing ranges and exact import names."""
    loader = WasmLoader(BumpAllocator())
    wasm_bytes = _build_test_wasm_binary(export_names=["alpha", "beta", "gamma", "compute"])
    view = loader.prepare("radix_mod", wasm_bytes)
    # 1. TEST-LOAD-40: Entities registered in DecodedEntityRegistry
    assert len(view.entity_registry) > 0
    kinds = [e.kind for e in view.entity_registry]
    assert DecodedEntityKind.SECTION in kinds
    assert DecodedEntityKind.FUNCTION in kinds
    assert DecodedEntityKind.GLOBAL in kinds
    # 2. TEST-LOAD-41 & 42: Function body reverse lookup
    body = b"\x00\x20\x00\x20\x01\x6a\x0b"
    func_start = wasm_bytes.index(body)
    func_size = len(body)
    assert view.code_offsets[0] == (func_start, func_size)
    entity_start = view.lookup_by_file_offset(func_start)
    assert entity_start is not None
    assert entity_start.kind == DecodedEntityKind.FUNCTION
    assert entity_start.index == 0
    assert (entity_start.start_offset, entity_start.end_offset) == (
        func_start,
        func_start + func_size,
    )
    entity_mid = view.lookup_by_file_offset(func_start + 2)
    assert entity_mid is not None
    assert entity_mid.kind == DecodedEntityKind.FUNCTION
    # 3. TEST-LOAD-43: Global entry reverse lookup
    global_start = wasm_bytes.index(b"\x41\x2a\x0b")
    assert view.globals[0].init_expr_offset == global_start
    entity_glob = view.lookup_by_file_offset(global_start)
    assert entity_glob is not None
    assert entity_glob.kind == DecodedEntityKind.GLOBAL
    assert entity_glob.index == 0
    assert (entity_glob.start_offset, entity_glob.end_offset) == (global_start, global_start + 3)
    # 4. TEST-LOAD-44: Invalid / out-of-bounds offsets
    for offset in range(8):
        assert view.lookup_by_file_offset(offset) is None
    assert view.lookup_by_file_offset(len(wasm_bytes)) is None
    assert view.lookup_by_file_offset(len(wasm_bytes) + 100) is None
    assert view.lookup_by_file_offset(0xFFFFFFFF) is None
    # 5. TEST-LOAD-45: Import table ReadOnlyRadixBinaryTreeView search
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
    # Import 1: radix_mod.alpha
    app_imp.extend(_encode_leb128_u32(len(b"radix_mod")))
    app_imp.extend(b"radix_mod")
    app_imp.extend(_encode_leb128_u32(len(b"alpha")))
    app_imp.extend(b"alpha")
    app_imp.append(ExternalKind.FUNCTION)
    app_imp.extend(_encode_leb128_u32(0))
    # Import 2: radix_mod.compute
    app_imp.extend(_encode_leb128_u32(len(b"radix_mod")))
    app_imp.extend(b"radix_mod")
    app_imp.extend(_encode_leb128_u32(len(b"compute")))
    app_imp.extend(b"compute")
    app_imp.append(ExternalKind.FUNCTION)
    app_imp.extend(_encode_leb128_u32(0))
    app_buf.append(SectionID.IMPORT)
    app_buf.extend(_encode_leb128_u32(len(app_imp)))
    app_buf.extend(app_imp)
    app_view = loader.prepare("app_test_view", bytes(app_buf))
    imp_alpha = app_view.find_import("radix_mod", "alpha")
    assert imp_alpha is not None
    assert app_view.import_names(imp_alpha) == ("radix_mod", "alpha")
    imp_compute = app_view.find_import("radix_mod", "compute")
    assert imp_compute is not None
    assert app_view.import_names(imp_compute) == ("radix_mod", "compute")
    assert app_view.find_import("radix_mod", "unknown") is None
    # An ordinary lookup supplies no evidence for hash collision handling.
    exp_entry = view.lookup_export("gamma")
    assert exp_entry is not None
    assert view.export_name(exp_entry) == "gamma"
    # 7. TEST-LOAD-47: Fast non-existent symbol rejection
    assert view.lookup_export("totally_fake_symbol") is None


def test_load_54_rom_backed_names_and_hash_collision_resolution():
    """TEST-LOAD-46, TEST-LOAD-54 / GOTCHA-LOAD-01: Equal hashes resolve distinct ROM names to distinct functions."""
    loader = WasmLoader(BumpAllocator())
    view = loader.prepare(
        "collision_mod", wat_to_wasm('(module (func (export "ufbwjn")) (func (export "rsksbm")))')
    )

    first = view.lookup_export("ufbwjn")
    second = view.lookup_export("rsksbm")
    assert first is not None
    assert second is not None
    assert view.export_name(first) == "ufbwjn"
    assert view.export_name(second) == "rsksbm"
    assert first.index == 0
    assert second.index == 1
    assert first is not second
    assert view.lookup_export_func("ufbwjn") == 0
    assert view.lookup_export_func("rsksbm") == 1
    assert fnv1a_32("ufbwjn") == fnv1a_32("rsksbm")
    assert (
        bytes(view.rom_binary[first.name_offset : first.name_offset + first.name_size]) == b"ufbwjn"
    )


def test_load_48_loader_basic_block_index():
    """TEST-LOAD-48: Verifies loader owns basic block metadata and ReadOnlyRadixBinaryTreeStorage index."""
    import wasmtime
    from wasm_reader import parse

    wat = """(module
        (func (export "f1") (result i32)
            (i32.const 10)
            (return)
        )
        (func (export "f2") (result i32)
            (i32.const 20)
            (return)
        )
    )"""
    wasm_bytes = bytes(wasmtime.wat2wasm(wat))
    mod = parse(wasm_bytes)

    # Loader owns basic block storage and index ({Loader_BasicBlockIndex})
    assert mod.block_storage is not None
    assert len(mod.blocks) == 2
    assert mod.total_basic_blocks == 2

    # Lookup basic blocks directly from loader
    for blk in mod.blocks:
        found = mod.get_block(blk.head_pc)
        assert found is blk
        assert found.head_pc == blk.head_pc


def test_load_56_rejects_overaligned_memory_access():
    """TEST-LOAD-56: WebAssembly memarg validation accepts natural alignment and rejects a larger hint."""
    from wasm_reader import parse

    valid = parse(
        wat_to_wasm(
            "(module (memory 1) (func (param i32) (result i32) local.get 0 i32.load align=4))"
        )
    )
    assert len(valid.functions) == 1
    wasm_bytes = wat_to_wasm(
        "(module (memory 1) (func (param i32) (result i32) local.get 0 i32.load align=8))"
    )
    with expect_assertion("memory alignment exceeds the natural alignment"):
        parse(wasm_bytes)


def test_load_16_resolves_imported_global_offsets_for_active_segments():
    """TEST-LOAD-16: Streamed element/data offsets use the actual imported immutable i32 global."""
    from wasm_reader import parse

    module = parse(
        wat_to_wasm(
            '(module (import "host" "base" (global i32)) '
            "(memory 1) (table 4 funcref) (func $f) "
            '(data (global.get 0) "D") (elem (global.get 0) func $f))'
        )
    )
    memory = bytearray(65536)
    module.init_memory_data(memory, (2,))
    assert memory == bytearray(2) + b"D" + bytearray(65533)
    assert len(module.data_segments) == 0
    assert len(module.elements) == 0

    table = module.table_contents(0, (2,))
    assert tuple(table) == (None, None, 0, None)


def test_load_51_keeps_unreachable_polymorphism_inside_its_control_frame():
    """An unreachable outer frame must not make a nested block type-polymorphic."""
    from wasm_reader import parse

    wasm_bytes = wat_to_wasm("(module (func (unreachable) (block (drop (i32.eqz (nop))))))")
    with expect_assertion("WASM operand stack underflow"):
        parse(wasm_bytes)


def test_load_52_rejects_custom_section_names_past_section_end():
    """Custom section names are length-bounded before UTF-8 validation."""
    from wasm_reader import parse

    wasm_bytes = b"\x00asm\x01\x00\x00\x00\x00\x02\x03a"
    with expect_assertion("custom section name exceeds section bounds"):
        parse(wasm_bytes)


def test_load_53_bounds_leb128_and_section_counts_before_storage_configuration():
    """LEB128 reads stop at their field width and current section boundary."""
    from leb128 import decode_signed, decode_unsigned
    from wasm_reader import parse

    assert decode_unsigned(memoryview(b"\xff\xff\xff\xff\x0f"), 0) == (0xFFFF_FFFF, 5)
    assert decode_signed(memoryview(b"\x80\x80\x80\x80\x78"), 0, bits=32) == (
        -(1 << 31),
        5,
    )
    with expect_assertion("truncated unsigned LEB128"):
        decode_unsigned(memoryview(b"\x80"), 0)
    with expect_assertion("exceeds width"):
        decode_unsigned(memoryview(b"\xff\xff\xff\xff\x10"), 0)
    with expect_assertion("maximum 5 bytes"):
        decode_unsigned(memoryview(b"\x80\x80\x80\x80\x80\x00"), 0)

    over_capacity_types = b"\x00asm\x01\x00\x00\x00\x01\x02\x81\x02"
    with expect_assertion("exceeds configured maximum"):
        parse(memoryview(over_capacity_types))

    truncated_type_count_followed_by_section = b"\x00asm\x01\x00\x00\x00\x01\x01\x80\x03\x01\x00"
    with expect_assertion("truncated unsigned LEB128"):
        parse(memoryview(truncated_type_count_followed_by_section))


def test_load_55_active_parser_enforces_configured_wasm_memory_page_limit():
    """TEST-LOAD-55: The parser used by runtime creation enforces the configured page budget."""
    from config import FB_CONF_MAX_WASM_PAGES
    from wasm_reader import parse

    over_limit = _build_test_wasm_binary(memory_pages=FB_CONF_MAX_WASM_PAGES + 1)
    with expect_assertion("memory minimum exceeds FB_CONF_MAX_WASM_PAGES"):
        parse(memoryview(over_limit))


def test_load_30_function_capacity_rejects_one_more_than_configured_limit() -> None:
    """TEST-LOAD-30, TEST-LOAD-07: A valid module with one excess function cannot be committed."""
    functions = " ".join("(func)" for _ in range(FB_CONF_MAX_FUNCTIONS + 1))
    _assert_prepare_rejected_without_mutation(wat_to_wasm(f"(module {functions})"))


def test_load_31_more_than_64_exports_remain_searchable_without_fixed_export_limit() -> None:
    """TEST-LOAD-31: Every declared export survives indexing when the arena has enough space."""
    loader = WasmLoader(BumpAllocator())
    names = [f"export_{index:03}" for index in range(65)]
    view = loader.prepare("many_exports", _build_test_wasm_binary(export_names=names))
    assert view.is_ready
    assert [view.export_name(entry) for entry in view.exports_dict] == names
    for name in names:
        assert view.lookup_export_func(name) == 0
    assert view.lookup_export_func("export_065") is None


@pytest.mark.parametrize("bits, limit", ((32, 5), (64, 10)))
def test_load_32_unsigned_leb128_byte_budget(bits: int, limit: int) -> None:
    """TEST-LOAD-32: Continuation past the u32/u64 byte budget is rejected without reading further."""
    from leb128 import decode_unsigned

    with expect_assertion(f"maximum {limit} bytes"):
        decode_unsigned(memoryview(b"\x80" * limit + b"\x00"), 0, bits=bits)


def test_load_21_matching_imported_function_reexport_signature_links() -> None:
    """TEST-LOAD-21, TEST-LOAD-23: Imported re-exports use their import signature rather than a defined function slot."""
    loader = WasmLoader(BumpAllocator())
    library = loader.prepare(
        "lib_mod",
        wat_to_wasm(
            '(module (import "source" "original" (func $f (param i32 i32) (result i32))) (export "helper" (func $f)))'
        ),
    )
    view = loader.prepare("app_mod", _function_import_binary())
    assert loader.resolve_imports(view)
    assert view.is_ready
    assert view.resolved_imports.view().find(fnv1a_32("lib_mod.helper")) is library.lookup_export(
        "helper"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([str(_TEST_FILE)]))

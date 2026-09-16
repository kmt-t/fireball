from __future__ import annotations

import sys
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
    _TEST_FILE.parent,
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

"""
experiments/pysim/qa/tier2_runtime/test_loader.py
Tests for WASM Loader, Zero-Copy Indexing, and Hash + ReadOnlyRadixBinaryTreeView Symbol/Import/Offset Indexes.
Conforms strictly to docs/qa/tier2_runtime/runtime_loader_test_spec.md (TEST-LOAD-01 ~ TEST-LOAD-47).
"""

import struct

from helpers import expect_assertion, wat_to_wasm
from loader import (
    ExternalKind,
    FuncType,
    SectionID,
    ValType,
    WasmLoader,
    fnv1a_32,
)


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
    export_names: list[str] | None = None,
    corrupt_section_order: bool = False,
    corrupt_section_bounds: bool = False,
    invalid_type_idx: bool = False,
    memory_pages: int = 1,
) -> bytes:

    buf = bytearray()
    buf.extend(magic)
    buf.extend(struct.pack("<I", version))
    # Type Section (ID=1) -> (i32, i32) -> i32
    type_payload = bytearray()
    type_payload.extend(_encode_leb128_u32(1))
    type_payload.append(0x60)
    type_payload.extend(_encode_leb128_u32(2))
    type_payload.extend([ValType.I32, ValType.I32])
    type_payload.extend(_encode_leb128_u32(1))
    type_payload.append(ValType.I32)
    buf.append(SectionID.TYPE)
    buf.extend(_encode_leb128_u32(len(type_payload)))
    buf.extend(type_payload)
    # Function Section (ID=3)
    func_payload = bytearray()
    func_payload.extend(_encode_leb128_u32(1))
    func_payload.extend(_encode_leb128_u32(999 if invalid_type_idx else 0))
    buf.append(SectionID.FUNCTION)
    buf.extend(_encode_leb128_u32(len(func_payload)))
    buf.extend(func_payload)
    # Memory Section (ID=5)
    mem_payload = bytearray()
    mem_payload.extend(_encode_leb128_u32(1))
    mem_payload.append(0x00)
    mem_payload.extend(_encode_leb128_u32(memory_pages))
    buf.append(SectionID.MEMORY)
    buf.extend(_encode_leb128_u32(len(mem_payload)))
    buf.extend(mem_payload)
    # Global Section (ID=6)
    glob_payload = bytearray()
    glob_payload.extend(_encode_leb128_u32(1))
    glob_payload.append(ValType.I32)
    glob_payload.append(0x00)
    glob_payload.append(0x41)
    glob_payload.extend(_encode_leb128_s32(42))
    glob_payload.append(0x0B)
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
        exp_payload.extend(_encode_leb128_u32(0))

    if corrupt_section_order:
        buf.append(SectionID.IMPORT)
        buf.extend(_encode_leb128_u32(0))

    buf.append(SectionID.EXPORT)
    buf.extend(_encode_leb128_u32(len(exp_payload)))
    buf.extend(exp_payload)
    # Code Section (ID=10)
    code_body = bytearray()
    code_body.extend(_encode_leb128_u32(0))
    code_body.extend([0x20, 0x00, 0x20, 0x01, 0x6A, 0x0B])
    code_payload = bytearray()
    code_payload.extend(_encode_leb128_u32(1))
    code_payload.extend(_encode_leb128_u32(len(code_body)))
    code_payload.extend(code_body)
    buf.append(SectionID.CODE)
    if corrupt_section_bounds:
        buf.extend(_encode_leb128_u32(9999))
    else:
        buf.extend(_encode_leb128_u32(len(code_payload)))

    buf.extend(code_payload)
    return bytes(buf)


def test_load_01_to_07_lightweight_verification():
    """TEST-LOAD-01..07: Verifies V1-V6 lightweight checks and transactional rollback."""
    loader = WasmLoader()
    # Normal prepare
    valid_wasm = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("valid_mod", valid_wasm)
    assert view.is_ready is True
    # V1: Bad magic
    watermark = loader.allocator.offset
    with expect_assertion():
        loader.prepare("bad_magic", _build_test_wasm_binary(magic=b"\x7fELF"))
    assert loader.allocator.offset == watermark
    # V2: Bad version
    with expect_assertion():
        loader.prepare("bad_ver", _build_test_wasm_binary(version=2))
    assert loader.allocator.offset == watermark
    # V3: Bad section bounds
    with expect_assertion():
        loader.prepare("bad_bounds", _build_test_wasm_binary(corrupt_section_bounds=True))
    assert loader.allocator.offset == watermark
    # V4: Bad section order
    with expect_assertion():
        loader.prepare("bad_order", _build_test_wasm_binary(corrupt_section_order=True))
    assert loader.allocator.offset == watermark
    # V5: Bad type index
    with expect_assertion():
        loader.prepare("bad_type", _build_test_wasm_binary(invalid_type_idx=True))
    assert loader.allocator.offset == watermark
    # V6: Exceeds page budget
    with expect_assertion():
        loader.prepare("bad_mem", _build_test_wasm_binary(memory_pages=32))
    assert loader.allocator.offset == watermark


def test_load_10_to_15_zero_copy_and_accessors():
    """TEST-LOAD-10..15: Verifies ROM direct references, Hash + ReadOnlyRadixBinaryTreeView export lookup, and lazy accessors."""
    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["zeta", "alpha", "beta"])
    view = loader.prepare("zc_mod", wasm_bytes)
    # Exports sorted
    exp_names = [e.name for e in view.exports_dict]
    assert exp_names == ["alpha", "beta", "zeta"]
    # Hash + ReadOnlyRadixBinaryTreeView lookup (TEST-LOAD-13)
    assert view.lookup_export_func("alpha") == 0
    assert view.lookup_export_func("beta") == 0
    assert view.lookup_export_func("zeta") == 0
    assert view.lookup_export_func("nonexistent") is None
    # Function Accessor
    func_acc = view.get_function(0)
    assert func_acc.get_type_index() == 0
    assert func_acc.get_signature() == FuncType([ValType.I32, ValType.I32], [ValType.I32])
    code_stream = func_acc.get_code_stream()
    bytecode = bytes(code_stream.read_bytes(code_stream.remaining()))
    assert bytecode == bytes([0x20, 0x00, 0x20, 0x01, 0x6A, 0x0B])
    # Global Accessor
    glob_acc = view.get_global(0)
    valtype, mutable = glob_acc.get_metadata()
    assert valtype == ValType.I32
    assert mutable is False


def test_load_20_to_25_multi_module_import_resolution():
    """TEST-LOAD-20..25: Verifies multi-module imports, readiness, linking via Hash + ReadOnlyRadixBinaryTreeView, and unloading."""
    loader = WasmLoader()
    # 1. Prepare target library module
    lib_wasm = _build_test_wasm_binary(export_names=["helper"])
    lib_view = loader.prepare("lib_mod", lib_wasm)
    # 2. Build dependent app module
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
    # Import section: lib_mod.helper
    app_imp = bytearray()
    app_imp.extend(_encode_leb128_u32(1))
    app_imp.extend(_encode_leb128_u32(len(b"lib_mod")))
    app_imp.extend(b"lib_mod")
    app_imp.extend(_encode_leb128_u32(len(b"helper")))
    app_imp.extend(b"helper")
    app_imp.append(ExternalKind.FUNCTION)
    app_imp.extend(_encode_leb128_u32(0))
    app_buf.append(SectionID.IMPORT)
    app_buf.extend(_encode_leb128_u32(len(app_imp)))
    app_buf.extend(app_imp)
    app_view = loader.prepare("app_mod", bytes(app_buf))
    assert app_view.is_ready is False  # Pending resolution
    # TEST-LOAD-21: Hash + ReadOnlyRadixBinaryTreeView import resolution
    assert loader.resolve_imports(app_view) is True
    assert app_view.is_ready is True
    assert app_view.resolved_imports.view().find(fnv1a_32("lib_mod.helper")) is not None
    # Unload
    assert loader.unload(app_view) is True
    assert loader.lookup("app_mod") is None


def test_load_40_to_47_radix_binary_tree_view_indexes():
    """TEST-LOAD-40..47: Verifies ReadOnlyRadixBinaryTreeView file offset and Hash symbol/import indexes."""
    loader = WasmLoader()
    wasm_bytes = _build_test_wasm_binary(export_names=["alpha", "beta", "gamma", "compute"])
    view = loader.prepare("radix_mod", wasm_bytes)
    # 1. TEST-LOAD-40: Entities registered in DecodedEntityRegistry
    assert len(view.entity_registry) > 0
    kinds = [e.kind for e in view.entity_registry]
    assert "SECTION" in kinds
    assert "FUNCTION" in kinds
    assert "GLOBAL" in kinds
    # 2. TEST-LOAD-41 & 42: Function body reverse lookup
    func_start, func_size = view.code_offsets[0]
    entity_start = view.lookup_by_file_offset(func_start)
    assert entity_start is not None
    assert entity_start.kind == "FUNCTION"
    assert entity_start.index == 0
    entity_mid = view.lookup_by_file_offset(func_start + 2)
    assert entity_mid is not None
    assert entity_mid.kind == "FUNCTION"
    # 3. TEST-LOAD-43: Global entry reverse lookup
    global_entry = view.globals[0]
    entity_glob = view.lookup_by_file_offset(global_entry.init_expr_offset)
    assert entity_glob is not None
    assert entity_glob.kind == "GLOBAL"
    # 4. TEST-LOAD-44: Invalid / out-of-bounds offsets
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
    assert imp_alpha.field_name == "alpha"
    imp_compute = app_view.find_import("radix_mod", "compute")
    assert imp_compute is not None
    assert imp_compute.field_name == "compute"
    assert app_view.find_import("radix_mod", "unknown") is None
    # 6. TEST-LOAD-46: Hash collision verification
    exp_entry = view.lookup_export("gamma")
    assert exp_entry is not None
    assert exp_entry.name == "gamma"
    # 7. TEST-LOAD-47: Fast non-existent symbol rejection
    assert view.lookup_export("totally_fake_symbol") is None


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
    assert mod.block_storage is not None
    assert len(mod.blocks) == 2
    assert mod.total_basic_blocks == 2

    # Lookup basic blocks directly from loader
    for blk in mod.blocks:
        found = mod.get_block(blk.head_pc)
        assert found is blk
        assert found.head_pc == blk.head_pc


def test_load_49_rejects_overaligned_memory_access():
    """MVP validation rejects memarg alignment stronger than the access width."""
    from wasm_reader import parse

    wasm_bytes = wat_to_wasm(
        "(module (memory 1) (func (param i32) (result i32) "
        "local.get 0 i32.load align=8))"
    )
    with expect_assertion("memory alignment exceeds the natural alignment"):
        parse(wasm_bytes)


def test_load_50_resolves_imported_global_offsets_for_active_segments():
    """MVP data and element offset expressions may read imported immutable i32 globals."""
    from wasm_reader import parse

    module = parse(
        wat_to_wasm(
            '(module (import "host" "base" (global i32)) '
            '(memory 1) (table 4 funcref) (func $f) '
            '(data (global.get 0) "D") (elem (global.get 0) func $f))'
        )
    )
    memory = bytearray(65536)
    module.init_memory_data(memory, (2,))
    assert memory[2] == ord("D")

    table = module.table_contents(0, (2,))
    assert table[2] == 0


def test_load_51_keeps_unreachable_polymorphism_inside_its_control_frame():
    """An unreachable outer frame must not make a nested block type-polymorphic."""
    from wasm_reader import parse

    wasm_bytes = wat_to_wasm(
        "(module (func (unreachable) (block (drop (i32.eqz (nop))))))"
    )
    with expect_assertion("WASM operand stack underflow"):
        parse(wasm_bytes)


def test_load_52_rejects_custom_section_names_past_section_end():
    """Custom section names are length-bounded before UTF-8 validation."""
    from wasm_reader import parse

    wasm_bytes = b"\x00asm\x01\x00\x00\x00\x00\x02\x03a"
    with expect_assertion("custom section name exceeds section bounds"):
        parse(wasm_bytes)


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"\n[PASS] All {len(ALL_TESTS)} WASM Loader tests passed successfully.")

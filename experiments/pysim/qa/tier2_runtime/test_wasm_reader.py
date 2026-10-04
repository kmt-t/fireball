"""QA for the WASM parser used by the Python reference runtime."""

from __future__ import annotations

from pathlib import Path

import pytest
from bump_allocator import BumpAllocator
from config import FB_CONF_MAX_FUNCTIONS, FB_CONF_MAX_WASM_PAGES
from fnv1a import fnv1a_32
from qa.private.tier2_runtime.module_link_harness import (
    QA_MAX_REGISTERED_MODULES,
    QAModuleLinkHarness,
)
from qa.shared.helpers import (
    _build_test_wasm_binary,
    expect_assertion,
    wat_to_wasm,
)
from tier2_runtime.wasm.reader import parse

_TEST_FILE = Path(__file__).resolve()


def _assert_parse_rejected_without_mutation(binary: bytes, message: str = "") -> None:
    allocator = BumpAllocator()
    watermark = allocator.offset
    for _ in range(2):
        with expect_assertion(message):
            parse(memoryview(binary), allocator)
        assert allocator.offset == watermark


def test_load_01_invalid_magic_rejects_and_rolls_back() -> None:
    _assert_parse_rejected_without_mutation(_build_test_wasm_binary(magic=b"\x7fELF"), "magic")


def test_load_02_unsupported_version_rejects_and_rolls_back() -> None:
    _assert_parse_rejected_without_mutation(_build_test_wasm_binary(version=2), "version")


def test_load_03_section_past_binary_end_rejects_and_rolls_back() -> None:
    _assert_parse_rejected_without_mutation(
        _build_test_wasm_binary(corrupt_section_bounds=True), "section length"
    )


@pytest.mark.parametrize("duplicate", (False, True))
def test_load_04_decreasing_or_duplicate_section_rejects_and_rolls_back(
    duplicate: bool,
) -> None:
    binary = _build_test_wasm_binary()
    type_section = b"\x01\x07\x01\x60\x02\x7f\x7f\x01\x7f"
    assert binary[8:17] == type_section
    malformed = binary[:17] + binary[8:] if duplicate else binary + type_section
    _assert_parse_rejected_without_mutation(malformed, "out of order")


def test_load_04_custom_sections_are_exempt_from_type_ordering() -> None:
    binary = _build_test_wasm_binary() + b"\x00\x02\x01x\x00\x02\x01y"
    module = parse(memoryview(binary))
    assert module.export_func_index("add") == 0


def test_load_05_invalid_type_index_rejects_and_rolls_back() -> None:
    _assert_parse_rejected_without_mutation(_build_test_wasm_binary(invalid_type_idx=True), "type")


def test_load_06_memory_page_limit_rejects_and_rolls_back() -> None:
    _assert_parse_rejected_without_mutation(
        _build_test_wasm_binary(memory_pages=FB_CONF_MAX_WASM_PAGES + 1),
        "memory",
    )


def test_load_07_arena_exhaustion_rolls_back_partial_metadata_allocations() -> None:
    allocator = BumpAllocator(capacity=2048)
    allocator.allocate(2040, alignment=1)
    watermark = allocator.offset
    binary = _build_test_wasm_binary()
    for _ in range(3):
        with expect_assertion("capacity exceeded"):
            parse(memoryview(binary), allocator)
        assert allocator.offset == watermark


def test_load_16_resolves_imported_global_offsets_for_active_segments() -> None:
    module = parse(
        memoryview(
            wat_to_wasm(
                '(module (import "host" "base" (global i32)) '
                "(memory 1) (table 4 funcref) (func $f) "
                '(data (global.get 0) "D") (elem (global.get 0) func $f))'
            )
        )
    )
    memory = bytearray(65536)
    module.init_memory_data(memory, (2,))
    assert memory == bytearray(2) + b"D" + bytearray(65533)
    assert len(module.data_segments) == 0
    assert len(module.elements) == 0
    assert tuple(module.table_contents(0, (2,))) == (None, None, 0, None)


def _function_import_binary(
    field: str = "helper", signature: str = "(param i32 i32) (result i32)"
) -> bytes:
    return wat_to_wasm(f'(module (import "lib_mod" "{field}" (func {signature})))')


def test_load_20_qa_link_harness_keeps_unresolved_import_unready() -> None:
    harness = QAModuleLinkHarness()
    module = harness.prepare("app_mod", _function_import_binary())

    assert harness.lookup("app_mod") is module
    assert not module.is_ready
    assert module.resolved_imports == ()
    with expect_assertion("dependency module is unresolved"):
        harness.resolve_imports(module)
    assert not module.is_ready
    assert module.resolved_imports == ()


def test_load_21_qa_link_harness_resolves_matching_signature_across_type_indexes() -> None:
    harness = QAModuleLinkHarness()
    library = harness.prepare(
        "lib_mod",
        wat_to_wasm(
            "(module (type (func)) "
            '(func (export "helper") (param i32 i32) (result i32) '
            "local.get 0 local.get 1 i32.add))"
        ),
    )
    module = harness.prepare("app_mod", _function_import_binary())

    assert library.module.functions[0].type_index != module.module.imports[0].type_index
    assert not module.is_ready
    assert harness.resolve_imports(module)
    assert module.is_ready
    assert tuple(
        (target.module_name, target.function_index) for target in module.resolved_imports
    ) == (("lib_mod", 0),)


def test_load_21_qa_link_harness_resolves_imported_function_reexport() -> None:
    harness = QAModuleLinkHarness()
    harness.prepare("source", _build_test_wasm_binary(export_names=["original"]))
    harness.prepare(
        "lib_mod",
        wat_to_wasm(
            '(module (import "source" "original" '
            '(func $f (param i32 i32) (result i32))) (export "helper" (func $f)))'
        ),
    )
    library = harness.lookup("lib_mod")
    assert library is not None
    assert harness.resolve_imports(library)
    module = harness.prepare("app_mod", _function_import_binary())

    assert harness.resolve_imports(module)
    assert tuple(
        (target.module_name, target.function_index) for target in module.resolved_imports
    ) == (("lib_mod", 0),)


def test_load_22_qa_link_harness_missing_late_symbol_commits_no_partial_links() -> None:
    harness = QAModuleLinkHarness()
    harness.prepare("lib_mod", _build_test_wasm_binary(export_names=["helper"]))
    module = harness.prepare(
        "app_mod",
        wat_to_wasm(
            "(module "
            '(import "lib_mod" "helper" (func (param i32 i32) (result i32))) '
            '(import "lib_mod" "missing" (func)))'
        ),
    )

    with expect_assertion("unresolved function export"):
        harness.resolve_imports(module)
    assert not module.is_ready
    assert module.resolved_imports == ()
    assert harness.lookup("app_mod") is module


@pytest.mark.parametrize(
    "signature",
    (
        "(param i64 i32) (result i32)",
        "(param i32) (result i32)",
        "(param i32 i32) (result i64)",
        "(param i32 i32)",
    ),
)
def test_load_23_qa_link_harness_rejects_function_signature_mismatch(
    signature: str,
) -> None:
    harness = QAModuleLinkHarness()
    harness.prepare("lib_mod", _build_test_wasm_binary(export_names=["helper"]))
    module = harness.prepare("app_mod", _function_import_binary(signature=signature))

    with expect_assertion("function signature mismatch"):
        harness.resolve_imports(module)
    assert not module.is_ready
    assert module.resolved_imports == ()


def test_load_23_qa_link_harness_late_signature_mismatch_commits_no_partial_links() -> None:
    harness = QAModuleLinkHarness()
    harness.prepare("lib_mod", _build_test_wasm_binary(export_names=["helper", "other"]))
    module = harness.prepare(
        "app_mod",
        wat_to_wasm(
            "(module "
            '(import "lib_mod" "helper" (func (param i32 i32) (result i32))) '
            '(import "lib_mod" "other" (func (param i64) (result i64))))'
        ),
    )

    with expect_assertion("function signature mismatch"):
        harness.resolve_imports(module)
    assert not module.is_ready
    assert module.resolved_imports == ()


def test_load_24_qa_link_harness_enforces_registry_capacity() -> None:
    harness = QAModuleLinkHarness()
    binary = _build_test_wasm_binary()
    modules = [
        harness.prepare(f"module_{index}", binary) for index in range(QA_MAX_REGISTERED_MODULES)
    ]

    with expect_assertion("registry capacity"):
        harness.prepare("overflow", binary)
    assert harness.lookup("overflow") is None
    for index, module in enumerate(modules):
        assert harness.lookup(f"module_{index}") is module
        assert module.is_ready


def test_load_46_parser_export_lookup_checks_names_when_hashes_collide() -> None:
    module = parse(
        memoryview(wat_to_wasm('(module (func (export "ufbwjn")) (func (export "rsksbm")))'))
    )

    assert fnv1a_32("ufbwjn") == fnv1a_32("rsksbm")
    assert module.export_func_index("ufbwjn") == 0
    assert module.export_func_index("rsksbm") == 1


def test_load_30_function_capacity_rejects_one_more_than_configured_limit() -> None:
    functions = " ".join("(func)" for _ in range(FB_CONF_MAX_FUNCTIONS + 1))
    _assert_parse_rejected_without_mutation(wat_to_wasm(f"(module {functions})"), "functions")


def test_load_31_more_than_64_exports_are_retained() -> None:
    names = [f"export_{index:03}" for index in range(65)]
    module = parse(memoryview(_build_test_wasm_binary(export_names=names)))
    assert len(module.exports) == len(names)
    for name in names:
        assert module.export_func_index(name) == 0


def test_load_32_unsigned_leb128_byte_budget() -> None:
    from tier2_runtime.wasm.leb128 import decode_unsigned

    for bits, limit in ((32, 5), (64, 10)):
        with expect_assertion(f"maximum {limit} bytes"):
            decode_unsigned(memoryview(b"\x80" * limit + b"\x00"), 0, bits=bits)


def test_load_48_module_builds_basic_block_index() -> None:
    wasm_bytes = wat_to_wasm(
        """(module
            (func (export "f1") (result i32) (i32.const 10) (return))
            (func (export "f2") (result i32) (i32.const 20) (return)))"""
    )
    module = parse(memoryview(wasm_bytes))

    assert module.block_storage is not None
    assert len(module.blocks) == 2
    assert module.total_basic_blocks == 2
    for block in module.blocks:
        assert module.get_block(block.head_pc) is block


def test_load_51_keeps_unreachable_polymorphism_inside_its_control_frame() -> None:
    wasm_bytes = wat_to_wasm("(module (func (unreachable) (block (drop (i32.eqz (nop))))))")
    with expect_assertion("WASM operand stack underflow"):
        parse(memoryview(wasm_bytes))


def test_load_52_rejects_custom_section_names_past_section_end() -> None:
    wasm_bytes = b"\x00asm\x01\x00\x00\x00\x00\x02\x03a"
    with expect_assertion("custom section name exceeds section bounds"):
        parse(memoryview(wasm_bytes))


def test_load_53_bounds_leb128_and_section_counts_before_storage_configuration() -> None:
    from tier2_runtime.wasm.leb128 import decode_signed, decode_unsigned

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
    truncated_type_count = b"\x00asm\x01\x00\x00\x00\x01\x01\x80\x03\x01\x00"
    with expect_assertion("truncated unsigned LEB128"):
        parse(memoryview(truncated_type_count))


def test_load_55_parser_enforces_configured_wasm_memory_page_limit() -> None:
    over_limit = _build_test_wasm_binary(memory_pages=FB_CONF_MAX_WASM_PAGES + 1)
    with expect_assertion("memory minimum exceeds FB_CONF_MAX_WASM_PAGES"):
        parse(memoryview(over_limit))


def test_load_56_rejects_overaligned_memory_access() -> None:
    valid = parse(
        memoryview(
            wat_to_wasm(
                "(module (memory 1) (func (param i32) (result i32) local.get 0 i32.load align=4))"
            )
        )
    )
    assert len(valid.functions) == 1
    invalid = wat_to_wasm(
        "(module (memory 1) (func (param i32) (result i32) local.get 0 i32.load align=8))"
    )
    with expect_assertion("memory alignment exceeds the natural alignment"):
        parse(memoryview(invalid))


if __name__ == "__main__":
    raise SystemExit(pytest.main([str(_TEST_FILE)]))

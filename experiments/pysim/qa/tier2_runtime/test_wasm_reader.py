"""QA for the WASM parser used by the Python reference runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from bump_allocator import BumpAllocator
from config import (
    FB_CONF_MAX_FUNCTIONS,
    FB_CONF_MAX_IMPORTS,
    FB_CONF_MAX_LOCALS,
    FB_CONF_MAX_NESTING_DEPTH,
    FB_CONF_MAX_TYPES,
    FB_CONF_MAX_VALUE_STACK,
    FB_CONF_MAX_WASM_PAGES,
)
from fnv1a import fnv1a_32
from qa.private.tier2_runtime.module_link_harness import (
    QA_MAX_REGISTERED_MODULES,
    QAModuleLinkHarness,
)
from qa.shared.helpers import (
    _build_test_wasm_binary,
    _encode_leb128_u32,
    expect_assertion,
    wat_to_wasm,
)
from system_containers import fold_mix32
from tier2_runtime.interpreter.errors import TrapCode
from tier2_runtime.wasm.leb128 import decode_unsigned_full
from tier2_runtime.wasm.reader import parse, parse_standalone
from tier2_runtime.wasm.validation import InvalidWasm

_TEST_FILE = Path(__file__).resolve()


def test_br_table_annotation_uses_label_count_instead_of_instruction_width() -> None:
    from tier2_runtime.interpreter.control_flow import build_interpreter_annotation_pool
    from tier2_runtime.wasm.module import LocalLayout

    count = 253
    count_bytes = _encode_leb128_u32(count)
    code = b"\x0e" + count_bytes + bytes(count + 1)
    assert len(code) > 0xFF
    annotations, _ = build_interpreter_annotation_pool(
        memoryview(code), (0,), local_layout=LocalLayout(())
    )
    assert bytes(annotations)[:2] == count.to_bytes(2, "little")
    assert len(annotations) == 2 + (count + 1) * 4


def test_loader_annotation_pool_resolves_branch_target_block_index() -> None:
    module = parse_standalone(
        wat_to_wasm('(module (func (export "f") (result i32) (block $b (br $b)) i32.const 7))')
    )
    function = module.functions[0]
    function_index = len(module.imports)
    code = module.code_for(function_index)
    branch = next(
        block
        for block in module.blocks
        if block.func_index == function_index
        and code[block.head_pc - function.code_pc_offset] == 0x0C
    )
    annotations = function.interpreter_annotation_pool
    assert annotations is not None
    edge_offset = branch.interpreter_annotation_byte_offset
    target_index = int.from_bytes(bytes(annotations)[edge_offset : edge_offset + 2], "little")
    assert target_index == branch.next_block_index
    assert branch.next_pc is not None
    assert module.blocks[target_index].head_pc == branch.next_pc


def test_loader_accepts_br_table_with_u16_label_count() -> None:
    count = 253
    binary = wat_to_wasm(
        '(module (func (export "main") (block (i32.const 0) (br_table '
        + "0 " * (count + 1)
        + "))))"
    )
    module = parse(memoryview(binary), BumpAllocator(capacity=1024 * 1024))
    function = module.functions[0]
    code = module.code_for(0)
    assert len(code) > 0xFF
    assert function.interpreter_annotation_pool is not None


def test_loader_rejects_br_table_label_count_above_u16() -> None:
    from tier2_runtime.interpreter.control_flow import build_interpreter_annotation_pool
    from tier2_runtime.wasm.module import LocalLayout

    count = 0x1_0000
    code = b"\x0e" + _encode_leb128_u32(count) + bytes(count + 1)
    with pytest.raises(InvalidWasm, match="ERR_WASM_UNSUPPORTED_FEATURE: br_table label count"):
        build_interpreter_annotation_pool(memoryview(code), (0,), local_layout=LocalLayout(()))


def test_loader_accepts_configured_type_local_and_function_limits() -> None:
    types = " ".join("(type (func))" for _ in range(FB_CONF_MAX_TYPES))
    type_module = parse_standalone(wat_to_wasm(f"(module {types})"))
    assert len(type_module.types) == FB_CONF_MAX_TYPES

    imports = " ".join(f'(import "env" "f{index}" (func))' for index in range(FB_CONF_MAX_IMPORTS))
    functions = " ".join("(func)" for _ in range(FB_CONF_MAX_FUNCTIONS))
    function_module = parse_standalone(wat_to_wasm(f"(module {imports} {functions})"))
    assert len(function_module.imports) == FB_CONF_MAX_IMPORTS
    assert len(function_module.functions) == FB_CONF_MAX_FUNCTIONS
    assert len(function_module.imports) + len(function_module.functions) == (
        FB_CONF_MAX_IMPORTS + FB_CONF_MAX_FUNCTIONS
    )

    locals_ = " ".join("(local i32)" for _ in range(FB_CONF_MAX_LOCALS))
    local_module = parse_standalone(wat_to_wasm(f'(module (func (export "f") {locals_}))'))
    assert len(local_module.functions[0].locals_extra) == FB_CONF_MAX_LOCALS


def test_loader_accepts_and_rejects_nesting_depth_boundary() -> None:
    nested = "(block " * FB_CONF_MAX_NESTING_DEPTH + ")" * FB_CONF_MAX_NESTING_DEPTH
    accepted = parse_standalone(wat_to_wasm(f'(module (func (export "f") {nested}))'))
    assert accepted.functions[0].control_map is not None

    over_limit = FB_CONF_MAX_NESTING_DEPTH + 1
    nested_over_limit = "(block " * over_limit + ")" * over_limit
    with pytest.raises(InvalidWasm, match="ERR_WASM_UNSUPPORTED_FEATURE: block/loop/if nesting"):
        parse_standalone(wat_to_wasm(f'(module (func (export "f") {nested_over_limit}))'))


def test_loader_records_maximum_operand_stack_in_raw_words() -> None:
    module = parse_standalone(
        wat_to_wasm(
            """(module
          (func (result i64)
            i32.const 1 i64.const 2 f64.const 3 drop drop drop i64.const 4)
          (func (param i32) (result i64)
            local.get 0
            if (result i64)
              i64.const 1
            else
              f64.const 3 drop i32.const 4 i64.const 5 drop drop i64.const 6
            end)
          (func))"""
        )
    )
    assert tuple(fn.max_operand_stack_words for fn in module.functions) == (5, 3, 0)


def test_load_61_type_analysis_dispatch_is_immutable() -> None:
    """TEST-LOAD-61: execution cannot overwrite the loader's static opcode catalog."""
    from tier2_runtime.wasm.opcodes import I32_ADD, I32_CONST
    from tier2_runtime.wasm.reader import _STACK_ANALYSIS_DISPATCH

    add_handler = _STACK_ANALYSIS_DISPATCH[I32_ADD]
    assert add_handler is not None
    with pytest.raises(TypeError):
        _STACK_ANALYSIS_DISPATCH[I32_ADD] = None
    assert _STACK_ANALYSIS_DISPATCH[I32_ADD] is add_handler
    module = parse_standalone(memoryview(_build_test_wasm_binary()))
    assert module.code_for(0)[-2] == I32_ADD
    assert _STACK_ANALYSIS_DISPATCH[I32_CONST] is not add_handler


def _assert_parse_rejected_without_mutation(binary: bytes, message: str = "") -> None:
    allocator = BumpAllocator()
    watermark = allocator.offset
    for _ in range(2):
        with pytest.raises(InvalidWasm, match=message) as caught:
            parse(memoryview(binary), allocator)
        assert caught.value.code == TrapCode.INVALID_WASM_BINARY
        assert allocator.offset == watermark


def _single_void_function_module(code: bytes) -> bytes:
    body = b"\x00" + code
    type_payload = b"\x01\x60\x00\x00"
    function_payload = b"\x01\x00"
    code_payload = b"\x01" + _encode_leb128_u32(len(body)) + body

    def section(section_id: int, payload: bytes) -> bytes:
        return bytes((section_id,)) + _encode_leb128_u32(len(payload)) + payload

    return (
        b"\x00asm\x01\x00\x00\x00"
        + section(1, type_payload)
        + section(3, function_payload)
        + section(10, code_payload)
    )


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
    module = parse_standalone(memoryview(binary))
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


def test_load_10_signature_and_code_accessors_borrow_original_binary() -> None:
    """TEST-LOAD-10: immutable variable-length metadata stays in the input ROM."""
    binary = _build_test_wasm_binary()
    module = parse_standalone(memoryview(binary))
    assert module.source is not None and module.source.obj is binary
    record = module.types[0]
    assert record.param_count == 2
    assert (
        module.source[record.param_offset : record.param_offset + record.param_count] == b"\x7f\x7f"
    )
    assert record.result_count == 1
    assert (
        module.source[record.result_offset : record.result_offset + record.result_count] == b"\x7f"
    )
    signature = module.type_at(0)
    assert signature.params.obj is binary and signature.results.obj is binary
    assert signature.params == b"\x7f\x7f" and signature.results == b"\x7f"
    assert (
        signature.params
        == module.source[signature.params_source_offset : signature.params_source_offset + 2]
    )
    assert (
        signature.results
        == module.source[signature.results_source_offset : signature.results_source_offset + 1]
    )
    code = module.code_for(0)
    assert code.obj is binary and code == b"\x20\x00\x20\x01\x6a\x0b"


def test_load_16_resolves_imported_global_offsets_for_active_segments() -> None:
    module = parse_standalone(
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
    assert module.source is not None
    assert (
        module.source[
            module.data_section_offset : module.data_section_offset + module.data_section_size
        ]
        == b"\x01\x00\x23\x00\x0b\x01D"
    )
    assert (
        module.source[
            module.element_section_offset : module.element_section_offset
            + module.element_section_size
        ]
        == b"\x01\x00\x23\x00\x0b\x01\x00"
    )
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
    module = parse_standalone(
        memoryview(wat_to_wasm('(module (func (export "ufbwjn")) (func (export "rsksbm")))'))
    )

    assert fnv1a_32("ufbwjn") == fnv1a_32("rsksbm")
    assert module.export_func_index("ufbwjn") == 0
    assert module.export_func_index("rsksbm") == 1


def test_load_30_function_capacity_rejects_one_more_than_configured_limit() -> None:
    functions = " ".join("(func)" for _ in range(FB_CONF_MAX_FUNCTIONS + 1))
    _assert_parse_rejected_without_mutation(wat_to_wasm(f"(module {functions})"), "functions")


def test_load_type_capacity_rejects_one_more_than_configured_limit() -> None:
    types = " ".join("(type (func))" for _ in range(FB_CONF_MAX_TYPES + 1))
    _assert_parse_rejected_without_mutation(wat_to_wasm(f"(module {types})"), "types")


def test_load_local_capacity_rejects_one_more_than_configured_limit() -> None:
    locals_ = " ".join("(local i32)" for _ in range(FB_CONF_MAX_LOCALS + 1))
    _assert_parse_rejected_without_mutation(
        wat_to_wasm(f'(module (func (export "f") {locals_}))'), "local count"
    )


def test_load_parameter_capacity_rejects_one_more_than_configured_limit() -> None:
    parameters = " ".join("(param i32)" for _ in range(FB_CONF_MAX_VALUE_STACK + 1))
    _assert_parse_rejected_without_mutation(
        wat_to_wasm(f"(module (func {parameters}))"), "function parameter count"
    )


def test_load_31_more_than_64_exports_are_retained() -> None:
    names = [f"export_{index:03}" for index in range(65)]
    module = parse_standalone(memoryview(_build_test_wasm_binary(export_names=names)))
    assert len(module.exports) == len(names)
    for name in names:
        assert module.export_func_index(name) == 0


def test_load_32_unsigned_leb128_byte_budget() -> None:

    for bits, limit in ((32, 5), (64, 10)):
        with pytest.raises(InvalidWasm, match=f"maximum {limit} bytes"):
            decode_unsigned_full(memoryview(b"\x80" * limit + b"\x00"), 0, bits=bits)


def test_load_48_module_builds_basic_block_index() -> None:
    function_count = 40
    function_declarations = " ".join("(func)" for _ in range(function_count))
    wasm_bytes = wat_to_wasm(f"(module {function_declarations})")
    module = parse_standalone(memoryview(wasm_bytes))

    assert module.block_storage is not None
    assert len(module.blocks) == function_count
    assert module.total_basic_blocks == function_count
    for block_index, block in enumerate(module.blocks):
        assert module.get_block(block.head_pc) is block
        search_index = module.block_storage.view().find(fold_mix32(block.head_pc))
        assert search_index == block_index


def test_load_59_indexes_more_than_ten_thousand_basic_blocks() -> None:
    repetitions = 3_334
    block_sequence = b"\x02\x40\x0c\x00\x0b" * repetitions
    wasm_bytes = _single_void_function_module(block_sequence + b"\x0b")
    module = parse(memoryview(wasm_bytes), BumpAllocator(capacity=1024 * 1024))

    assert len(module.blocks) > 10_000
    assert module.block_storage is not None
    for block in module.blocks:
        assert module.get_block(block.head_pc) is block


def test_loader_rejects_basic_block_count_above_u16_capacity() -> None:
    pattern = b"\x02\x40\x0c\x00\x0b"
    binary = _single_void_function_module(pattern * 21_845 + b"\x0b")

    with pytest.raises(InvalidWasm, match="basic block count exceeds u16 capacity"):
        parse_standalone(memoryview(binary))


def test_loader_accepts_basic_block_count_at_u16_capacity() -> None:
    pattern = b"\x02\x40\x0c\x00\x0b"
    large_body = b"\x00" + pattern * 21_844 + b"\x0b"
    empty_body = b"\x00\x0b"
    type_payload = b"\x01\x60\x00\x00"
    function_payload = b"\x03\x00\x00\x00"
    bodies = (large_body, empty_body, empty_body)
    code_payload = b"\x03" + b"".join(_encode_leb128_u32(len(body)) + body for body in bodies)

    def section(section_id: int, payload: bytes) -> bytes:
        return bytes((section_id,)) + _encode_leb128_u32(len(payload)) + payload

    binary = (
        b"\x00asm\x01\x00\x00\x00"
        + section(1, type_payload)
        + section(3, function_payload)
        + section(10, code_payload)
    )
    module = parse(memoryview(binary), BumpAllocator(capacity=16 * 1024 * 1024))

    assert len(module.blocks) == 0xFFFF


def test_load_57_includes_runtime_api_operations_in_trace_span() -> None:
    wasm_bytes = wat_to_wasm(
        """(module
            (func (export "popcount") (result i32)
              (i32.popcnt (i32.const 240))))"""
    )
    module = parse_standalone(memoryview(wasm_bytes))

    function = module.functions[0]
    entry = module.get_block(function.code_pc_offset)
    assert entry is not None
    assert entry.func_index == 0
    assert entry.head_pc == function.code_pc_offset
    code = module.code_for(0)
    assert code[-1] == 0x0B  # END is loader metadata, outside the executable body span.
    assert entry.byte_span == len(code) - 1
    assert entry.interpreter_annotation_byte_offset == 0


def test_load_51_keeps_unreachable_polymorphism_inside_its_control_frame() -> None:
    wasm_bytes = wat_to_wasm("(module (func (unreachable) (block (drop (i32.eqz (nop))))))")
    with pytest.raises(InvalidWasm, match="WASM operand stack underflow"):
        parse_standalone(memoryview(wasm_bytes))


def test_load_52_rejects_custom_section_names_past_section_end() -> None:
    wasm_bytes = b"\x00asm\x01\x00\x00\x00\x00\x02\x03a"
    with pytest.raises(InvalidWasm, match="custom section name exceeds section bounds"):
        parse_standalone(memoryview(wasm_bytes))


def test_load_53_bounds_leb128_and_section_counts_before_storage_configuration() -> None:
    from tier2_runtime.wasm.leb128 import (
        decode_signed_full,
        decode_unsigned_full,
    )

    assert decode_unsigned_full(memoryview(b"\xff\xff\xff\xff\x0f"), 0) == (0xFFFF_FFFF, 5)
    assert decode_signed_full(memoryview(b"\x80\x80\x80\x80\x78"), 0, bits=32) == (
        -(1 << 31),
        5,
    )
    with pytest.raises(InvalidWasm, match="truncated unsigned LEB128"):
        decode_unsigned_full(memoryview(b"\x80"), 0)
    with pytest.raises(InvalidWasm, match="exceeds width"):
        decode_unsigned_full(memoryview(b"\xff\xff\xff\xff\x10"), 0)
    with pytest.raises(InvalidWasm, match="maximum 5 bytes"):
        decode_unsigned_full(memoryview(b"\x80\x80\x80\x80\x80\x00"), 0)

    over_capacity_types = b"\x00asm\x01\x00\x00\x00\x01\x02\x81\x02"
    with pytest.raises(InvalidWasm, match="exceeds configured maximum"):
        parse_standalone(memoryview(over_capacity_types))
    truncated_type_count = b"\x00asm\x01\x00\x00\x00\x01\x01\x80\x03\x01\x00"
    with pytest.raises(InvalidWasm, match="truncated unsigned LEB128"):
        parse_standalone(memoryview(truncated_type_count))


def test_load_55_parser_enforces_configured_wasm_memory_page_limit() -> None:
    over_limit = _build_test_wasm_binary(memory_pages=FB_CONF_MAX_WASM_PAGES + 1)
    with pytest.raises(InvalidWasm, match="memory minimum exceeds FB_CONF_MAX_WASM_PAGES"):
        parse_standalone(memoryview(over_limit))


def test_load_56_rejects_overaligned_memory_access() -> None:
    valid = parse_standalone(
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
    with pytest.raises(InvalidWasm, match="memory alignment exceeds the natural alignment"):
        parse_standalone(memoryview(invalid))


@pytest.mark.parametrize(
    "binary",
    (
        b"bad wasm",
        _single_void_function_module(b"\x41\x80"),
        _single_void_function_module(b"\x43\x00\x0b"),
        _single_void_function_module(b"\x3f"),
        _single_void_function_module(b"\xfc\x7f\x0b"),
        _single_void_function_module(b"\x6a\x0b"),
    ),
)
def test_load_59_invalid_binary_traps_with_assertions_disabled(binary: bytes) -> None:
    script = """
import sys
from tier2_runtime.wasm.reader import parse_standalone
from tier2_runtime.wasm.validation import InvalidWasm
from tier2_runtime.interpreter.errors import TrapCode
try:
    parse_standalone(memoryview(bytes.fromhex(sys.argv[1])))
except InvalidWasm as error:
    if error.code != TrapCode.INVALID_WASM_BINARY:
        raise SystemExit(2)
else:
    raise SystemExit(3)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    result = subprocess.run(
        [sys.executable, "-O", "-c", script, binary.hex()],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_load_60_preserves_internal_assertion_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from tier2_runtime.wasm import reader
    from tier2_runtime.wasm.module import Module

    allocator = BumpAllocator()
    watermark = allocator.offset
    failure = AssertionError("loader host contract")

    def fail_contract(_data: memoryview, target: BumpAllocator) -> Module:
        target.allocate(8, alignment=8)
        raise failure

    monkeypatch.setattr(reader, "_parse_with_allocator", fail_contract)
    with pytest.raises(AssertionError) as caught:
        parse(memoryview(b""), allocator)
    assert caught.value is failure
    assert allocator.offset == watermark


if __name__ == "__main__":
    raise SystemExit(pytest.main([str(_TEST_FILE)]))

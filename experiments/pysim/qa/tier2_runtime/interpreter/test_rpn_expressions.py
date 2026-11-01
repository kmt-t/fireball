"""RPN expression annotations (REQ-097 D): loader detection and the one-handler evaluator.

The expected values come from the reference interpreter, which runs the unmodified bytecode and
never reads the annotation expressions. The stack-invariance check uses values that stay on the
VM operand stack around the expression, so a moved stack pointer changes the result.
"""

from __future__ import annotations

import pytest
from qa.shared.helpers import make_interpreter as Interpreter
from qa.shared.helpers import make_native_interpreter, wat_to_wasm
from tier2_runtime.interpreter.control_flow import find_rpn_expressions
from tier2_runtime.wasm.reader import parse_standalone


def _loop_module(body: str):
    wat = f"""
    (module
      (func (export "f") (param i32 i32) (result i32) (local i32 i32)
        {body}
      )
    )
    """
    return parse_standalone(wat_to_wasm(wat))


def _function_code(module):
    function = module.functions[0]
    return module.source[function.code_offset : function.code_offset + function.code_size], function


def test_rpn_01_native_matches_reference_for_every_expression_operator():
    """TEST-RPN-01: each expression operator (add, sub, shl, shr_u) and a constant head agree."""
    body = """
        local.get 0 i32.const 5 i32.shl local.get 1 i32.sub local.set 2
        local.get 1 i32.const 3 i32.shr_u local.get 0 i32.add local.set 3
        local.get 2 local.get 3 i32.sub local.get 0 i32.add local.set 2
        local.get 2 local.get 3 i32.sub i32.const 7 i32.add local.set 3
        local.get 3 local.get 2 i32.add
    """
    module = _loop_module(body)
    native = make_native_interpreter(module)
    reference = Interpreter(module)
    for a, b in [(0, 0), (1, 2), (7, 9), (0xFFFF_FFFF, 1), (123456, 0xDEAD), (5, 0xFFFF_FFF0)]:
        expected = reference.call(0, [a, b]).unwrap()
        assert native.call(0, [a, b]).unwrap() == expected


def test_rpn_02_vm_stack_pointer_is_unchanged_across_an_expression():
    """TEST-RPN-02: values below the expression survive it, so the VM stack pointer did not move."""
    body = """
        i32.const 7
        local.get 0 i32.const 3 i32.add local.set 2
        local.get 1 i32.const 2 i32.shl local.set 3
        local.get 2 i32.add
        local.get 3 i32.add
    """
    module = _loop_module(body)
    native = make_native_interpreter(module)
    reference = Interpreter(module)
    for a, b in [(1, 2), (100, 200), (0, 0xFFFF_FFFF)]:
        expected = reference.call(0, [a, b]).unwrap()
        assert native.call(0, [a, b]).unwrap() == expected


def test_rpn_03_loader_finds_expressions_and_bounds_them_by_block_and_capacity():
    """TEST-RPN-03: detection follows the ISA; a head at a block start is allowed, a block head
    inside a run splits it, and a depth above the function's stack capacity is rejected."""
    body = """
        (block $exit (loop $loop
          local.get 2 local.get 1 i32.add local.set 2
          local.get 1 i32.const 1 i32.add local.set 1
          local.get 1 local.get 0 i32.gt_s br_if $exit
          br $loop))
        local.get 2
    """
    module = _loop_module(body)
    code, function = _function_code(module)
    from tier2_runtime.interpreter.control_flow import extract_basic_blocks

    heads = [block[0] for block in extract_basic_blocks(code, function.control_map, pc_base=0)]
    expressions = find_rpn_expressions(
        code, heads, function.local_layout_cache, function.max_operand_stack_words
    )
    # Two END expressions (local.set) and one END_STACK expression (the compare before br_if).
    assert len(expressions) == 3
    end_expressions = [expression for expression in expressions if expression.writes_local]
    stack_expressions = [expression for expression in expressions if not expression.writes_local]
    assert len(end_expressions) == 2 and len(stack_expressions) == 1
    for expression in end_expressions:
        assert expression.max_depth == 2
        assert expression.code_length == expression.set_offset + 2 - expression.head_offset
    assert stack_expressions[0].max_depth == 2
    assert (
        stack_expressions[0].resume_offset
        == stack_expressions[0].head_offset + stack_expressions[0].code_length
    )
    # A stack capacity of 1 cannot hold the depth-2 expressions.
    assert find_rpn_expressions(code, heads, function.local_layout_cache, 1) == ()


I32_BINARY_OPERATORS = (
    "i32.add",
    "i32.sub",
    "i32.mul",
    "i32.and",
    "i32.or",
    "i32.xor",
    "i32.shl",
    "i32.shr_s",
    "i32.shr_u",
    "i32.rotl",
    "i32.rotr",
    "i32.eq",
    "i32.ne",
    "i32.lt_s",
    "i32.lt_u",
    "i32.gt_s",
    "i32.gt_u",
    "i32.le_s",
    "i32.le_u",
    "i32.ge_s",
    "i32.ge_u",
)
I32_UNARY_OPERATORS = ("i32.eqz", "i32.clz", "i32.ctz", "i32.popcnt")
I32_CHECKED_OPERATORS = ("i32.div_s", "i32.div_u", "i32.rem_s", "i32.rem_u")
I32_OPERAND_PAIRS = [
    (0, 0),
    (1, 2),
    (7, 9),
    (0xFFFF_FFFF, 1),
    (123456, 0xDEAD),
    (5, 0xFFFF_FFF0),
    (0x8000_0000, 0xFFFF_FFFF),
    (0x8000_0000, 31),
    (3, 33),
    (0, 0x8000_0000),
]


def _operator_module(operator: str):
    if operator in I32_UNARY_OPERATORS:
        body = f"local.get 0 {operator} local.set 2 local.get 2"
    else:
        body = f"local.get 0 local.get 1 {operator} local.set 2 local.get 2"
    return _loop_module(body)


@pytest.mark.parametrize(
    "operator", I32_BINARY_OPERATORS + I32_UNARY_OPERATORS + I32_CHECKED_OPERATORS
)
def test_rpn_04_every_i32_operator_matches_reference(operator: str):
    """TEST-RPN-04: each i32 operator of the expression ISA agrees with the reference, including
    the shift counts modulo 32, the signed and unsigned comparisons, and the rotations."""
    module = _operator_module(operator)
    code, function = _function_code(module)
    from tier2_runtime.interpreter.control_flow import extract_basic_blocks

    heads = [block[0] for block in extract_basic_blocks(code, function.control_map, pc_base=0)]
    assert find_rpn_expressions(
        code, heads, function.local_layout_cache, function.max_operand_stack_words
    )
    native = make_native_interpreter(module)
    reference = Interpreter(module)
    arguments = (
        [[a] for a, _ in I32_OPERAND_PAIRS]
        if operator in I32_UNARY_OPERATORS
        else I32_OPERAND_PAIRS
    )
    for values in arguments:
        values = list(values) if len(values) == 2 else [values[0], 1]
        expected = reference.call(0, values)
        actual = native.call(0, values)
        assert actual.is_ok == expected.is_ok
        if expected.is_ok:
            assert actual.unwrap() == expected.unwrap()
        else:
            assert actual.error.code == expected.error.code


@pytest.mark.parametrize("operator", I32_CHECKED_OPERATORS)
def test_rpn_05_trapping_operators_report_the_reference_trap(operator: str):
    """TEST-RPN-05: a division by zero and the signed overflow trap with the reference trap code,
    and the operator stops the following effects."""
    module = _operator_module(operator)
    native = make_native_interpreter(module)
    reference = Interpreter(module)
    for values in ([7, 0], [0x8000_0000, 0xFFFF_FFFF], [9, 2]):
        expected = reference.call(0, values)
        actual = native.call(0, values)
        assert actual.is_ok == expected.is_ok
        if expected.is_ok:
            assert actual.unwrap() == expected.unwrap()
        else:
            assert actual.error.code == expected.error.code


def test_rpn_06_end_stack_hands_the_temporaries_to_the_vm_stack():
    """TEST-RPN-06: an expression that ends before a call leaves its value on the VM stack, the
    call consumes it, and the value below the expression survives."""
    body = """
        i32.const 7
        local.get 0 i32.const 3 i32.add
        call $twice
        i32.add
    """
    wat = f"""
    (module
      (func $twice (param i32) (result i32) local.get 0 i32.const 2 i32.mul)
      (func (export "f") (param i32 i32) (result i32) (local i32 i32)
        {body}
      )
    )
    """
    module = parse_standalone(wat_to_wasm(wat))
    native = make_native_interpreter(module)
    reference = Interpreter(module)
    function = module.functions[1]
    code = module.source[function.code_offset : function.code_offset + function.code_size]
    from tier2_runtime.interpreter.control_flow import extract_basic_blocks

    heads = [block[0] for block in extract_basic_blocks(code, function.control_map, pc_base=0)]
    stack_expressions = [
        expression
        for expression in find_rpn_expressions(
            code, heads, function.local_layout_cache, function.max_operand_stack_words
        )
        if not expression.writes_local
    ]
    assert stack_expressions, "the expression before the call must end with END_STACK"
    for a, b in [(1, 2), (100, 200), (0, 0xFFFF_FFFF)]:
        expected = reference.call(1, [a, b]).unwrap()
        assert native.call(1, [a, b]).unwrap() == expected


SPECIALISED_CASES = (
    # (form index in RPN_SPECIALISED_FORMS, body)
    (0, "local.get 0 local.get 1 i32.mul i32.const 7 i32.add local.set 2 local.get 2"),
    (
        1,
        "local.get 0 local.get 1 i32.mul i32.const 2 i32.shr_s i32.const 3 i32.add local.set 2 local.get 2",
    ),
    (2, "local.get 0 i32.const 3 i32.shr_u local.set 2 local.get 2"),
    (3, "local.get 0 local.get 1 i32.sub local.set 2 local.get 2"),
    (4, "local.get 0 local.get 1 i32.add i32.const 5 i32.mul local.set 2 local.get 2"),
    (5, "local.get 0 local.get 1 i32.mul local.get 0 i32.sub local.set 2 local.get 2"),
    (
        6,
        "local.get 0 i32.const 3 i32.gt_s i32.const 1 i32.add if (result i32) i32.const 9 else i32.const 0 end",
    ),
    (
        7,
        "local.get 0 i32.const 3 i32.lt_s i32.const 1 i32.add if (result i32) i32.const 9 else i32.const 0 end",
    ),
    (
        8,
        "local.get 0 local.get 1 i32.gt_u i32.const 1 i32.add if (result i32) i32.const 9 else i32.const 0 end",
    ),
    # destination VM: the operator ends the expression, the VM takes its result
    (
        9,
        "local.get 0 local.get 1 i32.mul i32.const 2 i32.shr_s if (result i32) i32.const 9 else i32.const 0 end",
    ),
    (10, "local.get 0 i32.const 3 i32.lt_s if (result i32) i32.const 9 else i32.const 0 end"),
    (11, "local.get 0 i32.const 3 i32.gt_s if (result i32) i32.const 9 else i32.const 0 end"),
    (
        12,
        "local.get 0 local.get 1 i32.lt_s i32.const 1 i32.add if (result i32) i32.const 9 else i32.const 0 end",
    ),
    (
        13,
        "local.get 0 i32.const 3 i32.shr_u i32.const 1 i32.add if (result i32) i32.const 9 else i32.const 0 end",
    ),
    (
        14,
        "local.get 0 i32.const 3 i32.shl i32.const 1 i32.add if (result i32) i32.const 9 else i32.const 0 end",
    ),
    (
        15,
        "local.get 0 i32.const 3 i32.shr_s i32.const 1 i32.add if (result i32) i32.const 9 else i32.const 0 end",
    ),
    (16, "local.get 0 i32.const 3 i32.add local.set 2 local.get 2"),
    (17, "local.get 0 local.get 1 i32.mul local.get 0 i32.add local.set 2 local.get 2"),
)


def _element_codes(elements: bytes) -> list[int]:
    """The element codes of one program, walked with each element's own length."""
    from tier2_runtime.wasm.opcodes import RPN_ELEM_SPEC_BASE, RPN_SPECIALISED_FORMS

    codes: list[int] = []
    cursor = 0
    while cursor < len(elements):
        code = elements[cursor]
        codes.append(code)
        if code == 0x00:
            cursor += 3
        elif code == 0x01:
            cursor += 5
        elif 0x10 <= code < 0x10 + 25:
            cursor += 2 if code - 0x10 in (3, 4, 5, 6) else 1
        elif 0x40 <= code < 0x44:
            cursor += 1
        elif RPN_ELEM_SPEC_BASE <= code < RPN_ELEM_SPEC_BASE + len(RPN_SPECIALISED_FORMS):
            _kind, left, right, dest = RPN_SPECIALISED_FORMS[code - RPN_ELEM_SPEC_BASE]
            size = 1 + (2 if left == "L" else 4 if left == "I" else 0)
            size += 2 if right == "L" else 4 if right == "I" else 0
            cursor += size + (2 if dest == "L" else 0)
        else:
            raise AssertionError(f"unexpected element code {code:#x}")
    return codes


@pytest.mark.parametrize("form_index, body", SPECIALISED_CASES)
def test_rpn_07_specialised_forms_match_reference(form_index: int, body: str):
    """TEST-RPN-07: each specialised form is emitted for its operand kinds and destination, and the
    native result agrees with the reference."""
    from tier2_runtime.interpreter.control_flow import extract_basic_blocks
    from tier2_runtime.wasm.opcodes import RPN_ELEM_SPEC_BASE

    module = _loop_module(body)
    code, function = _function_code(module)
    heads = [block[0] for block in extract_basic_blocks(code, function.control_map, pc_base=0)]
    expressions = find_rpn_expressions(
        code, heads, function.local_layout_cache, function.max_operand_stack_words
    )
    emitted = [code for expression in expressions for code in _element_codes(expression.elements)]
    assert RPN_ELEM_SPEC_BASE + form_index in emitted
    native = make_native_interpreter(module)
    reference = Interpreter(module)
    for a, b in [
        (0, 0),
        (1, 2),
        (7, 9),
        (0xFFFF_FFFF, 1),
        (123456, 0xDEAD),
        (5, 0xFFFF_FFF0),
        (3, 3),
    ]:
        expected = reference.call(0, [a, b]).unwrap()
        assert native.call(0, [a, b]).unwrap() == expected

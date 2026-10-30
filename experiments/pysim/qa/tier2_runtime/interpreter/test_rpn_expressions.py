"""RPN expression annotations (REQ-097 D): loader detection and the one-handler evaluator.

The expected values come from the reference interpreter, which runs the unmodified bytecode and
never reads the annotation expressions. The stack-invariance check uses values that stay on the
VM operand stack around the expression, so a moved stack pointer changes the result.
"""

from __future__ import annotations

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
    assert len(expressions) == 2
    for expression in expressions:
        assert expression.max_depth == 2
        assert expression.code_length == expression.set_offset + 2 - expression.head_offset
    # A stack capacity of 1 cannot hold the depth-2 expression.
    assert find_rpn_expressions(code, heads, function.local_layout_cache, 1) == ()

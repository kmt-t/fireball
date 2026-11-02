"""Observe real QA setup state in a plain, non-rewritten Python process."""

from __future__ import annotations

import json
import sys

import wasmtime
from qa.shared.jit_cache import HotspotBitmap
from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.runtime_support import compile_module_block
from qa.shared.x64_jit import TraceCompiler
from qa.tier2_runtime.interpreter.test_cps_interpreter import _run_step
from qa.tier3_plugins.debugger.test_debugger import _make_register_test_view
from tier2_runtime.abi.interpreter_abi import ExecutionContextABI, NativeValueStack
from tier2_runtime.wasm.reader import parse_standalone


def main() -> None:
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    context = ExecutionContextABI()
    code = memoryview(bytearray((0x41, 3, 0x41, 4, 0x6A, 0x0B)))
    result = _run_step(code, context, stack, locals_stack, 0, stack.capacity, 0)
    registers = _make_register_test_view()
    bitmap = HotspotBitmap(card_shift=2, code_lengths=(256,), function_pc_bases=(0,))
    # Two straight-line loader blocks: the first falls through to the second.
    module = parse_standalone(
        wasmtime.wat2wasm(
            '(module (func (export "f") (block i32.const 1 drop) (block i32.const 2 drop)'
            " (block i32.const 3 drop)))"
        )
    )
    manager = JITRuntimeManager(bank_capacity=512)
    manager.register_module(module)
    cache = manager.cache
    first, second = (compile_module_block(TraceCompiler(), module, block) for block in module.blocks[:2])
    first_inserted = cache.insert(first)
    second_inserted = cache.insert(second)
    print(
        json.dumps(
            {
                "optimization": sys.flags.optimize,
                "outcome": result,
                "top": stack.raw_at(0),
                "registers": [
                    registers.locals[:3],
                    len(registers.stack),
                    registers.stack.raw_at(0),
                ],
                "jit_cache": {
                    "card_count": bitmap.card_count,
                    "inserted": [first_inserted, second_inserted],
                    "head_pcs": [first.head_pc, second.head_pc],
                    "chain_targets": [first.chain_next, second.chain_next],
                    "chain_body_counts": [
                        first.header._native.chain_body_count,
                        second.header._native.chain_body_count,
                    ],
                },
            }
        )
    )


if __name__ == "__main__":
    main()

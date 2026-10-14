"""Observe real QA setup state in a plain, non-rewritten Python process."""

from __future__ import annotations

import json
import sys

from qa.shared.jit_cache import HotspotBitmap, JitRuntimeBoundary, JITTrace
from qa.tier2_runtime.interpreter.test_cps_interpreter import _run_step
from qa.tier3_plugins.debugger.test_debugger import _make_register_test_view
from tier2_runtime.abi.interpreter_abi import ExecutionContextABI, NativeValueStack


def main() -> None:
    stack = NativeValueStack()
    locals_stack = NativeValueStack()
    context = ExecutionContextABI()
    code = memoryview(bytearray((0x41, 3, 0x41, 4, 0x6A, 0x0B)))
    result = _run_step(code, context, stack, locals_stack, 0, stack.capacity, 0)
    registers = _make_register_test_view()
    bitmap = HotspotBitmap(card_shift=2, code_lengths=(256,), function_pc_bases=(0,))
    cache = JitRuntimeBoundary(bank_capacity=512)
    first = JITTrace(head_pc=0x100, size_bytes=64, next_pc=0x200)
    second = JITTrace(head_pc=0x200, size_bytes=64, next_pc=0x100)
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
                    "cycle_links": [first.chain_next, second.chain_next],
                    "cycle_body_counts": [
                        first.header._native.chain_body_count,
                        second.header._native.chain_body_count,
                    ],
                },
            }
        )
    )


if __name__ == "__main__":
    main()

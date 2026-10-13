"""Observe real QA setup state in a plain, non-rewritten Python process."""

from __future__ import annotations

import json
import sys

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
            }
        )
    )


if __name__ == "__main__":
    main()

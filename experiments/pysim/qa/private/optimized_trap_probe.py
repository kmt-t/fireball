"""Observe guest failures as results in plain optimized Python processes."""

from __future__ import annotations

import json
import sys

from qa.shared.helpers import make_interpreter, make_native_interpreter, wat_to_wasm
from tier2_runtime.wasm.reader import parse_standalone


def main() -> None:
    cases = (
        (
            "unreachable",
            '(module (memory 1) (func (export "main") unreachable i32.const 0 i32.const 99 i32.store))',
        ),
        (
            "divide",
            '(module (memory 1) (func (export "main") i32.const 1 i32.const 0 i32.div_s drop i32.const 0 i32.const 99 i32.store))',
        ),
        (
            "memory",
            '(module (memory 1) (func (export "main") i32.const 65535 i32.load drop i32.const 0 i32.const 99 i32.store))',
        ),
        (
            "conversion",
            '(module (memory 1) (func (export "main") f64.const nan i32.trunc_f64_s drop i32.const 0 i32.const 99 i32.store))',
        ),
        (
            "start",
            '(module (memory 1) (func $init unreachable) (start $init) (func (export "main") i32.const 0 i32.const 99 i32.store))',
        ),
    )
    outcomes = []
    for name, wat in cases:
        for constructor in (make_interpreter, make_native_interpreter):
            module = parse_standalone(wat_to_wasm(wat))
            memory = bytearray(65536)
            interpreter = constructor(module, memory=memory)
            result = interpreter.call(module.export_func_index("main"), ())
            outcomes.append(
                [
                    name,
                    result.is_ok,
                    None if result.error is None else int(result.error.code),
                    list(memory[:4]),
                ]
            )
    print(json.dumps({"optimization": sys.flags.optimize, "outcomes": outcomes}))


if __name__ == "__main__":
    main()

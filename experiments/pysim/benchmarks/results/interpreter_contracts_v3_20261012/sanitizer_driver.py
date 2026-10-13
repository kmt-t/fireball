"""計時用ライブラリを置き換えず、検査ビルドで関連テストを実行する。"""

from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

_load_library = ctypes.PyDLL


def load_library(name: str | None, *args, **kwargs):
    if name is not None and Path(name).name == "libnative_interpreter.so":
        name = "/tmp/fireball-contracts-v3/libinterpreter_sanitized.so"
    return _load_library(name, *args, **kwargs)


ctypes.PyDLL = load_library
raise SystemExit(
    pytest.main(
        [
            "-q",
            "experiments/pysim/qa/tier2_runtime/interpreter/test_interpreter.py",
            "experiments/pysim/qa/tier2_runtime/interpreter/test_cps_interpreter.py",
            "experiments/pysim/qa/tier2_runtime/test_wasm_reader.py",
            "-k",
            "wasm_runtime_traps_keep_code or typed_block_results or native_cps_entry "
            "or native_shared_stack or native_const_capacity or maximum_operand_stack or function_entry_capacity or callee_capacity",
        ]
    )
)

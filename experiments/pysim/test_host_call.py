"""
experiments/pysim/test_host_call.py

Tests host-function dispatch via pure trace JIT compiler (TraceCompiler).
Verifies native execution of host-calls across 0 to 6 arguments (fireball-call6 max).
"""

from __future__ import annotations

import ctypes

from runtime_engine import BasicBlock, WASMContext
from x64_jit import TraceCompiler

HOST_FUNC_T = ctypes.CFUNCTYPE(
    ctypes.c_uint32, *([ctypes.c_uint32] * 7)
)


def _run_with_host_call(nparams: int, arg_values: list[int]) -> tuple[int, list[int]]:
    """Returns (jit_result, args_the_host_function_actually_received)."""
    received: list[int] = []

    def host_fn(*args):
        received.extend(args[:nparams])
        return sum(v * (i + 1) for i, v in enumerate(args[:nparams])) & 0xFFFFFFFF

    def host_wrapper():
        return host_fn(*arg_values)

    trampoline = ctypes.CFUNCTYPE(ctypes.c_uint32)(host_wrapper)
    trampoline_addr = ctypes.cast(trampoline, ctypes.c_void_p).value

    block = BasicBlock(
        head_pc=0x100,
        ops=[
            ("call_host", trampoline_addr),
            ("local.set", 0),
        ],
        next_pc=None,
    )

    compiler = TraceCompiler(host_trampolines={1: trampoline_addr})
    trace = compiler.compile_trace(0x100, block)
    ctx = WASMContext(locals_values=[0])
    trace.native_fn(ctx)

    return ctx.locals[0], received


def test_0_params():
    res, received = _run_with_host_call(0, [])
    assert received == []
    assert res == 0


def test_1_param():
    res, received = _run_with_host_call(1, [42])
    assert received == [42]
    assert res == 42


def test_2_params():
    res, received = _run_with_host_call(2, [10, 20])
    assert received == [10, 20]
    assert res == 10 * 1 + 20 * 2  # 50


def test_4_params():
    res, received = _run_with_host_call(4, [1, 2, 3, 4])
    assert received == [1, 2, 3, 4]
    assert res == 1 * 1 + 2 * 2 + 3 * 3 + 4 * 4  # 30


def test_6_params_fireball_call6_max():
    res, received = _run_with_host_call(6, [10, 20, 30, 40, 50, 60])
    assert received == [10, 20, 30, 40, 50, 60]
    expected = 10 * 1 + 20 * 2 + 30 * 3 + 40 * 4 + 50 * 5 + 60 * 6  # 910
    assert res == expected


ALL_TESTS = [
    test_0_params,
    test_1_param,
    test_2_params,
    test_4_params,
    test_6_params_fireball_call6_max,
]

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"\n[PASS] All {len(ALL_TESTS)} host call tests passed.")

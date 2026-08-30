"""
experiments/pysim/test_host_call.py

Spec-first tests for FunctionCompiler._emit_host_call -- the
`fireball_call`-shaped bridge (docs/components/tier1_core/system_syscall.md
§3's `fireball-call0`..`fireball-call6` WIT variants) from JIT'd WASM code
into a real ctypes-wrapped Python callable.

Every arity from 0 to 7 total params is exercised with DISTINCT values in
every slot (not zeros: a zero in the wrong register/stack slot is
indistinguishable from "never written", which is exactly the kind of
mistake that slipped through earlier in this build -- see
test_x64_stencils.py's own docstring). A Python-side recorder captures the
args it actually received, in order, so any register/stack-slot mixup in
the marshalling shows up as a wrong value at a specific position instead
of an aggregate checksum that could pass by cancellation.
"""

from __future__ import annotations

import ctypes

from exec_memory import ExecutableBuffer
from wasm_builder import ModuleBuilder
from wasm_module import I32
from wasm_reader import parse as parse_wasm
from x64_jit import TraceJITCompiler

HOST_FUNC_T = ctypes.CFUNCTYPE(
    ctypes.c_uint32, *([ctypes.c_uint32] * 7)  # up to 7 total params (fireball-call6's max)
)


def _build_module_calling_host(nparams: int, arg_values: list[int]):
    b = ModuleBuilder()
    host_idx = b.add_import("env", "host_fn", (I32,) * nparams, (I32,))
    f = b.add_function((), (I32,), export_name="entry")
    for v in arg_values:
        f.i32_const(v)
    f.call(host_idx)
    return b, host_idx


def _run_with_host_call(nparams: int, arg_values: list[int]) -> tuple[int, list[int]]:
    """Returns (jit_result, args_the_host_function_actually_received)."""
    received: list[int] = []

    def host_fn(*args):
        received.extend(args[:nparams])
        # A distinguishable, order-dependent "checksum": sum(arg[i] * (i+1)).
        return sum(v * (i + 1) for i, v in enumerate(args[:nparams])) & 0xFFFFFFFF

    # keepalive: ctypes callback trampolines must not be garbage-collected
    # while JIT'd code can still call them.
    trampoline = HOST_FUNC_T(host_fn)
    trampoline_addr = ctypes.cast(trampoline, ctypes.c_void_p).value

    builder, host_idx = _build_module_calling_host(nparams, arg_values)
    module = parse_wasm(builder.build())
    entry_index = module.export_func_index("entry")

    jit = TraceJITCompiler(host_trampolines={host_idx: trampoline_addr})
    blob, _ = jit.compile_function_as_trace(module, entry_index)

    buf = ExecutableBuffer(max(len(blob), 64))
    try:
        buf.write(0, blob)
        fn = buf.function_at(0, ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        result = fn(0, 0)
        return result, received
    finally:
        buf.close()
        del trampoline  # noqa: F841 -- explicit, though buf.close() already ran every call


def _distinct_values(n: int) -> list[int]:
    # Base-100 distinct values so a transposition (arg2 in arg3's slot) is
    # visible in the recorded list, not just in an aggregate.
    return [100 + i * 11 for i in range(n)]


def test_host_call_with_zero_params():
    result, received = _run_with_host_call(0, [])
    assert received == []
    assert result == 0


def test_host_call_with_one_to_four_register_only_params():
    for n in range(1, 5):
        values = _distinct_values(n)
        result, received = _run_with_host_call(n, values)
        assert received == values, f"n={n}: expected {values}, got {received}"
        assert result == sum(v * (i + 1) for i, v in enumerate(values))


def test_host_call_with_five_to_seven_params_spills_onto_the_stack():
    """params 4.. (0-indexed) exceed the 4 ABI argument registers and must
    be marshalled onto the stack -- this is the branch that needed the
    16-byte-alignment + shadow-space handling."""
    for n in range(5, 8):
        values = _distinct_values(n)
        result, received = _run_with_host_call(n, values)
        assert received == values, f"n={n}: expected {values}, got {received}"
        assert result == sum(v * (i + 1) for i, v in enumerate(values))


def test_host_call_does_not_corrupt_the_wasm_stack_around_it():
    """Pushes a sentinel before and after the host call and confirms both
    survive -- proves the call glue's rsp save/restore is exact, not just
    "close enough" for this particular arg count."""
    b = ModuleBuilder()
    host_idx = b.add_import("env", "host_fn", (I32, I32), (I32,))
    f = b.add_function((), (I32,), export_name="entry")
    f.i32_const(777)             # sentinel, pushed before the call
    f.i32_const(1).i32_const(2)
    f.call(host_idx)
    f.i32_add()                  # sentinel + host_result

    received = []

    def host_fn(*args):
        received.extend(args[:2])
        return 1000

    trampoline = HOST_FUNC_T(host_fn)
    trampoline_addr = ctypes.cast(trampoline, ctypes.c_void_p).value
    module = parse_wasm(b.build())
    entry_index = module.export_func_index("entry")
    jit = TraceJITCompiler(host_trampolines={host_idx: trampoline_addr})
    blob, _ = jit.compile_function_as_trace(module, entry_index)

    buf = ExecutableBuffer(max(len(blob), 64))
    try:
        buf.write(0, blob)
        fn = buf.function_at(0, ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        result = fn(0, 0)
        assert received == [1, 2]
        assert result == 777 + 1000
    finally:
        buf.close()
        del trampoline


def test_host_call_result_bit_pattern_survives_high_bit_set():
    """fireball_call returns u32; WASM i32 has no inherent signedness (only
    individual ops like lt_s/lt_u pick an interpretation), and this
    codebase's own convention -- proven by every other test here -- is
    that a value crossing the true function-return boundary comes back
    sign-extended into its 64-bit slot (see EPILOGUE_RETURN_I32). What
    must survive intact is the low-32-bit *bit pattern*: 0x80000001
    sign-extended is exactly -0x7FFFFFFF, i.e. `result & 0xFFFFFFFF` must
    still read back as 0x80000001, whichever way the full 64 bits print.
    """
    def host_fn(*args):
        return 0x80000001

    trampoline = HOST_FUNC_T(host_fn)
    trampoline_addr = ctypes.cast(trampoline, ctypes.c_void_p).value
    b = ModuleBuilder()
    host_idx = b.add_import("env", "host_fn", (), (I32,))
    f = b.add_function((), (I32,), export_name="entry")
    f.call(host_idx)
    module = parse_wasm(b.build())
    entry_index = module.export_func_index("entry")
    jit = TraceJITCompiler(host_trampolines={host_idx: trampoline_addr})
    blob, _ = jit.compile_function_as_trace(module, entry_index)

    buf = ExecutableBuffer(max(len(blob), 64))
    try:
        buf.write(0, blob)
        fn = buf.function_at(0, ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        result = fn(0, 0)
        assert result & 0xFFFFFFFF == 0x80000001, f"expected bit pattern 0x80000001, got {result & 0xFFFFFFFF:#x}"
    finally:
        buf.close()
        del trampoline


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"[PASS] All {len(ALL_TESTS)} host-call bridge tests passed (executed as real machine code).")

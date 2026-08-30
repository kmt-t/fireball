"""
experiments/pysim/test_x64_stencils.py
Spec-first tests for x64_stencils.py: each stencil is assembled into a real
executable buffer and actually run on the CPU with controlled inputs, and
the result is checked against a value computed independently in Python from
the *semantic* spec (WASM i32 wraparound, signed/unsigned comparison,
32-bit truncating shifts, ...) -- never against a second hand-derived hex
string, since hand-deriving the same encoding twice reproduces the same
mistake twice. This is what caught four real REX-byte / relocation-offset
bugs during development; run this file whenever a stencil changes.
Each test builds PROLOGUE + <stencil(s) under test, with relocations
patched> + EPILOGUE_RETURN_I32, executes it via ctypes, and asserts on the
real return value.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import ctypes
import random

import x64_stencils as st
from exec_memory import ExecutableBuffer

I32_MASK = 0xFFFFFFFF


def _to_i32(v: int) -> int:
    v &= I32_MASK
    return v - (1 << 32) if v & 0x8000_0000 else v


def _u32(v: int) -> int:
    return v & I32_MASK


def patch(code: bytearray, base: int, stencil: st.Stencil, reloc_name: str, value: int) -> None:
    # Every relocation this codebase uses is 4 bytes except globals'
    # absolute-address "addr" slots, which are a full 64-bit pointer.
    width = 8 if reloc_name == "addr" else 4
    off = base + stencil.relocs[reloc_name]
    code[off : off + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")


def emit(code: bytearray, stencil: st.Stencil, **patches: int) -> int:
    """
    Appends `stencil` to `code`, patches any given relocations, and
        returns the base offset it was written at (for patches that need to be
        computed after the fact, e.g. branch targets in the JIT proper).
    """

    base = len(code)
    code += stencil.code
    for name, value in patches.items():
        patch(code, base, stencil, name, value)
    return base


def run_i32(
    body_stencils_with_patches: list[tuple[st.Stencil, dict]],
    locals_values: list[int] | None = None,
    memory: bytearray | None = None,
) -> int:

    code = bytearray()
    code += st.PROLOGUE.code
    for stencil, patches in body_stencils_with_patches:
        emit(code, stencil, **patches)

    code += st.EPILOGUE_RETURN_I32.code
    buf = ExecutableBuffer(max(len(code), 64))
    try:
        buf.write(0, bytes(code))
        n_locals = max(len(locals_values or []), 1)
        LocalsArray = ctypes.c_int64 * n_locals
        locals_arr = LocalsArray(*[0] * n_locals)
        for i, v in enumerate(locals_values or []):
            locals_arr[i] = v

        mem_ptr = 0
        c_mem = None
        if memory is not None:
            c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
            mem_ptr = ctypes.addressof(c_mem)

        fn = buf.function_at(0, ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        return fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(mem_ptr))
    finally:
        buf.close()


def run_i32_checked(
    body_stencils_with_patches: list[tuple[st.Stencil, dict]],
    locals_values: list[int] | None = None,
    memory: bytearray | None = None,
) -> int:
    """
    Like run_i32, but any memory-access stencil's "trap" relocation left
        unspecified in `body_stencils_with_patches` is wired to a real trap
        stub placed after the epilogue -- an out-of-bounds access genuinely
        raises `OSError` (a real access violation) instead of silently landing
        on whatever bytes happen to follow.
    """
    code = bytearray()
    code += st.PROLOGUE.code
    trap_relocs = []
    for stencil, patches in body_stencils_with_patches:
        base = emit(code, stencil, **patches)
        if "trap" in stencil.relocs and "trap" not in patches:
            trap_relocs.append(base + stencil.relocs["trap"])

    code += st.EPILOGUE_RETURN_I32.code
    trap_offset = len(code)
    code += st.TRAP.code
    for reloc in trap_relocs:
        rel = trap_offset - (reloc + 4)
        code[reloc : reloc + 4] = (rel & 0xFFFFFFFF).to_bytes(4, "little")

    buf = ExecutableBuffer(max(len(code), 64))
    try:
        buf.write(0, bytes(code))
        n_locals = max(len(locals_values or []), 1)
        LocalsArray = ctypes.c_int64 * n_locals
        locals_arr = LocalsArray(*[0] * n_locals)
        for i, v in enumerate(locals_values or []):
            locals_arr[i] = v

        mem_ptr = 0
        c_mem = None
        if memory is not None:
            c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
            mem_ptr = ctypes.addressof(c_mem)

        fn = buf.function_at(0, ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        return fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(mem_ptr))
    finally:
        buf.close()


def const_(v: int) -> tuple[st.Stencil, dict]:
    return (st.I32_CONST, {"imm": v})


def push_two(a: int, b: int) -> list[tuple[st.Stencil, dict]]:
    return [const_(a), const_(b)]


# ---------------------------------------------------------------------------
# prologue/epilogue plumbing
# ---------------------------------------------------------------------------


def test_prologue_epilogue_passthrough_constant():
    assert run_i32([const_(42)]) == 42
    assert run_i32([const_(-7)]) == -7


def test_epilogue_sign_extends_negative_i32_into_the_i64_return_value():
    result = run_i32([const_(-1)])
    assert result == -1, "a naive zero-extend would return 0xFFFFFFFF (4294967295) instead"


# ---------------------------------------------------------------------------
# locals
# ---------------------------------------------------------------------------


def test_local_get_reads_the_correct_slot_by_index():
    code = [(st.LOCAL_GET, {"disp": 1 * 8})]
    assert run_i32(code, locals_values=[111, 222, 333]) == 222


def test_local_set_writes_the_correct_slot_and_leaves_stack_empty_of_it():
    # local[0] = 99; return local[0]
    code = [const_(99), (st.LOCAL_SET, {"disp": 0}), (st.LOCAL_GET, {"disp": 0})]
    assert run_i32(code, locals_values=[0]) == 99


def test_local_tee_writes_the_slot_but_also_leaves_the_value_on_the_stack():
    # local[0] = tee(77)  -- the teed value is the function's return value directly
    code = [const_(77), (st.LOCAL_TEE, {"disp": 0})]
    assert run_i32(code, locals_values=[0]) == 77
    # and it must have actually written the local, not just left a stray push:
    code2 = [
        const_(77),
        (st.LOCAL_TEE, {"disp": 0}),
        (st.DROP, {}),
        (st.LOCAL_GET, {"disp": 0}),
    ]
    assert run_i32(code2, locals_values=[0]) == 77


def test_local_get_set_tee_at_a_nonzero_locals_array_offset():
    """
    Regression test for the disp-offset-off-by-one bugs found by hand
        audit: local index 2 must resolve to byte offset 16, not some
        off-by-a-few-bytes address inside the instruction encoding itself.
    """

    code = [
        const_(555),
        (st.LOCAL_SET, {"disp": 2 * 8}),
        (st.LOCAL_GET, {"disp": 2 * 8}),
    ]
    assert run_i32(code, locals_values=[0, 0, 0, 0]) == 555


# ---------------------------------------------------------------------------
# arithmetic (checked against Python's own i32 wraparound arithmetic)
# ---------------------------------------------------------------------------


def test_i32_add_wraps_at_32_bits_like_real_wasm_i32():
    a, b = 0x7FFFFFFF, 1
    assert run_i32([*push_two(a, b), (st.I32_ADD, {})]) == _to_i32(a + b)
    assert _to_i32(a + b) == -0x80000000  # sanity: this really does overflow


def test_i32_sub_and_mul_match_python_reference():
    a, b = -5, 17
    assert run_i32([*push_two(a, b), (st.I32_SUB, {})]) == _to_i32(a - b)
    assert run_i32([*push_two(a, b), (st.I32_MUL, {})]) == _to_i32(a * b)


def test_i32_bitwise_ops_match_python_reference():
    a, b = 0x0F0F0F0F, 0x00FFFF00
    assert run_i32([*push_two(a, b), (st.I32_AND, {})]) == _to_i32(a & b)
    assert run_i32([*push_two(a, b), (st.I32_OR, {})]) == _to_i32(a | b)
    assert run_i32([*push_two(a, b), (st.I32_XOR, {})]) == _to_i32(a ^ b)


def test_i32_div_and_rem_signed_and_unsigned():
    assert run_i32([*push_two(7, 2), (st.I32_DIV_S, {})]) == 3
    assert run_i32([*push_two(-7, 2), (st.I32_DIV_S, {})]) == -3  # truncated toward zero
    assert run_i32([*push_two(7, 2), (st.I32_REM_S, {})]) == 1
    assert run_i32([*push_two(-7, 2), (st.I32_REM_S, {})]) == -1
    assert run_i32([*push_two(_to_i32(4294967280), 2), (st.I32_DIV_U, {})]) == _to_i32(
        0xFFFFFFF0 // 2
    )
    assert run_i32([*push_two(_to_i32(4294967280), 3), (st.I32_REM_U, {})]) == _to_i32(
        0xFFFFFFF0 % 3
    )


def test_i32_shifts_mask_the_count_to_5_bits_like_wasm_requires():
    assert run_i32([*push_two(1, 33), (st.I32_SHL, {})]) == 1 << (33 % 32)  # == 2
    assert run_i32([*push_two(-8, 1), (st.I32_SHR_S, {})]) == -4
    assert run_i32([*push_two(_to_i32(2147483648), 1), (st.I32_SHR_U, {})]) == 0x40000000


# ---------------------------------------------------------------------------
# comparisons
# ---------------------------------------------------------------------------


def test_i32_eqz():
    assert run_i32([const_(0), (st.I32_EQZ, {})]) == 1
    assert run_i32([const_(5), (st.I32_EQZ, {})]) == 0


def test_all_i32_comparisons_against_python_reference():
    cases = [(-3, 5), (5, -3), (5, 5), (0, 0), (-1, 1)]
    ops = {
        "eq": (st.I32_EQ, lambda a, b: a == b),
        "ne": (st.I32_NE, lambda a, b: a != b),
        "lt_s": (st.I32_LT_S, lambda a, b: a < b),
        "gt_s": (st.I32_GT_S, lambda a, b: a > b),
        "le_s": (st.I32_LE_S, lambda a, b: a <= b),
        "ge_s": (st.I32_GE_S, lambda a, b: a >= b),
        "lt_u": (st.I32_LT_U, lambda a, b: _u32(a) < _u32(b)),
        "gt_u": (st.I32_GT_U, lambda a, b: _u32(a) > _u32(b)),
        "le_u": (st.I32_LE_U, lambda a, b: _u32(a) <= _u32(b)),
        "ge_u": (st.I32_GE_U, lambda a, b: _u32(a) >= _u32(b)),
    }
    for a, b in cases:
        for name, (stencil, ref) in ops.items():
            got = run_i32([*push_two(a, b), (stencil, {})])
            want = 1 if ref(a, b) else 0
            assert got == want, f"i32.{name}({a},{b}): got {got}, want {want}"


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


def test_i32_store_then_i32_load_round_trip():
    memory = bytearray(64)
    # store(addr=8, value=0x11223344); return load(addr=8)
    code = [
        const_(8),
        const_(0x11223344),
        (st.I32_STORE, {"disp": 0}),
        const_(8),
        (st.I32_LOAD, {"disp": 0}),
    ]
    assert run_i32(code, memory=memory) == 0x11223344
    assert memory[8:12] == (0x11223344).to_bytes(4, "little")


def test_i32_load_store_honor_the_memarg_static_offset():
    memory = bytearray(64)
    # i32.store leaves nothing on the stack (unlike i32.load); a trailing
    # const gives the epilogue something of its own to pop, so it can never
    # reach past our pushes into the native call-return address underneath.
    code = [
        const_(4),
        const_(99),
        (st.I32_STORE, {"disp": 12}),
        const_(0),
    ]  # writes at addr 4+12=16
    run_i32(code, memory=memory)
    assert int.from_bytes(memory[16:20], "little") == 99


# ---------------------------------------------------------------------------
# bounds checking (wasm_instruction_set.md 3.4's mandatory "比較+トラップ")
# ---------------------------------------------------------------------------


def test_in_bounds_access_does_not_trap():
    memory = bytearray(16)
    # max_addr = mem_size(16) - memarg.offset(0) - width(4) = 12: addr=12 is exactly in bounds.
    code = [
        const_(12),
        const_(0x2A),
        (st.I32_STORE, {"disp": 0, "max_addr": 12}),
        const_(0),
    ]
    run_i32_checked(code, memory=memory)
    assert int.from_bytes(memory[12:16], "little") == 0x2A


def test_out_of_bounds_load_traps():
    memory = bytearray(16)
    code = [
        const_(13),
        (st.I32_LOAD, {"disp": 0, "max_addr": 12}),
    ]  # addr=13 > max_addr=12
    try:
        run_i32_checked(code, memory=memory)
        raise AssertionError("expected an out-of-bounds i32.load to trap")
    except OSError:
        pass


def test_out_of_bounds_store_traps():
    memory = bytearray(16)
    code = [const_(13), const_(0), (st.I32_STORE, {"disp": 0, "max_addr": 12})]
    try:
        run_i32_checked(code, memory=memory)
        raise AssertionError("expected an out-of-bounds i32.store to trap")
    except OSError:
        pass


def test_memarg_static_offset_is_folded_into_the_bounds_check():
    """
    A large memarg.offset can push an in-range-looking address out of
        bounds; max_addr already accounts for it (mem_size - offset - width),
        so this must trap without any extra runtime addition.
    """

    memory = bytearray(16)
    # effective address = 8 + offset(8) = 16, one past the end of a 16-byte memory.
    max_addr = 16 - 8 - 4
    code = [const_(8), (st.I32_LOAD, {"disp": 8, "max_addr": max_addr})]
    try:
        run_i32_checked(code, memory=memory)
        raise AssertionError("expected the memarg-offset-adjusted access to trap")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# sub-word memory access
# ---------------------------------------------------------------------------


def test_i32_load8_and_load16_sign_and_zero_extend_correctly():
    memory = bytearray(16)
    memory[0] = 0xFF  # -1 as i8, 255 as u8
    memory[4:6] = (0x8000).to_bytes(2, "little")  # -32768 as i16, 32768 as u16
    assert run_i32([const_(0), (st.I32_LOAD8_S, {"disp": 0})], memory=memory) == -1
    assert run_i32([const_(0), (st.I32_LOAD8_U, {"disp": 0})], memory=memory) == 255
    assert run_i32([const_(4), (st.I32_LOAD16_S, {"disp": 0})], memory=memory) == -32768
    assert run_i32([const_(4), (st.I32_LOAD16_U, {"disp": 0})], memory=memory) == 32768


def test_i32_store8_and_store16_write_only_their_width():
    memory = bytearray(b"\xcc" * 8)
    run_i32(
        [const_(0), const_(0x1234), (st.I32_STORE8, {"disp": 0}), const_(0)],
        memory=memory,
    )
    assert memory[0] == 0x34 and memory[1] == 0xCC, "store8 must not touch the byte after it"
    memory2 = bytearray(b"\xcc" * 8)
    run_i32(
        [const_(0), const_(0x12345678), (st.I32_STORE16, {"disp": 0}), const_(0)],
        memory=memory2,
    )
    assert memory2[0:2] == (0x5678).to_bytes(2, "little")
    assert memory2[2] == 0xCC, "store16 must not touch the byte after it"


# ---------------------------------------------------------------------------
# clz / ctz / popcnt / rotl / rotr
# ---------------------------------------------------------------------------


def test_i32_clz_ctz_popcnt_match_python_reference():
    cases = [0, 1, 2, 0x80000000, 0xFFFFFFFF, 0x0000FFFF, 0x12345678]
    for v in cases:
        sv = _to_i32(v)
        clz = run_i32([const_(sv), (st.I32_CLZ, {})])
        ctz = run_i32([const_(sv), (st.I32_CTZ, {})])
        popcnt = run_i32([const_(sv), (st.I32_POPCNT, {})])
        assert clz == (32 if v == 0 else 32 - v.bit_length()), f"clz({v:#x})"
        expected_ctz = 32 if v == 0 else (v & -v).bit_length() - 1
        assert ctz == expected_ctz, f"ctz({v:#x})"
        assert popcnt == bin(v).count("1"), f"popcnt({v:#x})"


def test_i32_rotl_rotr_match_python_reference():
    def rotl(v, n):
        n &= 31
        return ((v << n) | (v >> (32 - n))) & I32_MASK if n else v

    def rotr(v, n):
        n &= 31
        return ((v >> n) | (v << (32 - n))) & I32_MASK if n else v

    for v, n in [(0x12345678, 4), (0x80000000, 1), (1, 31), (0xFFFFFFFF, 8), (5, 0)]:
        assert run_i32([*push_two(_to_i32(v), n), (st.I32_ROTL, {})]) == _to_i32(rotl(v, n))
        assert run_i32([*push_two(_to_i32(v), n), (st.I32_ROTR, {})]) == _to_i32(rotr(v, n))


# ---------------------------------------------------------------------------
# globals
# ---------------------------------------------------------------------------


def test_global_get_reads_through_the_baked_in_absolute_address():
    Globals = ctypes.c_int64 * 2
    globals_arr = Globals(111, 222)
    addr = ctypes.addressof(globals_arr)
    assert run_i32([(st.GLOBAL_GET, {"addr": addr + 1 * 8})]) == 222


def test_global_set_writes_through_the_baked_in_absolute_address():
    Globals = ctypes.c_int64 * 2
    globals_arr = Globals(111, 222)
    addr = ctypes.addressof(globals_arr)
    code = [const_(999), (st.GLOBAL_SET, {"addr": addr + 0 * 8}), const_(0)]
    run_i32(code)
    assert list(globals_arr) == [999, 222]


# ---------------------------------------------------------------------------
# drop / select
# ---------------------------------------------------------------------------


def test_drop_discards_the_top_value():
    code = [const_(1), const_(2), (st.DROP, {})]
    assert run_i32(code) == 1


def test_select_picks_operand_a_when_condition_nonzero_else_b():
    # select(a=10, b=20, cond) -- pushed in that order per WASM's operand layout
    assert run_i32([const_(10), const_(20), const_(1), (st.SELECT, {})]) == 10
    assert run_i32([const_(10), const_(20), const_(0), (st.SELECT, {})]) == 20


# ---------------------------------------------------------------------------
# fuzz cross-check against Python's operator semantics for the arithmetic ops
# ---------------------------------------------------------------------------


def test_fuzz_add_sub_mul_against_python_reference():
    rng = random.Random(1234)
    for _ in range(200):
        a = rng.randint(-(2**31), 2**31 - 1)
        b = rng.randint(-(2**31), 2**31 - 1)
        assert run_i32([*push_two(a, b), (st.I32_ADD, {})]) == _to_i32(a + b)
        assert run_i32([*push_two(a, b), (st.I32_SUB, {})]) == _to_i32(a - b)
        assert run_i32([*push_two(a, b), (st.I32_MUL, {})]) == _to_i32(a * b)


# Discovered by name rather than hand-listed: a hand-maintained list is
# exactly the kind of bookkeeping that silently drifts (a new test added
# above and never wired in here would just never run). Order is
# definition order, so failures still read top-to-bottom sensibly.
ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

    print(f"[PASS] All {len(ALL_TESTS)} x64 stencil tests passed (executed as real machine code).")

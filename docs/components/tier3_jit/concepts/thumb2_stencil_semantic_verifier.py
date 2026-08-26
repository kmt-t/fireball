"""
docs/components/tier3_jit/concepts/thumb2_stencil_semantic_verifier.py
Dynamic semantic verifier for the JIT stencil catalog (jit_copy_patch_concept.py).

Everything else in this directory only checks that encoded bytes match a
*second* hand-written copy of the same bytes (static parity). That proves
nothing about whether the bytes, once fetched by a real CPU, compute the
WASM operation they claim to. This module actually EXECUTES each ALU/compare/
bit-manipulation stencil's hex_bytes on a real ARMv8-M Thumb interpreter
(Unicorn CPU emulator, not another hand-derivation) with concrete register
inputs, and checks the resulting register state against the WASM-specified
result computed independently in Python.

Requires the `unicorn` package (not a project dependency; run via
`uv run --with unicorn python thumb2_stencil_semantic_verifier.py`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jit_copy_patch_concept import CopyPatchJITEngine  # noqa: E402

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_ERR_EXCEPTION, UcError  # noqa: E402
from unicorn.arm_const import (  # noqa: E402
    UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
)

CODE_BASE = 0x8000
DATA_BASE = 0x20000
MASK32 = 0xFFFFFFFF


def _to_u32(x: int) -> int:
    return x & MASK32


def _to_s32(x: int) -> int:
    x &= MASK32
    return x - (1 << 32) if x & 0x8000_0000 else x


def run_stencil(hex_bytes: str, r2: int = 0, r3: int = 0, r4: int = 0, r5: int = 0, r6: int = 0,
                mem_writes: dict[int, bytes] | None = None) -> dict:
    """Execute a stencil's raw bytes (+ a BKPT sentinel) and return final registers and memory."""
    code = bytes.fromhex(hex_bytes.replace(" ", "")) + bytes.fromhex("00BE")  # BKPT #0
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_BASE, 0x1000)
    mu.mem_write(CODE_BASE, code)
    mu.mem_map(DATA_BASE, 0x20000)  # 128KB data area for linear memory & globals

    if mem_writes:
        for addr, val in mem_writes.items():
            mu.mem_write(addr, val)

    mu.reg_write(UC_ARM_REG_R2, _to_u32(r2))
    mu.reg_write(UC_ARM_REG_R3, _to_u32(r3))
    mu.reg_write(UC_ARM_REG_R4, _to_u32(r4))
    mu.reg_write(UC_ARM_REG_R5, _to_u32(r5))
    mu.reg_write(UC_ARM_REG_R6, _to_u32(r6))
    try:
        mu.emu_start(CODE_BASE | 1, CODE_BASE + len(code))
    except UcError as e:
        if e.errno != UC_ERR_EXCEPTION:
            raise  # anything but our own BKPT sentinel is a real emulation fault
    return {
        "r2": mu.reg_read(UC_ARM_REG_R2),
        "r3": mu.reg_read(UC_ARM_REG_R3),
        "r4": mu.reg_read(UC_ARM_REG_R4),
        "r5": mu.reg_read(UC_ARM_REG_R5),
        "r6": mu.reg_read(UC_ARM_REG_R6),
        "mem_read": lambda addr, size: mu.mem_read(addr, size),
    }


def _clz32(v: int) -> int:
    v &= MASK32
    if v == 0:
        return 32
    n = 0
    while not (v & 0x8000_0000):
        v <<= 1
        n += 1
    return n


def _ctz32(v: int) -> int:
    v &= MASK32
    if v == 0:
        return 32
    n = 0
    while not (v & 1):
        v >>= 1
        n += 1
    return n


def _rotr32(v: int, n: int) -> int:
    v &= MASK32
    n &= 31
    return _to_u32((v >> n) | (v << (32 - n)))


def _rotl32(v: int, n: int) -> int:
    v &= MASK32
    n &= 31
    return _to_u32((v << n) | (v >> (32 - n)))


# Each case: (stencil_name, r4_in, r5_in, expected_r4, is_signed_result)
# Register convention throughout the catalog: result lands in r4; for the
# two-operand ALU family the operation is "r5 OP r4" (NOS OP TOS), matching
# the disassembly text (e.g. "ADDS r4, r5, r4" -> r4 = r5 + r4).
ALU_CASES = [
    ("i32_add_d2", 100, 23, _to_u32(23 + 100)),
    ("i32_add_d2", 0xFFFFFFFF, 1, 0),  # wraparound
    ("i32_sub_d2", 100, 23, _to_u32(23 - 100)),
    ("i32_mul_d2", 1234, 5678, _to_u32(1234 * 5678)),
    ("i32_div_s_d2", 7, _to_u32(-100), _to_u32(int(-100 / 7))),
    ("i32_div_u_d2", 7, 100, 100 // 7),
    ("i32_and_d2", 0b1100, 0b1010, 0b1000),
    ("i32_or_d2", 0b1100, 0b1010, 0b1110),
    ("i32_xor_d2", 0b1100, 0b1010, 0b0110),
    ("i32_shl_d2", 3, 1, 1 << 3),
    ("i32_shr_s_d2", 3, _to_u32(-16), _to_u32(-16 >> 3)),
    ("i32_shr_u_d2", 3, 0x80000000, 0x80000000 >> 3),
    ("i32_rotr_d2", 5, 0b1, _rotr32(0b1, 5)),
    ("i32_rotl_d2", 5, 1, _rotl32(1, 5)),
    ("i32_clz_d1", 0x00010000, 0, _clz32(0x00010000)),
    ("i32_ctz_d1", 0x00010000, 0, _ctz32(0x00010000)),
]

REM_CASES = [
    # rem stencils use r3 as scratch (SDIV/UDIV r3,r5,r4 ; MLS r4,r3,r4,r5)
    ("i32_rem_s_d2", 7, _to_u32(-100), _to_u32(-100 - 7 * int(-100 / 7))),
    ("i32_rem_u_d2", 7, 100, 100 % 7),
]

CMP_CASES = [
    # (stencil, r4(TOS), r5(NOS), expected r4)  disassembly is "CMP r5, r4" then MOV per condition
    ("i32_eqz_d1", 0, 0, 1),
    ("i32_eqz_d1", 5, 0, 0),
    ("i32_eq_d2", 5, 5, 1),
    ("i32_eq_d2", 5, 4, 0),
    ("i32_ne_d2", 5, 4, 1),
    ("i32_ne_d2", 5, 5, 0),
    ("i32_lt_s_d2", 5, 4, 1),   # r5(4) < r4(5)
    ("i32_lt_s_d2", 4, 5, 0),
    ("i32_lt_u_d2", 5, 4, 1),
    ("i32_gt_s_d2", 4, 5, 1),   # r5(5) > r4(4)
    ("i32_gt_u_d2", 4, 5, 1),
    ("i32_le_s_d2", 5, 5, 1),
    ("i32_le_s_d2", 4, 5, 0),   # r5(5) <= r4(4) is false
    ("i32_le_s_d2", 5, 4, 1),   # r5(4) <= r4(5) is true
    ("i32_ge_s_d2", 5, 5, 1),
    ("i32_ge_s_d2", 5, 4, 0),   # r5(4) >= r4(5) is false
    ("i32_ge_s_d2", 4, 5, 1),   # r5(5) >= r4(4) is true
]


def main() -> None:
    engine = CopyPatchJITEngine()
    failures = []
    total = 0

    for name, r4_in, r5_in, expected in ALU_CASES:
        total += 1
        st = engine.stencils[name]
        result = run_stencil(st.hex_bytes, r4=r4_in, r5=r5_in)
        if _to_u32(result["r4"]) != _to_u32(expected):
            failures.append(
                f"{name}(r4={r4_in:#x}, r5={r5_in:#x}): got r4={result['r4']:#x}, "
                f"expected {expected & MASK32:#x}"
            )

    for name, r4_in, r5_in, expected in REM_CASES:
        total += 1
        st = engine.stencils[name]
        result = run_stencil(st.hex_bytes, r4=r4_in, r5=r5_in)
        if _to_u32(result["r4"]) != _to_u32(expected):
            failures.append(
                f"{name}(r4={r4_in:#x}, r5={r5_in:#x}): got r4={result['r4']:#x}, "
                f"expected {expected & MASK32:#x}"
            )

    for name, r4_in, r5_in, expected in CMP_CASES:
        total += 1
        st = engine.stencils[name]
        result = run_stencil(st.hex_bytes, r4=r4_in, r5=r5_in)
        if result["r4"] != expected:
            failures.append(
                f"{name}(r4={r4_in}, r5={r5_in}): got r4={result['r4']}, expected {expected}"
            )

    # --- Memory Load/Store Stencils with FastAddressCheck Boundary Mask (r3=mem_base, r6=mem_mask) ---
    mem_base = DATA_BASE
    mem_mask = 0x0000FFFF  # 64KB mask

    # Test i32_load_r3: load from masked address
    # Address 0x10004 & 0xFFFF = 0x0004 -> reads from mem_base + 0x0004
    st_load = engine.stencils["i32_load_r3"]
    res_load = run_stencil(
        st_load.hex_bytes,
        r3=mem_base, r4=0x10004, r6=mem_mask,
        mem_writes={mem_base + 0x0004: (0xDEADBEEF).to_bytes(4, "little")}
    )
    total += 1
    if _to_u32(res_load["r4"]) != 0xDEADBEEF:
        failures.append(f"i32_load_r3: got r4={res_load['r4']:#x}, expected 0xDEADBEEF")

    # Test i32_store_r3: store to masked address
    # Address 0x20008 & 0xFFFF = 0x0008 -> writes to mem_base + 0x0008
    st_store = engine.stencils["i32_store_r3"]
    res_store = run_stencil(
        st_store.hex_bytes,
        r3=mem_base, r4=0xCAFEBABE, r5=0x20008, r6=mem_mask,
    )
    total += 1
    stored_val = int.from_bytes(res_store["mem_read"](mem_base + 0x0008, 4), "little")
    if stored_val != 0xCAFEBABE:
        failures.append(f"i32_store_r3: stored val={stored_val:#x}, expected 0xCAFEBABE")

    # --- Global Get / Set Stencils via vsoc_runtime.globals_base (env + 0x0C) ---
    env_addr = DATA_BASE + 0x10000
    globals_addr = DATA_BASE + 0x11000

    # Test global_get_d0: env + 0x0C -> globals_addr -> reads global[0]
    st_gget = engine.stencils["global_get_d0"]
    res_gget = run_stencil(
        st_gget.hex_bytes,
        r2=env_addr,
        mem_writes={
            env_addr + 0x0C: globals_addr.to_bytes(4, "little"),
            globals_addr + 0x00: (0x12345678).to_bytes(4, "little"),
        }
    )
    total += 1
    if _to_u32(res_gget["r4"]) != 0x12345678:
        failures.append(f"global_get_d0: got r4={res_gget['r4']:#x}, expected 0x12345678")

    # Test global_set_d1: env + 0x0C -> globals_addr -> writes global[0]
    st_gset = engine.stencils["global_set_d1"]
    res_gset = run_stencil(
        st_gset.hex_bytes,
        r2=env_addr, r4=0x87654321,
        mem_writes={
            env_addr + 0x0C: globals_addr.to_bytes(4, "little"),
        }
    )
    total += 1
    g_stored = int.from_bytes(res_gset["mem_read"](globals_addr + 0x00, 4), "little")
    if g_stored != 0x87654321:
        failures.append(f"global_set_d1: stored val={g_stored:#x}, expected 0x87654321")

    stencil_count = len(set(c[0] for c in ALU_CASES + REM_CASES + CMP_CASES)) + 4
    print(f"Executed {total} case(s) across {stencil_count} stencils on a real ARMv8-M Thumb emulator.")
    if failures:
        print(f"[FAIL] {len(failures)} case(s) computed the wrong result:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("[PASS] Every executed stencil produced the WASM-correct result.")


if __name__ == "__main__":
    main()

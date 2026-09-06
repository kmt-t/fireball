"""
experiments/pysim/jit/x64_asm.py
Generic, register-name-driven x64 encoders for the small set of
instructions the JIT's calling-convention glue code needs (as opposed to
x64_stencils.py's fixed, WASM-opcode-keyed Copy-and-Patch templates). Used
for WASM-to-WASM `call` argument marshalling and for the `fireball_call`-
style host-call bridge, both of which need a variable number of registers
depending on the callee's arity -- something a fixed stencil table can't
parametrize, but a name -> (REX-needs-extension, low-3-bits) table can.
Every helper here is covered by test_x64_asm.py, which executes the
encoded bytes as real machine code -- the same "run it, don't just
re-derive it" discipline that caught four bugs in x64_stencils.py.
"""

from __future__ import annotations

from system_containers import FlatMapView

# name -> (needs_rex_extension_bit, low_3_bits_of_the_register_number)
# A sorted flat_map_view<std::string_view, ...> (system_containers.md
# explicitly names string_view as a valid Key type), never a dict -- this
# is a fixed 16-entry table known at compile time, exactly the shape a
# `constexpr std::array` lookup keyed by register name would have.
_REG_ENTRIES: list[tuple[str, tuple[int, int]]] = sorted(
    [
        ("rax", (0, 0)),
        ("rcx", (0, 1)),
        ("rdx", (0, 2)),
        ("rbx", (0, 3)),
        ("rsp", (0, 4)),
        ("rbp", (0, 5)),
        ("rsi", (0, 6)),
        ("rdi", (0, 7)),
        ("r8", (1, 0)),
        ("r9", (1, 1)),
        ("r10", (1, 2)),
        ("r11", (1, 3)),
        ("r12", (1, 4)),
        ("r13", (1, 5)),
        ("r14", (1, 6)),
        ("r15", (1, 7)),
    ],
    key=lambda e: e[0],
)
_REG_ENTRIES_TUPLE: tuple[tuple[str, tuple[int, int]], ...] = tuple(_REG_ENTRIES)
REG_INFO: FlatMapView[str, tuple[int, int]] = FlatMapView(_REG_ENTRIES_TUPLE)


def push_reg(name: str) -> bytes:
    ext, lo = REG_INFO[name]
    prefix = bytes((0x41,)) if ext else b""
    return prefix + bytes((0x50 + lo,))


def pop_reg(name: str) -> bytes:
    ext, lo = REG_INFO[name]
    prefix = bytes((0x41,)) if ext else b""
    return prefix + bytes((0x58 + lo,))


def mov_reg_reg(dst: str, src: str) -> bytes:
    """mov dst, src (64-bit)."""
    dst_ext, dst_lo = REG_INFO[dst]
    src_ext, src_lo = REG_INFO[src]
    rex = 0x48 | (0x04 if src_ext else 0) | (0x01 if dst_ext else 0)  # W | R(src) | B(dst)
    modrm = 0xC0 | (src_lo << 3) | dst_lo
    return bytes((rex, 0x89, modrm))


def mov_reg_imm64(dst: str, imm64: int) -> bytes:
    dst_ext, dst_lo = REG_INFO[dst]
    rex = 0x48 | (0x01 if dst_ext else 0)
    opcode = 0xB8 + dst_lo
    return bytes((rex, opcode)) + (imm64 & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


def mov_store_rsp_disp32(disp: int, src: str) -> bytes:
    """mov [rsp+disp32], src (64-bit store, no index)."""
    src_ext, src_lo = REG_INFO[src]
    rex = 0x48 | (0x04 if src_ext else 0)  # W | R(src); base rsp never needs B
    modrm = 0x80 | (src_lo << 3) | 0x04  # mod=10, reg=src, rm=100 (SIB follows)
    sib = 0x24  # scale=00, index=100 (none), base=100 (rsp)
    return bytes((rex, 0x89, modrm, sib)) + (disp & 0xFFFFFFFF).to_bytes(4, "little")


def mov_load_rsp_disp32(dst: str, disp: int) -> bytes:
    """mov dst, [rsp+disp32] (64-bit load, no index)."""
    dst_ext, dst_lo = REG_INFO[dst]
    rex = 0x48 | (0x04 if dst_ext else 0)
    modrm = 0x80 | (dst_lo << 3) | 0x04
    sib = 0x24
    return bytes((rex, 0x8B, modrm, sib)) + (disp & 0xFFFFFFFF).to_bytes(4, "little")


def and_rsp_imm8(imm8: int) -> bytes:
    """and rsp, imm8 (sign-extended) -- used as `and rsp, -16` to align down."""
    return bytes((0x48, 0x83, 0xE4, imm8 & 0xFF))


def sub_rsp_imm8(imm8: int) -> bytes:
    assert 0 <= imm8 <= 127
    return bytes((0x48, 0x83, 0xEC, imm8))


def add_rsp_imm8(imm8: int) -> bytes:
    assert 0 <= imm8 <= 127
    return bytes((0x48, 0x83, 0xC4, imm8))


def call_reg(reg: str) -> bytes:
    ext, lo = REG_INFO[reg]
    prefix = bytes((0x41,)) if ext else b""
    modrm = 0xD0 | lo
    return prefix + bytes((0xFF, modrm))


def ret() -> bytes:
    return bytes((0xC3,))


_SCALE_BITS = {1: 0, 2: 1, 4: 2, 8: 3}


def mov_load_scaled(dst: str, base: str, index: str, scale: int) -> bytes:
    """
    mov dst, [base + index*scale] (64-bit load). `index` may not be
        "rsp" (that encoding means "no index" instead); `base` being "rbp" or
        "r13" costs one extra (zero) displacement byte, same SIB quirk as the
        stack-pointer-relative helpers above.
    """

    assert index != "rsp", 'rsp cannot be a SIB index register (that encoding means "no index")'
    dst_ext, dst_lo = REG_INFO[dst]
    base_ext, base_lo = REG_INFO[base]
    index_ext, index_lo = REG_INFO[index]
    rex = 0x48 | (0x04 if dst_ext else 0) | (0x02 if index_ext else 0) | (0x01 if base_ext else 0)
    needs_disp8 = base_lo == 5  # rbp/r13 as SIB base always needs an explicit displacement
    mod = 0x40 if needs_disp8 else 0x00
    modrm = mod | (dst_lo << 3) | 0x04  # rm=100 => SIB follows
    sib = (_SCALE_BITS[scale] << 6) | (index_lo << 3) | base_lo
    tail = bytes((0x00,)) if needs_disp8 else b""
    return bytes((rex, 0x8B, modrm, sib)) + tail


def cmp_dword_scaled_imm32(base: str, index: str, scale: int, imm32: int) -> bytes:
    """cmp dword [base + index*scale], imm32 (32-bit compare, no REX.W)."""
    assert index != "rsp"
    base_ext, base_lo = REG_INFO[base]
    index_ext, index_lo = REG_INFO[index]
    rex_bits = (0x02 if index_ext else 0) | (0x01 if base_ext else 0)
    prefix = bytes((0x40 | rex_bits,)) if rex_bits else b""
    needs_disp8 = base_lo == 5
    mod = 0x40 if needs_disp8 else 0x00
    modrm = mod | (0x07 << 3) | 0x04  # reg=111 (the /7 CMP extension), rm=100 => SIB follows
    sib = (_SCALE_BITS[scale] << 6) | (index_lo << 3) | base_lo
    tail = bytes((0x00,)) if needs_disp8 else b""
    return prefix + bytes((0x81, modrm, sib)) + tail + (imm32 & 0xFFFFFFFF).to_bytes(4, "little")


def test_reg_reg(reg: str) -> bytes:
    """test reg, reg (64-bit) -- sets ZF iff reg == 0."""
    ext, lo = REG_INFO[reg]
    rex = 0x48 | (0x05 if ext else 0)  # both R and B extend the same register here
    modrm = 0xC0 | (lo << 3) | lo
    return bytes((rex, 0x85, modrm))


def cmp_reg_imm32(reg: str, imm32: int) -> bytes:
    """cmp reg32, imm32 (32-bit compare against any register, not just eax's short form)."""
    ext, lo = REG_INFO[reg]
    prefix = bytes((0x41,)) if ext else b""
    modrm = 0xC0 | (0x07 << 3) | lo
    return prefix + bytes((0x81, modrm)) + (imm32 & 0xFFFFFFFF).to_bytes(4, "little")


def jmp_rel32_placeholder() -> tuple[bytes, int]:
    """
    Returns (bytes, reloc_offset): an unconditional near jump with its
        4-byte displacement zeroed, ready for x64_jit.py's _patch_rel32.
    """

    return bytes((0xE9, 0x00, 0x00, 0x00, 0x00)), 1


def jcc_rel32_placeholder(condition: str) -> tuple[bytes, int]:
    """
    Returns (bytes, reloc_offset) for a near Jcc. `condition` is the
        mnemonic suffix: "e"/"z" (equal/zero), "ne"/"nz", "a" (above,
        unsigned), "ae", "b", "be" -- the ones this codebase's glue code uses.
    """

    opcode = {
        "e": 0x84,
        "z": 0x84,
        "ne": 0x85,
        "nz": 0x85,
        "a": 0x87,
        "ae": 0x83,
        "b": 0x82,
        "be": 0x86,
    }[condition]
    return bytes((0x0F, opcode, 0x00, 0x00, 0x00, 0x00)), 2

"""Read the local direct-branch records that the stencil macros emit.

Each record is three little-endian 32-bit words written by FB_BRANCH_RECORD: the instruction
offset, the target offset, and the instruction size, relative to the start of .text.
The table is derived from these records only; no disassembler text is parsed.
"""

from __future__ import annotations

import struct
from pathlib import Path

from .elf_reader import read_section_bytes
from .stencil_model import BranchFixup, GenerationError, Symbol

RECORD_SECTION = ".fb_branch_records"
RECORD_FORMAT = "<3I"
RECORD_BYTES = struct.calcsize(RECORD_FORMAT)
# Encodings the macros emit: rel8 jump or jcc (2 bytes), rel32 jump (5 bytes), rel32 jcc (6 bytes).
ALLOWED_INSTRUCTION_SIZES = (2, 5, 6)


def read_branch_records(
    path: Path, symbols: dict[str, Symbol]
) -> dict[str, tuple[BranchFixup, ...]]:
    """Return the fixups of every stencil that contains a recorded local branch, by symbol name."""
    raw = read_section_bytes(path, RECORD_SECTION)
    if len(raw) % RECORD_BYTES != 0:
        raise GenerationError(
            f"{RECORD_SECTION} has {len(raw)} bytes; not a multiple of {RECORD_BYTES}"
        )
    stencils = [symbol for symbol in symbols.values() if symbol.size != 0]
    branch_map: dict[str, list[BranchFixup]] = {}
    for instruction, target, size in struct.iter_unpack(RECORD_FORMAT, raw):
        owner = next(
            (
                symbol
                for symbol in stencils
                if symbol.address <= instruction < symbol.address + symbol.size
            ),
            None,
        )
        if owner is None:
            raise GenerationError(f"branch record at 0x{instruction:x} lies outside every stencil")
        end = owner.address + owner.size
        where = f"{owner.name}+0x{instruction - owner.address:x}"
        if size not in ALLOWED_INSTRUCTION_SIZES:
            raise GenerationError(f"unsupported branch record size {size} at {where}")
        if instruction + size > end:
            raise GenerationError(f"branch record at {where} runs past its stencil")
        if not (owner.address <= target < end):
            raise GenerationError(f"branch record at {where} leaves its stencil")
        branch_map.setdefault(owner.name, []).append(
            BranchFixup(instruction - owner.address, size, target - owner.address)
        )
    return {
        name: tuple(sorted(values, key=lambda item: item.instruction_offset))
        for name, values in branch_map.items()
    }

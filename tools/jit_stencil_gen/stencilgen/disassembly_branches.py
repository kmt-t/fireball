"""Local direct-branch fixups read from objdump text.

This module is the last text parser in the generator. Stage 2 replaces it with
branch records emitted by the assembly macros; until then it is the only use of
objdump in the generator.
"""

from __future__ import annotations

import re
from pathlib import Path

from .stencil_model import BranchFixup, GenerationError, Symbol
from .toolchain import command_output

DISASSEMBLY_LINE = re.compile(
    r"^\s*([0-9a-fA-F]+):\s*((?:[0-9a-fA-F]{2}\s+)+)"
    r"([A-Za-z][A-Za-z0-9.]*)\s*(.*?)\s*$"
)
DIRECT_TARGET = re.compile(r"^\s*(?:0x)?([0-9a-fA-F]+)\b")


def parse_disassembly(
    objdump: str, object_path: Path, symbols: dict[str, Symbol]
) -> dict[str, tuple[BranchFixup, ...]]:
    output = command_output(
        [objdump, "-d", "-Mintel", str(object_path)],
        "object disassembly inspection",
    )
    markers: dict[str, set[int]] = {}
    for name, symbol in symbols.items():
        for prefix, key in (
            ("fb_jit_next_slot__", "next"),
            ("fb_jit_abort_slot__", "abort"),
            ("fb_jit_start_slot__", "start"),
            ("fb_jit_exit_slot__", "exit"),
        ):
            if name.startswith(prefix):
                markers.setdefault(key, set()).add(symbol.address)

    branch_map: dict[str, list[BranchFixup]] = {}
    for line in output.splitlines():
        match = DISASSEMBLY_LINE.match(line)
        if match is None:
            continue
        address = int(match.group(1), 16)
        encoded = bytes(int(value, 16) for value in match.group(2).split())
        mnemonic = match.group(3).lower()
        operand = match.group(4)
        # FF /4 is an indirect jump through a register or memory operand; it has no fixup.
        if not mnemonic.startswith("j") or operand.startswith("*") or encoded[0] == 0xFF:
            continue
        owner = next(
            (
                symbol
                for symbol in symbols.values()
                if symbol.size != 0 and symbol.address <= address < symbol.address + symbol.size
            ),
            None,
        )
        if owner is None:
            raise GenerationError(f"direct branch at 0x{address:x} lies outside every stencil")
        if address in markers.get("next", set()) | markers.get("abort", set()) | {
            *markers.get("start", set()),
            *markers.get("exit", set()),
        }:
            continue
        # A direct branch that cannot be recorded fails generation; it is never dropped silently.
        target_match = DIRECT_TARGET.match(operand)
        if target_match is None or len(encoded) not in (2, 5, 6):
            raise GenerationError(
                f"unsupported direct branch {operand!r} at {owner.name}+0x{address - owner.address:x}"
            )
        target = int(target_match.group(1), 16)
        if not (owner.address <= target < owner.address + owner.size):
            raise GenerationError(
                f"direct branch at {owner.name}+0x{address - owner.address:x} leaves its stencil"
            )
        branch_map.setdefault(owner.name, []).append(
            BranchFixup(address - owner.address, len(encoded), target - owner.address)
        )
    return {
        name: tuple(sorted(values, key=lambda item: item.instruction_offset))
        for name, values in branch_map.items()
    }

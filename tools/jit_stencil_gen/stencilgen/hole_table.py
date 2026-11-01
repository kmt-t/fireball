"""Build the per-stencil hole table (slots, immediates, and local positions) from symbols."""

from __future__ import annotations

import re

from .stencil_model import BranchFixup, GenerationError, Stencil, Symbol

WORD_SUFFIX = re.compile(r"^(?P<base>.+)_word_(?P<word>[0-9]+)$")
PATCH_SLOT_PREFIXES = (1, 4, 8)


def stencil_symbols(role: str, symbols: dict[str, Symbol]) -> list[Symbol]:
    if role == "operations":
        prefix = "fb_stencil_"
    elif role == "trace":
        prefix = "fb_stencil_"
    else:
        raise GenerationError(f"stencil role is invalid: {role}")
    selected = [
        symbol for name, symbol in symbols.items() if name.startswith(prefix) and symbol.size != 0
    ]
    if role == "trace":
        selected.extend(
            symbol
            for name, symbol in symbols.items()
            if name.startswith("fb_trace_") and symbol.size != 0
        )
    if not selected:
        raise GenerationError(f"assembly contains no {role} stencil symbols")
    return sorted(selected, key=lambda symbol: symbol.address)


def stencil_name(symbol: str) -> str:
    if symbol.startswith("fb_stencil_"):
        return symbol.removeprefix("fb_stencil_")
    if symbol.startswith("fb_trace_"):
        return symbol.removeprefix("fb_trace_").join(("trace_", ""))
    raise GenerationError(f"stencil symbol has an unexpected name: {symbol}")


def collect_stencils(
    role: str,
    symbols: dict[str, Symbol],
    branches: dict[str, tuple[BranchFixup, ...]],
) -> tuple[list[Stencil], dict[str, list[Stencil]]]:
    selected = stencil_symbols(role, symbols)
    groups: dict[str, list[tuple[int, Stencil]]] = {}
    direct: list[Stencil] = []
    for symbol in selected:
        symbol_name = symbol.name
        name = stencil_name(symbol_name)
        next_symbol = symbols.get(f"fb_jit_next_slot__{symbol_name}")
        abort_symbol = symbols.get(f"fb_jit_abort_slot__{symbol_name}")
        start_symbol = symbols.get(f"fb_jit_start_slot__{symbol_name}")
        exit_symbol = symbols.get(f"fb_jit_exit_slot__{symbol_name}")

        patch_slots = [
            (width, symbols.get(f"fb_jit_patch_slot_{width}__{symbol_name}"))
            for width in PATCH_SLOT_PREFIXES
        ]
        patch_slots = [(width, marker) for width, marker in patch_slots if marker is not None]
        if len(patch_slots) > 1:
            raise GenerationError(f"stencil {symbol_name!r} has more than one immediate patch slot")
        patch_width, patch_symbol = patch_slots[0] if patch_slots else (0, None)
        patch_offset = None if patch_symbol is None else patch_symbol.address - symbol.address
        if patch_offset is not None and (
            patch_offset < 0 or patch_width > symbol.size - patch_offset
        ):
            raise GenerationError(
                f"stencil {symbol_name!r} has an immediate patch slot outside its code"
            )
        instruction_symbol = symbols.get(f"fb_jit_instruction_slot__{symbol_name}")
        wide_instruction_symbol = symbols.get(f"fb_jit_instruction_slot7__{symbol_name}")
        if instruction_symbol is not None and wide_instruction_symbol is not None:
            raise GenerationError(f"stencil {symbol_name!r} has two instruction slots")
        if wide_instruction_symbol is not None:
            instruction_symbol = wide_instruction_symbol
        instruction_offset = (
            None if instruction_symbol is None else instruction_symbol.address - symbol.address
        )
        instruction_width = (
            0 if instruction_symbol is None else (7 if wide_instruction_symbol is not None else 4)
        )
        if instruction_offset is not None and (
            instruction_offset < 0 or instruction_width > symbol.size - instruction_offset
        ):
            raise GenerationError(
                f"stencil {symbol_name!r} has an instruction slot outside its code"
            )

        stencil = Stencil(
            symbol,
            None if next_symbol is None else next_symbol.address - symbol.address,
            None if abort_symbol is None else abort_symbol.address - symbol.address,
            None if start_symbol is None else start_symbol.address - symbol.address,
            None if exit_symbol is None else exit_symbol.address - symbol.address,
            branches.get(symbol_name, ()),
            patch_offset,
            patch_width,
            instruction_offset,
            instruction_width,
        )
        word_match = WORD_SUFFIX.match(name)
        if word_match is None:
            direct.append(stencil)
        else:
            groups.setdefault(word_match.group("base") + "_by_word", []).append(
                (int(word_match.group("word")), stencil)
            )

    arrays: dict[str, list[Stencil]] = {}
    for name, indexed in groups.items():
        ordered = sorted(indexed, key=lambda entry: entry[0])
        indices = [entry[0] for entry in ordered]
        if indices != list(range(len(indices))):
            raise GenerationError(f"variant group {name!r} is not contiguous from zero")
        arrays[name] = [entry[1] for entry in ordered]
    return direct, arrays

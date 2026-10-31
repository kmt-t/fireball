"""Write the raw code blobs and the C++ headers consumed by the JIT build."""

from __future__ import annotations

import re
from pathlib import Path

from .hole_table import stencil_name
from .stencil_model import GenerationError, Stencil, Symbol


def cpp_bytes(raw: bytes) -> str:
    lines = []
    for start in range(0, len(raw), 16):
        values = ", ".join(f"0x{byte:02X}" for byte in raw[start : start + 16])
        lines.append(f"    {values},")
    return "\n".join(lines)


def range_initializer(stencil: Stencil, code_id: int, branch_offset: int) -> str:
    symbol = stencil.symbol
    return (
        f'{{"{symbol.name}", {symbol.address}, {symbol.size}, '
        f"{'no_link_slot' if stencil.next_slot is None else stencil.next_slot}, "
        f"{'no_link_slot' if stencil.abort_slot is None else stencil.abort_slot}, "
        f"{'no_link_slot' if stencil.start_slot is None else stencil.start_slot}, "
        f"{'no_link_slot' if stencil.exit_slot is None else stencil.exit_slot}, "
        f"{branch_offset}, {len(stencil.branches)}, {code_id}, "
        f"{'no_link_slot' if stencil.patch_slot_offset is None else stencil.patch_slot_offset}, "
        f"{stencil.patch_slot_width}, "
        f"{'no_link_slot' if stencil.instruction_slot_offset is None else stencil.instruction_slot_offset}, "
        f"{stencil.instruction_slot_width}}}"
    )


def emit_stencil_header(
    namespace: str,
    header: Path,
    raw: bytes,
    stencils: list[Stencil],
    arrays: dict[str, list[Stencil]],
) -> None:
    all_stencils = stencils + [item for values in arrays.values() for item in values]
    fixups = [fixup for stencil in all_stencils for fixup in stencil.branches]
    branch_offsets: dict[str, int] = {}
    cursor = 0
    for stencil in all_stencils:
        branch_offsets[stencil.symbol.name] = cursor
        cursor += len(stencil.branches)
    guard = "FIREBALL_GENERATED_" + re.sub(r"[^A-Z0-9]", "_", header.name.upper())
    source = [
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <array>",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
        f"namespace {namespace} {{",
        "inline constexpr std::size_t no_link_slot = static_cast<std::size_t>(-1);",
        "struct stencil_range {",
        "  const char *symbol; std::size_t code_offset; std::size_t code_size;",
        "  std::size_t next_slot_offset; std::size_t abort_slot_offset;",
        "  std::size_t start_slot_offset; std::size_t exit_slot_offset;",
        "  std::size_t branch_offset; std::size_t branch_count; std::uint16_t code_id;",
        "  std::size_t patch_slot_offset; std::uint8_t patch_slot_width;",
        "  std::size_t instruction_slot_offset; std::uint8_t instruction_slot_width;",
        "};",
        "struct stencil_branch_fixup {",
        "  std::size_t instruction_offset; std::uint8_t instruction_size;",
        "  std::size_t target_offset;",
        "};",
        f"inline constexpr std::array<std::uint8_t, {len(raw)}> fb_jit_stencils_start{{{{",
        cpp_bytes(raw),
        "}};",
        f"inline constexpr std::array<stencil_branch_fixup, {len(fixups)}> stencil_branch_fixups{{{{",
    ]
    for stencil in all_stencils:
        for fixup in stencil.branches:
            source.append(
                f"    {{{fixup.instruction_offset}, {fixup.instruction_size}, "
                f"{fixup.target_offset}}},"
            )
    source.extend(("}};", ""))
    for code_id, stencil in enumerate(stencils):
        source.append(
            f"inline constexpr stencil_range {stencil_name(stencil.symbol.name)} = "
            f"{range_initializer(stencil, code_id, branch_offsets[stencil.symbol.name])};"
        )
    next_code_id = len(stencils)
    for group_name, variants in arrays.items():
        source.append(
            f"inline constexpr std::array<stencil_range, {len(variants)}> {group_name}{{{{"
        )
        for stencil in variants:
            source.append(
                f"    {range_initializer(stencil, next_code_id, branch_offsets[stencil.symbol.name])},"
            )
            next_code_id += 1
        source.append("}};")
    source.extend((f"}} // namespace {namespace}", "", f"#endif // {guard}", ""))
    header.write_text("\n".join(source), encoding="utf-8")


def emit_common_header(header: Path, raw: bytes, symbols: dict[str, Symbol]) -> None:
    dispatcher = symbols.get("fb_common_chain_dispatcher")
    if dispatcher is None or dispatcher.size == 0:
        raise GenerationError("common assembly is missing fb_common_chain_dispatcher")
    source = [
        "#ifndef FIREBALL_GENERATED_COMMON_CODE_HXX",
        "#define FIREBALL_GENERATED_COMMON_CODE_HXX",
        "",
        "#include <array>",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
        "namespace fireball::pysim::jit::generated {",
        f"inline constexpr std::array<std::uint8_t, {len(raw)}> fb_jit_common_code_start{{{{",
        cpp_bytes(raw),
        "}};",
        f"inline constexpr std::uint32_t common_code_bytes = {len(raw)};",
        f"inline constexpr std::uint32_t common_chain_dispatcher_offset = {dispatcher.address};",
        f"inline constexpr std::uint32_t common_chain_dispatcher_size = {dispatcher.size};",
        "} // namespace fireball::pysim::jit::generated",
        "",
        "#endif // FIREBALL_GENERATED_COMMON_CODE_HXX",
        "",
    ]
    header.write_text("\n".join(source), encoding="utf-8")

#!/usr/bin/env python3
"""Assemble x64 JIT LIR and emit its raw code and C++ metadata."""

from __future__ import annotations

import argparse
import re
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class GenerationError(RuntimeError):
    """A source, ABI, object, or metadata validation error."""


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    address: int
    size: int


@dataclass(frozen=True, slots=True)
class BranchFixup:
    instruction_offset: int
    instruction_size: int
    target_offset: int


@dataclass(frozen=True, slots=True)
class Stencil:
    symbol: Symbol
    next_slot: int | None
    abort_slot: int | None
    start_slot: int | None
    exit_slot: int | None
    branches: tuple[BranchFixup, ...]
    patch_slot_offset: int | None
    patch_slot_width: int
    instruction_slot_offset: int | None
    instruction_slot_width: int


SYMBOL_LINE = re.compile(r"^\s*([0-9a-fA-F]+)(?:\s+([0-9a-fA-F]+))?\s+([A-Za-z])\s+(\S+)\s*$")
DISASSEMBLY_LINE = re.compile(
    r"^\s*([0-9a-fA-F]+):\s*((?:[0-9a-fA-F]{2}\s+)+)"
    r"([A-Za-z][A-Za-z0-9.]*)\s*(.*?)\s*$"
)
DIRECT_TARGET = re.compile(r"^\s*([0-9a-fA-F]+)\b")
WORD_SUFFIX = re.compile(r"^(?P<base>.+)_word_(?P<word>[0-9]+)$")
PATCH_SLOT_PREFIXES = (1, 4, 8)
RUNTIME_API_STENCIL_OPTIMIZATION = r"""/* Resolve the API slot, prepare the native ABI arguments, and call the API. */
.macro FB_RUNTIME_API_CALL
  movzx eax, byte ptr [FB_REL_REG]
  mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_REL_OFFSET]
  movsxd r10, dword ptr [r11]
  lea r11, [r11 + r10]
  mov r10d, dword ptr [FB_REL_REG + 3]
  mov dword ptr [FB_CTX_REG + FB_CONTEXT_IP_OFFSET], r10d
  movsxd r10, dword ptr [r11 + FB_TRACE_COMMON_RELATIVE_OFFSET]
  mov r11, qword ptr [r11 + r10 + FB_TRACE_RUNTIME_API_TABLE_POINTER_OFFSET]
  mov rax, qword ptr [r11 + rax * 8]
  push FB_CTX_REG
  push FB_IP_REG
  push FB_SP_REG
  push FB_REL_REG
  sub rsp, 64
  mov qword ptr [rsp + 32], FB_SP_REG
  mov r11, qword ptr [FB_CTX_REG + FB_CONTEXT_LOCAL_BASE_OFFSET]
  mov qword ptr [rsp + 40], r11
  mov r11, qword ptr [rsp + 88]
  mov r10, qword ptr [r11 + FB_CONTEXT_SP_OFFSET]
  mov qword ptr [rsp + 56], r10
  mov qword ptr [r11 + FB_CONTEXT_SP_OFFSET], FB_SP_REG
#if defined(_WIN32)
  mov rcx, qword ptr [rsp + 88]
  lea rdx, [rsp + 32]
  lea r8, [rsp + 40]
  lea r9, [FB_REL_REG + 7]
#else
  mov rdi, qword ptr [rsp + 88]
  lea rsi, [rsp + 32]
  lea rdx, [rsp + 40]
  lea rcx, [FB_REL_REG + 7]
#endif
  call rax
.endm

/* Synchronize state and select trace continuation or its shared abort exit. */
.macro FB_RUNTIME_API_SYNC name
  mov r11, qword ptr [rsp + 88]
  mov r10, qword ptr [rsp + 40]
  mov qword ptr [r11 + FB_CONTEXT_LOCAL_BASE_OFFSET], r10
  test al, al
  jz .Lruntime_api_abort_sync\@
  mov r10, qword ptr [rsp + 56]
  mov qword ptr [r11 + FB_CONTEXT_SP_OFFSET], r10
  xor r8d, r8d
  jmp .Lruntime_api_exit\@
.Lruntime_api_abort_sync\@:
  mov r10, qword ptr [rsp + 32]
  mov qword ptr [r11 + FB_CONTEXT_SP_OFFSET], r10
  mov r8d, 1
.Lruntime_api_exit\@:
  mov r10, qword ptr [rsp + 32]
  mov r11, qword ptr [rsp + 64]
  movzx eax, byte ptr [r11 + 2]
  lea r11, [r11 + rax + 7]
  add rsp, 64
  pop FB_REL_REG
  pop FB_SP_REG
  pop FB_IP_REG
  pop FB_CTX_REG
  mov FB_SP_REG, r10
  mov FB_REL_REG, r11
  test r8d, r8d
  jnz .Lruntime_api_abort\@
  jmp .Lruntime_api_continue\@
.Lruntime_api_abort\@:
  FB_SLOT_MARK fb_jit_abort_slot__\name, 0xe9
.Lruntime_api_continue\@:
.endm

/* Call one opcode API through the common-area table; false exits the trace. */
.macro FB_RUNTIME_API_STENCIL name
FB_STENCIL_BEGIN \name
  FB_RUNTIME_API_CALL
  FB_RUNTIME_API_SYNC \name
FB_STENCIL_END \name
.endm

FB_RUNTIME_API_STENCIL fb_stencil_runtime_api
"""
TRACE_ABORT_STENCIL_OPTIMIZATION = """FB_TRACE_BEGIN fb_trace_return_abort
  FB_NEXT_MARK fb_trace_return_abort
FB_TRACE_END fb_trace_return_abort"""


def optimized_assembly_source(role: str, source: Path, directory: Path) -> Path:
    """Apply stencil-layout optimizations without editing the hand-authored LIR."""
    if role not in ("operations", "trace"):
        return source

    text = source.read_text(encoding="utf-8")
    if role == "operations":
        pattern = re.compile(
            r"/\* Call one opcode API through the common-area table; false exits the trace\. \*/\n"
            r".*?(?=\n\.macro FB_LOCAL_SET_CONST_I32)",
            re.DOTALL,
        )
        text, replacements = pattern.subn(
            lambda _match: RUNTIME_API_STENCIL_OPTIMIZATION, text, count=1
        )
    else:
        pattern = re.compile(
            r"FB_TRACE_BEGIN fb_trace_return_abort\n.*?FB_TRACE_END fb_trace_return_abort",
            re.DOTALL,
        )
        text, replacements = pattern.subn(
            lambda _match: TRACE_ABORT_STENCIL_OPTIMIZATION, text, count=1
        )
    if replacements != 1:
        raise GenerationError(
            f"{role} stencil source did not contain its expected optimization anchor"
        )

    optimized_source = directory / f"{source.stem}.optimized.S"
    optimized_source.write_text(text, encoding="utf-8")
    return optimized_source


def command_output(command: Sequence[str], label: str) -> str:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        raise GenerationError(f"{label} failed:\n{completed.stdout}")
    return completed.stdout


def find_tool(explicit: str | None, names: Sequence[str]) -> str:
    if explicit is not None:
        return explicit
    for name in names:
        resolved = shutil.which(name)
        if resolved is not None:
            return resolved
    raise GenerationError(f"could not find any of: {', '.join(names)}")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def compile_abi_probe(
    clang: str, objcopy: str, target: str | None, directory: Path
) -> tuple[int, ...]:
    root = repo_root()
    source = root / "tools/jit_stencil_gen/abi_probe.cxx"
    object_path = directory / "jit_abi_probe.o"
    section_path = directory / "jit_abi_probe.bin"
    command = [clang, "-std=c++23", "-O0", "-c", str(source), "-o", str(object_path)]
    if target is not None:
        command.extend(("--target", target))
    if sys.platform != "win32":
        command.append("-fPIC")
    command_output(command, "production C++ ABI probe compilation")
    command_output(
        [objcopy, "--dump-section", f".fb_abi={section_path}", str(object_path)],
        "production C++ ABI extraction",
    )
    raw = section_path.read_bytes()
    if len(raw) != 13 * 4:
        raise GenerationError(f"production ABI section has {len(raw)} bytes; expected 52")
    return struct.unpack("<13I", raw)


def parse_symbols(nm: str, object_path: Path) -> dict[str, Symbol]:
    output = command_output(
        [nm, "-n", "-S", "--defined-only", str(object_path)],
        "object symbol inspection",
    )
    symbols: dict[str, Symbol] = {}
    for line in output.splitlines():
        match = SYMBOL_LINE.match(line)
        if match is None:
            continue
        address = int(match.group(1), 16)
        size_text = match.group(2)
        name = match.group(4)
        if name in symbols:
            raise GenerationError(f"duplicate object symbol {name!r}")
        symbols[name] = Symbol(name, address, int(size_text, 16) if size_text else 0)
    return symbols


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
        if not mnemonic.startswith("j") or operand.startswith("*"):
            continue
        target_match = DIRECT_TARGET.match(operand)
        if target_match is None:
            continue
        target = int(target_match.group(1), 16)
        owner = next(
            (
                symbol
                for symbol in symbols.values()
                if symbol.size != 0 and symbol.address <= address < symbol.address + symbol.size
            ),
            None,
        )
        if owner is None or not (owner.address <= target < owner.address + owner.size):
            continue
        if address in markers.get("next", set()) | markers.get("abort", set()) | {
            *markers.get("start", set()),
            *markers.get("exit", set()),
        }:
            continue
        if len(encoded) not in (2, 5, 6):
            raise GenerationError(
                f"unsupported direct branch encoding at {owner.name}+0x{address - owner.address:x}"
            )
        branch_map.setdefault(owner.name, []).append(
            BranchFixup(address - owner.address, len(encoded), target - owner.address)
        )
    return {
        name: tuple(sorted(values, key=lambda item: item.instruction_offset))
        for name, values in branch_map.items()
    }


def check_pic_object(objdump: str, object_path: Path) -> None:
    output = command_output([objdump, "-r", str(object_path)], "relocation inspection")
    active_text_relocations = False
    for line in output.splitlines():
        if line.startswith("RELOCATION RECORDS FOR ["):
            active_text_relocations = line == "RELOCATION RECORDS FOR [.text]:"
            continue
        if active_text_relocations and line.strip():
            raise GenerationError(f"PIC stencil object retains a .text relocation: {line.strip()}")
        relocation = re.search(r"\bR_[A-Z0-9_]+\b", line)
        if relocation is not None and re.search(
            r"(?:GOT|PLT|R_X86_64_64)", relocation.group(), re.IGNORECASE
        ):
            raise GenerationError(f"PIC stencil contains a GOT/PLT relocation: {line}")


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
        instruction_offset = (
            None if instruction_symbol is None else instruction_symbol.address - symbol.address
        )
        instruction_width = 4 if instruction_symbol is not None else 0
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


def role_definitions(role: str, abi: tuple[int, ...], config_path: Path) -> dict[str, int]:
    (
        local_base,
        trap_code,
        chain_target,
        common_relative,
        const_rel,
        header,
        context_ip,
        context_sp,
        context_flags,
        boundary_metadata_bytes,
        context_rel,
        context_current_block_index,
        trace_block_index,
    ) = abi
    config = runpy.run_path(str(config_path))
    api_table_pointer_offset = (
        config["JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET"]
        + config["JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES"]
    )
    if role == "operations":
        return {
            "FB_CONTEXT_LOCAL_BASE_OFFSET": local_base,
            "FB_CONTEXT_IP_OFFSET": context_ip,
            "FB_CONTEXT_SP_OFFSET": context_sp,
            "FB_CONTEXT_RUNTIME_FLAGS_OFFSET": context_flags,
            "FB_TRACE_COMMON_RELATIVE_OFFSET": common_relative,
            "FB_TRACE_RUNTIME_API_TABLE_POINTER_OFFSET": api_table_pointer_offset,
            "FB_CONTEXT_REL_OFFSET": context_rel,
            "FB_CPS_EXIT_MASK": 0xF00,
        }
    if role == "trace":
        return {
            "FB_CONTEXT_TRAP_CODE_OFFSET": trap_code,
            "FB_TRACE_COMMON_RELATIVE_OFFSET": common_relative,
            "FB_TRACE_CONST_REL_OFFSET": const_rel,
            "FB_TRACE_HEADER_BYTES": header,
            "FB_TRACE_CHAIN_ENTRY_OFFSET": config["JIT_X64_TRACE_CHAIN_ENTRY_OFFSET"],
            "FB_CONTEXT_REL_OFFSET": context_rel,
        }
    if role == "common":
        return {
            "FB_TRACE_CONST_REL_OFFSET": const_rel,
            "FB_TRACE_HEADER_BYTES": header,
            "FB_TRACE_CHAIN_TARGET_OFFSET": chain_target,
            "FB_TRACE_CHAIN_ENTRY_OFFSET": config["JIT_X64_TRACE_CHAIN_ENTRY_OFFSET"],
            "FB_TRACE_BOUNDARY_METADATA_BYTES": boundary_metadata_bytes,
            "FB_CONTEXT_IP_OFFSET": context_ip,
            "FB_CONTEXT_REL_OFFSET": context_rel,
            "FB_CONTEXT_CURRENT_BLOCK_INDEX_OFFSET": context_current_block_index,
            "FB_TRACE_BLOCK_INDEX_OFFSET": trace_block_index,
        }
    raise GenerationError(f"unsupported assembly role {role!r}")


def generate(args: argparse.Namespace) -> None:
    clang = find_tool(args.clang, ("clang",))
    clangxx = find_tool(args.clangxx, ("clang++",))
    objcopy = find_tool(args.objcopy, ("llvm-objcopy", "objcopy"))
    objdump = find_tool(args.objdump, ("llvm-objdump", "objdump"))
    nm = find_tool(args.nm, ("llvm-nm", "nm"))
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    header = (output_dir / args.header).resolve()
    header.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="jit-stencil-") as temp_name:
        directory = Path(temp_name)
        abi = compile_abi_probe(clangxx, objcopy, args.target, directory)
        object_path = directory / f"{source.stem}.o"
        raw_path = directory / f"{source.stem}.bin"
        config_path = args.config_source or (repo_root() / "experiments/pysim/tier1_core/config.py")
        definitions = role_definitions(args.role, abi, config_path)
        assembly_source = optimized_assembly_source(args.role, source, directory)
        compile_command = [clang, "-c", "-fPIC", str(assembly_source), "-o", str(object_path)]
        if args.target is not None:
            compile_command.extend(("--target", args.target))
        for name, value in definitions.items():
            compile_command.append(f"-D{name}={value}")
        command_output(compile_command, f"{args.role} assembly compilation")
        check_pic_object(objdump, object_path)
        command_output(
            [objcopy, "--dump-section", f".text={raw_path}", str(object_path)],
            "raw .text extraction",
        )
        raw = raw_path.read_bytes()
        symbols = parse_symbols(nm, object_path)

        if args.assembly_output is not None:
            args.assembly_output.resolve().parent.mkdir(parents=True, exist_ok=True)
            preprocess_command = [
                clang,
                "-E",
                "-P",
                "-x",
                "assembler-with-cpp",
                str(assembly_source),
            ]
            if args.target is not None:
                preprocess_command.extend(("--target", args.target))
            preprocess_command.extend(f"-D{name}={value}" for name, value in definitions.items())
            preprocessed = command_output(
                preprocess_command,
                "assembly preprocessing",
            )
            args.assembly_output.resolve().write_text(preprocessed, encoding="utf-8")
        (output_dir / args.binary).write_bytes(raw)

        if args.role == "common":
            emit_common_header(header, raw, symbols)
        else:
            branches = parse_disassembly(objdump, object_path, symbols)
            stencils, arrays = collect_stencils(args.role, symbols, branches)
            emit_stencil_header(args.namespace, header, raw, stencils, arrays)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--role", choices=("operations", "trace", "common"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--header", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--namespace", default="generated")
    parser.add_argument("--assembly-output", type=Path)
    parser.add_argument("--config-source", type=Path)
    parser.add_argument("--clang")
    parser.add_argument("--clangxx")
    parser.add_argument("--objcopy")
    parser.add_argument("--objdump")
    parser.add_argument("--nm")
    parser.add_argument("--target")
    args = parser.parse_args()
    try:
        generate(args)
    except (GenerationError, OSError, ValueError) as error:
        print(f"jit-stencil-gen: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

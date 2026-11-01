"""Inputs that depend on the target ABI and on the assembly source before assembling."""

from __future__ import annotations

import re
import runpy
import struct
import sys
from pathlib import Path

from .elf_reader import read_section_bytes
from .stencil_model import GenerationError
from .toolchain import command_output

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
  FB_LOCAL_BRANCH_REL8 0x74, .Lruntime_api_abort_sync\@
  mov r10, qword ptr [rsp + 56]
  mov qword ptr [r11 + FB_CONTEXT_SP_OFFSET], r10
  xor r8d, r8d
  FB_LOCAL_BRANCH_REL8 0xeb, .Lruntime_api_exit\@
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
  FB_LOCAL_BRANCH_REL8 0x75, .Lruntime_api_abort\@
  FB_LOCAL_BRANCH_REL8 0xeb, .Lruntime_api_continue\@
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
    if role == "trace":
        # The trace return stubs are hand-written as plain `ret`: the wrapper decides the
        # outcome from execution_context, so the abort stub needs no link slot.
        return source

    text = source.read_text(encoding="utf-8")
    if role == "operations":
        pattern = re.compile(
            r"/\* Call one opcode API through the common-area table; false exits the trace\. \*/\n"
            r".*?(?=\n\n/\* Tail-call one Interpreter CPS call handler)",
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


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
            "FB_CONTEXT_RUNTIME_FLAGS_OFFSET": context_flags,
            "FB_CPS_EXIT_MASK": 0xF00,
            "FB_TRACE_COMMON_RELATIVE_OFFSET": common_relative,
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


ABI_SECTION = ".fb_abi"
ABI_FIELD_COUNT = 13


def compile_abi_probe(clang: str, target: str | None, directory: Path) -> tuple[int, ...]:
    """Compile the production C++ ABI probe and return its 13 ABI words."""
    source = repo_root() / "tools/jit_stencil_gen/abi_probe.cxx"
    object_path = directory / "jit_abi_probe.o"
    command = [clang, "-std=c++23", "-O0", "-c", str(source), "-o", str(object_path)]
    if target is not None:
        command.extend(("--target", target))
    if sys.platform != "win32":
        command.append("-fPIC")
    command_output(command, "production C++ ABI probe compilation")
    raw = read_section_bytes(object_path, ABI_SECTION)
    if len(raw) != ABI_FIELD_COUNT * 4:
        raise GenerationError(
            f"production ABI section has {len(raw)} bytes; expected {ABI_FIELD_COUNT * 4}"
        )
    return struct.unpack(f"<{ABI_FIELD_COUNT}I", raw)

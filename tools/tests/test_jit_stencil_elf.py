"""Exercise the ELF reader and the hole-table invariants of the JIT stencil generator."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = str(ROOT / "tools/jit_stencil_gen")
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

elf_reader = importlib.import_module("stencilgen.elf_reader")
hole_table = importlib.import_module("stencilgen.hole_table")
stencil_model = importlib.import_module("stencilgen.stencil_model")
Symbol = stencil_model.Symbol
GenerationError = stencil_model.GenerationError


def assemble(tmp_path: Path, source: str, name: str = "stencil.o") -> Path:
    """Assemble an x86-64 PIC object with the same clang integrated assembler the build uses."""
    asm = tmp_path / (Path(name).stem + ".S")
    asm.write_text(source, encoding="utf-8")
    obj = tmp_path / name
    subprocess.run(
        ["clang", "-c", "-fPIC", "--target=x86_64-linux-gnu", str(asm), "-o", str(obj)],
        check=True,
        capture_output=True,
        text=True,
    )
    return obj


def test_read_object_returns_text_bytes_and_defined_symbols(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        ".intel_syntax noprefix\n.text\n"
        ".globl fb_stencil_add\n.type fb_stencil_add,@function\nfb_stencil_add:\n"
        "  mov eax, 1\n  ret\n.size fb_stencil_add,.-fb_stencil_add\n",
    )
    text, symbols = elf_reader.read_object(obj)
    assert text == bytes([0xB8, 0x01, 0x00, 0x00, 0x00, 0xC3])
    assert symbols["fb_stencil_add"] == Symbol("fb_stencil_add", 0, 6)


def test_read_section_bytes_returns_a_named_section(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        '.section .fb_abi,"",@progbits\n.long 7, 9\n.text\nret\n',
    )
    assert elf_reader.read_section_bytes(obj, ".fb_abi") == bytes([7, 0, 0, 0, 9, 0, 0, 0])


def test_read_section_bytes_fails_for_a_missing_section(tmp_path: Path) -> None:
    obj = assemble(tmp_path, ".text\nret\n")
    with pytest.raises(GenerationError, match=re.escape("has no .fb_abi section")):
        elf_reader.read_section_bytes(obj, ".fb_abi")


def test_relocation_in_text_is_rejected(tmp_path: Path) -> None:
    obj = assemble(tmp_path, ".intel_syntax noprefix\n.text\ncall external_target\n")
    with pytest.raises(GenerationError, match=re.escape("retains a .text relocation")):
        elf_reader.read_object(obj)


def test_got_relocation_in_data_is_rejected(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        ".intel_syntax noprefix\n.section .data\n.long external_target@GOTPCREL\n.text\nret\n",
    )
    with pytest.raises(GenerationError, match="GOT/PLT relocation"):
        elf_reader.read_object(obj)


def test_patch_slot_outside_its_stencil_fails() -> None:
    symbols = {
        "fb_stencil_bad": Symbol("fb_stencil_bad", 0, 4),
        "fb_jit_patch_slot_4__fb_stencil_bad": Symbol("fb_jit_patch_slot_4__fb_stencil_bad", 10, 0),
    }
    with pytest.raises(GenerationError, match="immediate patch slot outside its code"):
        hole_table.collect_stencils("operations", symbols, {})


def test_duplicate_stencil_variant_index_fails() -> None:
    symbols = {
        "fb_stencil_local_get_0_word_0": Symbol("fb_stencil_local_get_0_word_0", 0, 4),
        "fb_stencil_local_get_0_word_2": Symbol("fb_stencil_local_get_0_word_2", 4, 4),
    }
    with pytest.raises(GenerationError, match="is not contiguous from zero"):
        hole_table.collect_stencils("operations", symbols, {})


def test_valid_patch_slot_is_recorded_with_its_width() -> None:
    symbols = {
        "fb_stencil_const": Symbol("fb_stencil_const", 0, 8),
        "fb_jit_patch_slot_4__fb_stencil_const": Symbol(
            "fb_jit_patch_slot_4__fb_stencil_const", 1, 0
        ),
    }
    direct, arrays = hole_table.collect_stencils("operations", symbols, {})
    assert arrays == {}
    assert len(direct) == 1
    assert direct[0].patch_slot_offset == 1
    assert direct[0].patch_slot_width == 4

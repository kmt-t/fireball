"""Exercise the direct-branch fixup parser of the JIT stencil generator with canned disassembly."""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_assembly", ROOT / "tools/jit_stencil_gen/generate_assembly.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_generator()
Symbol = generator.Symbol
BranchFixup = generator.BranchFixup
GenerationError = generator.GenerationError


def canned_objdump(tmp_path: Path) -> Path:
    # parse_disassembly calls `objdump -d -Mintel <object>`; this stub prints the object file as text.
    stub = tmp_path / "objdump"
    stub.write_text('#!/bin/sh\nexec cat "$3"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


def parse(tmp_path: Path, disassembly: str, symbols: dict[str, Symbol]) -> dict[str, object]:
    listing = tmp_path / "listing.txt"
    listing.write_text(disassembly, encoding="utf-8")
    return dict(generator.parse_disassembly(str(canned_objdump(tmp_path)), listing, symbols))


STENCIL = {"fb_stencil_a": Symbol("fb_stencil_a", 0, 0x20)}


def test_hex_prefixed_direct_target_is_recorded(tmp_path: Path) -> None:
    # llvm-objdump prints the target as 0x18, which the former pattern could not read.
    disassembly = "  10: 0f 85 06 00 00 00  \tjne 0x18 <fb_stencil_a+0x18>\n"
    result = parse(tmp_path, disassembly, STENCIL)
    assert result == {"fb_stencil_a": (BranchFixup(0x10, 6, 0x18),)}


def test_numeric_direct_target_is_recorded(tmp_path: Path) -> None:
    # GNU objdump prints the target without a prefix.
    disassembly = "  10: 0f 85 06 00 00 00  \tjne 18 <fb_stencil_a+0x18>\n"
    result = parse(tmp_path, disassembly, STENCIL)
    assert result == {"fb_stencil_a": (BranchFixup(0x10, 6, 0x18),)}


def test_short_direct_branch_is_recorded_with_its_size(tmp_path: Path) -> None:
    disassembly = "  10: 74 06  \tje 0x18 <fb_stencil_a+0x18>\n"
    result = parse(tmp_path, disassembly, STENCIL)
    assert result == {"fb_stencil_a": (BranchFixup(0x10, 2, 0x18),)}


def test_unreadable_direct_operand_fails_instead_of_being_dropped(tmp_path: Path) -> None:
    disassembly = "  10: 74 06  \tje somewhere_unknown\n"
    with pytest.raises(GenerationError, match="unsupported direct branch"):
        parse(tmp_path, disassembly, STENCIL)


def test_direct_branch_leaving_its_stencil_fails(tmp_path: Path) -> None:
    disassembly = "  10: 74 36  \tje 0x48 <fb_other>\n"
    with pytest.raises(GenerationError, match="leaves its stencil"):
        parse(tmp_path, disassembly, STENCIL)


def test_direct_branch_outside_every_stencil_fails(tmp_path: Path) -> None:
    disassembly = "  40: 74 06  \tje 0x48 <fb_stencil_a+0x48>\n"
    with pytest.raises(GenerationError, match="outside every stencil"):
        parse(tmp_path, disassembly, STENCIL)


def test_indirect_register_jump_is_not_a_fixup(tmp_path: Path) -> None:
    disassembly = "  10: ff e0  \tjmp rax\n"
    assert parse(tmp_path, disassembly, STENCIL) == {}


def test_slot_marker_jump_is_not_a_fixup(tmp_path: Path) -> None:
    symbols = {
        "fb_stencil_a": Symbol("fb_stencil_a", 0, 0x20),
        "fb_jit_next_slot__fb_stencil_a": Symbol("fb_jit_next_slot__fb_stencil_a", 0x10, 0),
    }
    disassembly = "  10: e9 00 00 00 00  \tjmp 15 <fb_jit_next_slot__fb_stencil_a+0x5>\n"
    assert parse(tmp_path, disassembly, symbols) == {}

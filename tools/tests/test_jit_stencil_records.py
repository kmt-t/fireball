"""Exercise the branch-record reader of the JIT stencil generator with real assembled objects."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "tools/jit_stencil_gen"
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

branch_records = importlib.import_module("stencilgen.branch_records")
stencil_model = importlib.import_module("stencilgen.stencil_model")
GenerationError = stencil_model.GenerationError
BranchFixup = stencil_model.BranchFixup
read_branch_records = branch_records.read_branch_records
read_object = importlib.import_module("stencilgen.elf_reader").read_object

LIR_DIR = ROOT / "experiments/pysim/native/tier3_plugins/jit/lir/x64"


def assemble(tmp_path: Path, body: str) -> Path:
    """Assemble a PIC x86-64 object whose stencil sources include the shared branch macros."""
    asm = tmp_path / "stencil.S"
    asm.write_text(
        '.intel_syntax noprefix\n#include "branch_records.inc"\n.text\n.Ltext_base:\n' + body,
        encoding="utf-8",
    )
    obj = tmp_path / "stencil.o"
    subprocess.run(
        [
            "clang",
            "-c",
            "-fPIC",
            "--target=x86_64-linux-gnu",
            f"-I{LIR_DIR}",
            str(asm),
            "-o",
            str(obj),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return obj


def stencil(name: str, body: str) -> str:
    return f".globl {name}\n.type {name},@function\n{name}:\n{body}.size {name},.-{name}\n"


def test_macro_branch_is_recorded_relative_to_its_stencil(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        stencil(
            "fb_stencil_a",
            "  cmp eax, 0\n  FB_LOCAL_BRANCH_REL8 0x75, .Ltarget\n  nop\n.Ltarget:\n  ret\n",
        ),
    )
    _, symbols = read_object(obj)
    assert read_branch_records(obj, symbols) == {
        "fb_stencil_a": (BranchFixup(3, 2, 6),),
    }


def test_near_branch_is_recorded_with_its_six_byte_size(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        stencil(
            "fb_stencil_b",
            "  test eax, eax\n  FB_LOCAL_BRANCH_REL32 0x84, .Lend\n  nop\n.Lend:\n  ret\n",
        ),
    )
    _, symbols = read_object(obj)
    fixups = read_branch_records(obj, symbols)["fb_stencil_b"]
    assert fixups == (BranchFixup(2, 6, 9),)


def test_objects_without_macro_branches_have_no_records(tmp_path: Path) -> None:
    obj = tmp_path / "plain.o"
    asm = tmp_path / "plain.S"
    asm.write_text(
        ".intel_syntax noprefix\n.text\n" + stencil("fb_stencil_c", "  ret\n"), encoding="utf-8"
    )
    subprocess.run(
        ["clang", "-c", "-fPIC", "--target=x86_64-linux-gnu", str(asm), "-o", str(obj)],
        check=True,
        capture_output=True,
        text=True,
    )
    _, symbols = read_object(obj)
    with pytest.raises(GenerationError, match=re.escape("has no .fb_branch_records section")):
        read_branch_records(obj, symbols)


def test_record_with_unsupported_size_fails(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        stencil("fb_stencil_d", "  nop\n  ret\n")
        + '.pushsection .fb_branch_records, "a"\n.long 0, 1, 3\n.popsection\n',
    )
    _, symbols = read_object(obj)
    with pytest.raises(GenerationError, match="unsupported branch record size 3"):
        read_branch_records(obj, symbols)


def test_record_outside_every_stencil_fails(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        stencil("fb_stencil_e", "  ret\n")
        + '.pushsection .fb_branch_records, "a"\n.long 50, 0, 2\n.popsection\n',
    )
    _, symbols = read_object(obj)
    with pytest.raises(GenerationError, match="lies outside every stencil"):
        read_branch_records(obj, symbols)


def test_record_target_outside_its_stencil_fails(tmp_path: Path) -> None:
    obj = assemble(
        tmp_path,
        stencil("fb_stencil_f", "  nop\n  ret\n")
        + '.pushsection .fb_branch_records, "a"\n.long 0, 40, 2\n.popsection\n',
    )
    _, symbols = read_object(obj)
    with pytest.raises(GenerationError, match="leaves its stencil"):
        read_branch_records(obj, symbols)

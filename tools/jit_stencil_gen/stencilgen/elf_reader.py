"""Read the ELF object that the assembler produces: sections, symbols, and relocations.

pyelftools handles ELF32 (ARM, M33) and ELF64 (x86-64) with relocation type names.
"""

from __future__ import annotations

from pathlib import Path

from elftools.elf.descriptions import describe_reloc_type
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from .stencil_model import GenerationError, Symbol

# A stencil object must not depend on the loader: no relocation in .text, and no
# GOT, PLT, or absolute 64-bit relocation in any section.
_TEXT_SECTION = ".text"


def _defined_symbols(elf: ELFFile) -> dict[str, Symbol]:
    symbols: dict[str, Symbol] = {}
    for section in elf.iter_sections():
        if section.name != ".symtab":
            continue
        for entry in section.iter_symbols():
            name = entry.name
            if name == "" or entry["st_shndx"] == "SHN_UNDEF":
                continue
            if name in symbols:
                raise GenerationError(f"duplicate object symbol {name!r}")
            symbols[name] = Symbol(name, int(entry["st_value"]), int(entry["st_size"]))
    return symbols


def _reject_non_pic_relocations(elf: ELFFile) -> None:
    for section in elf.iter_sections():
        if not isinstance(section, RelocationSection):
            continue
        target = elf.get_section(section["sh_info"]).name
        for relocation in section.iter_relocations():
            kind = describe_reloc_type(relocation["r_info_type"], elf)
            if target == _TEXT_SECTION:
                raise GenerationError(
                    f"PIC stencil object retains a .text relocation: {kind} at {relocation['r_offset']:#x}"
                )
            if "GOT" in kind or "PLT" in kind or kind == "R_X86_64_64":
                raise GenerationError(f"PIC stencil contains a GOT/PLT relocation: {kind}")


def read_object(path: Path) -> tuple[bytes, dict[str, Symbol]]:
    """Return the .text bytes and the defined symbols of a PIC stencil object.

    Raises GenerationError when the object retains a relocation that a loader would have to fix.
    """
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        _reject_non_pic_relocations(elf)
        text = elf.get_section_by_name(_TEXT_SECTION)
        if text is None:
            raise GenerationError(f"object {path.name} has no .text section")
        return text.data(), _defined_symbols(elf)


def read_section_bytes(path: Path, name: str) -> bytes:
    """Return the raw bytes of one named section of an object file."""
    with path.open("rb") as stream:
        section = ELFFile(stream).get_section_by_name(name)
        if section is None:
            raise GenerationError(f"object {path.name} has no {name} section")
        return section.data()

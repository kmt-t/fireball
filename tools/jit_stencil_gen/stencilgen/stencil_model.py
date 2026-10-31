"""Immutable data types shared by the stencil generator stages."""

from __future__ import annotations

from dataclasses import dataclass


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

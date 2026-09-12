"""Interpreter-side views over the fixed Native WASM stacks.

This module is the editable Python implementation for stack windows used by
the interpreter.  ``interop_abi.py`` owns only the ctypes layout mirrors;
``interpreter.py`` owns opcode dispatch and does not contain the Native stack
adapter implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from interop_abi import (
    ControlFrameNative,
    ControlStackNative,
    NativeValueStack,
    NATIVE_CONTROL_STACK_CAPACITY,
)
from wasm_module import F32, F64, I64


class ControlFrameKind(IntEnum):
    BLOCK = 0
    LOOP = 1
    IF = 2


@dataclass(slots=True)
class ControlFrame:
    kind: ControlFrameKind
    start: int
    match_end: int
    stack_height: int


class NativeControlStack:
    """Python access shim over the fixed Native control-frame record."""

    __slots__ = ("_capacity", "_native")

    def __init__(self, capacity: int = NATIVE_CONTROL_STACK_CAPACITY):
        assert 0 <= capacity <= NATIVE_CONTROL_STACK_CAPACITY
        self._capacity = capacity
        self._native = ControlStackNative()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def native(self) -> ControlStackNative:
        return self._native

    def __len__(self) -> int:
        return int(self._native.size)

    def __bool__(self) -> bool:
        return self._native.size != 0

    def _index(self, index: int) -> int:
        normalized = index if index >= 0 else len(self) + index
        if not 0 <= normalized < len(self):
            raise IndexError("native control stack index out of range")
        return normalized

    @staticmethod
    def _decode(native_frame: ControlFrameNative) -> ControlFrame:
        return ControlFrame(
            ControlFrameKind(int(native_frame.kind)),
            int(native_frame.start),
            int(native_frame.match_end),
            int(native_frame.stack_height),
        )

    def __getitem__(self, index: int) -> ControlFrame:
        return self._decode(self._native.frames[self._index(index)])

    def push_back(self, frame: ControlFrame) -> bool:
        size = len(self)
        if size >= self._capacity:
            return False
        native_frame = self._native.frames[size]
        native_frame.kind = int(frame.kind)
        native_frame.start = frame.start
        native_frame.match_end = frame.match_end
        native_frame.stack_height = frame.stack_height
        self._native.frames[size] = native_frame
        self._native.size = size + 1
        return True

    def pop_back(self) -> ControlFrame:
        if not self:
            raise IndexError("native control frame stack underflow")
        index = len(self) - 1
        frame = self._decode(self._native.frames[index])
        self._native.frames[index] = ControlFrameNative()
        self._native.size = index
        return frame

    def truncate(self, size: int) -> None:
        assert 0 <= size <= len(self)
        while len(self) > size:
            self.pop_back()


class ControlFrameWindow:
    """Current function's window into the context-owned control stack."""

    __slots__ = ("_base", "_storage")

    def __init__(self, storage: NativeControlStack, base: int):
        self._storage = storage
        self._base = base

    def __len__(self) -> int:
        return len(self._storage) - self._base

    def __bool__(self) -> bool:
        return len(self) != 0

    def _absolute_index(self, index: int) -> int:
        absolute = self._base + index if index >= 0 else len(self._storage) + index
        if not (self._base <= absolute < len(self._storage)):
            raise IndexError("control frame index out of range")
        return absolute

    def __getitem__(self, index: int) -> ControlFrame:
        return self._storage[self._absolute_index(index)]

    def push_back(self, frame: ControlFrame) -> bool:
        return self._storage.push_back(frame)

    def pop_back(self) -> ControlFrame:
        if not self:
            raise IndexError("native control frame stack underflow")
        return self._storage.pop_back()

    def truncate(self, depth: int) -> None:
        """Remove frames above the requested relative stack depth."""
        if depth < 0 or depth > len(self):
            return
        while len(self) > depth:
            self._storage.pop_back()


class LocalStackWindow:
    """Typed view over one frame's locals in the raw Native local stack."""

    __slots__ = ("_base", "_storage", "_types")

    def __init__(self, storage: NativeValueStack, base: int, types: tuple[str, ...]):
        self._storage = storage
        self._base = base
        self._types = types

    def __len__(self) -> int:
        return len(self._types)

    def _local_index(self, index: int) -> int:
        normalized = index if index >= 0 else len(self) + index
        if not 0 <= normalized < len(self):
            raise IndexError("local stack index out of range")
        return normalized

    def _slot_index(self, index: int) -> int:
        normalized = self._local_index(index)
        return self._base + sum(
            2 if value_type in (I64, F64) else 1 for value_type in self._types[:normalized]
        )

    def __getitem__(self, index: int) -> int | float:
        normalized = self._local_index(index)
        slot = self._slot_index(normalized)
        value_type = self._types[normalized]
        if value_type == I64:
            return self._storage.read_i64(slot)
        if value_type == F32:
            return self._storage.read_f32(slot)
        if value_type == F64:
            return self._storage.read_f64(slot)
        return self._storage.read_i32(slot)

    def value_type(self, index: int) -> str:
        return self._types[self._local_index(index)]

    def get_i32(self, index: int) -> int:
        return self._storage.read_i32(self._slot_index(index))

    def get_i64(self, index: int) -> int:
        return self._storage.read_i64(self._slot_index(index))

    def get_f32(self, index: int) -> float:
        return self._storage.read_f32(self._slot_index(index))

    def get_f64(self, index: int) -> float:
        return self._storage.read_f64(self._slot_index(index))

    def set_i32(self, index: int, value: int) -> None:
        self._storage.write_i32(self._slot_index(index), value)

    def set_i64(self, index: int, value: int) -> None:
        self._storage.write_i64(self._slot_index(index), value)

    def set_f32(self, index: int, value: float) -> None:
        self._storage.write_f32(self._slot_index(index), value)

    def set_f64(self, index: int, value: float) -> None:
        self._storage.write_f64(self._slot_index(index), value)

    def __setitem__(self, index: int, value: int | float) -> None:
        normalized = self._local_index(index)
        slot = self._slot_index(normalized)
        value_type = self._types[normalized]
        if value_type == I64:
            self._storage.write_i64(slot, int(value))
        elif value_type == F32:
            self._storage.write_f32(slot, float(value))
        elif value_type == F64:
            self._storage.write_f64(slot, float(value))
        else:
            self._storage.write_i32(slot, int(value))


# Private aliases keep the interpreter's handler type signatures compact while
# the editable implementation remains in this separate module.
_ControlFrameWindow = ControlFrameWindow
_LocalStackWindow = LocalStackWindow


__all__ = [
    "ControlFrame",
    "ControlFrameKind",
    "ControlFrameWindow",
    "LocalStackWindow",
    "NativeControlStack",
    "_ControlFrameWindow",
    "_LocalStackWindow",
]

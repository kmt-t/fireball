"""Interpreter-side views over the fixed Native WASM stacks.

This module is the editable Python implementation for stack windows used by
the interpreter.  ``interop_abi.py`` owns only the ctypes layout mirrors;
``interpreter.py`` owns opcode dispatch and does not contain the Native stack
adapter implementation.
"""

from __future__ import annotations

from enum import IntEnum

from interop_abi import (
    NATIVE_CONTROL_STACK_CAPACITY,
    ControlFrameNative,
    ControlStackNative,
    NativeValueStack,
)
from wasm_module import LocalWidthMap


class ControlFrameKind(IntEnum):
    BLOCK = 0
    LOOP = 1
    IF = 2


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
        size = int(self._native.size)
        normalized = index if index >= 0 else size + index
        if not 0 <= normalized < size:
            assert False, "native control stack index out of range"
        return normalized

    def __getitem__(self, index: int) -> ControlFrameNative:
        return self._native.frames[self._index(index)]

    def push_back(
        self,
        kind: ControlFrameKind,
        start: int,
        match_end: int,
        stack_height: int,
        result_arity: int = 0,
    ) -> bool:
        size = int(self._native.size)
        if size >= self._capacity:
            return False
        native_frame = self._native.frames[size]
        native_frame.kind = int(kind)
        native_frame.start = start
        native_frame.match_end = match_end
        native_frame.stack_height = stack_height
        native_frame.result_arity = result_arity
        self._native.frames[size] = native_frame
        self._native.size = size + 1
        return True

    def pop_back(self) -> ControlFrameNative:
        if not self:
            assert False, "native control frame stack underflow"
        index = int(self._native.size) - 1
        frame = self._native.frames[index]
        self._native.size = index
        return frame

    def truncate(self, size: int) -> None:
        assert 0 <= size <= len(self)
        self._native.size = size

    def set_size(self, size: int) -> None:
        """Set the native control-stack pointer without walking frames."""

        assert 0 <= size <= self._capacity
        self._native.size = size


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
        storage_size = int(self._storage.native.size)
        absolute = self._base + index if index >= 0 else storage_size + index
        if not (self._base <= absolute < storage_size):
            assert False, "control frame index out of range"
        return absolute

    def __getitem__(self, index: int) -> ControlFrameNative:
        return self._storage[self._absolute_index(index)]

    def push_back(
        self,
        kind: ControlFrameKind,
        start: int,
        match_end: int,
        stack_height: int,
        result_arity: int = 0,
    ) -> bool:
        return self._storage.push_back(kind, start, match_end, stack_height, result_arity)

    def pop_back(self) -> ControlFrameNative:
        if not self:
            assert False, "native control frame stack underflow"
        return self._storage.pop_back()

    def truncate(self, depth: int) -> None:
        """Remove frames above the requested relative stack depth."""
        assert 0 <= depth <= len(self)
        self._storage.set_size(self._base + depth)

    def branch(
        self,
        depth: int,
        values: NativeValueStack,
        outer_result_arity: int = 0,
    ) -> int | None:
        """Unwind this function's native control frames and operand stack."""

        frame_count = len(self)
        assert 0 <= depth <= frame_count
        if depth == frame_count:
            assert 0 <= outer_result_arity <= 2
            final_size = len(values)
            assert outer_result_arity <= final_size
            result_start = final_size - outer_result_arity
            result0 = values.raw_at(result_start) if outer_result_arity >= 1 else 0
            result1 = values.raw_at(result_start + 1) if outer_result_arity >= 2 else 0
            self._storage.set_size(self._base)
            values.truncate(result_start)
            if outer_result_arity >= 1:
                pushed = values.push_back(result0)
                assert pushed
            if outer_result_arity >= 2:
                pushed = values.push_back(result1)
                assert pushed
            return None
        target_index = frame_count - depth - 1
        target = self[target_index]
        saved_height = int(target.stack_height)
        result_arity = (
            0 if int(target.kind) == int(ControlFrameKind.LOOP) else int(target.result_arity)
        )
        assert 0 <= result_arity <= 2
        final_size = len(values)
        assert saved_height <= final_size
        assert result_arity <= final_size - saved_height
        result_start = final_size - result_arity
        result0 = values.raw_at(result_start) if result_arity >= 1 else 0
        result1 = values.raw_at(result_start + 1) if result_arity >= 2 else 0
        values.truncate(saved_height)
        if result_arity >= 1:
            pushed = values.push_back(result0)
            assert pushed
        if result_arity >= 2:
            pushed = values.push_back(result1)
            assert pushed
        if int(target.kind) == int(ControlFrameKind.LOOP):
            self._storage.set_size(self._base + target_index + 1)
            return int(target.start) + 2
        self._storage.set_size(self._base + target_index)
        return int(target.match_end) + 1


class LocalStackWindow:
    """Typed access methods over one frame's raw Native local slots."""

    __slots__ = ("_base", "_slot_count", "_storage", "_widths")

    def __init__(self, storage: NativeValueStack, base: int, widths: LocalWidthMap):
        """`widths` holds each local's raw width and the frame's slot stride (`slot_words`)."""
        assert widths.slot_words == 1 or widths.slot_words == 2
        self._storage = storage
        self._base = base
        self._widths = widths
        self._slot_count = len(widths) * widths.slot_words

    def __len__(self) -> int:
        return len(self._widths)

    def _local_index(self, index: int) -> int:
        local_count = len(self._widths)
        normalized = index if index >= 0 else local_count + index
        if not 0 <= normalized < local_count:
            assert False, "local stack index out of range"
        return normalized

    def _slot_index(self, index: int) -> int:
        normalized = self._local_index(index)
        return self._base + normalized * self._widths.slot_words

    def raw_slot(self, index: int) -> int:
        """Return the absolute raw-slot position for a logical local."""

        return self._slot_index(index)

    def raw_span(self, index: int) -> tuple[int, int]:
        """Return one logical local's absolute slot and raw width together."""

        normalized = self._local_index(index)
        start = normalized * self._widths.slot_words
        width = self._widths.words(normalized)
        assert start + width <= self._slot_count
        assert width == 1 or width == 2
        return self._base + start, width

    def raw_width(self, index: int) -> int:
        """Return the raw slot count without interpreting the value."""

        normalized = self._local_index(index)
        width = self._widths.words(normalized)
        assert normalized * self._widths.slot_words + width <= self._slot_count
        assert width == 1 or width == 2
        return width

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


# Private aliases keep the interpreter's handler type signatures compact while
# the editable implementation remains in this separate module.
_ControlFrameWindow = ControlFrameWindow
_LocalStackWindow = LocalStackWindow


__all__ = (
    "ControlFrameKind",
    "ControlFrameWindow",
    "LocalStackWindow",
    "NativeControlStack",
    "_ControlFrameWindow",
    "_LocalStackWindow",
)

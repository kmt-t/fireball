"""Python access adapters for the interpreter's fixed Native stack ABI."""

from __future__ import annotations

import ctypes

from tier2_runtime.abi.interpreter_abi import (
    NATIVE_CALL_STACK_CAPACITY,
    CallFrameNative,
    CallStackNative,
    NativeValueStack,
)
from tier2_runtime.wasm.module import LocalLayout


class NativeCallFrameStack:
    """Python access shim over the context-owned Native call-frame stack."""

    __slots__ = ("_capacity", "_native", "_native_address", "_native_view")

    def __init__(self, capacity: int = NATIVE_CALL_STACK_CAPACITY):
        assert 0 <= capacity <= NATIVE_CALL_STACK_CAPACITY
        self._capacity = capacity
        self._native = CallStackNative()
        self._native_view = memoryview(self._native)
        self._native_address = ctypes.addressof(self._native)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def native(self) -> CallStackNative:
        return self._native

    @property
    def address(self) -> int:
        return self._native_address

    def frame_address(self, index: int) -> int:
        """Return a call-frame pointer, including the one-past-end pointer."""

        assert 0 <= index <= self._capacity
        return (
            self._native_address
            + CallStackNative.frames.offset
            + index * ctypes.sizeof(CallFrameNative)
        )

    def frame_index(self, address: int) -> int:
        """Convert an ABI frame pointer back to its Python storage index."""

        first = self.frame_address(0)
        delta = address - first
        assert delta >= 0 and delta % ctypes.sizeof(CallFrameNative) == 0
        index = delta // ctypes.sizeof(CallFrameNative)
        assert index <= self._capacity
        return index

    @property
    def raw_view(self) -> memoryview:
        """Return the zero-copy native call-stack record view."""

        return self._native_view

    def __len__(self) -> int:
        return int(self._native.size)

    def __bool__(self) -> bool:
        return self._native.size != 0

    def _index(self, index: int) -> int:
        size = int(self._native.size)
        normalized = index if index >= 0 else size + index
        if not 0 <= normalized < size:
            assert False, "native call-frame stack index out of range"
        return normalized

    def __getitem__(self, index: int) -> CallFrameNative:
        return self._native.frames[self._index(index)]

    def push_back(self, frame: CallFrameNative) -> bool:
        size = int(self._native.size)
        if size >= self._capacity:
            return False
        self._native.frames[size] = frame
        self._native.size = size + 1
        return True

    def pop_back(self) -> CallFrameNative:
        if not self:
            assert False, "native call-frame stack underflow"
        index = int(self._native.size) - 1
        frame = self._native.frames[index]
        self._native.size = index
        return frame

    def truncate(self, size: int) -> None:
        assert 0 <= size <= len(self)
        self._native.size = size

    def set_size(self, size: int) -> None:
        """Set the native call-stack pointer after an interpreter transition."""

        assert 0 <= size <= self._capacity
        self._native.size = size


class LocalStackWindow:
    """Typed access methods over one frame's packed Native local slots."""

    __slots__ = ("_base", "_layout", "_slot_count", "_storage")

    def __init__(self, storage: NativeValueStack, base: int, layout: LocalLayout):
        """`layout` provides load-time raw-word offsets and sizes for every local."""
        self._storage = storage
        self._base = base
        self._layout = layout
        self._slot_count = layout.total_words

    def __len__(self) -> int:
        return len(self._layout)

    def _local_index(self, index: int) -> int:
        local_count = len(self._layout)
        normalized = index if index >= 0 else local_count + index
        if not 0 <= normalized < local_count:
            assert False, "local stack index out of range"
        return normalized

    def _slot_index(self, index: int) -> int:
        normalized = self._local_index(index)
        return self._base + self._layout.offset(normalized)

    def raw_slot(self, index: int) -> int:
        """Return the absolute raw-slot position for a logical local."""

        return self._slot_index(index)

    def raw_span(self, index: int) -> tuple[int, int]:
        """Return one logical local's absolute slot and raw width together."""

        normalized = self._local_index(index)
        start = self._layout.offset(normalized)
        width = self._layout.size_words(normalized)
        assert start + width <= self._slot_count
        assert width == 1 or width == 2
        return self._base + start, width

    def raw_width(self, index: int) -> int:
        """Return the raw slot count without interpreting the value."""

        normalized = self._local_index(index)
        width = self._layout.size_words(normalized)
        assert self._layout.offset(normalized) + width <= self._slot_count
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
_LocalStackWindow = LocalStackWindow


__all__ = ("LocalStackWindow", "_LocalStackWindow")

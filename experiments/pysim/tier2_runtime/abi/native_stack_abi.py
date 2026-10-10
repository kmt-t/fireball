"""Python access adapters for the interpreter's fixed Native stack ABI."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from tier2_runtime.abi.interpreter_abi import (
    NATIVE_CALL_STACK_CAPACITY,
    NATIVE_CONTROL_STACK_CAPACITY,
    CallFrameNative,
    CallStackNative,
    ControlFrameNative,
    ControlStackNative,
    NativeValueStack,
)
from tier2_runtime.wasm.module import LocalLayout


class NativeControlStack:
    """Python access shim over the fixed Native control-frame record."""

    __slots__ = (
        "_capacity",
        "_native",
        "_native_address",
        "_native_bytes",
        "_native_view",
        "_stack_base_address",
    )

    def __init__(
        self,
        capacity: int = NATIVE_CONTROL_STACK_CAPACITY,
        *,
        stack_base_address: int = 0x1000_0000,
    ):
        assert 0 <= capacity <= NATIVE_CONTROL_STACK_CAPACITY
        self._capacity = capacity
        self._stack_base_address = stack_base_address
        self._native = ControlStackNative()
        self._native_address = ctypes.addressof(self._native)
        self._native_bytes = ctypes.sizeof(self._native)
        self._native_view = memoryview(self._native)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def native(self) -> ControlStackNative:
        return self._native

    @property
    def address(self) -> int:
        """Address of the stable control-frame record borrowed by the native ABI."""
        return self._native_address

    @property
    def native_bytes(self) -> int:
        """Byte size of the fixed control-frame record."""
        return self._native_bytes

    def bind_stack_base(self, address: int) -> None:
        """Bind the value-stack base used to encode control-frame heights."""

        assert address != 0 and address % 4 == 0
        self._stack_base_address = address

    def frame_address(self, index: int) -> int:
        """Return a control-frame pointer, including the one-past-end pointer."""

        assert 0 <= index <= self._capacity
        return (
            self._native_address
            + ControlStackNative.frames.offset
            + index * ctypes.sizeof(ControlFrameNative)
        )

    def frame_index(self, address: int) -> int:
        """Convert an ABI frame pointer back to its Python storage index."""

        first = self.frame_address(0)
        delta = address - first
        assert delta >= 0 and delta % ctypes.sizeof(ControlFrameNative) == 0
        index = delta // ctypes.sizeof(ControlFrameNative)
        assert index <= self._capacity
        return index

    def stack_height_index(self, address: int) -> int:
        """Decode a saved stack pointer into the Python stack's logical height."""

        delta = address - self._stack_base_address
        assert delta >= 0 and delta % 4 == 0
        return delta // 4

    @property
    def raw_view(self) -> memoryview:
        """Return the zero-copy native control-frame record view."""

        return self._native_view

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
        start: int,
        stack_height: int,
    ) -> bool:
        size = int(self._native.size)
        if size >= self._capacity:
            return False
        native_frame = self._native.frames[size]
        native_frame.start = start
        native_frame.stack_height = self._stack_base_address + stack_height * 4
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
        start: int,
        stack_height: int,
    ) -> bool:
        return self._storage.push_back(start, stack_height)

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
        resolve_control: Callable[[int], tuple[bool, int, int, int]],
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
        is_loop, match_end, result_arity, next_pc = resolve_control(int(target.start))
        saved_height = self._storage.stack_height_index(int(target.stack_height or 0))
        if is_loop:
            result_arity = 0
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
        if is_loop:
            self._storage.set_size(self._base + target_index + 1)
            return next_pc
        self._storage.set_size(self._base + target_index)
        return match_end + 1


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
_ControlFrameWindow = ControlFrameWindow
_LocalStackWindow = LocalStackWindow


__all__ = (
    "ControlFrameWindow",
    "LocalStackWindow",
    "NativeControlStack",
    "_ControlFrameWindow",
    "_LocalStackWindow",
)

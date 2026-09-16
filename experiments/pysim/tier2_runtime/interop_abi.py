"""ctypes mirror of the Tier 2 interpreter/JIT C ABI Native structures.

The structures in this module are layout mirrors only. They do not own the
pointed-to buffers. The owner must keep every buffer and descriptor alive for
the complete native call. The field order and offsets are shared with
``experiments/pysim/tier2_runtime/wasm_interop.hxx``.
"""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Iterator, Sequence

from jit_abi import JIT_CONTEXT_HELPER_PTR_OFFSET, JIT_CONTEXT_SIZE_BYTES, JIT_HELPER_COUNT
from wasm_module import WASM_VALUE_SLOT_BYTES

NATIVE_VALUE_STACK_CAPACITY = 128
NATIVE_CONTROL_STACK_CAPACITY = 32
NATIVE_STACK_ALIGNMENT_BYTES = WASM_VALUE_SLOT_BYTES


class ExecutionContextNative(ctypes.Structure):
    """Fixed physical execution context consumed by interpreter and JIT."""

    __slots__ = ()

    _fields_ = (
        ("ip", ctypes.c_uint32),
        ("sp_base", ctypes.c_uint32),
        ("sp_limit", ctypes.c_uint32),
        ("sp_offset", ctypes.c_uint32),
        ("local_base_addr", ctypes.c_uint32),
        ("local_limit_addr", ctypes.c_uint32),
        ("local_offset", ctypes.c_uint32),
        ("cf_base_addr", ctypes.c_uint32),
        ("cf_limit_addr", ctypes.c_uint32),
        ("cf_offset", ctypes.c_uint32),
        ("mem_base", ctypes.c_uint32),
        ("mem_size", ctypes.c_uint32),
        ("globals_base", ctypes.c_uint32),
        ("globals_limit", ctypes.c_uint32),
        ("handler_table", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("jit_helper_ptrs", ctypes.c_uint64 * JIT_HELPER_COUNT),
    )


class ConstBufferViewNative(ctypes.Structure):
    """Non-owning immutable byte buffer view: address, length, reserved."""

    __slots__ = ()

    _fields_ = (
        ("data", ctypes.c_void_p),
        ("size", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    )


class WasmFunctionViewNative(ctypes.Structure):
    """Non-owning function metadata and code view."""

    __slots__ = ()

    _fields_ = (
        ("code", ConstBufferViewNative),
        ("type_index", ctypes.c_uint32),
        ("locals_count", ctypes.c_uint32),
    )


class WasmModuleViewNative(ctypes.Structure):
    """Non-owning module descriptor pointing at a function-view array."""

    __slots__ = ()

    _fields_ = (
        ("function_table", ctypes.c_void_p),
        ("function_count", ctypes.c_uint32),
        ("imported_function_count", ctypes.c_uint32),
        ("start_function", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    )


class WasmRunRequestNative(ctypes.Structure):
    """Flat request passed from a Python adapter to a native run entry point."""

    __slots__ = ()

    _fields_ = (
        ("module_view", ctypes.c_void_p),
        ("execution_context", ctypes.c_void_p),
        ("arguments", ctypes.c_void_p),
        ("results", ctypes.c_void_p),
        ("function_index", ctypes.c_uint32),
        ("argument_count", ctypes.c_uint32),
        ("result_capacity", ctypes.c_uint32),
        ("max_blocks", ctypes.c_uint32),
    )


class WasmRunResultNative(ctypes.Structure):
    """Flat raw result buffer returned by a native interpreter/JIT entry point.

    The result record deliberately carries no value-type metadata. The caller
    owns the function signature and interprets the returned Native slots.
    """

    __slots__ = ()

    _fields_ = (
        ("status", ctypes.c_uint32),
        ("fault_code", ctypes.c_uint32),
        ("value_count", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("results", ctypes.c_void_p),
    )


class ValueStackNative(ctypes.Structure):
    """Fixed-capacity raw WASM value stack shared with the embedded runtime."""

    __slots__ = ()

    _fields_ = (
        ("values", ctypes.c_uint32 * NATIVE_VALUE_STACK_CAPACITY),
        ("size", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    )


class ControlFrameNative(ctypes.Structure):
    """Flat Native representation; result_arity counts shared raw stack slots."""

    __slots__ = ()

    _fields_ = (
        ("kind", ctypes.c_uint32),
        ("start", ctypes.c_uint32),
        ("match_end", ctypes.c_uint32),
        ("stack_height", ctypes.c_uint32),
        ("result_arity", ctypes.c_uint16),
    )


class ControlStackNative(ctypes.Structure):
    """Fixed-capacity Native control-frame stack."""

    __slots__ = ()

    _fields_ = (
        ("frames", ControlFrameNative * NATIVE_CONTROL_STACK_CAPACITY),
        ("size", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    )


class NativeValueStack:
    """Python access shim over a fixed raw Native value-stack record.

    The backing storage is always the Native record. ``capacity`` can lower
    the usable limit for a caller, but it can never allocate a second Python
    stack or exceed the ABI capacity. Type interpretation is deliberately
    performed by the WASM opcode handler, not stored beside each slot.
    """

    __slots__ = ("_capacity", "_native", "_values_address")

    def __init__(self, capacity: int = NATIVE_VALUE_STACK_CAPACITY):
        assert 0 <= capacity <= NATIVE_VALUE_STACK_CAPACITY
        self._capacity = capacity
        self._native = ValueStackNative()
        self._values_address = ctypes.addressof(self._native) + ValueStackNative.values.offset
        assert self._values_address % NATIVE_STACK_ALIGNMENT_BYTES == 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def native(self) -> ValueStackNative:
        return self._native

    def __len__(self) -> int:
        return int(self._native.size)

    def __bool__(self) -> bool:
        return self._native.size != 0

    @staticmethod
    def _encode(value: int) -> int:
        return value & 0xFFFF_FFFF

    def _decode(self, index: int) -> int:
        return int(self._native.values[index])

    def _index(self, index: int) -> int:
        size = int(self._native.size)
        normalized = index if index >= 0 else size + index
        assert 0 <= normalized < size
        return normalized

    def __getitem__(self, index: int) -> int:
        return self._decode(self._index(index))

    def __setitem__(self, index: int, value: int) -> None:
        normalized = self._index(index)
        self._native.values[normalized] = self._encode(value)

    def raw_at(self, index: int) -> int:
        """Read one native slot, including a slot reserved by native code."""

        assert 0 <= index < self._capacity
        return int(self._native.values[index])

    def raw_top(self) -> int:
        """Read the raw top slot for the interpreter's CPS continuation."""

        assert self
        return int(self._native.values[int(self._native.size) - 1])

    def __iter__(self) -> Iterator[int]:
        for index in range(len(self)):
            yield self._decode(index)

    def __eq__(self, other: Sequence[int]) -> bool:
        size = len(self)
        if size != len(other):
            return False
        for index in range(size):
            if self.raw_at(index) != other[index]:
                return False
        return True

    def push_back(self, value: int) -> bool:
        size = int(self._native.size)
        if size >= self._capacity:
            return False
        self._native.values[size] = self._encode(value)
        self._native.size = size + 1
        return True

    def extend(self, values: Sequence[int]) -> bool:
        value_count = len(values)
        size = int(self._native.size)
        if size + value_count > self._capacity:
            return False
        for index in range(value_count):
            self._native.values[size + index] = self._encode(values[index])
        self._native.size = size + value_count
        return True

    def push_zeroed(self, count: int) -> bool:
        """Append zero-initialized raw slots in one fixed-capacity operation."""

        assert count >= 0
        size = int(self._native.size)
        if size + count > self._capacity:
            return False
        for index in range(count):
            self._native.values[size + index] = 0
        self._native.size = size + count
        return True

    def write_raw_at(self, index: int, value: int) -> None:
        """Write one slot after the caller has reserved it in the stack."""

        assert 0 <= index < self._capacity
        self._native.values[index] = self._encode(value)

    def truncate(self, size: int) -> None:
        """Move the stack pointer back without touching released slots."""

        assert 0 <= size <= len(self)
        self._native.size = size

    def set_size(self, size: int) -> None:
        """Set the raw stack pointer after native code wrote existing storage."""

        assert 0 <= size <= self._capacity
        self._native.size = size

    def _validate_range(self, start: int, count: int) -> None:
        assert count >= 0
        assert 0 <= start and start + count <= int(self._native.size)

    def push_raw_from(self, source: NativeValueStack, start: int, count: int) -> bool:
        """Copy raw slots from another Native stack onto this stack."""

        source._validate_range(start, count)
        destination_start = int(self._native.size)
        if destination_start + count > self._capacity:
            return False
        if count == 1:
            self._native.values[destination_start] = source._native.values[start]
            self._native.size = destination_start + 1
            return True
        for index in range(count):
            self._native.values[destination_start + index] = source._native.values[start + index]
        self._native.size = destination_start + count
        return True

    def copy_raw_to(self, destination: NativeValueStack, start: int, count: int) -> None:
        """Copy the top raw slots to an existing range in another stack."""

        source_start = int(self._native.size) - count
        self._validate_range(source_start, count)
        destination._validate_range(start, count)
        if count == 1:
            destination._native.values[start] = self._native.values[source_start]
            return
        for index in range(count):
            destination._native.values[start + index] = self._native.values[source_start + index]

    def pop_raw_to(self, destination: NativeValueStack, start: int, count: int) -> None:
        """Copy and remove the top raw slots into another Native stack."""

        self.copy_raw_to(destination, start, count)
        self._native.size -= count

    def pop_back(self) -> int:
        assert self
        index = int(self._native.size) - 1
        value = self._decode(index)
        self._native.size = index
        self._native.values[index] = 0
        return value

    def push_i32(self, value: int) -> bool:
        return self.push_back(value & 0xFFFF_FFFF)

    def push_i64(self, value: int) -> bool:
        raw = value & 0xFFFF_FFFF_FFFF_FFFF
        if int(self._native.size) + 2 > self._capacity:
            return False
        return self.push_back(raw & 0xFFFF_FFFF) and self.push_back(raw >> 32)

    def push_f32(self, value: float) -> bool:
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
        return self.push_back(bits)

    def push_f64(self, value: float) -> bool:
        bits = struct.unpack("<Q", struct.pack("<d", value))[0]
        if int(self._native.size) + 2 > self._capacity:
            return False
        return self.push_back(bits & 0xFFFF_FFFF) and self.push_back(bits >> 32)

    def pop_i32(self) -> int:
        value = self.pop_back()
        value &= 0xFFFF_FFFF
        return value - (1 << 32) if value & 0x8000_0000 else value

    def pop_i64(self) -> int:
        assert len(self) >= 2
        high = self.pop_back()
        low = self.pop_back()
        raw = (high << 32) | low
        return raw - (1 << 64) if raw & (1 << 63) else raw

    def pop_f32(self) -> float:
        value = self.pop_back()
        return struct.unpack("<f", struct.pack("<I", value & 0xFFFF_FFFF))[0]

    def pop_f64(self) -> float:
        assert len(self) >= 2
        high = self.pop_back()
        low = self.pop_back()
        return struct.unpack("<d", struct.pack("<Q", (high << 32) | low))[0]

    def peek_i32(self) -> int:
        assert len(self) >= 1
        return self.read_i32(len(self) - 1)

    def peek_i64(self) -> int:
        assert len(self) >= 2
        return self.read_i64(len(self) - 2)

    def peek_f32(self) -> float:
        assert len(self) >= 1
        return self.read_f32(len(self) - 1)

    def peek_f64(self) -> float:
        assert len(self) >= 2
        return self.read_f64(len(self) - 2)

    def read_i32(self, index: int) -> int:
        value = self._decode(self._index(index))
        return value - (1 << 32) if value & 0x8000_0000 else value

    def read_i64(self, index: int) -> int:
        self._index(index)
        assert index + 1 < len(self)
        raw = self._decode(index) | (self._decode(index + 1) << 32)
        return raw - (1 << 64) if raw & (1 << 63) else raw

    def read_f32(self, index: int) -> float:
        return struct.unpack("<f", struct.pack("<I", self._decode(self._index(index))))[0]

    def read_f64(self, index: int) -> float:
        self._index(index)
        assert index + 1 < len(self)
        raw = self._decode(index) | (self._decode(index + 1) << 32)
        return struct.unpack("<d", struct.pack("<Q", raw))[0]

    def write_i32(self, index: int, value: int) -> None:
        self._index(index)
        self._native.values[index] = value & 0xFFFF_FFFF

    def write_i64(self, index: int, value: int) -> None:
        self._index(index)
        assert index + 1 < len(self)
        raw = value & 0xFFFF_FFFF_FFFF_FFFF
        self._native.values[index] = raw & 0xFFFF_FFFF
        self._native.values[index + 1] = raw >> 32

    def write_f32(self, index: int, value: float) -> None:
        self.write_i32(index, struct.unpack("<I", struct.pack("<f", value))[0])

    def write_f64(self, index: int, value: float) -> None:
        self._index(index)
        assert index + 1 < len(self)
        raw = struct.unpack("<Q", struct.pack("<d", value))[0]
        self._native.values[index] = raw & 0xFFFF_FFFF
        self._native.values[index + 1] = raw >> 32

    def clear(self) -> None:
        while self:
            assert self.pop_back() is not None

    def __delitem__(self, index: int) -> None:
        start = self._index(index)
        stop = start + 1
        if start >= stop:
            return
        removed = stop - start
        for current in range(start, len(self) - removed):
            self._native.values[current] = self._native.values[current + removed]
        for current in range(len(self) - removed, len(self)):
            self._native.values[current] = 0
        self._native.size -= removed

    def value_ptr(self, start: int = 0) -> ctypes.c_void_p:
        """Return a pointer to the Native raw value words."""

        assert 0 <= start <= self._capacity
        return ctypes.c_void_p(self._values_address + start * 4)


assert ctypes.sizeof(ctypes.c_void_p) == 8
assert ctypes.sizeof(ExecutionContextNative) == JIT_CONTEXT_SIZE_BYTES
assert ExecutionContextNative.mem_base.offset == 0x28
assert ExecutionContextNative.handler_table.offset == 0x38
assert ExecutionContextNative.reserved0.offset == 0x3C
assert ExecutionContextNative.jit_helper_ptrs.offset == JIT_CONTEXT_HELPER_PTR_OFFSET
assert ctypes.sizeof(ConstBufferViewNative) == 16
assert ctypes.sizeof(WasmFunctionViewNative) == 24
assert ctypes.sizeof(WasmModuleViewNative) == 24
assert ctypes.sizeof(WasmRunRequestNative) == 48
assert ctypes.sizeof(WasmRunResultNative) == 24
assert ctypes.sizeof(ValueStackNative) == 520
assert ValueStackNative.size.offset == 512
assert ctypes.sizeof(ControlFrameNative) == 20
assert ctypes.sizeof(ControlStackNative) == 648
assert ControlStackNative.size.offset == 640


__all__ = (
    "NATIVE_CONTROL_STACK_CAPACITY",
    "NATIVE_VALUE_STACK_CAPACITY",
    "ConstBufferViewNative",
    "ControlFrameNative",
    "ControlStackNative",
    "ExecutionContextNative",
    "NativeValueStack",
    "ValueStackNative",
    "WasmFunctionViewNative",
    "WasmModuleViewNative",
    "WasmRunRequestNative",
    "WasmRunResultNative",
)

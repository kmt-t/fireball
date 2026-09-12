"""ctypes mirror of the Tier 2 interpreter/JIT C ABI Native structures.

The structures in this module are layout mirrors only. They do not own the
pointed-to buffers. The owner must keep every buffer and descriptor alive for
the complete native call. The field order and offsets are shared with
``experiments/pysim/tier2_runtime/wasm_interop.hxx``.
"""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Iterable, Iterator

from jit_abi import JIT_CONTEXT_HELPER_PTR_OFFSET, JIT_CONTEXT_SIZE_BYTES


NATIVE_VALUE_STACK_CAPACITY = 64
NATIVE_CONTROL_STACK_CAPACITY = 32


class ExecutionContextNative(ctypes.Structure):
    """Fixed physical execution context consumed by interpreter and JIT."""

    _fields_ = [
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
        ("complex_helper_ptr", ctypes.c_uint64),
    ]


class ConstBufferViewNative(ctypes.Structure):
    """Non-owning immutable byte buffer view: address, length, reserved."""

    _fields_ = [
        ("data", ctypes.c_void_p),
        ("size", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class WasmFunctionViewNative(ctypes.Structure):
    """Non-owning function metadata and code view."""

    _fields_ = [
        ("code", ConstBufferViewNative),
        ("type_index", ctypes.c_uint32),
        ("locals_count", ctypes.c_uint32),
    ]


class WasmModuleViewNative(ctypes.Structure):
    """Non-owning module descriptor pointing at a function-view array."""

    _fields_ = [
        ("function_table", ctypes.c_void_p),
        ("function_count", ctypes.c_uint32),
        ("imported_function_count", ctypes.c_uint32),
        ("start_function", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class WasmRunRequestNative(ctypes.Structure):
    """Flat request passed from a Python adapter to a native run entry point."""

    _fields_ = [
        ("module_view", ctypes.c_void_p),
        ("execution_context", ctypes.c_void_p),
        ("arguments", ctypes.c_void_p),
        ("results", ctypes.c_void_p),
        ("function_index", ctypes.c_uint32),
        ("argument_count", ctypes.c_uint32),
        ("result_capacity", ctypes.c_uint32),
        ("max_blocks", ctypes.c_uint32),
    ]


class WasmRunResultNative(ctypes.Structure):
    """Flat raw result buffer returned by a native interpreter/JIT entry point.

    The result record deliberately carries no value-type metadata. The caller
    owns the function signature and interprets the returned Native slots.
    """

    _fields_ = [
        ("status", ctypes.c_uint32),
        ("fault_code", ctypes.c_uint32),
        ("value_count", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("results", ctypes.c_void_p),
    ]


class ValueStackNative(ctypes.Structure):
    """Fixed-capacity raw WASM value stack shared with the embedded runtime."""

    _fields_ = [
        ("values", ctypes.c_uint32 * NATIVE_VALUE_STACK_CAPACITY),
        ("size", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class ControlFrameNative(ctypes.Structure):
    """Flat Native representation of one WASM control frame."""

    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("start", ctypes.c_uint32),
        ("match_end", ctypes.c_uint32),
        ("stack_height", ctypes.c_uint32),
    ]


class ControlStackNative(ctypes.Structure):
    """Fixed-capacity Native control-frame stack."""

    _fields_ = [
        ("frames", ControlFrameNative * NATIVE_CONTROL_STACK_CAPACITY),
        ("size", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class NativeValueStack:
    """Python access shim over a fixed raw Native value-stack record.

    The backing storage is always the Native record. ``capacity`` can lower
    the usable limit for a caller, but it can never allocate a second Python
    stack or exceed the ABI capacity. Type interpretation is deliberately
    performed by the WASM opcode handler, not stored beside each slot.
    """

    __slots__ = ("_capacity", "_native")

    def __init__(self, capacity: int = NATIVE_VALUE_STACK_CAPACITY):
        assert 0 <= capacity <= NATIVE_VALUE_STACK_CAPACITY
        self._capacity = capacity
        self._native = ValueStackNative()

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
        assert isinstance(value, int)
        return value & 0xFFFF_FFFF

    def _decode(self, index: int) -> int:
        return int(self._native.values[index])

    def _index(self, index: int) -> int:
        normalized = index if index >= 0 else len(self) + index
        if not 0 <= normalized < len(self):
            raise IndexError("native value stack index out of range")
        return normalized

    def __getitem__(self, index: int | slice) -> int | tuple[int, ...]:
        if isinstance(index, slice):
            return tuple(self._decode(i) for i in range(*index.indices(len(self))))
        return self._decode(self._index(index))

    def __setitem__(self, index: int, value: int) -> None:
        normalized = self._index(index)
        self._native.values[normalized] = self._encode(value)

    def __iter__(self) -> Iterator[int]:
        for index in range(len(self)):
            yield self._decode(index)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NativeValueStack):
            return tuple(self) == tuple(other)
        if isinstance(other, (list, tuple)):
            return tuple(self) == tuple(other)
        return False

    def push_back(self, value: int) -> bool:
        size = len(self)
        if size >= self._capacity:
            return False
        self._native.values[size] = self._encode(value)
        self._native.size = size + 1
        return True

    def extend(self, values: Iterable[int]) -> bool:
        pending = tuple(values)
        if len(self) + len(pending) > self._capacity:
            return False
        for value in pending:
            assert self.push_back(value)
        return True

    def pop_back(self) -> int | None:
        if not self:
            return None
        index = len(self) - 1
        value = self._decode(index)
        self._native.size = index
        self._native.values[index] = 0
        return value

    def push_i32(self, value: int) -> bool:
        return self.push_back(value & 0xFFFF_FFFF)

    def push_i64(self, value: int) -> bool:
        raw = value & 0xFFFF_FFFF_FFFF_FFFF
        if len(self) + 2 > self._capacity:
            return False
        return self.push_back(raw & 0xFFFF_FFFF) and self.push_back(raw >> 32)

    def push_f32(self, value: float) -> bool:
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
        return self.push_back(bits)

    def push_f64(self, value: float) -> bool:
        bits = struct.unpack("<Q", struct.pack("<d", value))[0]
        if len(self) + 2 > self._capacity:
            return False
        return self.push_back(bits & 0xFFFF_FFFF) and self.push_back(bits >> 32)

    def pop_i32(self) -> int | None:
        value = self.pop_back()
        if value is None:
            return None
        value &= 0xFFFF_FFFF
        return value - (1 << 32) if value & 0x8000_0000 else value

    def pop_i64(self) -> int | None:
        if len(self) < 2:
            return None
        high = self.pop_back()
        low = self.pop_back()
        if high is None or low is None:
            return None
        raw = (high << 32) | low
        return raw - (1 << 64) if raw & (1 << 63) else raw

    def pop_f32(self) -> float | None:
        value = self.pop_back()
        if value is None:
            return None
        return struct.unpack("<f", struct.pack("<I", value & 0xFFFF_FFFF))[0]

    def pop_f64(self) -> float | None:
        if len(self) < 2:
            return None
        high = self.pop_back()
        low = self.pop_back()
        if high is None or low is None:
            return None
        return struct.unpack("<d", struct.pack("<Q", (high << 32) | low))[0]

    def peek_i32(self) -> int:
        if len(self) < 1:
            raise IndexError("native value stack underflow")
        return self.read_i32(len(self) - 1)

    def peek_i64(self) -> int:
        if len(self) < 2:
            raise IndexError("native value stack underflow")
        return self.read_i64(len(self) - 2)

    def peek_f32(self) -> float:
        if len(self) < 1:
            raise IndexError("native value stack underflow")
        return self.read_f32(len(self) - 1)

    def peek_f64(self) -> float:
        if len(self) < 2:
            raise IndexError("native value stack underflow")
        return self.read_f64(len(self) - 2)

    def read_i32(self, index: int) -> int:
        value = self._decode(self._index(index))
        return value - (1 << 32) if value & 0x8000_0000 else value

    def read_i64(self, index: int) -> int:
        self._index(index)
        if index + 1 >= len(self):
            raise IndexError("native i64 value exceeds stack bounds")
        raw = self._decode(index) | (self._decode(index + 1) << 32)
        return raw - (1 << 64) if raw & (1 << 63) else raw

    def read_f32(self, index: int) -> float:
        return struct.unpack("<f", struct.pack("<I", self._decode(self._index(index))))[0]

    def read_f64(self, index: int) -> float:
        self._index(index)
        if index + 1 >= len(self):
            raise IndexError("native f64 value exceeds stack bounds")
        raw = self._decode(index) | (self._decode(index + 1) << 32)
        return struct.unpack("<d", struct.pack("<Q", raw))[0]

    def write_i32(self, index: int, value: int) -> None:
        self._index(index)
        self._native.values[index] = value & 0xFFFF_FFFF

    def write_i64(self, index: int, value: int) -> None:
        self._index(index)
        if index + 1 >= len(self):
            raise IndexError("native i64 value exceeds stack bounds")
        raw = value & 0xFFFF_FFFF_FFFF_FFFF
        self._native.values[index] = raw & 0xFFFF_FFFF
        self._native.values[index + 1] = raw >> 32

    def write_f32(self, index: int, value: float) -> None:
        self.write_i32(index, struct.unpack("<I", struct.pack("<f", value))[0])

    def write_f64(self, index: int, value: float) -> None:
        self._index(index)
        if index + 1 >= len(self):
            raise IndexError("native f64 value exceeds stack bounds")
        raw = struct.unpack("<Q", struct.pack("<d", value))[0]
        self._native.values[index] = raw & 0xFFFF_FFFF
        self._native.values[index + 1] = raw >> 32

    def clear(self) -> None:
        while self:
            assert self.pop_back() is not None

    def __delitem__(self, index: int | slice) -> None:
        if isinstance(index, int):
            start = self._index(index)
            stop = start + 1
        else:
            start, stop, step = index.indices(len(self))
            if step != 1:
                raise ValueError("native value stack deletion requires a unit step")
        if start >= stop:
            return
        removed = stop - start
        for current in range(start, len(self) - removed):
            self._native.values[current] = self._native.values[current + removed]
        for current in range(len(self) - removed, len(self)):
            self._native.values[current] = 0
        self._native.size -= removed

    def value_ptr(self, start: int = 0) -> ctypes.c_void_p:
        """Return a pointer to the Native raw value words for JIT/native code."""

        assert 0 <= start <= len(self)
        address = ctypes.addressof(self._native) + ValueStackNative.values.offset + start * 4
        return ctypes.c_void_p(address)


def _offset(struct_type: type[ctypes.Structure], field_name: str) -> int:
    return int(getattr(struct_type, field_name).offset)


assert ctypes.sizeof(ctypes.c_void_p) == 8
assert ctypes.sizeof(ExecutionContextNative) == JIT_CONTEXT_SIZE_BYTES
assert _offset(ExecutionContextNative, "mem_base") == 0x28
assert _offset(ExecutionContextNative, "handler_table") == 0x38
assert _offset(ExecutionContextNative, "reserved0") == 0x3C
assert _offset(ExecutionContextNative, "complex_helper_ptr") == JIT_CONTEXT_HELPER_PTR_OFFSET
assert ctypes.sizeof(ConstBufferViewNative) == 16
assert ctypes.sizeof(WasmFunctionViewNative) == 24
assert ctypes.sizeof(WasmModuleViewNative) == 24
assert ctypes.sizeof(WasmRunRequestNative) == 48
assert ctypes.sizeof(WasmRunResultNative) == 24
assert ctypes.sizeof(ValueStackNative) == 264
assert ValueStackNative.size.offset == 256
assert ctypes.sizeof(ControlFrameNative) == 16
assert ctypes.sizeof(ControlStackNative) == 520
assert ControlStackNative.size.offset == 512


__all__ = [
    "ConstBufferViewNative",
    "ExecutionContextNative",
    "ControlFrameNative",
    "ControlStackNative",
    "NativeValueStack",
    "NATIVE_CONTROL_STACK_CAPACITY",
    "NATIVE_VALUE_STACK_CAPACITY",
    "ValueStackNative",
    "WasmFunctionViewNative",
    "WasmModuleViewNative",
    "WasmRunRequestNative",
    "WasmRunResultNative",
]

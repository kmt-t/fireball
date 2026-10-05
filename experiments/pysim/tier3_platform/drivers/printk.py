"""Tier 3 printk driver over a replaceable low-level byte sink."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from tier1_core.native_buffer import BufferLease
from tier1_core.native_printk import BASE64, EVENT, PRINTK_WRITE, NativePrintkWriter
from tier1_core.printk import (
    PRINTK_MAX_RECORD_SIZE,
    PrintkEvent,
    PrintkLevel,
    PrintkWriter,
    printk_argument_count,
)

PRINTK_BUFFER_CAPACITY = 4096


class PrintkBuffer:
    """Fixed-capacity pysim capture sink, isolated from guest stdout."""

    __slots__ = ("_output", "_output_len", "bytes_written")

    def __init__(self, capacity: int = PRINTK_BUFFER_CAPACITY) -> None:
        assert capacity > 0
        self._output = bytearray(capacity)
        self._output_len = 0
        self.bytes_written = 0

    def write(self, data: memoryview) -> int:
        written = len(data)
        if written > len(self._output) - self._output_len:
            return 0
        self._output[self._output_len : self._output_len + written] = data
        self._output_len += written
        self.bytes_written += written
        return written

    def drain_output(self) -> bytes:
        data = bytes(self._output[: self._output_len])
        self._output_len = 0
        return data

    def close(self) -> None:
        self._output_len = 0


class PrintkSink:
    """Write buffered runtime records and synchronous kernel events to one sink."""

    __slots__ = (
        "_arguments",
        "_callback_error",
        "_decode_record",
        "_event_buffer",
        "_raw_writer",
        "_record_writer",
        "_sink",
    )

    def __init__(self, sink: PrintkWriter, decode_record: Callable[[memoryview], str]) -> None:
        self._sink = sink
        self._decode_record = decode_record
        self._event_buffer = bytearray(PRINTK_MAX_RECORD_SIZE)
        self._arguments = (ctypes.c_uint32 * 4)()
        self._callback_error: AssertionError | None = None
        self._raw_writer = NativePrintkWriter(0, PRINTK_WRITE(self._write_native_raw))
        self._record_writer = NativePrintkWriter(0, PRINTK_WRITE(self._write_native_record))

    def write(self, data: memoryview) -> int:
        """Decode one record at output time; report consumed input bytes to Logger."""
        output = (self._decode_record(data) + "\n").encode("utf-8")
        written = self._sink.write(memoryview(output))
        assert written == 0 or written == len(output), "partial printk text write"
        return len(data) if written == len(output) else 0

    def write_raw(self, data: memoryview) -> int:
        """Preserve guest stderr bytes on the same physical endpoint."""
        return self._sink.write(data)

    def _write_native_raw(self, owner: int, data: int, size: int) -> int:
        try:
            return self._sink.write(
                memoryview((ctypes.c_uint8 * size).from_address(data)).cast("B")
            )
        except AssertionError as error:
            self._callback_error = error
            return 0

    def _write_native_record(self, owner: int, data: int, size: int) -> int:
        try:
            return self.write(memoryview((ctypes.c_uint8 * size).from_address(data)).cast("B"))
        except AssertionError as error:
            self._callback_error = error
            return 0

    def write_base64(self, data: memoryview) -> int:
        """Borrow a byte view; C++ encodes it in the existing fixed workspace."""
        assert data.ndim == 1 and data.itemsize == 1 and data.format == "B"
        self._callback_error = None
        lease = BufferLease(data, flags=0x18)  # PyBUF_STRIDES permits sliced byte views.
        try:
            workspace = (ctypes.c_uint8 * PRINTK_MAX_RECORD_SIZE).from_buffer(self._event_buffer)
            status = BASE64(
                ctypes.byref(self._raw_writer), workspace, lease.address, lease.size, lease.stride
            )
        finally:
            lease.release()
        assert self._callback_error is None, str(self._callback_error)
        assert status == 1, "incomplete printk base64 write"
        return len(data)

    def write_event(
        self,
        level: PrintkLevel,
        event: PrintkEvent,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> None:
        """C++ emits the shared event record; the platform decodes at output time."""
        argument_count = printk_argument_count(event)
        assert 0 <= arg0 <= 0xFFFF_FFFF
        assert 0 <= arg1 <= 0xFFFF_FFFF
        assert 0 <= arg2 <= 0xFFFF_FFFF
        assert 0 <= arg3 <= 0xFFFF_FFFF
        self._arguments[0] = arg0
        self._arguments[1] = arg1
        self._arguments[2] = arg2
        self._arguments[3] = arg3
        self._callback_error = None
        workspace = (ctypes.c_uint8 * PRINTK_MAX_RECORD_SIZE).from_buffer(self._event_buffer)
        EVENT(
            ctypes.byref(self._record_writer),
            workspace,
            int(level),
            int(event),
            argument_count,
            self._arguments,
        )
        assert self._callback_error is None, str(self._callback_error)

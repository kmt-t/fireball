"""Tier 3 printk driver over a replaceable low-level byte sink."""

from __future__ import annotations

from tier1_core.printk import (
    PRINTK_RECORD_SIZE,
    PrintkEvent,
    PrintkLevel,
    PrintkWriter,
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

    __slots__ = ("_event_buffer", "_event_view", "_sink")

    def __init__(self, sink: PrintkWriter) -> None:
        self._sink = sink
        self._event_buffer = bytearray(PRINTK_RECORD_SIZE)
        self._event_view = memoryview(self._event_buffer)

    def write(self, data: memoryview) -> int:
        return self._sink.write(data)

    def write_event(
        self,
        level: PrintkLevel,
        event: PrintkEvent,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> None:
        """Emit one compact diagnostic record synchronously to the printk sink."""
        wire = self._event_buffer
        wire[0] = int(level)
        event_id = int(event)
        assert 0 <= event_id <= 0x00FF_FFFF
        wire[1] = event_id & 0xFF
        wire[2] = (event_id >> 8) & 0xFF
        wire[3] = (event_id >> 16) & 0xFF
        self._write_u32(4, arg0)
        self._write_u32(8, arg1)
        self._write_u32(12, arg2)
        self._write_u32(16, arg3)
        self._sink.write(self._event_view)

    def _write_u32(self, offset: int, value: int) -> None:
        assert 0 <= value <= 0xFFFF_FFFF
        wire = self._event_buffer
        wire[offset] = value & 0xFF
        wire[offset + 1] = (value >> 8) & 0xFF
        wire[offset + 2] = (value >> 16) & 0xFF
        wire[offset + 3] = (value >> 24) & 0xFF

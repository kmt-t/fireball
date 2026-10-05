"""Tier 3 printk driver over a replaceable low-level byte sink."""

from __future__ import annotations

from collections.abc import Callable

from tier1_core.printk import (
    PRINTK_HEADER_SIZE,
    PRINTK_MAX_RECORD_SIZE,
    PrintkEvent,
    PrintkLevel,
    PrintkWriter,
    printk_argument_count,
)

PRINTK_BUFFER_CAPACITY = 4096
_BASE64_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


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

    __slots__ = ("_decode_record", "_event_buffer", "_event_view", "_sink")

    def __init__(self, sink: PrintkWriter, decode_record: Callable[[memoryview], str]) -> None:
        self._sink = sink
        self._decode_record = decode_record
        self._event_buffer = bytearray(PRINTK_MAX_RECORD_SIZE)
        self._event_view = memoryview(self._event_buffer)

    def write(self, data: memoryview) -> int:
        """Decode one record at output time; report consumed input bytes to Logger."""
        output = (self._decode_record(data) + "\n").encode("utf-8")
        written = self._sink.write(memoryview(output))
        assert written == 0 or written == len(output), "partial printk text write"
        return len(data) if written == len(output) else 0

    def write_raw(self, data: memoryview) -> int:
        """Preserve guest stderr bytes on the same physical endpoint."""
        return self._sink.write(data)

    def write_base64(self, data: memoryview) -> int:
        """Encode one binary payload plus LF using the existing fixed record workspace.

        Only the final group may contain padding. A failed write can leave a prefix
        on the physical endpoint, so assert immediately rather than retry the frame.
        """
        assert data.ndim == 1 and data.itemsize == 1 and data.format == "B"
        wire = self._event_buffer
        input_offset = 0
        output_size = 0
        while input_offset < len(data):
            remaining = len(data) - input_offset
            byte0 = data[input_offset]
            byte1 = data[input_offset + 1] if remaining > 1 else 0
            byte2 = data[input_offset + 2] if remaining > 2 else 0
            wire[output_size] = _BASE64_ALPHABET[byte0 >> 2]
            wire[output_size + 1] = _BASE64_ALPHABET[((byte0 & 3) << 4) | (byte1 >> 4)]
            wire[output_size + 2] = (
                _BASE64_ALPHABET[((byte1 & 15) << 2) | (byte2 >> 6)] if remaining > 1 else ord("=")
            )
            wire[output_size + 3] = _BASE64_ALPHABET[byte2 & 63] if remaining > 2 else ord("=")
            input_offset += min(remaining, 3)
            output_size += 4
            if output_size == PRINTK_MAX_RECORD_SIZE or input_offset == len(data):
                written = self._sink.write(self._event_view[:output_size])
                assert written == output_size, "incomplete printk base64 write"
                output_size = 0
        wire[0] = ord("\n")
        written = self._sink.write(self._event_view[:1])
        assert written == 1, "incomplete printk base64 newline"
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
        """Decode and emit one diagnostic synchronously without the Logger ring."""
        argument_count = printk_argument_count(event)
        assert 0 <= arg0 <= 0xFFFF_FFFF
        assert 0 <= arg1 <= 0xFFFF_FFFF
        assert 0 <= arg2 <= 0xFFFF_FFFF
        assert 0 <= arg3 <= 0xFFFF_FFFF
        wire = self._event_buffer
        wire[0] = int(level)
        event_id = int(event)
        assert 0 <= event_id <= 0x00FF_FFFF
        wire[1] = event_id & 0xFF
        wire[2] = (event_id >> 8) & 0xFF
        wire[3] = (event_id >> 16) & 0xFF
        if argument_count > 0:
            self._write_u32(4, arg0)
        if argument_count > 1:
            self._write_u32(8, arg1)
        if argument_count > 2:
            self._write_u32(12, arg2)
        if argument_count > 3:
            self._write_u32(16, arg3)
        record_size = PRINTK_HEADER_SIZE + 4 * argument_count
        self.write(self._event_view[:record_size])

    def _write_u32(self, offset: int, value: int) -> None:
        assert 0 <= value <= 0xFFFF_FFFF
        wire = self._event_buffer
        wire[offset] = value & 0xFF
        wire[offset + 1] = (value >> 8) & 0xFF
        wire[offset + 2] = (value >> 16) & 0xFF
        wire[offset + 3] = (value >> 24) & 0xFF

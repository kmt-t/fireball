"""Bounded byte-level framework for the Tier 2 WASM reference parser.

This module owns only byte-level traversal: stream bounds, the WASM header,
and section boundaries/order. ``wasm_reader`` interprets the returned payload
ranges and builds the runtime module model separately.
"""

from __future__ import annotations

from dataclasses import dataclass

MAGIC = b"\x00asm"
VERSION = b"\x01\x00\x00\x00"
CUSTOM_SECTION_ID = 0
LAST_MVP_SECTION_ID = 11


class BinaryStream:
    """Bounded reader over a borrowed ROM range."""

    __slots__ = ("cursor", "limit", "view")

    def __init__(
        self,
        data: memoryview,
        offset: int = 0,
        length: int | None = None,
    ) -> None:
        self.view = memoryview(data)
        assert offset >= 0, "stream offset must be non-negative"
        assert length is None or length >= 0, "stream length must be non-negative"
        self.cursor = offset
        self.limit = len(self.view) if length is None else offset + length
        assert self.limit <= len(self.view), (
            f"Stream limit {self.limit} exceeds underlying buffer size {len(self.view)}"
        )

    def remaining(self) -> int:
        return max(0, self.limit - self.cursor)

    def tell(self) -> int:
        return self.cursor

    def seek(self, pos: int) -> None:
        assert 0 <= pos <= self.limit, f"Seek position {pos} out of range [0, {self.limit}]"
        self.cursor = pos

    def read_bytes(self, n: int) -> memoryview:
        assert n >= 0, "read length must be non-negative"
        if self.cursor + n > self.limit:
            assert False, (
                f"Unexpected end of stream: requested {n} bytes, {self.remaining()} remaining"
            )
        result = self.view[self.cursor : self.cursor + n]
        self.cursor += n
        return result

    def read_u8(self) -> int:
        return self.read_bytes(1)[0]

    def read_leb128_u32(self) -> int:
        result = 0
        shift = 0
        count = 0
        while True:
            assert count < 5, "LEB128 u32 exceeded maximum 5 bytes"
            assert self.cursor < self.limit, "Truncated LEB128 u32 integer"
            byte = self.read_u8()
            count += 1
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7

        assert result <= 0xFFFFFFFF, "LEB128 u32 value out of 32-bit range"
        return result


@dataclass(slots=True)
class SectionFrame:
    """Reusable bounded view of one section; the reader mutates it on advance."""

    section_id: int
    payload_offset: int
    payload_size: int


class WasmSectionReader:
    """Validate and expose section frames without interpreting their payloads."""

    __slots__ = ("_frame", "_last_section_id", "_stream")

    def __init__(self, data: memoryview) -> None:
        self._stream = BinaryStream(data)
        assert self._stream.remaining() >= 8, "truncated WASM header"
        magic = bytes(self._stream.read_bytes(4))
        assert magic == MAGIC, "missing \\0asm magic header"
        version = bytes(self._stream.read_bytes(4))
        assert version == VERSION, f"unsupported wasm version {version!r}"
        self._frame = SectionFrame(0, 0, 0)
        self._last_section_id = 0

    def next_section(self) -> SectionFrame | None:
        """Advance to the next frame; the returned view is reused on the next call."""
        if self._stream.remaining() == 0:
            return None

        section_id = self._stream.read_u8()
        assert 0 <= section_id <= LAST_MVP_SECTION_ID, f"unsupported MVP section id={section_id}"
        payload_size = self._stream.read_leb128_u32()
        payload_offset = self._stream.tell()
        payload_end = payload_offset + payload_size
        assert payload_end <= self._stream.limit, "section length exceeds module bounds"

        if section_id != CUSTOM_SECTION_ID:
            assert section_id > self._last_section_id, (
                f"WASM sections are duplicated or out of order: {section_id} "
                f"after {self._last_section_id}"
            )
            self._last_section_id = section_id

        self._stream.seek(payload_end)
        self._frame.section_id = section_id
        self._frame.payload_offset = payload_offset
        self._frame.payload_size = payload_size
        return self._frame

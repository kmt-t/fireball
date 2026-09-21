"""File-backed stream sink used by profiling and host-side logging tests."""

from __future__ import annotations

from typing import BinaryIO


class FileLogSink:
    """Appends logger bytes to a caller-owned host file."""

    __slots__ = ("_file", "bytes_written")

    def __init__(self, file: BinaryIO) -> None:
        self._file = file
        self.bytes_written = 0

    def write(self, data: memoryview) -> int:
        written = self._file.write(data)
        assert written == len(data)
        self.bytes_written += written
        return written

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()



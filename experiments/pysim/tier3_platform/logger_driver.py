"""ロガー出力先を stdout から分離して受け持つ pysim 用 HAL ドライバ。"""

from __future__ import annotations

from typing import BinaryIO

from hal_dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET, HalDriver, WasiIpcCmd
from system_containers import ReadOnlyFlatMapView


class FileLogSink:
    """バイト列を呼び出し側が開いたホストファイルへ追記する StreamSink 実体。"""

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


class LoggerDriver(HalDriver):
    """ログ出力専用のストリームを提供し、ゲストの標準出力とバッファを共有しない。"""

    def __init__(self, uri: str, sink: FileLogSink) -> None:
        super().__init__(uri)
        self.sink = sink
        self.register_command(WasiIpcCmd.STREAM_WRITE_BUFFER, self._write_buffer)
        self.register_command(WasiIpcCmd.STREAM_FLUSH, self._flush)
        self.register_command(WasiIpcCmd.STREAM_CLOSE, self._close)

    def _write_buffer(self, params: ReadOnlyFlatMapView) -> int:
        assert self._buffer_pool is not None, "HAL buffer pool is not bound"
        handle = params.find(ARG_BUFFER_HANDLE)
        offset = params.find(ARG_OFFSET)
        length = params.find(ARG_LENGTH)
        assert handle is not None
        assert offset is not None
        assert length is not None
        return self.sink.write(self._buffer_pool.view_for_driver(handle, offset, length))

    def _flush(self, params: ReadOnlyFlatMapView) -> int:
        self.sink.flush()
        return 0

    def _close(self, params: ReadOnlyFlatMapView) -> int:
        self.sink.close()
        return 0

"""時刻と標準入出力だけを提供する pysim 用ダミードライバ。"""

from __future__ import annotations

import time

from hal_dispatch import (
    ARG_BUFFER_HANDLE,
    ARG_LENGTH,
    ARG_MAX_LEN,
    ARG_OFFSET,
    HalDriver,
    WasiIpcCmd,
)
from system_containers import FlatMapView
from stream_transport import StreamTransport


class Timer:
    """プラットフォーム単調時計のpysim実体。"""

    def get_now_ns(self) -> int:
        return time.monotonic_ns()

    def subscribe(self, nanos: int, callback) -> int:
        assert nanos >= 0
        assert callback is not None
        return 1


class DummyDriver(HalDriver):
    """標準入出力ストリームと単調増加時刻を提供する。"""

    def __init__(
        self,
        uri: str,
        transport: StreamTransport | None = None,
        stream_enabled: bool = True,
    ):
        super().__init__(uri)
        self.transport = transport or StreamTransport()
        self.start_time_ns = time.monotonic_ns()
        self.tick_count = 0
        self.timer = Timer()
        if stream_enabled:
            self.register_command(WasiIpcCmd.STREAM_WRITE_BUFFER, self._write_buffer)
            self.register_command(WasiIpcCmd.STREAM_READ_BUFFER, self._read_buffer)
            self.register_command(WasiIpcCmd.STREAM_FLUSH, self._flush)
            self.register_command(WasiIpcCmd.STREAM_CLOSE, self._close)
        self.register_command(WasiIpcCmd.CLOCK_GET_NOW, self._get_now)
        self.register_command(WasiIpcCmd.CLOCK_SUBSCRIBE, self._subscribe)
        self.register_command(WasiIpcCmd.CLOCK_GET_RES, self._get_resolution)

    def feed_stdin(self, data: bytes) -> int:
        """標準入力ストリームへホスト側からバイト列を供給する。"""
        return self.transport.feed_input(data)

    def drain_stdout(self) -> bytes:
        """ホスト側から標準出力ストリームを読み出す。"""
        return self.transport.drain_output()

    def _buffer_view(self, params: FlatMapView, length_key: int) -> memoryview:
        assert self._buffer_pool is not None, "HAL buffer pool is not bound"
        handle = params.find(ARG_BUFFER_HANDLE)
        offset = params.find(ARG_OFFSET)
        length = params.find(length_key)
        assert handle is not None
        assert offset is not None
        assert length is not None
        return self._buffer_pool.view_for_driver(handle, offset, length)

    def _write_buffer(self, params: FlatMapView) -> int:
        view = self._buffer_view(params, ARG_LENGTH)
        return self.transport.write(view)

    def _read_buffer(self, params: FlatMapView) -> int:
        view = self._buffer_view(params, ARG_MAX_LEN)
        data = self.transport.read_input(len(view))
        view[: len(data)] = data
        return len(data)

    def _flush(self, params: FlatMapView) -> int:
        return 0

    def _close(self, params: FlatMapView) -> int:
        return 0

    def get_monotonic_ns(self) -> int:
        return time.monotonic_ns() - self.start_time_ns

    def step_ticks(self, count: int = 1) -> int:
        assert count >= 0
        self.tick_count += count
        return self.tick_count

    def _get_now(self, params: FlatMapView) -> int:
        return self.timer.get_now_ns()

    def _subscribe(self, params: FlatMapView) -> int:
        return 1

    def _get_resolution(self, params: FlatMapView) -> int:
        return 1_000_000

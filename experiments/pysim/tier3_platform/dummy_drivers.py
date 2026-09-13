"""時刻と標準入出力だけを提供する pysim 用ダミードライバ。"""

from __future__ import annotations

import time

from hal_dispatch import (
    ARG_LENGTH,
    ARG_MAX_LEN,
    HalDriver,
    Timer,
    UartTransport,
    WasiIpcCmd,
)
from system_containers import FlatMapView


class DummyUartDriver(HalDriver):
    """標準入力・標準出力を全二重ストリームとして提供する。"""

    def __init__(
        self, uri: str = "fireball://device/uart/0", transport: UartTransport | None = None
    ):
        super().__init__(uri)
        self.transport = transport or UartTransport()
        self.register_command(WasiIpcCmd.STREAM_WRITE_BUFFER, self._write_buffer)
        self.register_command(WasiIpcCmd.STREAM_READ_BUFFER, self._read_buffer)
        self.register_command(WasiIpcCmd.STREAM_FLUSH, self._flush)
        self.register_command(WasiIpcCmd.STREAM_CLOSE, self._close)

    def write_stdout(self, data: bytes) -> int:
        """標準出力へバイト列をストリーミング送信する。"""
        return self.transport.write(data)

    def feed_stdin(self, data: bytes) -> int:
        """標準入力ストリームへホスト側からバイト列を供給する。"""
        return self.transport.feed_stdin(data)

    def read_stdin(self, max_len: int = 4096) -> bytes:
        """標準入力ストリームから最大 ``max_len`` バイトを読む。"""
        return self.transport.read_stdin(max_len)

    def drain_stdout(self) -> bytes:
        """ホスト側から標準出力ストリームを読み出す。"""
        return self.transport.drain()

    def _write_buffer(self, params: FlatMapView) -> int:
        length = params.find(ARG_LENGTH)
        return 0 if length is None else length

    def _read_buffer(self, params: FlatMapView) -> bytes:
        max_len = params.find(ARG_MAX_LEN)
        return self.read_stdin(4096 if max_len is None or max_len == 0 else max_len)

    def _flush(self, params: FlatMapView) -> int:
        return 0

    def _close(self, params: FlatMapView) -> int:
        return 0


class DummyTimerDriver(HalDriver):
    """単調増加時刻とタイマ tick を提供する。"""

    def __init__(self, uri: str = "fireball://device/timer/0"):
        super().__init__(uri)
        self.start_time_ns = time.monotonic_ns()
        self.tick_count = 0
        self.timer = Timer()
        self.register_command(WasiIpcCmd.CLOCK_GET_NOW, self._get_now)
        self.register_command(WasiIpcCmd.CLOCK_SUBSCRIBE, self._subscribe)
        self.register_command(WasiIpcCmd.CLOCK_GET_RES, self._get_resolution)

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

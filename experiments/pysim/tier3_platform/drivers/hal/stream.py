"""pysim用の固定長標準入出力バッファ実体。"""

from __future__ import annotations

FB_CONF_STDIO_BUFFER_SIZE = 4096


class StreamTransport:
    """HALドライバがアクセスする固定長の標準入出力バッファ。"""

    def __init__(self, capacity: int = FB_CONF_STDIO_BUFFER_SIZE) -> None:
        assert capacity > 0
        self._input = bytearray(capacity)
        self._output = bytearray(capacity)
        self._input_len = 0
        self._output_len = 0
        self.bytes_written = 0

    def write(self, data: memoryview) -> int:
        """デバイス側から標準出力へバイト列を書き込む。"""
        written = len(data)
        assert written <= len(self._output) - self._output_len
        self._output[self._output_len : self._output_len + written] = data
        self._output_len += written
        self.bytes_written += written
        return written

    def feed_input(self, data: bytes) -> int:
        """ホスト側から標準入力へバイト列を供給する。"""
        written = len(data)
        assert written <= len(self._input) - self._input_len
        self._input[self._input_len : self._input_len + written] = data
        self._input_len += written
        return written

    def read_input(self, max_len: int = 4096) -> bytes:
        """デバイス側が標準入力からバイト列を読み出す。"""
        assert max_len > 0
        count = min(max_len, self._input_len)
        data = bytes(self._input[:count])
        remaining = self._input_len - count
        self._input[:remaining] = self._input[count : self._input_len]
        self._input_len = remaining
        return data

    def drain_output(self) -> bytes:
        """ホスト側が標準出力からバイト列を読み出す。"""
        data = bytes(self._output[: self._output_len])
        self._output_len = 0
        return data

    def close(self) -> None:
        self._input_len = 0
        self._output_len = 0


class DedicatedLogSink:
    """固定容量の診断ログ専用Sink。標準出力とは別の蓄積領域を持つ。"""

    def __init__(self, capacity: int = FB_CONF_STDIO_BUFFER_SIZE) -> None:
        assert capacity > 0
        self._output = bytearray(capacity)
        self._output_len = 0
        self.bytes_written = 0

    def write(self, data: memoryview) -> int:
        """診断ログを専用バッファへ書き込む。"""
        written = len(data)
        assert written <= len(self._output) - self._output_len
        self._output[self._output_len : self._output_len + written] = data
        self._output_len += written
        self.bytes_written += written
        return written

    def drain_output(self) -> bytes:
        """蓄積済みログをホスト側へ取り出す。"""
        data = bytes(self._output[: self._output_len])
        self._output_len = 0
        return data

    def close(self) -> None:
        self._output_len = 0


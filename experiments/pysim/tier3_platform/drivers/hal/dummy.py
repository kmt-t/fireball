"""時刻と標準入出力だけを提供する pysim 用ダミードライバ。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from tier2_runtime.hal.dispatch import (
    ARG_BUFFER_HANDLE,
    ARG_LENGTH,
    ARG_MAX_LEN,
    ARG_NANOS,
    ARG_NANOS_HI,
    ARG_OFFSET,
    ARG_POLLABLE_HANDLE,
    HalDriver,
    WasiIpcCmd,
)
from system_containers import ReadOnlyFlatMapView, StaticVector
from tier3_platform.drivers.hal.stream import StreamTransport

FB_CONF_HAL_MAX_POLLABLES = 16
_POLLABLE_SLOT_BITS = 4
_POLLABLE_SLOT_MASK = (1 << _POLLABLE_SLOT_BITS) - 1
_POLLABLE_GENERATION_MAX = (1 << (32 - _POLLABLE_SLOT_BITS)) - 1


@dataclass(slots=True)
class _TimerSubscription:
    deadline_ns: int = 0
    generation: int = 0
    active: bool = False


class Timer:
    """プラットフォーム単調時計のpysim実体。"""

    __slots__ = ("_subscriptions",)

    def __init__(self) -> None:
        self._subscriptions: StaticVector[_TimerSubscription] = StaticVector(
            capacity=FB_CONF_HAL_MAX_POLLABLES
        )
        for _ in range(FB_CONF_HAL_MAX_POLLABLES):
            self._subscriptions.append(_TimerSubscription())

    def get_now_ns(self) -> int:
        return time.monotonic_ns()

    def subscribe(self, nanos: int) -> int:
        """Reserve one bounded one-shot pollable timer and return its generation handle."""
        assert nanos >= 0
        for slot_index, slot in enumerate(self._subscriptions):
            if not slot.active:
                assert slot.generation < _POLLABLE_GENERATION_MAX
                slot.generation += 1
                slot.deadline_ns = self.get_now_ns() + nanos
                slot.active = True
                return (slot.generation << _POLLABLE_SLOT_BITS) | slot_index
        assert False, "HAL timer pollable capacity exceeded"

    def is_ready(self, handle: int) -> bool:
        """Check a live subscription without changing its state."""
        slot = self._slot_for(handle)
        return self.get_now_ns() >= slot.deadline_ns

    def wakeup_ns(self, handle: int) -> int:
        """Return the subscription deadline for a cooperative scheduler wait."""
        return self._slot_for(handle).deadline_ns

    def drop(self, handle: int) -> None:
        """Release a completed or abandoned pollable slot for bounded reuse."""
        slot = self._slot_for(handle)
        slot.active = False

    def _slot_for(self, handle: int) -> _TimerSubscription:
        assert 0 <= handle <= 0xFFFF_FFFF
        slot_index = handle & _POLLABLE_SLOT_MASK
        generation = handle >> _POLLABLE_SLOT_BITS
        assert generation > 0
        slot = self._subscriptions[slot_index]
        assert slot.active and slot.generation == generation, "stale HAL timer pollable handle"
        return slot


class DummyDriver(HalDriver):
    """標準入出力ストリームと単調増加時刻を提供する。"""

    __slots__ = ("start_time_ns", "tick_count", "timer", "transport")

    def __init__(
        self,
        transport: StreamTransport | None = None,
        stream_enabled: bool = True,
    ):
        super().__init__()
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
        self.register_command(WasiIpcCmd.POLL_CHECK, self._poll_check)
        self.register_command(WasiIpcCmd.POLL_WAIT, self._poll_wait)
        self.register_command(WasiIpcCmd.POLL_DROP, self._poll_drop)

    def feed_stdin(self, data: bytes) -> int:
        """標準入力ストリームへホスト側からバイト列を供給する。"""
        return self.transport.feed_input(data)

    def drain_stdout(self) -> bytes:
        """ホスト側から標準出力ストリームを読み出す。"""
        return self.transport.drain_output()

    def _buffer_view(self, params: ReadOnlyFlatMapView, length_key: int) -> memoryview:
        assert self._buffer_pool is not None, "HAL buffer pool is not bound"
        handle = params.find(ARG_BUFFER_HANDLE)
        offset = params.find(ARG_OFFSET)
        length = params.find(length_key)
        assert handle is not None
        assert offset is not None
        assert length is not None
        return self._buffer_pool.view_for_driver(handle, offset, length)

    def _write_buffer(self, params: ReadOnlyFlatMapView) -> int:
        view = self._buffer_view(params, ARG_LENGTH)
        return self.transport.write(view)

    def _read_buffer(self, params: ReadOnlyFlatMapView) -> int:
        view = self._buffer_view(params, ARG_MAX_LEN)
        data = self.transport.read_input(len(view))
        view[: len(data)] = data
        return len(data)

    def _flush(self, params: ReadOnlyFlatMapView) -> int:
        return 0

    def _close(self, params: ReadOnlyFlatMapView) -> int:
        return 0

    def get_monotonic_ns(self) -> int:
        return time.monotonic_ns() - self.start_time_ns

    def step_ticks(self, count: int = 1) -> int:
        assert count >= 0
        self.tick_count += count
        return self.tick_count

    def _get_now(self, params: ReadOnlyFlatMapView) -> int:
        return self.timer.get_now_ns()

    def _subscribe(self, params: ReadOnlyFlatMapView) -> int:
        nanos_low = params.find(ARG_NANOS)
        nanos_high = params.find(ARG_NANOS_HI)
        assert nanos_low is not None
        assert nanos_high is not None
        return self.timer.subscribe(nanos_low | (nanos_high << 32))

    def _get_resolution(self, params: ReadOnlyFlatMapView) -> int:
        resolution = time.get_clock_info("monotonic").resolution
        return max(1, round(resolution * 1_000_000_000))

    def _poll_check(self, params: ReadOnlyFlatMapView) -> int:
        handle = params.find(ARG_POLLABLE_HANDLE)
        assert handle is not None
        return 1 if self.timer.is_ready(handle) else 0

    def _poll_wait(self, params: ReadOnlyFlatMapView) -> int:
        handle = params.find(ARG_POLLABLE_HANDLE)
        assert handle is not None
        return 1 if self.timer.is_ready(handle) else 0

    def poll_wakeup_ns(self, handle: int) -> int:
        return self.timer.wakeup_ns(handle)

    def _poll_drop(self, params: ReadOnlyFlatMapView) -> int:
        handle = params.find(ARG_POLLABLE_HANDLE)
        assert handle is not None
        self.timer.drop(handle)
        return 0

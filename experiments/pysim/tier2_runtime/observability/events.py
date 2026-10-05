"""Runtimeイベント契約とPysim内のC++/Python ABI参照モデル。"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Protocol

from system_containers import StaticVector

RUNTIME_EVENT_NO_MODULE: int = 0xFFFF_FFFF
RUNTIME_EVENT_NO_PC: int = 0xFFFF_FFFF
RUNTIME_EVENT_ABI_MAJOR: int = 1
RUNTIME_EVENT_ABI_MINOR: int = 0
RUNTIME_EVENT_CLOCK_MONOTONIC_NS: int = 1
RUNTIME_EVENT_CLOCK_FREQUENCY_HZ: int = 1_000_000_000

_BATCH_MAGIC: bytes = b"FBEO"
_BATCH_HEADER: struct.Struct = struct.Struct("<4sHHHHHHIIII")
_EVENT_RECORD: struct.Struct = struct.Struct("<HHIIIIIQ")
_BATCH_HEADER_SIZE: int = 32
_EVENT_RECORD_SIZE: int = 32
_EVENT_RECORD_KIND_RUNTIME: int = 1
_BATCH_FLAG_TICKS_VALID: int = 1 << 0
_BATCH_FLAG_OVERFLOW: int = 1 << 1
_U32_MAX: int = 0xFFFF_FFFF

assert _BATCH_HEADER.size == _BATCH_HEADER_SIZE
assert _EVENT_RECORD.size == _EVENT_RECORD_SIZE


class RuntimeEventKind(IntEnum):
    """Runtimeが生成する意味上の実行イベント。"""

    MODULE_LOADED = 1
    MODULE_LOAD = MODULE_LOADED
    FUNCTION_ENTER = 2
    FUNCTION_EXIT = 3
    JIT_ENTER = 4
    JIT_EXIT = 5
    HOST_CALL_ENTER = 6
    HOST_CALL_EXIT = 7
    YIELD = 8
    TRAP = 9
    DEBUG_STOP = 10


class RuntimeEventFlags(IntFlag):
    """C++レコードのフラグとPython Adapter内の状態。"""

    NONE = 0
    TICK_VALID = 1 << 0
    ESTIMATED = 1 << 1
    JIT = 1 << 2
    INTERPRETER = 1 << 8
    TRAP = 1 << 9
    DEBUG_STOP = 1 << 10
    DROPPED = 1 << 11
    ABORTED = 1 << 12


class RuntimeExecutionError(IntEnum):
    """Executor outcome category used without Python exceptions."""

    GUEST_TRAP = 1
    HOST_FAILURE = 2


class RuntimeEventExportStatus(IntEnum):
    """Pysimが再現するC ABI export関数の状態値。"""

    OK = 0
    BUFFER_TOO_SMALL = 1
    INVALID_HANDLE = 2
    UNSUPPORTED_VERSION = 3
    NOT_AT_SAFE_POINT = 4


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Python Event APIの値。C++レコード配置とは独立している。"""

    kind: RuntimeEventKind
    runtime_id: int
    module_id: int
    function_id: int
    guest_pc: int
    tick: int
    call_id: int
    flags: RuntimeEventFlags = RuntimeEventFlags.NONE
    auxiliary: int = 0


@dataclass(frozen=True, slots=True)
class RuntimeEventBatch:
    """ABI Adapterが生成するPython側のイベントバッチ。"""

    runtime_id: int
    records: Sequence[RuntimeEvent]
    dropped_count: int
    clock_frequency_hz: int
    clock_domain: int


@dataclass(frozen=True, slots=True)
class RuntimeEventExport:
    """C ABI export操作の戻り値を表す参照モデル。"""

    status: RuntimeEventExportStatus
    required_size: int
    data: bytes


class RuntimeObserver(Protocol):
    """Python Runtime Event Batch APIを受信する観測プラグイン契約。"""

    def on_runtime_batch(self, batch: RuntimeEventBatch) -> None: ...


class RuntimeEventSink:
    """固定容量のC++ Runtime Event Sinkとexport経路のPysim参照モデル。"""

    __slots__ = ("_head", "clock_domain", "clock_frequency_hz", "dropped_count", "records")

    def __init__(
        self,
        capacity: int = 64,
        clock_frequency_hz: int = RUNTIME_EVENT_CLOCK_FREQUENCY_HZ,
        clock_domain: int = RUNTIME_EVENT_CLOCK_MONOTONIC_NS,
    ) -> None:
        assert capacity > 0
        assert capacity & (capacity - 1) == 0
        assert 0 <= clock_frequency_hz <= _U32_MAX
        assert 0 <= clock_domain <= _U32_MAX
        self._head = 0
        self.records: StaticVector[RuntimeEvent] = StaticVector(capacity=capacity)
        self.dropped_count = 0
        self.clock_frequency_hz = clock_frequency_hz
        self.clock_domain = clock_domain

    def _events(self) -> Iterator[RuntimeEvent]:
        """Borrow the retained records in issue order without another buffer."""
        for index in range(len(self.records)):
            yield self.records[(self._head + index) & (self.records.capacity - 1)]

    @property
    def required_size(self) -> int:
        """Return the exact size required for the next successful export."""

        return _BATCH_HEADER_SIZE + len(self.records) * _EVENT_RECORD_SIZE

    def record(self, event: RuntimeEvent) -> None:
        """Keep the latest events by overwriting the oldest slot in constant time."""

        if len(self.records) < self.records.capacity:
            self.records.append(event)
            return
        self.records[self._head] = event
        self._head = (self._head + 1) & (self.records.capacity - 1)
        self.dropped_count = min(_U32_MAX, self.dropped_count + 1)

    def export(self, abi_major: int, capacity: int) -> RuntimeEventExport:
        """Encode a batch and consume records only after a successful export."""

        required_size = self.required_size
        if abi_major != RUNTIME_EVENT_ABI_MAJOR:
            return RuntimeEventExport(
                RuntimeEventExportStatus.UNSUPPORTED_VERSION, required_size, b""
            )
        if capacity < required_size:
            return RuntimeEventExport(RuntimeEventExportStatus.BUFFER_TOO_SMALL, required_size, b"")

        batch_flags = 0
        if any(event.flags & RuntimeEventFlags.TICK_VALID for event in self._events()):
            batch_flags |= _BATCH_FLAG_TICKS_VALID
        if self.dropped_count:
            batch_flags |= _BATCH_FLAG_OVERFLOW
        output = bytearray(required_size)
        _BATCH_HEADER.pack_into(
            output,
            0,
            _BATCH_MAGIC,
            RUNTIME_EVENT_ABI_MAJOR,
            RUNTIME_EVENT_ABI_MINOR,
            _BATCH_HEADER_SIZE,
            _EVENT_RECORD_SIZE,
            _EVENT_RECORD_KIND_RUNTIME,
            batch_flags,
            len(self.records),
            self.dropped_count,
            self.clock_frequency_hz if batch_flags & _BATCH_FLAG_TICKS_VALID else 0,
            self.clock_domain if batch_flags & _BATCH_FLAG_TICKS_VALID else 0,
        )
        offset = _BATCH_HEADER_SIZE
        for event in self._events():
            _EVENT_RECORD.pack_into(
                output,
                offset,
                int(event.kind),
                self._wire_flags(event),
                event.module_id,
                event.function_id,
                event.guest_pc,
                event.call_id,
                event.auxiliary,
                event.tick if event.flags & RuntimeEventFlags.TICK_VALID else 0,
            )
            offset += _EVENT_RECORD_SIZE
        self.records.clear()
        self._head = 0
        return RuntimeEventExport(RuntimeEventExportStatus.OK, required_size, bytes(output))

    @staticmethod
    def _wire_flags(event: RuntimeEvent) -> int:
        flags = RuntimeEventFlags.NONE
        if event.flags & RuntimeEventFlags.TICK_VALID:
            flags |= RuntimeEventFlags.TICK_VALID
        if event.flags & RuntimeEventFlags.ESTIMATED:
            flags |= RuntimeEventFlags.ESTIMATED
        if event.flags & RuntimeEventFlags.JIT:
            flags |= RuntimeEventFlags.JIT
        return int(flags)


class RuntimeEventAdapter:
    """Convert the versioned byte batch into Python-owned event values."""

    __slots__ = ()

    @staticmethod
    def decode(data: bytes, runtime_id: int) -> RuntimeEventBatch:
        """Validate ABI layout and produce Python API values."""

        assert len(data) >= _BATCH_HEADER_SIZE
        (
            magic,
            abi_major,
            abi_minor,
            header_size,
            record_size,
            record_kind,
            batch_flags,
            record_count,
            dropped_count,
            clock_frequency_hz,
            clock_domain,
        ) = _BATCH_HEADER.unpack_from(data)
        assert magic == _BATCH_MAGIC
        assert abi_major == RUNTIME_EVENT_ABI_MAJOR
        assert abi_minor <= RUNTIME_EVENT_ABI_MINOR
        assert header_size == _BATCH_HEADER_SIZE
        assert record_size == _EVENT_RECORD_SIZE
        assert record_kind == _EVENT_RECORD_KIND_RUNTIME
        assert batch_flags & ~(_BATCH_FLAG_TICKS_VALID | _BATCH_FLAG_OVERFLOW) == 0
        assert len(data) == header_size + record_count * record_size

        records: StaticVector[RuntimeEvent] = StaticVector(capacity=max(1, record_count))
        offset = header_size
        for _ in range(record_count):
            (
                kind_value,
                wire_flags,
                module_id,
                function_id,
                guest_pc,
                call_id,
                auxiliary,
                tick,
            ) = _EVENT_RECORD.unpack_from(data, offset)
            kind = RuntimeEventKind(kind_value)
            assert (
                wire_flags
                & ~int(
                    RuntimeEventFlags.TICK_VALID
                    | RuntimeEventFlags.ESTIMATED
                    | RuntimeEventFlags.JIT
                )
                == 0
            )
            flags = RuntimeEventFlags(wire_flags)
            is_function_boundary = (
                kind == RuntimeEventKind.FUNCTION_ENTER or kind == RuntimeEventKind.FUNCTION_EXIT
            )
            if is_function_boundary and not flags & RuntimeEventFlags.JIT:
                flags |= RuntimeEventFlags.INTERPRETER
            if kind == RuntimeEventKind.TRAP:
                flags |= RuntimeEventFlags.TRAP
            elif kind == RuntimeEventKind.DEBUG_STOP:
                flags |= RuntimeEventFlags.DEBUG_STOP
            if kind == RuntimeEventKind.FUNCTION_EXIT and auxiliary != 0:
                flags |= RuntimeEventFlags.ABORTED
            records.append(
                RuntimeEvent(
                    kind=kind,
                    runtime_id=runtime_id,
                    module_id=module_id,
                    function_id=function_id,
                    guest_pc=guest_pc,
                    tick=tick,
                    call_id=call_id,
                    flags=flags,
                    auxiliary=auxiliary,
                )
            )
            offset += record_size
        return RuntimeEventBatch(
            runtime_id=runtime_id,
            records=records,
            dropped_count=dropped_count,
            clock_frequency_hz=clock_frequency_hz,
            clock_domain=clock_domain,
        )


def dispatch_runtime_event_batch(
    observers: StaticVector[RuntimeObserver], batch: RuntimeEventBatch
) -> None:
    """Deliver each converted batch after the runtime reaches a safe point."""

    for observer in observers:
        observer.on_runtime_batch(batch)

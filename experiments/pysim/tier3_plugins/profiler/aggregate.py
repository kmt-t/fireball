"""Host-side aggregation for decoded profile log records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from system_containers import MutableFlatMapStorage, SequenceView, StaticVector
from tier2_runtime.observability.logger import LOG_HEADER_SIZE, LogDictionary

_PROFILE_ENTER_ID = 0x0501
_PROFILE_EXIT_ID = 0x0502
_U32_MASK = 0xFFFF_FFFF


@dataclass(frozen=True, slots=True)
class ProfileLogRecord:
    event_id: int
    function_id: int
    call_id: int
    tick: int


@dataclass(slots=True)
class _OpenFrame:
    function_id: int
    call_id: int
    start_tick: int
    child_ticks: int = 0


@dataclass(slots=True)
class _FunctionTotals:
    call_count: int = 0
    inclusive_ticks: int = 0
    self_ticks: int = 0


@dataclass(frozen=True, slots=True)
class FunctionProfile:
    function_id: int
    call_count: int
    inclusive_ticks: int
    self_ticks: int


@dataclass(frozen=True, slots=True)
class CallEdgeProfile:
    parent_function_id: int
    function_id: int
    call_count: int


@dataclass(frozen=True, slots=True)
class ProfileReport:
    functions: StaticVector[FunctionProfile]
    edges: StaticVector[CallEdgeProfile]
    unmatched_exits: int

    loss_caveat: ClassVar[str] = (
        "Profile durations may include intervals missing from the debug output bus."
    )


def decode_profile_records(
    data: bytes, dictionary: LogDictionary
) -> StaticVector[ProfileLogRecord]:
    """Decode profile records from the same dictionary-sized wire stream as runtime logs."""
    records: StaticVector[ProfileLogRecord] = StaticVector(capacity=len(data) // LOG_HEADER_SIZE)
    offset = 0
    while offset < len(data):
        assert len(data) - offset >= LOG_HEADER_SIZE, "truncated profile log header"
        dict_offset = data[offset + 1] | (data[offset + 2] << 8) | (data[offset + 3] << 16)
        argument_count = dictionary.argument_count(dict_offset)
        record_size = LOG_HEADER_SIZE + 4 * argument_count
        assert offset + record_size <= len(data), "truncated profile log arguments"
        if dict_offset == _PROFILE_ENTER_ID or dict_offset == _PROFILE_EXIT_ID:
            assert argument_count == 3, "profile dictionary entry requires three arguments"
            values: StaticVector[int] = StaticVector(capacity=3)
            for argument_index in range(3):
                start = offset + LOG_HEADER_SIZE + 4 * argument_index
                values.append(
                    data[start]
                    | (data[start + 1] << 8)
                    | (data[start + 2] << 16)
                    | (data[start + 3] << 24)
                )
            records.append(ProfileLogRecord(dict_offset, values[0], values[1], values[2]))
        offset += record_size
    return records.freeze()


def aggregate_profile(records: SequenceView[ProfileLogRecord]) -> ProfileReport:
    """Aggregate complete stack-matched calls; unmatched exits are counted without estimates."""
    stack: StaticVector[_OpenFrame] = StaticVector(capacity=len(records))
    functions: MutableFlatMapStorage[int, _FunctionTotals] = MutableFlatMapStorage(
        capacity=len(records)
    )
    edges: MutableFlatMapStorage[int, int] = MutableFlatMapStorage(capacity=len(records))
    unmatched_exits = 0
    for record in records:
        if record.event_id == _PROFILE_ENTER_ID:
            if len(stack) > 0:
                parent_id = stack[-1].function_id
                edge_key = ((parent_id & _U32_MASK) << 32) | (record.function_id & _U32_MASK)
                edge_count = edges.view().find(edge_key)
                inserted = edges.insert(edge_key, 1 if edge_count is None else edge_count + 1)
                assert inserted, "edge table sized to event count must not overflow"
            stack.append(_OpenFrame(record.function_id, record.call_id, record.tick))
            totals = functions.view().find(record.function_id)
            if totals is None:
                totals = _FunctionTotals()
                inserted = functions.insert(record.function_id, totals)
                assert inserted, "function table sized to event count must not overflow"
            totals.call_count += 1
        elif record.event_id == _PROFILE_EXIT_ID:
            if len(stack) == 0:
                unmatched_exits += 1
                continue
            frame = stack[-1]
            if frame.function_id != record.function_id or frame.call_id != record.call_id:
                unmatched_exits += 1
                continue
            stack.pop_at()
            inclusive = (record.tick - frame.start_tick) & _U32_MASK
            self_ticks = inclusive - frame.child_ticks
            assert self_ticks >= 0, "child intervals cannot exceed their parent interval"
            totals = functions.view().find(frame.function_id)
            assert totals is not None
            totals.inclusive_ticks += inclusive
            totals.self_ticks += self_ticks
            if len(stack) > 0:
                stack[-1].child_ticks += inclusive
    function_results: StaticVector[FunctionProfile] = StaticVector(capacity=len(functions))
    for function_id, totals in functions:
        function_results.append(
            FunctionProfile(
                function_id, totals.call_count, totals.inclusive_ticks, totals.self_ticks
            )
        )
    edge_results: StaticVector[CallEdgeProfile] = StaticVector(capacity=len(edges))
    for edge_key, count in edges:
        edge_results.append(CallEdgeProfile(edge_key >> 32, edge_key & _U32_MASK, count))
    return ProfileReport(function_results.freeze(), edge_results.freeze(), unmatched_exits)

"""Controlled external vDMA service at the existing synchronous callable boundary.

The double owns device progress, noncoherent bus bytes and its COOS wait adapter.
Interpreter, host-call gateway and scheduler remain real. This does not stand in
for verification of a production DMA driver's cache maintenance implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import StrEnum

from interrupt_event import InterruptEvent
from ipc_router import Role
from scheduler import ChannelAction, TaskState
from system import System
from tier2_runtime.syscall.hostcall import WasiErrno

# Fixture-owned internal completion key; no guest vIRQ is registered for it.
COMPLETION_KEY = 0xD0A0
COMPLETION_EVENT = InterruptEvent(COMPLETION_KEY, 17, 23, 31, 47)


class DmaPhase(StrEnum):
    PENDING = "pending"
    PARTIAL = "partial"
    DEVICE_COMPLETE = "device-complete"
    NOTIFIED = "notified"
    BEFORE_VISIBLE = "before-visible"
    CPU_VISIBLE = "cpu-visible"
    FAILED = "failed"


@dataclass(slots=True)
class MockTransfer:
    source_cpu: memoryview
    source_offset: int
    destination_cpu: memoryview
    destination_offset: int
    count: int
    source_bus: bytearray
    destination_bus: bytearray


class ControlledVdmaMock:
    """TEST-VSOC-62/63/65/66/73: deterministic external service, not guest execution."""

    def __init__(
        self,
        system: System,
        observer: Callable[[DmaPhase, int], None],
        *,
        deferred: bool,
        coherent: bool,
        delay_steps: int = 0,
        fail_after: int | None = None,
    ) -> None:
        assert delay_steps >= 0
        self.system = system
        self.observer = observer
        self.deferred = deferred
        self.coherent = coherent
        self.delay_steps = delay_steps
        self.fail_after = fail_after
        self.requests: list[tuple[int, int, int]] = []
        self.actions: list[str] = []
        self.wait_events: list[InterruptEvent] = []
        self.busy_request: tuple[int, int, int] | None = None
        self.transfer: MockTransfer | None = None
        self.owner_id = 0
        self.producer_id = 0
        self.written = 0

    def occupy(self, source: int, destination: int, count: int) -> None:
        assert self.busy_request is None
        src, so = self.system._vdma_region(source, count, is_write=False)
        dst, do = self.system._vdma_region(destination, count, is_write=True)
        assert src is not None and so is not None and dst is not None and do is not None
        self.transfer = MockTransfer(
            src,
            so,
            dst,
            do,
            count,
            bytearray(src[so : so + count]),
            bytearray(dst[do : do + count]),
        )
        self.busy_request = (source, destination, count)
        self._write_prefix(1)

    def release_engine(self) -> None:
        """Script explicit stop confirmation of the prior mock request."""
        assert self.busy_request is not None
        self._make_visible()
        self.busy_request = None
        self.transfer = None
        self.written = 0

    def __call__(self, source: int, destination: int, count: int) -> int:
        self.requests.append((source, destination, count))
        # AGAIN is this mock target's occupied-engine contract, not a universal
        # vDMA errno or the HAL pool's map-buffer BUSY result.
        if self.busy_request is not None:
            return int(WasiErrno.AGAIN)
        assert self.transfer is None
        src, so = self.system._vdma_region(source, count, is_write=False)
        dst, do = self.system._vdma_region(destination, count, is_write=True)
        if src is None or so is None or dst is None or do is None:
            return int(WasiErrno.FAULT)
        self.owner_id = self.system.scheduler.current_task_id
        self.busy_request = (source, destination, count)
        self.transfer = MockTransfer(
            src,
            so,
            dst,
            do,
            count,
            bytearray(b"\xd7" * count),
            bytearray(dst[do : do + count]),
        )
        # Separate CPU and bus storage models dirty source and stale destination
        # cache lines. Operations below belong to the external service double.
        self.actions.append("clean-source")
        self.transfer.source_bus[:] = src[so : so + count]
        self.actions.extend(("barrier-before", "start"))
        self.observer(DmaPhase.PENDING, 0)
        if self.deferred:
            self.producer_id = self.system.scheduler.spawn(
                "mock_dma_device", self._producer(), role=Role.CORE_SERVICE
            )
            self._wait_for_completion()
        else:
            self._progress()
        self.actions.append("invalidate-destination")
        self.observer(DmaPhase.BEFORE_VISIBLE, self.written)
        self._make_visible()
        self.actions.append("barrier-after")
        if self.fail_after is not None:
            self.observer(DmaPhase.FAILED, self.written)
            self.busy_request = None
            return int(WasiErrno.IO)
        self.observer(DmaPhase.CPU_VISIBLE, self.written)
        self.actions.append("return-success")
        self.busy_request = None
        return int(WasiErrno.SUCCESS)

    def _write_prefix(self, count: int) -> None:
        transfer = self.transfer
        assert transfer is not None and 0 <= count <= transfer.count
        transfer.destination_bus[:count] = transfer.source_bus[:count]
        self.written = count
        if self.coherent:
            start = transfer.destination_offset
            transfer.destination_cpu[start : start + count] = transfer.destination_bus[:count]

    def _progress(self) -> None:
        transfer = self.transfer
        assert transfer is not None
        prefix = transfer.count // 2 if self.fail_after is None else self.fail_after
        assert 0 <= prefix <= transfer.count
        self._write_prefix(prefix)
        self.actions.append("partial-write")
        self.observer(DmaPhase.PARTIAL, prefix)
        if self.fail_after is None:
            self._write_prefix(transfer.count)
        self.actions.append("device-complete")
        self.observer(DmaPhase.DEVICE_COMPLETE, self.written)

    def _producer(self) -> Generator[tuple[ChannelAction, None], None, None]:
        owner = self.system.scheduler.get_task(self.owner_id)
        assert owner is not None
        for _ in range(self.delay_steps):
            self.observer(DmaPhase.PENDING, self.written)
            yield (ChannelAction.YIELD, None)
        # A different interrupt cannot satisfy this operation's registered wait.
        assert self.system.scheduler.notify_interrupt(
            InterruptEvent(COMPLETION_KEY + 1, 17, 23, 31, 47)
        )
        self.observer(DmaPhase.PENDING, self.written)
        yield (ChannelAction.YIELD, None)
        self._progress()
        state_before = owner.state
        pending_before = owner.pending_interrupt_event
        assert self.system.scheduler.notify_interrupt(COMPLETION_EVENT)
        # ISR publication alone must not perform a task-state transition.
        assert owner.state == state_before == TaskState.BLOCKED
        assert owner.pending_interrupt_event is pending_before is None
        self.actions.append("notify")
        self.observer(DmaPhase.NOTIFIED, self.written)

    def _wait_for_completion(self) -> None:
        """Adapt the synchronous test service using the existing COOS wait/FIFO.

        Nested scheduler steps drive the mock device while the native call stack
        stays suspended in this service. Detach the awakened owner before it can
        be selected recursively; restore the same outer coroutine afterwards.
        No Interpreter instruction stepping or product waiting path is replaced.
        """
        scheduler = self.system.scheduler
        task = scheduler.current_task
        assert task is not None and task.task_id == self.owner_id
        with scheduler.task_context(task):
            scheduler.wait_for_interrupt(COMPLETION_KEY)
            # Bounded by the scripted producer, not by an external-device timeout.
            for _ in range(4 * self.delay_steps + 64):
                scheduler.drain_interrupts()
                if task.state == TaskState.READY:
                    break
                assert task.state == TaskState.BLOCKED
                with scheduler.task_context(task):
                    assert scheduler.step() is not None, "scripted completion producer stalled"
            else:
                assert False, "scripted producer did not publish its completion"
            scheduler.detach(task)
            event = scheduler.consume_interrupt_event()
            assert event == COMPLETION_EVENT
            self.wait_events.append(event)
            assert task.waiting_irq is None and task.pending_interrupt_event is None
            task.state = TaskState.RUNNING

    def _make_visible(self) -> None:
        transfer = self.transfer
        assert transfer is not None
        start = transfer.destination_offset
        transfer.destination_cpu[start : start + transfer.count] = transfer.destination_bus

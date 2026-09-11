"""
docs/components/tier3_platform/concepts/platform_driver_concept.py
Reference Concept Implementation: physical interrupt push boundary

The concept models the HAL driver's interrupt notification boundary. Device
register access and RSP framing remain separate physical-driver concerns.
"""

from dataclasses import dataclass
from typing import Final

BACKS = [
    "components/tier3_platform/platform_driver.md",
]

INTERRUPT_EVENT_WORDS: Final[int] = 5


@dataclass(frozen=True)
class InterruptEvent:
    vector_id: int
    source_id: int
    cause_code: int
    payload0: int
    payload1: int

    @classmethod
    def from_words(cls, words: tuple[int, ...]) -> "InterruptEvent":
        if len(words) != INTERRUPT_EVENT_WORDS:
            raise ValueError("interrupt-event must contain exactly five words")
        return cls(*words)

    def as_words(self) -> tuple[int, int, int, int, int]:
        return (
            self.vector_id,
            self.source_id,
            self.cause_code,
            self.payload0,
            self.payload1,
        )


class InterruptFifo:
    """Fixed-capacity FIFO used between ISR notification and scheduler drain."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("FIFO capacity must be positive")
        self._slots: list[InterruptEvent | None] = [None] * capacity
        self._read_index = 0
        self._write_index = 0
        self._size = 0

    @property
    def capacity(self) -> int:
        return len(self._slots)

    @property
    def pending_count(self) -> int:
        return self._size

    def push(self, event: InterruptEvent) -> bool:
        if self._size == self.capacity:
            return False
        self._slots[self._write_index] = event
        self._write_index = (self._write_index + 1) % self.capacity
        self._size += 1
        return True

    def pop(self) -> InterruptEvent | None:
        if self._size == 0:
            return None
        event = self._slots[self._read_index]
        self._slots[self._read_index] = None
        self._read_index = (self._read_index + 1) % self.capacity
        self._size -= 1
        return event


class PlatformDriverConcept:
    """HAL interrupt boundary with no ISR-side task-state mutation."""

    def __init__(self, fifo_capacity: int = 4) -> None:
        self._fifo = InterruptFifo(fifo_capacity)
        self._task_ready = False

    @property
    def task_ready(self) -> bool:
        return self._task_ready

    @property
    def pending_count(self) -> int:
        return self._fifo.pending_count

    def isr_notify(self, words: tuple[int, ...]) -> bool:
        """Construct and enqueue an event; this method cannot set task_ready."""
        return self._fifo.push(InterruptEvent.from_words(words))

    def scheduler_drain(self) -> InterruptEvent | None:
        """Drain one event at the cooperative scheduler boundary."""
        event = self._fifo.pop()
        if event is not None:
            self._task_ready = True
        return event


def test_isr_push_preserves_event_and_delays_ready() -> None:
    driver = PlatformDriverConcept(fifo_capacity=2)
    words = (1, 2, 3, 4, 5)
    assert driver.isr_notify(words) is True
    assert driver.task_ready is False
    assert driver.pending_count == 1

    event = driver.scheduler_drain()
    assert event is not None
    assert event.as_words() == words
    assert driver.task_ready is True
    assert driver.pending_count == 0


def test_fixed_fifo_rejects_overflow() -> None:
    driver = PlatformDriverConcept(fifo_capacity=1)
    event = (10, 20, 30, 40, 50)
    assert driver.isr_notify(event) is True
    assert driver.isr_notify(event) is False
    assert driver.pending_count == 1


if __name__ == "__main__":
    test_isr_push_preserves_event_and_delays_ready()
    test_fixed_fifo_rejects_overflow()
    print("[PASS] Platform driver interrupt boundary concept tests passed.")

"""Generic fixed-width interrupt-event shared by COOS and the vSoC runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterruptEvent:
    """Five u32 words carried from ISR notification to a vSoC safepoint."""

    vector_id: int
    source_id: int
    cause_code: int
    payload0: int
    payload1: int

    def words(self) -> tuple[int, int, int, int, int]:
        return (
            self.vector_id & 0xFFFF_FFFF,
            self.source_id & 0xFFFF_FFFF,
            self.cause_code & 0xFFFF_FFFF,
            self.payload0 & 0xFFFF_FFFF,
            self.payload1 & 0xFFFF_FFFF,
        )

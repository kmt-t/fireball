"""
experiments/pysim/recovery.py

{META_RecoveryStrategy} -- docs/components/tier1_interface/interface_wit.md 3.2.

Every fallible host-side operation returns one of four strategies instead of
a bare error code, so the caller always has an actionable next step:
ignore / retry / restart / panic.
"""

from __future__ import annotations

import time
from enum import IntEnum
from typing import Callable


class RecoveryStrategy(IntEnum):
    IGNORE = 0
    RETRY = 1
    RESTART = 2
    PANIC = 3


FB_CONF_RETRY_BACKOFF_MS = 10   # docs/components/tier1_core/system_config.md 3.3.7
RETRY_MAX_ATTEMPTS = 3          # interface_wit.md 3.2's retry invariant


class Panic(Exception):
    """A `panic` recovery-strategy decision. In the real system this halts
    the whole kernel and dumps state; here it stands in for that."""


class RetryExhausted(Exception):
    """Raised when an operation still fails after RETRY_MAX_ATTEMPTS attempts.

    FINDING: interface_wit.md's invariant only states that retries "must not
    exceed 3" -- it never says what happens on the 3rd failure. This concept
    escalates to RESTART (the caller's TCB/heap gets reset) as the only sane
    default, since silently retrying forever or silently giving up with no
    strategy at all both violate {META_RecoveryStrategy}'s "the caller
    always gets an actionable strategy" design point. The spec should say
    this explicitly instead of leaving it to be discovered here.
    """

    def __init__(self, attempts: int, escalated_to: RecoveryStrategy = RecoveryStrategy.RESTART):
        self.attempts = attempts
        self.escalated_to = escalated_to
        super().__init__(f"retry exhausted after {attempts} attempts -> escalated to {escalated_to.name}")


def call_with_retry(
    operation: Callable[[], bool],
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    backoff_ms: int = FB_CONF_RETRY_BACKOFF_MS,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Runs operation() until it returns True or max_attempts is exhausted.

    Returns the number of attempts actually made. Raises RetryExhausted
    (never silently swallows a permanent failure) once max_attempts is hit.
    """
    for attempt in range(1, max_attempts + 1):
        if operation():
            return attempt
        if attempt < max_attempts:
            sleep(backoff_ms / 1000.0)
    raise RetryExhausted(attempts=max_attempts)


def classify_ipc_enqueue_failure(queue_was_full: bool) -> RecoveryStrategy:
    """FINDING: interface_wit.md's `ignore` row example text reads "一時的な
    バッファ空/満杯通知など、データ喪失を伴わず無視可能な事象" (buffer
    full/empty *notifications*, no data loss). But ipc_router.md 4.1's
    actual Queue-Full behavior is a failed Enqueue that rolls ownership
    back to the sender -- the message *is* lost unless the sender retries.
    That is `retry`'s definition ("再試行により回復可能"), not `ignore`'s.
    The two docs use the same words ("バッファ...満杯") for what turn out,
    on inspection, to be two different situations. This function pins down
    the resolution this concept assumes (queue-full-on-send is RETRY, not
    IGNORE) instead of silently reproducing the ambiguity.
    """
    return RecoveryStrategy.RETRY if queue_was_full else RecoveryStrategy.IGNORE

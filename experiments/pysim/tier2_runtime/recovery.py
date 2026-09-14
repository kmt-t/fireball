"""
experiments/pysim/tier1_core/recovery.py
{META_RecoveryStrategy} & {Errorcode_To_Strategy}
Reference Implementation of the Fireball Recovery Engine without exceptions.
Mirroring docs/components/tier1_interface/interface_wit.md §3.2,
docs/components/tier1_core/os_coos.md §4.2, and
docs/components/tier1_core/system_config.md §3.3.7.
Recovery Strategy Classification:
  0. IGNORE: Harmless or transient notification, caller continues execution.
  1. RETRY: Transient contention/timeout. Re-execute after FB_CONF_RETRY_BACKOFF_MS (10ms), up to 3 times.
  2. RESTART: Module/task context corrupted or retry exhausted. Reset TCB/heap/context and restart.
  3. PANIC: Fatal safety violation (MPU fault, double free, RBAC violation). Halt system safely.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import IntEnum
from typing import Generic, Never, TypeVar

T = TypeVar("T")
E = TypeVar("E")
FB_CONF_RETRY_BACKOFF_MS = 10  # docs/components/tier1_core/system_config.md 3.3.7
RETRY_MAX_ATTEMPTS = 3  # interface_wit.md 3.2 retry invariant


class RecoveryStrategy(IntEnum):
    IGNORE = 0
    RETRY = 1
    RESTART = 2
    PANIC = 3


class Result(Generic[T, E]):
    """Zero-exception Result container representing success or failure with actionable recovery strategy."""

    __slots__ = ("error", "is_ok", "strategy", "value")

    def __init__(
        self,
        is_ok: bool,
        value: T | None = None,
        error: E | None = None,
        strategy: RecoveryStrategy = RecoveryStrategy.IGNORE,
    ):
        self.is_ok = is_ok
        self.value = value
        self.error = error
        self.strategy = strategy

    @classmethod
    def ok(cls, value: T) -> Result[T, Never]:
        return cls(is_ok=True, value=value, error=None, strategy=RecoveryStrategy.IGNORE)

    @classmethod
    def err(cls, error: E, strategy: RecoveryStrategy = RecoveryStrategy.RETRY) -> Result[Never, E]:
        return cls(is_ok=False, value=None, error=error, strategy=strategy)

    def unwrap(self) -> T:
        if not self.is_ok:
            return None  # No exception raised
        return self.value


def classify_trap_strategy(trap: str) -> RecoveryStrategy:
    """Deterministic mapping from a vMMIO / interpreter / MPU trap string to {META_RecoveryStrategy}."""
    trap = trap.upper()
    if (
        trap.find("OUT_OF_BOUNDS") >= 0
        or trap.find("ACCESS_VIOLATION") >= 0
        or trap.find("OWNER_MISMATCH") >= 0
        or trap.find("MPU") >= 0
    ):
        return RecoveryStrategy.PANIC
    if trap.find("UNDEFINED_FC") >= 0:
        return RecoveryStrategy.PANIC
    if trap.find("UNREGISTERED_PAGE") >= 0 or trap.find("UNINITIALIZED") >= 0:
        return RecoveryStrategy.RESTART
    if trap.find("BUSY") >= 0 or trap.find("AGAIN") >= 0:
        return RecoveryStrategy.RETRY
    return RecoveryStrategy.RESTART


def classify_errno_strategy(errno: int) -> RecoveryStrategy:
    """Deterministic mapping from a WASI errno (wasi::errno) to {META_RecoveryStrategy}."""
    if errno == 0:  # SUCCESS
        return RecoveryStrategy.IGNORE
    if errno == 6 or errno == 73 or errno == 76:  # EAGAIN, ETIMEDOUT, ENOMEM
        return RecoveryStrategy.RETRY
    if errno == 28 or errno == 44 or errno == 8:  # EINVAL, ENOENT, EBADF
        return RecoveryStrategy.RESTART
    if errno == 63 or errno == 21:  # EPERM, EFAULT
        return RecoveryStrategy.PANIC
    return RecoveryStrategy.RESTART


class RecoveryManager:
    """Manages layered execution, retries, escalations, and reset/panic recovery actions without exceptions."""

    def __init__(
        self,
        max_retries: int = RETRY_MAX_ATTEMPTS,
        backoff_ms: int = FB_CONF_RETRY_BACKOFF_MS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.max_retries = max_retries
        self.backoff_ms = backoff_ms
        self.sleep_fn = sleep_fn
        self.total_retries = 0
        self.total_restarts = 0
        self.total_panics = 0

    def execute_with_recovery(
        self,
        operation: Callable[[], Result[T, E]],
        task_reset_fn: Callable[[], bool] | None = None,
        panic_fn: Callable[[str], None] | None = None,
    ) -> Result[T, E]:
        """
        Executes operation with full 4-tier recovery strategy without raising exceptions.
                Workflow:
                  1. Initial attempts with RETRY (up to max_retries with backoff).
                  2. On retry exhaustion: escalate to RESTART.
                  3. RESTART: invoke task_reset_fn() to clean TCB/heap and retry once.
                  4. PANIC: invoke panic_fn() to safely halt kernel and return PANIC result.
        """
        # Tier 1: Initial execution and retry loop
        for attempt in range(1, self.max_retries + 1):
            res = operation()
            if res.is_ok:
                return res
            strategy = res.strategy
            if strategy == RecoveryStrategy.IGNORE:
                return res
            if strategy == RecoveryStrategy.PANIC:
                self.total_panics += 1
                if panic_fn:
                    panic_fn(f"Fatal panic triggered by error: {res.error}")
                return Result.err(error=res.error, strategy=RecoveryStrategy.PANIC)
            if strategy == RecoveryStrategy.RESTART:
                break  # Directly escalate to task reset
            # RETRY strategy: backoff and retry
            self.total_retries += 1
            if attempt < self.max_retries:
                self.sleep_fn(self.backoff_ms / 1000.0)

        # Tier 2: Retry Exhaustion -> Escalate to RESTART
        self.total_restarts += 1
        if task_reset_fn is not None:
            reset_ok = task_reset_fn()
            if reset_ok:
                # Re-run after clean task reset
                post_reset_res = operation()
                if post_reset_res.is_ok:
                    return post_reset_res
        # Tier 3: Unrecoverable after restart -> Escalate to PANIC
        self.total_panics += 1
        if panic_fn:
            panic_fn("Unrecoverable error after restart escalation")
        return Result.err(
            error="RETRY_EXHAUSTED_ESCALATED_TO_PANIC", strategy=RecoveryStrategy.PANIC
        )

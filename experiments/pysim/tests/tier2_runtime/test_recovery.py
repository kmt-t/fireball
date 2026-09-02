from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: Fault Recovery Strategies
Traceability: system_recovery_spec
"""

import sys
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from recovery import (
    RecoveryManager,
    RecoveryStrategy,
    Result,
    classify_errno_strategy,
    classify_trap_strategy,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_recovery_01_retry_success_within_limit():
    """RECOVERY-01: Transient failure succeeds within 3 retries (10ms backoff) without exceptions."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    attempts = [0]

    def op() -> Result[str, str]:
        attempts[0] += 1
        if attempts[0] < 3:
            return Result.err("BUSY", RecoveryStrategy.RETRY)
        return Result.ok("SUCCESS_DATA")

    res = mgr.execute_with_recovery(op)
    assert res.is_ok is True
    assert res.value == "SUCCESS_DATA"
    assert attempts[0] == 3
    assert mgr.total_retries == 2
    assert mgr.total_restarts == 0
    assert mgr.total_panics == 0


def test_recovery_02_retry_exhaustion_escalates_to_restart():
    """RECOVERY-02: 3-attempt retry exhaustion automatically escalates to RESTART."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    attempts = [0]
    reset_called = [False]

    def failing_op() -> Result[str, str]:
        attempts[0] += 1
        if reset_called[0]:
            return Result.ok("RECOVERED_AFTER_RESET")
        return Result.err("RESOURCE_EXHAUSTED", RecoveryStrategy.RETRY)

    def do_reset() -> bool:
        reset_called[0] = True
        return True

    res = mgr.execute_with_recovery(failing_op, task_reset_fn=do_reset)
    assert res.is_ok is True
    assert res.value == "RECOVERED_AFTER_RESET"
    assert attempts[0] == 4  # 3 initial retries + 1 post-reset run
    assert reset_called[0] is True
    assert mgr.total_restarts == 1
    assert mgr.total_panics == 0


def test_recovery_03_panic_triggers_immediate_failsafe():
    """RECOVERY-03: Fatal safety violation (MPU fault/permission) triggers PANIC immediately without retry."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    panic_msg = []

    def fatal_op() -> Result[str, str]:
        return Result.err("TRAP_ACCESS_VIOLATION", RecoveryStrategy.PANIC)

    def panic_hook(msg: str) -> None:
        panic_msg.append(msg)

    res = mgr.execute_with_recovery(fatal_op, panic_fn=panic_hook)
    assert res.is_ok is False
    assert res.strategy == RecoveryStrategy.PANIC
    assert len(panic_msg) == 1
    assert "TRAP_ACCESS_VIOLATION" in panic_msg[0]
    assert mgr.total_panics == 1
    assert mgr.total_retries == 0


def test_recovery_04_errorcode_to_strategy_mapping():
    """RECOVERY-04: Error code to RecoveryStrategy mapping matches {Errorcode_To_Strategy} spec."""
    # WASI Errno mappings
    assert classify_errno_strategy(0) == RecoveryStrategy.IGNORE  # SUCCESS
    assert classify_errno_strategy(6) == RecoveryStrategy.RETRY  # EAGAIN
    assert classify_errno_strategy(73) == RecoveryStrategy.RETRY  # ETIMEDOUT
    assert classify_errno_strategy(76) == RecoveryStrategy.RETRY  # ENOMEM
    assert classify_errno_strategy(28) == RecoveryStrategy.RESTART  # EINVAL
    assert classify_errno_strategy(44) == RecoveryStrategy.RESTART  # ENOENT
    assert classify_errno_strategy(8) == RecoveryStrategy.RESTART  # EBADF
    assert classify_errno_strategy(63) == RecoveryStrategy.PANIC  # EPERM
    assert classify_errno_strategy(21) == RecoveryStrategy.PANIC  # EFAULT
    # String traps
    assert classify_trap_strategy("TRAP_MEMORY_OUT_OF_BOUNDS") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_ACCESS_VIOLATION") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_OWNER_MISMATCH") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_UNDEFINED_FC") == RecoveryStrategy.PANIC
    assert classify_trap_strategy("TRAP_UNREGISTERED_PAGE") == RecoveryStrategy.RESTART


# ===========================================================================
# 6. Tier 3 JIT Hotspot Profiling & 3-Bank Cache (jit_compiler_test_spec.md, jit_runtime_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_recovery_01_retry_success_within_limit()
    test_recovery_02_retry_exhaustion_escalates_to_restart()
    test_recovery_03_panic_triggers_immediate_failsafe()
    test_recovery_04_errorcode_to_strategy_mapping()
    print("[PASS] All 4 Fault Recovery Strategies tests passed.")

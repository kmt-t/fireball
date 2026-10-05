from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: Fault Recovery Strategies
Traceability: docs/qa/tier3_platform/interface_wit_test_spec.md, TEST-WIT-01..05
"""

import pytest
from config import FB_CONF_RETRY_BACKOFF_MS, FB_CONF_RETRY_MAX_ATTEMPTS
from tier2_runtime.runtime.recovery import (
    RecoveryManager,
    RecoveryStrategy,
    Result,
    classify_errno_strategy,
    classify_trap_strategy,
)


@pytest.mark.parametrize("success_attempt", (1, 2, 3))
def test_recovery_01_retry_success_within_limit(success_attempt: int) -> None:
    """TEST-WIT-02: 成功位置にかかわらず即時終了し、成功後の再実行を禁止する。"""
    sleeps: list[float] = []
    timeline: list[str] = []

    def sleep_spy(seconds: float) -> None:
        sleeps.append(seconds)
        timeline.append("sleep")

    mgr = RecoveryManager(sleep_fn=sleep_spy)
    assert mgr.max_retries == FB_CONF_RETRY_MAX_ATTEMPTS == 3
    assert mgr.backoff_ms == FB_CONF_RETRY_BACKOFF_MS == 10
    attempts = [0]

    def op() -> Result[str, str]:
        attempts[0] += 1
        timeline.append("operation")
        if attempts[0] < success_attempt:
            return Result.err("BUSY", RecoveryStrategy.RETRY)
        return Result.ok("SUCCESS_DATA")

    def reset() -> bool:
        timeline.append("reset")
        return True

    def panic(_message: str) -> None:
        timeline.append("panic")

    res = mgr.execute_with_recovery(op, task_reset_fn=reset, panic_fn=panic)
    assert res.is_ok is True
    assert res.value == "SUCCESS_DATA"
    assert attempts[0] == success_attempt
    assert sleeps == [0.010] * (success_attempt - 1)
    assert timeline == ["operation", "sleep"] * (success_attempt - 1) + ["operation"]
    assert mgr.total_retries == success_attempt - 1
    assert mgr.total_restarts == 0
    assert mgr.total_panics == 0


def test_recovery_02_retry_exhaustion_escalates_to_restart():
    """TEST-WIT-02/03: 初期3回で打ち切り、reset後も失敗要求を再実行しない。"""
    sleeps: list[float] = []
    timeline: list[str] = []
    panic_messages: list[str] = []

    def sleep_spy(seconds: float) -> None:
        sleeps.append(seconds)
        timeline.append("sleep")

    mgr = RecoveryManager(sleep_fn=sleep_spy)
    attempts = [0]
    reset_called = [False]

    def failing_op() -> Result[str, str]:
        attempts[0] += 1
        timeline.append("operation-after-reset" if reset_called[0] else "operation")
        if reset_called[0]:
            return Result.ok("RECOVERED_AFTER_RESET")
        return Result.err("RESOURCE_EXHAUSTED", RecoveryStrategy.RETRY)

    def do_reset() -> bool:
        timeline.append("reset")
        reset_called[0] = True
        return True

    res = mgr.execute_with_recovery(
        failing_op, task_reset_fn=do_reset, panic_fn=panic_messages.append
    )
    assert not res.is_ok
    assert res.error == "RESOURCE_EXHAUSTED"
    assert res.strategy == RecoveryStrategy.RESTART
    assert attempts[0] == 3
    assert sleeps == [0.010, 0.010]
    assert timeline == [
        "operation",
        "sleep",
        "operation",
        "sleep",
        "operation",
        "reset",
    ]
    assert reset_called[0] is True
    assert mgr.total_restarts == 1
    assert mgr.total_panics == 0
    assert panic_messages == []


def test_recovery_03_panic_invokes_hook_immediately_without_retry_or_reset():
    """TEST-WIT-05: PANICの選択と通知だけを検査し、実機停止を主張しない。"""
    sleeps: list[float] = []
    mgr = RecoveryManager(sleep_fn=sleeps.append)
    panic_msg: list[str] = []
    actions: list[str] = []

    def fatal_op() -> Result[str, str]:
        actions.append("operation")
        return Result.err("TRAP_ACCESS_VIOLATION", RecoveryStrategy.PANIC)

    def reset_hook() -> bool:
        actions.append("reset")
        return True

    def panic_hook(msg: str) -> None:
        panic_msg.append(msg)
        actions.append("panic")

    res = mgr.execute_with_recovery(fatal_op, task_reset_fn=reset_hook, panic_fn=panic_hook)
    assert res.is_ok is False
    assert res.strategy == RecoveryStrategy.PANIC
    assert res.error == "TRAP_ACCESS_VIOLATION"
    assert res.value is None
    assert len(panic_msg) == 1
    assert "TRAP_ACCESS_VIOLATION" in panic_msg[0]
    assert mgr.total_panics == 1
    assert mgr.total_retries == 0
    assert mgr.total_restarts == 0
    assert sleeps == []
    assert actions == ["operation", "panic"]


def test_recovery_04_errorcode_to_strategy_mapping():
    """TEST-WIT-01..05: {Errorcode_To_Strategy}の分類を具体例で照合する。"""
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
    success = Result.ok("value")
    assert success.is_ok
    assert success.error is None
    assert success.unwrap() == "value"
    failure = Result.err("failed")
    assert not failure.is_ok
    assert failure.value is None
    assert failure.error == "failed"
    with pytest.raises(AssertionError, match="failed Result"):
        failure.unwrap()


def test_recovery_05_ignore_returns_original_result_without_recovery_actions():
    """TEST-WIT-01: IGNOREは元の結果を返し、待機・reset・panicを実行しない。"""
    sleeps: list[float] = []
    actions: list[str] = []
    mgr = RecoveryManager(sleep_fn=sleeps.append)
    original: Result[str, str] = Result.err("NO_DATA", RecoveryStrategy.IGNORE)

    def operation() -> Result[str, str]:
        actions.append("operation")
        return original

    def reset() -> bool:
        actions.append("reset")
        return True

    def panic(_message: str) -> None:
        actions.append("panic")

    assert mgr.execute_with_recovery(operation, reset, panic) is original
    assert original.error == "NO_DATA"
    assert original.strategy == RecoveryStrategy.IGNORE
    assert sleeps == []
    assert actions == ["operation"]
    assert (mgr.total_retries, mgr.total_restarts, mgr.total_panics) == (0, 0, 0)


def test_recovery_06_restart_skips_retry_backoff_and_invokes_reset_once():
    """TEST-WIT-04: 直接RESTARTを選択すると待機せずresetへ進む。"""
    sleeps: list[float] = []
    actions: list[str] = []
    mgr = RecoveryManager(sleep_fn=sleeps.append)
    reset_completed = False

    def operation() -> Result[str, str]:
        actions.append("operation")
        if reset_completed:
            return Result.ok("recovered")
        return Result.err("CONTEXT_CORRUPTED", RecoveryStrategy.RESTART)

    def reset() -> bool:
        nonlocal reset_completed
        actions.append("reset")
        reset_completed = True
        return True

    result = mgr.execute_with_recovery(operation, reset)
    assert not result.is_ok
    assert result.error == "CONTEXT_CORRUPTED"
    assert result.strategy == RecoveryStrategy.RESTART
    assert actions == ["operation", "reset"]
    assert sleeps == []
    assert (mgr.total_retries, mgr.total_restarts, mgr.total_panics) == (0, 1, 0)


@pytest.mark.parametrize(
    "reset_succeeds", [False, True], ids=["reset-fails", "reset-recovers-service-only"]
)
def test_recovery_07_unrecoverable_restart_escalates_to_panic_once(reset_succeeds: bool):
    """TEST-WIT-03/05: reset成功は要求失敗を返し、reset失敗だけPANICへ進む。"""
    sleeps: list[float] = []
    actions: list[str] = []
    panic_messages: list[str] = []
    mgr = RecoveryManager(sleep_fn=sleeps.append)

    def operation() -> Result[str, str]:
        actions.append("operation")
        return Result.err("BUSY", RecoveryStrategy.RETRY)

    def reset() -> bool:
        actions.append("reset")
        return reset_succeeds

    def panic(message: str) -> None:
        actions.append("panic")
        panic_messages.append(message)

    result = mgr.execute_with_recovery(operation, reset, panic)
    assert not result.is_ok
    assert result.strategy == (
        RecoveryStrategy.RESTART if reset_succeeds else RecoveryStrategy.PANIC
    )
    assert result.value is None
    assert result.error is not None
    assert actions == ["operation"] * 3 + ["reset"] + ([] if reset_succeeds else ["panic"])
    assert sleeps == [0.010, 0.010]
    assert len(panic_messages) == (0 if reset_succeeds else 1)
    assert mgr.total_restarts == 1
    assert mgr.total_panics == (0 if reset_succeeds else 1)


def test_recovery_result_without_success_value_fails_fast() -> None:
    result: Result[int, str] = Result(is_ok=True)
    with pytest.raises(AssertionError, match="without a value"):
        result.unwrap()


def test_recovery_panic_preserves_concrete_error_value() -> None:
    manager = RecoveryManager(sleep_fn=lambda _seconds: None)

    def operation() -> Result[int, int]:
        return Result.err(37, RecoveryStrategy.RETRY)

    result = manager.execute_with_recovery(operation, task_reset_fn=lambda: False)
    assert result.strategy == RecoveryStrategy.PANIC
    assert result.error == 37
    assert result.value is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

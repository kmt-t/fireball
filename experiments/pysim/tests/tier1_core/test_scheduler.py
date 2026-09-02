from __future__ import annotations

"""
Unit tests for Tier 1 Core: Round-Robin Scheduler
Traceability: os_scheduler_test_spec.md
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

from scheduler import Scheduler


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_sched_01_pure_round_robin_fifo():
    """SCHED-01: Pure round-robin execution without priority bias."""
    order: list[str] = []

    def worker(name: str, steps: int):
        for _ in range(steps):
            order.append(name)
            yield None

    sched = Scheduler()
    sched.spawn("a", worker("a", 2))
    sched.spawn("b", worker("b", 2))
    sched.run_to_completion()
    assert order == ["a", "b", "a", "b"]


def test_sched_02_task_capacity_limit():
    """SCHED-02: Scheduler enforces FB_CONF_MAX_TASKS (16) limit."""
    sched = Scheduler(max_tasks=4)
    for i in range(4):
        sched.spawn(f"t{i}")

    try:
        sched.spawn("t_overflow")
        raise AssertionError("Expected RuntimeError for task capacity overflow")
    except RuntimeError as e:
        assert "capacity exceeded" in str(e)


def test_sched_03_duplicate_task_id_rejected():
    """SCHED-03: Attempting to spawn with an existing task_id is rejected."""
    sched = Scheduler()
    sched.spawn("t1", task_id=10)
    try:
        sched.spawn("t2", task_id=10)
        raise AssertionError("Expected ValueError for duplicate task_id")
    except ValueError as e:
        assert "already exists" in str(e)


# ===========================================================================
# 3. Tier 3 Platform Memory: Partitions & SharedBlock RAII (platform_memory_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_sched_01_pure_round_robin_fifo()
    test_sched_02_task_capacity_limit()
    test_sched_03_duplicate_task_id_rejected()
    print("[PASS] All 3 Round-Robin Scheduler tests passed.")

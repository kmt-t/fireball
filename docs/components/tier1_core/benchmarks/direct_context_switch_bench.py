"""
docs/components/tier1_core/benchmarks/direct_context_switch_bench.py
Empirical backing for {DirectContextSwitch} (requires/requirement_list.md),
whose verification method is declared as "ベンチマーク" (Benchmark).

Drives the real, shipped decision function -- COOSKernel._handoff_or_yield()
in ../concepts/coos_concept.py -- rather than reimplementing the scheduling
logic here, so this benchmark cannot silently drift from what the concept
code actually does. The claim under test: symmetric coroutine transfer
bypasses the READY-queue append/pop pair that a queue-mediated handoff would
otherwise pay, for every handoff within the consecutive-handoff bound.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "concepts"))
from coos_concept import COOSKernel


def count_queue_ops_for_n_handoffs(n: int, max_consecutive_handoffs: int) -> tuple[int, int]:
    """Returns (direct_switch_count, queue_op_count) for n handoff decisions."""
    kernel = COOSKernel(max_consecutive_handoffs=max_consecutive_handoffs)
    direct = 0
    queue_ops = 0
    for i in range(n):
        kind, _ = kernel._handoff_or_yield(f"task_{i % 4}")
        if kind == "DIRECT_SWITCH":
            direct += 1
        else:
            queue_ops += 1  # one ready_queue.append() actually happened
    return direct, queue_ops


def time_direct_switch_decision(n: int) -> float:
    """Wall-clock cost of n handoff decisions, all within the direct-switch bound."""
    kernel = COOSKernel(max_consecutive_handoffs=n + 1)
    start = time.perf_counter()
    for i in range(n):
        kernel._handoff_or_yield(f"task_{i % 4}")
    return time.perf_counter() - start


def main() -> None:
    n = 100_000
    bound = 4
    direct, queue_ops = count_queue_ops_for_n_handoffs(n, bound)
    assert direct + queue_ops == n
    expected_queue_ops = n // (bound + 1)  # one forced YIELD every (bound+1) handoffs
    assert abs(queue_ops - expected_queue_ops) <= 1, (
        f"queue op count {queue_ops} does not match the consecutive-handoff bound "
        f"(expected ~{expected_queue_ops}); the bypass is not behaving as documented"
    )
    avoided_fraction = direct / n
    elapsed = time_direct_switch_decision(n)
    per_call_ns = (elapsed / n) * 1e9
    print(f"[MEASURED] {n} handoff decisions (consecutive_handoffs bound={bound}):")
    print(
        f"           {direct} DIRECT_SWITCH (queue bypassed), {queue_ops} queued "
        f"({avoided_fraction:.1%} of handoffs skip the READY-queue append/pop pair)"
    )
    print(
        f"           {per_call_ns:.1f} ns/decision (Python interpreter overhead, "
        f"not representative of Cortex-M cycles -- see {{DirectContextSwitch}})"
    )
    assert avoided_fraction > 0.5, "most handoffs should bypass the queue, or the claim is false"
    print(
        "[PASS] DIRECT_SWITCH measurably bypasses the READY-queue for the documented majority of handoffs."
    )


if __name__ == "__main__":
    main()

"""Compare a fixed folded-XOR cache hit in C++ and the Python reference path."""

from __future__ import annotations

import sys
from pathlib import Path
from statistics import median
from time import perf_counter

_PYSIM_DIR = Path(__file__).resolve()
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from config import JIT_CACHE_FAST_SLOT_COUNT
from system_containers import StaticVector
from tier3_executer.jit import _jit_cache_native
from tier3_executer.jit.jit_cache import JITTrace

TEST_PC = 0x10020
ITERATIONS = 1_000_000
ROUNDS = 5


def _folded_slot(pc: int) -> int:
    """Return the configured three-stage folding-XOR slot for one PC."""

    folded = pc ^ (pc >> 16)
    folded ^= folded >> 8
    folded ^= folded >> 4
    return folded & (JIT_CACHE_FAST_SLOT_COUNT - 1)


def main() -> None:
    assert _jit_cache_native.FAST_SLOT_COUNT == JIT_CACHE_FAST_SLOT_COUNT
    trace = JITTrace(head_pc=TEST_PC)

    native_cache = _jit_cache_native.FastCache()
    native_cache.store(TEST_PC, trace)

    python_slots: StaticVector[tuple[int, JITTrace] | None] = StaticVector(
        capacity=JIT_CACHE_FAST_SLOT_COUNT
    )
    for _ in range(JIT_CACHE_FAST_SLOT_COUNT):
        python_slots.append(None)
    python_slots[_folded_slot(TEST_PC)] = (TEST_PC, trace)

    def native_lookup() -> JITTrace | None:
        return native_cache.lookup(TEST_PC)

    def python_lookup() -> JITTrace | None:
        slot = _folded_slot(TEST_PC)
        entry = python_slots[slot]
        if entry is None or entry[0] != TEST_PC:
            return None
        return entry[1]

    native_samples: StaticVector[float] = StaticVector(capacity=ROUNDS)
    python_samples: StaticVector[float] = StaticVector(capacity=ROUNDS)
    for _ in range(ROUNDS):
        started = perf_counter()
        for _ in range(ITERATIONS):
            native_result = native_lookup()
        native_samples.append((perf_counter() - started) * 1e9 / ITERATIONS)
        assert native_result is trace

        started = perf_counter()
        for _ in range(ITERATIONS):
            python_result = python_lookup()
        python_samples.append((perf_counter() - started) * 1e9 / ITERATIONS)
        assert python_result is trace

    native_median = median(native_samples)
    python_median = median(python_samples)
    print(f"iterations_per_round={ITERATIONS}")
    print(f"rounds={ROUNDS}")
    print(f"native_fastcache_hit_ns_median={native_median:.1f}")
    print(f"python_staticvector_hit_ns_median={python_median:.1f}")
    print(f"native_over_python_ratio={native_median / python_median:.3f}")
    print("[PASS] both paths returned the same trace object")


if __name__ == "__main__":
    main()

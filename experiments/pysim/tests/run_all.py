"""
Unit Test Suite Runner for pysim.
Executes all unit tests in strict architectural tier order:
1. Tier 1 Core & Interface (foundational kernel, containers, logging, IPC)
2. Tier 2 Runtime (loader, interpreter, syscall, vMMIO, vSoC, debugger)
3. Tier 3 Platform & JIT (memory MPU, HAL, x64 asm/stencils, JIT compiler/runtime)
4. Cross-Cutting Verification (pairwise combinations, gotchas & invariants)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
PYSIM_ROOT = TEST_DIR.parent
REPO_ROOT = PYSIM_ROOT.parent.parent

for p in [
    PYSIM_ROOT,
    PYSIM_ROOT / "core",
    PYSIM_ROOT / "runtime",
    PYSIM_ROOT / "jit",
    PYSIM_ROOT / "platforms",
    REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

# Ordered test suites reflecting the architecture dependency layers
TEST_SUITES = [
    # --- Tier 1: Core ---
    ("Tier 1 Core", "COOS Rendezvous & Handoff", TEST_DIR / "tier1_core" / "test_coos.py"),
    ("Tier 1 Core", "Round-Robin Scheduler", TEST_DIR / "tier1_core" / "test_scheduler.py"),
    ("Tier 1 Core", "System Containers & Views", TEST_DIR / "tier1_core" / "test_containers.py"),
    ("Tier 1 Core", "System Logging & Ring Buffer", TEST_DIR / "tier1_core" / "test_logging.py"),
    # --- Tier 1: Interface ---
    (
        "Tier 1 Interface",
        "IPC Router & Shared Memory",
        TEST_DIR / "tier1_interface" / "test_ipc_router.py",
    ),
    # --- Tier 2: Runtime ---
    ("Tier 2 Runtime", "WASM Loader & Segments", TEST_DIR / "tier2_runtime" / "test_loader.py"),
    (
        "Tier 2 Runtime",
        "WASM Interpreter & Instructions",
        TEST_DIR / "tier2_runtime" / "test_interpreter.py",
    ),
    (
        "Tier 2 Runtime",
        "WASM Differential Oracle (wasmtime)",
        TEST_DIR / "tier2_runtime" / "test_wasm_differential.py",
    ),
    (
        "Tier 2 Runtime",
        "Syscall & WASI Environment",
        TEST_DIR / "tier2_runtime" / "test_syscall.py",
    ),
    ("Tier 2 Runtime", "Virtual MMIO Controller", TEST_DIR / "tier2_runtime" / "test_vmmio.py"),
    (
        "Tier 2 Runtime",
        "Fault Recovery Strategies",
        TEST_DIR / "tier2_runtime" / "test_recovery.py",
    ),
    ("Tier 2 Runtime", "vSoC Multitasking & Pipeline", TEST_DIR / "tier2_runtime" / "test_vsoc.py"),
    ("Tier 2 Runtime", "Debug Manager Core", TEST_DIR / "tier2_runtime" / "test_debugger.py"),
    ("Tier 2 Runtime", "GDB RSP Remote Session", TEST_DIR / "tier2_runtime" / "test_gdb_remote.py"),
    # --- Tier 3: Platform ---
    (
        "Tier 3 Platform",
        "Physical Memory & MPU W^X",
        TEST_DIR / "tier3_platform" / "test_memory.py",
    ),
    ("Tier 3 Platform", "HAL Drivers & ShmPool", TEST_DIR / "tier3_platform" / "test_hal.py"),
    # --- Tier 3: JIT ---
    ("Tier 3 JIT", "x64 Assembler", TEST_DIR / "tier3_jit" / "test_x64_asm.py"),
    ("Tier 3 JIT", "x64 Stencils Catalog", TEST_DIR / "tier3_jit" / "test_x64_stencils.py"),
    (
        "Tier 3 JIT",
        "JIT Hotspot Profiler & 3-Bank Cache",
        TEST_DIR / "tier3_jit" / "test_jit_runtime.py",
    ),
    ("Tier 3 JIT", "x64 Copy-and-Patch JIT", TEST_DIR / "tier3_jit" / "test_x64_jit.py"),
    # --- Cross-Cutting ---
    (
        "Cross-Cutting",
        "All-Pairs Combinatorial Matrix",
        TEST_DIR / "cross_cutting" / "test_pairwise_combinations.py",
    ),
    (
        "Cross-Cutting",
        "Implementation Gotchas & Invariants",
        TEST_DIR / "cross_cutting" / "test_gotchas.py",
    ),
]


def run_all_tests():
    print("=" * 84)
    print("           Fireball pysim Architectural Unit Test Suite (Tier 1 -> 3)            ")
    print("=" * 84)
    total_start = time.perf_counter()
    passed = 0
    failed = 0
    current_tier = None

    for tier, name, script_path in TEST_SUITES:
        if tier != current_tier:
            current_tier = tier
            print(f"\n[{current_tier.upper()}]")
            print("-" * 84)

        t0 = time.perf_counter()
        res = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000

        if res.returncode == 0:
            status = "[PASS]"
            print(f"  {status} {name:<42} ({script_path.name:<24}) {elapsed_ms:>8.2f} ms")
            passed += 1
        else:
            status = "[FAIL]"
            print(f"  {status} {name:<42} ({script_path.name:<24}) {elapsed_ms:>8.2f} ms")
            print("--- STDOUT ---")
            print(res.stdout)
            print("--- STDERR ---")
            print(res.stderr)
            print("-" * 84)
            failed += 1

    total_elapsed_ms = (time.perf_counter() - total_start) * 1000
    print("\n" + "=" * 84)
    print(
        f" Unit Test Summary: {passed}/{len(TEST_SUITES)} Passed, {failed} Failed ({total_elapsed_ms:.2f} ms total)"
    )
    print("=" * 84)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()

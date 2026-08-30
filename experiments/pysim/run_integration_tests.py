"""
Fireball Full Component Integration Test Suite Runner.

Executes all 6 integration test scenarios end-to-end against genuine WASM bytecode:
- Scenario 1: Tier 1 Core + Tier 2 Loader & Linear Memory (Data Segments, memory.grow, Radix lookup)
- Scenario 2: Tier 2 Runtime + System Call & WASI IO (scatter-gather fd_write, proc_exit)
- Scenario 3: Tier 2 Interpreter + Recursion & Indirect Table Dispatch (call_indirect, br_table)
- Scenario 4: Tier 2 Runtime + Tier 3 JIT Hybrid Compilation (Card Marking, idle_hook JIT, Differential check)
- Scenario 5: Multiple Functions, UnifiedPC & bswap32 Radix Tree (Cross-function JIT tracking)
- Scenario 6: COOS Cooperative Multitasking & Coroutine Interleaving (Fuel yield/resume, shared memory)
"""

import sys
import time
import subprocess

SCENARIOS = [
    ("Scenario 1: Loader, Memory & Data Segments", "experiments/pysim/scenario1_loader_and_memory.py"),
    ("Scenario 2: WASI System Call & I/O Dispatch", "experiments/pysim/scenario2_wasi_syscall_io.py"),
    ("Scenario 3: Recursion & Indirect Table Dispatch", "experiments/pysim/scenario3_recursion_and_tables.py"),
    ("Scenario 4: Hybrid JIT Compilation & Hotspot", "experiments/pysim/scenario4_hybrid_jit_loop.py"),
    ("Scenario 5: Multi-Function UnifiedPC & Radix", "experiments/pysim/scenario5_multimodule_unified_pc.py"),
    ("Scenario 6: COOS Cooperative Multitasking", "experiments/pysim/scenario6_coos_multitask_yield.py"),
    ("Scenario 7: GDB Remote Debugger Socket Session", "experiments/pysim/scenario7_gdb_socket_debugger.py"),
]


def run_all_scenarios():
    print("================================================================================")
    print("      Fireball End-to-End Component Integration Test Suite                      ")
    print("================================================================================")

    total_start = time.perf_counter()
    passed = 0
    failed = 0

    for name, script in SCENARIOS:
        print(f"\n>>> Running {name} ({script})...")
        t0 = time.perf_counter()
        res = subprocess.run([sys.executable, script], capture_output=True, text=True)
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000

        if res.returncode == 0:
            print(res.stdout.strip())
            print(f"    -> [SUCCESS] {name} passed in {elapsed_ms:.2f} ms")
            passed += 1
        else:
            print(f"    -> [FAILURE] {name} failed (exit code {res.returncode}):")
            print(res.stdout)
            print(res.stderr)
            failed += 1

    total_elapsed_ms = (time.perf_counter() - total_start) * 1000
    print("\n================================================================================")
    print(f" Integration Test Summary: {passed}/{len(SCENARIOS)} Passed, {failed} Failed ({total_elapsed_ms:.2f} ms total)")
    print("================================================================================")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_scenarios()

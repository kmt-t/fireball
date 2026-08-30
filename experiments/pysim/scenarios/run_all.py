"""
Fireball Full Component Integration Test Suite Runner.
Executes all 11 integration test scenarios end-to-end against genuine WASM bytecode.
"""

import subprocess
import sys
import time
from pathlib import Path

SCENARIO_DIR = Path(__file__).resolve().parent
PYSIM_ROOT = SCENARIO_DIR.parent
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

SCENARIOS = [
    (
        "Scenario 1: Loader, Memory & Data Segments",
        SCENARIO_DIR / "scenario1_loader_and_memory.py",
    ),
    (
        "Scenario 2: WASI System Call & I/O Dispatch",
        SCENARIO_DIR / "scenario2_wasi_syscall_io.py",
    ),
    (
        "Scenario 3: Recursion & Indirect Table Dispatch",
        SCENARIO_DIR / "scenario3_recursion_and_tables.py",
    ),
    (
        "Scenario 4: Hybrid JIT Compilation & Hotspot",
        SCENARIO_DIR / "scenario4_hybrid_jit_loop.py",
    ),
    (
        "Scenario 5: Multi-Function UnifiedPC & Radix",
        SCENARIO_DIR / "scenario5_multimodule_unified_pc.py",
    ),
    (
        "Scenario 6: COOS Cooperative Multitasking",
        SCENARIO_DIR / "scenario6_coos_multitask_yield.py",
    ),
    (
        "Scenario 7: GDB Remote Debugger Socket Session",
        SCENARIO_DIR / "scenario7_gdb_socket_debugger.py",
    ),
    (
        "Scenario 8: Storage Coverage & GDB Debugger",
        SCENARIO_DIR / "scenario8_comprehensive_storage_coverage.py",
    ),
    (
        "Scenario 9: IPC Router & Structured Logging",
        SCENARIO_DIR / "scenario9_ipc_router_and_logging.py",
    ),
    (
        "Scenario 10: vMMIO Virtual Devices & Translation",
        SCENARIO_DIR / "scenario10_vmmio_virtual_devices.py",
    ),
    (
        "Scenario 11: HAL & WASI Dummy Drivers",
        SCENARIO_DIR / "scenario11_hal_and_wasi_drivers.py",
    ),
]


def run_all_scenarios():
    print("=" * 80)
    print(
        "      Fireball End-to-End Component Integration Test Suite                      "
    )
    print("=" * 80)
    total_start = time.perf_counter()
    passed = 0
    failed = 0
    for name, script_path in SCENARIOS:
        print(f"\n>>> Running {name} ({script_path.name})...")
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
            print(res.stdout.strip())
            print(f"    -> [SUCCESS] {name} passed in {elapsed_ms:.2f} ms")
            passed += 1
        else:
            print(f"    -> [FAILURE] {name} failed (exit code {res.returncode}):")
            print(res.stdout)
            print(res.stderr)
            failed += 1

    total_elapsed_ms = (time.perf_counter() - total_start) * 1000
    print("\n" + "=" * 80)
    print(
        f" Integration Test Summary: {passed}/{len(SCENARIOS)} Passed, {failed} Failed ({total_elapsed_ms:.2f} ms total)"
    )
    print("=" * 80)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_scenarios()

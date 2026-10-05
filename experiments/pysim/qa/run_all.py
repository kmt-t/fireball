"""
Component unit-test runner for pysim.
Executes component-focused tests in strict architectural tier order:
1. Tier 1 Core & Interface (foundational kernel, containers, logging, IPC)
2. Tier 2 Runtime (loader, syscall, vMMIO, vSoC, runtime composition)
3. Tier 2 Interpreter, Tier 3 Plugins (JIT, debugger, profiler)
4. Component-local regressions and verification helpers

Cross-component pairwise tests and compiled WASM guests have separate integration
and workload runners. They are intentionally excluded from this unit-test list.
"""

from __future__ import annotations

import ast
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
PYSIM_ROOT = TEST_DIR.parent
REPO_ROOT = PYSIM_ROOT.parent.parent
if str(PYSIM_ROOT) not in sys.path:
    sys.path.insert(0, str(PYSIM_ROOT))

from qa.shared.execution import run_python

# Ordered test suites reflecting the architecture dependency layers
TEST_SUITES = [
    # --- Tier 1: Core ---
    ("Tier 1 Core", "COOS Rendezvous & Handoff", TEST_DIR / "tier1_core" / "test_coos.py"),
    ("Tier 1 Core", "Round-Robin Scheduler", TEST_DIR / "tier1_core" / "test_scheduler.py"),
    ("Tier 1 Core", "System Containers & Views", TEST_DIR / "tier1_core" / "test_containers.py"),
    # --- Tier 1: Interface ---
    (
        "Tier 1 Interface",
        "IPC Router & Shared Memory",
        TEST_DIR / "tier1_interface" / "test_ipc_router.py",
    ),
    # --- Tier 2: Runtime ---
    (
        "Tier 2 Runtime",
        "System Logging & Ring Buffer",
        TEST_DIR / "tier2_runtime" / "test_logging.py",
    ),
    (
        "Tier 2 Runtime",
        "WASM Reference Parser & Segments",
        TEST_DIR / "tier2_runtime" / "test_wasm_reader.py",
    ),
    (
        "Tier 3 Plugins",
        "JIT Candidate Scoring",
        TEST_DIR / "tier3_plugins" / "jit" / "test_jit_scoring.py",
    ),
    (
        "Tier 2 Runtime",
        "WASM Interpreter & Instructions",
        TEST_DIR / "tier2_runtime" / "interpreter" / "test_interpreter.py",
    ),
    (
        "Tier 2 Runtime",
        "CPS Interpreter Python/Native Compatibility",
        TEST_DIR / "tier2_runtime" / "interpreter" / "test_cps_interpreter.py",
    ),
    (
        "Tier 2 Runtime",
        "Python/C++ ABI Native Layout",
        TEST_DIR / "tier2_runtime" / "test_interpreter_abi.py",
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
    ("Tier 2 Runtime", "vDMA Transfer Contracts", TEST_DIR / "tier2_runtime" / "test_vdma.py"),
    (
        "Tier 2 Runtime",
        "Runtime Static Composition",
        TEST_DIR / "tier2_runtime" / "test_runtime_composer.py",
    ),
    # --- Tier 3 Plugins ---
    (
        "Tier 3 Plugins",
        "Debug Manager Core",
        TEST_DIR / "tier3_plugins" / "debugger" / "test_debugger.py",
    ),
    (
        "Tier 3 Plugins",
        "GDB RSP Remote Session",
        TEST_DIR / "tier3_plugins" / "debugger" / "test_gdb_remote.py",
    ),
    (
        "Tier 3 Plugins",
        "Guest Profiler",
        TEST_DIR / "tier3_plugins" / "profiler" / "test_guest_profiler.py",
    ),
    (
        "Tier 3 Plugins",
        "Runtime Event Logger",
        TEST_DIR / "tier3_plugins" / "logger" / "test_runtime_event_logger.py",
    ),
    # --- Tier 3: Platform ---
    (
        "Tier 3 Platform",
        "Physical Memory Software Contract",
        TEST_DIR / "tier3_platform" / "test_memory.py",
    ),
    ("Tier 3 Platform", "HAL Drivers & ShmPool", TEST_DIR / "tier3_platform" / "test_hal.py"),
    (
        "Tier 3 Platform",
        "libfireball Host-Call Contract",
        TEST_DIR / "tier3_platform" / "test_libfireball.py",
    ),
    # --- Tier 3: Executer ---
    (
        "Tier 3 Plugins",
        "Executable Memory W^X",
        TEST_DIR / "tier3_plugins" / "jit" / "test_exec_memory.py",
    ),
    (
        "Tier 3 Plugins",
        "JIT Extension Hotspot History & 3-Bank Cache",
        TEST_DIR / "tier3_plugins" / "jit" / "test_jit_runtime.py",
    ),
    (
        "Tier 3 Plugins",
        "x64 Copy-and-Patch JIT",
        TEST_DIR / "tier3_plugins" / "jit" / "test_x64_jit.py",
    ),
    (
        "Tier 3 Plugins",
        "JIT Differential (wasmtime / Tier 2 / Tier 3)",
        TEST_DIR / "tier3_plugins" / "jit" / "test_jit_differential.py",
    ),
    # --- Cross-Cutting ---
    (
        "Cross-Cutting",
        "Implementation Gotchas & Invariants",
        TEST_DIR / "cross_cutting" / "test_gotchas.py",
    ),
    (
        "Cross-Cutting",
        "Entry Point (main.py)",
        TEST_DIR / "cross_cutting" / "test_entrypoint.py",
    ),
]


def assert_registered_test_modules(test_dir: Path, registered: Sequence[Path]) -> None:
    """検証マトリクスの実行入口から、実在する試験モジュールを取りこぼさない。"""
    discovered: set[Path] = set()
    for path in test_dir.rglob("test_*.py"):
        if path.is_relative_to(test_dir / "integration") or path.is_relative_to(
            test_dir / "workloads"
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        has_tests = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
        has_test_case = any(
            isinstance(node, ast.ClassDef) and node.name.startswith("Test") for node in tree.body
        )
        if has_tests or has_test_case:
            discovered.add(path.resolve())
    registered_paths = {path.resolve() for path in registered}
    assert len(registered_paths) == len(registered), "duplicate test suite registration"
    missing = discovered - registered_paths
    stale = registered_paths - discovered
    assert not missing, f"unregistered test suites: {sorted(str(path) for path in missing)}"
    assert not stale, f"missing or empty test suites: {sorted(str(path) for path in stale)}"


def run_all_tests():
    assert_registered_test_modules(TEST_DIR, tuple(path for _, _, path in TEST_SUITES))
    print("=" * 84)
    print(f"Python optimization level (parent and children): {sys.flags.optimize}")
    print("             Fireball pysim Component Unit Test Suite (Tier 1 -> 3)              ")
    print("=" * 84)
    total_start = time.perf_counter()
    passed = 0
    failed = 0
    current_tier = None
    test_env = os.environ.copy()
    python_paths = [str(REPO_ROOT), str(PYSIM_ROOT)]
    existing_python_path = test_env.get("PYTHONPATH")
    if existing_python_path:
        python_paths.append(existing_python_path)
    test_env["PYTHONPATH"] = os.pathsep.join(python_paths)

    for tier, name, script_path in TEST_SUITES:
        if tier != current_tier:
            current_tier = tier
            print(f"\n[{current_tier.upper()}]")
            print("-" * 84)

        t0 = time.perf_counter()
        res = run_python(
            ["-m", "pytest", "-q", str(script_path), "-s"],
            capture_output=True,
            cwd=REPO_ROOT,
            environment=test_env,
        )
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000

        if res.returncode == 0:
            status = "[PASS]"
            print(f"  {status} {name:<42} ({script_path.name:<24}) {elapsed_ms:>8.2f} ms")
            # 成功終了でもskip/xfailを隠さず、pytestの集計を表示する。
            output_lines = res.stdout.strip().splitlines()
            if output_lines:
                print(f"         {output_lines[-1]}")
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

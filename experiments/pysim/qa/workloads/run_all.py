"""Run compiled WASM workloads against Fireball's reference runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

WORKLOAD_DIR = Path(__file__).resolve().parent
PYSIM_ROOT = WORKLOAD_DIR.parent.parent
REPO_ROOT = PYSIM_ROOT.parent.parent
if str(PYSIM_ROOT) not in sys.path:
    sys.path.insert(0, str(PYSIM_ROOT))

from qa.run_all import assert_registered_test_modules

WORKLOAD_SUITES = (
    WORKLOAD_DIR / "test_wasi_guest.py",
    WORKLOAD_DIR / "test_driver_stubs_guest.py",
    WORKLOAD_DIR / "test_wasm_core_spec.py",
)


def run_all_workloads() -> int:
    assert_registered_test_modules(WORKLOAD_DIR, WORKLOAD_SUITES)
    environment = os.environ.copy()
    python_paths = [str(REPO_ROOT), str(PYSIM_ROOT)]
    existing_python_path = environment.get("PYTHONPATH")
    if existing_python_path:
        python_paths.append(existing_python_path)
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)

    passed = 0
    started = time.perf_counter()
    for suite in WORKLOAD_SUITES:
        print(f"\n>>> Running WASM workload suite: {suite.name}", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(suite), "-s"],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
        )
        passed += result.returncode == 0
    failures = len(WORKLOAD_SUITES) - passed
    elapsed = time.perf_counter() - started
    print(
        f"WASM workload suites: {passed}/{len(WORKLOAD_SUITES)} passed "
        f"({failures} failed, {elapsed:.2f}s)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_all_workloads())

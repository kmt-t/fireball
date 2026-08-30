"""
Unit Test Suite Runner for pysim.
Executes all unit tests under experiments/pysim/tests/.
"""

import sys
import time
import subprocess
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

TEST_FILES = [
    ("Instruction Set Coverage", TEST_DIR / "test_instructions.py"),
    ("WASM Loader & Segments", TEST_DIR / "test_loader.py"),
    ("Host Call & Syscalls", TEST_DIR / "test_host_call.py"),
    ("Debugger Core", TEST_DIR / "test_debugger.py"),
    ("GDB RSP Remote Session", TEST_DIR / "test_gdb_remote_connection.py"),
    ("x64 Assembler", TEST_DIR / "test_x64_asm.py"),
    ("x64 Stencils Catalog", TEST_DIR / "test_x64_stencils.py"),
    ("x64 Copy-and-Patch JIT", TEST_DIR / "test_x64_jit.py"),
    ("All-Pairs Combinatorial Matrix", TEST_DIR / "test_pairwise_combinations.py"),
]


def run_all_tests():
    print("=" * 80)
    print(
        "      Fireball pysim Unit Test Suite                                            "
    )
    print("=" * 80)
    total_start = time.perf_counter()
    passed = 0
    failed = 0
    for name, script_path in TEST_FILES:
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
        f" Unit Test Summary: {passed}/{len(TEST_FILES)} Passed, {failed} Failed ({total_elapsed_ms:.2f} ms total)"
    )
    print("=" * 80)
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()

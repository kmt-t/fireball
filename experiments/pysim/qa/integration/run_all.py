"""Run component integration suites and the 12 system integration scenarios."""

from __future__ import annotations

import os
import sys
from pathlib import Path

INTEGRATION_DIR = Path(__file__).resolve().parent
PYSIM_ROOT = INTEGRATION_DIR.parent.parent
REPO_ROOT = PYSIM_ROOT.parent.parent
if str(PYSIM_ROOT) not in sys.path:
    sys.path.insert(0, str(PYSIM_ROOT))

from qa.run_all import assert_registered_test_modules
from qa.shared.execution import run_python

INTEGRATION_SUITES = (
    INTEGRATION_DIR / "test_pairwise_combinations.py",
    INTEGRATION_DIR / "test_guest_profiler_runtime.py",
    INTEGRATION_DIR / "test_driver_stubs.py",
    INTEGRATION_DIR / "test_lldb_debugger.py",
)
SCENARIO_RUNNER = INTEGRATION_DIR.parent / "scenarios" / "run_all.py"


def _test_environment() -> dict[str, str]:
    environment = os.environ.copy()
    python_paths = [str(REPO_ROOT), str(PYSIM_ROOT)]
    existing_python_path = environment.get("PYTHONPATH")
    if existing_python_path:
        python_paths.append(existing_python_path)
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    return environment


def run_all_integrations() -> int:
    assert_registered_test_modules(INTEGRATION_DIR, INTEGRATION_SUITES)
    commands = [["-m", "pytest", "-q", str(path), "-s"] for path in INTEGRATION_SUITES]
    commands.append([str(SCENARIO_RUNNER)])
    failures = 0
    for command in commands:
        result = run_python(
            command,
            cwd=REPO_ROOT,
            environment=_test_environment(),
        )
        failures += result.returncode != 0
    print(f"Integration test groups: {len(commands) - failures}/{len(commands)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_all_integrations())

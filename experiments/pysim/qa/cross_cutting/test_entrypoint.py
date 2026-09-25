"""
experiments/pysim/qa/cross_cutting/test_entrypoint.py
Runs the `main.py` entry point end to end so that it cannot rot unnoticed.
Traceability: docs/qa/integration_test_scenarios.md (entry point smoke).
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent



def test_entrypoint_01_main_runs_every_demo_and_finds_no_violation():
    """TEST-ENTRY-01: main.py imports, runs all guest tasks, and reports no behavioral bug."""
    import main

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        main.main()
    report = captured.getvalue()

    for expected in (
        "[structured-logger] log_event -> QUEUED",
        "[console-writer] wrote",
        "out-of-bounds slice correctly refused",
        "[hostile-neighbor] cross-task view correctly refused",
        "succeeded after 3 attempt(s)",
        "strategy=PANIC",
        "fact(6) = 720",
        "No behavioral bugs found",
    ):
        assert expected in report, f"main.py output lacks: {expected}"
    assert len(main.findings) == 0, list(main.findings)
    # The guest's raw stdout bytes and the structured log line both reached the transport.
    assert "guest computed pi ~= 3.141593 at runtime" in report
    assert "task booted (free=21504 bytes" in report


if __name__ == "__main__":
    test_entrypoint_01_main_runs_every_demo_and_finds_no_violation()
    print("[PASS] All 1 entry point tests passed.")

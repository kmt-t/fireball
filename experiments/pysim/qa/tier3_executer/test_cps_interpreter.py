from __future__ import annotations

"""Cross-backend tests for the optional native CPS handler probe."""

import sys
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[2]

for _path in (
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_executer",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tier3_executer.interpreter import _HANDLERS
from tier3_executer.interpreter_cps import BACKEND, HANDLER_COUNT, NATIVE_AVAILABLE


def test_cps_entry_is_python_compatible() -> None:
    assert BACKEND in ("python", "native")
    assert NATIVE_AVAILABLE == (BACKEND == "native")


def test_native_cps_chain_covers_interpreter_handler_table() -> None:
    assert HANDLER_COUNT == sum(1 for handler in _HANDLERS if handler is not None)


ALL_TESTS = sorted(
    (value for name, value in globals().items() if name.startswith("test_") and callable(value)),
    key=lambda function: function.__code__.co_firstlineno,
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"[PASS] All {len(ALL_TESTS)} CPS backend tests passed ({BACKEND}).")

"""
experiments/pysim/qa/cross_cutting/test_entrypoint.py
Runs the `main.py` entry point end to end so that it cannot rot unnoticed.
Traceability: docs/qa/integration_test_scenarios.md (entry point smoke).
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest
from qa.run_all import TEST_DIR, TEST_SUITES, assert_registered_test_modules

from tools.check_verification_matrix import _test_spec_paths

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent


def test_entrypoint_02_every_test_module_is_registered() -> None:
    """TEST-ENTRY-02: verification_factor_matrixの実行入口は全試験モジュールを登録する。"""
    assert_registered_test_modules(TEST_DIR, tuple(path for _, _, path in TEST_SUITES))


def test_entrypoint_03_unregistered_test_module_fails_gate(tmp_path: Path) -> None:
    """TEST-ENTRY-03: 未登録の試験を合格扱いせず、補助モジュールは試験と数えない。"""
    suite = tmp_path / "test_contract.py"
    suite.write_text("def test_behavior():\n    assert 1 == 1\n", encoding="utf-8")
    (tmp_path / "test_support.py").write_text(
        "def make_fixture():\n    return 1\n", encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="unregistered test suites"):
        assert_registered_test_modules(tmp_path, ())
    assert_registered_test_modules(tmp_path, (suite,))


def test_entrypoint_04_duplicate_or_stale_registration_fails_gate(tmp_path: Path) -> None:
    """TEST-ENTRY-04: 重複と、削除済み・試験ゼロの登録を検出する。"""
    suite = tmp_path / "test_contract.py"
    suite.write_text("def test_behavior():\n    assert 1 == 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="duplicate"):
        assert_registered_test_modules(tmp_path, (suite, suite))
    empty = tmp_path / "test_empty.py"
    empty.write_text("def make_fixture():\n    return 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="missing or empty"):
        assert_registered_test_modules(tmp_path, (suite, empty))


def test_entrypoint_05_matrix_counts_tier_and_wasm_test_specs(tmp_path: Path) -> None:
    """TEST-ENTRY-05: QA FORMATのTier別と横断Wasm仕様を同じ分母へ含める。"""
    tier = tmp_path / "tier1_core"
    cross_cutting = tmp_path / "specs"
    tier.mkdir()
    cross_cutting.mkdir()
    (tier / "containers_test_spec.md").write_text("# Contract\n", encoding="utf-8")
    (cross_cutting / "wasm_test_spec.md").write_text("# Contract\n", encoding="utf-8")
    (tmp_path / "FORMAT.md").write_text("# Format\n", encoding="utf-8")
    (cross_cutting / "factors.csv").write_text("factor,level\n", encoding="utf-8")
    assert tuple(path.relative_to(tmp_path).as_posix() for path in _test_spec_paths(tmp_path)) == (
        "specs/wasm_test_spec.md",
        "tier1_core/containers_test_spec.md",
    )


def test_entrypoint_01_main_runs_every_demo_and_finds_no_violation():
    """TEST-ENTRY-01: main.pyの実行と読者向け出力を検査する。機能適合は各契約テストが担う。"""
    import main

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        main.main()
    report = captured.getvalue()

    for expected in (
        "[structured-logger] log_event -> SUCCESS",
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
    raise SystemExit(pytest.main([__file__]))

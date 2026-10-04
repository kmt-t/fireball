"""
experiments/pysim/qa/cross_cutting/test_entrypoint.py
Runs the `main.py` entry point end to end so that it cannot rot unnoticed.
Traceability: docs/qa/integration_test_scenarios.md (entry point smoke).
"""

from __future__ import annotations

import ast
import configparser
import contextlib
import io
import tomllib
from pathlib import Path

import pytest
from qa.integration.run_all import INTEGRATION_DIR, INTEGRATION_SUITES
from qa.run_all import TEST_DIR, TEST_SUITES, assert_registered_test_modules
from qa.workloads.run_all import WORKLOAD_DIR, WORKLOAD_SUITES

from tools.check_verification_matrix import _test_spec_paths

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_PRODUCT_MODULE_DIRS = (
    "tier1_core",
    "tier1_interface",
    "tier2_runtime",
    "tier3_plugins",
    "tier3_plugins",
    "tier3_platform",
)
_FORBIDDEN_PRODUCT_IMPORT_ROOTS = frozenset(
    ("qa", "pytest", "pytest_cov", "hypothesis", "wasmtime", "unittest", "mock")
)


def _test_only_imports(tree: ast.Module) -> tuple[tuple[int, str], ...]:
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = (node.module,)
        else:
            continue
        for module in modules:
            parts = module.split(".")
            if parts[0] in _FORBIDDEN_PRODUCT_IMPORT_ROOTS or "qa" in parts:
                violations.append((node.lineno, module))
    return tuple(violations)


def test_entrypoint_06_product_modules_do_not_import_qa_support() -> None:
    """TEST-ENTRY-06: QA helpers and frameworks stay outside product import graphs."""
    synthetic_tree = ast.parse("from qa.shared.helpers import make_interpreter\nimport pytest\n")
    assert _test_only_imports(synthetic_tree) == (
        (1, "qa.shared.helpers"),
        (2, "pytest"),
    )

    sources = [
        path
        for directory in _PRODUCT_MODULE_DIRS
        for path in (_PYSIM_DIR / directory).rglob("*.py")
    ]
    sources.extend(
        (
            _PYSIM_DIR / "__init__.py",
            _PYSIM_DIR / "main.py",
            _PYSIM_DIR / "system.py",
        )
    )
    violations: list[str] = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in _test_only_imports(tree):
            violations.append(f"{path.relative_to(_PYSIM_DIR)}:{line}: {module}")
    assert not violations, "product code imports test-only support:\n" + "\n".join(violations)


def test_entrypoint_07_test_dependencies_are_dev_only() -> None:
    """TEST-ENTRY-07: Python runtime metadata does not include QA dependencies."""
    config_path = _PYSIM_DIR.parent.parent / "pyproject.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "dependencies" not in config["project"]
    groups = config["dependency-groups"]
    shared_dependencies = {item.split(">=", maxsplit=1)[0] for item in groups["qa-shared"]}
    private_dependencies = {item.split(">=", maxsplit=1)[0] for item in groups["qa-private"]}
    assert shared_dependencies == {"pytest", "pytest-cov", "hypothesis"}
    assert private_dependencies == {"wasmtime"}


def test_entrypoint_08_qa_is_not_a_top_level_pytest_import_root() -> None:
    """TEST-ENTRY-08: Tests import support through its QA package namespace."""
    parser = configparser.ConfigParser()
    parser.read(_PYSIM_DIR.parent.parent / "pytest.ini", encoding="utf-8")
    python_paths = {entry.strip() for entry in parser["pytest"]["pythonpath"].splitlines()}
    assert "experiments/pysim/qa" not in python_paths


def test_entrypoint_02_every_test_module_is_registered() -> None:
    """TEST-ENTRY-02: 単体・結合・WASM workloadの入口が各試験を登録する。"""
    assert_registered_test_modules(TEST_DIR, tuple(path for _, _, path in TEST_SUITES))
    assert_registered_test_modules(INTEGRATION_DIR, INTEGRATION_SUITES)
    assert_registered_test_modules(WORKLOAD_DIR, WORKLOAD_SUITES)


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

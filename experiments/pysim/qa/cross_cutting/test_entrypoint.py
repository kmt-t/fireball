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
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from qa.integration.run_all import INTEGRATION_DIR, INTEGRATION_SUITES
from qa.run_all import TEST_DIR, TEST_SUITES, assert_registered_test_modules
from qa.shared.execution import run_python
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


@pytest.mark.parametrize("optimization", (0, 1, 2))
def test_optimized_valid_operations_preserve_results_and_state(optimization: int) -> None:
    """Observe normal, -O and -OO behavior from an assertion-enabled parent."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(str(Path(path).resolve()) for path in sys.path)
    environment.pop("PYTHONOPTIMIZE", None)
    result = subprocess.run(
        [sys.executable, *(["-O"] * optimization), "-m", "qa.private.optimized_mode_probe"],
        cwd=_PYSIM_DIR.parent.parent,
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed == {
        "optimization": optimization,
        "rendezvous": ["DIRECT_SWITCH", 1, True, 99],
        "local_types": [127],
        "reference_results": [37, 1999000, 1999000, 9.0, 3.75, 33],
        "native_results": [37, 1999000, 1999000, 9.0, 3.75, 33],
        "jit_used": True,
        "pte_registered": True,
        "pc_frequency": 2,
        "stack_size_after_clear": 0,
    }


@pytest.mark.parametrize("optimization", (0, 1, 2))
def test_runner_propagates_optimization_to_its_child(optimization: int) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(str(Path(path).resolve()) for path in sys.path)
    environment.pop("PYTHONOPTIMIZE", None)
    program = (
        "from qa.shared.execution import run_python; from pathlib import Path; "
        "import sys; r=run_python(['-c', 'import sys; print(sys.flags.optimize)'], "
        "cwd=Path.cwd(), capture_output=True); "
        "print(sys.flags.optimize, r.stdout.strip()); sys.exit(r.returncode)"
    )
    result = subprocess.run(
        [sys.executable, *(["-O"] * optimization), "-c", program],
        cwd=_PYSIM_DIR.parent.parent,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{optimization} {optimization}"


def test_runner_timeout_is_a_failure_with_visible_output() -> None:
    result = run_python(
        ["-c", "import time; print('entered', flush=True); time.sleep(10)"],
        cwd=_PYSIM_DIR.parent.parent,
        capture_output=True,
        timeout=0.2,
    )
    assert result.returncode == 124
    assert "entered" in result.stdout
    assert "timed out" in result.stderr


def test_product_type_check_scope_matches_tier_inventory() -> None:
    root = _PYSIM_DIR.parent.parent
    config = yaml.safe_load((root / "spec-integrator.yaml").read_text(encoding="utf-8"))
    imports = config["pysim_imports"]
    tier_files = {
        path.resolve()
        for tier in imports["tiers"]
        for pattern in tier["paths"]
        for path in (root / imports["root"]).glob(pattern)
        if path.is_file()
    }
    pyright = json.loads((root / "pyrightconfig.json").read_text(encoding="utf-8"))
    checked_files = {
        path.resolve()
        for pattern in pyright["include"]
        for path in root.glob(pattern)
        if path.is_file()
    }
    assert checked_files == tier_files


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

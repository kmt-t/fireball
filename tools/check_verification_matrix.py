"""Strict completeness checker for product and PySim verification matrices."""

from __future__ import annotations

import ast
import csv
import re
import sys
from itertools import combinations
from pathlib import Path

import yaml


class MatrixCheckError(Exception):
    """Raised when a required verification matrix invariant is violated."""


def _load_config(config_path: Path) -> dict:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    matrix = data.get("verification_matrix")
    if not isinstance(matrix, dict) or not matrix.get("enabled", False):
        raise MatrixCheckError("verification_matrix.enabled must be true")
    if not matrix.get("strict", False):
        raise MatrixCheckError("verification_matrix.strict must be true")
    for scope in ("product", "pysim"):
        if not isinstance(matrix.get(scope), dict):
            raise MatrixCheckError(f"verification_matrix.{scope} must be configured")
    return matrix


def _relative(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _require_count(label: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise MatrixCheckError(f"{label}: expected {expected}, got {actual}")


def _test_spec_paths(test_spec_root: Path) -> list[Path]:
    """Collect both scopes defined by docs/qa/FORMAT.md."""
    return sorted(
        (*test_spec_root.glob("tier*/*_test_spec.md"), *test_spec_root.glob("specs/*_test_spec.md"))
    )


def _product_inventory(repo_root: Path, matrix: dict) -> dict[str, list[str]]:
    component_root = repo_root / matrix["component_root"]
    inventory = {
        "component_specs": sorted(
            _relative(path, repo_root)
            for path in component_root.glob("tier*/*.md")
            if path.name != "FORMAT.md"
        ),
        "concept_files": sorted(
            _relative(path, repo_root)
            for path in component_root.glob("tier*/concepts/*_concept.py")
        ),
        "formal_models": sorted(
            _relative(path, repo_root)
            for path in component_root.glob("tier*/formal/*_model.py")
        ),
        "test_specs": sorted(
            _relative(path, repo_root)
            for path in _test_spec_paths(repo_root / matrix["test_spec_root"])
        ),
    }
    expected = matrix["expected"]
    for key in inventory:
        _require_count(key, len(inventory[key]), int(expected[key]))
    return inventory


def _unit_suite_paths(repo_root: Path, matrix: dict) -> list[str]:
    qa_root = repo_root / matrix["qa_root"]
    excluded = {"integration", "scenarios", "workloads"}
    return sorted(
        _relative(path, repo_root)
        for path in qa_root.rglob("test_*.py")
        if path.relative_to(qa_root).parts[0] not in excluded
    )


def _pysim_inventory(repo_root: Path, matrix: dict) -> dict[str, list[str]]:
    inventory = {
        "unit_suites": _unit_suite_paths(repo_root, matrix),
        "scenarios": sorted(
            _relative(path, repo_root)
            for path in (repo_root / matrix["scenario_dir"]).glob("scenario[0-9]*_*.py")
        ),
        "integration_suites": sorted(
            _relative(path, repo_root)
            for path in (repo_root / matrix["integration_dir"]).glob("test_*.py")
        ),
        "workload_suites": sorted(
            _relative(path, repo_root)
            for path in (repo_root / matrix["workload_dir"]).glob("test_*.py")
        ),
        "factor_csv": [_relative(repo_root / matrix["factor_csv"], repo_root)],
        "unit_runner": [_relative(repo_root / matrix["unit_runner"], repo_root)],
        "scenario_runner": [_relative(repo_root / matrix["scenario_runner"], repo_root)],
        "integration_runner": [_relative(repo_root / matrix["integration_runner"], repo_root)],
        "workload_runner": [_relative(repo_root / matrix["workload_runner"], repo_root)],
        "pairwise_test": [_relative(repo_root / matrix["pairwise_test"], repo_root)],
    }
    expected = matrix["expected"]
    expected_counts = {
        "unit_suites": "unit_suites",
        "scenarios": "scenarios",
        "integration_suites": "integration_suites",
        "workload_suites": "workload_suites",
    }
    for key, expected_key in expected_counts.items():
        _require_count(key, len(inventory[key]), int(expected[expected_key]))
    return inventory


def _load_factors(repo_root: Path, matrix: dict) -> list[dict[str, object]]:
    factor_path = repo_root / matrix["factor_csv"]
    with factor_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    required_columns = {"factor_id", "factor_name", "level_id", "level"}
    if not rows or set(rows[0]) != required_columns:
        raise MatrixCheckError("factor CSV must define factor_id, factor_name, level_id, level")
    factors: list[dict[str, object]] = []
    factor_ids: set[str] = set()
    current_id = ""
    current_levels: list[str] = []
    current_name = ""
    current_level_ids: set[str] = set()
    for row in rows:
        factor_id = row["factor_id"]
        factor_name = row["factor_name"]
        level_id = row["level_id"]
        level = row["level"]
        if factor_id != current_id:
            if current_id:
                factors.append({"id": current_id, "levels": current_levels})
            if factor_id in factor_ids or not factor_id:
                raise MatrixCheckError(f"duplicate or empty factor ID: {factor_id!r}")
            factor_ids.add(factor_id)
            current_id = factor_id
            current_levels = []
            current_name = factor_name
            current_level_ids = set()
        if (
            not factor_name
            or factor_name != current_name
            or not level_id
            or level_id in current_level_ids
            or not level
            or level in current_levels
        ):
            raise MatrixCheckError(f"invalid or duplicate factor level: {row}")
        current_level_ids.add(level_id)
        current_levels.append(level)
    factors.append({"id": current_id, "levels": current_levels})
    return factors


def _read_assignment(module_path: Path, name: str) -> object:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    raise MatrixCheckError(f"{name} is not a literal assignment in {module_path}")


def _check_pairwise(repo_root: Path, matrix: dict, factors: list[dict[str, object]]) -> None:
    pairwise_path = repo_root / matrix["pairwise_test"]
    cases = _read_assignment(pairwise_path, "PAIRWISE_CASES")
    expected = matrix["expected"]
    if not isinstance(cases, list):
        raise MatrixCheckError("PAIRWISE_CASES must be a list of literal rows")
    _require_count("pairwise_factors", len(factors), int(expected["pairwise_factors"]))
    _require_count("pairwise_cases", len(cases), int(expected["pairwise_cases"]))

    covered: set[tuple[int, int, object, object]] = set()
    for case_number, row in enumerate(cases, start=1):
        if not isinstance(row, tuple) or len(row) != len(factors):
            raise MatrixCheckError("every pairwise row must contain all factors; IDs are generated")
        for index, factor in enumerate(factors):
            if row[index] not in factor["levels"]:
                raise MatrixCheckError(
                    f"TEST-PAIR-{case_number:02d}: {factor['id']} has undeclared level {row[index]!r}"
                )
        for left, right in combinations(range(len(factors)), 2):
            covered.add((left, right, row[left], row[right]))

    required = {
        (left, right, left_level, right_level)
        for left, right in combinations(range(len(factors)), 2)
        for left_level in factors[left]["levels"]
        for right_level in factors[right]["levels"]
    }
    _require_count("pairwise_interactions", len(required), int(expected["pairwise_interactions"]))
    missing = required - covered
    if missing:
        raise MatrixCheckError(f"pairwise coverage missing {len(missing)} interactions")


def _registered_paths(source: str, directory_name: str) -> set[str]:
    pattern = rf"{directory_name}\s*/\s*[\"']([^\"']+)[\"']"
    return set(re.findall(pattern, source))


def _registered_unit_paths(source: str) -> set[str]:
    registered: set[str] = set()
    for match in re.finditer(r"TEST_DIR\s*/\s*([^\n,)]+)", source):
        parts = re.findall(r'"([^"]+)"', match.group(1))
        if parts and parts[-1].endswith(".py"):
            registered.add("/".join(parts))
    return registered


def _check_pysim_runners(repo_root: Path, matrix: dict, inventory: dict[str, list[str]]) -> None:
    qa_root = repo_root / matrix["qa_root"]
    unit_source = (repo_root / matrix["unit_runner"]).read_text(encoding="utf-8")
    registered_units = _registered_unit_paths(unit_source)
    actual_units = {
        (repo_root / path).relative_to(qa_root).as_posix()
        for path in inventory["unit_suites"]
    }
    if registered_units != actual_units:
        missing = sorted(actual_units - registered_units)
        extra = sorted(registered_units - actual_units)
        raise MatrixCheckError(f"unit suite registration mismatch: missing={missing}, extra={extra}")

    scenario_source = (repo_root / matrix["scenario_runner"]).read_text(encoding="utf-8")
    registered_scenarios = _registered_paths(scenario_source, "SCENARIO_DIR")
    actual_scenarios = {Path(path).name for path in inventory["scenarios"]}
    if registered_scenarios != actual_scenarios:
        missing = sorted(actual_scenarios - registered_scenarios)
        extra = sorted(registered_scenarios - actual_scenarios)
        raise MatrixCheckError(f"scenario registration mismatch: missing={missing}, extra={extra}")

    for category in ("integration", "workload"):
        runner = repo_root / matrix[f"{category}_runner"]
        source = runner.read_text(encoding="utf-8")
        registered = _registered_paths(source, f"{category.upper()}_DIR")
        actual = {Path(path).name for path in inventory[f"{category}_suites"]}
        if registered != actual:
            missing = sorted(actual - registered)
            extra = sorted(registered - actual)
            raise MatrixCheckError(
                f"{category} suite registration mismatch: missing={missing}, extra={extra}"
            )

    pairwise_source = (repo_root / matrix["pairwise_test"]).read_text(encoding="utf-8")
    required_fragments = (
        "def pairwise_case_id",
        "def run_single_pairwise_case",
        "case_id = pairwise_case_id(case_number)",
        "run_single_pairwise_case(case_id, case_tuple)",
        '"case_number,case_tuple",',
        "tuple(enumerate(PAIRWISE_CASES, start=1))",
        "def test_pairwise_case(case_number: int, case_tuple: tuple[str, ...])",
        "ids=tuple(pairwise_case_id(number) for number in range(1, len(PAIRWISE_CASES) + 1))",
    )
    for fragment in required_fragments:
        if fragment not in pairwise_source:
            raise MatrixCheckError(f"pairwise test has no generated-ID execution hook: {fragment}")


def _check_document(
    repo_root: Path,
    document_path: str,
    inventory: dict[str, list[str]],
    required_sections: tuple[str, ...],
    forbidden_paths: tuple[str, ...],
) -> None:
    path = repo_root / document_path
    if not path.is_file():
        raise MatrixCheckError(f"matrix document does not exist: {path}")
    document = path.read_text(encoding="utf-8")
    for heading in required_sections:
        if heading not in document:
            raise MatrixCheckError(f"matrix document is missing required section: {heading}")
    if "MISSING" in document:
        raise MatrixCheckError("matrix document contains MISSING; intentional N/A must be explicit")
    for forbidden in forbidden_paths:
        if forbidden in document:
            raise MatrixCheckError(f"matrix document crosses its ownership boundary: {forbidden}")
    for paths in inventory.values():
        for artifact in paths:
            if artifact not in document:
                raise MatrixCheckError(f"matrix document does not list artifact: {artifact}")


def run(repo_root: Path, config_path: Path) -> None:
    matrix = _load_config(config_path)
    product = matrix["product"]
    pysim = matrix["pysim"]
    product_inventory = _product_inventory(repo_root, product)
    pysim_inventory = _pysim_inventory(repo_root, pysim)
    factors = _load_factors(repo_root, pysim)
    _check_pairwise(repo_root, pysim, factors)
    _check_pysim_runners(repo_root, pysim, pysim_inventory)
    _check_document(
        repo_root,
        product["document"],
        product_inventory,
        ("成果物連鎖マトリクス", "成果物インベントリ", "適用範囲"),
        ("experiments/pysim/",),
    )
    _check_document(
        repo_root,
        pysim["document"],
        pysim_inventory,
        (
            "実行資産",
            "コンポーネント単体スイート",
            "結合スイート",
            "システムシナリオ",
            "WASMワークロード",
            "因子カタログ",
            "シナリオ因子マトリクス",
            "PySim QAゲート",
        ),
        ("docs/components/", "docs/qa/"),
    )
    print(
        "[PASS] product verification matrix: "
        f"{len(product_inventory['component_specs'])} specs, "
        f"{len(product_inventory['concept_files'])} concepts, "
        f"{len(product_inventory['formal_models'])} formal models, "
        f"{len(product_inventory['test_specs'])} test specs"
    )
    print(
        "[PASS] PySim QA matrix: "
        f"{len(pysim_inventory['unit_suites'])} unit suites, "
        f"{len(pysim_inventory['integration_suites'])} integration suites, "
        f"{len(pysim_inventory['workload_suites'])} WASM workload suites, "
        f"{len(pysim_inventory['scenarios'])} scenarios, pairwise complete"
    )


if __name__ == "__main__":
    try:
        default_root = Path(__file__).resolve().parent.parent
        config_path = (
            Path(sys.argv[1]).resolve()
            if len(sys.argv) > 1
            else default_root / "spec-integrator.yaml"
        )
        run(config_path.parent, config_path)
    except (MatrixCheckError, KeyError, OSError, ValueError) as error:
        print(f"[FAIL] verification matrix: {error}", file=sys.stderr)
        sys.exit(1)

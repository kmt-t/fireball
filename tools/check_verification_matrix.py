"""Strict completeness checker for Fireball verification matrices.

This checker is deliberately independent from the document database.  It
checks the concrete artifact inventory, the executable registrations, and the
pairwise data that the database cannot infer from prose alone.
"""

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
    return matrix


def _relative(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _require_count(label: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise MatrixCheckError(f"{label}: expected {expected}, got {actual}")


def _artifact_inventory(repo_root: Path, matrix: dict) -> dict[str, list[str]]:
    root = repo_root / matrix["component_root"]
    inventory = {
        "component_specs": sorted(
            _relative(path, repo_root)
            for path in root.glob("tier*/*.md")
            if path.name != "FORMAT.md"
        ),
        "concept_files": sorted(
            _relative(path, repo_root) for path in root.glob("tier*/concepts/*_concept.py")
        ),
        "formal_models": sorted(
            _relative(path, repo_root) for path in root.glob("tier*/formal/*_model.py")
        ),
        "test_specs": sorted(
            _relative(path, repo_root)
            for path in (repo_root / matrix["test_spec_root"]).glob("tier*/*_test_spec.md")
        ),
        "scenarios": sorted(
            _relative(path, repo_root)
            for path in (repo_root / matrix["scenario_dir"]).glob("scenario[0-9]*_*.py")
        ),
        "factor_csv": [_relative(repo_root / matrix["factor_csv"], repo_root)],
    }
    expected = matrix["expected"]
    for key in ("component_specs", "concept_files", "formal_models", "test_specs", "scenarios"):
        _require_count(key, len(inventory[key]), int(expected[key]))
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

    case_ids: set[str] = set()
    covered: set[tuple[int, int, object, object]] = set()
    for case_number, row in enumerate(cases, start=1):
        if not isinstance(row, tuple) or len(row) != len(factors):
            raise MatrixCheckError("every pairwise row must contain all factors; IDs are generated")
        case_id = f"TEST-PAIR-{case_number:02d}"
        if case_id in case_ids:
            raise MatrixCheckError(f"duplicate generated pairwise case ID: {case_id}")
        case_ids.add(case_id)
        for index, factor in enumerate(factors):
            if row[index] not in factor["levels"]:
                raise MatrixCheckError(
                    f"{case_id}: {factor['id']} has undeclared level {row[index]!r}"
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


def _registered_paths(source: str, variable: str, directory_name: str) -> set[str]:
    if variable == "SCENARIOS":
        pattern = rf"{directory_name}\s*/\s*[\"']([^\"']+)[\"']"
    else:
        pattern = rf"{directory_name}\s*/\s*[\"']([^\"']+)[\"']"
    return set(re.findall(pattern, source))


def _check_runner_inventory(repo_root: Path, matrix: dict, inventory: dict[str, list[str]]) -> None:
    scenario_dir = repo_root / matrix["scenario_dir"]
    scenario_runner = repo_root / matrix["scenario_runner"]
    scenario_source = scenario_runner.read_text(encoding="utf-8")
    registered_scenarios = _registered_paths(scenario_source, "SCENARIOS", "SCENARIO_DIR")
    actual_scenarios = {Path(path).name for path in inventory["scenarios"]}
    if registered_scenarios != actual_scenarios:
        missing = sorted(actual_scenarios - registered_scenarios)
        extra = sorted(registered_scenarios - actual_scenarios)
        raise MatrixCheckError(f"scenario registration mismatch: missing={missing}, extra={extra}")
    if not scenario_dir.exists():
        raise MatrixCheckError(f"scenario directory does not exist: {scenario_dir}")

    unit_runner = repo_root / matrix["unit_runner"]
    unit_source = unit_runner.read_text(encoding="utf-8")
    registered_units: set[str] = set()
    for match in re.finditer(r"TEST_DIR\s*/\s*([^\n,)]+)", unit_source):
        parts = re.findall(r'"([^"]+)"', match.group(1))
        if parts and parts[-1].endswith(".py"):
            registered_units.add("/".join(parts))
    _require_count("pysim_unit_suites", len(registered_units), int(matrix["expected"]["pysim_unit_suites"]))

    pairwise_source = (repo_root / matrix["pairwise_test"]).read_text(encoding="utf-8")
    required_fragments = (
        "def pairwise_case_id",
        "def run_single_pairwise_case",
        "case_id = pairwise_case_id(case_number)",
        "run_single_pairwise_case(case_id, case_tuple)",
    )
    required_execution_fragments = (
        *required_fragments,
        "assert tuple(executed_case_ids) == expected_case_ids",
    )
    for fragment in required_execution_fragments:
        if fragment not in pairwise_source:
            raise MatrixCheckError(f"pairwise test has no generated-ID execution hook: {fragment}")


def _check_document(repo_root: Path, matrix: dict, inventory: dict[str, list[str]]) -> None:
    document_path = repo_root / matrix["document"]
    if not document_path.is_file():
        raise MatrixCheckError(f"matrix document does not exist: {document_path}")
    document = document_path.read_text(encoding="utf-8")
    for heading in ("成果物連鎖マトリクス", "因子カタログ", "シナリオ因子マトリクス", "検証ゲート"):
        if heading not in document:
            raise MatrixCheckError(f"matrix document is missing required section: {heading}")
    if "MISSING" in document:
        raise MatrixCheckError("matrix document contains MISSING; intentional N/A must be explicit")
    for paths in inventory.values():
        for path in paths:
            if path not in document:
                raise MatrixCheckError(f"matrix document does not list artifact: {path}")
    for scenario_path in inventory["scenarios"]:
        if Path(scenario_path).name not in document:
            raise MatrixCheckError(f"matrix document does not list scenario: {scenario_path}")


def run(repo_root: Path, config_path: Path) -> None:
    matrix = _load_config(config_path)
    inventory = _artifact_inventory(repo_root, matrix)
    factors = _load_factors(repo_root, matrix)
    _check_pairwise(repo_root, matrix, factors)
    _check_runner_inventory(repo_root, matrix, inventory)
    _check_document(repo_root, matrix, inventory)
    print(
        "[PASS] verification matrix: "
        f"{len(inventory['component_specs'])} specs, "
        f"{len(inventory['concept_files'])} concepts, "
        f"{len(inventory['formal_models'])} formal models, "
        f"{len(inventory['test_specs'])} test specs, "
        f"{len(inventory['scenarios'])} scenarios, pairwise complete"
    )


if __name__ == "__main__":
    try:
        default_root = Path(__file__).resolve().parent.parent
        config_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default_root / "spec-integrator.yaml"
        run(config_path.parent, config_path)
    except (MatrixCheckError, KeyError, OSError, ValueError) as error:
        print(f"[FAIL] verification matrix: {error}", file=sys.stderr)
        sys.exit(1)

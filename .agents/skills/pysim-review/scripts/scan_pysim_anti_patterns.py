#!/usr/bin/env python3
"""
scan_pysim_anti_patterns.py
pysim コードベース向け静的アンチパターンスキャナ。
組み込み C++ 移植性の観点から、以下の違反を AST 解析で検出します：
1. typing.Any / object / 非None Union の使用 (NO_BROAD_TYPE)
2. dict / set / list などの組み込みコンテナの使用 (NO_BUILTIN_CONTAINER)
3. list.append / insert / pop による動的伸縮 (NO_UNBOUNDED_LIST)
4. 関数引数・戻り値の型注釈欠落 (MISSING_TYPE_ANNOTATION)
5. RTTI・動的型検査の使用 (NO_RTTI)
6. bytearray の使用 (MUTABLE_BYTEARRAY)
7. 実行時クラスでの __slots__ 欠落 (NO_SLOTS)
8. 到達不能な if 分岐 (DEAD_IF_BRANCH)
9. 明示的な例外送出 (NO_RAISE; except による捕捉は許可)
10. テストコード・テスト用バックドアの製品コード混入 (NO_TEST_CODE_IN_PRODUCT)
11. 製品クラスの文字列メンバー (NO_STRING_MEMBER)
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
from pathlib import Path
from typing import TypedDict

try:
    import yaml
except ImportError:  # pragma: no cover - the standalone scanner also runs without PyYAML
    yaml = None


class Issue(TypedDict):
    rule_id: str
    severity: str
    file: str
    line: int
    col: int
    message: str


class PySimASTVisitor(ast.NodeVisitor):
    def __init__(
        self,
        filename: str,
        enforce_builtin_containers: bool = True,
        callable_parameter_lists: set[int] | None = None,
        enforce_rtti: bool = True,
        enforce_in_operator: bool = True,
        enforce_raise: bool = True,
        enforce_test_backdoor: bool = True,
        enforce_string_members: bool = True,
    ):
        self.filename = filename
        self.enforce_builtin_containers = enforce_builtin_containers
        self.callable_parameter_lists = callable_parameter_lists or set()
        self.enforce_rtti = enforce_rtti
        self.enforce_in_operator = enforce_in_operator
        self.enforce_raise = enforce_raise
        self.enforce_test_backdoor = enforce_test_backdoor
        self.enforce_string_members = enforce_string_members
        self.issues: list[Issue] = []

    def _add_issue(self, rule_id: str, severity: str, node: ast.AST, message: str) -> None:
        self.issues.append(
            {
                "rule_id": rule_id,
                "severity": severity,
                "file": self.filename,
                "line": getattr(node, "lineno", 0),
                "col": getattr(node, "col_offset", 0),
                "message": message,
            }
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "typing.Any" or alias.name == "Any":
                self._add_issue(
                    "NO_ANY", "ERROR", node, "typing.Any is strictly prohibited in pysim."
                )
            if self.enforce_test_backdoor and alias.name in {"pytest", "unittest", "unittest.mock", "mock"}:
                self._add_issue(
                    "NO_TEST_CODE_IN_PRODUCT",
                    "ERROR",
                    node,
                    f"Test-only module '{alias.name}' is imported by pysim product code.",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in ("typing", "typing_extensions"):
            for alias in node.names:
                if alias.name == "Any":
                    self._add_issue(
                        "NO_ANY", "ERROR", node, "typing.Any is strictly prohibited in pysim."
                    )
        if self.enforce_test_backdoor and node.module in {"pytest", "unittest", "unittest.mock", "mock"}:
            self._add_issue(
                "NO_TEST_CODE_IN_PRODUCT",
                "ERROR",
                node,
                f"Test-only module '{node.module}' is imported by pysim product code.",
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "object":
            self._add_issue(
                "NO_OBJECT_TYPE",
                "ERROR",
                node,
                "The object type is prohibited in pysim. Use a concrete type or algebraic data type.",
            )
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if not self.enforce_raise:
            self.generic_visit(node)
            return
        self._add_issue(
            "NO_RAISE",
            "ERROR",
            node,
            "Explicit 'raise' is forbidden in pysim; return an error result or fail-fast with assert.",
        )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.enforce_rtti and node.attr in {
            "__class__",
            "__dict__",
            "__getattribute__",
            "__getattr__",
            "__setattr__",
            "__instancecheck__",
            "__subclasscheck__",
        }:
            self._add_issue(
                "NO_RTTI",
                "ERROR",
                node,
                f"Dynamic type/reflection attribute '{node.attr}' is prohibited in pysim.",
            )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self.enforce_in_operator and any(
            isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops
        ):
            self._add_issue(
                "NO_IN_OPERATOR",
                "ERROR",
                node,
                "The 'in' operator is prohibited in pysim product code; use a bounded view lookup or indexed access.",
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.enforce_test_backdoor and (node.name.startswith("test_") or node.name.startswith("_test_")):
            self._add_issue(
                "NO_TEST_CODE_IN_PRODUCT",
                "ERROR",
                node,
                f"Test-only symbol '{node.name}' is defined in pysim product code.",
            )
        self._check_function_types(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.enforce_test_backdoor and (node.name.startswith("test_") or node.name.startswith("_test_")):
            self._add_issue(
                "NO_TEST_CODE_IN_PRODUCT",
                "ERROR",
                node,
                f"Test-only symbol '{node.name}' is defined in pysim product code.",
            )
        self._check_function_types(node)
        self.generic_visit(node)

    def _check_function_types(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # 特殊メソッド (__init__ など) の戻り値 None 省略は許容
        if node.returns is None and node.name not in ("__init__", "__del__"):
            self._add_issue(
                "MISSING_RETURN_TYPE",
                "WARNING",
                node,
                f"Function '{node.name}' is missing return type annotation.",
            )

        for arg in node.args.args:
            if arg.arg in ("self", "cls"):
                continue
            if arg.annotation is None:
                self._add_issue(
                    "MISSING_PARAM_TYPE",
                    "WARNING",
                    arg,
                    f"Parameter '{arg.arg}' of function '{node.name}' is missing type annotation.",
                )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name.startswith("test_") or node.name.startswith("_test_"):
            self._add_issue(
                "NO_TEST_CODE_IN_PRODUCT",
                "ERROR",
                node,
                f"Test-only symbol '{node.name}' is defined in pysim product code.",
            )
        # Check __slots__ definition for non-enum/non-exception classes
        base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
        is_exempt = any(
            name in (
                "IntEnum",
                "IntFlag",
                "Enum",
                "Exception",
                "RuntimeError",
                "ValueError",
                "TypedDict",
                "Protocol",
            )
            for name in base_names
        )
        dataclass_slots = any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
            and any(
                keyword.arg == "slots"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in decorator.keywords
            )
            for decorator in node.decorator_list
        )
        if not is_exempt:
            has_slots = any(
                isinstance(stmt, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in stmt.targets)
                for stmt in node.body
            )
            if not has_slots and not dataclass_slots:
                self._add_issue(
                    "NO_SLOTS",
                    "WARNING",
                    node,
                    f"Class '{node.name}' does not define '__slots__'. Add __slots__ to eliminate dynamic __dict__ RAM overhead.",
                )
        if self.enforce_string_members:
            member_annotations: list[ast.AnnAssign] = [
                statement for statement in node.body if isinstance(statement, ast.AnnAssign)
            ]
            for method in node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                member_annotations.extend(
                    statement
                    for statement in ast.walk(method)
                    if isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Attribute)
                    and isinstance(statement.target.value, ast.Name)
                    and statement.target.value.id in ("self", "cls")
                )
            for statement in member_annotations:
                if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                    continue
                if (
                    isinstance(statement.annotation, ast.Subscript)
                    and isinstance(statement.annotation.value, ast.Name)
                    and statement.annotation.value.id in ("ClassVar", "Final")
                ):
                    continue
                if not any(
                    isinstance(item, ast.Name) and item.id == "str"
                    for item in ast.walk(statement.annotation)
                ):
                    continue
                self._add_issue(
                    "NO_STRING_MEMBER",
                    "ERROR",
                    statement,
                    "String-typed class members are prohibited in pysim product code; store a ROM byte range, integer ID, or fixed numeric representation.",
                )
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        # Check constant falsy condition (dead branch)
        if isinstance(node.test, ast.Constant):
            if not node.test.value:
                self._add_issue(
                    "DEAD_IF_BRANCH",
                    "WARNING",
                    node,
                    "Unreachable 'if' branch with constant falsy condition detected.",
                )
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        if not self.enforce_builtin_containers:
            self.generic_visit(node)
            return
        self._add_issue(
            "NO_DYNAMIC_DICT",
            "ERROR",
            node,
            "Dict literal {} is prohibited in runtime structures. Use system containers (FlatMapView, FlatMapStorage, etc.).",
        )
        self.generic_visit(node)

    def visit_Set(self, node: ast.Set) -> None:
        if not self.enforce_builtin_containers:
            self.generic_visit(node)
            return
        self._add_issue(
            "NO_DYNAMIC_SET",
            "ERROR",
            node,
            "Set literal is prohibited in runtime structures. Use FlatSetView / FlatSetStorage.",
        )
        self.generic_visit(node)

    def visit_List(self, node: ast.List) -> None:
        if not self.enforce_builtin_containers:
            self.generic_visit(node)
            return
        if id(node) in self.callable_parameter_lists:
            self.generic_visit(node)
            return
        self._add_issue(
            "NO_BUILTIN_LIST",
            "ERROR",
            node,
            "List literal is prohibited in pysim. Use a fixed-capacity system container.",
        )
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        if not self.enforce_builtin_containers:
            self.generic_visit(node)
            return
        self._add_issue(
            "NO_BUILTIN_LIST",
            "ERROR",
            node,
            "List comprehension is prohibited in pysim. Use a fixed-capacity system container.",
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # dict() / set() / list() / bytearray()
        if isinstance(node.func, ast.Name):
            if node.func.id == "dict" and self.enforce_builtin_containers:
                self._add_issue(
                    "NO_DYNAMIC_DICT",
                    "ERROR",
                    node,
                    "Calling dict() is prohibited. Use system containers (FlatMapView, etc.).",
                )
            elif node.func.id == "set" and self.enforce_builtin_containers:
                self._add_issue(
                    "NO_DYNAMIC_SET",
                    "ERROR",
                    node,
                    "Calling set() is prohibited. Use FlatSetView / FlatSetStorage.",
                )
            elif node.func.id == "list" and self.enforce_builtin_containers:
                self._add_issue(
                    "NO_BUILTIN_LIST",
                    "ERROR",
                    node,
                    "Calling list() is prohibited. Use a fixed-capacity system container.",
                )
            elif node.func.id in ("isinstance", "type", "hasattr", "getattr", "setattr"):
                self._add_issue(
                    "NO_RTTI",
                    "ERROR",
                    node,
                    f"Dynamic type inspection '{node.func.id}' (RTTI / reflection) is prohibited in pysim.",
                )
            elif node.func.id == "bytearray":
                self._add_issue(
                    "MUTABLE_BYTEARRAY",
                    "WARNING",
                    node,
                    "bytearray() creates mutable RAM buffer. Use immutable 'bytes' if data is read-only (ROM placeable).",
                )

        # list.append / insert / pop
        elif isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if self.enforce_builtin_containers and attr in ("append", "insert"):
                self._add_issue(
                    "NO_UNBOUNDED_LIST",
                    "WARNING",
                    node,
                    f"Dynamic container growth '.{attr}()' detected. Ensure StaticVector or RingBuffer with bounded capacity is used.",
                )

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # dict[K, V], set[T], or list[T] as type annotation
        if self.enforce_builtin_containers and isinstance(node.value, ast.Name):
            if node.value.id == "dict":
                self._add_issue(
                    "NO_DYNAMIC_DICT",
                    "ERROR",
                    node,
                    "Type annotation 'dict[...]' is prohibited in pysim. Use system container views.",
                )
            elif node.value.id == "set":
                self._add_issue(
                    "NO_DYNAMIC_SET",
                    "ERROR",
                    node,
                    "Type annotation 'set[...]' is prohibited in pysim. Use FlatSetView.",
                )
            elif node.value.id == "list":
                self._add_issue(
                    "NO_BUILTIN_LIST",
                    "ERROR",
                    node,
                    "Type annotation 'list[...]' is prohibited in pysim. Use a concrete system container type.",
                )
        self.generic_visit(node)


def scan_file(
    file_path: Path,
    repo_root: Path,
    builtin_container_exclude_paths: set[str],
    rtti_exclude_paths: set[str],
    in_operator_exclude_paths: set[str],
    raise_exclude_paths: set[str],
    test_backdoor_exclude_paths: set[str],
    non_none_union_exclude_paths: set[str],
    string_member_exclude_paths: set[str],
) -> list[Issue]:
    rel_path = str(file_path.relative_to(repo_root)).replace("\\", "/")
    content = file_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content, filename=rel_path)
    except SyntaxError as e:
        return [
            {
                "rule_id": "SYNTAX_ERROR",
                "severity": "CRITICAL",
                "file": rel_path,
                "line": e.lineno or 0,
                "col": e.offset or 0,
                "message": f"Syntax error: {e.msg}",
            }
        ]

    is_container_implementation = any(
        fnmatch.fnmatch(rel_path, pattern) for pattern in builtin_container_exclude_paths
    )
    enforce_rtti = not any(fnmatch.fnmatch(rel_path, pattern) for pattern in rtti_exclude_paths)
    enforce_in_operator = not any(
        fnmatch.fnmatch(rel_path, pattern) for pattern in in_operator_exclude_paths
    )
    enforce_raise = not any(fnmatch.fnmatch(rel_path, pattern) for pattern in raise_exclude_paths)
    enforce_test_backdoor = not any(
        fnmatch.fnmatch(rel_path, pattern) for pattern in test_backdoor_exclude_paths
    )
    enforce_non_none_union = not any(
        fnmatch.fnmatch(rel_path, pattern) for pattern in non_none_union_exclude_paths
    )
    enforce_string_members = not any(
        fnmatch.fnmatch(rel_path, pattern) for pattern in string_member_exclude_paths
    )
    callable_parameter_lists = {
        id(node.slice.elts[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and (
            (isinstance(node.value, ast.Name) and node.value.id == "Callable")
            or (isinstance(node.value, ast.Attribute) and node.value.attr == "Callable")
        )
        and isinstance(node.slice, ast.Tuple)
        and node.slice.elts
        and isinstance(node.slice.elts[0], ast.List)
    }
    visitor = PySimASTVisitor(
        rel_path,
        not is_container_implementation,
        callable_parameter_lists=callable_parameter_lists,
        enforce_rtti=enforce_rtti,
        enforce_in_operator=enforce_in_operator,
        enforce_raise=enforce_raise,
        enforce_test_backdoor=enforce_test_backdoor,
        enforce_string_members=enforce_string_members,
    )
    visitor.visit(tree)
    if enforce_non_none_union:
        visitor.issues.extend(_scan_non_none_unions(tree, rel_path))
    return visitor.issues


def _union_parts(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _union_parts(node.left) + _union_parts(node.right)
    return [node]


def _is_none_type(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _scan_non_none_unions(tree: ast.Module, filename: str) -> list[Issue]:
    annotations: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        elif isinstance(node, ast.Assign):
            # PEP 604 aliases are ordinary assignments in the AST.
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
                if target_name and target_name[0].isupper():
                    annotations.append(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            annotations.extend(arg.annotation for arg in arguments if arg.annotation is not None)
            if node.args.vararg is not None and node.args.vararg.annotation is not None:
                annotations.append(node.args.vararg.annotation)
            if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
                annotations.append(node.args.kwarg.annotation)
            if node.returns is not None:
                annotations.append(node.returns)

    issues: list[Issue] = []
    for annotation in annotations:
        for node in _union_nodes(annotation):
            parts: list[ast.AST] | None = None
            if isinstance(node, ast.BinOp):
                parts = _union_parts(node)
            elif isinstance(node, ast.Subscript) and (
                isinstance(node.value, ast.Name) or isinstance(node.value, ast.Attribute)
            ):
                value_name = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                if value_name == "Union":
                    parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
                elif value_name == "Optional":
                    args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
                    if len(args) != 1:
                        parts = args
            if parts is not None and (
                len(parts) != 2 or sum(_is_none_type(part) for part in parts) != 1
            ):
                issues.append(
                    {
                        "rule_id": "NO_NON_NONE_UNION",
                        "severity": "ERROR",
                        "file": filename,
                        "line": getattr(node, "lineno", 0),
                        "col": getattr(node, "col_offset", 0),
                        "message": "Only T | None or Optional[T] is allowed in pysim.",
                    }
                )
    return issues


def _union_nodes(annotation: ast.AST) -> list[ast.AST]:
    """Returns top-level union expressions without duplicate nested reports."""
    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            nodes.append(node)
            return
        if isinstance(node, ast.Subscript) and (
            isinstance(node.value, ast.Name) or isinstance(node.value, ast.Attribute)
        ):
            value_name = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
            if value_name in ("Union", "Optional"):
                nodes.append(node)
                return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(annotation)
    return nodes


def load_builtin_container_exclude_paths(repo_root: Path) -> set[str]:
    config_path = repo_root / "spec-integrator.yaml"
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError:
        return set()

    return load_source_policy_paths_from_text(config_text, "builtin_container_exclude_paths")


def load_source_policy_paths(repo_root: Path, field_name: str) -> set[str]:
    config_path = repo_root / "spec-integrator.yaml"
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError:
        return set()

    return load_source_policy_paths_from_text(config_text, field_name)


def load_source_policy_paths_from_text(config_text: str, field_name: str) -> set[str]:
    if yaml is not None:
        try:
            config = yaml.safe_load(config_text) or {}
        except yaml.YAMLError:
            return set()
        checks = config.get("source_verification", {}).get("groups", {}).get("python_pysim", {}).get("checks", [])
        for check in checks:
            if isinstance(check, dict) and check.get("id") == "anti_sabotage":
                paths = check.get(field_name, [])
                return {
                    str(path).replace("\\", "/").lstrip("./")
                    for path in paths
                    if isinstance(path, str)
                }
        return set()

    key = re.escape(field_name)
    match = re.search(
        rf"(?ms)^\s*{key}:\s*\n((?:\s+-\s+['\"]?[^\n'\"]+['\"]?\s*\n?)+)",
        config_text,
    )
    if not match:
        return set()
    return {
        value.replace("\\", "/").lstrip("./")
        for value in re.findall(r"^\s+-\s+['\"]?([^'\"\n]+?)['\"]?\s*$", match.group(1), re.MULTILINE)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="pysim 静的アンチパターンスキャナ")
    parser.add_argument("paths", nargs="+", help="スキャン対象のファイルまたはディレクトリ")
    parser.add_argument("--json", action="store_true", help="JSON形式で結果を出力")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[4]  # repo_root
    builtin_container_exclude_paths = load_builtin_container_exclude_paths(repo_root)
    rtti_exclude_paths = load_source_policy_paths(repo_root, "rtti_exclude_paths")
    in_operator_exclude_paths = load_source_policy_paths(repo_root, "in_operator_exclude_paths")
    raise_exclude_paths = load_source_policy_paths(repo_root, "raise_exclude_paths")
    test_backdoor_exclude_paths = load_source_policy_paths(repo_root, "test_backdoor_exclude_paths")
    non_none_union_exclude_paths = load_source_policy_paths(
        repo_root, "non_none_union_exclude_paths"
    )
    string_member_exclude_paths = load_source_policy_paths(
        repo_root, "string_member_exclude_paths"
    )

    all_issues: list[Issue] = []
    for p_str in args.paths:
        p = (repo_root / p_str).resolve() if not Path(p_str).is_absolute() else Path(p_str)
        if p.is_file() and p.suffix == ".py":
            all_issues.extend(
                scan_file(
                    p,
                    repo_root,
                    builtin_container_exclude_paths,
                    rtti_exclude_paths,
                    in_operator_exclude_paths,
                    raise_exclude_paths,
                    test_backdoor_exclude_paths,
                    non_none_union_exclude_paths,
                    string_member_exclude_paths,
                )
            )
        elif p.is_dir():
            for py_file in p.glob("**/*.py"):
                if "__pycache__" in py_file.parts or ".pytest_cache" in py_file.parts:
                    continue
                all_issues.extend(
                    scan_file(
                        py_file,
                        repo_root,
                        builtin_container_exclude_paths,
                        rtti_exclude_paths,
                        in_operator_exclude_paths,
                        raise_exclude_paths,
                        test_backdoor_exclude_paths,
                        non_none_union_exclude_paths,
                        string_member_exclude_paths,
                    )
                )

    if args.json:
        print(json.dumps(all_issues, indent=2, ensure_ascii=False))
        return 0

    print(f"=== pysim Anti-Pattern Scan Report: {len(all_issues)} issue(s) detected ===")
    errors = [i for i in all_issues if i["severity"] == "ERROR"]
    warnings = [i for i in all_issues if i["severity"] == "WARNING"]
    print(f"  Errors: {len(errors)}, Warnings: {len(warnings)}\n")

    for issue in all_issues:
        prefix = "[ERROR]" if issue["severity"] in ("ERROR", "CRITICAL") else "[WARN]"
        print(
            f"{prefix} [{issue['rule_id']}] {issue['file']}:{issue['line']}:{issue['col']} - {issue['message']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

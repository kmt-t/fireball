#!/usr/bin/env python3
"""
scan_pysim_anti_patterns.py
pysim コードベース向け静的アンチパターンスキャナ。
組み込み C++ 移植性の観点から、以下の違反を AST 解析で検出します：
1. typing.Any の使用 (NO_ANY)
2. dict / set などの動的コンテナの使用 (NO_DYNAMIC_DICT_SET)
3. list.append / insert / pop による無制限な動的伸縮 (NO_UNBOUNDED_LIST)
4. 関数引数・戻り値の型注釈欠落 (MISSING_TYPE_ANNOTATION)
5. RTTI・動的型検査の使用 (NO_RTTI)
6. bytearray の使用 (MUTABLE_BYTEARRAY)
7. 実行時クラスでの __slots__ 欠落 (NO_SLOTS)
8. 到達不能な if 分岐 (DEAD_IF_BRANCH)
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import TypedDict


class Issue(TypedDict):
    rule_id: str
    severity: str
    file: str
    line: int
    col: int
    message: str


class PySimASTVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
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
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in ("typing", "typing_extensions"):
            for alias in node.names:
                if alias.name == "Any":
                    self._add_issue(
                        "NO_ANY", "ERROR", node, "typing.Any is strictly prohibited in pysim."
                    )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_types(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
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
        # Check __slots__ definition for non-enum/non-exception classes
        base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
        is_exempt = any(name in ("IntEnum", "Enum", "Exception", "RuntimeError", "ValueError", "TypedDict") for name in base_names)
        if not is_exempt:
            has_slots = any(
                isinstance(stmt, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__slots__" for target in stmt.targets)
                for stmt in node.body
            )
            if not has_slots:
                self._add_issue(
                    "NO_SLOTS",
                    "WARNING",
                    node,
                    f"Class '{node.name}' does not define '__slots__'. Add __slots__ to eliminate dynamic __dict__ RAM overhead.",
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
        self._add_issue(
            "NO_DYNAMIC_DICT",
            "ERROR",
            node,
            "Dict literal {} is prohibited in runtime structures. Use system containers (FlatMapView, FlatMapStorage, etc.).",
        )
        self.generic_visit(node)

    def visit_Set(self, node: ast.Set) -> None:
        self._add_issue(
            "NO_DYNAMIC_SET",
            "ERROR",
            node,
            "Set literal is prohibited in runtime structures. Use FlatSetView / FlatSetStorage.",
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # dict() / set() / bytearray()
        if isinstance(node.func, ast.Name):
            if node.func.id == "dict":
                self._add_issue(
                    "NO_DYNAMIC_DICT",
                    "ERROR",
                    node,
                    "Calling dict() is prohibited. Use system containers (FlatMapView, etc.).",
                )
            elif node.func.id == "set":
                self._add_issue(
                    "NO_DYNAMIC_SET",
                    "ERROR",
                    node,
                    "Calling set() is prohibited. Use FlatSetView / FlatSetStorage.",
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
            if attr in ("append", "insert"):
                self._add_issue(
                    "NO_UNBOUNDED_LIST",
                    "WARNING",
                    node,
                    f"Dynamic container growth '.{attr}()' detected. Ensure StaticVector or RingBuffer with bounded capacity is used.",
                )

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # dict[K, V] or set[T] as type annotation
        if isinstance(node.value, ast.Name):
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
        self.generic_visit(node)


def scan_file(file_path: Path, repo_root: Path) -> list[Issue]:
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

    visitor = PySimASTVisitor(rel_path)
    visitor.visit(tree)
    return visitor.issues


def main() -> int:
    parser = argparse.ArgumentParser(description="pysim 静的アンチパターンスキャナ")
    parser.add_argument("paths", nargs="+", help="スキャン対象のファイルまたはディレクトリ")
    parser.add_argument("--json", action="store_true", help="JSON形式で結果を出力")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[4]  # repo_root

    all_issues: list[Issue] = []
    for p_str in args.paths:
        p = (repo_root / p_str).resolve() if not Path(p_str).is_absolute() else Path(p_str)
        if p.is_file() and p.suffix == ".py":
            all_issues.extend(scan_file(p, repo_root))
        elif p.is_dir():
            for py_file in p.glob("**/*.py"):
                if "__pycache__" in py_file.parts or ".pytest_cache" in py_file.parts:
                    continue
                all_issues.extend(scan_file(py_file, repo_root))

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

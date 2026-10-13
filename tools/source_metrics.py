"""SLOC counters shared by the firmware gate and resource inventory."""

from __future__ import annotations

import ast
import io
import tokenize

from pygments.lexers import CLexer, CppLexer
from pygments.token import Comment


def python_code_lines(source: str) -> int:
    tree = ast.parse(source)
    docstrings: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        value = node.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            assert value.end_lineno is not None and value.end_col_offset is not None
            docstrings.append(
                ((value.lineno, value.col_offset), (value.end_lineno, value.end_col_offset))
            )
    lines: set[int] = set()
    ignored = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in ignored or not token.string.strip():
            continue
        if token.type == tokenize.STRING and any(
            start <= token.start < end for start, end in docstrings
        ):
            continue
        for offset, part in enumerate(token.string.split("\n")):
            if part.strip():
                lines.add(token.start[0] + offset)
    return len(lines)


def cpp_code_lines(source: str, *, c_language: bool = False) -> int:
    lines: set[int] = set()
    line = 1
    lexer = CLexer() if c_language else CppLexer()
    for _, kind, value in lexer.get_tokens_unprocessed(source):
        # Pygments categorizes preprocessor directives as Comment.Preproc;
        # they are product code and remain included in SLOC.
        if kind not in Comment or kind in Comment.Preproc:
            for offset, part in enumerate(value.split("\n")):
                if part.strip():
                    lines.add(line + offset)
        line += value.count("\n")
    return len(lines)

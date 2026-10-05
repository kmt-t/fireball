"""Inventory existing source lines and host ELF sections without extrapolating to ARM."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import platform
import subprocess
import sys
import tempfile
import tokenize
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pygments
from pygments.lexers import CLexer, CppLexer
from pygments.token import Comment

_ROOT = Path(__file__).resolve().parents[4]
_PYSIM = _ROOT / "experiments/pysim"
_REFERENCE_INTERPRETER = _PYSIM / "tier2_runtime/interpreter"
sys.path.insert(0, str(_PYSIM / "tier1_core"))
from config import (
    JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES,
    JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET,
    JIT_TRACE_COMMON_EPILOGUE_OFFSET,
    JIT_X64_CHAIN_TARGET_OFFSET,
    JIT_X64_TRACE_ENTRY_STUB_BYTES,
    JIT_X64_TRACE_HEADER_BYTES,
)


@dataclass(frozen=True)
class SourceSize:
    path: str
    sha256: str
    physical_lines: int
    code_lines: int


@dataclass(frozen=True)
class SourceGroup:
    name: str
    files: tuple[SourceSize, ...]
    physical_lines: int
    code_lines: int


@dataclass(frozen=True)
class ELFSize:
    path: str
    sha256: str
    sections: dict[str, int]
    text_rodata_bytes: int
    data_bss_bytes: int
    unwind_bytes: int


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


def source_group(name: str, paths: tuple[Path, ...]) -> SourceGroup:
    files: list[SourceSize] = []
    for path in sorted(paths):
        data = path.read_bytes()
        source = data.decode("utf-8")
        code = (
            python_code_lines(source)
            if path.suffix == ".py"
            else cpp_code_lines(source, c_language=path.suffix == ".c")
        )
        files.append(
            SourceSize(
                str(path.relative_to(_ROOT)),
                hashlib.sha256(data).hexdigest(),
                len(source.splitlines()),
                code,
            )
        )
    return SourceGroup(
        name, tuple(files), sum(f.physical_lines for f in files), sum(f.code_lines for f in files)
    )


def elf_size(path: Path) -> ELFSize:
    output = subprocess.check_output(["size", "-A", str(path)], text=True)
    sections: dict[str, int] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0].startswith("."):
            sections[fields[0]] = int(fields[1])
    assert ".text" in sections
    return ELFSize(
        str(path.relative_to(_ROOT)),
        hashlib.sha256(path.read_bytes()).hexdigest(),
        sections,
        sections[".text"] + sections.get(".rodata", 0),
        sections.get(".data", 0) + sections.get(".bss", 0),
        sections.get(".eh_frame", 0) + sections.get(".eh_frame_hdr", 0),
    )


def compiler_stack_frames() -> dict[str, int]:
    definitions = {
        "FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET": JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET,
        "FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES": JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES,
        "FB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET": JIT_TRACE_COMMON_EPILOGUE_OFFSET,
        "FB_CONF_JIT_X64_TRACE_HEADER_BYTES": JIT_X64_TRACE_HEADER_BYTES,
        "FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET": JIT_X64_CHAIN_TARGET_OFFSET,
        "FB_CONF_JIT_X64_TRACE_ENTRY_STUB_BYTES": JIT_X64_TRACE_ENTRY_STUB_BYTES,
    }
    source = _PYSIM / "native/tier3_plugins/jit/trace_compiler.cxx"
    with tempfile.TemporaryDirectory(prefix="fireball-resource-") as directory:
        output = Path(directory) / "trace_compiler.o"
        subprocess.run(
            [
                "clang++",
                "-std=c++23",
                "-O2",
                "-fPIC",
                "-fstack-usage",
                *(f"-D{name}={value}" for name, value in definitions.items()),
                "-c",
                str(source),
                "-o",
                str(output),
            ],
            check=True,
        )
        frames: dict[str, int] = {}
        for line in output.with_suffix(".su").read_text().splitlines():
            name, size, classification = line.split("\t")
            assert classification == "static", line
            function = name.split(":", 3)[-1]
            if "compile_wasm_trace" in function:
                function = "compile_wasm_trace"
            elif "compile_instruction_body" in function:
                function = "compile_instruction_body"
            frames[function] = int(size)
        assert "compile_wasm_trace" in frames and "compile_instruction_body" in frames
        return frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert python_code_lines('"""module doc"""\n# comment\nx = "# literal"\n') == 1
    assert cpp_code_lines('#define N 1 // comment\n/* comment */\nconst char* s = "//";\n') == 2
    groups = [
        source_group(
            name,
            tuple(
                path
                for path in (_PYSIM / name).rglob("*.py")
                if not path.is_relative_to(_REFERENCE_INTERPRETER)
            ),
        )
        for name in (
            "tier1_core",
            "tier1_interface",
            "tier2_runtime",
            "tier3_plugins",
            "tier3_platform",
        )
    ]
    groups.append(source_group("pysim_common", (_PYSIM / "system.py", _PYSIM / "__init__.py")))
    reference_interpreter = source_group(
        "reference_python_interpreter_excluded",
        tuple(_REFERENCE_INTERPRETER.rglob("*.py")),
    )
    for name, roots in (
        ("product_c_cpp", (_ROOT / "src", _ROOT / "inc")),
        ("native_reference_c_cpp", (_PYSIM / "native", _PYSIM / "tier2_runtime/abi")),
    ):
        paths = tuple(
            p
            for root in roots
            for p in root.rglob("*")
            if p.suffix in (".c", ".cxx", ".hxx", ".h", ".cpp", ".hpp")
        )
        groups.append(source_group(name, paths))
    libraries = tuple(
        elf_size(_PYSIM / path)
        for path in (
            "tier2_runtime/interpreter/libnative_interpreter.so",
            "tier3_plugins/jit/libtrace_compiler.so",
        )
    )
    stack_frames = compiler_stack_frames()
    report = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True
        ).strip(),
        "source_worktree_modified": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=_ROOT, text=True).strip()
        ),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "host_machine": platform.machine(),
        "pygments_version": pygments.__version__,
        "compiler": subprocess.check_output(["clang++", "--version"], text=True).splitlines()[0],
        "sloc_definition": "physical nonblank code lines; comments and Python docstrings excluded; preprocessor directives included",
        "source_excluded": [
            "QA/tests",
            "benchmarks",
            "scenarios",
            "third_party/",
            "main.py",
            "aobench.py",
            "experiments/pysim/tier2_runtime/interpreter/*.py (reference only)",
        ],
        "reference_python_interpreter_excluded": asdict(reference_interpreter),
        "elf_scope": "x64 reference shared libraries built by checked-in -O2 build scripts; not ARM firmware",
        "source_groups": [asdict(group) for group in groups],
        "native_libraries": [asdict(library) for library in libraries],
        "jit_compiler_stack": {
            "measurement_flags": "-std=c++23 -O2 -fPIC -fstack-usage with checked-in JIT configuration",
            "source_sha256": hashlib.sha256(
                (_PYSIM / "native/tier3_plugins/jit/trace_compiler.cxx").read_bytes()
            ).hexdigest(),
            "function_frame_bytes": stack_frames,
            "entry_frames_subtotal_bytes": stack_frames["compile_wasm_trace"]
            + stack_frames["compile_instruction_body"],
            "caller_output_buffer_bytes": 8192,
            "minimum_known_compile_bytes": stack_frames["compile_wasm_trace"]
            + stack_frames["compile_instruction_body"]
            + 8192,
            "excluded": "additional callee/ABI frames, result structures and Python objects",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for group in groups:
        print(
            f"{group.name}: files={len(group.files)}, physical={group.physical_lines}, code={group.code_lines}"
        )
    print(f"native text+rodata: {sum(lib.text_rodata_bytes for lib in libraries)} bytes")


if __name__ == "__main__":
    main()

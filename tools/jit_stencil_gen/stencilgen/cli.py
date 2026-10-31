"""Command-line driver: assemble one role's source and write its binary and header."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .branch_records import read_branch_records
from .build_inputs import compile_abi_probe, optimized_assembly_source, repo_root, role_definitions
from .elf_reader import read_object
from .emit_outputs import emit_common_header, emit_stencil_header
from .hole_table import collect_stencils
from .stencil_model import GenerationError
from .toolchain import command_output, find_tool


def generate(args: argparse.Namespace) -> None:
    clang = find_tool(args.clang, ("clang",))
    clangxx = find_tool(args.clangxx, ("clang++",))
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    header = (output_dir / args.header).resolve()
    header.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="jit-stencil-") as temp_name:
        directory = Path(temp_name)
        abi = compile_abi_probe(clangxx, args.target, directory)
        object_path = directory / f"{source.stem}.o"
        config_path = args.config_source or (repo_root() / "experiments/pysim/tier1_core/config.py")
        definitions = role_definitions(args.role, abi, config_path)
        assembly_source = optimized_assembly_source(args.role, source, directory)
        compile_command = [clang, "-c", "-fPIC", str(assembly_source), "-o", str(object_path)]
        compile_command.append(f"-I{source.parent}")
        if args.target is not None:
            compile_command.extend(("--target", args.target))
        for name, value in definitions.items():
            compile_command.append(f"-D{name}={value}")
        command_output(compile_command, f"{args.role} assembly compilation")
        raw, symbols = read_object(object_path)

        if args.assembly_output is not None:
            args.assembly_output.resolve().parent.mkdir(parents=True, exist_ok=True)
            preprocess_command = [
                clang,
                "-E",
                "-P",
                "-x",
                "assembler-with-cpp",
                str(assembly_source),
            ]
            preprocess_command.append(f"-I{source.parent}")
            if args.target is not None:
                preprocess_command.extend(("--target", args.target))
            preprocess_command.extend(f"-D{name}={value}" for name, value in definitions.items())
            preprocessed = command_output(preprocess_command, "assembly preprocessing")
            args.assembly_output.resolve().write_text(preprocessed, encoding="utf-8")
        (output_dir / args.binary).write_bytes(raw)

        if args.role == "common":
            emit_common_header(header, raw, symbols)
        else:
            branches = read_branch_records(object_path, symbols)
            stencils, arrays = collect_stencils(args.role, symbols, branches)
            emit_stencil_header(args.namespace, header, raw, stencils, arrays)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble x64 JIT LIR and emit its raw code and C++ metadata."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--role", choices=("operations", "trace", "common"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--header", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--namespace", default="generated")
    parser.add_argument("--assembly-output", type=Path)
    parser.add_argument("--config-source", type=Path)
    parser.add_argument("--clang")
    parser.add_argument("--clangxx")
    parser.add_argument("--target")
    args = parser.parse_args()
    try:
        generate(args)
    except (GenerationError, OSError, ValueError) as error:
        print(f"jit-stencil-gen: {error}", file=sys.stderr)
        return 1
    return 0

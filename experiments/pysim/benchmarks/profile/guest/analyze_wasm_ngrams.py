"""Compile the Clang WASM profile workload and report instruction n-grams."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

SOURCE = Path(__file__).with_name("suite.c")
FUNCTION_HEADER = re.compile(r"^\s*[0-9a-f]+ func\[\d+\]")
DISASSEMBLED_INSTRUCTION = re.compile(r"^\s*[0-9a-f]+:.*\|\s+([a-z0-9_.]+)(?:\s+(.*))?$")
INTEGER_BINARY_OPS = (
    "i32.add",
    "i32.sub",
    "i32.mul",
    "i32.and",
    "i32.or",
    "i32.xor",
    "i32.shl",
    "i32.shr_s",
    "i32.shr_u",
)


def read_functions(wasm_path: Path, wasm_objdump: str) -> list[list[tuple[str, str]]]:
    disassembly = subprocess.run(
        [wasm_objdump, "-d", str(wasm_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    functions: list[list[tuple[str, str]]] = []
    current_function: list[tuple[str, str]] | None = None
    for line in disassembly.splitlines():
        if FUNCTION_HEADER.match(line):
            current_function = []
            functions.append(current_function)
            continue
        if current_function is None:
            continue
        instruction = DISASSEMBLED_INSTRUCTION.match(line)
        if instruction is not None:
            current_function.append((instruction.group(1), instruction.group(2) or ""))
    return functions


def signed_i32(operand: str) -> int:
    value = int(operand.strip().split()[0], 0) & 0xFFFF_FFFF
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value


def print_report(functions: list[list[tuple[str, str]]], top_count: int) -> None:
    instruction_count = sum(len(function) for function in functions)
    print(f"functions={len(functions)} instructions={instruction_count}")

    for width in (2, 3, 4):
        ngrams: Counter[tuple[str, ...]] = Counter()
        for function in functions:
            opcodes = [opcode for opcode, _ in function]
            ngrams.update(
                tuple(opcodes[index : index + width]) for index in range(len(opcodes) - width + 1)
            )
        print(f"\ntop {width}-grams")
        for ngram, frequency in ngrams.most_common(top_count):
            print(f"{frequency:4}  {' '.join(ngram)}")

    print("\ni32.const followed by an integer operation")
    for operation in INTEGER_BINARY_OPS:
        immediate_frequencies: Counter[int] = Counter()
        for function in functions:
            for index in range(len(function) - 1):
                if function[index][0] == "i32.const" and function[index + 1][0] == operation:
                    immediate_frequencies[signed_i32(function[index][1])] += 1
        if immediate_frequencies:
            common = ", ".join(
                f"{value}:{frequency}"
                for value, frequency in immediate_frequencies.most_common(top_count)
            )
            print(f"{operation:9} count={sum(immediate_frequencies.values()):4}  {common}")

    print("\nlocal.get + i32.const + integer operation")
    for operation in INTEGER_BINARY_OPS:
        immediate_frequencies = Counter()
        for function in functions:
            for index in range(len(function) - 2):
                if (
                    function[index][0] == "local.get"
                    and function[index + 1][0] == "i32.const"
                    and function[index + 2][0] == operation
                ):
                    immediate_frequencies[signed_i32(function[index + 1][1])] += 1
        if immediate_frequencies:
            common = ", ".join(
                f"{value}:{frequency}"
                for value, frequency in immediate_frequencies.most_common(top_count)
            )
            print(f"{operation:9} count={sum(immediate_frequencies.values()):4}  {common}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wasm", type=Path, help="analyze this WASM module instead of rebuilding suite.c"
    )
    parser.add_argument("--clang", default=os.environ.get("CLANG", "clang"))
    parser.add_argument("--wasm-objdump", default=os.environ.get("WASM_OBJDUMP", "wasm-objdump"))
    parser.add_argument("--top", type=int, default=20)
    arguments = parser.parse_args()
    if arguments.top < 1:
        parser.error("--top must be positive")

    if arguments.wasm is not None:
        functions = read_functions(arguments.wasm, arguments.wasm_objdump)
        print(f"module={arguments.wasm}")
        print_report(functions, arguments.top)
        return

    with tempfile.TemporaryDirectory(prefix="fireball-wasm-ngrams-") as temporary_directory:
        wasm_path = Path(temporary_directory) / "suite.wasm"
        subprocess.run(
            [
                arguments.clang,
                "--target=wasm32",
                "-std=c23",
                "-mcpu=mvp",
                "-O2",
                "-ffreestanding",
                "-fno-builtin",
                "-nostdlib",
                "-Wl,--no-entry",
                "-Wl,--strip-all",
                "-Wl,-z,stack-size=4096",
                "-Wl,--initial-memory=65536",
                "-Wl,--max-memory=65536",
                "-o",
                str(wasm_path),
                str(SOURCE),
            ],
            check=True,
        )
        compiler_version = subprocess.run(
            [arguments.clang, "--version"], check=True, capture_output=True, text=True
        ).stdout.splitlines()[0]
        print(f"source={SOURCE}")
        print(f"compiler={compiler_version}")
        print(f"module=Clang-generated {wasm_path.name}")
        print_report(read_functions(wasm_path, arguments.wasm_objdump), arguments.top)


if __name__ == "__main__":
    main()

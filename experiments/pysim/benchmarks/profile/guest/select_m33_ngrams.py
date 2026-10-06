"""Rank WASM immediate supernodes by a Clang Cortex-M33 instruction-count probe.

This is a candidate filter, not an ARM JIT implementation. The current JIT emits x64;
the probe estimates a fused local/constant/operator body against separately lowered C
operations. Its scores must be replaced with counts from real ARM stencil emission once
that backend and its register/stack contract are defined.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import analyze_wasm_ngrams as wasm_ngrams

SOURCE = Path(__file__).with_name("suite.c")
TARGET_FLAGS = (
    "--target=armv8m.main-none-eabi",
    "-mcpu=cortex-m33",
    "-mthumb",
    "-mfloat-abi=soft",
    "-std=c23",
    "-O2",
    "-ffreestanding",
    "-fno-builtin",
    "-fno-inline",
)
FUNCTION_LABEL = re.compile(r"^\s*[0-9a-f]+ <([^>]+)>:$")
MACHINE_INSTRUCTION = re.compile(
    r"^\s*[0-9a-f]+:\s+((?:[0-9a-f]{4}\s+)+)([a-z][a-z0-9.]*)\b",
    re.IGNORECASE,
)
RETURN_MNEMONICS = frozenset(("bx", "pop"))


@dataclass(frozen=True)
class ProbeCost:
    instructions: int
    text_bytes: int


@dataclass(frozen=True)
class Candidate:
    operation: str
    immediate: int
    frequency: int
    baseline_instructions: int
    fused_instructions: int
    fused_bytes: int

    @property
    def saved_instructions(self) -> int:
        return self.baseline_instructions - self.fused_instructions

    @property
    def total_saved_instructions(self) -> int:
        return self.frequency * self.saved_instructions


def operation_expression(operation: str, left: str, right: str) -> str:
    if operation == "i32.add":
        return f"{left} + {right}"
    if operation == "i32.sub":
        return f"{left} - {right}"
    if operation == "i32.mul":
        return f"{left} * {right}"
    if operation == "i32.and":
        return f"{left} & {right}"
    if operation == "i32.or":
        return f"{left} | {right}"
    if operation == "i32.xor":
        return f"{left} ^ {right}"
    if operation == "i32.shl":
        return f"{left} << ({right} & 31u)"
    if operation == "i32.shr_u":
        return f"{left} >> ({right} & 31u)"
    if operation == "i32.shr_s":
        return f"(uint32_t)((int32_t){left} >> ({right} & 31u))"
    raise ValueError(f"unsupported i32 operation: {operation}")


def collect_candidates(
    functions: list[list[tuple[str, str]]],
) -> Counter[tuple[str, int]]:
    frequencies: Counter[tuple[str, int]] = Counter()
    for function in functions:
        for index in range(len(function) - 2):
            if function[index][0] != "local.get" or function[index + 1][0] != "i32.const":
                continue
            operation = function[index + 2][0]
            if operation in wasm_ngrams.INTEGER_BINARY_OPS:
                frequencies[(operation, wasm_ngrams.signed_i32(function[index + 1][1]))] += 1
    return frequencies


def make_probe_source(candidates: tuple[tuple[str, int], ...]) -> str:
    lines = [
        "#include <stdint.h>",
        "__attribute__((noinline)) uint32_t base_local(const uint32_t *p) { return p[0]; }",
    ]
    for index, (operation, immediate) in enumerate(candidates):
        value = immediate & 0xFFFF_FFFF
        lines.append(
            f"__attribute__((noinline)) uint32_t base_const_{index}(void) "
            f"{{ return UINT32_C({value}); }}"
        )
        expression = operation_expression(operation, "left", "right")
        lines.append(
            f"__attribute__((noinline)) uint32_t base_op_{index}(uint32_t left, uint32_t right) "
            f"{{ return {expression}; }}"
        )
        fused_expression = operation_expression(operation, "p[0]", f"UINT32_C({value})")
        lines.append(
            f"__attribute__((noinline)) uint32_t fused_{index}(const uint32_t *p) "
            f"{{ return {fused_expression}; }}"
        )
    return "\n".join(lines) + "\n"


def read_function_costs(disassembly: str) -> dict[str, ProbeCost]:
    costs: dict[str, ProbeCost] = {}
    current_name: str | None = None
    body_instructions = 0
    body_bytes = 0
    body_ended = False

    def finish_function() -> None:
        if current_name is not None:
            assert body_ended, f"probe function {current_name} has no recognized return"
            costs[current_name] = ProbeCost(body_instructions, body_bytes)

    for line in disassembly.splitlines():
        label = FUNCTION_LABEL.match(line)
        if label is not None:
            finish_function()
            current_name = label.group(1)
            body_instructions = 0
            body_bytes = 0
            body_ended = False
            continue
        if current_name is None or body_ended:
            continue
        instruction = MACHINE_INSTRUCTION.match(line)
        if instruction is None:
            continue
        encoded_halfwords = instruction.group(1).split()
        mnemonic = instruction.group(2).lower()
        if mnemonic in RETURN_MNEMONICS:
            body_ended = True
            continue
        body_instructions += 1
        body_bytes += 2 * len(encoded_halfwords)

    finish_function()
    return costs


def compile_wasm(clang: str, wasm_path: Path) -> None:
    subprocess.run(
        [
            clang,
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


def find_llvm_objdump(clang: str, requested: str) -> str:
    found = shutil.which(requested)
    if found is not None:
        return found
    discovered = subprocess.run(
        [clang, "-print-prog-name=llvm-objdump"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if Path(discovered).is_file():
        return discovered
    raise FileNotFoundError(f"llvm-objdump not found: {requested}")


def compile_probe(
    clang: str,
    llvm_objdump: str,
    candidates: tuple[tuple[str, int], ...],
    temporary_directory: Path,
) -> dict[str, ProbeCost]:
    source = temporary_directory / "m33_probe.c"
    object_file = temporary_directory / "m33_probe.o"
    source.write_text(make_probe_source(candidates))
    subprocess.run([clang, *TARGET_FLAGS, "-c", str(source), "-o", str(object_file)], check=True)
    disassembly = subprocess.run(
        [llvm_objdump, "-d", str(object_file)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return read_function_costs(disassembly)


def rank_candidates(
    frequencies: Counter[tuple[str, int]],
    costs: dict[str, ProbeCost],
    probe_patterns: tuple[tuple[str, int], ...],
) -> tuple[Candidate, ...]:
    ranked: list[Candidate] = []
    local_cost = costs["base_local"].instructions
    for index, (operation, immediate) in enumerate(probe_patterns):
        frequency = frequencies[(operation, immediate)]
        baseline_instructions = (
            local_cost
            + costs[f"base_const_{index}"].instructions
            + costs[f"base_op_{index}"].instructions
        )
        fused = costs[f"fused_{index}"]
        ranked.append(
            Candidate(
                operation=operation,
                immediate=immediate,
                frequency=frequency,
                baseline_instructions=baseline_instructions,
                fused_instructions=fused.instructions,
                fused_bytes=fused.text_bytes,
            )
        )
    return tuple(
        sorted(
            ranked,
            key=lambda candidate: (
                candidate.total_saved_instructions,
                candidate.saved_instructions,
                candidate.frequency,
                candidate.operation,
                -candidate.immediate,
            ),
            reverse=True,
        )
    )


def choose_candidates(
    candidates: tuple[Candidate, ...], rom_budget_bytes: int | None
) -> tuple[Candidate, ...]:
    profitable = tuple(candidate for candidate in candidates if candidate.saved_instructions > 0)
    if rom_budget_bytes is None:
        return profitable
    selected: list[Candidate] = []
    used_bytes = 0
    for candidate in sorted(
        profitable,
        key=lambda item: (
            item.total_saved_instructions / max(item.fused_bytes, 1),
            item.total_saved_instructions,
            item.operation,
            -item.immediate,
        ),
        reverse=True,
    ):
        if used_bytes + candidate.fused_bytes <= rom_budget_bytes:
            selected.append(candidate)
            used_bytes += candidate.fused_bytes
    return tuple(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wasm", type=Path, help="analyze this module instead of rebuilding suite.c"
    )
    parser.add_argument("--clang", default=os.environ.get("CLANG", "clang"))
    parser.add_argument("--wasm-objdump", default=os.environ.get("WASM_OBJDUMP", "wasm-objdump"))
    parser.add_argument("--llvm-objdump", default=os.environ.get("LLVM_OBJDUMP", "llvm-objdump"))
    parser.add_argument(
        "--top", type=int, help="probe only this many frequent patterns (default: all)"
    )
    parser.add_argument(
        "--rom-budget-bytes",
        type=int,
        help="greedily select profitable fused bodies within this code-size budget",
    )
    arguments = parser.parse_args()
    if arguments.top is not None and arguments.top < 1:
        parser.error("--top must be positive")
    if arguments.rom_budget_bytes is not None and arguments.rom_budget_bytes < 0:
        parser.error("--rom-budget-bytes must be nonnegative")

    llvm_objdump = find_llvm_objdump(arguments.clang, arguments.llvm_objdump)
    compiler_version = subprocess.run(
        [arguments.clang, "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    with tempfile.TemporaryDirectory(prefix="fireball-m33-ngrams-") as directory:
        temporary_directory = Path(directory)
        wasm_path = arguments.wasm or temporary_directory / "suite.wasm"
        if arguments.wasm is None:
            compile_wasm(arguments.clang, wasm_path)
        frequencies = collect_candidates(
            wasm_ngrams.read_functions(wasm_path, arguments.wasm_objdump)
        )
        selected_frequencies = Counter(
            dict(frequencies.most_common(arguments.top))
            if arguments.top is not None
            else frequencies
        )
        if not selected_frequencies:
            print(f"module={wasm_path}: no local.get + i32.const + integer-op candidates")
            return
        probe_patterns = tuple(key for key, _ in selected_frequencies.most_common())
        costs = compile_probe(
            arguments.clang,
            llvm_objdump,
            probe_patterns,
            temporary_directory,
        )
        ranked = rank_candidates(selected_frequencies, costs, probe_patterns)
        selected = choose_candidates(ranked, arguments.rom_budget_bytes)

    print("target=armv8m.main-none-eabi cpu=cortex-m33 thumb soft-float clang=-O2")
    print(f"compiler={compiler_version}")
    module_label = str(arguments.wasm) if arguments.wasm is not None else f"Clang-built {SOURCE}"
    print(f"module={module_label}")
    print(f"observed_patterns={len(frequencies)} probed_patterns={len(selected_frequencies)}")
    print("cost_model=Clang C probe; estimate only until an ARM JIT stencil backend exists")
    print(
        "operation immediate static_freq base_ins fused_ins saved/occ weighted_saved "
        "probe_body_bytes selected"
    )
    selected_set = {(item.operation, item.immediate) for item in selected}
    for candidate in ranked:
        print(
            f"{candidate.operation:9} {candidate.immediate:9d} {candidate.frequency:5d} "
            f"{candidate.baseline_instructions:8d} {candidate.fused_instructions:9d} "
            f"{candidate.saved_instructions:9d} {candidate.total_saved_instructions:11d} "
            f"{candidate.fused_bytes:16d} "
            f"{'yes' if (candidate.operation, candidate.immediate) in selected_set else 'no'}"
        )
    print(
        f"selected_patterns={len(selected)} "
        f"estimated_static_instruction_savings="
        f"{sum(candidate.total_saved_instructions for candidate in selected)} "
        f"selected_probe_body_bytes={sum(candidate.fused_bytes for candidate in selected)}"
    )


if __name__ == "__main__":
    main()

"""Measure generated x64 stencil bytes and trace composition latency."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path(__file__).resolve().parent
BENCH = ROOT / "experiments/pysim/benchmarks"
GENERATOR = ROOT / "tools/jit_stencil_gen/generate_assembly.py"
LIR = ROOT / "experiments/pysim/native/tier3_plugins/jit/lir/x64"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(
    label: str,
) -> dict[str, str | int | float | list[dict[str, str | int | float]]]:
    output = Path("/tmp") / f"fireball-jit-stencils-{label}"
    output.mkdir(parents=True, exist_ok=True)
    roles = (
        ("trace", LIR / "trace.S", "stencils.hxx", "stencils.bin", "generated"),
        (
            "operations",
            LIR / "operations.S",
            "asm_stencils.hxx",
            "asm_stencils.bin",
            "asm_generated",
        ),
        ("common", LIR / "common.S", "common_code.hxx", "common_code.bin", "generated"),
    )
    rows: list[dict[str, str | int | float]] = []
    for role, source, header, binary, namespace in roles:
        command = [
            str(ROOT / ".venv/bin/python"),
            str(GENERATOR),
            str(source),
            "--role",
            role,
            "--output-dir",
            str(output),
            "--header",
            header,
            "--binary",
            binary,
            "--namespace",
            namespace,
        ]
        started = time.perf_counter()
        subprocess.run(command, cwd=ROOT, check=True)
        elapsed = time.perf_counter() - started
        path = output / binary
        rows.append(
            {
                "role": role,
                "command": command,
                "elapsed_ms": elapsed * 1000.0,
                "text_bytes": path.stat().st_size,
                "text_sha256": sha256(path),
                "input_sha256": sha256(source),
            }
        )
    return {
        "output_directory": str(output),
        "roles": rows,
        "total_text_bytes": sum(int(row["text_bytes"]) for row in rows),
    }


def measure_compile_latency() -> dict[str, float | int | str]:
    assert os.sched_getaffinity(0) == {2}
    sys.path.insert(0, str(BENCH))
    from _bootstrap import configure_import_paths

    configure_import_paths(ROOT / "experiments/pysim", BENCH)
    import tier2_runtime.wasm.opcodes as op
    from bench_jit import _extract_benchmark_blocks
    from qa.shared.x64_jit import TraceCompiler
    from tier2_runtime.interpreter.control_flow import iter_block_ops
    from tier2_runtime.wasm.module import I32, LocalLayout

    code = bytes([op.LOCAL_GET, 0, op.I32_CONST, 1, op.I32_ADD, op.LOCAL_SET, 0])
    head_pc, next_pc, loops_to, _frame_depth, byte_span = _extract_benchmark_blocks(code)[0]
    compiler = TraceCompiler()
    count = 10_000

    def compile_one() -> None:
        trace = compiler.compile_instructions(
            head_pc=head_pc,
            instructions=iter_block_ops(code, head_pc, byte_span),
            next_pc=next_pc,
            loops_to=loops_to,
            byte_length=byte_span,
            local_layout=LocalLayout((I32,)),
        )
        assert trace is not None

    for _ in range(100):
        compile_one()
    started = time.perf_counter()
    for _ in range(count):
        compile_one()
    elapsed = time.perf_counter() - started
    return {
        "fixture": "local.get 0; i32.const 1; i32.add; local.set 0",
        "trace_count": count,
        "elapsed_ms": elapsed * 1000.0,
        "us_per_trace": elapsed * 1_000_000.0 / count,
        "traces_per_second": count / elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=("before", "after"), required=True)
    parser.add_argument("--trial", type=int, required=True)
    args = parser.parse_args()
    result = {
        "label": args.label,
        "trial": args.trial,
        "compiler": subprocess.check_output(["clang++", "--version"], text=True).splitlines()[0],
        "stencils": generate(args.label),
        "trace_composition": measure_compile_latency(),
    }
    destination = RESULTS / f"jit_compiler_{args.label}_{args.trial}.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

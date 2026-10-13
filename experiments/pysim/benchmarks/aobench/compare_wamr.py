"""Compare release Fireball engines with a pinned WAMR Fast Interpreter build."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

_ROOT = Path(__file__).resolve().parents[4]
_BENCH = Path(__file__).resolve().parent
_ENGINES = ("interpreter", "jit", "wamr")


class ProcessMeasurement(TypedDict):
    engine: str
    width: int
    height: int
    warmup: int
    repeats: int
    samples_ms: list[float]
    median_ms: float
    frame_bytes: int
    frame_sha256: str
    wasm_sha256: str
    trial: int


class ResolutionMeasurement(TypedDict):
    width: int
    height: int
    processes: list[ProcessMeasurement]
    median_ms: dict[str, float]
    process_median_range_ms: dict[str, list[float]]
    jit_interpreter_ratio: float
    interpreter_wamr_ratio: float
    jit_wamr_ratio: float
    frame_sha256: str


class ComparisonReport(TypedDict):
    started_at: str
    cpu: str
    cpu_affinity: int
    python: str
    platform: str
    trials: int
    warmup: int
    repeats: int
    compiler: str
    native_flags: list[str]
    wamr_commit: str
    wamr_build_options: dict[str, str]
    wamr_compile_definitions: str
    wamr_compile_flags: str
    wamr_link_command: str
    source_sha256: dict[str, str]
    library_sha256: dict[str, str]
    timing: str
    host_io: str
    resolutions: list[ResolutionMeasurement]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(build_dir: Path, output: Path, cpu: int, trials: int) -> None:
    """Rotate engine order, verify every frame, and retain every process sample."""
    assert trials > 0 and cpu >= 0
    build_dir = build_dir.resolve()
    library = build_dir / "libiwasm.so"
    cache_lines = (build_dir / "CMakeCache.txt").read_text().splitlines()
    build_options = {
        line.split(":", 1)[0]: line.split("=", 1)[1]
        for line in cache_lines
        if line and not line.startswith(("#", "//")) and ":" in line and "=" in line
    }
    for name, value in {
        "WAMR_BUILD_INTERP": "1",
        "WAMR_BUILD_FAST_INTERP": "1",
        "WAMR_BUILD_AOT": "0",
        "WAMR_BUILD_JIT": "0",
        "WAMR_BUILD_FAST_JIT": "0",
        "WAMR_BUILD_LIBC_WASI": "1",
        "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_C_FLAGS_RELEASE": "-O3 -DNDEBUG -flto=thin",
    }.items():
        assert build_options[name] == value, (name, build_options[name])
    source_paths = sorted(
        path
        for tree in ("native", "tier2_runtime", "tier3_plugins", "tier1_core")
        for path in (_ROOT / "experiments/pysim" / tree).rglob("*")
        if path.suffix in (".py", ".cxx", ".hxx", ".S", ".sh")
    )
    source_paths.extend(
        (_BENCH / name)
        for name in ("bench_native_build.py", "bench_wamr.py", "compare_wamr.py", "aobench.wasm")
    )
    source_paths.extend(
        _ROOT / name
        for name in (
            "tools/jit_stencil_gen/generate_assembly.py",
            "tools/jit_stencil_gen/abi_probe.cxx",
        )
    )
    source_hashes = {str(p.relative_to(_ROOT)): _file_hash(p) for p in source_paths}
    library_paths = (
        _ROOT / "experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so",
        _ROOT / "experiments/pysim/tier3_plugins/jit/libtrace_compiler.so",
        library,
    )
    library_hashes = {str(p): _file_hash(p) for p in library_paths}
    cpu_model = next(
        line.split(":", 1)[1].strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith("model name")
    )
    compile_flags = (build_dir / "CMakeFiles/vmlib.dir/flags.make").read_text().splitlines()
    report: ComparisonReport = {
        "started_at": datetime.now().astimezone().isoformat(),
        "cpu": cpu_model,
        "cpu_affinity": cpu,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "trials": trials,
        "warmup": 3,
        "repeats": 7,
        "compiler": subprocess.check_output(["clang", "--version"], text=True).splitlines()[0],
        "native_flags": ["-O3", "-DNDEBUG", "-flto=thin", "-fuse-ld=lld"],
        "wamr_commit": subprocess.check_output(
            ["git", "-C", str(_ROOT / "third_party/wasm-micro-runtime"), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "wamr_build_options": {
            key: value
            for key, value in build_options.items()
            if key.startswith("WAMR_")
            or key
            in (
                "CMAKE_BUILD_TYPE",
                "CMAKE_C_FLAGS",
                "CMAKE_C_FLAGS_RELEASE",
                "CMAKE_C_COMPILER",
                "CMAKE_SHARED_LINKER_FLAGS",
                "BUILD_SHARED_LIBS",
            )
        },
        "source_sha256": source_hashes,
        "library_sha256": library_hashes,
        "wamr_compile_definitions": next(
            line for line in compile_flags if line.startswith("C_DEFINES =")
        ),
        "wamr_compile_flags": next(line for line in compile_flags if line.startswith("C_FLAGS =")),
        "wamr_link_command": (build_dir / "CMakeFiles/vmlib.dir/link.txt").read_text().strip(),
        "timing": "NativeInterpreter.call() or wasm_runtime_call_wasm(); no load/setup/hash",
        "host_io": "Fireball Python WASI/HAL/StreamTransport; WAMR C WASI/memfd",
        "resolutions": [],
    }
    for width in (64, 128, 192, 256, 320):
        rows: list[ProcessMeasurement] = []
        for trial in range(trials):
            order = _ENGINES[trial % 3 :] + _ENGINES[: trial % 3]
            for engine in order:
                worker = _BENCH / ("bench_wamr.py" if engine == "wamr" else "bench_native_build.py")
                options = ["--library", str(library)] if engine == "wamr" else ["--engine", engine]
                command = [
                    "taskset",
                    "-c",
                    str(cpu),
                    sys.executable,
                    str(worker),
                    *options,
                    "--width",
                    str(width),
                    "--warmup",
                    "3",
                    "--repeats",
                    "7",
                ]
                row = cast(
                    ProcessMeasurement, json.loads(subprocess.check_output(command, text=True))
                )
                assert row["engine"] == engine and row["width"] == width
                assert len(row["samples_ms"]) == 7 and row["frame_bytes"] == (width + 1) * (
                    width * 9 // 16
                )
                row["trial"] = trial + 1
                rows.append(row)
                print(
                    f"{width}x{row['height']} trial={trial + 1} {engine}: {row['median_ms']:.3f} ms",
                    flush=True,
                )
        assert len({row["frame_sha256"] for row in rows}) == 1
        assert len({row["wasm_sha256"] for row in rows}) == 1
        process_medians = {
            engine: [row["median_ms"] for row in rows if row["engine"] == engine]
            for engine in _ENGINES
        }
        medians = {engine: statistics.median(values) for engine, values in process_medians.items()}
        report["resolutions"].append(
            ResolutionMeasurement(
                width=width,
                height=width * 9 // 16,
                processes=rows,
                median_ms=medians,
                process_median_range_ms={
                    engine: [min(values), max(values)] for engine, values in process_medians.items()
                },
                jit_interpreter_ratio=medians["jit"] / medians["interpreter"],
                interpreter_wamr_ratio=medians["interpreter"] / medians["wamr"],
                jit_wamr_ratio=medians["jit"] / medians["wamr"],
                frame_sha256=rows[0]["frame_sha256"],
            )
        )
    assert source_hashes == {str(p.relative_to(_ROOT)): _file_hash(p) for p in source_paths}
    assert library_hashes == {str(p): _file_hash(p) for p in library_paths}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Saved: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wamr-build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    compare(args.wamr_build, args.output, args.cpu, args.trials)

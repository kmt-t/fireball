from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
wamr_build = (root / "build/wamr-benchmark").resolve()
bench = root / "experiments/pysim/benchmarks/aobench"
library = wamr_build / "libiwasm.so"
engines = ("interpreter", "jit", "wamr")
rows: list[dict[str, object]] = []

for trial in range(3):
    order = engines[trial:] + engines[:trial]
    for engine in order:
        worker = bench / ("bench_wamr.py" if engine == "wamr" else "bench_native_build.py")
        options = ["--library", str(library)] if engine == "wamr" else ["--engine", engine]
        command = [
            "taskset",
            "-c",
            "2",
            sys.executable,
            str(worker),
            *options,
            "--width",
            "32",
            "--warmup",
            "3",
            "--repeats",
            "7",
        ]
        row = json.loads(subprocess.check_output(command, text=True))
        assert row["engine"] == engine and row["width"] == 32
        assert len(row["samples_ms"]) == 7
        row["trial"] = trial + 1
        rows.append(row)

assert len({row["frame_sha256"] for row in rows}) == 1
assert len({row["wasm_sha256"] for row in rows}) == 1
medians = {
    engine: statistics.median([float(row["median_ms"]) for row in rows if row["engine"] == engine])
    for engine in engines
}
ranges = {
    engine: [
        min(float(row["median_ms"]) for row in rows if row["engine"] == engine),
        max(float(row["median_ms"]) for row in rows if row["engine"] == engine),
    ]
    for engine in engines
}
report = {
    "width": 32,
    "height": 16,
    "cpu": 2,
    "cpu_affinity": 2,
    "build": "release",
    "trials": 3,
    "warmup": 3,
    "repeats": 7,
    "median_ms": medians,
    "process_median_range_ms": ranges,
    "processes": rows,
    "frame_sha256": rows[0]["frame_sha256"],
    "wasm_sha256": rows[0]["wasm_sha256"],
    "timing": "NativeInterpreter.call() or wasm_runtime_call_wasm(); no load/setup/hash",
}
Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

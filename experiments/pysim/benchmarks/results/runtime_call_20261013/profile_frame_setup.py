"""Profile only runtime_call frame preparation with an equivalent DWARF build."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path(__file__).resolve().parent
OPS = ROOT / "experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx"
DISPATCH = ROOT / "experiments/pysim/native/tier2_runtime/interpreter/native_interpreter.cxx"
LIBRARY = ROOT / "experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so"
MEASURE = RESULTS.parent / "interpreter_cost_20261012/measure.py"
PROFILE = Path("/opt/AMDuProf_5.3-521/bin/AMDuProfCLI")
OLD_COPY = """  const auto *arguments = argument_base;
  auto *argument = arguments;
  for (std::uint32_t index = 0; index < target.param_count; ++index) {
    const auto offset = target.local_offsets[index];
    const auto width = target.local_sizes[index];
    for (std::uint32_t word = 0; word < width; ++word) {
      callee_local_base[offset + word] = *argument++;
    }
  }
"""
NEW_COPY = """  std::memcpy(callee_local_base, argument_base,
              target.param_packed_slot_count * sizeof(*callee_local_base));
"""
FLAGS = (
    "-std=c++23",
    "-O3",
    "-DNDEBUG",
    "-flto=thin",
    "-fuse-ld=lld",
    "-fvisibility=hidden",
    "-fno-exceptions",
    "-fno-rtti",
    "-fomit-frame-pointer",
    "-Wall",
    "-Wextra",
    "-Wpedantic",
    "-shared",
    "-fPIC",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_text(path: Path, output: Path) -> str:
    subprocess.run(["llvm-objcopy", "--dump-section", f".text={output}", str(path)], check=True)
    return digest(output.read_bytes())


def profile_samples(database: Path, symbol_library: Path) -> tuple[int, int, int, int]:
    data = symbol_library.read_bytes()
    program_header_offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size, count = struct.unpack_from("<HH", data, 54)
    executable_pages = []
    for index in range(count):
        kind, flags, _, address = struct.unpack_from(
            "<IIQQ", data, program_header_offset + index * entry_size
        )
        if kind == 1 and flags & 1:
            executable_pages.append(address & ~4095)
    assert len(executable_pages) == 1
    code_page = executable_pages[0]
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    event = '"event#0x200000000e000"'
    total = int(connection.execute(f"SELECT sum({event}) FROM UnifiedSampleSeries").fetchone()[0])
    rows = connection.execute(
        f"SELECT m.path, s.moduleRVA, sum(s.{event}) "
        "FROM UnifiedSampleSeries s JOIN Module m ON m.id=s.moduleId "
        "GROUP BY m.path,s.moduleRVA"
    ).fetchall()
    connection.close()
    native = [
        (int(address), int(samples))
        for path, address, samples in rows
        if Path(path).name == LIBRARY.name
    ]
    addresses = sorted({address for address, _ in native})
    symbols = json.loads(
        subprocess.check_output(
            [
                "llvm-symbolizer-21",
                f"--obj={symbol_library}",
                "--inlines",
                "--output-style=JSON",
                *(hex(address + code_page) for address in addresses),
            ],
            text=True,
            env={**os.environ, "DEBUGINFOD_URLS": ""},
        )
    )
    frame_address_samples: set[int] = set()
    call_address_samples: set[int] = set()
    sample_by_address = dict(native)
    for address, symbol_row in zip(addresses, symbols, strict=True):
        chain = symbol_row["Symbol"]
        for entry in chain:
            function = entry["FunctionName"]
            source = Path(entry["FileName"]).name
            line = int(entry["Line"])
            if source in {
                "native_wasm_operations.cxx",
                "fireball-runtime-call-before.cxx",
            } and function.startswith("bool fireball::wasm_runtime::runtime_call<"):
                call_address_samples.add(address)
                if 1597 <= line <= 1636:
                    frame_address_samples.add(address)
    frame = sum(sample_by_address[address] for address in frame_address_samples)
    call = sum(sample_by_address[address] for address in call_address_samples)
    return total, call, frame, len(native)


def main() -> None:
    original_ops = OPS.read_bytes()
    original_library = LIBRARY.read_bytes()
    after_ops = original_ops.decode()
    assert after_ops.count(NEW_COPY) == 1
    before_ops = after_ops.replace(NEW_COPY, OLD_COPY, 1)
    before_ops_path = Path("/tmp/fireball-runtime-call-before.cxx")
    symbol_dir = Path("/tmp/fireball-runtime-call-profile")
    symbol_dir.mkdir(exist_ok=True)
    before_ops_path.write_text(before_ops)
    rows = []
    try:
        for variant, ops_path in (("before", before_ops_path), ("after", OPS)):
            release_variant = symbol_dir / f"{variant}.release.so"
            symbols_variant = symbol_dir / f"{variant}.symbols.so"
            base = [
                "clang++",
                *FLAGS,
                "-I",
                str(OPS.parent),
                str(DISPATCH),
                str(ops_path),
            ]
            subprocess.run([*base, "-o", str(release_variant)], cwd=ROOT, check=True)
            subprocess.run([*base, "-g", "-o", str(symbols_variant)], cwd=ROOT, check=True)
            release_text = dump_text(release_variant, symbol_dir / f"{variant}.release.text")
            symbols_text = dump_text(symbols_variant, symbol_dir / f"{variant}.symbols.text")
            assert release_text == symbols_text
            if variant == "after":
                assert digest(original_library) == digest(LIBRARY.read_bytes())
                assert release_text == dump_text(LIBRARY, symbol_dir / "current.release.text")
            median = next(
                item["median_ms"]["interpreter"]
                for item in json.loads((RESULTS / f"aobench_{variant}.json").read_text())[
                    "resolutions"
                ]
                if item["width"] == 320
            )
            backup = symbol_dir / "installed-release.so"
            backup.write_bytes(original_library)
            shutil.copy2(symbols_variant, LIBRARY)
            try:
                for trial in range(1, 4):
                    raw = symbol_dir / f"profile_{variant}_retry_{trial}"
                    collect = [
                        str(PROFILE),
                        "collect",
                        "--config",
                        "hotspots",
                        "--timer-interval",
                        "1",
                        "--start-paused",
                        "-o",
                        str(raw),
                        "taskset",
                        "-c",
                        "2",
                        str(ROOT / ".venv/bin/python"),
                        str(MEASURE),
                        "--engine",
                        "interpreter",
                        "--width",
                        "320",
                        "--profile",
                    ]
                    subprocess.run(
                        collect,
                        cwd=ROOT,
                        check=True,
                        stdout=(RESULTS / f"profile_{variant}_{trial}.log").open("w"),
                        stderr=subprocess.STDOUT,
                    )
                    report = [
                        str(PROFILE),
                        "report",
                        "-i",
                        str(raw),
                        "--inline",
                        "--cutoff",
                        "0",
                        "--db",
                        "sqlite",
                    ]
                    subprocess.run(
                        report,
                        cwd=ROOT,
                        check=True,
                        stdout=(RESULTS / f"profile_{variant}_{trial}_report.log").open("w"),
                        stderr=subprocess.STDOUT,
                    )
                    total, call, frame, native_addresses = profile_samples(
                        raw / "cpu.db", symbols_variant
                    )
                    rows.append(
                        {
                            "variant": variant,
                            "trial": trial,
                            "total_samples": total,
                            "runtime_call_samples": call,
                            "frame_setup_samples": frame,
                            "native_sampled_addresses": native_addresses,
                            "frame_setup_share_percent": frame / total * 100,
                            "frame_setup_ms_per_call": median * frame / total,
                        }
                    )
            finally:
                LIBRARY.write_bytes(original_library)
                assert digest(LIBRARY.read_bytes()) == digest(original_library)
    finally:
        LIBRARY.write_bytes(original_library)
        assert digest(LIBRARY.read_bytes()) == digest(original_library)

    (RESULTS / "frame_setup_profile.json").write_text(
        json.dumps(
            {
                "source_sha256_after": digest(original_ops),
                "library_sha256_before": digest(original_library),
                "compiler": subprocess.check_output(
                    ["clang++", "--version"], text=True
                ).splitlines()[0],
                "flags": [*FLAGS, "-g for symbolization build only"],
                "profile_command_template": "AMDuProfCLI collect --config hotspots --timer-interval 1 --start-paused -o <raw> taskset -c 2 .venv/bin/python <measure.py> --engine interpreter --width 320 --profile",
                "frame_setup_source_lines": [1597, 1636],
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()

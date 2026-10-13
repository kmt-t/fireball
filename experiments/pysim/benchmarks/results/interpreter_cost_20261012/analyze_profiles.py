"""uProf のアドレス標本を DWARF の inline chain へ結び付ける。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import struct
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
TEMP = Path("/tmp/fireball-interpreter-cost-20261012")
CATEGORIES = (
    "block_search",
    "backing_block_index_search",
    "control_frame_stores",
    "control_handlers",
    "runtime_call",
    "call_frame_setup",
    "call_capacity_checks",
    "dispatch_outer",
    "jit_lookup",
)


def executable_page(path: Path) -> int:
    data = path.read_bytes()
    assert data[:5] == b"\x7fELF\x02"
    offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size, count = struct.unpack_from("<HH", data, 54)
    pages = []
    for index in range(count):
        kind, flags, _, address = struct.unpack_from("<IIQQ", data, offset + index * entry_size)
        if kind == 1 and flags & 1:
            pages.append(address & ~4095)
    assert len(pages) == 1
    return pages[0]


def categorize(symbols: list[dict]) -> set[str]:
    result: set[str] = set()
    for symbol in symbols:
        name = symbol["FunctionName"]
        line = symbol["Line"]
        file = Path(symbol["FileName"]).name
        if name.startswith("unsigned int fireball::interpreter::find_cached_block_index<"):
            result.add("block_search")
        if name.startswith("fb_native_find_block_index_folded("):
            result.add("backing_block_index_search")
        if name.startswith("fireball::wasm_runtime::runtime_block(") or name.startswith(
            "fireball::wasm_runtime::runtime_if("
        ):
            result.add("control_handlers")
        if name.startswith("bool fireball::wasm_runtime::runtime_call<"):
            result.add("runtime_call")
            if file == "native_wasm_operations.cxx" and 1573 <= line <= 1618:
                result.add("call_frame_setup")
            if file == "native_wasm_operations.cxx" and 1565 <= line <= 1571:
                result.add("call_capacity_checks")
        if file == "native_wasm_operations.cxx" and (1044 <= line <= 1046 or 1890 <= line <= 1892):
            result.add("control_frame_stores")
        if name.startswith("int fireball::interpreter::run_native_dispatch_abi<"):
            result.add("dispatch_outer")
        if name.startswith("fireball::jit_provider_adapter<void>::lookup_runtime_trace("):
            result.add("jit_lookup")
    return result


def main() -> None:
    binaries = {
        "libnative_interpreter.so": TEMP / "symbols_interpreter.so",
        "libtrace_compiler.so": TEMP / "baseline_jit.so",
    }
    offsets = {name: executable_page(path) for name, path in binaries.items()}
    sessions: dict[str, list[tuple[str, int, float, str]]] = {}
    totals: dict[str, float] = {}
    addresses: dict[str, set[int]] = {name: set() for name in binaries}
    for trial in range(1, 4):
        for width in (64, 128, 192, 256, 320):
            for engine in ("interpreter", "jit"):
                name = f"profile_{engine}_{width}_{trial}"
                db = TEMP / name / "cpu.db"
                assert db.exists(), db
                connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                event = '"event#0x200000000e000"'
                totals[name] = connection.execute(
                    f"SELECT sum({event}) FROM UnifiedSampleSeries"
                ).fetchone()[0]
                rows = connection.execute(
                    f"SELECT m.path, s.moduleRVA, sum(s.{event}), f.name "
                    "FROM UnifiedSampleSeries s JOIN Module m ON m.id=s.moduleId "
                    "JOIN Function f ON f.id=s.functionId GROUP BY m.path,s.moduleRVA,f.name"
                ).fetchall()
                connection.close()
                sessions[name] = rows
                for path, address, _, _ in rows:
                    module = Path(path).name
                    if module in binaries:
                        addresses[module].add(address)
    mappings = {}
    env = os.environ.copy()
    env["DEBUGINFOD_URLS"] = ""
    for module, path in binaries.items():
        ordered = sorted(addresses[module])
        command = ["llvm-symbolizer-21", f"--obj={path}", "--inlines", "--output-style=JSON"]
        output = subprocess.check_output(
            command + [hex(address + offsets[module]) for address in ordered],
            text=True,
            input="",
            env=env,
            timeout=60,
        )
        symbols = json.loads(output)
        assert len(symbols) == len(ordered)
        mappings[module] = dict(zip(ordered, [row["Symbol"] for row in symbols], strict=True))
    (OUT / "symbol_mapping.json").write_text(
        json.dumps(
            {
                module: {str(address): value for address, value in mapping.items()}
                for module, mapping in mappings.items()
            },
            indent=2,
        )
        + "\n"
    )
    summaries = []
    for name, rows in sessions.items():
        _, engine, width, trial = name.split("_")
        category_samples = dict.fromkeys(CATEGORIES, 0.0)
        unresolved = 0.0
        with (OUT / f"{name}_samples.csv").open("w") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "module",
                    "module_rva",
                    "elf_address",
                    "samples",
                    "categories",
                    "innermost_function",
                    "innermost_line",
                ]
            )
            for path, address, samples, reported_name in rows:
                module = Path(path).name
                if module not in mappings:
                    writer.writerow([module, address, "", samples, "", reported_name, ""])
                    continue
                symbols = mappings[module][address]
                categories = categorize(symbols)
                if not symbols[0]["FunctionName"]:
                    unresolved += samples
                for category in categories:
                    category_samples[category] += samples
                writer.writerow(
                    [
                        module,
                        address,
                        hex(address + offsets[module]),
                        samples,
                        ";".join(sorted(categories)),
                        symbols[0]["FunctionName"],
                        symbols[0]["Line"],
                    ]
                )
        summaries.append(
            {
                "engine": engine,
                "width": int(width),
                "trial": int(trial),
                "total_samples": totals[name],
                "sample_interval_ms": 1,
                "category_samples": category_samples,
                "unresolved_native_samples": unresolved,
            }
        )
    (OUT / "profile_summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    (OUT / "symbol_builds.json").write_text(
        json.dumps(
            {
                "executable_page_offsets": offsets,
                "symbol_library_sha256": {
                    name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in binaries.items()
                },
                "baseline_text_sha256": hashlib.sha256(
                    (TEMP / "baseline.text").read_bytes()
                ).hexdigest(),
                "symbols_text_sha256": hashlib.sha256(
                    (TEMP / "symbols.text").read_bytes()
                ).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()

"""採用する計時・計数・標本だけを集計し、比較可能な表を保存する。"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]


def read_measurements(mode: str, engine: str, width: int) -> list[dict]:
    return [
        json.loads((OUT / f"{mode}_{engine}_{width}_{trial}.json").read_text())
        for trial in (1, 2, 3)
    ]


def main() -> None:
    profiles = json.loads((OUT / "profile_summary.json").read_text())
    oracle = json.loads(
        (OUT.parent / "aobench_interpreter_jit_wamr_20261012_current_baseline.json").read_text()
    )
    oracle_hashes = {
        row["width"]: row["processes"][0]["frame_sha256"] for row in oracle["resolutions"]
    }
    summaries = []
    count_rows = []
    timing_rows = []
    cost_rows = []
    denominators = {
        "block_search": ("block_search_calls",),
        "backing_block_index_search": ("block_search_misses",),
        "control_frame_stores": ("control_block_loop_constructs", "control_if_constructs"),
        "control_handlers": ("runtime_block_loop_entries", "runtime_if_entries"),
        "runtime_call": ("runtime_call_entries",),
        "call_frame_setup": ("call_frame_constructs",),
        "call_capacity_checks": ("call_frame_constructs",),
        "dispatch_outer": ("dispatch_iterations",),
        "jit_lookup": ("jit_lookup_calls",),
    }
    for width in (64, 128, 192, 256, 320):
        for engine in ("interpreter", "jit"):
            modes = {
                mode: read_measurements(mode, engine, width)
                for mode in ("baseline", "diagnostic", "restored")
            }
            profiled = [
                next(
                    json.loads(line)
                    for line in (OUT / f"profile_{engine}_{width}_{trial}.log")
                    .read_text()
                    .splitlines()
                    if line.startswith("{")
                )
                for trial in (1, 2, 3)
            ]
            all_rows = [row for rows in modes.values() for row in rows] + profiled
            assert {row["frame_sha256"] for row in all_rows} == {oracle_hashes[width]}
            assert len({row["wasm_sha256"] for row in all_rows}) == 1
            assert all(
                row["affinity"] == [2]
                and row["yield_threshold"] == 4096
                and row["warmup"] == 3
                and row["repeats"] == 7
                for row in all_rows
            )
            assert (
                len(
                    {
                        row["interpreter_library_sha256"]
                        for mode in ("baseline", "restored")
                        for row in modes[mode]
                    }
                )
                == 1
            )
            assert (
                len(
                    {
                        row["jit_library_sha256"]
                        for mode in ("baseline", "restored")
                        for row in modes[mode]
                    }
                )
                == 1
            )
            medians = {
                mode: statistics.median(row["median_ms"] for row in rows)
                for mode, rows in modes.items()
            }
            normal = [row["median_ms"] for mode in ("baseline", "restored") for row in modes[mode]]
            reference = statistics.median(normal)
            frames = [frame for row in modes["diagnostic"] for frame in row["frame_counters"][3:]]
            assert len(frames) == 21
            for frame in frames:
                assert (
                    frame["block_search_calls"]
                    == frame["block_search_hits"] + frame["block_search_misses"]
                )
                assert frame["runtime_call_entries"] == frame["call_frame_constructs"] + 1
            counts = {
                key: {
                    "median": statistics.median(frame[key] for frame in frames),
                    "mean": statistics.mean(frame[key] for frame in frames),
                    "min": min(frame[key] for frame in frames),
                    "max": max(frame[key] for frame in frames),
                }
                for key in frames[0]
            }
            for key, values in counts.items():
                count_rows.append(
                    {
                        "engine": engine,
                        "width": width,
                        "height": width * 9 // 16,
                        "operation": key,
                        **values,
                    }
                )
            selected = [
                row for row in profiles if row["engine"] == engine and row["width"] == width
            ]
            total = sum(row["total_samples"] for row in selected)
            costs = {}
            for category, keys in denominators.items():
                samples = sum(row["category_samples"][category] for row in selected)
                share = samples / total
                iterations = sum(counts[key]["mean"] for key in keys)
                costs[category] = {
                    "samples": samples,
                    "total_samples": total,
                    "share_percent": share * 100,
                    "per_process_share_percent": [
                        row["category_samples"][category] / row["total_samples"] * 100
                        for row in selected
                    ],
                    "estimated_ms_per_frame": medians["baseline"] * share,
                    "estimated_ns_per_occurrence": medians["baseline"]
                    * share
                    * 1_000_000
                    / iterations
                    if iterations
                    else None,
                    "sampled_ms_per_frame": samples / 21,
                    "sampled_ns_per_occurrence": samples * 1_000_000 / (21 * iterations)
                    if iterations
                    else None,
                    "occurrences_mean": iterations,
                    "occurrence_counter_keys": keys,
                }
                cost_rows.append(
                    {
                        "engine": engine,
                        "width": width,
                        "height": width * 9 // 16,
                        "operation": category,
                        **{
                            k: v
                            for k, v in costs[category].items()
                            if k not in ("per_process_share_percent", "occurrence_counter_keys")
                        },
                    }
                )
            timing = {
                "engine": engine,
                "width": width,
                "height": width * 9 // 16,
                "baseline_ms": medians["baseline"],
                "diagnostic_ms": medians["diagnostic"],
                "restored_ms": medians["restored"],
                "normal_reference_ms": reference,
                "diagnostic_net_difference_percent": (medians["diagnostic"] / reference - 1) * 100,
                "normal_process_median_min_ms": min(normal),
                "normal_process_median_max_ms": max(normal),
                "diagnostic_process_median_min_ms": min(
                    row["median_ms"] for row in modes["diagnostic"]
                ),
                "diagnostic_process_median_max_ms": max(
                    row["median_ms"] for row in modes["diagnostic"]
                ),
                "profiled_ms": statistics.median(row["median_ms"] for row in profiled),
                "profiled_net_difference_percent": (
                    statistics.median(row["median_ms"] for row in profiled) / reference - 1
                )
                * 100,
                "frame_sha256": oracle_hashes[width],
            }
            timing_rows.append(timing)
            summaries.append({**timing, "counts": counts, "costs": costs})
    (OUT / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    for name, rows in (
        ("counts.csv", count_rows),
        ("timing.csv", timing_rows),
        ("costs.csv", cost_rows),
    ):
        with (OUT / name).open("w") as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    print(
        "120 independent processes validated; 840 timed calls; output hashes match stored WAMR reference"
    )


if __name__ == "__main__":
    main()

"""Warn about staged changes that need a smaller review unit."""

from __future__ import annotations

import argparse
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-files", type=int, default=50)
    parser.add_argument("--max-lines", type=int, default=2000)
    args = parser.parse_args()
    rows = subprocess.check_output(
        ["git", "diff", "--cached", "--numstat", "--no-renames"], text=True
    ).splitlines()
    lines = 0
    artifacts = 0
    for row in rows:
        added, removed, path = row.split("\t", 2)
        lines += sum(int(value) for value in (added, removed) if value != "-")
        artifacts += int(path.startswith("experiments/pysim/benchmarks/results/"))
    print(f"Staged scope: {len(rows)} files, {lines} changed text lines, {artifacts} result files")
    if len(rows) > args.max_files or lines > args.max_lines:
        print("WARNING: split responsibilities or describe file and line counts in the commit.")
    if artifacts and artifacts != len(rows):
        print("WARNING: commit benchmark result evidence separately from code.")
    if artifacts:
        print("WARNING: document why tracked benchmark evidence is necessary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

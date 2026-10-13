"""CPU 2 で独立プロセスを順次起動し、出力とコマンドを保存する。"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "diagnostic", "restored"), required=True)
    args = parser.parse_args()
    for trial in range(1, 4):
        for width in (64, 128, 192, 256, 320):
            for engine in ("interpreter", "jit"):
                command = [
                    "taskset",
                    "-c",
                    "2",
                    str(ROOT / ".venv/bin/python"),
                    str(OUT / "measure.py"),
                    "--engine",
                    engine,
                    "--width",
                    str(width),
                ]
                if args.mode == "diagnostic":
                    command.append("--diagnostic")
                completed = subprocess.run(command, check=True, capture_output=True, text=True)
                row = json.loads(completed.stdout)
                row["command"] = command
                row["trial"] = trial
                row["mode"] = args.mode
                (OUT / f"{args.mode}_{engine}_{width}_{trial}.json").write_text(
                    json.dumps(row, indent=2) + "\n"
                )
                print(
                    f"{args.mode} {engine} {width} trial={trial}: {row['median_ms']:.3f} ms",
                    flush=True,
                )


if __name__ == "__main__":
    main()

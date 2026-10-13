"""計数なしで七つの呼び出しだけを AMD uProf にサンプリングさせる。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[5]
TEMP = Path("/tmp/fireball-contracts-v3-step1")


def main() -> None:
    for trial in range(1, 4):
        for width in (64, 128, 192, 256, 320):
            for engine in ("interpreter", "jit"):
                name = f"profile_{engine}_{width}_{trial}"
                raw = TEMP / name
                command = [
                    "AMDuProfCLI",
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
                    str(OUT / "measure.py"),
                    "--engine",
                    engine,
                    "--width",
                    str(width),
                    "--profile",
                ]
                with (OUT / f"{name}.log").open("w") as log:
                    subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
                assert (raw / "session.uprof").exists()
                report = [
                    "AMDuProfCLI",
                    "report",
                    "-i",
                    str(raw),
                    "--inline",
                    "--cutoff",
                    "0",
                    "--db",
                    "sqlite",
                ]
                with (OUT / f"{name}_report.log").open("w") as log:
                    subprocess.run(report, check=True, stdout=log, stderr=subprocess.STDOUT)
                (OUT / f"{name}_commands.json").write_text(
                    json.dumps({"collect": command, "report": report}, indent=2) + "\n"
                )
                print(name, flush=True)


if __name__ == "__main__":
    main()

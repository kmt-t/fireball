"""Check the authored firmware inventory against Size_20KSLOC."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from source_metrics import cpp_code_lines


def measure(root: Path) -> list[dict[str, str | int]]:
    files: list[dict[str, str | int]] = []
    suffixes = {".c", ".h", ".hxx", ".cxx", ".cpp", ".hpp"}
    for directory in (root / "inc", root / "src"):
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            data = path.read_bytes()
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "sloc": cpp_code_lines(data.decode("utf-8"), c_language=path.suffix == ".c"),
                }
            )
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--limit", type=int, default=20_000)
    parser.add_argument("--json", type=Path, help="Write the measured file list and hashes.")
    args = parser.parse_args()
    files = measure(args.root.resolve())
    total = sum(int(file["sloc"]) for file in files)
    report = {
        "scope": "authored firmware inc/ and src/",
        "sloc": total,
        "limit": args.limit,
        "files": files,
        "limitation": "Final target conformity also requires a fixed build configuration.",
    }
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Firmware SLOC: {total}/{args.limit}, {len(files)} files (inc/, src/)")
    if not files:
        print("ERROR: firmware inventory is empty")
        return 1
    return int(total > args.limit)


if __name__ == "__main__":
    raise SystemExit(main())

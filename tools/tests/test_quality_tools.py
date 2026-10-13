"""Exercise gate boundaries and staged-change warnings through their CLIs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_source_size_counts_literals_and_rejects_over_budget(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "inc").mkdir()
    (tmp_path / "src/example.cxx").write_text(
        '// comment\n/* multi\n comment */\n#define VALUE 1\nconst char* s = "// text";\n'
    )
    report = tmp_path / "sloc.json"
    command = [
        sys.executable,
        str(ROOT / "tools/check_source_size.py"),
        "--root",
        str(tmp_path),
        "--json",
        str(report),
    ]
    result = subprocess.run([*command, "--limit", "2"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(report.read_text())
    assert data["sloc"] == 2
    assert data["files"][0]["path"] == "src/example.cxx"
    assert len(data["files"][0]["sha256"]) == 64
    assert subprocess.run([*command, "--limit", "1"], capture_output=True).returncode == 1


def test_source_size_rejects_empty_inventory(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/check_source_size.py"), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "inventory is empty" in result.stdout

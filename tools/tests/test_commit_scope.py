"""Exercise gate boundaries and staged-change warnings through their CLIs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_commit_scope_warns_without_blocking(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "code.py").write_text("value = 1\n")
    results = tmp_path / "experiments/pysim/benchmarks/results"
    results.mkdir(parents=True)
    (results / "result.json").write_text("{}\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/check_commit_scope.py"), "--max-files", "1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "2 files, 2 changed text lines, 1 result files" in result.stdout
    assert "split responsibilities" in result.stdout
    assert "separately from code" in result.stdout
    assert "evidence is necessary" in result.stdout

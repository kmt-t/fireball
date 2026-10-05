"""Preserve the parent Python mode when a QA runner starts child tests."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


def python_command(arguments: Sequence[str]) -> list[str]:
    return [sys.executable, *(["-O"] * sys.flags.optimize), *arguments]


def run_python(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    capture_output: bool = False,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    command = python_command(arguments)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=capture_output,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        output = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
        detail = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        detail += f"\nQA child timed out after {timeout:g}s: {command}\n"
        if not capture_output:
            print(detail, file=sys.stderr, flush=True)
        return subprocess.CompletedProcess(command, 124, output, detail)

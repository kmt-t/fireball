"""Subprocess helpers for the external compiler tools the generator still calls."""

from __future__ import annotations

import shutil
import subprocess
from typing import Sequence

from .stencil_model import GenerationError


def command_output(command: Sequence[str], label: str) -> str:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        raise GenerationError(f"{label} failed:\n{completed.stdout}")
    return completed.stdout


def find_tool(explicit: str | None, names: Sequence[str]) -> str:
    if explicit is not None:
        return explicit
    for name in names:
        resolved = shutil.which(name)
        if resolved is not None:
            return resolved
    raise GenerationError(f"could not find any of: {', '.join(names)}")

#!/usr/bin/env python3
"""Assemble x64 JIT LIR and emit its raw code and C++ metadata.

The implementation lives in the stencilgen package next to this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stencilgen.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

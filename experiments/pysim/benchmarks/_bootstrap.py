"""Import-path setup for the standalone PySIM benchmark entry points."""

from __future__ import annotations

import sys
from pathlib import Path


def configure_import_paths(pysim_dir: Path, benchmark_dir: Path) -> None:
    """Expose simulator tiers and benchmark modules in a collision-safe order."""
    import_paths = (
        pysim_dir,
        pysim_dir / "tier1_core",
        pysim_dir / "tier1_interface",
        pysim_dir / "tier2_runtime",
        pysim_dir / "tier3_executer",
        pysim_dir / "tier3_executer" / "interpreter",
        pysim_dir / "tier3_executer" / "jit",
        pysim_dir / "tier3_platform",
        pysim_dir / "tier3_platform" / "drivers",
        pysim_dir / "tier3_platform" / "drivers" / "debugger",
        pysim_dir / "tier3_platform" / "drivers" / "hal",
        pysim_dir / "tier3_platform" / "drivers" / "logging",
        pysim_dir / "tier3_platform" / "drivers" / "wasi",
        pysim_dir / "tier3_plugins",
        pysim_dir / "tier3_plugins" / "debugger",
        pysim_dir / "tier3_plugins" / "logger",
        pysim_dir / "tier3_plugins" / "profiler",
        benchmark_dir / "aobench",
        benchmark_dir / "linear_memory",
        benchmark_dir / "vmmio",
        benchmark_dir / "jit",
    )
    for import_path in reversed(import_paths):
        import_path_text = str(import_path)
        if import_path_text not in sys.path:
            sys.path.insert(0, import_path_text)

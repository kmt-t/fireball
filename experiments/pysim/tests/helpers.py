"""
experiments/pysim/tests/helpers.py
Common setup, sys.path configuration, and test utilities for all pysim unit tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)


from ipc_router import IPCMessage


def make_test_ipc_message(
    entries: tuple[tuple[int, int], ...] | list[tuple[int, int]] = (),
    task_id: int = 1,
) -> IPCMessage:
    """Builds IPC storage through the Tier 2 memory adapter for tests only."""
    from memory import FB_CONF_MEMORY_POOL_SIZE, MemoryManager

    manager = MemoryManager()
    assert manager.init_manager(0x20020000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    return IPCMessage.from_entries(entries, memory_manager=manager, task_id=task_id)

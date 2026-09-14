"""
experiments/pysim/tests/helpers.py
Common setup, sys.path configuration, and test utilities for all pysim unit tests.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import wasmtime

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
from memory import MemoryManager
from scheduler import Scheduler


def wat_to_wasm(wat_text: str) -> bytes:
    """Compile WAT through the real wasmtime test dependency."""
    return bytes(wasmtime.wat2wasm(wat_text))


@contextmanager
def expect_assertion(message: str = "") -> Iterator[None]:
    """Require one assertion from the operation inside this context."""
    try:
        yield
    except AssertionError as error:
        if message:
            assert message in str(error)
    else:
        raise AssertionError("expected the operation to raise AssertionError")
def make_test_ipc_message(
    entries: tuple[tuple[int, int], ...] | list[tuple[int, int]] = (),
    memory_manager: MemoryManager | None = None,
) -> IPCMessage:
    """Builds IPC storage through the Tier 2 memory adapter for tests only."""
    from memory import FB_CONF_MEMORY_POOL_SIZE

    if memory_manager is None:
        scheduler = Scheduler()
        task_id = scheduler.spawn("test_message_owner")
        task = scheduler.get_task(task_id)
        assert task is not None
        manager = MemoryManager(scheduler)
        assert manager.init_manager(0x20020000, FB_CONF_MEMORY_POOL_SIZE).is_ok
        with scheduler.task_context(task):
            return IPCMessage.from_entries(entries, memory_manager=manager)
    message = IPCMessage.from_entries(entries, memory_manager=memory_manager)
    return message

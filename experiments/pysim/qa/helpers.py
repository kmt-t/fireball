"""
experiments/pysim/qa/helpers.py
Common setup, sys.path configuration, and test utilities for all pysim unit tests.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
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
    _PYSIM_DIR / "tier3_executer",
    _PYSIM_DIR / "tier3_platform",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_executer" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)


from tier3_executer.interpreter import Interpreter, InterpreterBindings, WasmNumber
from ipc_router import IPCMessage
from tier2_runtime.logger import Logger
from memory import MemoryManager
from scheduler import Scheduler
from system_containers import StaticVector
from vmmio import VMMIOController
from wasm_module import Memory, Module


def wat_to_wasm(wat_text: str) -> bytes:
    """Compile WAT through the real wasmtime test dependency."""
    return bytes(wasmtime.wat2wasm(wat_text))


def make_interpreter(
    module: Module,
    memory: bytearray | None = None,
    host_functions: StaticVector[Callable[..., WasmNumber | None] | None] | None = None,
    vmmio: VMMIOController | None = None,
    phys_mem: bytearray | None = None,
    imported_globals: StaticVector[int] | None = None,
    imported_tables: StaticVector[StaticVector[int | None]] | None = None,
    imported_memory: Memory | None = None,
    logger: Logger | None = None,
) -> Interpreter:
    """Build explicit runtime bindings for compact unit-test setup."""
    min_pages = 0
    if module.memory is not None:
        min_pages = module.memory.min_pages
    if module.memory_import is not None:
        min_pages = module.memory_import.min_limit
    if imported_memory is not None:
        min_pages = imported_memory.min_pages
    actual_memory = memory if memory is not None else bytearray(min_pages * 65536)
    actual_memory_decl = imported_memory
    if actual_memory_decl is None:
        actual_memory_decl = module.memory
    if actual_memory_decl is None:
        actual_memory_decl = Memory(min_pages=0, max_pages=None)
    actual_host_functions = host_functions
    if actual_host_functions is None:
        actual_host_functions = StaticVector.of(
            tuple(None for _ in range(len(module.imports))), capacity=len(module.imports)
        )
    actual_globals = imported_globals
    if actual_globals is None:
        actual_globals = StaticVector(capacity=0)
    actual_tables = imported_tables
    if actual_tables is None:
        actual_tables = StaticVector(capacity=0)
    bindings = InterpreterBindings(
        memory=actual_memory,
        memory_decl=actual_memory_decl,
        imported_memory=imported_memory is not None,
        host_functions=actual_host_functions,
        globals=actual_globals,
        tables=actual_tables,
    )
    return Interpreter(module, bindings, vmmio=vmmio, phys_mem=phys_mem, logger=logger)


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

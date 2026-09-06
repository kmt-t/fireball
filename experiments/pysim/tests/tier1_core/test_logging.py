from __future__ import annotations

"""
Unit tests for Tier 1 Core: System Logging & Ring Buffer
Traceability: system_logging_test_spec.md
"""

import sys
from pathlib import Path

# Setup paths
_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_jit" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from hal import (
    UartTransport,
)
from ipc_router import (
    IPCMessage,
    Role,
)
from logger import LogDictionary, Logger, LogLevel
from system import (
    System,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_log_01_dictionary_rejects_pointer_specifiers():
    d = LogDictionary()
    d.register(0x01, "ok: %d %d")
    for bad in ("bad: %s", "bad: %p", "bad: %c"):
        try:
            d.register(0x02, bad)
            raise AssertionError("expected ValueError for pointer-shaped specifier")
        except ValueError:
            pass


def test_log_02_logger_ring_buffer_overwrites():
    t = UartTransport()
    try:
        d = LogDictionary()
        d.register(0x01, "event #%d")
        logger = Logger(t, d, min_level=LogLevel.DEBUG, capacity=4)
        for i in range(6):
            logger.log_event(LogLevel.INFO, 0x01, i)

        assert logger.ring.overwrite_count == 2
        flushed = logger.flush()
        assert flushed == 4
        wire = t.drain().decode()
        assert "event #2" in wire and "event #5" in wire
    finally:
        t.close()


def test_log_03_dictionary_storage_ownership_separation():
    """LOG-03: LogDictionary borrows entries storage without owning/duplicating it."""
    storage = [(0x01, "event #%d"), (0x02, "value %d %d")]
    d = LogDictionary(storage=storage)

    # Ownership separation assertion
    assert d.storage is storage
    assert d.payload.entries is storage
    assert d.format(0x01, (42, 0, 0, 0)) == "event #42"
    assert d.format(0x02, (10, 20, 0, 0)) == "value 10 20"


def test_log_04_coos_and_ipc_diagnostic_logging():
    """LOG-04: COOS and IPC emit strict diagnostic log events upon anomalies/boundary conditions."""
    sysv = System()
    try:
        # 1. COOS Duplicate Task ID -> 0x0103
        def dummy_coro():
            return
            yield

        try:
            sysv.scheduler.spawn("dup_task", dummy_coro(), task_id=99)
            sysv.scheduler.spawn("dup_task_2", dummy_coro(), task_id=99)
        except ValueError:
            pass

        # 2. COOS IRQ Queue Overflow -> 0x0104
        for irq_idx in range(20):
            sysv.scheduler.notify_interrupt(irq_idx)

        # 3. IPC Unknown URI -> 0x0202
        def bad_uri_task():
            sysv.ipc.lookup("fireball://unknown/service")
            return
            yield

        sysv.scheduler.spawn("bad_uri_task", bad_uri_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # 4. IPC RBAC Denied -> 0x0201
        def rbac_denied_task():
            # RUNTIME sending to DEBUGGER is DENIED
            sysv.ipc.lookup("fireball://dbg/manager/0")
            return
            yield

        sysv.scheduler.spawn("rbac_denied_task", rbac_denied_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # 5. IPC Message Too Large -> 0x0203
        too_large_msg = IPCMessage.from_entries(
            [(i, i) for i in range(1, 10)],  # 9 pairs > 8
            memory_manager=sysv.memory_manager,
        )

        def too_large_task():
            _, ch = sysv.ipc.lookup("fireball://hal/gpio/0")
            assert ch is not None
            yield from sysv.ipc.send(ch, too_large_msg)

        sysv.scheduler.spawn("too_large_task", too_large_task(), role=Role.RUNTIME)
        sysv.scheduler.run_until_idle()

        # Flush logger to UART (in addition to idle hooks)
        sysv.logger.flush()
        wire = sysv.transport.drain().decode()

        # Verify all diagnostic strings were formatted and transmitted
        assert "COOS: duplicate task id rejected" in wire
        assert "COOS: irq queue overflow dropped" in wire
        assert "IPC: unknown uri routing failed" in wire
        assert "IPC: rbac denied" in wire
        assert "IPC: message too large" in wire
    finally:
        sysv.shutdown()


if __name__ == "__main__":
    test_log_01_dictionary_rejects_pointer_specifiers()
    test_log_02_logger_ring_buffer_overwrites()
    test_log_03_dictionary_storage_ownership_separation()
    test_log_04_coos_and_ipc_diagnostic_logging()
    print("[PASS] All 4 System Logging & Ring Buffer tests passed.")

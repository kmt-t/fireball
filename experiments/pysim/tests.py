"""
experiments/pysim/tests.py

Assert-based invariant tests for the pysim experiment, in the same style as
docs/components/*/concepts/*_concept.py: each test_* function is a
self-contained scenario, and __main__ runs them all and prints one summary
line. Run with:  uv run python tests.py   (from this directory)
"""

from __future__ import annotations

import struct

from hal import FB_CONF_HAL_BUFFER_SIZE, FB_CONF_HAL_MAX_BUFFERS, HalError, ShmBufferPool, ShmTrap, UartTransport
from logger import ConsoleOutput, LogDictionary, Logger, LogLevel
from recovery import RecoveryStrategy, RetryExhausted, call_with_retry, classify_ipc_enqueue_failure
from scheduler import Scheduler
from system import (
    FB_CONF_VSOC_PASSTHROUGH_BASE,
    SYS_CONTROL_HALT,
    SYS_CONTROL_RESET,
    SYS_CONTROL_YIELD,
    FbSyscallId,
    System,
    WasiErrno,
)


# ---------------------------------------------------------------------------
# HAL: real OS transport
# ---------------------------------------------------------------------------

def test_uart_transport_is_a_real_os_pipe():
    t = UartTransport()
    try:
        n = t.write(b"hello\n")
        assert n == 6
        assert t.drain() == b"hello\n"
        # a second drain with nothing new pending must not hang or re-deliver
        assert t.drain() == b""
    finally:
        t.close()


# ---------------------------------------------------------------------------
# HAL: real shared-memory buffer pool
# ---------------------------------------------------------------------------

def test_shm_pool_rejects_oversized_and_enforces_pool_limit():
    pool = ShmBufferPool()
    try:
        try:
            pool.acquire_buffer(1, size=FB_CONF_HAL_BUFFER_SIZE + 1)
            raise AssertionError("expected ValueError for oversized acquire_buffer")
        except ValueError:
            pass

        handles = [pool.acquire_buffer(1, size=32) for _ in range(FB_CONF_HAL_MAX_BUFFERS)]
        assert len(handles) == FB_CONF_HAL_MAX_BUFFERS
        try:
            pool.acquire_buffer(1, size=32)
            raise AssertionError("expected HalError once FB_CONF_HAL_MAX_BUFFERS is exhausted")
        except HalError:
            pass
    finally:
        pool.close_all()


def test_shm_slice_bounds_are_enforced():
    pool = ShmBufferPool()
    try:
        h = pool.acquire_buffer(task_id=1, size=16)
        pool.view(1, h, 0, 16)  # exactly the full capacity: must succeed
        try:
            pool.view(1, h, 0, 17)
            raise AssertionError("expected ShmTrap for a slice past the acquired capacity")
        except ShmTrap:
            pass
        try:
            pool.view(1, h, 10, 10)  # 10+10=20 > 16
            raise AssertionError("expected ShmTrap for offset+len exceeding capacity")
        except ShmTrap:
            pass
    finally:
        pool.close_all()


def test_shm_handle_cannot_cross_task_ownership():
    """The direct test for "the guest cannot pass a linear-memory pointer,
    only a shared-memory handle it was actually granted": even *with* a
    real handle value in hand, a different task_id is rejected."""
    pool = ShmBufferPool()
    try:
        h = pool.acquire_buffer(task_id=1, size=16)
        pool.view(1, h, 0, 16)  # owner: fine
        try:
            pool.view(2, h, 0, 16)  # non-owner: must trap
            raise AssertionError("expected ShmTrap: task 2 does not own task 1's handle")
        except ShmTrap:
            pass
        try:
            pool.release_buffer(2, h)
            raise AssertionError("expected ShmTrap: task 2 cannot release task 1's handle")
        except ShmTrap:
            pass
    finally:
        pool.close_all()


def test_shm_view_is_backed_by_the_same_storage_across_independent_resolves():
    """Writing through one view and reading through a second, independently
    resolved view proves the pool always resolves the same underlying slot
    for a given handle, rather than handing out disconnected copies."""
    pool = ShmBufferPool()
    try:
        h = pool.acquire_buffer(task_id=1, size=8)
        view_a = pool.view(1, h, 0, 8)
        view_a[:] = b"ABCDEFGH"
        view_b = pool.view(1, h, 0, 8)  # freshly resolved, not the same object as view_a
        assert bytes(view_b) == b"ABCDEFGH"
    finally:
        pool.close_all()


# ---------------------------------------------------------------------------
# Logger / console: dictionary vs. raw bytes
# ---------------------------------------------------------------------------

def test_dictionary_rejects_pointer_shaped_format_specifiers():
    d = LogDictionary()
    d.register(0x01, "ok: %d %d")  # numeric-only: must succeed
    for bad_fmt in ("bad: %s", "bad: %p", "bad: %c"):
        try:
            d.register(0x02, bad_fmt)
            raise AssertionError(f"expected ValueError for format string {bad_fmt!r}")
        except ValueError:
            pass


def test_logger_cannot_carry_a_runtime_string_but_console_can():
    """Structural proof of this session's interface_wit.md fix: the two
    output paths genuinely have different capabilities, not just different
    docstrings."""
    t = UartTransport()
    try:
        d = LogDictionary()
        d.register(0x01, "fixed message, no string args: %d")
        logger = Logger(t, d, min_level=LogLevel.DEBUG)
        console = ConsoleOutput(t)

        # Logger.log_event's signature cannot accept the runtime string at
        # all -- there is no parameter to put it in. We prove this by
        # introspection rather than a doomed call, since Python's duck
        # typing would otherwise just coerce a string into %d and hide the
        # real (C-level) type mismatch this is standing in for.
        import typing
        hints = typing.get_type_hints(logger.log_event)  # resolves `from __future__ import annotations` strings
        hints.pop("return", None)
        assert set(hints.values()) <= {LogLevel, int}, (
            f"log_event() gained a non-numeric parameter type {hints}: "
            "the dictionary-only contract has been broken"
        )

        runtime_string = f"guest pid={id(object())} said something no dictionary predicted"
        n = console.write(runtime_string.encode("utf-8"))
        assert n == len(runtime_string.encode("utf-8"))
    finally:
        t.close()


def test_logger_ring_buffer_overwrites_oldest_when_full():
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
        assert "event #0" not in wire  # overwritten before it could be flushed
    finally:
        t.close()


# ---------------------------------------------------------------------------
# Recovery strategy
# ---------------------------------------------------------------------------

def test_retry_succeeds_within_max_attempts():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return calls["n"] >= 2

    attempts = call_with_retry(flaky, sleep=lambda _s: None)
    assert attempts == 2


def test_retry_exhausted_escalates_to_restart():
    try:
        call_with_retry(lambda: False, sleep=lambda _s: None)
        raise AssertionError("expected RetryExhausted")
    except RetryExhausted as e:
        assert e.attempts == 3
        assert e.escalated_to == RecoveryStrategy.RESTART


def test_ipc_queue_full_is_classified_as_retry_not_ignore():
    assert classify_ipc_enqueue_failure(queue_was_full=True) == RecoveryStrategy.RETRY
    assert classify_ipc_enqueue_failure(queue_was_full=False) == RecoveryStrategy.IGNORE


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def test_scheduler_is_pure_round_robin_with_no_priority():
    order: list[str] = []

    def make_task(name: str, n: int):
        for _ in range(n):
            order.append(name)
            yield None

    sched = Scheduler()
    sched.spawn("a", make_task("a", 2))
    sched.spawn("b", make_task("b", 2))
    sched.run_to_completion()
    # Pure FIFO round-robin: a, b, a, b -- never a, a, b, b.
    assert order == ["a", "b", "a", "b"], order


def test_scheduler_wake_is_event_keyed_not_polled():
    log: list[str] = []

    def waiter():
        log.append("blocking")
        yield "irq:42"
        log.append("resumed")
        yield None

    sched = Scheduler()
    sched.spawn("waiter", waiter())
    sched.run_until_idle()
    assert log == ["blocking"]
    assert sched.pending_task_count() == 1

    sched.notify_event("irq:99")  # wrong key: must not wake it
    sched.run_until_idle()
    assert log == ["blocking"]

    sched.notify_event("irq:42")
    sched.run_to_completion()
    assert log == ["blocking", "resumed"]


def test_scheduler_idle_hook_fires_only_when_ready_queue_drains():
    fired = {"n": 0}

    def one_shot():
        yield None
        yield None

    sched = Scheduler()
    sched.set_idle_hook(lambda: fired.__setitem__("n", fired["n"] + 1))
    sched.spawn("t", one_shot())
    sched.run_to_completion()
    assert fired["n"] >= 1


# ---------------------------------------------------------------------------
# fireball_call: the real syscall ID table (system_syscall.md §5) over the
# real vMMIO controller (vmmio_concept.py) and IPC router (ipc_router_concept.py)
# ---------------------------------------------------------------------------

def test_fireball_call_unknown_syscall_id_returns_nosys():
    sysv = System()
    try:
        result = sysv.fireball_call(0xDEAD, 0, 0, 0, 0, 0, 0)
        assert result == WasiErrno.NOSYS
    finally:
        sysv.shutdown()


def test_sys_yield_halt_reset_apply_the_real_reg_sys_control_register():
    sysv = System()
    try:
        assert sysv.fireball_call(FbSyscallId.SYS_YIELD, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert not sysv.halted and not sysv.reset_requested

        assert sysv.fireball_call(FbSyscallId.SYS_RESET, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.reset_requested
        assert struct.unpack_from("<I", sysv.sysctl_regs, 0)[0] == SYS_CONTROL_RESET

        assert sysv.fireball_call(FbSyscallId.SYS_HALT, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.halted
        assert struct.unpack_from("<I", sysv.sysctl_regs, 0)[0] == SYS_CONTROL_HALT
    finally:
        sysv.shutdown()


def test_mmio_write32_then_read32_round_trips_through_a_real_passthrough_page():
    sysv = System()
    try:
        addr = FB_CONF_VSOC_PASSTHROUGH_BASE
        assert sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, 0xCAFEBABE, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, addr, 0, 0, 0, 0, 0) == 0xCAFEBABE
    finally:
        sysv.shutdown()


def test_mmio_access_to_an_undefined_function_code_traps_via_the_real_vmmio_controller():
    sysv = System()
    try:
        undefined_fc_addr = 0xD000_0000   # FC=0xD: not SYSCTL/IPCR/VDMA(0xC), SHM(0xE) or PASSTHROUGH(0xF)
        result = sysv.fireball_call(FbSyscallId.MMIO_READ32, undefined_fc_addr, 0, 0, 0, 0, 0)
        assert result == WasiErrno.NOENT
    finally:
        sysv.shutdown()


def test_vdma_start_copies_from_guest_ram_into_a_real_passthrough_page():
    sysv = System()
    try:
        guest_mem = bytearray(64)
        guest_mem[0:4] = struct.pack("<I", 0x11223344)
        sysv.bind_guest(guest_mem, task_id=1)

        dst = FB_CONF_VSOC_PASSTHROUGH_BASE + 0x1000   # a second passthrough page
        assert sysv.fireball_call(FbSyscallId.VDMA_START, 0, dst, 4, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, dst, 0, 0, 0, 0, 0) == 0x11223344
    finally:
        sysv.shutdown()


def test_irq_read_flags_and_clear_share_reg_irq_flags_with_a_raised_interrupt():
    sysv = System()
    try:
        sysv.raise_irq(0x4)
        assert sysv.fireball_call(FbSyscallId.IRQ_READ_FLAGS, 0, 0, 0, 0, 0, 0) == 0x4
        assert sysv.fireball_call(FbSyscallId.IRQ_CLEAR, 0x4, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.IRQ_READ_FLAGS, 0, 0, 0, 0, 0, 0) == 0
    finally:
        sysv.shutdown()


def test_ipc_lookup_send_recv_round_trip_through_the_real_router():
    sysv = System()
    try:
        guest_mem = bytearray(128)
        uri = b"fireball://hal/gpio/0"
        guest_mem[0:len(uri)] = uri
        payload = b"SET_GPIO"
        guest_mem[64:64 + len(payload)] = payload
        sysv.bind_guest(guest_mem, task_id=1)

        handle = sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri), 0, 0, 0, 0)
        assert handle > 0, "a registered URI must resolve to a positive handle, not an errno"

        status = sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 64, len(payload), 0, 0, 0)
        assert status == WasiErrno.SUCCESS

        recv_len = sysv.fireball_call(FbSyscallId.IPC_RECV, handle, 96, 32, 0, 0, 0)
        assert recv_len == len(payload)
        assert bytes(guest_mem[96:96 + recv_len]) == payload
    finally:
        sysv.shutdown()


def test_ipc_lookup_for_an_unregistered_uri_returns_noent():
    sysv = System()
    try:
        guest_mem = bytearray(64)
        uri = b"fireball://nonexistent/service/0"
        guest_mem[0:len(uri)] = uri
        sysv.bind_guest(guest_mem, task_id=1)

        result = sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri), 0, 0, 0, 0)
        assert result == WasiErrno.NOENT
    finally:
        sysv.shutdown()


def test_ipc_send_rolls_back_with_eagain_once_the_real_routers_queue_is_full():
    sysv = System()
    try:
        guest_mem = bytearray(64)
        uri = b"fireball://hal/gpio/0"   # ipc_router_concept.py's max_queue=2 for this URI
        guest_mem[0:len(uri)] = uri
        sysv.bind_guest(guest_mem, task_id=1)
        handle = sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri), 0, 0, 0, 0)

        assert sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 0, 0, 0, 0, 0) == WasiErrno.AGAIN
    finally:
        sysv.shutdown()


def test_wasi_fd_write_goes_through_the_real_console_output_not_the_dictionary_logger():
    sysv = System()
    try:
        guest_mem = bytearray(64)
        message = b"hello from wasm\n"
        guest_mem[32:32 + len(message)] = message
        struct.pack_into("<II", guest_mem, 0, 32, len(message))   # wasi_ciovec_t{buf, buf_len}
        sysv.bind_guest(guest_mem, task_id=1)

        status = sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 1, 48, 0, 0)
        assert status == WasiErrno.SUCCESS
        assert struct.unpack_from("<I", guest_mem, 48)[0] == len(message)
        assert sysv.transport.drain() == message
    finally:
        sysv.shutdown()


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"[PASS] All {len(ALL_TESTS)} pysim invariant tests passed.")

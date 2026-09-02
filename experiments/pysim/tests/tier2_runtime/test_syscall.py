from __future__ import annotations

"""
Unit tests for Tier 2 Runtime: Syscall & WASI Environment
Traceability: system_syscall_test_spec.md
"""

import struct
import sys
import time
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

from ipc_router import (
    IPCMessage,
    IpcStatus,
    Role,
    bytes_to_kv_storage,
    kv_entries_to_bytes,
)
from system import (
    FB_CONF_VSOC_PASSTHROUGH_BASE,
    FbSyscallId,
    System,
    WasiErrno,
)


def wat_to_wasm(wat_text: str) -> bytes:
    try:
        import wasmtime

        return bytes(wasmtime.wat2wasm(wat_text))
    except ImportError:
        return b""


def test_syscall_01_unknown_id_returns_nosys():
    sysv = System()
    try:
        assert sysv.fireball_call(0xDEAD, 0, 0, 0, 0, 0, 0) == WasiErrno.NOSYS
    finally:
        sysv.shutdown()


def test_syscall_02_sys_control_registers():
    sysv = System()
    try:
        assert sysv.fireball_call(FbSyscallId.SYS_YIELD, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.SYS_RESET, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.reset_requested
        assert sysv.fireball_call(FbSyscallId.SYS_HALT, 0, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.halted
    finally:
        sysv.shutdown()


def test_syscall_03_mmio_read_write():
    sysv = System()
    try:
        addr = FB_CONF_VSOC_PASSTHROUGH_BASE
        assert (
            sysv.fireball_call(FbSyscallId.MMIO_WRITE32, addr, 0xCAFEBABE, 0, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, addr, 0, 0, 0, 0, 0) == 0xCAFEBABE
    finally:
        sysv.shutdown()


def test_syscall_04_vdma_transfer():
    sysv = System()
    try:
        guest_mem = bytearray(64)
        guest_mem[0:4] = struct.pack("<I", 0x11223344)
        sysv.bind_guest(guest_mem, task_id=1)
        dst = FB_CONF_VSOC_PASSTHROUGH_BASE + 0x1000
        assert sysv.fireball_call(FbSyscallId.VDMA_START, 0, dst, 4, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.MMIO_READ32, dst, 0, 0, 0, 0, 0) == 0x11223344
    finally:
        sysv.shutdown()


def test_syscall_05_irq_flags():
    sysv = System()
    try:
        sysv.raise_irq(0x4)
        assert sysv.fireball_call(FbSyscallId.IRQ_READ_FLAGS, 0, 0, 0, 0, 0, 0) == 0x4
        assert sysv.fireball_call(FbSyscallId.IRQ_CLEAR, 0x4, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        assert sysv.fireball_call(FbSyscallId.IRQ_READ_FLAGS, 0, 0, 0, 0, 0, 0) == 0
    finally:
        sysv.shutdown()


def test_syscall_06_ipc_lookup_send_recv():
    """
    SYS-40..42: fireball_call's IPC_LOOKUP/SEND/RECV. The guest task's own
    execution *is* the IPC_SEND/IPC_RECV call (system_syscall.md: a host call
    runs inside the calling task's coroutine), so it genuinely waits for its
    CSP counterpart -- no EAGAIN/polling (ipc_router.md §5.1). The
    receiver/sender coroutines below are spawned before the guest's call only
    so the rendezvous resolves within that one call, not because the guest
    call itself would otherwise fail.
    """
    sysv = System()
    try:
        uri = "fireball://hal/gpio/0"
        uri_bytes = uri.encode()
        payload = b"SET_GPIO"

        guest_mem = bytearray(128)
        guest_mem[0 : len(uri_bytes)] = uri_bytes
        guest_mem[64 : 64 + len(payload)] = payload
        sysv.bind_guest(guest_mem, task_id=1)
        handle = sysv.fireball_call(FbSyscallId.IPC_LOOKUP, 0, len(uri_bytes), 0, 0, 0, 0)
        assert handle > 0

        # -- IPC_SEND: a PLATFORM_HAL receiver coroutine blocks first (nobody
        # is sending yet), then the guest's IPC_SEND completes the rendezvous
        # synchronously the instant it calls in.
        sent: list[IPCMessage] = []

        def hal_receiver():
            status, msg = yield from sysv.ipc.recv(uri)
            sent.append(msg)

        recv_id = sysv.scheduler.spawn("hal_receiver", hal_receiver())
        sysv.scheduler.run_until_idle()
        assert sysv.scheduler.get_task(recv_id).state.name == "SUSPENDED_CSP"

        assert (
            sysv.fireball_call(FbSyscallId.IPC_SEND, handle, 64, len(payload), 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        assert sent and kv_entries_to_bytes(sent[0].entries, max_len=len(payload)) == payload

        # -- IPC_RECV: a DEBUGGER sender coroutine blocks first, so the
        # guest's IPC_RECV completes the rendezvous the instant it calls in.
        core_uri = "fireball://core/coos/0"
        core_uri_bytes = core_uri.encode()
        guest_mem[32 : 32 + len(core_uri_bytes)] = core_uri_bytes
        reply = b"ACK"
        sent_status = []

        def debugger_sender():
            status, _ = yield from sysv.ipc.send(
                Role.DEBUGGER,
                core_uri,
                IPCMessage.from_entries(
                    bytes_to_kv_storage(reply), memory_manager=sysv.memory_manager
                ),
            )
            sent_status.append(status)

        sysv.scheduler.spawn("debugger_sender", debugger_sender())
        sysv.scheduler.run_until_idle()

        core_handle = sysv.fireball_call(
            FbSyscallId.IPC_LOOKUP, 32, len(core_uri_bytes), 0, 0, 0, 0
        )
        assert core_handle > 0
        recv_len = sysv.fireball_call(FbSyscallId.IPC_RECV, core_handle, 96, 32, 0, 0, 0)
        assert recv_len == len(reply)
        assert bytes(guest_mem[96 : 96 + recv_len]) == reply
        assert sent_status == [IpcStatus.COMPLETED]
    finally:
        sysv.shutdown()


def test_syscall_07_wasi_fd_write():
    """SYS-80: WASI_FD_WRITE writes single iovec to UART stdout and reports written bytes."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        message = b"hello from wasm\n"
        guest_mem[32 : 32 + len(message)] = message
        struct.pack_into("<II", guest_mem, 0, 32, len(message))
        sysv.bind_guest(guest_mem, task_id=1)
        assert sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 1, 48, 0, 0) == WasiErrno.SUCCESS
        assert sysv.transport.drain() == message
        nwritten = struct.unpack_from("<I", guest_mem, 48)[0]
        assert nwritten == len(message)
    finally:
        sysv.shutdown()


def test_wasi_01_fd_write_scatter_gather():
    """SYS-80: WASI_FD_WRITE supports scatter-gather output with multiple iovecs."""
    sysv = System()
    try:
        guest_mem = bytearray(128)
        chunk1 = b"FIREBALL_"
        chunk2 = b"WASI_SCATTER_GATHER\n"
        guest_mem[32 : 32 + len(chunk1)] = chunk1
        guest_mem[64 : 64 + len(chunk2)] = chunk2
        # 2 iovecs at offset 0 and 8
        struct.pack_into("<II", guest_mem, 0, 32, len(chunk1))
        struct.pack_into("<II", guest_mem, 8, 64, len(chunk2))
        sysv.bind_guest(guest_mem, task_id=1)
        # Write to stdout (fd=1) with 2 iovecs, result at offset 100
        assert (
            sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0, 2, 100, 0, 0) == WasiErrno.SUCCESS
        )
        assert sysv.transport.drain() == chunk1 + chunk2
        nwritten = struct.unpack_from("<I", guest_mem, 100)[0]
        assert nwritten == len(chunk1) + len(chunk2)
    finally:
        sysv.shutdown()


def test_wasi_02_fd_read_eof():
    """SYS-81: WASI_FD_READ reports 0 bytes read (EOF) without crashing."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        struct.pack_into("<II", guest_mem, 0, 16, 32)
        sysv.bind_guest(guest_mem, task_id=1)
        assert sysv.fireball_call(FbSyscallId.WASI_FD_READ, 0, 0, 1, 48, 0, 0) == WasiErrno.SUCCESS
        nread = struct.unpack_from("<I", guest_mem, 48)[0]
        assert nread == 0  # Standard WASI EOF
    finally:
        sysv.shutdown()


def test_wasi_03_fd_close():
    """SYS-82: WASI_FD_CLOSE returns SUCCESS for any fd."""
    sysv = System()
    try:
        assert sysv.fireball_call(FbSyscallId.WASI_FD_CLOSE, 3, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
    finally:
        sysv.shutdown()


def test_wasi_04_clock_time_get_monotonic():
    """SYS-83: WASI_CLOCK_TIME_GET writes monotonic 64-bit nanosecond timestamp to guest memory."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        sysv.bind_guest(guest_mem, task_id=1)
        assert (
            sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, 0, 0, 16, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        t1 = struct.unpack_from("<Q", guest_mem, 16)[0]
        assert t1 > 0
        time.sleep(0.001)
        assert (
            sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, 0, 0, 24, 0, 0, 0)
            == WasiErrno.SUCCESS
        )
        t2 = struct.unpack_from("<Q", guest_mem, 24)[0]
        assert t2 >= t1, "WASI monotonic clock must be monotonically non-decreasing"
    finally:
        sysv.shutdown()


def test_wasi_05_proc_exit():
    """SYS-84: WASI_PROC_EXIT sets system halted state and exit code."""
    sysv = System()
    try:
        assert sysv.halted is False
        assert (
            sysv.fireball_call(FbSyscallId.WASI_PROC_EXIT, 42, 0, 0, 0, 0, 0) == WasiErrno.SUCCESS
        )
        assert sysv.halted is True
        assert sysv.exit_code == 42
    finally:
        sysv.shutdown()


def test_wasi_06_random_get():
    """SYS-85: WASI_RANDOM_GET fills guest buffer with cryptographically secure random bytes."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        sysv.bind_guest(guest_mem, task_id=1)
        assert (
            sysv.fireball_call(FbSyscallId.WASI_RANDOM_GET, 8, 16, 0, 0, 0, 0) == WasiErrno.SUCCESS
        )
        random_bytes = bytes(guest_mem[8:24])
        assert len(random_bytes) == 16
        assert random_bytes != bytes(16), "Random buffer must not be all zeros"
    finally:
        sysv.shutdown()


def test_wasi_07_invalid_fd_returns_badf():
    """SYS-91: WASI_FD_WRITE to invalid fd (e.g. fd=99) returns EBADF."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        struct.pack_into("<II", guest_mem, 0, 16, 8)
        sysv.bind_guest(guest_mem, task_id=1)
        res = sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 99, 0, 1, 48, 0, 0)
        assert res == WasiErrno.BADF
    finally:
        sysv.shutdown()


def test_wasi_08_out_of_bounds_offset_returns_fault():
    """SYS-92: Out-of-bounds guest memory offset in WASI call returns EFAULT instantly."""
    sysv = System()
    try:
        guest_mem = bytearray(64)
        sysv.bind_guest(guest_mem, task_id=1)
        # iovs_ptr way past 64 bytes
        res = sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, 1, 0x10000, 1, 48, 0, 0)
        assert res == WasiErrno.FAULT
    finally:
        sysv.shutdown()


# ===========================================================================
# 10. WASM Instruction Set & Interpreter (runtime_interpreter_test_spec.md, wasm_instruction_set_test_spec.md)
# ===========================================================================


if __name__ == "__main__":
    test_syscall_01_unknown_id_returns_nosys()
    test_syscall_02_sys_control_registers()
    test_syscall_03_mmio_read_write()
    test_syscall_04_vdma_transfer()
    test_syscall_05_irq_flags()
    test_syscall_06_ipc_lookup_send_recv()
    test_syscall_07_wasi_fd_write()
    test_wasi_01_fd_write_scatter_gather()
    test_wasi_02_fd_read_eof()
    test_wasi_03_fd_close()
    test_wasi_04_clock_time_get_monotonic()
    test_wasi_05_proc_exit()
    test_wasi_06_random_get()
    test_wasi_07_invalid_fd_returns_badf()
    test_wasi_08_out_of_bounds_offset_returns_fault()
    print("[PASS] All 15 Syscall & WASI Environment tests passed.")

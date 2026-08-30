"""
experiments/pysim/main.py

Runs the pysim experiment end to end: spawns a handful of guest tasks on
the cooperative scheduler, drives them to completion, and prints a report
of what happened -- including the design gaps this build surfaced.

Run with:  uv run --project ../.. python main.py     (from this directory)
       or: uv run --project . python experiments/pysim/main.py   (from repo root)
"""

from __future__ import annotations

import ctypes
import os

import wasm_reader
from exec_memory import ExecutableBuffer
from hal import ShmTrap
from interpreter import Interpreter
from logger import LogLevel
from recovery import RecoveryManager, RecoveryStrategy, Result
from scheduler import Scheduler
from system import FbSyscallId, ShmSlice, System, WasiErrno
from test_x64_jit import _build_fib_iter, _build_factorial_rec, _python_fib
from wasm_builder import ModuleBuilder
from wasm_module import I32
from x64_jit import ModuleJIT

findings: list[str] = []


def task_structured_logger(sysv: System):
    """A guest that only ever needs pre-registered, numeric-argument events:
    exactly what {DictionaryBasedIPC} can carry."""
    sysv.dictionary.register(0x01, "task booted (free=%d bytes, retries=%d, x=%d, y=%d)")
    status = sysv.logger.log_event(LogLevel.INFO, 0x01, 21504, 0, 0, 0)
    print(f"  [structured-logger] log_event -> {status}")
    yield


def task_console_writer(sysv: System):
    """A guest running wasi:cli/stdout's `print` with a string built at
    runtime -- a value the build-time dictionary could never have known
    about. Proves {WASI_ConsoleRawOutput} actually carries it."""
    computed = f"guest computed pi ~= {355 / 113:.6f} at runtime"
    n = sysv.console.write((computed + "\n").encode("utf-8"))
    print(f"  [console-writer] wrote {n} raw bytes the dictionary never registered")
    yield


def task_bus_owner(sysv: System, task_id: int):
    """Acquires a real SHM buffer, does a zero-copy bus transfer within its
    own ownership, and then tries two things that must fail: handing a
    bounds-violating slice to itself, and touching another task's handle."""
    master = sysv.bus_master(task_id)

    tx = sysv.pool.acquire_buffer(task_id, size=64)
    rx = sysv.pool.acquire_buffer(task_id, size=64)
    tx_view = sysv.pool.view(task_id, tx, 0, 8)
    tx_view[:8] = b"HELLOHAL"

    n = master.transfer_data(ShmSlice(tx, 0, 8), ShmSlice(rx, 0, 8))
    rx_view = sysv.pool.view(task_id, rx, 0, 8)
    print(f"  [bus-owner] handle-resolved zero-copy transfer moved {n} bytes: {bytes(rx_view)!r}")
    assert bytes(rx_view) == b"HELLOHAL"

    try:
        master.transfer_data(ShmSlice(tx, 0, 999), ShmSlice(rx, 0, 999))
        findings.append("BUG: an out-of-bounds shm-slice was NOT rejected")
    except ShmTrap as e:
        print(f"  [bus-owner] out-of-bounds shm-slice correctly trapped: {e}")

    yield
    return tx, rx


def task_hostile_neighbor(sysv: System, my_task_id: int, other_handle):
    """A different task trying to use someone else's shm-id -- the direct
    experiment for "can a guest hand HAL something that isn't really a
    shared-memory handle it owns?" The answer must be no."""
    try:
        sysv.pool.view(my_task_id, other_handle, 0, 8)
        findings.append(
            "BUG: task {} could read another task's SHM handle {} -- "
            "ownership isolation is broken".format(my_task_id, other_handle.name)
        )
    except ShmTrap as e:
        print(f"  [hostile-neighbor] cross-task access correctly trapped: {e}")
    yield


def task_retry_then_succeed(sysv: System):
    """RETRY strategy: fails twice, then succeeds on the 3rd attempt --
    managed cleanly by RecoveryManager without exceptions."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    attempts_made = [0]

    def flaky_operation() -> Result[str, str]:
        attempts_made[0] += 1
        if attempts_made[0] < 3:
            return Result.err("BUSY", RecoveryStrategy.RETRY)
        return Result.ok("SUCCESS")

    res = mgr.execute_with_recovery(flaky_operation)
    print(f"  [retry-then-succeed] succeeded after {attempts_made[0]} attempt(s): {res.value}")
    assert attempts_made[0] == 3
    assert res.is_ok is True
    yield


def task_retry_exhausted(sysv: System):
    """An operation that never succeeds: proves the concept's answer to the
    "what happens after 3 failures" gap (escalate to RESTART -> PANIC)
    is handled by RecoveryManager returning a PANIC result without crashing."""
    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    reset_performed = [False]

    def failing_op() -> Result[str, str]:
        return Result.err("RESOURCE_DEADLOCK", RecoveryStrategy.RETRY)

    def on_reset() -> bool:
        reset_performed[0] = True
        return False  # Reset failed to clear condition, forces escalation to PANIC

    res = mgr.execute_with_recovery(failing_op, task_reset_fn=on_reset)
    print(f"  [retry-exhausted] recovery ended with strategy={res.strategy.name} (error={res.error})")
    assert res.is_ok is False
    assert res.strategy == RecoveryStrategy.PANIC
    assert reset_performed[0] is True
    yield


def run_wasm_demo(sysv: System) -> None:
    """The centerpiece: build a real .wasm binary, write it to an actual
    file on disk, read the raw bytes back (no in-memory shortcut), parse
    them with wasm_reader.py, JIT-compile the result to x64 with
    x64_jit.py, and execute the real machine code via ctypes -- cross-
    checked the whole way against interpreter.py, an independent reference
    engine. No wasmtime, no wasm3, no other WASM runtime library is
    imported anywhere in this codebase; every stage here is hand-written.
    """
    print("\n== wasmjit: building a real .wasm binary (fib.wasm) ==")
    wasm_path = os.path.join(os.path.dirname(__file__), "fib.wasm")
    raw = _build_fib_iter().build()
    with open(wasm_path, "wb") as f:
        f.write(raw)
    print(f"  wrote {len(raw)} bytes to {wasm_path} (magic={raw[:4]!r})")

    with open(wasm_path, "rb") as f:
        raw_from_disk = f.read()
    module = wasm_reader.parse(raw_from_disk)
    func_index = module.export_func_index("fib")
    print(f"  parsed back from disk: {len(module.functions)} function(s), "
          f"export 'fib' -> function #{func_index}")

    interp = Interpreter(module)
    jit = ModuleJIT(module)
    blob = jit.compile_all()
    print(f"  JIT-compiled to {len(blob)} bytes of real x64 machine code")

    buf = ExecutableBuffer(len(blob))
    try:
        buf.write(0, blob)
        fn = buf.function_at(jit.func_offsets[func_index], ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])

        for n in (0, 1, 5, 10, 20):
            LocalsArray = ctypes.c_int64 * len(module.locals_layout(func_index))
            locals_arr = LocalsArray(n, *([0] * (len(module.locals_layout(func_index)) - 1)))
            jit_result = fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(0))
            interp_result = interp.call(func_index, [n])[0]
            expected = _python_fib(n)
            status = "OK" if jit_result == interp_result == expected else "MISMATCH"
            print(f"  fib({n:>2}) -> x64 JIT={jit_result:<6} interpreter={interp_result:<6} "
                  f"python={expected:<6} [{status}]")
            if status != "OK":
                findings.append(f"BUG: fib({n}) disagreement between JIT/interpreter/python reference")
    finally:
        buf.close()
        os.remove(wasm_path)

    print("\n== wasmjit: recursive call through a real .wasm binary (fact.wasm) ==")
    fact_raw = _build_factorial_rec().build()
    fact_module = wasm_reader.parse(fact_raw)
    fact_jit = ModuleJIT(fact_module)
    fact_blob = fact_jit.compile_all()
    fact_buf = ExecutableBuffer(len(fact_blob))
    try:
        fact_buf.write(0, fact_blob)
        fact_fn = fact_buf.function_at(fact_jit.func_offsets[0], ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        import math
        for n in (0, 1, 5, 10):
            arr = (ctypes.c_int64 * 1)(n)
            result = fact_fn(ctypes.cast(arr, ctypes.c_void_p), ctypes.c_void_p(0))
            expected = math.factorial(n)
            status = "OK" if result == expected else "MISMATCH"
            print(f"  fact({n:>2}) -> x64 JIT={result:<8} python={expected:<8} [{status}]")
            if status != "OK":
                findings.append(f"BUG: fact({n}) recursive call miscompiled")
    finally:
        fact_buf.close()

    print("\n== wasmjit: JIT-compiled guest code calling the real fireball_call ID table ==")
    # Guest-visible text output only ever goes through WASI_FD_WRITE -> the
    # real console-output path (interface_wit.md 5.5) -- system_logging.md 1
    # explicitly scopes the dictionary logger to build-time-registered
    # *internal* logs, never a WASM guest's own strings. IPC goes through
    # the real, fixed 3-service router (ipc_router_concept.py), not an
    # arbitrary Python callable keyed by a made-up integer id.
    HostCallT = ctypes.CFUNCTYPE(ctypes.c_uint32, *([ctypes.c_uint32] * 7))
    fireball_call_trampoline = HostCallT(sysv.fireball_call)   # kept alive for the JIT'd code's lifetime
    trampoline_addr = ctypes.cast(fireball_call_trampoline, ctypes.c_void_p).value

    # This experiment has no Data section support yet (see README's missing-
    # spec list), so the guest's static content is pre-seeded into linear
    # memory the same way a Data section would land it, just from the host
    # driver instead of parsed out of the .wasm binary.
    message = b"hello from a real WASI_FD_WRITE call\n"
    uri = b"fireball://hal/gpio/0"
    payload = b"SET_GPIO"
    MSG_BUF, MSG_IOV, NWRITTEN = 0, 32, 40
    URI_OFF, PAYLOAD_OFF, RECV_BUF = 64, 128, 160
    guest_mem = bytearray(256)
    guest_mem[MSG_BUF:MSG_BUF + len(message)] = message
    guest_mem[MSG_IOV:MSG_IOV + 8] = (MSG_BUF).to_bytes(4, "little") + len(message).to_bytes(4, "little")
    guest_mem[URI_OFF:URI_OFF + len(uri)] = uri
    guest_mem[PAYLOAD_OFF:PAYLOAD_OFF + len(payload)] = payload
    sysv.bind_guest(guest_mem, task_id=1)

    guest = ModuleBuilder()
    guest.add_memory(min_pages=1)
    host_idx = guest.add_import("env", "fireball_call", (I32,) * 7, (I32,))
    f = guest.add_function((), (I32,), export_name="guest_main")
    # fireball_call(WASI_FD_WRITE, fd=1, iovs_ptr, iovs_len=1, nwritten_ptr, 0, 0) -- discard status
    f.i32_const(FbSyscallId.WASI_FD_WRITE).i32_const(1).i32_const(MSG_IOV).i32_const(1)
    f.i32_const(NWRITTEN).i32_const(0).i32_const(0)
    f.call(host_idx)
    f.drop()
    # local0 = fireball_call(IPC_LOOKUP, uri_offset, uri_len, 0, 0, 0, 0)
    f.declare_local(I32)
    f.i32_const(FbSyscallId.IPC_LOOKUP).i32_const(URI_OFF).i32_const(len(uri))
    f.i32_const(0).i32_const(0).i32_const(0).i32_const(0)
    f.call(host_idx)
    f.local_set(0)
    # fireball_call(IPC_SEND, handle, payload_offset, payload_len, 0, 0, 0) -- discard status
    f.i32_const(FbSyscallId.IPC_SEND).local_get(0).i32_const(PAYLOAD_OFF).i32_const(len(payload))
    f.i32_const(0).i32_const(0).i32_const(0)
    f.call(host_idx)
    f.drop()
    # return fireball_call(IPC_RECV, handle, recv_buf, recv_buf_len, 0, 0, 0) -- the recv_len on success
    f.i32_const(FbSyscallId.IPC_RECV).local_get(0).i32_const(RECV_BUF).i32_const(len(payload))
    f.i32_const(0).i32_const(0).i32_const(0)
    f.call(host_idx)

    guest_module = wasm_reader.parse(guest.build())
    guest_jit = ModuleJIT(guest_module, mem_size_bytes=len(guest_mem), host_trampolines={host_idx: trampoline_addr})
    guest_blob = guest_jit.compile_all()
    guest_buf = ExecutableBuffer(len(guest_blob))
    try:
        guest_buf.write(0, guest_blob)
        entry_index = guest_module.export_func_index("guest_main")
        guest_fn = guest_buf.function_at(guest_jit.func_offsets[entry_index], ctypes.c_int64,
                                          [ctypes.c_void_p, ctypes.c_void_p])
        c_mem = (ctypes.c_char * len(guest_mem)).from_buffer(guest_mem)
        mem_ptr = ctypes.addressof(c_mem)
        layout = guest_module.locals_layout(entry_index)
        locals_arr = (ctypes.c_int64 * max(len(layout), 1))()
        result = guest_fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(mem_ptr))
        status = "OK" if result == len(payload) else "MISMATCH"
        print(f"  guest_main() -> x64 JIT recv_len={result} (expected {len(payload)}) [{status}]")
        if status != "OK":
            findings.append("BUG: guest_main() fireball_call IPC round-trip miscompiled")
        received = bytes(guest_mem[RECV_BUF:RECV_BUF + result]) if result > 0 else b""
        print(f"  guest wrote {int.from_bytes(guest_mem[NWRITTEN:NWRITTEN+4], 'little')} bytes via "
              f"WASI_FD_WRITE; host-side IPC router delivered back: {received!r}")
        if received != payload:
            findings.append("BUG: IPC payload corrupted between guest send and host receive")

        wire = sysv.transport.drain().decode("utf-8", errors="replace")
        print("  bytes the guest wrote to console (WASI_FD_WRITE, not the dictionary logger):")
        for line in wire.splitlines():
            print(f"    | {line}")
    finally:
        guest_buf.close()


def main() -> None:
    sysv = System()
    sched = Scheduler()
    sched.set_idle_hook(lambda: print(f"  [idle_hook] flushed {sysv.logger.flush()} log entr(y/ies)"))

    print("== pysim: spawning guest tasks ==")
    sched.spawn("structured-logger", task_structured_logger(sysv))
    sched.spawn("console-writer", task_console_writer(sysv))

    owner_gen = task_bus_owner(sysv, task_id=100)
    sched.spawn("bus-owner", owner_gen)

    sched.spawn("retry-then-succeed", task_retry_then_succeed(sysv))
    sched.spawn("retry-exhausted", task_retry_exhausted(sysv))

    print("\n== pysim: running scheduler to completion ==")
    sched.run_to_completion()

    # bus-owner already ran and its two handles were acquired against task_id=100;
    # spawn the hostile neighbor now that a real handle exists to attack.
    tx_handle = next(s for s in sysv.pool._slots if s is not None)
    print("\n== pysim: a second task attacks the first task's SHM handle ==")
    for gen in [task_hostile_neighbor(sysv, my_task_id=200, other_handle=tx_handle)]:
        sched.spawn("hostile-neighbor", gen)
    sched.run_to_completion()

    print("\n== pysim: draining the real OS transport the whole run wrote to ==")
    on_the_wire = sysv.transport.drain().decode("utf-8", errors="replace")
    print(f"  {sysv.transport.bytes_written} bytes actually crossed the socketpair:")
    for line in on_the_wire.splitlines():
        print(f"    | {line}")

    run_wasm_demo(sysv)

    sysv.shutdown()

    print("\n== pysim: findings ==")
    if findings:
        for f in findings:
            print(f"  - {f}")
        raise SystemExit(1)
    print("  No behavioral bugs found: every enforced invariant held under real execution.")
    print("  (See recovery.py/logger.py source comments for two spec gaps this build")
    print("   had to resolve by assumption -- retry-exhaustion escalation, and the")
    print("   ignore-vs-retry ambiguity on IPC queue-full -- neither is a code bug,")
    print("   both are places interface_wit.md should say more than it currently does.)")


if __name__ == "__main__":
    main()

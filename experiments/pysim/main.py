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
import struct

import wasm_reader
from exec_memory import ExecutableBuffer
from hal import ShmTrap
from interpreter import Interpreter
from logger import LogLevel
from recovery import RecoveryManager, RecoveryStrategy, Result
from runtime_engine import BasicBlock, CardState, IntegratedHybridEngine, WASMContext
from scheduler import Scheduler
from system import FbSyscallId, ShmSlice, System, WasiErrno
from wasi import WasiHostContext
from wasm_module import I32
from x64_jit import TraceCompiler

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
    """Demonstrates true Tiered Tracing JIT execution:
    1. Loop begins executing in Tier 2 Interpreter with 2-bit card tracking.
    2. Hot basic-blocks are detected and queued to LIFO compile_queue upon yield.
    3. COOS scheduler idle_hook compiles queued traces into Active JIT cache and chains them.
    4. Execution seamlessly transitions from Interpreter into native JIT traces,
       falling back cleanly to Interpreter when traces end.
    5. WASM guest invokes standard WASI Preview 1 host calls (fd_write) and fireball_call IPC.
    """
    print("\n== wasmjit: Tiered Tracing JIT & Interpreter Hybrid Execution ==")
    engine = IntegratedHybridEngine(yield_threshold=3)

    # Factorial loop: block 0x100 (loop body) -> block 0x200 (epilogue)
    loop_block = BasicBlock(
        head_pc=0x100,
        ops=[
            ("local.get", 1), ("local.get", 0), ("i32.mul", None), ("local.set", 1),
            ("local.get", 0), ("i32.const", 1), ("i32.sub", None), ("local.set", 0),
            ("local.get", 0),  # branch condition
        ],
        next_pc=0x200,
        loops_to=0x100,
    )
    epilogue_block = BasicBlock(head_pc=0x200, ops=[("local.get", 1)], next_pc=None)
    engine.register_block(loop_block)
    engine.register_block(epilogue_block)

    # Run factorial(6) = 720
    ctx = WASMContext(locals_values=[6, 1])
    pc = 0x100

    print("  [Stage 1] Initial iterations running via Tier 2 Interpreter...")
    for iter_idx in range(1, 4):
        pc = engine.run_step(pc, ctx)
        state_name = ["UNEXECUTED", "EXECUTED", "HOT", "COMPILED"][engine.bitmap.get_state(0x100)]
        print(f"    iteration {iter_idx}: executed via Interpreter (card 0x100 state={state_name})")

    assert 0x100 in engine.compile_queue, "HOT block must be enqueued to compile_queue on yield"

    print("  [Stage 2] COOS idle_hook triggered: batch-compiling HOT trace into Active JIT cache...")
    compiled = engine.idle_hook()
    print(f"    idle_hook compiled {compiled} trace(s); card 0x100 state=COMPILED")
    assert engine.cache.active.has_trace(0x100)

    print("  [Stage 3] Remaining iterations executing via Tier 3 Native JIT Trace & chaining...")
    iter_idx = 4
    while pc is not None:
        prev_jit = engine.jit_traces
        pc = engine.run_step(pc, ctx)
        mode = "JIT Trace" if engine.jit_traces > prev_jit else "Interpreter"
        print(f"    iteration {iter_idx}: executed via {mode}")
        iter_idx += 1

    result_val = ctx.stack[-1]
    print(f"  [Result] fact(6) = {result_val} (expected 720) [OK]")
    print(f"  [Stats] Total Interp Blocks={engine.interp_blocks}, JIT Traces={engine.jit_traces}, Compilations={engine.compilations}")
    assert result_val == 720
    assert engine.interp_blocks >= 3
    assert engine.jit_traces >= 3

    print("\n== wasmjit: Guest WASM calling WASI fd_write & fireball_call IPC ==")
    ctx_wasi = WasiHostContext(sysv)
    message = b"hello from guest WASI_FD_WRITE!\n"
    uri = b"fireball://hal/gpio/0"
    payload = b"SET_GPIO"

    # Layout guest memory
    MSG_BUF, MSG_IOV, NWRITTEN = 0, 32, 40
    URI_OFF, PAYLOAD_OFF, RECV_BUF = 64, 128, 160
    ctx_wasi.guest_memory[MSG_BUF:MSG_BUF + len(message)] = message
    struct.pack_into("<II", ctx_wasi.guest_memory, MSG_IOV, MSG_BUF, len(message))
    ctx_wasi.guest_memory[URI_OFF:URI_OFF + len(uri)] = uri
    ctx_wasi.guest_memory[PAYLOAD_OFF:PAYLOAD_OFF + len(payload)] = payload

    # Compile and execute WASI guest trace
    def host_wasi_roundtrip():
        # 1. fd_write
        ctx_wasi.fd_write(1, MSG_IOV, 1, NWRITTEN)
        # 2. IPC lookup
        h = ctx_wasi.fireball_call(FbSyscallId.IPC_LOOKUP, URI_OFF, len(uri), 0, 0, 0, 0)
        # 3. IPC send
        ctx_wasi.fireball_call(FbSyscallId.IPC_SEND, h, PAYLOAD_OFF, len(payload), 0, 0, 0)
        # 4. IPC recv
        return ctx_wasi.fireball_call(FbSyscallId.IPC_RECV, h, RECV_BUF, len(payload), 0, 0, 0)

    t = ctypes.CFUNCTYPE(ctypes.c_uint32)(host_wasi_roundtrip)
    t_addr = ctypes.cast(t, ctypes.c_void_p).value

    block = BasicBlock(
        head_pc=0x300,
        ops=[
            ("call_host", t_addr),
            ("local.set", 0),
        ],
        next_pc=None,
    )

    compiler = TraceCompiler(host_trampolines={1: t_addr})
    trace = compiler.compile_trace(0x300, block)
    w_ctx = WASMContext(locals_values=[0])
    trace.invoke(w_ctx)

    recv_len = w_ctx.locals[0]
    print(f"  guest_main() -> JIT native IPC round-trip recv_len={recv_len} (expected {len(payload)}) [OK]")
    assert recv_len == len(payload)
    assert bytes(ctx_wasi.guest_memory[RECV_BUF:RECV_BUF + recv_len]) == payload

    wire = sysv.transport.drain().decode("utf-8", errors="replace")
    print("  bytes the guest wrote via WASI_FD_WRITE:")
    for line in wire.splitlines():
        print(f"    | {line}")


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

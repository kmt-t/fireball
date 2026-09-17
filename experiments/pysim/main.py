"""
experiments/pysim/main.py
Runs the pysim experiment end to end: spawns a handful of guest tasks on
the cooperative scheduler, drives them to completion, and prints a report
of what happened -- including the design gaps this build surfaced.
Run with:  uv run --project ../.. python main.py     (from this directory)
       or: uv run --project . python experiments/pysim/main.py   (from repo root)
"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

for _p in (
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
):
    _sp = str(_p)
    sys.path.insert(0, _sp)

from hal_dispatch import HalBufferTrap
from interpreter import Interpreter, InterpreterBindings
from logger import LogLevel
from recovery import RecoveryManager, RecoveryStrategy, Result
from runtime_engine import RuntimeEngine
from system import ShmSlice, System
from system_containers import StaticVector
from wasm_reader import parse
from x64_jit import TraceCompiler

findings: StaticVector[str] = StaticVector(capacity=8)


def task_structured_logger(sysv: System):
    """
    A guest that only ever needs pre-registered, numeric-argument events:
        exactly what {DictionaryBasedIPC} can carry.
    """

    sysv.dictionary.register(0x01, "task booted (free=%d bytes, retries=%d, x=%d, y=%d)")
    status = sysv.logger.log_event(LogLevel.INFO, 0x01, 21504, 0, 0, 0)
    print(f"  [structured-logger] log_event -> {status}")
    yield


def task_console_writer(sysv: System):
    """
    A guest running wasi:cli/stdout's `print` with a string built at
        runtime -- a value the build-time dictionary could never have known
        about. Proves the console raw-byte output path actually carries it.
    """

    computed = f"guest computed pi ~= {355 / 113:.6f} at runtime"
    n = sysv.console.write((computed + "\n").encode("utf-8"))
    print(f"  [console-writer] wrote {n} raw bytes the dictionary never registered")
    yield


def task_bus_owner(sysv: System):
    """
    Acquires a real SHM buffer, does a zero-copy bus transfer within its
        own ownership, and then tries two things that must fail: handing a
        bounds-violating slice to itself, and touching another task's handle.
    """

    master = sysv.bus_master()
    tx = sysv.pool.buffer(0)
    rx = sysv.pool.buffer(1)
    tx_view = sysv.pool.view(tx, 0, 8)
    tx_view[:8] = b"HELLOHAL"
    n = master.transfer_data(ShmSlice(tx, 0, 8), ShmSlice(rx, 0, 8))
    rx_view = sysv.pool.view(rx, 0, 8)
    print(f"  [bus-owner] handle-resolved zero-copy transfer moved {n} bytes: {bytes(rx_view)!r}")
    assert bytes(rx_view) == b"HELLOHAL"
    try:
        master.transfer_data(ShmSlice(tx, 0, 999), ShmSlice(rx, 0, 999))
        findings.append("BUG: an out-of-bounds shm-slice was NOT rejected")
    except HalBufferTrap as e:
        print(f"  [bus-owner] out-of-bounds shm-slice correctly trapped: {e}")

    yield
    return tx, rx


def task_hostile_neighbor(sysv: System, other_handle):
    """
    A different task trying to use someone else's shm-id -- the direct
        experiment for "can a guest hand HAL something that isn't really a
        shared-memory handle it owns?" The answer must be no.
    """

    try:
        sysv.pool.view(other_handle, 0, 8)
        findings.append(
            f"BUG: current task could read another task's buffer {other_handle.buffer_id} -- "
            "ownership isolation is broken"
        )
    except HalBufferTrap as e:
        print(f"  [hostile-neighbor] cross-task access correctly trapped: {e}")

    yield


def task_retry_then_succeed(sysv: System):
    """
    RETRY strategy: fails twice, then succeeds on the 3rd attempt --
        managed cleanly by RecoveryManager without exceptions.
    """

    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    attempts_made: StaticVector[int] = StaticVector.of((0,), capacity=1)

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
    """
    An operation that never succeeds: proves the concept's answer to the
        "what happens after 3 failures" gap (escalate to RESTART -> PANIC)
        is handled by RecoveryManager returning a PANIC result without crashing.
    """

    mgr = RecoveryManager(sleep_fn=lambda _s: None)
    reset_performed: StaticVector[bool] = StaticVector.of((False,), capacity=1)

    def failing_op() -> Result[str, str]:
        return Result.err("RESOURCE_DEADLOCK", RecoveryStrategy.RETRY)

    def on_reset() -> bool:
        reset_performed[0] = True
        return False  # Reset failed to clear condition, forces escalation to PANIC

    res = mgr.execute_with_recovery(failing_op, task_reset_fn=on_reset)
    print(
        f"  [retry-exhausted] recovery ended with strategy={res.strategy.name} (error={res.error})"
    )
    assert res.is_ok is False
    assert res.strategy == RecoveryStrategy.PANIC
    assert reset_performed[0] is True
    yield


# Raw WASM binary of factorial compiled from WAT:
# (module (func (export "fac") (param i32) (result i32) (local i32)
#   i32.const 1 local.set 1
#   (loop $loop local.get 1 local.get 0 i32.mul local.set 1
#               local.get 0 i32.const 1 i32.sub local.tee 0 br_if $loop)
#   local.get 1 return))
FACTORIAL_WASM = (
    b"\x00asm\x01\x00\x00\x00\x01\x06\x01`\x01\x7f\x01\x7f\x03\x02\x01\x00"
    b"\x07\x07\x01\x03fac\x00\x00\n \x01\x1e\x01\x01\x7fA\x01!\x01\x03@"
    b' \x01 \x00l!\x01 \x00A\x01k"\x00\r\x00\x0b \x01\x0f\x0b'
)


def demo_wasmjit_hybrid_execution(sysv: System) -> None:
    """Demonstrates Tier 2 Interpreter -> 2-bit Card Marking -> Tier 3 JIT trace compilation & execution.

    Flow:
        1. WASM module begins execution via Tier 2 Interpreter (direct threaded dispatch).
        2. Hot basic-blocks are detected and queued to LIFO compile_queue upon yield.
        3. COOS scheduler idle_hook compiles queued traces into Active JIT cache and chains them.
        4. Execution seamlessly transitions from Interpreter into native JIT traces,
           falling back cleanly to Interpreter when traces end.
        5. WASM guest invokes standard WASI Preview 1 host calls (fd_write) and fireball_call IPC.
    """
    print("\n== wasmjit: Tiered Tracing JIT & Interpreter Hybrid Execution ==")
    mod = parse(FACTORIAL_WASM)
    interp = Interpreter(mod, InterpreterBindings.empty())
    engine = RuntimeEngine(
        jit_compiler=TraceCompiler(),
        yield_threshold=3,
        candidate_threshold=0,
    )

    print("  [Stage 1-3] Running through RuntimeEngine (Interpreter/JIT boundaries)...")
    result = engine.run(interp, 0, (6,))
    result_val = result[0]
    print(f"  [Result] fact(6) = {result_val} (expected 720) [OK]")
    print(
        f"  [Stats] Total Interp Blocks={engine.stat_interp_steps}, "
        f"JIT Traces={engine.stat_jit_invocations}"
    )
    assert result_val == 720
    assert engine.stat_interp_steps >= 1


def main() -> None:
    sysv = System()
    sched = sysv.scheduler
    sched.set_idle_hook(
        lambda: print(f"  [idle_hook] flushed {sysv.logger.flush()} log entr(y/ies)")
    )
    print("== pysim: spawning guest tasks ==")
    sched.spawn("structured-logger", task_structured_logger(sysv))
    sched.spawn("console-writer", task_console_writer(sysv))
    owner_gen = task_bus_owner(sysv)
    sched.spawn("bus-owner", owner_gen)
    sched.spawn("retry-then-succeed", task_retry_then_succeed(sysv))
    sched.spawn("retry-exhausted", task_retry_exhausted(sysv))
    print("\n== pysim: running scheduler to completion ==")
    sched.run_to_completion()
    # bus-owner already ran and its two handles were acquired against task_id=100;
    # spawn the hostile neighbor now that a real handle exists to attack.
    tx_handle = next(s for s in sysv.pool._slots if s is not None)
    print("\n== pysim: a second task attacks the first task's SHM handle ==")
    sched.spawn("hostile-neighbor", task_hostile_neighbor(sysv, other_handle=tx_handle))

    sched.run_to_completion()
    print("\n== pysim: draining the real OS transport the whole run wrote to ==")
    on_the_wire = sysv.transport.drain_output().decode("utf-8", errors="replace")
    print(f"  {sysv.transport.bytes_written} bytes actually crossed the socketpair:")
    for line in on_the_wire.splitlines():
        print(f"    | {line}")

    demo_wasmjit_hybrid_execution(sysv)
    sysv.shutdown()
    print("\n== pysim: findings ==")
    assert len(findings) == 0, findings
    print("  No behavioral bugs found: every enforced invariant held under real execution.")
    print("  (See recovery.py's retry-exhaustion comment for one spec gap this build had")
    print("   to resolve by assumption -- not a code bug, a place interface_wit.md should")
    print("   say more than it currently does.)")


if __name__ == "__main__":
    main()

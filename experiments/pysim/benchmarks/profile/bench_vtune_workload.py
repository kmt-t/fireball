"""
experiments/pysim/benchmarks/profile/bench_vtune_workload.py
Large, self-checking workload for sampling profilers (Intel VTune, cProfile).

Why this exists: VTune's user-mode sampling interval is fixed at 10 ms, so a 6 s benchmark
yields ~600 samples and per-function shares are noise.  Repeating one small kernel would add
samples but no code coverage.  This workload instead runs a real compiler-generated program:
`guest/suite.c` builds (clang, wasm32 MVP) to a 519-basic-block, 28-function module of 17
distinct kernels -- SHA-256, CRC32, quick/heap sort, integer/float matmul, n-body, Mandelbrot,
LZ77, sieve, expression parser, bytecode VM, call_indirect dispatch, hash table, 64-bit math,
Game of Life, text state machine, sub-word FIR -- so that interpreter dispatch, block lookup,
memory access, calls, and (for the JIT) the working set all vary.

Expected results come from wasmtime running the same wasm natively, i.e. independent of pysim.
Work is deterministic; a different checksum means changed behavior, not just changed speed.
Phases run alone (`--phase`) or one kernel at a time (`--kernel name[:units]`) so that a
profile stays attributable.

"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent.parent
_PYSIM_DIR = _BENCH_DIR.parent
for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_jit",
    _PYSIM_DIR / "tier3_platform",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from dummy_drivers import DummyDriver
from interpreter import Interpreter, InterpreterBindings
from ipc_router import DataType, IPCMessage, IPCRouter, IPCStatus, Role, ScopeKind, pack_key32
from logger import LogDictionary, Logger, LogLevel
from logger_driver import FileLogSink, LoggerDriver
from memory import FB_CONF_MEMORY_POOL_SIZE, MemoryManager
from runtime_engine import RuntimeEngine
from scheduler import ChannelAction, Scheduler
from system import System
from system_containers import StaticVector
from wasi import WasiHostContext
from wasm_module import Module
from wasm_reader import parse
from x64_jit import TraceCompiler

SUITE_WASM_PATH = Path(__file__).resolve().parent / "guest" / "suite.wasm"
AO_WASM_PATH = _PYSIM_DIR / "aobench.wasm"
MASK32 = 0xFFFFFFFF
OS_MIX_MAX_SWEEPS = 10_000_000
OS_MIX_WAVE = 8  # concurrent guest tasks per wave; the scheduler allows 16 TCBs in total

# Work units per kernel at --scale 1, chosen so each kernel costs ~5 s on the Tier 2
# interpreter (measured per-unit cost on the reference host; see the calibration in the
# commit message).  Shares stay balanced, so no single kernel dominates the profile.
KERNEL_UNITS: dict[str, int] = {
    "k_sha256": 120,
    "k_crc32": 20,
    "k_sort": 1,
    "k_matmul": 12,
    "k_fmatmul": 10,
    "k_nbody": 700,
    "k_mandel": 75,
    "k_lz": 12,
    "k_sieve": 7,
    "k_expr": 500,
    "k_vm": 34,
    "k_dispatch": 15_000,
    "k_hash": 50,
    "k_int64": 2_700,
    "k_life": 22,
    "k_text": 25,
    "k_fir": 17,
}


@dataclass(frozen=True)
class KernelRun:
    name: str
    units: int
    seconds: float
    result: int


@dataclass(frozen=True)
class PhaseResult:
    name: str
    seconds: float
    work: str
    checksum: int
    detail: tuple[KernelRun, ...] = ()


def _scaled(value: int, scale: float) -> int:
    return max(1, int(value * scale))


def _crc(data: bytes) -> int:
    return zlib.crc32(data) & MASK32


class SuiteOracle:
    """Reference results from wasmtime executing suite.wasm natively (independent of pysim)."""

    def __init__(self, wasm: bytes) -> None:
        import wasmtime

        engine = wasmtime.Engine()
        module = wasmtime.Module(engine, wasm)
        self._store = wasmtime.Store(engine)
        self._exports = wasmtime.Instance(self._store, module, []).exports(self._store)

    def expected(self, kernel: str, units: int) -> int:
        return self._exports[kernel](self._store, units) & MASK32


LOG_DIR = Path(tempfile.gettempdir())


def _open_log(phase: str) -> tuple[Path, FileLogSink]:
    """One fresh log file per phase run, written through the logger HAL driver's sink."""
    path = LOG_DIR / f"fireball_vtune_{phase}.log"
    return path, FileLogSink(path.open("wb", buffering=64 * 1024))


def _start_logger(sysv: System, sink: FileLogSink) -> None:
    """Bind the system logger to the file sink through the logger HAL driver."""
    sysv.start_logger_driver(LoggerDriver(sysv.wasi_hal_bindings.logger_uri, sink), sink)


def _count_in_log(path: Path, marker: bytes) -> int:
    return path.read_bytes().count(marker)


def _new_guest():
    module = parse(SUITE_WASM_PATH.read_bytes())
    memory = bytearray(module.memory.min_pages * 65536)
    module.init_memory_data(memory, ())
    bindings = InterpreterBindings.with_memory_and_functions(memory, StaticVector(capacity=0))
    return module, Interpreter(module, bindings)


def _select_kernels(scale: float, override: list[str] | None) -> list[tuple[str, int]]:
    if override:
        picked = []
        for item in override:
            name, _, units = item.partition(":")
            assert name in KERNEL_UNITS, (
                f"unknown kernel {name}; choose from {sorted(KERNEL_UNITS)}"
            )
            picked.append((name, int(units) if units else _scaled(KERNEL_UNITS[name], scale)))
        return picked
    return [(name, _scaled(units, scale)) for name, units in KERNEL_UNITS.items()]


def _run_suite(
    kernels: list[tuple[str, int]],
    oracle: SuiteOracle | None,
    call: Callable[[Interpreter, Module, int, int], int],
    phase: str,
) -> PhaseResult:
    t_load = time.perf_counter()
    module, interp = _new_guest()
    load_seconds = time.perf_counter() - t_load
    runs: list[KernelRun] = []
    checksum = 0
    t_total = time.perf_counter()
    for name, units in kernels:
        t0 = time.perf_counter()
        result = call(interp, module, module.export_func_index(name), units) & MASK32
        dt = time.perf_counter() - t0
        if oracle is not None:
            assert result == oracle.expected(name, units), f"{name}({units}) diverged from wasmtime"
        runs.append(KernelRun(name, units, dt, result))
        checksum = zlib.crc32(result.to_bytes(4, "little"), checksum) & MASK32
    seconds = time.perf_counter() - t_total
    return PhaseResult(
        phase,
        seconds,
        f"{len(runs)} kernels, load {load_seconds * 1000:.0f} ms",
        checksum,
        tuple(runs),
    )


def phase_suite_interp(scale: float, kernels: list[str] | None, oracle: bool) -> PhaseResult:
    """Every kernel on the Tier 2 interpreter, sharing one guest instance."""
    picked = _select_kernels(scale, kernels)
    ref = SuiteOracle(SUITE_WASM_PATH.read_bytes()) if oracle else None

    def call(interp, module, index, units):
        return interp.call(index, [units])[0]

    return _run_suite(picked, ref, call, "suite_interp")


def phase_suite_jit(scale: float, kernels: list[str] | None, oracle: bool) -> PhaseResult:
    """Every kernel on the Tier 3 hybrid interpreter + copy-and-patch JIT (one shared engine)."""
    picked = _select_kernels(scale, kernels)
    ref = SuiteOracle(SUITE_WASM_PATH.read_bytes()) if oracle else None
    engine_holder: list[RuntimeEngine] = []

    def call(interp, module, index, units):
        if not engine_holder:
            engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=16)
            engine.register_module_blocks(module)
            engine_holder.append(engine)
        return engine_holder[0].run(interp, index, [units])[0]

    return _run_suite(picked, ref, call, "suite_jit")


def phase_os_mix(scale: float, kernels: list[str] | None, oracle: bool) -> PhaseResult:
    """All kernels as concurrent guests under the COOS scheduler with structured logging.

    Each task owns its module instance and linear memory (one runtime, one guest) and yields
    to the round-robin scheduler after every basic block, so scheduler dispatch, interpreter
    stepping, and logging are exercised together.  Uses a quarter of the suite work.
    """
    picked = [(n, max(1, u // 4)) for n, u in _select_kernels(scale, kernels)]
    ref = SuiteOracle(SUITE_WASM_PATH.read_bytes()) if oracle else None
    checksum = 0
    runs: list[KernelRun] = []
    seconds = 0.0
    log_path, sink = _open_log("os_mix")
    sysv = System()
    _start_logger(sysv, sink)  # scheduler, IPC, and guests all log through this Logger
    sysv.dictionary.register(0x200, "KERNEL_DONE: idx=%d result=%d")

    def guest(index: int, name: str, units: int):
        module, interp = _new_guest()
        steps = interp.run_iter(module.export_func_index(name), [units])
        state = next(steps)
        for state in steps:
            if not state.finished:
                yield (ChannelAction.YIELD, None)
        result = state.results[0] & MASK32
        assert sysv.logger.log_event(LogLevel.INFO, 0x200, index, result) == "QUEUED"
        return result

    # The scheduler holds 16 TCBs and returns a finished task's slot on the next spawn, so the
    # 17 guests run in waves of OS_MIX_WAVE on one system.  Results are read before the next wave.
    for wave in range(0, len(picked), OS_MIX_WAVE):
        members = picked[wave : wave + OS_MIX_WAVE]
        task_ids = [
            sysv.scheduler.spawn(name, guest(wave + i, name, units))
            for i, (name, units) in enumerate(members)
        ]
        t0 = time.perf_counter()
        # One sweep runs ~1000 scheduler steps; guests take 10^5..10^6 block steps in total.
        sysv.scheduler.run_to_completion(max_sweeps=OS_MIX_MAX_SWEEPS)
        seconds += time.perf_counter() - t0
        sysv.logger.flush()
        for task_id, (name, units) in zip(task_ids, members, strict=True):
            task = sysv.scheduler.get_task(task_id)
            assert task is not None and task.result is not None, f"{name} did not finish"
            result = task.result & MASK32
            if ref is not None:
                assert result == ref.expected(name, units), (
                    f"{name}({units}) diverged from wasmtime"
                )
            runs.append(KernelRun(name, units, 0.0, result))
            checksum = zlib.crc32(result.to_bytes(4, "little"), checksum) & MASK32
    sink.close()
    assert _count_in_log(log_path, b"KERNEL_DONE:") == len(picked), "structured log entries lost"
    work = f"{len(picked)} guests, waves of {OS_MIX_WAVE}, {sink.bytes_written} log bytes"
    return PhaseResult("os_mix", seconds, work, checksum, tuple(runs))


# --------------------------------------------------------------- AO-Bench

AO_SHADES = frozenset(b" .:+#@\n")
AO_MAX_FRAME_BYTES = 60_000  # frame lives in guest linear memory (1 page, max 16)


def _ao_size(scale: float) -> tuple[int, int]:
    width, height = 64, _scaled(32, scale)
    assert (width + 1) * height <= AO_MAX_FRAME_BYTES, "AO frame does not fit guest memory"
    return width, height


def _ao_guest(phase: str):
    module = parse(AO_WASM_PATH.read_bytes())
    sysv = System()
    _, sink = _open_log(phase)
    _start_logger(sysv, sink)  # keep system logs out of the guest's stdout stream
    wasi_ctx = WasiHostContext(sysv)
    sysv.start_hal_driver(DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport))
    funcs = wasi_ctx.build_interpreter_host_functions(module)
    module.init_memory_data(wasi_ctx.guest_memory, ())
    interp = Interpreter(
        module, InterpreterBindings.with_memory_and_functions(wasi_ctx.guest_memory, funcs)
    )
    return module, sysv, interp, sink


def _check_ao_frame(frame: bytes, width: int, height: int) -> None:
    assert len(frame) == (width + 1) * height, (len(frame), width, height)
    assert set(frame) <= AO_SHADES, "unexpected byte in AO frame"
    rows = frame.split(b"\n")
    assert len(rows) == height + 1 and rows[-1] == b"", "row count mismatch"
    assert all(len(r) == width for r in rows[:-1]), "ragged AO row"
    assert any(ch in b".:+#@" for ch in frame), "AO frame has no scene hits"


def phase_ao_interp(scale: float, kernels: list[str] | None, oracle: bool) -> PhaseResult:
    width, height = _ao_size(scale)
    module, sysv, interp, sink = _ao_guest("ao_interp")
    t0 = time.perf_counter()
    interp.call(module.export_func_index("main"), [width, height])
    seconds = time.perf_counter() - t0
    sysv.logger.flush()
    sink.close()
    frame = sysv.transport.drain_output()
    _check_ao_frame(frame, width, height)
    return PhaseResult(
        "ao_interp", seconds, f"AO {width}x{height} px, {sink.bytes_written} log B", _crc(frame)
    )


def phase_ao_jit(scale: float, kernels: list[str] | None, oracle: bool) -> PhaseResult:
    width, height = _ao_size(scale)
    module, sysv, interp, sink = _ao_guest("ao_jit")
    engine = RuntimeEngine(jit_compiler=TraceCompiler(), yield_threshold=16)
    engine.register_module_blocks(module)
    t0 = time.perf_counter()
    engine.run(interp, module.export_func_index("main"), [width, height])
    seconds = time.perf_counter() - t0
    sysv.logger.flush()
    sink.close()
    frame = sysv.transport.drain_output()
    _check_ao_frame(frame, width, height)
    return PhaseResult(
        "ao_jit", seconds, f"AO {width}x{height} px, {sink.bytes_written} log B", _crc(frame)
    )


# ------------------------------------------------------------ IPC stress

_KEY_SEQ = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=1)
_KEY_VAL = pack_key32(ScopeKind.FUNCTIONAL, DataType.UINT32, key_id=2)
_LOG_ID_MSG = 0x100


def phase_ipc(scale: float, kernels: list[str] | None, oracle: bool) -> PhaseResult:
    """IPC router CSP rendezvous and buffered logging under sustained message load."""
    count = _scaled(300_000, scale)
    sched = Scheduler()
    manager = MemoryManager(sched)
    assert manager.init_manager(0x20020000, FB_CONF_MEMORY_POOL_SIZE).is_ok
    router = IPCRouter(sched, manager)
    log_path, sink = _open_log("ipc")
    dictionary = LogDictionary()
    dictionary.register(_LOG_ID_MSG, "MSG: seq=%d val=%d")
    logger = Logger(transport=sink, dictionary=dictionary, min_level=LogLevel.INFO)
    sent_sum = [0]
    recv_sum = [0]
    received = [0]

    def producer():
        status, channel = router.lookup("fireball://core/coos/0")
        assert status == IPCStatus.COMPLETED and channel is not None
        for seq in range(count):
            value = (seq * 2654435761) & MASK32
            entries = [(_KEY_SEQ, seq), (_KEY_VAL, value)]
            msg = IPCMessage.from_entries(entries, memory_manager=manager)
            status, _ = yield from router.send(channel, msg)
            assert status == IPCStatus.COMPLETED, status
            sent_sum[0] = (sent_sum[0] + value) & MASK32
            assert logger.log_event(LogLevel.INFO, _LOG_ID_MSG, seq, value) == "QUEUED"

    def consumer():
        for seq in range(count):
            status, msg = yield from router.recv()
            assert status == IPCStatus.COMPLETED, status
            assert msg[_KEY_SEQ] == seq, (msg[_KEY_SEQ], seq)
            recv_sum[0] = (recv_sum[0] + msg[_KEY_VAL]) & MASK32
            received[0] += 1
            if received[0] % 8 == 0:
                logger.flush()

    sched.spawn("coos_receiver", consumer(), role=Role.CORE_SERVICE)
    sched.spawn("client_app", producer(), role=Role.RUNTIME)
    t0 = time.perf_counter()
    # One sweep runs ~1000 scheduler steps, so a large message count needs many sweeps.
    sched.run_to_completion(max_sweeps=OS_MIX_MAX_SWEEPS)
    seconds = time.perf_counter() - t0
    logger.flush()
    sink.close()
    assert received[0] == count, (received[0], count)
    assert recv_sum[0] == sent_sum[0], "payload lost or corrupted across rendezvous"
    assert _count_in_log(log_path, b"MSG: seq=") == count, "log entries lost"
    work = f"{count:,} msgs, {sink.bytes_written // 1024} KiB log"
    return PhaseResult("ipc", seconds, work, recv_sum[0])


PHASES: dict[str, Callable[[float, list[str] | None, bool], PhaseResult]] = {
    "suite_interp": phase_suite_interp,
    "suite_jit": phase_suite_jit,
    "os_mix": phase_os_mix,
    "ao_interp": phase_ao_interp,
    "ao_jit": phase_ao_jit,
    "ipc": phase_ipc,
}


def main(argv: list[str] | None = None) -> int:
    global LOG_DIR
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--phase", action="append", choices=sorted(PHASES), help="repeatable")
    parser.add_argument(
        "--scale", type=float, default=1.0, help="multiplies work; 1.0 ~ 85 s/suite"
    )
    parser.add_argument("--kernel", action="append", metavar="NAME[:UNITS]", help="suite phases")
    parser.add_argument("--no-oracle", action="store_true", help="skip the wasmtime comparison")
    parser.add_argument("--list", action="store_true", help="list phases and kernels, then exit")
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR, help="where phase logs are saved")
    args = parser.parse_args(argv)
    LOG_DIR = args.log_dir
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.list:
        print("phases:", ", ".join(PHASES))
        print("kernels (units at scale 1):", ", ".join(f"{k}:{v}" for k, v in KERNEL_UNITS.items()))
        return 0

    names = args.phase or list(PHASES)
    print(f"scale={args.scale} phases={','.join(names)}")
    for name in names:
        result = PHASES[name](args.scale, args.kernel, not args.no_oracle)
        print(
            f"{result.name:13s} {result.seconds:8.2f} s  {result.work:34s} "
            f"checksum=0x{result.checksum:08X}  [PASS]"
        )
        for run in result.detail:
            timing = f"{run.seconds:7.2f} s" if run.seconds else "       -  "
            print(f"    {run.name:11s} units={run.units:<7d} {timing}  result=0x{run.result:08X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

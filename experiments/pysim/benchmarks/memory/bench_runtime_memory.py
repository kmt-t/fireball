"""Measure existing runtime arena charges, including alignment and retained workspaces.

The arena is an accounting model, not a CPython heap or MCU RAM measurement.
Loader records use reference-layout charges; native views use the host ctypes ABI.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parents[1]
_PYSIM_DIR = _BENCH_DIR.parent
sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)
sys.path.insert(0, str(_BENCH_DIR / "interpreter"))

import wasmtime
from bench_call_dispatch import WAT
from bench_jit import JITCompilerBenchmark
from bump_allocator import BumpAllocator
from config import FB_CONF_RUNTIME_BUMP_ARENA_BYTES, JIT_CACHE_REGION_BYTES
from ipc_router import FB_URI_HAL_STDOUT
from qa.shared.jit_manager import JITRuntimeManager
from qa.shared.x64_jit import TraceCompiler
from system import System
from system_containers import StaticVector
from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
from tier2_runtime.runtime.engine import RuntimeEngine
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.wasi.context import WasiHostContext


@dataclass(frozen=True)
class Allocation:
    phase: str
    origin: str
    size: int
    padding: int


class MeasuredArena(BumpAllocator):
    """Observe allocation events without changing allocator policy or product code."""

    def __init__(self) -> None:
        super().__init__()
        self.phase = "load"
        self.allocations: list[Allocation] = []

    def allocate(self, size: int, alignment: int = 4) -> int:
        before = self.offset
        result = super().allocate(size, alignment)
        frame = sys._getframe(1)
        if frame.f_code.co_name in ("acquire", "_allocate_loader_vector"):
            assert frame.f_back is not None
            frame = frame.f_back
        self.allocations.append(
            Allocation(self.phase, frame.f_code.co_qualname, size, result - before)
        )
        return result


@dataclass(frozen=True)
class Workload:
    name: str
    wasm: bytes
    export: str
    arguments: tuple[int, ...]
    wasi: bool = False


@dataclass(frozen=True)
class AllocationTotal:
    phase: str
    origin: str
    allocations: int
    payload_bytes: int
    padding_bytes: int


@dataclass(frozen=True)
class JITStorage:
    name: str
    payload_bytes: int


@dataclass(frozen=True)
class Measurement:
    workload: str
    mode: str
    wasm_sha256: str
    wasm_source_bytes: int
    functions: int
    basic_blocks: int
    load_bytes: int
    instantiated_bytes: int
    after_call_bytes: tuple[int, ...]
    arena_peak_bytes: int
    alignment_padding_bytes: int
    guest_linear_memory_bytes: int
    jit_executable_region_bytes: int
    result_words: tuple[int, ...]
    output_sha256: str
    resident_traces_after_call: tuple[int, ...]
    jit_invocations: int
    allocation_totals: tuple[AllocationTotal, ...]
    dispatch_workspace_bytes: int
    resident_trace_bytes: int
    jit_unaccounted_storage: tuple[JITStorage, ...]


def _measure(workload: Workload, hybrid: bool, calls: int) -> Measurement:
    arena = MeasuredArena()
    jit = JITRuntimeManager(jit_compiler=TraceCompiler()) if hybrid else None
    engine = RuntimeEngine(bump_allocator=arena, jit_runtime=jit, collect_runtime_stats=True)
    module = engine.load_wasm(workload.wasm)
    loaded = arena.offset
    arena.phase = "instantiate"
    sysv = System()
    if workload.wasi:
        wasi = WasiHostContext(sysv)
        sysv.start_hal_driver(DummyDriver(sysv.pool, transport=sysv.transport), FB_URI_HAL_STDOUT)
        memory = wasi.guest_memory
        host_functions = wasi.build_interpreter_host_functions(module)
    else:
        memory = bytearray(0 if module.memory is None else module.memory.min_pages * 65536)
        host_functions = StaticVector(capacity=0)
    module.init_memory_data(memory, ())
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.with_memory_and_functions(memory, host_functions),
        bump_allocator=arena,
    )
    instantiated = arena.offset
    after_calls: list[int] = []
    traces: list[int] = []
    expected_result: tuple[int, ...] | None = None
    expected_output: bytes | None = None
    for call_index in range(calls):
        arena.phase = f"call_{call_index + 1}"
        result = tuple(
            engine.call(
                interpreter, module.export_func_index(workload.export), list(workload.arguments)
            )
        )
        output = sysv.transport.drain_output()
        if expected_result is None:
            expected_result, expected_output = result, output
        assert result == expected_result
        assert output == expected_output
        after_calls.append(arena.offset)
        traces.append(0 if jit is None else jit.cache.resident_count)
    assert expected_result is not None and expected_output is not None
    if workload.wasi:
        assert len(expected_output) == (workload.arguments[0] + 1) * workload.arguments[1]
    else:
        oracle_engine = wasmtime.Engine()
        oracle_store = wasmtime.Store(oracle_engine)
        oracle_module = wasmtime.Module(oracle_engine, workload.wasm)
        oracle_instance = wasmtime.Instance(oracle_store, oracle_module, [])
        oracle_function = oracle_instance.exports(oracle_store)[workload.export]
        assert expected_result == (oracle_function(oracle_store, *workload.arguments),)
    # Released workspaces remain reserved. Repeated calls must reach a stable watermark.
    assert after_calls[-1] == after_calls[-2], after_calls
    assert sum(event.size + event.padding for event in arena.allocations) == arena.offset
    totals: list[AllocationTotal] = []
    for phase, origin in sorted({(event.phase, event.origin) for event in arena.allocations}):
        events = [
            event for event in arena.allocations if event.phase == phase and event.origin == origin
        ]
        totals.append(
            AllocationTotal(
                phase,
                origin,
                len(events),
                sum(event.size for event in events),
                sum(event.padding for event in events),
            )
        )
    unaccounted: tuple[JITStorage, ...] = ()
    if jit is not None:
        native_storage = jit.cache._native
        # Native trace counters are included in the owning JitRuntime storage.
        unaccounted = (
            JITStorage("hotspot_state_bits", len(jit.bitmap.storage.buffer)),
            JITStorage("trackable_mask_bits", len(jit.trackable.storage.buffer)),
            JITStorage("card_update_bits", len(jit.update_bitmap.storage.buffer)),
            JITStorage(
                "native_jit_cache_runtime",
                native_storage.arena_size if native_storage.arena_offset is None else 0,
            ),
        )
    return Measurement(
        workload.name,
        "hybrid" if hybrid else "native",
        hashlib.sha256(workload.wasm).hexdigest(),
        len(workload.wasm),
        len(module.functions) + len(module.imports),
        module.total_basic_blocks,
        loaded,
        instantiated,
        tuple(after_calls),
        arena.offset,
        sum(event.padding for event in arena.allocations),
        len(memory),
        JIT_CACHE_REGION_BYTES if hybrid else 0,
        expected_result,
        hashlib.sha256(expected_output).hexdigest(),
        tuple(traces),
        engine.stat_jit_invocations,
        tuple(totals),
        0 if jit is None else jit.native_dispatch_state().arena_size,
        0 if jit is None else jit.cache.resident_bytes,
        unaccounted,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert args.calls >= 3
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_PYSIM_DIR, text=True
    ).strip()
    workloads = (
        Workload(
            "arithmetic", JITCompilerBenchmark()._create_heavy_loop_binary(), "heavy_loop", (1000,)
        ),
        Workload("indirect_calls", bytes(wasmtime.wat2wasm(WAT)), "bench_indirect", (100,)),
        Workload(
            "kernel_suite", (_BENCH_DIR / "profile/guest/suite.wasm").read_bytes(), "k_crc32", (1,)
        ),
        Workload(
            "aobench",
            (_BENCH_DIR / "aobench/aobench.wasm").read_bytes(),
            "main",
            (32, 16),
            wasi=True,
        ),
    )
    measurements: list[Measurement] = []
    for workload in workloads:
        native = _measure(workload, False, args.calls)
        hybrid = _measure(workload, True, args.calls)
        assert native.result_words == hybrid.result_words
        assert native.output_sha256 == hybrid.output_sha256
        measurements.extend((native, hybrid))
        print(
            f"{workload.name}: load={native.load_bytes}, "
            f"native={native.arena_peak_bytes}, hybrid={hybrid.arena_peak_bytes}, "
            f"guest={native.guest_linear_memory_bytes}",
            flush=True,
        )
    directory = BumpAllocator()
    directory_bytes = sum(
        ctypes.sizeof(storage)
        for storage in (
            directory._workspace_offsets,
            directory._workspace_sizes,
            directory._workspace_alignments,
            directory._workspace_states,
        )
    )
    report = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": revision,
        "source_worktree_modified": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--", "experiments/pysim"],
                cwd=_PYSIM_DIR.parents[1],
                text=True,
            ).strip()
        ),
        "runtime_source_sha256": {
            str(path.relative_to(_PYSIM_DIR)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(_PYSIM_DIR.rglob("*"))
            if path.is_file()
            and path.suffix in (".py", ".cxx", ".hxx", ".so")
            and not any(part in ("qa", "benchmarks", "__pycache__") for part in path.parts)
        },
        "measurement_model": "reference loader charges + host-native ctypes ABI",
        "host_machine": platform.machine(),
        "host_pointer_bytes": ctypes.sizeof(ctypes.c_void_p),
        "python_version": platform.python_version(),
        "arena_capacity_bytes": FB_CONF_RUNTIME_BUMP_ARENA_BYTES,
        "allocator_workspace_directory_bytes": directory_bytes,
        "calls_per_mode": args.calls,
        "measurement_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "allocator_sha256": hashlib.sha256(
            (_PYSIM_DIR / "tier1_core/bump_allocator.py").read_bytes()
        ).hexdigest(),
        "config_sha256": hashlib.sha256(
            (_PYSIM_DIR / "tier1_core/config.py").read_bytes()
        ).hexdigest(),
        "excluded": [
            "Python heap/RSS",
            "JIT manager metadata/compiler",
            "system/IPC/COOS pools",
            "OS/IRQ/native machine stack",
            "target ROM text/rodata",
        ],
        "measurements": [asdict(measurement) for measurement in measurements],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

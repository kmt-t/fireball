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
from _bootstrap import configure_import_paths, reserve_native_region

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)
sys.path.insert(0, str(_BENCH_DIR / "interpreter"))

import wasmtime
from bench_call_dispatch import WAT
from bench_jit import JITCompilerBenchmark
from bump_allocator import BumpAllocator
from config import FB_CONF_RUNTIME_BUMP_ARENA_BYTES, JIT_CACHE_REGION_BYTES
from ipc_router import FB_URI_HAL_STDOUT
from qa.shared.runtime_stats import RuntimeStatsEngine
from system import System
from system_containers import StaticVector
from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
from tier2_runtime.runtime.engine import RuntimeEngine
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.wasi.context import WasiHostContext
from tier3_plugins.jit.jit_manager import JITRuntimeManager


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
class PluginRegionRequest:
    size_bytes: int
    alignment_bytes: int


class MeasuredPluginRegion:
    """Measure the product plugin's public storage request without QA views."""

    def __init__(self) -> None:
        self.requests: list[PluginRegionRequest] = []

    def reserve(self, size: int, alignment: int) -> memoryview:
        self.requests.append(PluginRegionRequest(size, alignment))
        return reserve_native_region(size, alignment)


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
    jit_invocations: int | None
    allocation_totals: tuple[AllocationTotal, ...]
    plugin_region_requests: tuple[PluginRegionRequest, ...]
    plugin_region_bytes: int


@dataclass(frozen=True)
class JITExecutionValidation:
    workload: str
    jit_invocations: int
    wasm_sha256: str
    output_sha256: str


def _measure(
    workload: Workload, hybrid: bool, calls: int, *, observe_execution: bool = False
) -> Measurement:
    arena = MeasuredArena()
    plugin_region = MeasuredPluginRegion()
    jit = JITRuntimeManager(plugin_region.reserve) if hybrid else None
    observer = (
        RuntimeStatsEngine(bump_allocator=arena, jit_runtime=jit, collect_runtime_stats=True)
        if observe_execution
        else None
    )
    engine = observer if observer is not None else RuntimeEngine(bump_allocator=arena)
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
        execution_plugin=jit if observer is None else None,
    )
    instantiated = arena.offset
    after_calls: list[int] = []
    expected_result: tuple[int, ...] | None = None
    expected_output: bytes | None = None
    for call_index in range(calls):
        arena.phase = f"call_{call_index + 1}"
        if observer is None:
            result = tuple(
                interpreter.call(
                    module.export_func_index(workload.export), list(workload.arguments)
                )
            )
        else:
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
    if jit is not None:
        assert len(plugin_region.requests) == 1, plugin_region.requests
        if observer is not None:
            assert observer.stat_jit_invocations > 0
        jit.close()
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
        None if observer is None else observer.stat_jit_invocations,
        tuple(totals),
        tuple(plugin_region.requests),
        sum(request.size_bytes for request in plugin_region.requests),
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
            "arithmetic", JITCompilerBenchmark._create_heavy_loop_binary(), "heavy_loop", (1000,)
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
    jit_execution_validation: list[JITExecutionValidation] = []
    for workload in workloads:
        native = _measure(workload, False, args.calls)
        hybrid = _measure(workload, True, args.calls)
        assert native.result_words == hybrid.result_words
        assert native.output_sha256 == hybrid.output_sha256
        measurements.extend((native, hybrid))
        observed = _measure(workload, True, args.calls, observe_execution=True)
        assert observed.result_words == hybrid.result_words
        assert observed.output_sha256 == hybrid.output_sha256
        assert observed.jit_invocations is not None and observed.jit_invocations > 0
        jit_execution_validation.append(
            JITExecutionValidation(
                workload.name,
                observed.jit_invocations,
                observed.wasm_sha256,
                observed.output_sha256,
            )
        )
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
            and path.suffix in (".py", ".cxx", ".hxx", ".so", ".sh")
            and not any(part in ("qa", "benchmarks", "__pycache__") for part in path.parts)
        },
        "measurement_model": "reference loader charges + host-native ctypes ABI",
        "jit_compile_scratch_accounting": "compiler output is written into the executable cache; stack_locations is included in native stack frames",
        "jit_measurement_path": "product RuntimeEngine and JITRuntimeManager with measured region_provider; no QA dispatcher in memory measurements",
        "jit_execution_validation_path": "separate QA RuntimeStatsEngine and diagnostic interpreter dispatcher; excluded from product memory/ROM totals",
        "jit_execution_validation": [asdict(validation) for validation in jit_execution_validation],
        "execution_observer_sha256": {
            path: hashlib.sha256((_PYSIM_DIR / path).read_bytes()).hexdigest()
            for path in (
                "qa/shared/runtime_stats.py",
                "qa/private/interpreter_native_abi.py",
                "qa/private/libinterpreter_probe.so",
            )
        },
        "plugin_region_includes": [
            "native JitRuntime with execution extension and fixed history/trace/cache arrays",
            "packed card state, dirty and candidate masks",
            "host executable-page alignment padding",
            "JIT executable code region (not additive)",
        ],
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
            "Python adapter objects",
            "QA-only block/profile/snapshot buffers and QA JIT instrumentation",
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

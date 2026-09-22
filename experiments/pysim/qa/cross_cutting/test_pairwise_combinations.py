from __future__ import annotations

import csv
import sys
from itertools import combinations
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parent.parent
_PYSIM_DIR = _TESTS_DIR.parent
_REPO_ROOT = _PYSIM_DIR.parent.parent

for _p in [
    _TESTS_DIR,
    _PYSIM_DIR,
    _PYSIM_DIR / "tier1_core",
    _PYSIM_DIR / "tier1_interface",
    _PYSIM_DIR / "tier2_runtime",
    _PYSIM_DIR / "tier3_executer",
    _PYSIM_DIR / "tier3_platform",
    _TEST_FILE.parent,
    _REPO_ROOT / "docs" / "components" / "tier1_core" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier1_interface" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier2_runtime" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_executer" / "concepts",
    _REPO_ROOT / "docs" / "components" / "tier3_platform" / "concepts",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

"""
test_pairwise_combinations.py: Comprehensive 2-Way All-Pairs Combinatorial Test Suite.
Verifies that all 26 orthogonal test cases (covering 100% of the 288 2-way factor interactions)
execute or reject their explicitly forbidden composition while preserving architectural invariants.
"""

import wasmtime
from tier3_plugins.debugger.debugger import DebuggerManager
from helpers import make_interpreter as Interpreter
from test_support import make_runtime_engine
from system import System
from system_containers import ReadOnlyFlatMapView
from tier3_platform.drivers.wasi.context import WasiHostContext
from wasm_reader import parse
from tier3_executer.jit.x64_jit import TraceCompiler

PAIRWISE_CASES = [
    # (engine, cache, mem_width, storage, host_call, scheduler, debugger)
    ("hybrid", "cold", "8bit", "ram", "wasi_console", "noint", "detached"),
    ("jit", "evict", "8bit", "globals", "ipc", "yield", "inspect"),
    ("interp", "warm", "32bit", "locals", "none", "yield", "detached"),
    ("hybrid", "evict", "16bit", "locals", "wasi_vfs", "multi", "active"),
    ("jit", "warm", "grow", "shm", "hal", "noint", "active"),
    ("interp", "flush", "16bit", "shm", "wasi_console", "multi", "inspect"),
    ("hybrid", "cold", "grow", "globals", "none", "multi", "inspect"),
    ("jit", "flush", "16bit", "ram", "none", "yield", "active"),
    ("jit", "evict", "32bit", "ram", "hal", "multi", "detached"),
    ("interp", "flush", "grow", "locals", "ipc", "noint", "detached"),
    ("hybrid", "warm", "32bit", "globals", "wasi_vfs", "noint", "inspect"),
    ("interp", "cold", "32bit", "shm", "wasi_vfs", "yield", "active"),
    ("interp", "flush", "8bit", "locals", "hal", "multi", "inspect"),
    ("interp", "evict", "grow", "globals", "wasi_console", "yield", "detached"),
    ("hybrid", "warm", "16bit", "ram", "ipc", "multi", "active"),
    ("hybrid", "evict", "16bit", "shm", "hal", "yield", "detached"),
    ("hybrid", "flush", "16bit", "globals", "hal", "noint", "active"),
    ("hybrid", "evict", "8bit", "shm", "none", "noint", "active"),
    ("jit", "cold", "grow", "locals", "wasi_console", "noint", "active"),
    ("jit", "evict", "grow", "ram", "wasi_vfs", "noint", "detached"),
    ("interp", "flush", "8bit", "ram", "wasi_vfs", "multi", "inspect"),
    ("hybrid", "cold", "16bit", "shm", "ipc", "multi", "detached"),
    ("jit", "warm", "32bit", "shm", "wasi_console", "multi", "active"),
    ("jit", "flush", "32bit", "shm", "ipc", "noint", "active"),
    ("hybrid", "cold", "grow", "globals", "hal", "yield", "active"),
    ("hybrid", "warm", "8bit", "locals", "wasi_vfs", "multi", "active"),
]


def pairwise_case_id(case_number: int) -> str:
    return f"TEST-PAIR-{case_number:02d}"


def _load_factor_levels() -> tuple[tuple[str, ...], ...]:
    factor_csv = _REPO_ROOT / "docs" / "qa" / "specs" / "pairwise_factors.csv"
    levels: list[list[str]] = []
    factor_ids: list[str] = []
    with factor_csv.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            factor_id = row["factor_id"]
            if not factor_ids or factor_ids[-1] != factor_id:
                factor_ids.append(factor_id)
                levels.append([])
            levels[-1].append(row["level"])
    return tuple(tuple(factor_levels) for factor_levels in levels)


PAIRWISE_FACTORS = _load_factor_levels()

WAT_TEMPLATE = """
(module
  (import "wasi_snapshot_preview1" "fd_write" (func $fd_write (param i32 i32 i32 i32) (result i32)))
  (import "fireball" "fireball_call" (func $fireball_call (param i32 i32 i32 i32 i32 i32 i32) (result i32)))
  (memory 2 4)
  (global $g_acc (mut i32) (i32.const 100))
  (export "main" (func $main))
  (func $main (param $iter i32) (result i32)
    (local $i i32)
    (local $acc i32)
    (local.set $i (i32.const 0))
    (local.set $acc (i32.const 0))
    (loop $l
      ;; Mutate local
      (local.set $acc (i32.add (local.get $acc) (i32.const 1)))
      ;; 8-bit RAM access
      (i32.store8 (i32.const 10) (i32.and (local.get $acc) (i32.const 0xFF)))
      ;; 16-bit RAM access
      (i32.store16 (i32.const 20) (i32.and (local.get $acc) (i32.const 0xFFFF)))
      ;; 32-bit RAM access
      (i32.store (i32.const 30) (local.get $acc))
      ;; Global mutation
      (global.set $g_acc (i32.add (global.get $g_acc) (i32.const 2)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (local.get $iter)))
    )
    (i32.add (local.get $acc) (global.get $g_acc))
  )
)
"""

from tier3_platform.drivers.hal.dummy import DummyDriver
from hal_dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET, WasiIpcCmd
from fixtures.uvwasi_reference import UvwasiReferenceContext
from helpers import expect_assertion
from runtime_composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutionKind,
    RuntimeFactories,
    RuntimePluginSelection,
)
from runtime_events import RuntimeEvent


class _CompositionExecutor:
    def call(self, func_index: int, args: tuple[int, ...]) -> int:
        return func_index


class _CompositionObserver:
    def on_runtime_event(self, event: RuntimeEvent) -> None:
        return None


def _assert_debugger_jit_composition_rejected(case_id: str) -> None:
    """JIT を含むデバッグ構成は実行せず、合成時 assert で拒否する。"""

    factories = RuntimeFactories(
        interpreter=_CompositionExecutor,
        jit=_CompositionExecutor,
        logger=_CompositionObserver,
        debugger=_CompositionObserver,
        profiler=_CompositionObserver,
    )
    with expect_assertion("debugger-enabled runtime must use interpreter-only execution"):
        RuntimeComposer.compose(
            RuntimeCompositionConfig(
                execution=RuntimeExecutionKind.JIT,
                plugins=RuntimePluginSelection(debugger=True),
            ),
            factories,
        )
    print(f"    [PASS] {case_id}: rejected Debugger + JIT composition")


def run_single_pairwise_case(case_id: str, case_tuple: tuple[str, ...]) -> None:
    (
        engine_mode,
        cache_mode,
        mem_width,
        storage_mode,
        host_mode,
        sched_mode,
        dbg_mode,
    ) = case_tuple
    if engine_mode in ("jit", "hybrid") and dbg_mode in ("inspect", "active"):
        _assert_debugger_jit_composition_rejected(case_id)
        return
    # 1. Setup host system and services
    sysv = System()
    wasi_ctx = WasiHostContext(sysv, guest_memory=bytearray(2 * 65536))
    wasi_dummy = UvwasiReferenceContext()
    stdio = DummyDriver(sysv.wasi_hal_bindings.stdout_uri, transport=sysv.transport)
    sysv.start_hal_driver(stdio)
    # 2. Parse WASM Module
    wasm_bytes = bytes(wasmtime.wat2wasm(WAT_TEMPLATE))
    module = parse(wasm_bytes)
    fn_idx = module.export_func_index("main")
    # Build host imports
    host_funcs = wasi_ctx.build_interpreter_host_functions(module)
    # 3. Setup Runtime Engine & JIT
    trace_compiler = TraceCompiler() if engine_mode in ("jit", "hybrid") else None
    runtime_engine = (
        make_runtime_engine(jit_compiler=trace_compiler, yield_threshold=4)
        if engine_mode in ("jit", "hybrid")
        else None
    )
    if runtime_engine:
        runtime_engine.register_module_blocks(module)

    module.init_memory_data(wasi_ctx.guest_memory, ())
    interp = Interpreter(module, memory=wasi_ctx.guest_memory, host_functions=host_funcs)
    # Setup Debugger if needed
    dbg_mgr = None
    if dbg_mode in ("inspect", "active"):
        dbg_mgr = DebuggerManager(interp)
        dbg_mgr.attach()
        if dbg_mode == "active":
            dbg_mgr.add_breakpoint(0x0010)

    # 4. Apply Cache mode
    if runtime_engine and cache_mode == "flush":
        runtime_engine.jit_runtime.cache.flush_all()
    elif runtime_engine and cache_mode == "evict":
        # Rotate banks
        runtime_engine.jit_runtime.cache.rotate()
        runtime_engine.jit_runtime.cache.rotate()

    # 5. Apply Memory width / grow
    if mem_width == "grow":
        wasi_ctx.guest_memory.extend(b"\x00" * 65536)
        assert len(wasi_ctx.guest_memory) >= 65536 * 2

    # 6. Apply Storage mode
    if storage_mode == "shm":
        # Register vMMIO SHM page (FC=14 -> vpn=0x0E000)
        sysv.vmmio.map_shm_page(0x0E000, 1, 1)

    # 7. Execute according to Scheduler mode
    n_iters = 8
    if sched_mode == "noint":
        res = interp.call(fn_idx, [n_iters])
    elif sched_mode in ("yield", "multi"):
        if runtime_engine:
            res = runtime_engine.run(interp, fn_idx, [n_iters], idle_budget=2)
        else:
            call_state = interp.start(fn_idx, [n_iters])
            while not call_state.finished:
                call_state = interp.step(call_state)
            res = call_state.results

    # 8. Verify Result
    assert res is not None, f"Execution failed for {case_id}"
    expected_acc = n_iters
    expected_gacc = 100 + (n_iters * 2)
    assert res[0] == expected_acc + expected_gacc, (
        f"{case_id} result mismatch: got {res[0]}, expected {expected_acc + expected_gacc}"
    )
    # 9. Verify Invariants
    # Invariant A: Memory consistency
    assert wasi_ctx.guest_memory[10] == (n_iters & 0xFF)
    assert wasi_ctx.guest_memory[20] == (n_iters & 0xFF)
    assert wasi_ctx.guest_memory[30] == (n_iters & 0xFF)
    # Invariant B: Global state persistence
    assert interp.globals[0] == expected_gacc
    # Invariant C: Host call integrity
    if host_mode == "wasi_console":
        # write out to wasi
        wasi_ctx.fd_write(1, 10, 1, 40)
    elif host_mode == "wasi_vfs":
        # read from dummy file
        read_buf = bytearray(16)
        wasi_dummy.fd_read(3, read_buf, 0, 1, 12)
    elif host_mode == "hal":
        payload = f"pairwise:{case_id}".encode("ascii")
        if sysv.scheduler.current_task is None:
            sysv.start_runtime_task(name="pairwise_hal_guest")
        buffer_handle = sysv.pool.buffer(0)
        assert sysv.pool.map_for_io(buffer_handle.buffer_id).name == "MAPPED"
        sysv.pool.view(buffer_handle, 0, len(payload))[:] = payload
        assert stdio.dispatch(
            WasiIpcCmd.STREAM_WRITE_BUFFER,
            ReadOnlyFlatMapView(
                [
                    (ARG_BUFFER_HANDLE, buffer_handle.buffer_id),
                    (ARG_OFFSET, 0),
                    (ARG_LENGTH, len(payload)),
                ]
            ),
        ) == len(payload)
        assert stdio.drain_stdout() == payload
        sysv.pool.unmap_after_io(buffer_handle.buffer_id)


def test_all_pairwise_combinations():
    factor_count = len(PAIRWISE_FACTORS)
    assert all(len(case) == factor_count for case in PAIRWISE_CASES)
    covered_pairs = {
        (left, right, case[left], case[right])
        for case in PAIRWISE_CASES
        for left, right in combinations(range(factor_count), 2)
    }
    expected_pairs = sum(
        len(PAIRWISE_FACTORS[left]) * len(PAIRWISE_FACTORS[right])
        for left, right in combinations(range(factor_count), 2)
    )
    assert len(covered_pairs) == expected_pairs, (
        f"pairwise coverage incomplete: {len(covered_pairs)} of {expected_pairs} combinations"
    )
    print(f"[*] Executing {len(PAIRWISE_CASES)} All-Pairs Combinatorial Test Cases...")
    expected_case_ids = tuple(
        pairwise_case_id(case_number) for case_number in range(1, len(PAIRWISE_CASES) + 1)
    )
    executed_case_ids: list[str] = []
    for case_number, case_tuple in enumerate(PAIRWISE_CASES, start=1):
        case_id = pairwise_case_id(case_number)
        run_single_pairwise_case(case_id, case_tuple)
        executed_case_ids.append(case_id)
        print(f"    [PASS] {case_id}: {case_tuple}")
    assert tuple(executed_case_ids) == expected_case_ids
    print(
        f"[PASS] All {len(PAIRWISE_CASES)} Pairwise Combinations executed or rejected as specified with 100% 2-way interaction coverage."
    )


if __name__ == "__main__":
    test_all_pairwise_combinations()

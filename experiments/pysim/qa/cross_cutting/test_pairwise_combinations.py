"""固定26行の計画被覆と、許可構成の実状態・禁止構成の拒否を検査する。"""

from __future__ import annotations

import csv
from collections.abc import Generator
from itertools import combinations
from pathlib import Path

import wasmtime
from fixtures.uvwasi_reference import UvwasiReferenceContext
from hal_dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET, WasiIpcCmd
from helpers import expect_assertion, make_debug_execution, make_native_interpreter
from ipc_router import IPCStatus, Role
from runtime_composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutionKind,
    RuntimeFactories,
    RuntimePluginSelection,
)
from runtime_events import RuntimeEventBatch
from scheduler import ChannelAction, TaskState, WaitDir
from system import System
from system_containers import ReadOnlyFlatMapView, StaticVector
from test_support import make_runtime_engine
from tier3_executer.jit.x64_jit import TraceCompiler
from tier3_executer.runtime_engine import RuntimeDriveMode, RuntimeEngine
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.wasi.context import WasiHostContext
from tier3_plugins.debugger.debugger import DebuggerManager, GDBRspProtocol
from wasm_module import Module
from wasm_reader import parse

_REPO_ROOT = Path(__file__).resolve().parents[4]

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
    levels: list[list[str]] = []
    factor_ids: list[str] = []
    with (_REPO_ROOT / "docs/qa/specs/pairwise_factors.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        for row in csv.DictReader(stream):
            if not factor_ids or factor_ids[-1] != row["factor_id"]:
                factor_ids.append(row["factor_id"])
                levels.append([])
            levels[-1].append(row["level"])
    return tuple(tuple(factor_levels) for factor_levels in levels)


PAIRWISE_FACTORS = _load_factor_levels()


class _CompositionExecutor:
    def call(self, func_index: int, args: tuple[int, ...]) -> int:
        return func_index


class _CompositionObserver:
    def on_runtime_batch(self, batch: RuntimeEventBatch) -> None:
        return None


def _assert_debugger_jit_composition_rejected() -> None:
    created: list[str] = []

    def executor() -> _CompositionExecutor:
        created.append("executor")
        return _CompositionExecutor()

    factories = RuntimeFactories(
        interpreter=executor,
        jit=executor,
        logger=_CompositionObserver,
        debugger=_CompositionObserver,
        profiler=_CompositionObserver,
    )
    with expect_assertion("debugger-enabled runtime must use interpreter-only execution"):
        RuntimeComposer.compose(
            RuntimeCompositionConfig(
                execution=RuntimeExecutionKind.JIT, plugins=RuntimePluginSelection(debugger=True)
            ),
            factories,
        )
    assert created == [], "forbidden composition must fail before executor construction"


def _case_wat(storage_mode: str, mem_width: str, host_mode: str) -> str:
    address = 0xE0001000 if storage_mode == "shm" else 512
    if storage_mode == "locals":
        initialize, read = "(local.set $acc (i32.const 0))", "(local.get $acc)"
        write = "(local.set $acc (call $inc (local.get $acc)))"
    elif storage_mode == "globals":
        initialize, read = "(global.set $storage (i32.const 0))", "(global.get $storage)"
        write = "(global.set $storage (call $inc (global.get $storage)))"
    else:
        initialize, read = (
            f"(i32.store (i32.const {address}) (i32.const 0))",
            f"(i32.load (i32.const {address}))",
        )
        write = f"(i32.store (i32.const {address}) (call $inc {read}))"
    store = {
        "8bit": "i32.store8",
        "16bit": "i32.store16",
        "32bit": "i32.store",
        "grow": "i32.store",
    }[mem_width]
    host_import = (
        '(import "qa" "host" (func $host (param i32) (result i32)))' if host_mode != "none" else ""
    )
    host_call = "(drop (call $host (local.get $n)))" if host_mode != "none" else ""
    grow = "(global.set $grew (memory.grow (i32.const 1)))" if mem_width == "grow" else ""
    return f"""(module
      {host_import}
      (memory 2 4)
      (global $g_acc (mut i32) (i32.const 100))
      (global $storage (mut i32) (i32.const 0))
      (global $grew (mut i32) (i32.const -1))
      (func $inc (export "inc") (param i32) (result i32)
        (i32.add (local.get 0) (i32.const 1)))
      (func (export "main") (param $n i32) (result i32) (local $i i32) (local $acc i32)
        {initialize}
        (loop $top
          {write}
          ({store} (i32.const 256) (i32.const 305441741))
          (global.set $g_acc (i32.add (global.get $g_acc) (i32.const 2)))
          (local.set $i (i32.add (local.get $i) (i32.const 1)))
          (br_if $top (i32.lt_s (local.get $i) (local.get $n))))
        {grow}
        {host_call}
        (i32.add {read} (global.get $g_acc))))"""


def _prepare_cache(
    engine: RuntimeEngine, module: Module, cache_mode: str, engine_mode: str
) -> None:
    manager = engine.jit_runtime
    if engine_mode == "interp":
        assert manager is None
        return  # Cache factors are inapplicable with JIT disabled.
    assert manager is not None
    cache = manager.cache
    assert all(not bank.traces for bank in (cache.active, cache.warm, cache.oldest))
    inc_index = module.export_func_index("inc")
    block = next(
        block for block in module.blocks if block.head_pc >> 16 == inc_index and block.byte_span
    )

    def install() -> None:
        trace = manager._compile_trace(block.head_pc, block)
        assert trace is not None and cache.insert(trace)
        manager.bitmap.mark_compiled(block.head_pc)
        assert cache.active.get_trace(block.head_pc) is trace

    if cache_mode != "cold":
        install()
        if cache_mode == "warm":
            cache.rotate()
            assert cache.warm.has_trace(block.head_pc) and not cache.active.has_trace(block.head_pc)
        elif cache_mode == "evict":
            for _ in range(3):
                cache.rotate()
            assert all(
                not bank.has_trace(block.head_pc)
                for bank in (cache.active, cache.warm, cache.oldest)
            )
        elif cache_mode == "flush":
            manager.flush_all()
            assert all(not bank.traces for bank in (cache.active, cache.warm, cache.oldest))
    if engine_mode == "jit" and not any(
        bank.has_trace(block.head_pc) for bank in (cache.active, cache.warm, cache.oldest)
    ):
        install()  # Eager JIT setup follows the observed cache transition.


def _probe_debugger(dbg_mode: str) -> None:
    """The existing static debug composition is probed separately from COOS driving."""
    if dbg_mode == "detached":
        return
    module = parse(
        bytes(
            wasmtime.wat2wasm(
                "(module (func (param i32) (result i32) local.get 0 i32.const 1 i32.add))"
            )
        )
    )
    execution = make_debug_execution(module, 0, (7,))
    debugger = DebuggerManager(engine=execution)
    debugger.attach()
    rsp = GDBRspProtocol(debugger)
    response, pc = rsp.handle_packet("g", 0, execution.context, {})
    assert response == GDBRspProtocol.format_packet("00000000" * 4 + "07000000" + "00000000" * 15)
    assert pc == 0 and tuple(execution.context.stack) == ()
    if dbg_mode == "active":
        assert debugger.add_breakpoint(2)
        response, pc = rsp.handle_packet("c", pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05")
        assert pc == 2 and tuple(execution.context.stack) == (7,) and not execution.call.finished
        debugger.remove_breakpoint(2)
    response, _ = rsp.handle_packet("c", pc, execution.context, {})
    assert response == GDBRspProtocol.format_packet("W00")
    assert execution.call.finished and execution.call.results == [8]
    debugger.detach()
    assert not debugger.attached and execution.debugger is None


def run_single_pairwise_case(case_id: str, case_tuple: tuple[str, ...]) -> str:
    engine_mode, cache_mode, mem_width, storage_mode, host_mode, sched_mode, dbg_mode = case_tuple
    if engine_mode in ("jit", "hybrid") and dbg_mode in ("inspect", "active"):
        _assert_debugger_jit_composition_rejected()
        return "rejected"
    sysv = System()
    backend = UvwasiReferenceContext()
    memory = bytearray(b"\xa5" * (2 * 65536))
    wasi = WasiHostContext(sysv, guest_memory=memory, uvwasi=backend)
    driver = DummyDriver(transport=sysv.transport)
    driver_task_id = sysv.start_hal_driver(driver, sysv.wasi_hal_bindings.stdout_uri)
    payload = f"pairwise:{case_id}".encode("ascii")
    memory[1024:1032] = (1200).to_bytes(4, "little") + len(payload).to_bytes(4, "little")
    memory[1200 : 1200 + len(payload)] = payload
    expected_memory = bytearray(memory)
    expected_physical = bytearray(sysv.phys_mem)
    host_calls: list[int] = []
    host_responses: list[int] = []
    n_iters = 48

    def host_call(iterations: int) -> int:
        host_calls.append(iterations)
        if host_mode == "wasi_console":
            result = wasi.fd_write(1, 1024, 1, 1100)
            assert result == 0 and driver.drain_stdout() == payload
            expected_memory[1100:1104] = len(payload).to_bytes(4, "little")
        elif host_mode == "wasi_vfs":
            memory[1028:1032] = (8).to_bytes(4, "little")
            expected_memory[1028:1032] = (8).to_bytes(4, "little")
            result = wasi.fd_read(3, 1024, 1, 1100)
            assert result == 0 and memory[1200:1208] == b"[system]"
            virtual_file = backend.files.view().find(3)
            assert virtual_file is not None and virtual_file.cursor == 8
            expected_memory[1200:1208] = b"[system]"
            expected_memory[1100:1104] = (8).to_bytes(4, "little")
        else:
            handle = sysv.pool.buffer(0)
            assert sysv.pool.map_for_io(handle.buffer_id).name == "MAPPED"
            sysv.pool.view(handle, 0, len(payload))[:] = payload
            params = ReadOnlyFlatMapView(
                sorted(
                    (
                        (ARG_BUFFER_HANDLE, handle.buffer_id),
                        (ARG_OFFSET, 0),
                        (ARG_LENGTH, len(payload)),
                    )
                )
            )
            if host_mode == "ipc":
                response = wasi.core03p.send_ipc_command(
                    sysv.wasi_hal_bindings.stdout_uri, WasiIpcCmd.STREAM_WRITE_BUFFER, params
                )
                assert response.response_code == 0 and response.value == len(payload)
                status, channel = sysv.ipc.lookup(sysv.wasi_hal_bindings.stdout_uri)
                assert status == IPCStatus.COMPLETED and channel is not None
                assert channel.waiter_task is sysv.scheduler.get_task(driver_task_id)
                assert channel.waiter_dir == WaitDir.RECV
                assert channel.reply_payload is None and channel.reply_sender_task is None
                result = response.value
            else:
                assert host_mode == "hal"
                result = wasi.core03p.dispatch_command(
                    sysv.wasi_hal_bindings.stdout_uri, WasiIpcCmd.STREAM_WRITE_BUFFER, params
                )
                assert result == len(payload)
            assert driver.drain_stdout() == payload
            sysv.pool.unmap_after_io(handle.buffer_id)
        host_responses.append(result)
        return result

    module = parse(bytes(wasmtime.wat2wasm(_case_wat(storage_mode, mem_width, host_mode))))
    host_functions = StaticVector.of((host_call,) if host_mode != "none" else (), capacity=1)
    interp = make_native_interpreter(
        module,
        memory=memory,
        host_functions=host_functions,
        vmmio=sysv.vmmio,
        phys_mem=sysv.phys_mem,
    )
    drive_mode = RuntimeDriveMode.SYNCHRONOUS if sched_mode == "noint" else RuntimeDriveMode.COOS
    if engine_mode == "interp":
        engine = RuntimeEngine(yield_threshold=4, drive_mode=drive_mode, collect_runtime_stats=True)
        assert engine.jit_runtime is None
    else:
        engine = make_runtime_engine(
            jit_compiler=TraceCompiler(), yield_threshold=4, drive_mode=drive_mode
        )
        assert engine.jit_runtime is not None
        assert engine.jit_runtime.jit_compiler is not None
    engine.register_module_blocks(module)
    _prepare_cache(engine, module, cache_mode, engine_mode)
    main_index = module.export_func_index("main")
    schedule: list[str] = []
    yielded_pcs: list[int] = []

    def guest_task() -> Generator[tuple[ChannelAction, None], None, tuple[int, ...]]:
        schedule.append("guest-start")
        state = interp.start(main_index, (n_iters,))
        while not state.finished:
            boundary = engine.run(interp, state, idle_budget=2)
            state = boundary.call_state
            if boundary.yield_requested and not state.finished:
                yielded_pcs.append(state.current_pc())
                schedule.append("guest-yield")
                engine.on_yield()
                yield (ChannelAction.YIELD, None)
        result = engine.complete_call(interp, state, idle_budget=2)
        schedule.append("guest-done")
        return tuple(result)

    def peer_task() -> Generator[tuple[ChannelAction, None], None, int]:
        schedule.append("peer-start")
        yield (ChannelAction.YIELD, None)
        schedule.append("peer-done")
        return 73

    try:
        task = sysv.scheduler.current_task
        assert task is not None and task.role == Role.RUNTIME
        if sched_mode != "noint":
            task.coro = guest_task()
            task.state = TaskState.READY
            sysv.scheduler.attach(task)
            sysv.scheduler.current_task = None
        if storage_mode == "shm":
            sysv.vmmio.map_shm_page(0xE0001, phys_page=1, owner_id=task.task_id)
            pte = sysv.vmmio.ptes.view().find(0xE0001)
            assert (
                pte is not None and pte.owner_id == task.task_id and pte.physical_base_addr == 4096
            )
        if sched_mode == "noint":
            results = engine.call(interp, main_index, (n_iters,), idle_budget=2)
            assert sysv.scheduler.current_task is task
            assert schedule == [] and yielded_pcs == []
        else:
            peer_id = (
                sysv.scheduler.spawn("pairwise_peer", peer_task())
                if sched_mode == "multi"
                else None
            )
            sysv.scheduler.run_until_idle()
            assert task.state == TaskState.TERMINATED
            results = task.result
            assert yielded_pcs and schedule.count("guest-yield") == len(yielded_pcs)
            if peer_id is not None:
                peer = sysv.scheduler.get_task(peer_id)
                assert peer is not None and peer.state == TaskState.TERMINATED and peer.result == 73
                assert (
                    schedule.index("guest-start")
                    < schedule.index("peer-start")
                    < schedule.index("guest-done")
                )
                assert (
                    schedule.index("peer-start")
                    < schedule.index("peer-done")
                    < schedule.index("guest-done")
                )
        assert tuple(results) == (100 + 3 * n_iters,), case_id
        assert interp.globals[0] == 100 + 2 * n_iters
        assert interp.globals[1] == (n_iters if storage_mode == "globals" else 0)
        assert interp.globals[2] == (2 if mem_width == "grow" else 0xFFFFFFFF)
        width = {"8bit": 1, "16bit": 2, "32bit": 4, "grow": 4}[mem_width]
        expected_memory[256 : 256 + width] = (0x1234ABCD).to_bytes(4, "little")[:width]
        if storage_mode == "ram":
            expected_memory[512:516] = n_iters.to_bytes(4, "little")
        if storage_mode == "shm":
            expected_physical[4096:4100] = n_iters.to_bytes(4, "little")
        if mem_width == "grow":
            expected_memory.extend(bytes(65536))
        assert memory == expected_memory, (
            f"{case_id}: guest writes, width sentinels, and grow contents"
        )
        assert sysv.phys_mem == expected_physical, (
            f"{case_id}: bounded SHM writes and other physical bytes"
        )
        assert host_calls == ([] if host_mode == "none" else [n_iters])
        assert len(host_responses) == len(host_calls)
        if engine_mode == "interp":
            assert engine.stat_jit_invocations == 0
        else:
            assert engine.stat_jit_invocations > 0, f"{case_id}: no generated trace was executed"
        _probe_debugger(dbg_mode)
        assert memory == expected_memory and sysv.phys_mem == expected_physical
        return "executed"
    finally:
        if engine.jit_runtime is not None:
            engine.jit_runtime.cache.common_code.buffer.close()
        sysv.wasi_backend.close()


def test_all_pairwise_combinations() -> None:
    factor_count = len(PAIRWISE_FACTORS)
    assert all(len(case) == factor_count for case in PAIRWISE_CASES)
    for case in PAIRWISE_CASES:
        assert all(level in PAIRWISE_FACTORS[index] for index, level in enumerate(case))
    planned_pairs = {
        (left, right, case[left], case[right])
        for case in PAIRWISE_CASES
        for left, right in combinations(range(factor_count), 2)
    }
    expected_pairs = {
        (left, right, left_level, right_level)
        for left, right in combinations(range(factor_count), 2)
        for left_level in PAIRWISE_FACTORS[left]
        for right_level in PAIRWISE_FACTORS[right]
    }
    assert planned_pairs == expected_pairs
    expected_case_ids = tuple(
        pairwise_case_id(number) for number in range(1, len(PAIRWISE_CASES) + 1)
    )
    observed: list[tuple[str, str]] = []
    for case_number, case_tuple in enumerate(PAIRWISE_CASES, start=1):
        case_id = pairwise_case_id(case_number)
        outcome = run_single_pairwise_case(case_id, case_tuple)
        assert outcome == (
            "rejected" if case_tuple[0] != "interp" and case_tuple[6] != "detached" else "executed"
        )
        observed.append((case_id, outcome))
        print(f"[PASS] {case_id}: {outcome}: {case_tuple}")
    executed_case_ids = [case_id for case_id, _ in observed]
    assert tuple(executed_case_ids) == expected_case_ids
    print(
        f"[PASS] Static plan: {len(planned_pairs)}/{len(expected_pairs)} factor pairs; "
        f"allowed rows: {sum(outcome == 'executed' for _, outcome in observed)}; "
        f"rejected rows: {sum(outcome == 'rejected' for _, outcome in observed)}. "
        "Cache levels do not apply to interpreter-only rows; debugger probes are separate from COOS driving."
    )


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))

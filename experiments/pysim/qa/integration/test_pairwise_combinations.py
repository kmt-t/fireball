"""固定26行の計画被覆と、許可構成の実状態・禁止構成の拒否を検査する。"""

from __future__ import annotations

import csv
from collections.abc import Generator, Sequence
from contextlib import ExitStack
from itertools import combinations
from pathlib import Path

import pytest
import wasmtime
from ipc_router import FB_URI_HAL_STDOUT, IPCStatus, Role
from qa.private.debugger_support import make_debug_execution
from qa.shared.fixtures.platform_drivers import create_reference_platform_drivers
from qa.shared.fixtures.uvwasi_reference import UvwasiReferenceContext
from qa.shared.helpers import expect_assertion, make_native_interpreter
from qa.shared.runtime_stats import RuntimeStatsEngine as RuntimeEngine
from qa.shared.runtime_support import compile_runtime_block, make_runtime_engine
from qa.shared.x64_jit import TraceCompiler
from scheduler import ChannelAction, TaskState, WaitDir
from system import System
from system_containers import ReadOnlyFlatMapView, StaticVector
from tier2_runtime.hal.dispatch import ARG_BUFFER_HANDLE, ARG_LENGTH, ARG_OFFSET, WasiIpcCmd
from tier2_runtime.observability.events import RuntimeEventBatch, RuntimeExecutionError
from tier2_runtime.runtime.composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutionKind,
    RuntimeFactories,
    RuntimePluginSelection,
)
from tier2_runtime.runtime.engine import RuntimeDriveMode
from tier2_runtime.runtime.recovery import Result
from tier2_runtime.wasm.module import Module
from tier2_runtime.wasm.reader import parse
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.wasi.context import WasiHostContext
from tier3_plugins.debugger.debugger import DebuggerManager, GDBRspProtocol

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
    def call(self, func_index: int, args: Sequence[int]) -> Result[int, RuntimeExecutionError]:
        return Result.ok(func_index)


class _CompositionObserver:
    def on_runtime_batch(self, batch: RuntimeEventBatch) -> None:
        return None


def _assert_debugger_jit_composition_rejected() -> None:
    created: list[str] = []

    def executor() -> _CompositionExecutor:
        created.append("executor")
        return _CompositionExecutor()

    def observer() -> _CompositionObserver:
        created.append("observer")
        return _CompositionObserver()

    factories = RuntimeFactories(
        interpreter=executor,
        jit=executor,
        logger=observer,
        debugger=observer,
        profiler=observer,
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
    host_call = (
        "(local.set $host_result (call $host (local.get $n)))" if host_mode != "none" else ""
    )
    grow = "(global.set $grew (memory.grow (i32.const 1)))" if mem_width == "grow" else ""
    grow_write = "(i32.store8 (i32.const 196607) (i32.const 205))" if mem_width == "grow" else ""
    grow_read = "(i32.load8_u (i32.const 196607))" if mem_width == "grow" else "(i32.const 0)"
    return f"""(module
      {host_import}
      (memory 2 4)
      (global $g_acc (mut i32) (i32.const 100))
      (global $storage (mut i32) (i32.const 0))
      (global $grew (mut i32) (i32.const -1))
      (func $inc (export "inc") (param i32) (result i32)
        (i32.add (local.get 0) (i32.const 1)))
      (func (export "main") (param $n i32) (result i32) (local $i i32) (local $acc i32) (local $host_result i32)
        {initialize}
        (loop $top
          {write}
          ({store} (i32.const 256) (i32.const 305441741))
          (global.set $g_acc (i32.add (global.get $g_acc) (i32.const 2)))
          (local.set $i (i32.add (local.get $i) (i32.const 1)))
          (br_if $top (i32.lt_s (local.get $i) (local.get $n))))
        {grow}
        {grow_write}
        {host_call}
        (i32.add
          (i32.add {read} (global.get $g_acc))
          (i32.add (local.get $host_result) {grow_read}))))"""


def _prepare_cache(
    engine: RuntimeEngine, module: Module, cache_mode: str, engine_mode: str
) -> None:
    manager = engine.jit_runtime
    if engine_mode == "interp":
        assert manager is None
        return  # Cache factors are inapplicable with JIT disabled.
    assert manager is not None
    cache = manager.cache
    assert cache.resident_count == 0
    inc_index = module.export_func_index("inc")
    block = next(
        block for block in module.blocks if block.func_index == inc_index and block.byte_span
    )

    def install() -> None:
        trace = compile_runtime_block(manager, block)
        assert trace is not None and cache.insert(trace)
        assert cache.find_trace(block.head_pc) is trace

    if cache_mode != "cold":
        install()
        if cache_mode == "warm":
            cache.rotate()
            assert cache.lookup(block.head_pc) is not None
            assert cache.promotions == 0
        elif cache_mode == "evict":
            for _ in range(3):
                cache.rotate()
            assert cache.find_trace(block.head_pc) is None
        elif cache_mode == "flush":
            manager.flush_all()
            assert cache.resident_count == 0
    if engine_mode == "jit" and cache.find_trace(block.head_pc) is None:
        install()  # Eager JIT setup follows the observed cache transition.


@pytest.mark.parametrize("dbg_mode", ("inspect", "active"))
def test_debugger_registers_breakpoint_and_resume(dbg_mode: str) -> None:
    """静的デバッグ構成の停止・再開を、組合せワークロードから独立して確認する。"""
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
    pc_base = execution.call.current_pc()
    response, pc = rsp.handle_packet("g", pc_base, execution.context, {})
    expected_registers = (
        pc_base.to_bytes(4, "little").hex() + "00000000" * 3 + "07000000" + "00000000" * 15
    )
    assert response == GDBRspProtocol.format_packet(expected_registers)
    assert pc == pc_base and tuple(execution.context.stack) == ()
    if dbg_mode == "active":
        assert debugger.add_breakpoint(pc_base + 2)
        response, pc = rsp.handle_packet("c", pc, execution.context, {})
        assert response == GDBRspProtocol.format_packet("S05")
        assert pc == pc_base + 2 and tuple(execution.context.stack) == (7,)
        assert not execution.call.finished
        debugger.remove_breakpoint(pc_base + 2)
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
    with ExitStack() as resources:
        return _run_workload(case_id, case_tuple, resources)


def _run_workload(case_id: str, case_tuple: tuple[str, ...], resources: ExitStack) -> str:
    engine_mode, cache_mode, mem_width, storage_mode, host_mode, sched_mode, _ = case_tuple
    backend = UvwasiReferenceContext()
    sysv = System(drivers=create_reference_platform_drivers(backend))
    resources.callback(sysv.shutdown)
    memory = bytearray(b"\xa5" * (2 * 65536))
    wasi = WasiHostContext(sysv, guest_memory=memory, uvwasi=backend)
    driver = DummyDriver(sysv.pool, transport=sysv.transport)
    driver_task_id = sysv.start_hal_driver(driver, FB_URI_HAL_STDOUT)
    payload = f"pairwise:{case_id}".encode("ascii")
    memory[1024:1032] = (1200).to_bytes(4, "little") + len(payload).to_bytes(4, "little")
    memory[1200 : 1200 + len(payload)] = payload
    expected_memory = bytearray(memory)
    expected_physical = bytearray(sysv.phys_mem)
    for index in range(4):
        slot = sysv.pool.buffer(index)._storage
        slot[:] = bytes((0xA5 + index,)) * len(slot)
    expected_slots = [bytes(sysv.pool.buffer(index)._storage) for index in range(4)]
    if host_mode in ("wasi_console", "ipc", "hal"):
        expected_slot = bytearray(expected_slots[0])
        expected_slot[: len(payload)] = payload
        expected_slots[0] = bytes(expected_slot)
    expected_response = len(payload) if host_mode in ("ipc", "hal") else 0
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
                    FB_URI_HAL_STDOUT, WasiIpcCmd.STREAM_WRITE_BUFFER, params
                )
                assert response.response_code == 0 and response.value == len(payload)
                status, channel = sysv.ipc.lookup(FB_URI_HAL_STDOUT)
                assert status == IPCStatus.COMPLETED and channel is not None
                assert channel.waiter_task is sysv.scheduler.get_task(driver_task_id)
                assert channel.waiter_dir == WaitDir.RECV
                assert channel.reply_payload is None and channel.reply_sender_task is None
                result = response.value
            else:
                assert host_mode == "hal"
                result = wasi.core03p.dispatch_command(
                    FB_URI_HAL_STDOUT, WasiIpcCmd.STREAM_WRITE_BUFFER, params
                )
                assert result == len(payload)
            assert driver.drain_stdout() == payload
            sysv.pool.unmap_after_io(handle.buffer_id)
        host_responses.append(result)
        return result

    module = parse(bytes(wasmtime.wat2wasm(_case_wat(storage_mode, mem_width, host_mode))))
    host_functions = StaticVector.of((host_call,) if host_mode != "none" else (), capacity=1)
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
    if engine.jit_runtime is not None:
        resources.callback(engine.jit_runtime.cache._native.close)
    engine.register_module_blocks(module)
    interp = make_native_interpreter(
        module,
        memory=memory,
        host_functions=host_functions,
        vmmio=sysv.vmmio,
        phys_mem=sysv.phys_mem,
    )
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
        assert pte is not None and pte.owner_id == task.task_id and pte.physical_base_addr == 4096
    if sched_mode == "noint":
        results = engine.call(interp, main_index, (n_iters,), idle_budget=2)
        assert sysv.scheduler.current_task is task
        assert schedule == [] and yielded_pcs == []
    else:
        peer_id = (
            sysv.scheduler.spawn("pairwise_peer", peer_task()) if sched_mode == "multi" else None
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
    # ホストから戻った値と拡張領域のloadを、ゲストの最終結果に含める。
    expected_result = 100 + 3 * n_iters + expected_response
    if mem_width == "grow":
        expected_result += 205
    assert tuple(results) == (expected_result,), f"{case_id}: guest result across boundaries"
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
        expected_memory[-1] = 205
    assert memory == expected_memory, f"{case_id}: guest writes, width sentinels, and grow contents"
    assert sysv.phys_mem == expected_physical, (
        f"{case_id}: bounded SHM writes and other physical bytes"
    )
    assert host_calls == ([] if host_mode == "none" else [n_iters])
    assert host_responses == ([] if host_mode == "none" else [expected_response])
    assert [bytes(sysv.pool.buffer(index)._storage) for index in range(4)] == expected_slots
    assert sysv.pool._mapped_task_id is None and sysv.pool._mapped_buffer_id is None
    with sysv.scheduler.task_context(task):
        for index in range(4):
            assert not sysv.pool.can_view(sysv.pool.buffer(index), 0, 1)
    if engine_mode == "interp":
        assert engine.stat_jit_invocations == 0
    else:
        assert engine.stat_jit_invocations > 0, f"{case_id}: no generated trace was executed"
    return "executed"


def test_pairwise_plan_covers_all_declared_factor_pairs() -> None:
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


@pytest.mark.parametrize(
    "case_number,case_tuple",
    tuple(enumerate(PAIRWISE_CASES, start=1)),
    ids=tuple(pairwise_case_id(number) for number in range(1, len(PAIRWISE_CASES) + 1)),
)
def test_pairwise_case(case_number: int, case_tuple: tuple[str, ...]) -> None:
    """各行を独立実行し、禁止構成の拒否と許可構成の結果を区別する。"""
    case_id = pairwise_case_id(case_number)
    outcome = run_single_pairwise_case(case_id, case_tuple)
    forbidden = case_tuple[0] != "interp" and case_tuple[6] != "detached"
    assert outcome == ("rejected" if forbidden else "executed"), case_id


@pytest.mark.parametrize("engine_mode", ("jit", "hybrid"))
@pytest.mark.parametrize("cache_mode", ("warm", "flush"))
def test_jit_cache_transition_preserves_coos_shm_growth_and_ipc_reply(
    engine_mode: str, cache_mode: str
) -> None:
    """26行で実行しないcache水準を、実JIT・COOS・SHM・IPCの結合で確認する。"""
    case_tuple = (engine_mode, cache_mode, "grow", "shm", "ipc", "multi", "detached")
    assert run_single_pairwise_case(f"cache-{engine_mode}-{cache_mode}", case_tuple) == "executed"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))

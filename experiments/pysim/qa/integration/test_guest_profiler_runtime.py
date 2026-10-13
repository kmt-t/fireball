"""実ゲストの公開call境界からGuest Profilerの集計までを検証する。"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass

import pytest
import wasmtime
from qa.shared.helpers import make_native_interpreter
from qa.shared.runtime_stats import RuntimeStatsEngine as RuntimeEngine
from qa.shared.runtime_support import compile_runtime_block, make_runtime_engine
from qa.shared.x64_jit import TraceCompiler
from tier2_runtime.interpreter.interpreter import (
    InterpreterCall,
    NativeInterpreter,
    TrapCode,
    WasmNumber,
)
from tier2_runtime.observability.events import RuntimeExecutionError
from tier2_runtime.observability.logger import LogDictionary, Logger, LogLevel
from tier2_runtime.runtime.composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeFactories,
    RuntimePluginSelection,
)
from tier2_runtime.runtime.recovery import Result
from tier3_plugins.profiler.aggregate import (
    ProfileReport,
    aggregate_profile,
    decode_profile_records,
)
from tier3_plugins.profiler.guest_profiler import GuestProfiler

_GUEST_WAT = """(module
  (memory 1)
  (global $total (mut i32) (i32.const 0))
  (func $inc (export "inc") (param i32) (result i32)
    (i32.add (local.get 0) (i32.const 1)))
  (func (export "sum") (param $n i32) (result i32)
    (local $i i32) (local $sum i32)
    (loop $again
      (local.set $sum (i32.add (local.get $sum) (call $inc (local.get $i))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_s (local.get $i) (local.get $n))))
    (global.set $total (i32.add (global.get $total) (local.get $n)))
    (i32.store (i32.const 64) (local.get $sum))
    (i32.add (local.get $sum) (global.get $total)))
  (func (export "fail") (result i32)
    (i32.store8 (i32.const 128) (i32.const 204))
    unreachable))"""


class _GuestExecutor:
    """既存のnative境界を駆動し、trap状態を構成器のResult契約へ渡すQAアダプタ。"""

    def __init__(self, engine: RuntimeEngine, interpreter: NativeInterpreter) -> None:
        self.engine = engine
        self.interpreter = interpreter
        self.last_call: InterpreterCall | None = None

    def call(
        self, func_index: int, args: Sequence[WasmNumber]
    ) -> Result[tuple[WasmNumber, ...], RuntimeExecutionError]:
        call = self.interpreter.start(func_index, args)
        while not call.finished:
            call = self.engine.run(self.interpreter, call).call_state
        self.last_call = call
        if call.trap is not None:
            return Result.err(RuntimeExecutionError.GUEST_TRAP)
        assert call.results is not None
        return Result.ok(tuple(call.results))


@dataclass(frozen=True)
class _GuestState:
    """観測有効・無効の比較に使う実行結果とゲスト状態。"""

    values: tuple[WasmNumber, ...]
    trap: TrapCode | None
    memory: bytes
    globals: tuple[WasmNumber, ...]
    instruction_pointer: int
    stack_depths: tuple[int, int, int, int]


class _ProfileWire:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: memoryview) -> int:
        self.data.extend(data)
        return len(data)


def _run_guest_history(
    jit_enabled: bool,
    enabled: bool,
    resources: ExitStack,
) -> tuple[tuple[_GuestState, ...], ProfileReport | None]:
    wire = _ProfileWire()
    dictionary = LogDictionary()
    log_engine = Logger(wire, dictionary, min_level=LogLevel.DEBUG)
    profiler = GuestProfiler(log_engine)
    engine = (
        make_runtime_engine(jit_compiler=TraceCompiler())
        if jit_enabled
        else RuntimeEngine(collect_runtime_stats=True)
    )
    if engine.jit_runtime is not None:
        resources.callback(engine.jit_runtime.cache._native.close)
    module = engine.load_wasm(bytes(wasmtime.wat2wasm(_GUEST_WAT)))
    memory = bytearray(b"\xa5" * 65536)
    interpreter = make_native_interpreter(module, memory=memory)
    if engine.jit_runtime is not None:
        interpreter.attach_execution_plugin(engine.jit_runtime)
    executor = _GuestExecutor(engine, interpreter)
    if engine.jit_runtime is not None:
        inc_index = module.export_func_index("inc")
        block = next(block for block in module.blocks if block.func_index == inc_index)
        trace = compile_runtime_block(engine.jit_runtime, block)
        assert trace is not None
        assert_result_1 = engine.jit_runtime.cache.insert(trace)
        assert assert_result_1
    factories = RuntimeFactories(
        interpreter=lambda: executor,
        logger=GuestProfiler,
        debugger=GuestProfiler,
        profiler=lambda: profiler,
    )
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(plugins=RuntimePluginSelection(profiler=enabled)),
        factories,
        # 時計だけを固定する。イベントは構成器・Sink・ABI・Adapterから実配送する。
        tick_clock=lambda: 10,
    )
    expected_memory = bytearray(memory)
    states: list[_GuestState] = []
    # 1..48の和は1176。globalは48→51→56と累積し、trap前のstoreも保持する。
    history = (
        ("sum", (48,), (1224,), 1176, 48, 1),
        ("sum", (3,), (57,), 6, 51, 2),
        ("inc", (9,), (10,), 6, 51, 1),
        ("fail", (), (), 6, 51, 1),
        ("sum", (5,), (71,), 15, 56, 3),
    )
    for call_number, (name, args, values, stored_sum, total, _calls) in enumerate(history, start=1):
        result = runtime.call(module.export_func_index(name), args)
        call = executor.last_call
        assert call is not None and call.finished
        if name == "fail":
            assert not result.is_ok and result.error == RuntimeExecutionError.GUEST_TRAP
            assert call.trap is not None and call.trap.code == TrapCode.UNREACHABLE
            expected_memory[128] = 204
        else:
            assert result.is_ok and result.unwrap() == values
            assert call.trap is None
        expected_memory[64:68] = stored_sum.to_bytes(4, "little")
        assert memory == expected_memory, f"call {call_number}: guest memory"
        assert tuple(interpreter.globals) == (total,), f"call {call_number}: guest global"
        states.append(
            _GuestState(
                values=tuple(call.results) if call.results is not None else (),
                trap=call.trap.code if call.trap is not None else None,
                memory=bytes(memory),
                globals=tuple(interpreter.globals),
                instruction_pointer=call._ip,
                stack_depths=(
                    len(call.context.stack),
                    len(call.context.local_stack),
                    len(call.context.call_frame_stack),
                ),
            )
        )
    assert (engine.stat_jit_invocations > 0) == jit_enabled
    log_engine.flush()
    report = aggregate_profile(decode_profile_records(bytes(wire.data), dictionary))
    return tuple(states), report if enabled else None


@pytest.mark.parametrize(
    "jit_enabled", (False, True), ids=("interpreter", "interpreter-with-jit-plugin")
)
def test_real_guest_profile_logs_aggregate_call_counts_and_times(jit_enabled: bool) -> None:
    """Runtime events pass through the guest logger and host aggregator."""
    with ExitStack() as resources:
        observed, report = _run_guest_history(jit_enabled, True, resources)
        unobserved, no_report = _run_guest_history(jit_enabled, False, resources)
    assert observed == unobserved, "profiling must preserve guest results and state"
    assert report is not None and no_report is None
    assert tuple(
        (row.function_id, row.call_count, row.inclusive_ticks, row.self_ticks)
        for row in report.functions
    ) == ((0, 1, 1, 1), (1, 3, 3, 3), (2, 1, 2, 2))
    assert len(report.edges) == 0
    assert report.unmatched_exits == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

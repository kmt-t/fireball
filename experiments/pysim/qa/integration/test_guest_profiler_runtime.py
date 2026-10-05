"""実ゲストの公開call境界からGuest Profilerの集計までを検証する。"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass

import pytest
import wasmtime
from qa.shared.helpers import make_native_interpreter
from qa.shared.runtime_support import compile_runtime_block, make_runtime_engine
from qa.shared.x64_jit import TraceCompiler
from tier2_runtime.interpreter.interpreter import (
    InterpreterCall,
    NativeInterpreter,
    TrapCode,
    WasmNumber,
)
from tier2_runtime.observability.events import RuntimeExecutionError
from tier2_runtime.runtime.composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutionKind,
    RuntimeFactories,
    RuntimePluginSelection,
)
from tier2_runtime.runtime.engine import RuntimeEngine
from tier2_runtime.runtime.recovery import Result
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


def _run_guest_history(
    execution: RuntimeExecutionKind,
    profiler: GuestProfiler,
    enabled: bool,
    resources: ExitStack,
) -> tuple[_GuestState, ...]:
    engine = (
        make_runtime_engine(jit_compiler=TraceCompiler())
        if execution == RuntimeExecutionKind.JIT
        else RuntimeEngine(collect_runtime_stats=True)
    )
    if engine.jit_runtime is not None:
        resources.callback(engine.jit_runtime.cache._native.close)
    module = engine.load_wasm(bytes(wasmtime.wat2wasm(_GUEST_WAT)))
    memory = bytearray(b"\xa5" * 65536)
    interpreter = make_native_interpreter(module, memory=memory)
    executor = _GuestExecutor(engine, interpreter)
    if engine.jit_runtime is not None:
        inc_index = module.export_func_index("inc")
        block = next(block for block in module.blocks if block.func_index == inc_index)
        trace = compile_runtime_block(engine.jit_runtime, block)
        assert trace is not None and engine.jit_runtime.cache.insert(trace)
    factories = RuntimeFactories(
        interpreter=lambda: executor,
        jit=lambda: executor,
        logger=GuestProfiler,
        debugger=GuestProfiler,
        profiler=lambda: profiler,
    )
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(
            execution=execution, plugins=RuntimePluginSelection(profiler=enabled)
        ),
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
    for call_number, (name, args, values, stored_sum, total, calls) in enumerate(history, start=1):
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
        assert profiler.open_frame_count == 0, f"call {call_number}: unclosed profile frame"
        assert profiler.overflowed_frame_count == 0
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
                    len(call.context.control_frame_stack),
                ),
            )
        )
        if enabled and profiler.lost_events == 0:
            stats = profiler.stats_for(module.export_func_index(name))
            assert stats.call_count == calls, f"call {call_number}: profile call count"
    if execution == RuntimeExecutionKind.JIT:
        assert engine.stat_jit_invocations > 0, "the real guest must execute generated traces"
    else:
        assert engine.jit_runtime is None and engine.stat_jit_invocations == 0
    return tuple(states)


@pytest.mark.parametrize("execution", tuple(RuntimeExecutionKind), ids=("interpreter", "jit"))
@pytest.mark.parametrize("stack_capacity", (64, 0), ids=("normal", "overflow"))
def test_real_guest_profile_preserves_results_state_and_trap_history(
    execution: RuntimeExecutionKind, stack_capacity: int
) -> None:
    """TEST-PROF-08/09: 実native実行と実Profilerを結線し、無効構成とも比較する。"""
    profiler = GuestProfiler(stack_capacity=stack_capacity)
    disabled_profiler = GuestProfiler()
    with ExitStack() as resources:
        observed = _run_guest_history(execution, profiler, True, resources)
        unobserved = _run_guest_history(execution, disabled_profiler, False, resources)
    assert observed == unobserved, (
        "profiling must preserve results, trap, PC, stack depths and guest state"
    )
    assert disabled_profiler.open_frame_count == 0
    assert disabled_profiler.lost_events == 0 and disabled_profiler.estimated_events == 0
    assert profiler.unmatched_events == 0
    if stack_capacity == 0:
        assert profiler.lost_events == 5
        assert profiler.estimated_events > 0
        return
    normal_ticks, trap_ticks = (1, 2) if execution == RuntimeExecutionKind.INTERPRETER else (3, 4)
    # export順でなくWATの関数宣言順: inc=0, sum=1, fail=2。
    for function, calls, ticks, estimated in (
        (0, 1, normal_ticks, False),
        (1, 3, 3 * normal_ticks, False),
        (2, 1, trap_ticks, True),
    ):
        stats = profiler.stats_for(function)
        assert (stats.call_count, stats.inclusive_ticks, stats.self_ticks, stats.estimated) == (
            calls,
            ticks,
            ticks,
            estimated,
        ), f"function {function}: profile summary"
    assert profiler.lost_events == 0
    assert profiler.estimated_events == 2
    assert profiler.edge_calls(1, 2) == 0, (
        "separate public calls must not become a parent-child edge"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

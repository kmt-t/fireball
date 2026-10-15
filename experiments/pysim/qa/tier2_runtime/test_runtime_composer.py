"""RuntimeComposer の無効アスペクト除去とイベント結線を検証する。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from system_containers import StaticVector
from tier2_runtime.observability.events import (
    RUNTIME_EVENT_ABI_MAJOR,
    RUNTIME_EVENT_NO_MODULE,
    RUNTIME_EVENT_NO_PC,
    RuntimeEvent,
    RuntimeEventAdapter,
    RuntimeEventBatch,
    RuntimeEventExportStatus,
    RuntimeEventFlags,
    RuntimeEventKind,
    RuntimeEventSink,
    RuntimeExecutionError,
)
from tier2_runtime.runtime.composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutor,
    RuntimeFactories,
    RuntimePluginSelection,
    RuntimeWithoutPlugins,
    RuntimeWithPlugins,
)
from tier2_runtime.runtime.recovery import Result


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[int, tuple[int, ...]]] = []

    def call(self, func_index: int, args: Sequence[int]) -> Result[int, RuntimeExecutionError]:
        self.calls.append((func_index, tuple(args)))
        return Result.ok(func_index + sum(args))


class _Observer:
    def __init__(self) -> None:
        self.events: StaticVector[RuntimeEvent] = StaticVector(capacity=8)

    def on_runtime_batch(self, batch: RuntimeEventBatch) -> None:
        for event in batch.records:
            self.events.append(event)


class _Factory:
    def __init__(self) -> None:
        self.calls = 0
        self.instance = _Observer()

    def create(self) -> _Observer:
        self.calls += 1
        self.instance = _Observer()
        return self.instance


def _factories(
    interpreter: RuntimeExecutor[int, int],
) -> tuple[RuntimeFactories[int, int], _Factory, _Factory, _Factory]:
    logger = _Factory()
    debugger = _Factory()
    profiler = _Factory()
    factories = RuntimeFactories(
        lambda: interpreter,
        logger.create,
        debugger.create,
        profiler.create,
    )
    return factories, logger, debugger, profiler


def test_disabled_plugins_are_not_constructed_or_retained() -> None:
    interpreter = _Executor()
    factories, logger, debugger, profiler = _factories(interpreter)

    class ClockProbe:
        calls = 0

        def read(self) -> int:
            self.calls += 1
            return self.calls

    clock = ClockProbe()

    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(),
        factories,
        tick_clock=clock.read,
    )

    assert isinstance(runtime, RuntimeWithoutPlugins)
    assert not hasattr(runtime, "observers")
    assert runtime.call(3, (4,)).unwrap() == 7
    assert interpreter.calls == [(3, (4,))]
    assert logger.calls == 0
    assert debugger.calls == 0
    assert profiler.calls == 0
    assert clock.calls == 0


@pytest.mark.parametrize(
    "selection",
    (
        RuntimePluginSelection(logger=True),
        RuntimePluginSelection(profiler=True),
        RuntimePluginSelection(logger=True, profiler=True),
    ),
    ids=("logger", "profiler", "logger-and-profiler"),
)
def test_selected_plugins_receive_one_shared_event_stream(
    selection: RuntimePluginSelection,
) -> None:
    interpreter = _Executor()
    factories, logger, debugger, profiler = _factories(interpreter)
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(
            plugins=selection,
        ),
        factories,
        runtime_id=9,
        tick_clock=lambda: 10,
    )

    assert isinstance(runtime, RuntimeWithPlugins)
    # 呼出しを繰り返し、識別子とイベント列に前回の再送・混入がないことを確認する。
    assert runtime.call(5, (2,)).unwrap() == 7
    assert runtime.call(3, (4, 6)).unwrap() == 13
    expected_calls = [(5, (2,)), (3, (4, 6))]
    assert interpreter.calls == expected_calls
    assert logger.calls == int(selection.logger)
    assert debugger.calls == 0
    assert profiler.calls == int(selection.profiler)
    kinds = (RuntimeEventKind.FUNCTION_ENTER, RuntimeEventKind.FUNCTION_EXIT)
    flags = RuntimeEventFlags.TICK_VALID | RuntimeEventFlags.INTERPRETER
    expected = tuple(
        RuntimeEvent(
            kind,
            9,
            RUNTIME_EVENT_NO_MODULE,
            function,
            RUNTIME_EVENT_NO_PC,
            10 + (call_id - 1) * len(kinds) + offset,
            call_id,
            flags,
        )
        for call_id, function in ((1, 5), (2, 3))
        for offset, kind in enumerate(kinds)
    )
    assert tuple(logger.instance.events) == (expected if selection.logger else ())
    assert tuple(debugger.instance.events) == ()
    assert tuple(profiler.instance.events) == (expected if selection.profiler else ())


class _ErrorExecutor:
    def __init__(self, error: RuntimeExecutionError) -> None:
        self.error = error
        self.calls: list[tuple[int, tuple[int, ...]]] = []

    def call(self, func_index: int, args: Sequence[int]) -> Result[int, RuntimeExecutionError]:
        self.calls.append((func_index, tuple(args)))
        return Result.err(self.error)


@pytest.mark.parametrize(
    "error",
    (RuntimeExecutionError.GUEST_TRAP, RuntimeExecutionError.HOST_FAILURE),
    ids=("guest-trap", "host-failure"),
)
def test_execution_failure_preserves_result_and_reports_matching_events(
    error: RuntimeExecutionError,
) -> None:
    executor = _ErrorExecutor(error)
    factories, logger, _, _ = _factories(executor)
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(plugins=RuntimePluginSelection(logger=True)),
        factories,
        runtime_id=9,
        tick_clock=lambda: 10,
    )

    result = runtime.call(4, (17,))

    assert not result.is_ok and result.error == error
    assert executor.calls == [(4, (17,))]
    kinds = (RuntimeEventKind.FUNCTION_ENTER,)
    if error == RuntimeExecutionError.GUEST_TRAP:
        kinds += (RuntimeEventKind.TRAP,)
    kinds += (RuntimeEventKind.FUNCTION_EXIT,)
    flags = RuntimeEventFlags.TICK_VALID | RuntimeEventFlags.INTERPRETER
    trap_flags = RuntimeEventFlags.TICK_VALID | RuntimeEventFlags.TRAP
    expected = tuple(
        RuntimeEvent(
            kind,
            9,
            RUNTIME_EVENT_NO_MODULE,
            4,
            RUNTIME_EVENT_NO_PC,
            10 + offset,
            1,
            flags | RuntimeEventFlags.ABORTED | RuntimeEventFlags.ESTIMATED
            if kind == RuntimeEventKind.FUNCTION_EXIT
            else trap_flags
            if kind == RuntimeEventKind.TRAP
            else flags,
            int(error) if kind == RuntimeEventKind.FUNCTION_EXIT else 0,
        )
        for offset, kind in enumerate(kinds)
    )
    assert tuple(logger.instance.events) == expected


def test_debugger_composition_constructs_interpreter_only_runtime() -> None:
    interpreter = _Executor()
    factories, logger, debugger, profiler = _factories(interpreter)
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(
            plugins=RuntimePluginSelection(debugger=True),
        ),
        factories,
    )

    assert isinstance(runtime, RuntimeWithPlugins)
    assert runtime.call(7, (3,)).unwrap() == 10
    assert interpreter.calls == [(7, (3,))]
    assert logger.calls == 0
    assert debugger.calls == 1
    assert profiler.calls == 0


def test_runtime_composer_has_no_jit_execution_factory() -> None:
    assert "jit" not in RuntimeFactories.__dataclass_fields__


def test_runtime_engine_run_services_native_yields() -> None:
    """The runtime services the plugin's idle hook at each native yield boundary."""
    from qa.shared.helpers import wat_to_wasm
    from tier2_runtime.abi import native_abi
    from tier2_runtime.interpreter.interpreter import (
        InterpreterBindings,
        NativeInterpreter,
        NativeModuleExecution,
    )
    from tier2_runtime.runtime.engine import RuntimeEngine as ProductRuntimeEngine
    from tier2_runtime.wasm.module import Module
    from tier2_runtime.wasm.reader import parse_standalone

    class HookProbePlugin:
        def __init__(self) -> None:
            self.hook_calls = 0
            self.module: Module | None = None

        @property
        def native_entry(self) -> native_abi.NativeDispatchEntryPoint:
            return native_abi.RUN_DISPATCH

        def register_module(self, module: Module) -> None:
            self.module = module

        def bind_execution(self, execution: NativeModuleExecution) -> int:
            assert execution.module is self.module
            return 0

        def idle_hook(self, budget: int = 4) -> int:
            assert budget >= 0
            self.hook_calls += 1
            return 0

    module = parse_standalone(
        wat_to_wasm(
            """(module
              (func (export "sum") (param i32) (result i32) (local i32)
                (loop $again
                  local.get 1 local.get 0 i32.add local.set 1
                  local.get 0 i32.const 1 i32.sub local.tee 0 br_if $again)
                local.get 1))"""
        )
    )
    runtime = ProductRuntimeEngine.with_allocator(module.require_allocator())
    runtime.register_module_blocks(module)
    plugin = HookProbePlugin()
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.empty(),
        bump_allocator=runtime.bump_allocator,
        execution_plugin=plugin,
        yield_threshold=1,
    )

    call_state = interpreter.start(module.export_func_index("sum"), [8])
    while not call_state.finished:
        call_state = runtime.run(interpreter, call_state).call_state
    runtime.idle_hook()
    assert call_state.trap is None and call_state.results == [36]
    assert plugin.hook_calls > 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))


@pytest.mark.parametrize("collect_stats", (False, True))
@pytest.mark.parametrize("card_state", (0, 1, 2, 3))
def test_native_execution_plugin_can_be_replaced_without_jit_knowledge(collect_stats, card_state):
    """An opaque execution owner binds once and uses the common runtime boundary."""
    import ctypes

    from qa.shared.helpers import wat_to_wasm
    from qa.shared.runtime_stats import RuntimeStatsEngine as RuntimeEngine
    from tier2_runtime.abi import native_abi
    from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
    from tier2_runtime.wasm.reader import parse_standalone

    class NativeJitTrace(ctypes.Structure):
        _fields_ = (
            ("entry_address", ctypes.c_size_t),
            ("rel", ctypes.c_void_p),
            ("frame_depth", ctypes.c_uint32),
            ("stack_words", ctypes.c_uint32),
            ("body_count", ctypes.c_uint32),
            ("trace_cookie", ctypes.c_size_t),
        )

    JitLookup = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(NativeJitTrace),
    )
    JitObserve = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32)
    JitFinish = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32)
    callbacks = [0, 0, 0]

    def lookup(owner, block_index, pc, trace):
        assert owner == 123
        callbacks[0] += 1
        return 0

    def observe(owner, block_index, pc):
        assert owner == 123
        callbacks[1] += 1
        return 1

    def finish_boundary(owner, status, budget):
        assert owner == 123
        callbacks[2] += 1
        return 1

    lookup_callback = JitLookup(lookup)
    observe_callback = JitObserve(observe)
    finish_callback = JitFinish(finish_boundary)

    class NativeJitProvider(ctypes.Structure):
        _fields_ = (
            ("owner", ctypes.c_size_t),
            ("card_states", ctypes.c_void_p),
            ("card_count", ctypes.c_uint32),
            ("card_shift", ctypes.c_uint32),
            ("lookup", JitLookup),
            ("observe_block", JitObserve),
            ("finish_boundary", JitFinish),
        )

    packed_card_states = card_state | (card_state << 2)
    card_states = ctypes.c_uint8(packed_card_states)
    provider = NativeJitProvider(
        123,
        ctypes.addressof(card_states),
        2,
        31,
        lookup_callback,
        observe_callback,
        finish_callback,
    )
    provider_address = ctypes.addressof(provider)

    class DelegatingPlugin:
        def __init__(self):
            self.bindings = []

        def register_module(self, module):
            self.module = module

        def bind_execution(self, execution):
            assert execution.module is self.module
            self.bindings.append(execution)
            return provider_address

        @property
        def native_entry(self):
            return native_abi.RUN_DISPATCH_JIT

        def idle_hook(self, budget=4):
            return 0

    module = parse_standalone(
        wat_to_wasm("""(module
      (func (export "count") (param i32) (result i32)
        (loop $again
          local.get 0 i32.const 1 i32.sub local.tee 0 br_if $again)
        local.get 0))""")
    )
    plugin = DelegatingPlugin()
    engine = RuntimeEngine(
        bump_allocator=module.allocator,
        jit_runtime=plugin,
        collect_runtime_stats=collect_stats,
    )
    interpreter = NativeInterpreter(
        module,
        InterpreterBindings.empty(),
        bump_allocator=engine.bump_allocator,
        yield_threshold=2,
    )
    for _ in range(2):
        assert engine.call(interpreter, 0, [9]).unwrap() == [0]
    assert len(plugin.bindings) == 1
    assert (callbacks[0] >= 8) == (card_state == 3)
    assert callbacks[1] >= 8
    assert callbacks[2] > 0
    assert engine.stat_jit_invocations == 0
    assert (engine.stat_interp_steps > 0) == collect_stats
    engine.reset_stats()
    assert engine.stat_interp_steps == 0


@pytest.mark.parametrize("capacity", (1, 2, 4))
@pytest.mark.parametrize("overflow", (1, 4, 9))
def test_event_sink_retains_latest_events_in_issue_order(capacity: int, overflow: int) -> None:
    """TEST-OBS-03/05: 最新N件、累積欠落数、拒否後の保持と再充填を直接検査する。"""
    sink = RuntimeEventSink(capacity=capacity)
    runtime_id = 27
    total_dropped = 0
    next_call_id = 0
    for extra in (overflow, 0, overflow + capacity):
        issued = tuple(
            RuntimeEvent(
                kind=RuntimeEventKind.YIELD,
                runtime_id=runtime_id,
                module_id=3,
                function_id=7,
                guest_pc=11,
                tick=call_id + 100,
                call_id=call_id,
                flags=RuntimeEventFlags.TICK_VALID,
            )
            for call_id in range(next_call_id, next_call_id + capacity + extra)
        )
        next_call_id += len(issued)
        for event in issued:
            sink.record(event)
        total_dropped += extra
        required_size = 32 + 32 * capacity
        assert sink.required_size == required_size
        refused = sink.export(RUNTIME_EVENT_ABI_MAJOR, required_size - 1)
        assert refused.status == RuntimeEventExportStatus.BUFFER_TOO_SMALL
        assert refused.required_size == required_size and refused.data == b""
        refused = sink.export(RUNTIME_EVENT_ABI_MAJOR + 1, required_size)
        assert refused.status == RuntimeEventExportStatus.UNSUPPORTED_VERSION
        assert refused.required_size == required_size and refused.data == b""
        exported = sink.export(RUNTIME_EVENT_ABI_MAJOR, required_size)
        assert exported.status == RuntimeEventExportStatus.OK
        batch = RuntimeEventAdapter.decode(exported.data, runtime_id)
        assert tuple(batch.records) == issued[-capacity:]
        assert batch.dropped_count == total_dropped
        assert batch.clock_frequency_hz == 1_000_000_000
        assert batch.clock_domain == 1
        assert sink.required_size == 32
        empty = RuntimeEventAdapter.decode(
            sink.export(RUNTIME_EVENT_ABI_MAJOR, 32).data, runtime_id
        )
        assert tuple(empty.records) == ()
        assert empty.dropped_count == total_dropped

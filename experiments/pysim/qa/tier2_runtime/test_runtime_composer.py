"""RuntimeComposer の無効アスペクト除去とイベント結線を検証する。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from qa.shared.helpers import expect_assertion
from system_containers import StaticVector
from tier2_runtime.observability.events import (
    RUNTIME_EVENT_NO_MODULE,
    RUNTIME_EVENT_NO_PC,
    RuntimeEvent,
    RuntimeEventBatch,
    RuntimeEventFlags,
    RuntimeEventKind,
    RuntimeExecutionError,
)
from tier2_runtime.runtime.composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutionKind,
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
    jit: RuntimeExecutor[int, int],
) -> tuple[RuntimeFactories[int, int], _Factory, _Factory, _Factory]:
    logger = _Factory()
    debugger = _Factory()
    profiler = _Factory()
    factories = RuntimeFactories(
        lambda: interpreter,
        lambda: jit,
        logger.create,
        debugger.create,
        profiler.create,
    )
    return factories, logger, debugger, profiler


@pytest.mark.parametrize("execution", tuple(RuntimeExecutionKind), ids=("interpreter", "jit"))
def test_disabled_plugins_are_not_constructed_or_retained(execution: RuntimeExecutionKind) -> None:
    interpreter = _Executor()
    jit = _Executor()
    factories, logger, debugger, profiler = _factories(interpreter, jit)

    class ClockProbe:
        calls = 0

        def read(self) -> int:
            self.calls += 1
            return self.calls

    clock = ClockProbe()

    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(execution=execution),
        factories,
        tick_clock=clock.read,
    )

    assert isinstance(runtime, RuntimeWithoutPlugins)
    assert not hasattr(runtime, "observers")
    assert runtime.call(3, (4,)).unwrap() == 7
    assert interpreter.calls == (
        [(3, (4,))] if execution == RuntimeExecutionKind.INTERPRETER else []
    )
    assert jit.calls == ([(3, (4,))] if execution == RuntimeExecutionKind.JIT else [])
    assert logger.calls == 0
    assert debugger.calls == 0
    assert profiler.calls == 0
    assert clock.calls == 0


@pytest.mark.parametrize("execution", tuple(RuntimeExecutionKind), ids=("interpreter", "jit"))
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
    execution: RuntimeExecutionKind, selection: RuntimePluginSelection
) -> None:
    interpreter = _Executor()
    jit = _Executor()
    factories, logger, debugger, profiler = _factories(interpreter, jit)
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(
            execution=execution,
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
    assert interpreter.calls == (
        expected_calls if execution == RuntimeExecutionKind.INTERPRETER else []
    )
    assert jit.calls == (expected_calls if execution == RuntimeExecutionKind.JIT else [])
    assert logger.calls == int(selection.logger)
    assert debugger.calls == 0
    assert profiler.calls == int(selection.profiler)
    kinds = (
        (
            RuntimeEventKind.FUNCTION_ENTER,
            RuntimeEventKind.JIT_ENTER,
            RuntimeEventKind.JIT_EXIT,
            RuntimeEventKind.FUNCTION_EXIT,
        )
        if execution == RuntimeExecutionKind.JIT
        else (RuntimeEventKind.FUNCTION_ENTER, RuntimeEventKind.FUNCTION_EXIT)
    )
    flags = RuntimeEventFlags.TICK_VALID | (
        RuntimeEventFlags.JIT
        if execution == RuntimeExecutionKind.JIT
        else RuntimeEventFlags.INTERPRETER
    )
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


@pytest.mark.parametrize("execution", tuple(RuntimeExecutionKind), ids=("interpreter", "jit"))
@pytest.mark.parametrize(
    "error",
    (RuntimeExecutionError.GUEST_TRAP, RuntimeExecutionError.HOST_FAILURE),
    ids=("guest-trap", "host-failure"),
)
def test_execution_failure_preserves_result_and_reports_matching_events(
    execution: RuntimeExecutionKind, error: RuntimeExecutionError
) -> None:
    executor = _ErrorExecutor(error)
    factories, logger, _, _ = _factories(executor, executor)
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(execution=execution, plugins=RuntimePluginSelection(logger=True)),
        factories,
        runtime_id=9,
        tick_clock=lambda: 10,
    )

    result = runtime.call(4, (17,))

    assert not result.is_ok and result.error == error
    assert executor.calls == [(4, (17,))]
    kinds = (RuntimeEventKind.FUNCTION_ENTER,)
    if execution == RuntimeExecutionKind.JIT:
        kinds += (RuntimeEventKind.JIT_ENTER, RuntimeEventKind.JIT_EXIT)
    if error == RuntimeExecutionError.GUEST_TRAP:
        kinds += (RuntimeEventKind.TRAP,)
    kinds += (RuntimeEventKind.FUNCTION_EXIT,)
    flags = RuntimeEventFlags.TICK_VALID | (
        RuntimeEventFlags.JIT
        if execution == RuntimeExecutionKind.JIT
        else RuntimeEventFlags.INTERPRETER
    )
    trap_flags = RuntimeEventFlags.TICK_VALID | RuntimeEventFlags.TRAP
    if execution == RuntimeExecutionKind.JIT:
        trap_flags |= RuntimeEventFlags.JIT
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
    jit = _Executor()
    factories, logger, debugger, profiler = _factories(interpreter, jit)
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(
            execution=RuntimeExecutionKind.INTERPRETER,
            plugins=RuntimePluginSelection(debugger=True),
        ),
        factories,
    )

    assert isinstance(runtime, RuntimeWithPlugins)
    assert runtime.call(7, (3,)).unwrap() == 10
    assert interpreter.calls == [(7, (3,))]
    assert jit.calls == []
    assert logger.calls == 0
    assert debugger.calls == 1
    assert profiler.calls == 0


def test_debugger_cannot_be_composed_with_jit() -> None:
    interpreter = _Executor()
    jit = _Executor()
    factories, _, _, _ = _factories(interpreter, jit)

    with expect_assertion("debugger-enabled runtime must use interpreter-only execution"):
        RuntimeComposer.compose(
            RuntimeCompositionConfig(
                execution=RuntimeExecutionKind.JIT,
                plugins=RuntimePluginSelection(debugger=True),
            ),
            factories,
        )
    assert interpreter.calls == []
    assert jit.calls == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

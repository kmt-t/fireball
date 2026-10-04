"""RuntimeComposer の無効アスペクト除去とイベント結線を検証する。"""

from __future__ import annotations

from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]
_PYSIM_DIR = Path(__file__).resolve().parents[2]

from qa.shared.helpers import expect_assertion
from tier2_runtime.runtime.recovery import Result
from tier2_runtime.runtime.composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutionKind,
    RuntimeFactories,
    RuntimePluginSelection,
    RuntimeWithoutPlugins,
    RuntimeWithPlugins,
)
from tier2_runtime.observability.events import (
    RUNTIME_EVENT_NO_MODULE,
    RUNTIME_EVENT_NO_PC,
    RuntimeEvent,
    RuntimeEventBatch,
    RuntimeEventFlags,
    RuntimeEventKind,
    RuntimeExecutionError,
)
from system_containers import StaticVector


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, func_index: int, args: tuple[int, ...]) -> Result[int, RuntimeExecutionError]:
        self.calls += 1
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
    interpreter: _Executor,
    jit: _Executor,
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


def test_disabled_plugins_are_not_constructed_or_retained() -> None:
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
        RuntimeCompositionConfig(),
        factories,
        tick_clock=clock.read,
    )

    assert isinstance(runtime, RuntimeWithoutPlugins)
    assert not hasattr(runtime, "observers")
    assert runtime.call(3, (4,)).unwrap() == 7
    assert interpreter.calls == 1
    assert jit.calls == 0
    assert logger.calls == 0
    assert debugger.calls == 0
    assert profiler.calls == 0
    assert clock.calls == 0


def test_selected_plugins_receive_one_shared_event_stream() -> None:
    interpreter = _Executor()
    jit = _Executor()
    factories, logger, debugger, profiler = _factories(interpreter, jit)
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(
            execution=RuntimeExecutionKind.JIT,
            plugins=RuntimePluginSelection(logger=True, debugger=False, profiler=True),
        ),
        factories,
        runtime_id=9,
        tick_clock=lambda: 10,
    )

    assert isinstance(runtime, RuntimeWithPlugins)
    assert runtime.call(5, (2,)).unwrap() == 7
    assert interpreter.calls == 0
    assert jit.calls == 1
    assert logger.calls == 1
    assert debugger.calls == 0
    assert profiler.calls == 1
    expected = tuple(
        RuntimeEvent(
            kind,
            9,
            RUNTIME_EVENT_NO_MODULE,
            5,
            RUNTIME_EVENT_NO_PC,
            10 + offset,
            1,
            RuntimeEventFlags.TICK_VALID | RuntimeEventFlags.JIT,
        )
        for offset, kind in enumerate(
            (
                RuntimeEventKind.FUNCTION_ENTER,
                RuntimeEventKind.JIT_ENTER,
                RuntimeEventKind.JIT_EXIT,
                RuntimeEventKind.FUNCTION_EXIT,
            )
        )
    )
    assert tuple(logger.instance.events) == expected
    assert tuple(profiler.instance.events) == expected


class _TrappingExecutor:
    def call(self, _func_index: int, _args: tuple[int, ...]) -> Result[int, RuntimeExecutionError]:
        return Result.err(RuntimeExecutionError.GUEST_TRAP)


class _FailingExecutor:
    def call(self, _func_index: int, _args: tuple[int, ...]) -> Result[int, RuntimeExecutionError]:
        return Result.err(RuntimeExecutionError.HOST_FAILURE)


def test_guest_trap_is_distinguished_from_host_failure() -> None:
    interpreter = _TrappingExecutor()
    jit = _Executor()
    factories, logger, _, _ = _factories(jit, jit)
    factories = RuntimeFactories(
        interpreter=lambda: interpreter,
        jit=lambda: jit,
        logger=factories.logger,
        debugger=factories.debugger,
        profiler=factories.profiler,
    )
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(plugins=RuntimePluginSelection(logger=True)),
        factories,
    )
    assert isinstance(runtime, RuntimeWithPlugins)
    trap_result = runtime.call(4, ())
    assert not trap_result.is_ok and trap_result.error == RuntimeExecutionError.GUEST_TRAP
    assert [event.kind for event in logger.instance.events] == [
        RuntimeEventKind.FUNCTION_ENTER,
        RuntimeEventKind.TRAP,
        RuntimeEventKind.FUNCTION_EXIT,
    ]
    guest_exit = logger.instance.events[2]
    assert guest_exit.flags & RuntimeEventFlags.ABORTED
    assert guest_exit.flags & RuntimeEventFlags.ESTIMATED

    factories = RuntimeFactories(
        interpreter=_FailingExecutor,
        jit=lambda: jit,
        logger=factories.logger,
        debugger=factories.debugger,
        profiler=factories.profiler,
    )
    failing_runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(plugins=RuntimePluginSelection(logger=True)),
        factories,
    )
    assert isinstance(failing_runtime, RuntimeWithPlugins)
    failure_result = failing_runtime.call(4, ())
    assert not failure_result.is_ok and failure_result.error == RuntimeExecutionError.HOST_FAILURE
    assert [event.kind for event in logger.instance.events] == [
        RuntimeEventKind.FUNCTION_ENTER,
        RuntimeEventKind.FUNCTION_EXIT,
    ]
    aborted = logger.instance.events[1]
    assert aborted.kind == RuntimeEventKind.FUNCTION_EXIT
    assert aborted.flags & RuntimeEventFlags.ABORTED
    assert aborted.flags & RuntimeEventFlags.ESTIMATED


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
    assert interpreter.calls == 1
    assert jit.calls == 0
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
    assert interpreter.calls == 0
    assert jit.calls == 0


ALL_TESTS = tuple(
    value for name, value in globals().items() if name.startswith("test_") and callable(value)
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

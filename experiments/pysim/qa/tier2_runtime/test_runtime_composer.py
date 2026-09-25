"""RuntimeComposer の無効アスペクト除去とイベント結線を検証する。"""

from __future__ import annotations

from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]
_PYSIM_DIR = Path(__file__).resolve().parents[2]

from helpers import expect_assertion
from runtime_composer import (
    RuntimeComposer,
    RuntimeCompositionConfig,
    RuntimeExecutionKind,
    RuntimeFactories,
    RuntimePluginSelection,
    RuntimeWithoutPlugins,
    RuntimeWithPlugins,
)
from runtime_events import RuntimeEvent, RuntimeEventKind
from system_containers import StaticVector


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, func_index: int, args: tuple[int, ...]) -> int:
        self.calls += 1
        return func_index + sum(args)


class _Observer:
    def __init__(self) -> None:
        self.events: StaticVector[RuntimeEvent] = StaticVector(capacity=4)

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        self.events.append(event)


class _Factory:
    def __init__(self) -> None:
        self.calls = 0
        self.instance = _Observer()

    def create(self) -> _Observer:
        self.calls += 1
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
    runtime = RuntimeComposer.compose(
        RuntimeCompositionConfig(),
        factories,
    )

    assert isinstance(runtime, RuntimeWithoutPlugins)
    assert not hasattr(runtime, "observers")
    assert runtime.call(3, (4,)) == 7
    assert interpreter.calls == 1
    assert jit.calls == 0
    assert logger.calls == 0
    assert debugger.calls == 0
    assert profiler.calls == 0


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
    )

    assert isinstance(runtime, RuntimeWithPlugins)
    assert runtime.call(5, (2,)) == 7
    assert interpreter.calls == 0
    assert jit.calls == 1
    assert logger.calls == 1
    assert debugger.calls == 0
    assert profiler.calls == 1
    assert len(logger.instance.events) == 2
    assert len(profiler.instance.events) == 2
    assert logger.instance.events[0] == profiler.instance.events[0]
    assert logger.instance.events[0].kind == RuntimeEventKind.FUNCTION_ENTER
    assert logger.instance.events[1].kind == RuntimeEventKind.FUNCTION_EXIT
    assert logger.instance.events[0].runtime_id == 9


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
    assert runtime.call(7, (3,)) == 10
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
    value
    for name, value in globals().items()
    if name.startswith("test_") and callable(value)
)


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")

"""Runtime の静的プラグイン合成を表す pysim 実装。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Generic, Protocol, TypeVar

from runtime_events import RuntimeEvent, RuntimeEventFlags, RuntimeEventKind, RuntimeObserver
from system_containers import StaticVector

ResultT = TypeVar("ResultT")
ArgumentT = TypeVar("ArgumentT")


class RuntimeExecutor(Protocol, Generic[ResultT, ArgumentT]):
    """Interpreter または JIT に共通する最小呼出契約。"""

    def call(self, func_index: int, args: Sequence[ArgumentT]) -> ResultT: ...


class ComposedRuntime(Protocol, Generic[ResultT, ArgumentT]):
    """RuntimeComposer の生成結果が公開する呼出境界。"""

    def call(self, func_index: int, args: Sequence[ArgumentT]) -> ResultT: ...


@dataclass(frozen=True, slots=True)
class RuntimePluginSelection:
    """起動前に固定するプラグイン構成。"""

    logger: bool = False
    debugger: bool = False
    profiler: bool = False


class RuntimeExecutionKind(IntEnum):
    """構成時に一度だけ選択する実行方式。"""

    INTERPRETER = 1
    JIT = 2


@dataclass(frozen=True, slots=True)
class RuntimeCompositionConfig:
    """実行方式とプラグイン有効性を起動前に固定する構成。"""

    execution: RuntimeExecutionKind = RuntimeExecutionKind.INTERPRETER
    plugins: RuntimePluginSelection = RuntimePluginSelection()


@dataclass(frozen=True, slots=True)
class RuntimeFactories(Generic[ResultT, ArgumentT]):
    """合成時にだけ参照する実行器・プラグイン生成器群。"""

    interpreter: Callable[[], RuntimeExecutor[ResultT, ArgumentT]]
    jit: Callable[[], RuntimeExecutor[ResultT, ArgumentT]]
    logger: Callable[[], RuntimeObserver]
    debugger: Callable[[], RuntimeObserver]
    profiler: Callable[[], RuntimeObserver]


class RuntimeWithoutPlugins(Generic[ResultT, ArgumentT]):
    """観測・補助プラグインを一切保持しない合成結果。"""

    __slots__ = ("executor",)

    def __init__(self, executor: RuntimeExecutor[ResultT, ArgumentT]):
        self.executor = executor

    def call(self, func_index: int, args: Sequence[ArgumentT]) -> ResultT:
        return self.executor.call(func_index, args)


class RuntimeWithPlugins(Generic[ResultT, ArgumentT]):
    """有効なプラグインだけを固定ベクタへ結線した合成結果。"""

    __slots__ = ("executor", "observers", "runtime_id", "tick", "call_id")

    def __init__(
        self,
        executor: RuntimeExecutor[ResultT, ArgumentT],
        observers: StaticVector[RuntimeObserver],
        runtime_id: int,
    ):
        assert len(observers) > 0
        self.executor = executor
        self.observers = observers
        self.runtime_id = runtime_id
        self.tick = 0
        self.call_id = 0

    def _emit(
        self,
        kind: RuntimeEventKind,
        func_index: int,
        guest_pc: int,
        flags: RuntimeEventFlags,
    ) -> None:
        event = RuntimeEvent(
            kind=kind,
            runtime_id=self.runtime_id,
            module_id=0,
            function_id=func_index,
            guest_pc=guest_pc,
            tick=self.tick,
            call_id=self.call_id,
            flags=flags,
        )
        for index in range(len(self.observers)):
            self.observers[index].on_runtime_event(event)

    def call(self, func_index: int, args: Sequence[ArgumentT]) -> ResultT:
        """共通呼出境界からイベントを発行して、選択済み実行器を呼び出す。"""

        self.call_id += 1
        self.tick += 1
        self._emit(RuntimeEventKind.FUNCTION_ENTER, func_index, 0, RuntimeEventFlags.NONE)
        result = self.executor.call(func_index, args)
        self.tick += 1
        self._emit(RuntimeEventKind.FUNCTION_EXIT, func_index, 0, RuntimeEventFlags.NONE)
        return result


class RuntimeComposer:
    """不変構成から Runtime の具象形を起動時に一度だけ合成する。"""

    __slots__ = ()

    @staticmethod
    def compose(
        config: RuntimeCompositionConfig,
        factories: RuntimeFactories[ResultT, ArgumentT],
        runtime_id: int = 1,
    ) -> ComposedRuntime[ResultT, ArgumentT]:
        """無効プラグインを生成せず、選択済みの具象 Runtime だけを返す。"""

        selection = config.plugins
        if selection.debugger:
            assert config.execution == RuntimeExecutionKind.INTERPRETER, (
                "debugger-enabled runtime must use interpreter-only execution"
            )
        if config.execution == RuntimeExecutionKind.INTERPRETER:
            executor = factories.interpreter()
        else:
            assert config.execution == RuntimeExecutionKind.JIT
            executor = factories.jit()
        if not selection.logger and not selection.debugger and not selection.profiler:
            return RuntimeWithoutPlugins(executor)

        observers: StaticVector[RuntimeObserver] = StaticVector(capacity=3)
        if selection.logger:
            observers.append(factories.logger())
        if selection.debugger:
            observers.append(factories.debugger())
        if selection.profiler:
            observers.append(factories.profiler())
        return RuntimeWithPlugins(executor, observers, runtime_id)

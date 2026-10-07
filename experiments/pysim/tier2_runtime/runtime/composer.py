"""Runtime の静的プラグイン合成を表す pysim 実装。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from system_containers import SequenceView, StaticVector
from tier2_runtime.observability.events import (
    RUNTIME_EVENT_ABI_MAJOR,
    RUNTIME_EVENT_NO_MODULE,
    RUNTIME_EVENT_NO_PC,
    RuntimeEvent,
    RuntimeEventAdapter,
    RuntimeEventExportStatus,
    RuntimeEventFlags,
    RuntimeEventKind,
    RuntimeEventSink,
    RuntimeExecutionError,
    RuntimeObserver,
    dispatch_runtime_event_batch,
)
from tier2_runtime.runtime.recovery import Result

ResultT = TypeVar("ResultT", covariant=True)
ArgumentT = TypeVar("ArgumentT", contravariant=True)


class RuntimeExecutor(Protocol, Generic[ResultT, ArgumentT]):
    """Runtimeが呼び出すInterpreter契約。実行プラグインはInterpreter内部に属する。"""

    def call(
        self, func_index: int, args: SequenceView[ArgumentT]
    ) -> Result[ResultT, RuntimeExecutionError]: ...


class ComposedRuntime(Protocol, Generic[ResultT, ArgumentT]):
    """RuntimeComposer の生成結果が公開する呼出境界。"""

    def call(
        self, func_index: int, args: SequenceView[ArgumentT]
    ) -> Result[ResultT, RuntimeExecutionError]: ...


@dataclass(frozen=True, slots=True)
class RuntimePluginSelection:
    """起動前に固定するプラグイン構成。"""

    logger: bool = False
    debugger: bool = False
    profiler: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeCompositionConfig:
    """Interpreter周辺のRuntimeプラグイン有効性を起動前に固定する構成。"""

    plugins: RuntimePluginSelection = RuntimePluginSelection()


@dataclass(frozen=True, slots=True)
class RuntimeFactories(Generic[ResultT, ArgumentT]):
    """合成時にだけ参照するInterpreter・Runtimeプラグイン生成器群。"""

    interpreter: Callable[[], RuntimeExecutor[ResultT, ArgumentT]]
    logger: Callable[[], RuntimeObserver]
    debugger: Callable[[], RuntimeObserver]
    profiler: Callable[[], RuntimeObserver]


class RuntimeWithoutPlugins(Generic[ResultT, ArgumentT]):
    """観測・補助プラグインを一切保持しない合成結果。"""

    __slots__ = ("executor",)

    def __init__(self, executor: RuntimeExecutor[ResultT, ArgumentT]):
        self.executor = executor

    def call(
        self, func_index: int, args: SequenceView[ArgumentT]
    ) -> Result[ResultT, RuntimeExecutionError]:
        return self.executor.call(func_index, args)


class RuntimeWithPlugins(Generic[ResultT, ArgumentT]):
    """有効なプラグインだけを固定ベクタへ結線した合成結果。"""

    __slots__ = (
        "call_id",
        "event_sink",
        "executor",
        "observers",
        "runtime_id",
        "tick",
        "tick_clock",
    )

    def __init__(
        self,
        executor: RuntimeExecutor[ResultT, ArgumentT],
        observers: StaticVector[RuntimeObserver],
        runtime_id: int,
        tick_clock: Callable[[], int] = time.monotonic_ns,
        event_capacity: int = 64,
    ):
        assert len(observers) > 0
        self.executor = executor
        self.observers = observers
        self.runtime_id = runtime_id
        self.tick = -1
        self.call_id = 0
        self.tick_clock = tick_clock
        self.event_sink = RuntimeEventSink(capacity=event_capacity)

    def _next_tick(self) -> int:
        observed_tick = self.tick_clock()
        self.tick = max(observed_tick, self.tick + 1)
        return self.tick

    def _emit(
        self,
        kind: RuntimeEventKind,
        func_index: int,
        guest_pc: int,
        flags: RuntimeEventFlags,
        value: int = 0,
    ) -> None:
        event_flags = flags | RuntimeEventFlags.TICK_VALID
        event = RuntimeEvent(
            kind=kind,
            runtime_id=self.runtime_id,
            module_id=RUNTIME_EVENT_NO_MODULE,
            function_id=func_index,
            guest_pc=guest_pc if guest_pc >= 0 else RUNTIME_EVENT_NO_PC,
            tick=self._next_tick(),
            call_id=self.call_id,
            flags=event_flags,
            auxiliary=value,
        )
        self.event_sink.record(event)

    def _deliver_events_at_safe_point(self) -> None:
        """Export a versioned batch and notify Python plugins after execution."""

        exported = self.event_sink.export(
            abi_major=RUNTIME_EVENT_ABI_MAJOR,
            capacity=self.event_sink.required_size,
        )
        assert exported.status == RuntimeEventExportStatus.OK
        batch = RuntimeEventAdapter.decode(exported.data, self.runtime_id)
        if batch.records or batch.dropped_count:
            dispatch_runtime_event_batch(self.observers, batch)

    def call(
        self, func_index: int, args: SequenceView[ArgumentT]
    ) -> Result[ResultT, RuntimeExecutionError]:
        """Runtimeイベントを記録し、構成済みInterpreterを呼び出す。"""

        self.call_id += 1
        self._emit(RuntimeEventKind.FUNCTION_ENTER, func_index, -1, RuntimeEventFlags.NONE)
        result = self.executor.call(func_index, args)
        if result.is_ok:
            self._emit(
                RuntimeEventKind.FUNCTION_EXIT,
                func_index,
                -1,
                RuntimeEventFlags.NONE,
                value=0,
            )
            self._deliver_events_at_safe_point()
            return result
        if result.error == RuntimeExecutionError.GUEST_TRAP:
            self._emit(
                RuntimeEventKind.TRAP,
                func_index,
                -1,
                RuntimeEventFlags.NONE,
            )
        assert result.error is not None
        self._emit(
            RuntimeEventKind.FUNCTION_EXIT,
            func_index,
            -1,
            RuntimeEventFlags.ESTIMATED | RuntimeEventFlags.ABORTED,
            value=int(result.error),
        )
        self._deliver_events_at_safe_point()
        return result


class RuntimeComposer:
    """不変構成から Runtime の具象形を起動時に一度だけ選ぶ参照モデル。

    Python版は構成合成の振る舞いを確認する参照モデルである。未選択コードの
    バイナリ除去はC++本実装で検証する設計契約であり、このモデルはその証拠ではない。
    """

    __slots__ = ()

    @staticmethod
    def compose(
        config: RuntimeCompositionConfig,
        factories: RuntimeFactories[ResultT, ArgumentT],
        runtime_id: int = 1,
        tick_clock: Callable[[], int] = time.monotonic_ns,
        event_capacity: int = 64,
    ) -> ComposedRuntime[ResultT, ArgumentT]:
        """無効プラグインを生成せず、選択済みの具象 Runtime だけを返す。"""

        selection = config.plugins
        executor = factories.interpreter()
        if not selection.logger and not selection.debugger and not selection.profiler:
            return RuntimeWithoutPlugins(executor)

        observers: StaticVector[RuntimeObserver] = StaticVector(capacity=3)
        if selection.logger:
            observers.append(factories.logger())
        if selection.debugger:
            observers.append(factories.debugger())
        if selection.profiler:
            observers.append(factories.profiler())
        return RuntimeWithPlugins(
            executor,
            observers,
            runtime_id,
            tick_clock,
            event_capacity=event_capacity,
        )

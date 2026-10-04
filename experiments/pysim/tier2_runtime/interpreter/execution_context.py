"""experiments/pysim/tier2_runtime/interpreter/execution_context.py
Non-owning execution and debugger contracts used across runtime components.
"""

from __future__ import annotations

from typing import Protocol

from tier2_runtime.abi.interpreter_abi import NativeValueStack


class DebugLocalsView(Protocol):
    """停止した実行状態の非所有ローカルビュー。"""

    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> int: ...
    def __setitem__(self, index: int, value: int) -> None: ...


class DebugExecutionView(Protocol):
    """ExecutionControlが公開する実行状態の非所有ビュー。"""

    @property
    def stack(self) -> NativeValueStack: ...
    @property
    def stack_capacity(self) -> int: ...
    @property
    def locals(self) -> DebugLocalsView: ...
    @property
    def memory(self) -> bytearray | None: ...


class ExecutionControl(Protocol):
    """構成済みデバッグフックによる停止・再開の契約。"""

    def resume(self, pc: int, ctx: DebugExecutionView, single_step: bool) -> int | None: ...

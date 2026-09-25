"""固定幅 Runtime 観測イベントと観測シンクの Tier 2 契約。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Protocol


class RuntimeEventKind(IntEnum):
    """Runtime が発行するイベント種別。"""

    MODULE_LOAD = 1
    FUNCTION_ENTER = 2
    FUNCTION_EXIT = 3
    INTERPRETER_BOUNDARY = 4
    JIT_ENTER = 5
    JIT_EXIT = 6
    HOST_CALL_ENTER = 7
    HOST_CALL_EXIT = 8
    COOS_BOUNDARY = 9
    TRAP = 10
    DEBUG_STOP = 11


class RuntimeEventFlags(IntFlag):
    """イベントの実行方式と計測精度を表す固定ビット。"""

    NONE = 0
    INTERPRETER = 1 << 0
    JIT = 1 << 1
    TRAP = 1 << 2
    DEBUG_STOP = 1 << 3
    DROPPED = 1 << 4
    ESTIMATED = 1 << 5


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Runtime の実行境界で生成する固定幅相当のイベント。"""

    kind: RuntimeEventKind
    runtime_id: int
    module_id: int
    function_id: int
    guest_pc: int
    tick: int
    call_id: int
    flags: RuntimeEventFlags = RuntimeEventFlags.NONE
    auxiliary: int = 0


class RuntimeObserver(Protocol):
    """Runtime イベントを読み取る Tier 3 観測プラグイン契約。"""

    def on_runtime_event(self, event: RuntimeEvent) -> None: ...

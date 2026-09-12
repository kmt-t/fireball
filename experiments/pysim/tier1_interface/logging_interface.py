"""Independent logging port used by Tier 1 components."""

from __future__ import annotations

from enum import IntEnum
from typing import Protocol


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    FATAL = 4


class Logger(Protocol):
    """Minimal logging port; the concrete sink is owned by the runtime."""

    def log_event(
        self,
        level: IntEnum,
        dict_offset: int,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> str: ...


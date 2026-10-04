"""Tier 2 logging contract for Tier 2 and Tier 3 consumers."""

from __future__ import annotations

from enum import IntEnum
from typing import Protocol


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    FATAL = 4


class LogResult(IntEnum):
    SUCCESS = 0
    FILTERED = 1
    OVERWRITTEN = 2


class LoggerPort(Protocol):
    """Buffered event API provided by the Tier 2 runtime logger."""

    def log_event(
        self,
        level: IntEnum,
        dict_offset: int,
        arg0: int = 0,
        arg1: int = 0,
        arg2: int = 0,
        arg3: int = 0,
    ) -> LogResult: ...

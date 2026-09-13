"""WASIからHALへ渡すエンドポイント結線の契約型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WasiHalBindings:
    """WASIアダプタが利用するHALエンドポイントURIの注入値。"""

    stdout_uri: str
    timer_uri: str
    uart_uri: str
    logger_uri: str

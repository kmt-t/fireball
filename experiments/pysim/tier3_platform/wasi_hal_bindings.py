"""プラットフォームが選択するWASI-HALエンドポイント結線。"""

from __future__ import annotations

from ipc_router import (
    FB_URI_HAL_LOGGER,
    FB_URI_HAL_STDOUT,
    FB_URI_HAL_TIMER,
    FB_URI_HAL_UART,
)
from wasi_bindings import WasiHalBindings


DEFAULT_WASI_HAL_BINDINGS = WasiHalBindings(
    stdout_uri=FB_URI_HAL_STDOUT,
    timer_uri=FB_URI_HAL_TIMER,
    uart_uri=FB_URI_HAL_UART,
    logger_uri=FB_URI_HAL_LOGGER,
)

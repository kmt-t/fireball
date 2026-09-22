"""プラットフォームが選択するWASI-HALエンドポイント結線。"""

from __future__ import annotations

from ipc_router import (
    FB_URI_HAL_STDOUT,
)
from wasi_bindings import WasiHalBindings

DEFAULT_WASI_HAL_BINDINGS = WasiHalBindings(
    stdout_uri=FB_URI_HAL_STDOUT,
)

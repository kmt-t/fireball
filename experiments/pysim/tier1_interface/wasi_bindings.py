"""WASIからHALへ渡すエンドポイント結線の契約型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class WasiHalBindings:
    """WASIアダプタが利用する標準出力エンドポイントの注入値。"""

    stdout_uri: Final[str]

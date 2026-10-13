"""Guest binary validation failures, distinct from host contract assertions."""

from __future__ import annotations

from typing import NoReturn

from tier2_runtime.interpreter.errors import Trap, TrapCode


class InvalidWasm(Trap):
    """A malformed or unsupported guest binary rejected by the loader."""

    __slots__ = ()

    def __init__(self, reason: str) -> None:
        # BaseException keeps the constructor's reason in args.
        super().__init__(TrapCode.INVALID_WASM_BINARY)


def reject_wasm(reason: str) -> NoReturn:
    """Stop guest binary validation without catching internal assertions."""
    raise InvalidWasm(reason)


def require_wasm(condition: bool, reason: str) -> None:
    """Validate a guest constraint even when Python assertions are disabled."""
    if not condition:
        reject_wasm(reason)

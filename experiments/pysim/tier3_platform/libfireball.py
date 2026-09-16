"""WASM guest-side libfireball host-call ABI adapter.

The adapter models the statically linked guest library described by
``docs/components/tier3_platform/libfireball.md``.  It carries no vMMIO
register state: every operation is a synchronous import call to the supplied
fixed-arity host function.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

U32_MAX: Final[int] = 0xFFFF_FFFF
FireballHostCall = Callable[[int, int, int, int, int, int, int], int]


class Libfireball:
    """Fixed-arity ``fireball_call0`` through ``fireball_call6`` bindings."""

    __slots__ = ("_host_call",)

    def __init__(self, host_call: FireballHostCall):
        self._host_call = host_call

    def _call(
        self,
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        for value in (syscall_id, arg0, arg1, arg2, arg3, arg4, arg5):
            assert 0 <= value <= U32_MAX, "fireball host-call values must be u32"
        result = self._host_call(syscall_id, arg0, arg1, arg2, arg3, arg4, arg5)
        assert 0 <= result <= U32_MAX, "fireball host-call result must be u32"
        return result

    def fireball_call0(self, syscall_id: int) -> int:
        """Issues a host call with an ID and no operation arguments."""
        return self._call(syscall_id, 0, 0, 0, 0, 0, 0)

    def fireball_call1(self, syscall_id: int, arg0: int) -> int:
        """Issues a host call with one operation argument."""
        return self._call(syscall_id, arg0, 0, 0, 0, 0, 0)

    def fireball_call2(self, syscall_id: int, arg0: int, arg1: int) -> int:
        """Issues a host call with two operation arguments."""
        return self._call(syscall_id, arg0, arg1, 0, 0, 0, 0)

    def fireball_call3(self, syscall_id: int, arg0: int, arg1: int, arg2: int) -> int:
        """Issues a host call with three operation arguments."""
        return self._call(syscall_id, arg0, arg1, arg2, 0, 0, 0)

    def fireball_call4(
        self, syscall_id: int, arg0: int, arg1: int, arg2: int, arg3: int
    ) -> int:
        """Issues a host call with four operation arguments."""
        return self._call(syscall_id, arg0, arg1, arg2, arg3, 0, 0)

    def fireball_call5(
        self, syscall_id: int, arg0: int, arg1: int, arg2: int, arg3: int, arg4: int
    ) -> int:
        """Issues a host call with five operation arguments."""
        return self._call(syscall_id, arg0, arg1, arg2, arg3, arg4, 0)

    def fireball_call6(
        self,
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        """Issues the complete seven-field host-call ABI."""
        return self._call(syscall_id, arg0, arg1, arg2, arg3, arg4, arg5)

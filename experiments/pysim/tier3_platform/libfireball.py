"""WASM guest-side libfireball host-call ABI adapter.

The adapter models the statically linked guest library described by
``docs/components/tier3_platform/libfireball.md``.  It carries no vMMIO
register state: generic syscalls use the supplied fixed-arity host function,
while vIRQ and vDMA use their dedicated typed host-call functions.
"""

from __future__ import annotations

from typing import Final

from hostcall import FireballHostCallPort

U32_MAX: Final[int] = 0xFFFF_FFFF


class Libfireball:
    """Generic syscall and dedicated vIRQ/vDMA host-call bindings."""

    __slots__ = ("_host_calls",)

    def __init__(
        self,
        host_calls: FireballHostCallPort,
    ):
        self._host_calls = host_calls

    @staticmethod
    def _validate_u32(value: int) -> None:
        assert 0 <= value <= U32_MAX, "fireball host-call values must be u32"

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
        self._validate_u32(syscall_id)
        self._validate_u32(arg0)
        self._validate_u32(arg1)
        self._validate_u32(arg2)
        self._validate_u32(arg3)
        self._validate_u32(arg4)
        self._validate_u32(arg5)
        result = self._host_calls.fireball_call(syscall_id, arg0, arg1, arg2, arg3, arg4, arg5)
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

    def fireball_call4(self, syscall_id: int, arg0: int, arg1: int, arg2: int, arg3: int) -> int:
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

    def fireball_virq_register(self, node_id: int, function_index: int) -> int:
        """Stages a vIRQ guest handler through the dedicated host call."""
        self._validate_u32(node_id)
        self._validate_u32(function_index)
        result = self._host_calls.virq_register(node_id, function_index)
        assert 0 <= result <= U32_MAX, "vIRQ host-call result must be u32"
        return result

    def fireball_virq_unregister(self, node_id: int) -> int:
        """Stages removal of a vIRQ guest handler through the dedicated host call."""
        self._validate_u32(node_id)
        result = self._host_calls.virq_unregister(node_id)
        assert 0 <= result <= U32_MAX, "vIRQ host-call result must be u32"
        return result

    def fireball_vdma_start(self, source: int, destination: int, byte_count: int) -> int:
        """Starts a virtual DMA transfer through the dedicated host call."""
        self._validate_u32(source)
        self._validate_u32(destination)
        self._validate_u32(byte_count)
        result = self._host_calls.vdma_start(source, destination, byte_count)
        assert 0 <= result <= U32_MAX, "vDMA host-call result must be u32"
        return result

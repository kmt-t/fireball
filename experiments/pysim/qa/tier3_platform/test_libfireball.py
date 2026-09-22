from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[2]

from libfireball import Libfireball


class RecordingHostCalls:
    __slots__ = (
        "calls",
        "virq_register_calls",
        "virq_unregister_calls",
        "vdma_calls",
    )

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int, int, int, int, int]] = []
        self.virq_register_calls: list[tuple[int, int]] = []
        self.virq_unregister_calls: list[int] = []
        self.vdma_calls: list[tuple[int, int, int]] = []

    def fireball_call(
        self,
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        self.calls.append((syscall_id, arg0, arg1, arg2, arg3, arg4, arg5))
        return 0

    def virq_register(self, node_id: int, function_index: int) -> int:
        self.virq_register_calls.append((node_id, function_index))
        return 0

    def virq_unregister(self, node_id: int) -> int:
        self.virq_unregister_calls.append(node_id)
        return 0

    def vdma_start(self, source: int, destination: int, byte_count: int) -> int:
        self.vdma_calls.append((source, destination, byte_count))
        return 0


def test_libfireball_host_call_argument_packing() -> None:
    host_calls = RecordingHostCalls()
    lib = Libfireball(host_calls)
    assert lib.fireball_call0(0x01) == 0
    assert lib.fireball_call3(0x10, 1, 2, 3) == 0
    assert lib.fireball_call6(0x40, 1, 2, 3, 4, 5, 6) == 0
    assert host_calls.calls == [
        (0x01, 0, 0, 0, 0, 0, 0),
        (0x10, 1, 2, 3, 0, 0, 0),
        (0x40, 1, 2, 3, 4, 5, 6),
    ]


def test_libfireball_rejects_non_u32_host_call_values() -> None:
    lib = Libfireball(RecordingHostCalls())
    with pytest.raises(AssertionError):
        lib.fireball_call1(0x10, -1)


def test_libfireball_dedicated_host_calls() -> None:
    host_calls = RecordingHostCalls()
    lib = Libfireball(host_calls)
    assert lib.fireball_virq_register(5, 12) == 0
    assert lib.fireball_virq_unregister(5) == 0
    assert lib.fireball_vdma_start(1, 2, 3) == 0
    assert host_calls.virq_register_calls == [(5, 12)]
    assert host_calls.virq_unregister_calls == [5]
    assert host_calls.vdma_calls == [(1, 2, 3)]

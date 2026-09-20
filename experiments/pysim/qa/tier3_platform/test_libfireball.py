from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[2]
for _path in (
    _PYSIM_DIR,
    _PYSIM_DIR / "tier3_platform",
):
    _path_text = str(_path)
    if _path_text not in sys.path:
        sys.path.insert(0, _path_text)

from libfireball import Libfireball


def test_libfireball_host_call_argument_packing() -> None:
    calls: list[tuple[int, int, int, int, int, int, int]] = []

    def host_call(
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        calls.append((syscall_id, arg0, arg1, arg2, arg3, arg4, arg5))
        return 0

    lib = Libfireball(
        host_call,
        lambda _node_id, _function_index: 0,
        lambda _node_id: 0,
        lambda _source, _destination, _byte_count: 0,
    )
    assert lib.fireball_call0(0x01) == 0
    assert lib.fireball_call3(0x10, 1, 2, 3) == 0
    assert lib.fireball_call6(0x40, 1, 2, 3, 4, 5, 6) == 0
    assert calls == [
        (0x01, 0, 0, 0, 0, 0, 0),
        (0x10, 1, 2, 3, 0, 0, 0),
        (0x40, 1, 2, 3, 4, 5, 6),
    ]


def test_libfireball_rejects_non_u32_host_call_values() -> None:
    lib = Libfireball(
        lambda *_args: 0,
        lambda _node_id, _function_index: 0,
        lambda _node_id: 0,
        lambda _source, _destination, _byte_count: 0,
    )
    with pytest.raises(AssertionError):
        lib.fireball_call1(0x10, -1)


def test_libfireball_dedicated_host_calls() -> None:
    virq_register_calls: list[tuple[int, int]] = []
    virq_unregister_calls: list[int] = []
    vdma_calls: list[tuple[int, int, int]] = []

    def host_call(
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        del syscall_id, arg0, arg1, arg2, arg3, arg4, arg5
        return 0

    def virq_register(node_id: int, function_index: int) -> int:
        virq_register_calls.append((node_id, function_index))
        return 0

    def virq_unregister(node_id: int) -> int:
        virq_unregister_calls.append(node_id)
        return 0

    def vdma_start(source: int, destination: int, byte_count: int) -> int:
        vdma_calls.append((source, destination, byte_count))
        return 0

    lib = Libfireball(host_call, virq_register, virq_unregister, vdma_start)
    assert lib.fireball_virq_register(5, 12) == 0
    assert lib.fireball_virq_unregister(5) == 0
    assert lib.fireball_vdma_start(1, 2, 3) == 0
    assert virq_register_calls == [(5, 12)]
    assert virq_unregister_calls == [5]
    assert vdma_calls == [(1, 2, 3)]

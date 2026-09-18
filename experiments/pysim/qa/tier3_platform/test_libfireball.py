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

from libfireball import Libfireball, VIRQ_REGISTER, VIRQ_UNREGISTER


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

    lib = Libfireball(host_call)
    assert lib.fireball_call0(0x01) == 0
    assert lib.fireball_call3(0x20, 1, 2, 3) == 0
    assert lib.fireball_call6(0x40, 1, 2, 3, 4, 5, 6) == 0
    assert calls == [
        (0x01, 0, 0, 0, 0, 0, 0),
        (0x20, 1, 2, 3, 0, 0, 0),
        (0x40, 1, 2, 3, 4, 5, 6),
    ]


def test_libfireball_rejects_non_u32_host_call_values() -> None:
    lib = Libfireball(lambda *_args: 0)
    with pytest.raises(AssertionError):
        lib.fireball_call1(0x20, -1)


def test_libfireball_virq_registration_uses_host_call() -> None:
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

    lib = Libfireball(host_call)
    assert lib.fireball_virq_register(5, 12) == 0
    assert lib.fireball_virq_unregister(5) == 0
    assert calls == [
        (VIRQ_REGISTER, 5, 12, 0, 0, 0, 0),
        (VIRQ_UNREGISTER, 5, 0, 0, 0, 0, 0),
    ]

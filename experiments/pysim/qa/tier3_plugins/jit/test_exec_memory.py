from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest
from qa.shared.helpers import expect_assertion
from tier3_plugins.jit.exec_memory import ExecutableBuffer


def test_executable_buffer_wx_protection_lifecycle():
    """Check executable-memory transactions and reject writes after commit."""
    buf = ExecutableBuffer(64)
    try:
        buf.assert_no_rwx()
        assert buf.patch_in_progress
        buf.write(0, b"\x90\x90\x90\x90")

        buf.commit_jit_patch()
        buf.assert_no_rwx()
        assert not buf.patch_in_progress
        with expect_assertion("Cannot write to ExecutableBuffer"):
            buf.write(0, b"\xcc")

        buf.begin_jit_patch()
        buf.assert_no_rwx()
        assert buf.patch_in_progress
        buf.write(0, b"\xc3")
        buf.commit_jit_patch()
        buf.assert_no_rwx()
        buf.function_at(0, None, [])()
    finally:
        buf.close()


def _linux_buffer_permissions(buf: ExecutableBuffer) -> str:
    """Read mapping permissions from the OS, independently of buffer state."""
    assert buf.base is not None
    for line in Path("/proc/self/maps").read_text().splitlines():
        region, permissions, *_ = line.split()
        begin, end = (int(part, 16) for part in region.split("-"))
        if begin <= buf.base and buf.base + buf.size <= end:
            return permissions[:3]
    raise AssertionError("executable buffer is not fully contained in an OS mapping")


@pytest.mark.skipif(sys.platform != "linux", reason="OS mapping oracle uses Linux /proc/self/maps")
def test_executable_buffer_linux_mapping_enforces_wx():
    """Check actual OS permissions transition RW → RX → RW → RX."""
    buf = ExecutableBuffer(64)
    try:
        assert _linux_buffer_permissions(buf) == "rw-"
        buf.write(0, b"\xb8\x2a\x00\x00\x00\xc3")
        buf.commit_jit_patch()
        assert _linux_buffer_permissions(buf) == "r-x"
        assert buf.function_at(0, ctypes.c_int32, [])() == 42

        buf.begin_jit_patch()
        assert _linux_buffer_permissions(buf) == "rw-"
        buf.write(0, b"\xb8\x07\x00\x00\x00\xc3")
        buf.commit_jit_patch()
        assert _linux_buffer_permissions(buf) == "r-x"
        assert buf.function_at(0, ctypes.c_int32, [])() == 7
    finally:
        buf.close()


ALL_TESTS = sorted(
    (value for name, value in globals().items() if name.startswith("test_") and callable(value)),
    key=lambda test: test.__code__.co_firstlineno,
)

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

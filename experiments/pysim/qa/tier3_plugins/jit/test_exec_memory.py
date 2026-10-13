from __future__ import annotations

import ctypes
import platform
import sys
from pathlib import Path

import pytest
from config import JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES, JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET
from qa.private import jit_native_abi as native_abi
from qa.shared.exec_memory import ExecutableBuffer
from qa.shared.helpers import expect_assertion


def test_executable_buffer_patch_transaction_lifecycle():
    """Check logical patch transactions and reject API writes after commit."""
    buf = ExecutableBuffer(64)
    try:
        with expect_assertion("Invalid begin_jit_patch"):
            buf.begin_jit_patch()
        buf.write(0, b"\x90\x90\x90\x90")

        buf.commit_jit_patch()
        with expect_assertion("Invalid commit_jit_patch"):
            buf.commit_jit_patch()
        with expect_assertion("Cannot write to ExecutableBuffer"):
            buf.write(0, b"\xcc")

        buf.begin_jit_patch()
        buf.write(0, b"\xc3")
        buf.commit_jit_patch()
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


@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"},
    reason="OS mapping oracle covers Linux x64",
)
def test_executable_buffer_linux_mapping_keeps_execute_permission():
    """Check that x64 retains executable permission through each transaction."""
    buf = ExecutableBuffer(64)
    try:
        assert _linux_buffer_permissions(buf) == "rwx"
        assert native_abi.MEMORY_VALID_PROTECTION(ctypes.byref(buf._native)) == 1
        assert native_abi.MEMORY_NO_RWX(ctypes.byref(buf._native)) == 0
        buf.write(0, b"\xb8\x2a\x00\x00\x00\xc3")
        buf.commit_jit_patch()
        assert _linux_buffer_permissions(buf) == "rwx"
        assert buf.function_at(0, ctypes.c_int32, [])() == 42

        buf.begin_jit_patch()
        assert _linux_buffer_permissions(buf) == "rwx"
        buf.write(0, b"\xb8\x07\x00\x00\x00\xc3")
        buf.commit_jit_patch()
        assert _linux_buffer_permissions(buf) == "rwx"
        assert buf.function_at(0, ctypes.c_int32, [])() == 7
    finally:
        buf.close()


@pytest.mark.skipif(sys.platform != "linux", reason="read-only mapping oracle uses /proc")
def test_common_code_borrows_one_readonly_runtime_api_table():
    """TEST-JITR-86: separate caches share the API catalog in read-only storage."""
    first = ExecutableBuffer.region()
    second = ExecutableBuffer.region()
    try:
        pointer_offset = (
            JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET + JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES
        )
        assert first.base is not None and second.base is not None
        first_table = ctypes.c_void_p.from_address(first.base + pointer_offset).value
        second_table = ctypes.c_void_p.from_address(second.base + pointer_offset).value
        assert first_table is not None and first_table == second_table
        assert not first.base <= first_table < first.base + first.size
        assert not second.base <= first_table < second.base + second.size
        mapping_permissions = ""
        for line in Path("/proc/self/maps").read_text().splitlines():
            region, permissions, *_ = line.split()
            begin, end = (int(part, 16) for part in region.split("-"))
            if begin <= first_table < end:
                mapping_permissions = permissions[:3]
                break
        assert mapping_permissions == "r--"
        assert ctypes.c_void_p.from_address(first_table).value is not None
    finally:
        first.close()
        second.close()


ALL_TESTS = sorted(
    (value for name, value in globals().items() if name.startswith("test_") and callable(value)),
    key=lambda test: test.__code__.co_firstlineno,
)

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

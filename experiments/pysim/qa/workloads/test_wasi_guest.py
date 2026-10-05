"""TEST-INT-120..128: SDK/libc guest → real runtime/IPC/HAL → controlled endpoint.

The Fireball linkage fixture tests the existing syscall ABI. It is not the
unimplemented production libfireball Preview1/HAL adapter. Hardware and uvwasi
are controlled at their existing ports; internal transport remains real.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from ipc_router import FB_URI_HAL_STDOUT, Role
from qa.shared.helpers import make_native_interpreter
from scheduler import TaskState
from system import System
from tier2_runtime.hal.dispatch import FB_CONF_HAL_BUFFER_SIZE, HalBufferMapStatus, WasiIpcCmd
from tier2_runtime.observability.logger import LogDictionary
from tier2_runtime.syscall.hostcall import WasiErrno
from tier2_runtime.wasm.reader import parse
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.platform_config import PlatformDriverConfiguration
from tier3_platform.drivers.printk import PrintkBuffer, PrintkSink
from tier3_platform.drivers.wasi.context import WasiHostContext

ROOT = Path(__file__).resolve().parents[4]
SENTINEL = 0x1234_5678
CLOCK_VALUE = 6_543_210_987_654_321
READ_DATA = b"\x00\xffWASI-libc\x81\x7f\x19"


@dataclass(frozen=True)
class GuestArtifact:
    wasm: Path
    fireball: bool


@pytest.fixture(scope="module", params=(False, True), ids=("preview1", "fireball-fixture"))
def compiled_guest(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> GuestArtifact:
    output = tmp_path_factory.mktemp(
        "wasi-libc-fireball" if request.param else "wasi-libc-preview1"
    )
    command = [
        sys.executable,
        str(ROOT / "tools/guest_bindings/build_wasi_guest.py"),
        "--output",
        str(output),
    ]
    if request.param:
        command.append("--fireball")
    subprocess.run(command, check=True, capture_output=True, text=True)
    return GuestArtifact(output / "guest.wasm", bool(request.param))


class ControlledBackend:
    """Deterministic uvwasi-port dependency; does not replace the WASI adapter."""

    def __init__(self) -> None:
        self.failure = WasiErrno.SUCCESS
        self.calls: list[tuple[str, int]] = []
        self.output: list[bytes] = []
        self.closed = False

    def fd_write(
        self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nwritten_ptr: int
    ) -> int:
        self.calls.append(("write", fd))
        if self.failure != WasiErrno.SUCCESS:
            return int(self.failure)
        parts = []
        for index in range(iovs_len):
            base, length = struct.unpack_from("<II", memory, iovs_ptr + 8 * index)
            parts.append(bytes(memory[base : base + length]))
        data = b"".join(parts)
        self.output.append(data)
        struct.pack_into("<I", memory, nwritten_ptr, len(data))
        return 0

    def fd_read(
        self, fd: int, memory: bytearray, iovs_ptr: int, iovs_len: int, nread_ptr: int
    ) -> int:
        self.calls.append(("read", fd))
        if self.failure != WasiErrno.SUCCESS:
            return int(self.failure)
        written = 0
        for index in range(iovs_len):
            base, capacity = struct.unpack_from("<II", memory, iovs_ptr + 8 * index)
            count = min(capacity, len(READ_DATA) - written)
            memory[base : base + count] = READ_DATA[written : written + count]
            written += count
        struct.pack_into("<I", memory, nread_ptr, written)
        return 0

    def clock_time_get(
        self, clock_id: int, precision: int, memory: bytearray, time_ptr: int
    ) -> int:
        self.calls.append(("clock", clock_id))
        assert precision >= 0
        if self.failure != WasiErrno.SUCCESS:
            return int(self.failure)
        struct.pack_into("<Q", memory, time_ptr, CLOCK_VALUE)
        return 0

    def fd_close(self, fd: int) -> int:
        self.calls.append(("close", fd))
        return int(self.failure)

    def random_get(self, memory: bytearray, buf_ptr: int, buf_len: int) -> int:
        assert False, "this guest must not request random data"

    def close(self) -> None:
        self.closed = True


class ObservedHardware(StreamTransport):
    """Replace only the physical stream, retain DummyDriver and HalTask."""

    def __init__(self) -> None:
        super().__init__()
        self.system: System | None = None
        self.hal_task_id = 0
        self.guest_task_id = 0
        self.result_address = 0
        self.calls: list[bytes] = []
        self.owner_ids: list[int] = []

    def write(self, data: memoryview) -> int:
        system = self.system
        assert system is not None
        task = system.scheduler.current_task
        assert task is not None and task.task_id == self.hal_task_id
        assert task.state == TaskState.RUNNING and task.role != Role.RUNTIME
        guest = system.scheduler.get_task(self.guest_task_id)
        assert guest is not None and guest.state != TaskState.TERMINATED
        assert system.pool._mapped_task_id == self.guest_task_id
        assert system.pool._mapped_buffer_id == 0
        assert data.obj is system.pool.buffer(0)._storage
        assert system._guest_memory is not None
        assert struct.unpack_from("<I", system._guest_memory, self.result_address)[0] == SENTINEL
        self.calls.append(bytes(data))
        self.owner_ids.append(self.guest_task_id)
        return super().write(data)


class GuestSession:
    def __init__(self, artifact: GuestArtifact) -> None:
        self.backend = ControlledBackend()
        self.hardware = ObservedHardware()
        self.log = PrintkBuffer()
        dictionary = LogDictionary()
        self.system = System(
            drivers=PlatformDriverConfiguration(
                stdout_transport=self.hardware,
                printk=PrintkSink(self.log, dictionary.decode_record),
                wasi_backend=self.backend,
            ),
            log_dictionary=dictionary,
        )
        self.hardware.system = self.system
        self.driver = DummyDriver(self.system.pool, transport=self.hardware)
        self.hardware.hal_task_id = self.system.start_hal_driver(self.driver, FB_URI_HAL_STDOUT)
        self.module = parse(artifact.wasm.read_bytes())
        assert self.module.memory is not None and self.module.memory.min_pages == 1
        self.host = WasiHostContext(self.system)
        self.module.init_memory_data(self.host.guest_memory, ())
        functions = self.host.build_interpreter_host_functions(self.module)
        assert all(function is not None for function in functions)
        self.interpreter = make_native_interpreter(
            self.module,
            memory=self.host.guest_memory,
            host_functions=functions,
            bump_allocator=self.system.runtime_engine.bump_allocator,
        )
        self.call("_initialize")
        self.result_address = self.call("result_address")[0]
        self.hardware.result_address = self.result_address
        for index in range(4):
            self.system.pool.buffer(index)._storage[:] = (
                bytes((0xA5 + index,)) * FB_CONF_HAL_BUFFER_SIZE
            )

    def call(self, name: str, args: tuple[int, ...] = ()) -> tuple[int, ...]:
        if name not in ("_initialize", "result_address"):
            struct.pack_into("<I", self.host.guest_memory, self.result_address, SENTINEL)
        task_id = self.system.scheduler.spawn(
            "sdk_libc_guest",
            self.system.run_guest(self.interpreter, self.module.export_func_index(name), args),
            role=Role.RUNTIME,
        )
        self.hardware.guest_task_id = task_id
        self.system.scheduler.run_until_idle()
        task = self.system.scheduler.get_task(task_id)
        assert task is not None and task.state == TaskState.TERMINATED
        return tuple(int(value) for value in task.result)

    def result(self) -> tuple[int, int, int, bytes]:
        rc, error, clock = struct.unpack_from("<iiQ", self.host.guest_memory, self.result_address)
        data = bytes(self.host.guest_memory[self.result_address + 16 : self.result_address + 80])
        return rc, error, clock, data

    def slots(self) -> tuple[bytes, ...]:
        return tuple(bytes(self.system.pool.buffer(index)._storage) for index in range(4))

    def assert_unmapped(self) -> None:
        assert self.system.pool._mapped_task_id is None
        assert self.system.pool._mapped_buffer_id is None
        for index in range(4):
            assert not self.system.pool.can_view(self.system.pool.buffer(index), 0, 1)

    def close(self) -> None:
        self.system.shutdown()
        assert self.backend.closed


@pytest.fixture
def session(compiled_guest: GuestArtifact) -> Iterator[GuestSession]:
    instance = GuestSession(compiled_guest)
    try:
        yield instance
    finally:
        instance.close()


def expected_payload(count: int) -> bytes:
    return bytes((37 * index + 11) & 255 for index in range(count))


def expected_chunks(count: int) -> list[bytes]:
    payload = expected_payload(count)
    split = count // 3
    return [
        vector[start : start + FB_CONF_HAL_BUFFER_SIZE]
        for vector in (payload[:split], payload[split:])
        for start in range(0, len(vector), FB_CONF_HAL_BUFFER_SIZE)
    ]


def assert_stdout_transfer(session: GuestSession, count: int) -> None:
    before = session.slots()
    calls_before = len(session.hardware.calls)
    assert session.call("write_probe", (1, count)) == (count,)
    assert session.result()[:2] == (count, 0)
    chunks = expected_chunks(count)
    assert session.hardware.calls[calls_before:] == chunks
    assert session.hardware.drain_output() == expected_payload(count)
    assert session.hardware.drain_output() == b""
    expected_slot = bytearray(before[0])
    for chunk in chunks:
        expected_slot[: len(chunk)] = chunk
    assert session.slots() == (bytes(expected_slot), *before[1:])
    session.assert_unmapped()
    assert session.backend.calls == []
    hal_task = session.system.hal_task_for(FB_URI_HAL_STDOUT)
    assert hal_task is not None
    assert hal_task.processed_count == len(session.hardware.calls)
    if chunks:
        assert hal_task.last_handled_cmd == WasiIpcCmd.STREAM_WRITE_BUFFER


def test_sdk_linkage_uses_real_libc_and_resolves_expected_imports(
    compiled_guest: GuestArtifact,
    session: GuestSession,
) -> None:
    """TEST-INT-120: compile/link evidence and actual import resolution are distinct."""
    module = session.module
    imports = {
        (module.import_module_name(index), module.import_field_name(index))
        for index in range(len(module.imports))
    }
    expected = (
        {("fireball", "fireball_call")}
        if compiled_guest.fireball
        else {
            ("wasi_snapshot_preview1", name)
            for name in ("fd_write", "fd_read", "fd_close", "clock_time_get")
        }
    )
    assert imports == expected
    link_map = compiled_guest.wasm.with_name("guest.map").read_text()
    for symbol in ("libc.a", "writev", "clock_gettime"):
        assert symbol in link_map
    assert "clang version" in compiled_guest.wasm.with_name("compiler.txt").read_text()


@pytest.mark.parametrize("count", (0, 1, 255, 256, 257, 513, 800))
def test_sdk_stdout_traverses_ipc_hal_task_and_physical_boundary(
    session: GuestSession, count: int
) -> None:
    """TEST-INT-121: real libc scatter/gather, chunk edges, owner, output and unmapping."""
    assert_stdout_transfer(session, count)


@settings(max_examples=32, deadline=None, derandomize=True, print_blob=True)
@given(counts=st.lists(st.integers(0, 800), min_size=1, max_size=3))
def test_sdk_repeated_stdout_operations_reuse_unmapped_slots(
    compiled_guest: GuestArtifact, counts: list[int]
) -> None:
    """TEST-INT-122: bounded libc/IPC histories retain no output or mapping between calls."""
    instance = GuestSession(compiled_guest)
    try:
        for count in counts:
            assert_stdout_transfer(instance, count)
    finally:
        instance.close()


@pytest.mark.parametrize("count", (1, 257))
def test_sdk_stderr_uses_log_endpoint_and_preserves_hal_slots(
    session: GuestSession, count: int
) -> None:
    """TEST-INT-123: logger must not be substituted by the stdout/HAL endpoint."""
    before = session.slots()
    assert session.call("write_probe", (2, count)) == (count,)
    assert session.result()[:2] == (count, 0)
    assert bytes(session.log._output[: session.log._output_len]) == expected_payload(count)
    assert session.hardware.calls == [] and session.backend.calls == []
    assert session.slots() == before
    session.assert_unmapped()


def test_sdk_busy_mapping_preserves_owner_and_recovers_after_owner_unmaps(
    session: GuestSession,
) -> None:
    """TEST-INT-124: libc -1/errno, old owner/data, no I/O, then a valid retry."""
    owner_id = session.system.scheduler.spawn("mapping_owner", role=Role.RUNTIME)
    owner = session.system.scheduler.get_task(owner_id)
    assert owner is not None
    session.system.scheduler.detach(owner)
    with session.system.scheduler.task_context(owner):
        assert session.system.pool.map_for_io(0) == HalBufferMapStatus.MAPPED
    before = session.slots()
    assert session.call("write_probe", (1, 513)) == (-1,)
    assert session.result()[:2] == (-1, int(WasiErrno.AGAIN))
    assert session.hardware.calls == [] and session.backend.calls == []
    assert session.slots() == before
    assert session.system.pool._mapped_task_id == owner_id
    assert session.system.pool._mapped_buffer_id == 0
    with session.system.scheduler.task_context(owner):
        session.system.pool.unmap_after_io(0)
    assert_stdout_transfer(session, 513)


@pytest.mark.parametrize(
    "failure", (WasiErrno.SUCCESS, WasiErrno.BADF, WasiErrno.IO, WasiErrno.NOSYS)
)
def test_sdk_nonstandard_fd_write_reaches_backend_and_returns_libc_errno(
    session: GuestSession, failure: WasiErrno
) -> None:
    """TEST-INT-125: fd>=3 stays at the configured backend, not stdout/log/HAL."""
    session.backend.failure = failure
    before = session.slots()
    expected_rc = 33 if failure == WasiErrno.SUCCESS else -1
    assert session.call("write_probe", (3, 33)) == (expected_rc,)
    assert session.result()[:2] == (expected_rc, int(failure))
    assert session.backend.calls == [("write", 3)]
    assert session.backend.output == (
        [expected_payload(33)] if failure == WasiErrno.SUCCESS else []
    )
    assert session.hardware.calls == [] and session.log._output_len == 0
    assert session.slots() == before
    session.assert_unmapped()


@pytest.mark.parametrize(
    "failure", (WasiErrno.SUCCESS, WasiErrno.BADF, WasiErrno.IO, WasiErrno.NOSYS)
)
def test_sdk_read_observes_backend_bytes_and_preserves_unwritten_tail(
    session: GuestSession, failure: WasiErrno
) -> None:
    """TEST-INT-126: actual libc read consumes guest iovecs and translates target errno."""
    session.backend.failure = failure
    before = session.slots()
    expected_rc = len(READ_DATA) if failure == WasiErrno.SUCCESS else -1
    assert session.call("read_probe", (3, 32)) == (expected_rc,)
    rc, error, _, data = session.result()
    assert (rc, error) == (expected_rc, int(failure))
    expected_data = (
        READ_DATA + b"\xa5" * (64 - len(READ_DATA))
        if failure == WasiErrno.SUCCESS
        else b"\xa5" * 64
    )
    assert data == expected_data
    assert session.backend.calls == [("read", 3)]
    assert session.hardware.calls == [] and session.slots() == before
    session.assert_unmapped()


@pytest.mark.parametrize(
    "failure", (WasiErrno.SUCCESS, WasiErrno.BADF, WasiErrno.IO, WasiErrno.NOSYS)
)
def test_sdk_clock_keeps_full_u64_through_libc_timespec_conversion(
    session: GuestSession, failure: WasiErrno
) -> None:
    """TEST-INT-127: high timestamp bits cannot be lost at WASI/Fireball/backend boundaries."""
    session.backend.failure = failure
    expected_rc = 0 if failure == WasiErrno.SUCCESS else -1
    assert session.call("clock_probe") == (expected_rc,)
    rc, error, clock, _ = session.result()
    assert (rc, error) == (expected_rc, int(failure))
    assert clock == (CLOCK_VALUE if failure == WasiErrno.SUCCESS else 0)
    assert session.backend.calls == [("clock", 1)]
    assert session.hardware.calls == []
    session.assert_unmapped()


@pytest.mark.parametrize("failure", (WasiErrno.SUCCESS, WasiErrno.BADF))
def test_sdk_close_reaches_configured_backend(session: GuestSession, failure: WasiErrno) -> None:
    """TEST-INT-128: actual libc close propagates the configured backend result."""
    session.backend.failure = failure
    expected_rc = 0 if failure == WasiErrno.SUCCESS else -1
    assert session.call("close_probe", (3,)) == (expected_rc,)
    assert session.result()[:2] == (expected_rc, int(failure))
    assert session.backend.calls == [("close", 3)]
    assert session.hardware.calls == []
    session.assert_unmapped()

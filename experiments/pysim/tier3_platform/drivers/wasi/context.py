"""Tier 3 WASI and Fireball host-call driver adapters.

Fireball owns the guest-visible stdout and logger paths. Other WASI Preview 1
operations are delegated to the configured uvwasi backend; ``proc_exit``
remains a Fireball host call so the runtime can record guest termination.
"""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Protocol

from config import FB_CONF_MAX_IMPORTS
from hal_dispatch import (
    ARG_BUFFER_HANDLE,
    ARG_LENGTH,
    ARG_OFFSET,
    FB_CONF_HAL_BUFFER_SIZE,
    HalBufferHandle,
    HalBufferPool,
    HalTask,
    WasiIpcCmd,
)
from hostcall import FbSyscallId, FireballHostCallPort, WasiPreview1Host
from ipc_router import IPCRouter
from loader import fnv1a_32
from tier2_runtime.logger import Logger
from memory import MemoryManager
from scheduler import ChannelAction, Scheduler
from system_containers import (
    ReadOnlyFlatMapStorage,
    ReadOnlyFlatMapView,
    ReadOnlyRadixBinaryTreeStorage,
    StaticVector,
)
from wasi_bindings import WasiHalBindings
from wasm_module import Module
from tier3_platform.drivers.wasi.uvwasi import WasiPreview1Backend

WasiValue = int


class WasiRuntimeHost(Protocol):
    """Runtime services required by the host-side WASI import adapter."""

    __slots__ = ()

    host_calls: FireballHostCallPort
    ipc: IPCRouter
    logger: Logger
    memory_manager: MemoryManager
    pool: HalBufferPool
    scheduler: Scheduler
    wasi_hal_bindings: WasiHalBindings
    wasi_backend: WasiPreview1Backend

    def bind_runtime(self, memory: bytearray | None) -> None:
        """Bind guest linear memory and HAL slots to the active runtime."""

    def attach_wasi_context(self, context: WasiPreview1Host) -> None:
        """Attach the Preview 1 host callbacks used by generic syscalls."""

    def hal_task_for(self, uri: str) -> HalTask | None:
        """Resolve the HAL task registered for one URI."""


# ==============================================================================
# Fireball-owned WASI 0.3p URI and HAL bridge
# ==============================================================================
@dataclass(frozen=True, slots=True)
class WasiInterfaceVTable:
    """
    One URI's set of WASI 0.3p operations as a fixed-shape struct of
    function-pointer fields -- the C++ analogue of a struct-of-function-
    pointers vtable. A command *name* ("write-buffer", "get-now", ...) is not
    a URI and not log output, so under the Native ABI rule it cannot be a string
    dict key; each name instead becomes one statically-named field,
    resolved at compile time exactly like C++ member access. Unpopulated
    fields default to None; dispatch_command checks that directly rather
    than via `in`/`.get()` (dict-only APIs with no C++ counterpart).
    """

    write: Callable[..., WasiValue] | None = None
    read: Callable[..., WasiValue] | None = None
    close: Callable[..., WasiValue] | None = None
    write_buffer: Callable[..., WasiValue] | None = None
    read_buffer: Callable[..., WasiValue] | None = None
    flush: Callable[..., WasiValue] | None = None
    get_now: Callable[..., WasiValue] | None = None
    get_resolution: Callable[..., WasiValue] | None = None
    subscribe: Callable[..., WasiValue] | None = None
    set_pin: Callable[..., WasiValue] | None = None
    get_pin: Callable[..., WasiValue] | None = None
    config_pin: Callable[..., WasiValue] | None = None
    subscribe_edge: Callable[..., WasiValue] | None = None
    transfer: Callable[..., WasiValue] | None = None
    transfer_buffer: Callable[..., WasiValue] | None = None
    config: Callable[..., WasiValue] | None = None
    log: Callable[..., WasiValue] | None = None


class Wasi03pEngine:
    """Provide Fireball URI resolution, IPC dispatch, and the HAL buffer pool."""

    __slots__ = ("sysv", "bindings", "_interface_storage")

    def __init__(self, sysv: WasiRuntimeHost, bindings: WasiHalBindings | None = None):
        self.sysv = sysv
        self.bindings = bindings if bindings is not None else sysv.wasi_hal_bindings
        self._interface_storage: ReadOnlyFlatMapStorage[int, WasiInterfaceVTable]
        self._setup_standard_interfaces()

    def _setup_standard_interfaces(self) -> None:
        """
        Registers Fireball-owned stdout/logger and WASI stream aliases with
        hierarchical URIs. The registry itself is a read-only flat-map storage keyed by
        URI (std::string_view in C++) -- system_containers.md names this
        exact case ("the IPC registry") as flat_map_view's string-key use,
        so a sorted array here, not a dict, is the spec-sanctioned shape.
        """
        console_iface = WasiInterfaceVTable(
            write_buffer=lambda handle, offset, length: self._write_buffer(
                self.bindings.stdout_uri, handle, offset, length
            ),
        )
        logger_iface = WasiInterfaceVTable(
            log=lambda msg: self.sysv.logger.debug(msg),
        )

        entries: StaticVector[tuple[int, WasiInterfaceVTable]] = StaticVector.of(
            (
                (fnv1a_32(self.bindings.stdout_uri), console_iface),
                (fnv1a_32("wasi:io/streams@0.3.0"), console_iface),
                (fnv1a_32("wasi:io/streams"), console_iface),
                (fnv1a_32("wasi:cli/stdout@0.3.0"), console_iface),
                (fnv1a_32("wasi:cli/stdout"), console_iface),
                (fnv1a_32(self.bindings.logger_uri), logger_iface),
            ),
            capacity=FB_CONF_MAX_IMPORTS,
        )
        entries.sort(key=lambda e: e[0])
        self._interface_storage = ReadOnlyFlatMapStorage.create(entries)

    def get_interface(self, uri: str) -> WasiInterfaceVTable | None:
        """Resolves an interface descriptor by its Hierarchical IPC communication URI."""
        return self._interface_storage.view().find(fnv1a_32(uri))

    def dispatch_command(self, uri: str, cmd_id: int, params: ReadOnlyFlatMapView) -> WasiValue:
        """
        Dispatches through the HAL task. The runtime never invokes a driver
        vtable directly; URI resolution and command execution are separate
        IPC operations.
        """
        return self.send_ipc_command(uri, cmd_id, params)

    def send_ipc_command(self, uri: str, cmd_id: int, params: ReadOnlyFlatMapView) -> WasiValue:
        """
        Sends an IPC Driver Command to the HAL Server Task via IPCRouter ({hal_dispatch.md}).
        HAL operates as a distinct task and communicates strictly over IPC rendezvous.
        """
        from hal_dispatch import make_hal_ipc_message
        from ipc_router import IPCStatus, Role

        caller_task = self.sysv.scheduler.current_task
        assert caller_task is not None, "WASI IPC requires an active runtime task"

        def sender_coro() -> Generator[tuple[ChannelAction, None], None, None]:
            status, channel = self.sysv.ipc.lookup(uri)
            assert status == IPCStatus.COMPLETED
            assert channel is not None
            msg = make_hal_ipc_message(
                cmd_id, params.entries, memory_manager=self.sysv.memory_manager
            )
            yield from self.sysv.ipc.send(channel, msg)

        self.sysv.scheduler.spawn("wasi_ipc_sender", sender_coro(), role=Role.RUNTIME)
        self.sysv.scheduler.run_until_idle()
        self.sysv.scheduler.require_active_task(caller_task)

        target_task = self.sysv.hal_task_for(uri)
        assert target_task is not None, f"HAL driver is not started: {uri}"
        return target_task.last_result

    # Resource methods: Tier 2 only builds and sends HAL commands.
    def _send_simple(self, uri: str, cmd_id: WasiIpcCmd) -> int:
        result = self.send_ipc_command(uri, cmd_id, ReadOnlyFlatMapView(()))
        return int(result)

    def _write_buffer(self, uri: str, handle: HalBufferHandle, offset: int, length: int) -> int:
        params = ReadOnlyFlatMapView(
            sorted(
                (
                    (ARG_BUFFER_HANDLE, handle.buffer_id),
                    (ARG_OFFSET, offset),
                    (ARG_LENGTH, length),
                )
            )
        )
        result = self.send_ipc_command(uri, WasiIpcCmd.STREAM_WRITE_BUFFER, params)
        return int(result)

# ============================================================================== 
# WASI 0.1p Compatibility Layer (Adapter Pattern wrapping WASI 0.3p)
# ==============================================================================
class WasiHostContext:
    """
    WASI Preview 1 Host Context and ABI Adapter.
    Adapts Fireball-owned stdout/logger calls and delegates the remaining
    wasi_snapshot_preview1 functions to uvwasi.
    """

    __slots__ = (
        "sysv",
        "guest_memory",
        "bindings",
        "core03p",
        "uvwasi",
        "_keepalive_trampolines",
        "_import_storage",
    )

    def __init__(
        self,
        sysv: WasiRuntimeHost,
        guest_memory: bytearray | None = None,
        bindings: WasiHalBindings | None = None,
        uvwasi: WasiPreview1Backend | None = None,
    ):
        self.sysv = sysv
        self.guest_memory = guest_memory if guest_memory is not None else bytearray(64 * 1024)
        self.sysv.bind_runtime(self.guest_memory)
        self.bindings = bindings if bindings is not None else sysv.wasi_hal_bindings
        self.core03p = Wasi03pEngine(sysv, self.bindings)
        self.uvwasi = uvwasi if uvwasi is not None else sysv.wasi_backend
        self.sysv.attach_wasi_context(self)
        self._keepalive_trampolines: StaticVector[Callable[..., int]] = StaticVector(
            capacity=FB_CONF_MAX_IMPORTS
        )

        # Build static host import table via ReadOnlyRadixBinaryTreeView
        host_entries: StaticVector[tuple[str, str, Callable[..., int]]] = StaticVector.of(
            (
                ("wasi_snapshot_preview1", "fd_write", self.fd_write),
                ("wasi_snapshot_preview1", "fd_read", self.fd_read),
                ("wasi_snapshot_preview1", "fd_close", self.fd_close),
                ("wasi_snapshot_preview1", "clock_time_get", self.clock_time_get),
                ("wasi_snapshot_preview1", "proc_exit", self.proc_exit),
                ("wasi_snapshot_preview1", "random_get", self.random_get),
                ("wasi_unstable", "fd_write", self.fd_write),
                ("wasi_unstable", "fd_read", self.fd_read),
                ("wasi_unstable", "fd_close", self.fd_close),
                ("wasi_unstable", "clock_time_get", self.clock_time_get),
                ("wasi_unstable", "proc_exit", self.proc_exit),
                ("wasi_unstable", "random_get", self.random_get),
                # WASI 0.3p Dynamic URI Interface Resolver import
                ("wasi:resolver", "get_interface", self.wasi03p_get_interface),
                ("fireball", "get_interface", self.wasi03p_get_interface),
                ("fireball", "fireball_call", sysv.host_calls.fireball_call),
                ("fireball", "virq_register", sysv.host_calls.virq_register),
                ("fireball", "virq_unregister", sysv.host_calls.virq_unregister),
                ("fireball", "vdma_start", sysv.host_calls.vdma_start),
                ("fireball", "fd_write", self.fd_write),
            ),
            capacity=FB_CONF_MAX_IMPORTS,
        )
        hashed_entries: StaticVector[tuple[int, tuple[str, str, Callable[..., int]]]] = (
            StaticVector(capacity=FB_CONF_MAX_IMPORTS)
        )
        for mod, field, handler in host_entries:
            h = fnv1a_32(f"{mod}::{field}")
            hashed_entries.append((h, (mod, field, handler)))

        hashed_entries.sort(key=lambda x: x[0])
        keys: StaticVector[int] = StaticVector(capacity=len(hashed_entries))
        values: StaticVector[tuple[str, str, Callable[..., int]]] = StaticVector(
            capacity=len(hashed_entries)
        )
        for key, value in hashed_entries:
            keys.append(key)
            values.append(value)
        radix_shift = 28
        self._import_storage = ReadOnlyRadixBinaryTreeStorage.create(
            keys,
            values,
            radix_shift=radix_shift,
        )

    # --------------------------------------------------------------------------
    # WASI 0.3p URI Resolver Entry Point
    # --------------------------------------------------------------------------
    def wasi03p_get_interface(self, uri_ptr: int, uri_len: int) -> int:
        """
        Resolves URI string from guest memory and returns handle ID.
        `errors="replace"` makes the decode itself total (never raises) --
        malformed guest bytes just fail the lookup below via a mismatched
        URI, rather than needing a try/except (exceptions unavailable
        as control flow once disabled in the target C++ build).
        """
        uri = self.guest_memory[uri_ptr : uri_ptr + uri_len].decode("utf-8", errors="replace")
        iface = self.core03p.get_interface(uri)
        return 1 if iface is not None else 0

    # --------------------------------------------------------------------------
    # WASI 0.1p (Preview 1) adapted handlers
    # --------------------------------------------------------------------------
    def fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        """
        Routes Fireball stdout/logger writes through the Fireball HAL and
        delegates all other descriptors to uvwasi. Every guest-memory offset
        is validated before the first Fireball write.
        """
        if fd == 2:
            return self._fd_write_log(iovs_ptr, iovs_len, nwritten_ptr)
        if fd != 1:
            return self.uvwasi.fd_write(fd, self.guest_memory, iovs_ptr, iovs_len, nwritten_ptr)
        mem = self.guest_memory
        mem_len = len(mem)
        if iovs_len < 0 or nwritten_ptr < 0 or nwritten_ptr > mem_len - 4:
            return 21  # EFAULT
        if iovs_ptr < 0 or iovs_ptr > mem_len or iovs_len > (mem_len - iovs_ptr) // 8:
            return 21  # EFAULT

        # First pass: validate every iovec and its payload without side effects.
        for i in range(iovs_len):
            iov_offset = iovs_ptr + (i * 8)
            base, length = struct.unpack_from("<II", mem, iov_offset)
            if base > mem_len or length > mem_len - base:
                return 21  # EFAULT

        total_written = 0
        # Second pass: copy each guest slice into the HAL-owned buffer and
        # send it through the dedicated HAL task. The driver receives only a
        # buffer ID and slice coordinates, never a guest pointer.
        for i in range(iovs_len):
            iov_offset = iovs_ptr + (i * 8)
            base, length = struct.unpack_from("<II", mem, iov_offset)
            remaining = length
            source_offset = base
            while remaining:
                chunk_len = min(remaining, FB_CONF_HAL_BUFFER_SIZE)
                handle = self.sysv.pool.buffer(0)
                view = self.sysv.pool.view(handle, 0, chunk_len)
                view[:] = mem[source_offset : source_offset + chunk_len]
                result = self.core03p.send_ipc_command(
                    self.bindings.stdout_uri,
                    WasiIpcCmd.STREAM_WRITE_BUFFER,
                    ReadOnlyFlatMapView(
                        sorted(
                            (
                                (ARG_BUFFER_HANDLE, handle.buffer_id),
                                (ARG_OFFSET, 0),
                                (ARG_LENGTH, chunk_len),
                            )
                        )
                    ),
                )
                written = int(result)
                assert written == chunk_len
                total_written += written
                source_offset += chunk_len
                remaining -= chunk_len

        struct.pack_into("<I", mem, nwritten_ptr, total_written)
        return 0  # SUCCESS

    def _fd_write_log(self, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        """Routes stderr bytes to the Fireball logger sink, not to stdout."""
        mem = self.guest_memory
        mem_len = len(mem)
        if iovs_len < 0 or nwritten_ptr < 0 or nwritten_ptr > mem_len - 4:
            return 21
        if iovs_ptr < 0 or iovs_ptr > mem_len or iovs_len > (mem_len - iovs_ptr) // 8:
            return 21
        for i in range(iovs_len):
            base, length = struct.unpack_from("<II", mem, iovs_ptr + i * 8)
            if base > mem_len or length > mem_len - base:
                return 21
        total_written = 0
        for i in range(iovs_len):
            base, length = struct.unpack_from("<II", mem, iovs_ptr + i * 8)
            written = self.sysv.logger.transport.write(memoryview(mem)[base : base + length])
            assert written == length
            total_written += written
        struct.pack_into("<I", mem, nwritten_ptr, total_written)
        return 0

    def fd_read(self, fd: int, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        """Delegate wasi_snapshot_preview1:fd_read to uvwasi."""
        return self.uvwasi.fd_read(fd, self.guest_memory, iovs_ptr, iovs_len, nread_ptr)

    def fd_close(self, fd: int) -> int:
        """Delegate wasi_snapshot_preview1:fd_close to uvwasi."""
        return self.uvwasi.fd_close(fd)

    def clock_time_get(self, clock_id: int, precision: int, time_ptr: int) -> int:
        """
        Delegate wasi_snapshot_preview1:clock_time_get to uvwasi.
        """
        return self.uvwasi.clock_time_get(clock_id, precision, self.guest_memory, time_ptr)

    def proc_exit(self, exit_code: int) -> int:
        return int(
            self.sysv.host_calls.fireball_call(FbSyscallId.WASI_PROC_EXIT, exit_code, 0, 0, 0, 0, 0)
        )

    def random_get(self, buf_ptr: int, buf_len: int) -> int:
        """Delegate wasi_snapshot_preview1:random_get to uvwasi."""
        return self.uvwasi.random_get(self.guest_memory, buf_ptr, buf_len)

    def get_handler_for_import(
        self, module_name: str, field_name: str
    ) -> Callable[..., int] | None:
        """Resolves an import name to the corresponding host function callable via ReadOnlyRadixBinaryTreeView."""
        h = fnv1a_32(f"{module_name}::{field_name}")
        candidate = self._import_storage.view().find(h)
        if candidate is not None:
            mod, field, handler = candidate
            if mod == module_name and field == field_name:
                return handler
        return None

    def build_interpreter_host_functions(
        self, module: Module
    ) -> StaticVector[Callable[..., int] | None]:
        """
        Maps all imported functions in the module to host function
        callables for the Interpreter. Import indices are 0..len(imports)-1
        by WASM encoding (dense, no gaps), so a fixed-size array indexed by
        that ordinal is the direct fit -- not a dict, which would imply a
        sparse/arbitrary key space this table never has.
        """
        host_funcs: StaticVector[Callable[..., int] | None] = StaticVector(
            capacity=len(module.imports)
        )
        for _ in module.imports:
            host_funcs.append(None)
        for idx, _imp in enumerate(module.imports):
            host_funcs[idx] = self.get_handler_for_import(
                module.import_module_name(idx), module.import_field_name(idx)
            )
        return host_funcs

    def build_jit_trampolines(self, module: Module) -> StaticVector[int | None]:
        """Creates ctypes CFUNCTYPE native trampolines for JIT execution."""
        trampolines: StaticVector[int | None] = StaticVector(capacity=len(module.imports))
        for _ in module.imports:
            trampolines.append(None)
        for idx, imp in enumerate(module.imports):
            handler = self.get_handler_for_import(
                module.import_module_name(idx), module.import_field_name(idx)
            )
            if handler is None:
                continue
            ft = module.type_at(imp.type_index)
            nparams = len(ft.params)
            c_args = (ctypes.c_uint32,) * nparams
            c_ret = ctypes.c_uint32  # WASI returns errno as u32
            c_func_type = ctypes.CFUNCTYPE(c_ret, *c_args)

            def make_wrapper(h: Callable[..., int], np: int) -> Callable[..., int]:
                def wrapper(*args: int) -> int:
                    return h(*args[:np]) & 0xFFFF_FFFF

                return wrapper

            wrapped = make_wrapper(handler, nparams)
            t = c_func_type(wrapped)
            self._keepalive_trampolines.append(t)
            addr = ctypes.cast(t, ctypes.c_void_p).value
            assert addr is not None
            trampolines[idx] = addr
        return trampolines

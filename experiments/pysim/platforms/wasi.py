"""
experiments/pysim/wasi.py
HAL = WASI 0.3p Unified Core Engine and WASI 0.1p Compatibility Adapter.
Implements docs/components/tier1_interface/interface_wit.md,
docs/components/tier3_platform/platform_hal.md, and
docs/specs/wasi_preview1_abi.md.

- WASI 0.3p (Core): URI-based dynamic interface resolver (resolver.get-interface),
  resource handle tables, streams (wasi:io), clocks (wasi:clocks), CLI (wasi:cli),
  and hardware peripherals (fireball:hal/*).
- WASI 0.1p (Adapter): wasi_snapshot_preview1 ABI as a zero-cost wrapper delegating
  directly to WASI 0.3p resources.
"""

from __future__ import annotations

import ctypes
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from hal import (
    ARG_CLOCK_HZ,
    ARG_EDGE_TYPE,
    ARG_FD,
    ARG_LENGTH,
    ARG_MAX_LEN,
    ARG_MODE,
    ARG_NANOS,
    ARG_OFFSET,
    ARG_PIN_NO,
    ARG_QUERY_CMD_ID,
    ARG_RX_SHM_HANDLE,
    ARG_SHM_HANDLE,
    ARG_SLAVE_ADDR,
    ARG_TASK_ID,
    ARG_TX_SHM_HANDLE,
    ARG_VAL,
)
from loader import fnv1a_32
from system import FbSyscallId, System
from system_containers import FlatMapView, RadixBinaryTreeView
from wasm_module import Module


class WasiIpcCmd(IntEnum):
    """WASI 0.3p IPC Driver Command Protocol IDs."""

    # Common Capability Query
    QUERY_CAPS = 0x00
    # Stream (0x01..0x04)
    STREAM_WRITE_SHM = 0x01
    STREAM_READ_SHM = 0x02
    STREAM_FLUSH = 0x03
    STREAM_CLOSE = 0x04
    # Clock / Timer (0x10..0x12)
    CLOCK_GET_NOW = 0x10
    CLOCK_SUBSCRIBE = 0x11
    CLOCK_GET_RES = 0x12
    # GPIO / Trigger (0x20..0x23)
    GPIO_SET_PIN = 0x20
    GPIO_GET_PIN = 0x21
    GPIO_CONFIG_PIN = 0x22
    GPIO_SUBSCRIBE_EDGE = 0x23
    # Bus (0x30..0x31)
    BUS_TRANSFER_SHM = 0x30
    BUS_CONFIG = 0x31
    # Poll (0x40..0x41)
    POLL_CHECK = 0x40
    POLL_WAIT = 0x41


# ==============================================================================
# WASI 0.3p Core Subsystem (HAL = WASI 0.3p)
# ==============================================================================
@dataclass(frozen=True, slots=True)
class WasiInterfaceVTable:
    """
    One URI's set of WASI 0.3p operations as a fixed-shape struct of
    function-pointer fields -- the C++ analogue of a struct-of-function-
    pointers vtable. A command *name* ("write-shm", "get-now", ...) is not
    a URI and not log output, so under the POD rule it cannot be a string
    dict key; each name instead becomes one statically-named field,
    resolved at compile time exactly like C++ member access. Unpopulated
    fields default to None; dispatch_command checks that directly rather
    than via `in`/`.get()` (dict-only APIs with no C++ counterpart).
    """

    write: Callable[..., Any] | None = None
    read: Callable[..., Any] | None = None
    close: Callable[..., Any] | None = None
    write_shm: Callable[..., Any] | None = None
    read_shm: Callable[..., Any] | None = None
    flush: Callable[..., Any] | None = None
    get_now: Callable[..., Any] | None = None
    get_resolution: Callable[..., Any] | None = None
    subscribe: Callable[..., Any] | None = None
    set_pin: Callable[..., Any] | None = None
    get_pin: Callable[..., Any] | None = None
    config_pin: Callable[..., Any] | None = None
    subscribe_edge: Callable[..., Any] | None = None
    transfer: Callable[..., Any] | None = None
    transfer_shm: Callable[..., Any] | None = None
    config: Callable[..., Any] | None = None
    log: Callable[..., Any] | None = None


class Wasi03pEngine:
    """WASI 0.3p Core Engine providing Hierarchical URI Resolution, IPC Command Dispatch, and SHM."""

    def __init__(self, sysv: System):
        self.sysv = sysv
        self._interfaces: FlatMapView[str, WasiInterfaceVTable]
        self._setup_standard_interfaces()

    def _setup_standard_interfaces(self) -> None:
        """
        Registers standard WASI 0.3p and Fireball HAL interfaces with
        Hierarchical URIs. The registry itself is a FlatMapView keyed by
        URI (std::string_view in C++) -- system_containers.md names this
        exact case ("the IPC registry") as flat_map_view's string-key use,
        so a sorted array here, not a dict, is the spec-sanctioned shape.
        """
        uart_iface = WasiInterfaceVTable(
            write=self._stream_write,
            read=self._stream_read,
            close=self._stream_close,
            write_shm=self._write_shm,
            read_shm=self._read_shm,
            flush=lambda: 0,
        )
        timer_iface = WasiInterfaceVTable(
            get_now=self._clock_get_now,
            get_resolution=lambda: 1_000_000,  # 1ms
            subscribe=lambda nanos: 1,  # pollable handle
        )
        console_iface = WasiInterfaceVTable(
            write=self._console_write,
            write_shm=self._write_shm,
        )
        gpio_iface = WasiInterfaceVTable(
            set_pin=lambda pin, val: self.sysv.transport.write(f"[GPIO:{pin}={val}]".encode()),
            get_pin=lambda pin: 0,
            config_pin=lambda pin, mode: 0,
            subscribe_edge=lambda pin, edge: 1,  # pollable handle
        )
        bus_iface = WasiInterfaceVTable(
            transfer=lambda tx, rx: len(tx),
            transfer_shm=self._transfer_shm,
            config=lambda clock_hz, addr, mode: 0,
        )
        logger_iface = WasiInterfaceVTable(
            log=lambda msg: self.sysv.logger.debug(msg),
        )

        entries: list[tuple[str, WasiInterfaceVTable]] = [
            ("fireball://device/uart/0", uart_iface),
            ("fireball://service/stdout/0", uart_iface),
            ("wasi:io/streams@0.3.0", uart_iface),
            ("wasi:io/streams", uart_iface),
            ("fireball://device/timer/0", timer_iface),
            ("wasi:clocks/monotonic-clock@0.3.0", timer_iface),
            ("wasi:clocks/monotonic-clock", timer_iface),
            ("wasi:cli/stdout@0.3.0", console_iface),
            ("wasi:cli/stdout", console_iface),
            ("fireball://device/gpio/0", gpio_iface),
            ("fireball:hal/gpio@0.3.0", gpio_iface),
            ("fireball:hal/gpio", gpio_iface),
            ("fireball://device/i2c/0", bus_iface),
            ("fireball://device/spi/0", bus_iface),
            ("fireball:hal/bus@0.3.0", bus_iface),
            ("fireball:hal/bus", bus_iface),
            ("fireball://service/logger/0", logger_iface),
        ]
        entries.sort(key=lambda e: e[0])
        self._interface_entries = tuple(entries)
        self._interfaces = FlatMapView(self._interface_entries)

    def get_interface(self, uri: str) -> WasiInterfaceVTable | None:
        """Resolves an interface descriptor by its Hierarchical IPC communication URI."""
        return self._interfaces.find(uri)

    def dispatch_command(self, uri: str, cmd_id: int, params: FlatMapView) -> Any:
        """
        Dispatches a WASI 0.3p IPC Driver Command to the resolved device
        interface. Matches platform_hal.md §5.1's `control(id, cmd, params:
        ipc-message)`: `params` is always a FlatMapView over packed kv_pair
        keys (ipc_router.md §3.3) -- one statically-typed argument, no
        runtime inspection of what was passed.
        """
        iface = self.get_interface(uri)
        if iface is None:
            return None

        def _get_val(key_packed: int, default: Any = None) -> Any:
            val = params.find(key_packed)
            return default if val is None else val

        # 0. Capability Query
        if cmd_id == WasiIpcCmd.QUERY_CAPS:
            target_cmd = _get_val(ARG_QUERY_CMD_ID, 0)
            if target_cmd == WasiIpcCmd.QUERY_CAPS:
                return 1
            # Check Stream Capabilities
            if target_cmd in (WasiIpcCmd.STREAM_WRITE_SHM, WasiIpcCmd.STREAM_READ_SHM):
                return 1 if (iface.write_shm is not None or iface.read_shm is not None) else 0
            # Check Clock Capabilities
            if target_cmd in (
                WasiIpcCmd.CLOCK_GET_NOW,
                WasiIpcCmd.CLOCK_SUBSCRIBE,
                WasiIpcCmd.CLOCK_GET_RES,
            ):
                return 1 if iface.get_now is not None else 0
            # Check GPIO Capabilities
            if target_cmd in (
                WasiIpcCmd.GPIO_SET_PIN,
                WasiIpcCmd.GPIO_GET_PIN,
                WasiIpcCmd.GPIO_CONFIG_PIN,
                WasiIpcCmd.GPIO_SUBSCRIBE_EDGE,
            ):
                return 1 if iface.set_pin is not None else 0
            # Check Bus Capabilities
            if target_cmd in (WasiIpcCmd.BUS_TRANSFER_SHM, WasiIpcCmd.BUS_CONFIG):
                return 1 if iface.transfer_shm is not None else 0
            # Check Poll Capabilities
            if target_cmd in (WasiIpcCmd.POLL_CHECK, WasiIpcCmd.POLL_WAIT):
                return 1
            return 0

        # 1. Stream Commands
        if cmd_id == WasiIpcCmd.STREAM_WRITE_SHM:
            if iface.write_shm is None:
                return 0
            task_id = _get_val(ARG_TASK_ID, 1)
            handle = _get_val(ARG_SHM_HANDLE)
            offset = _get_val(ARG_OFFSET, 0)
            length = _get_val(ARG_LENGTH, 0)
            return iface.write_shm(task_id, handle, offset, length)
        elif cmd_id == WasiIpcCmd.STREAM_READ_SHM:
            if iface.read_shm is None:
                return 0
            task_id = _get_val(ARG_TASK_ID, 1)
            handle = _get_val(ARG_SHM_HANDLE)
            offset = _get_val(ARG_OFFSET, 0)
            max_len = _get_val(ARG_MAX_LEN, 0)
            return iface.read_shm(task_id, handle, offset, max_len)
        elif cmd_id == WasiIpcCmd.STREAM_FLUSH:
            return iface.flush() if iface.flush is not None else 0
        elif cmd_id == WasiIpcCmd.STREAM_CLOSE:
            fd = _get_val(ARG_FD, 1)
            return iface.close(fd) if iface.close is not None else 0

        # 2. Clock / Timer Commands
        elif cmd_id == WasiIpcCmd.CLOCK_GET_NOW:
            return iface.get_now() if iface.get_now is not None else None
        elif cmd_id == WasiIpcCmd.CLOCK_SUBSCRIBE:
            nanos = _get_val(ARG_NANOS, 0)
            return iface.subscribe(nanos) if iface.subscribe is not None else None
        elif cmd_id == WasiIpcCmd.CLOCK_GET_RES:
            return iface.get_resolution() if iface.get_resolution is not None else None

        # 3. GPIO / Trigger Commands
        elif cmd_id == WasiIpcCmd.GPIO_SET_PIN:
            if iface.set_pin is None:
                return None
            return iface.set_pin(_get_val(ARG_PIN_NO, 0), _get_val(ARG_VAL, False))
        elif cmd_id == WasiIpcCmd.GPIO_GET_PIN:
            if iface.get_pin is None:
                return None
            return iface.get_pin(_get_val(ARG_PIN_NO, 0))
        elif cmd_id == WasiIpcCmd.GPIO_CONFIG_PIN:
            if iface.config_pin is None:
                return None
            return iface.config_pin(_get_val(ARG_PIN_NO, 0), _get_val(ARG_MODE, 0))
        elif cmd_id == WasiIpcCmd.GPIO_SUBSCRIBE_EDGE:
            if iface.subscribe_edge is None:
                return None
            return iface.subscribe_edge(_get_val(ARG_PIN_NO, 0), _get_val(ARG_EDGE_TYPE, 0))

        # 4. Bus Commands
        elif cmd_id == WasiIpcCmd.BUS_TRANSFER_SHM:
            if iface.transfer_shm is None:
                return None
            return iface.transfer_shm(
                _get_val(ARG_TX_SHM_HANDLE), _get_val(ARG_RX_SHM_HANDLE), _get_val(ARG_LENGTH, 0)
            )
        elif cmd_id == WasiIpcCmd.BUS_CONFIG:
            if iface.config is None:
                return None
            return iface.config(
                _get_val(ARG_CLOCK_HZ, 100_000), _get_val(ARG_SLAVE_ADDR, 0), _get_val(ARG_MODE, 0)
            )

        # 5. Poll Commands
        elif cmd_id == WasiIpcCmd.POLL_CHECK:
            return 1  # Ready
        elif cmd_id == WasiIpcCmd.POLL_WAIT:
            return 0  # Success

        return None

    def send_ipc_command(self, uri: str, cmd_id: int, params: FlatMapView) -> Any:
        """
        Sends an IPC Driver Command to the HAL Server Task via IPCRouter ({platform_hal.md}).
        HAL operates as a distinct task and communicates strictly over IPC rendezvous.
        """
        from hal import make_hal_ipc_message
        from ipc_router import Role

        # Ensure HAL task is spawned on the scheduler
        self.sysv.spawn_hal_task()

        msg = make_hal_ipc_message(cmd_id, params.entries, memory_manager=self.sysv.memory_manager)

        def sender_coro():
            yield from self.sysv.ipc.send(Role.RUNTIME, uri, msg)

        self.sysv.scheduler.spawn("wasi_ipc_sender", sender_coro())
        self.sysv.scheduler.run_until_idle()

        if self.sysv.hal_task is not None:
            return self.sysv.hal_task.last_result
        return None

    # Resource Methods
    def _stream_write(self, fd: int, data: bytes) -> int:
        if fd in (1, 2):
            self.sysv.transport.write(data)
            return len(data)
        return len(data)

    def _stream_read(self, fd: int, max_len: int) -> bytes:
        return b""

    def _stream_close(self, fd: int) -> int:
        return 0

    def _write_shm(self, task_id: int, handle: Any, offset: int, length: int) -> int:
        """Writes data from shared memory (FC=14) to device transport."""
        if not self.sysv.pool.can_view(task_id, handle, offset, length):
            return 0
        view = self.sysv.pool.view(task_id, handle, offset, length)
        self.sysv.transport.write(bytes(view))
        return len(view)

    def _read_shm(self, task_id: int, handle: Any, offset: int, max_len: int) -> int:
        """Reads data from device transport into shared memory (FC=14)."""
        return 0

    def _transfer_shm(self, tx_handle: Any, rx_handle: Any, length: int) -> int:
        """Transfers data between shared memory buffers via DMA/Bus."""
        return length

    def _clock_get_now(self) -> int:
        return time.monotonic_ns()

    def _console_write(self, data: bytes) -> int:
        self.sysv.transport.write(data)
        return len(data)


# ==============================================================================
# WASI 0.1p Compatibility Layer (Adapter Pattern wrapping WASI 0.3p)
# ==============================================================================
class WasiHostContext:
    """
    WASI Preview 1 Host Context and ABI Adapter.
    Transparently adapts wasi_snapshot_preview1 function calls to WASI 0.3p / HAL Core.
    """

    def __init__(self, sysv: System, guest_memory: bytearray | None = None, task_id: int = 1):
        self.sysv = sysv
        self.task_id = task_id
        self.guest_memory = guest_memory if guest_memory is not None else bytearray(64 * 1024)
        self.sysv.bind_guest(self.guest_memory, task_id=self.task_id)
        self.core03p = Wasi03pEngine(sysv)
        self._keepalive_trampolines: list[Any] = []

        # Build static host import table via RadixBinaryTreeView
        host_entries: list[tuple[str, str, Callable[..., int]]] = [
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
            ("fireball", "fireball_call", self.fireball_call),
            ("fireball", "fd_write", self.fd_write),
            ("env", "fireball_call", self.fireball_call),
            ("env", "fd_write", self.fd_write),
        ]
        hashed_entries: list[tuple[int, tuple[str, str, Callable[..., int]]]] = []
        for mod, field, handler in host_entries:
            h = fnv1a_32(f"{mod}::{field}")
            hashed_entries.append((h, (mod, field, handler)))

        hashed_entries.sort(key=lambda x: x[0])
        keys = [x[0] for x in hashed_entries]
        values = [x[1] for x in hashed_entries]
        radix_shift = 16
        if keys:
            max_prefix = max(keys) >> radix_shift
            radix_table = [0] * (max_prefix + 2)
            current_prefix = 0
            for idx, k in enumerate(keys):
                prefix = k >> radix_shift
                while current_prefix < prefix:
                    current_prefix += 1
                    radix_table[current_prefix] = idx
            while current_prefix <= max_prefix:
                current_prefix += 1
                radix_table[current_prefix] = len(keys)
        else:
            radix_table = [0]

        self._import_keys = list(keys)
        self._import_values = list(values)
        self._import_radix_table = list(radix_table)
        self._import_tree = RadixBinaryTreeView(
            self._import_keys,
            self._import_values,
            self._import_radix_table,
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
    # WASI 0.1p (Preview 1) Adapted Handlers (Delegating to WASI 0.3p Streams/Clocks)
    # --------------------------------------------------------------------------
    def fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        """
        Adapts wasi_snapshot_preview1:fd_write to WASI 0.3p wasi:io/streams:write.
        Every guest-memory offset used below is bounds-checked before use,
        so the only exception this can still raise is a genuine OS-socket
        failure (broken pipe / send timeout) surfacing from UartTransport --
        a real hardware-boundary condition, not a control-flow shortcut, so
        it's caught narrowly (OSError) rather than a blanket `except Exception`.
        """
        mem = self.guest_memory
        mem_len = len(mem)
        total_written = 0
        stream_iface = self.core03p.get_interface("wasi:io/streams")
        write_fn = stream_iface.write if stream_iface is not None else None

        try:
            for i in range(iovs_len):
                iov_offset = iovs_ptr + (i * 8)
                if iov_offset + 8 > mem_len:
                    return 21  # EFAULT
                base, length = struct.unpack_from("<II", mem, iov_offset)
                if base + length > mem_len:
                    return 21  # EFAULT
                buf = bytes(mem[base : base + length])
                if write_fn is not None:
                    total_written += write_fn(fd, buf)
                else:
                    self.sysv.transport.write(buf)
                    total_written += len(buf)
        except OSError:
            # Fallback to system call directly
            return int(
                self.sysv.fireball_call(
                    FbSyscallId.WASI_FD_WRITE, fd, iovs_ptr, iovs_len, nwritten_ptr, 0, 0
                )
            )

        if nwritten_ptr + 4 <= mem_len:
            struct.pack_into("<I", mem, nwritten_ptr, total_written)
        return 0  # SUCCESS

    def fd_read(self, fd: int, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        """Adapts wasi_snapshot_preview1:fd_read to WASI 0.3p wasi:io/streams:read."""
        return int(
            self.sysv.fireball_call(
                FbSyscallId.WASI_FD_READ, fd, iovs_ptr, iovs_len, nread_ptr, 0, 0
            )
        )

    def fd_close(self, fd: int) -> int:
        """Adapts wasi_snapshot_preview1:fd_close to WASI 0.3p wasi:io/streams:close."""
        return int(self.sysv.fireball_call(FbSyscallId.WASI_FD_CLOSE, fd, 0, 0, 0, 0, 0))

    def clock_time_get(self, clock_id: int, precision: int, time_ptr: int) -> int:
        """
        Adapts wasi_snapshot_preview1:clock_time_get to WASI 0.3p
        wasi:clocks:get-now. The write into guest memory is bounds-checked
        before use, and get-now never raises, so no exception can occur
        here -- no try/except needed (exceptions unavailable as control
        flow once disabled in the target C++ build).
        """
        mem = self.guest_memory
        clock_iface = self.core03p.get_interface("wasi:clocks/monotonic-clock")
        now_ns = clock_iface.get_now() if clock_iface is not None else time.monotonic_ns()
        if time_ptr + 8 <= len(mem):
            struct.pack_into("<Q", mem, time_ptr, now_ns)
        return 0

    def proc_exit(self, exit_code: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_PROC_EXIT, exit_code, 0, 0, 0, 0, 0))

    def random_get(self, buf_ptr: int, buf_len: int) -> int:
        return int(
            self.sysv.fireball_call(FbSyscallId.WASI_RANDOM_GET, buf_ptr, buf_len, 0, 0, 0, 0)
        )

    def fireball_call(
        self,
        sys_id: int,
        a0: int = 0,
        a1: int = 0,
        a2: int = 0,
        a3: int = 0,
        a4: int = 0,
        a5: int = 0,
    ) -> int:
        return int(self.sysv.fireball_call(sys_id, a0, a1, a2, a3, a4, a5))

    def get_handler_for_import(
        self, module_name: str, field_name: str
    ) -> Callable[..., int] | None:
        """Resolves an import name to the corresponding host function callable via RadixBinaryTreeView."""
        h = fnv1a_32(f"{module_name}::{field_name}")
        candidate = self._import_tree.find(h)
        if candidate is not None:
            mod, field, handler = candidate
            if mod == module_name and field == field_name:
                return handler
        return None

    def build_interpreter_host_functions(self, module: Module) -> list[Callable[..., int] | None]:
        """
        Maps all imported functions in the module to host function
        callables for the Interpreter. Import indices are 0..len(imports)-1
        by WASM encoding (dense, no gaps), so a fixed-size array indexed by
        that ordinal is the direct fit -- not a dict, which would imply a
        sparse/arbitrary key space this table never has.
        """
        host_funcs: list[Callable[..., int] | None] = [None] * len(module.imports)
        for idx, imp in enumerate(module.imports):
            host_funcs[idx] = self.get_handler_for_import(imp.module, imp.name)
        return host_funcs

    def build_jit_trampolines(self, module: Module) -> list[int | None]:
        """Creates ctypes CFUNCTYPE native trampolines for JIT execution."""
        trampolines: list[int | None] = [None] * len(module.imports)
        for idx, imp in enumerate(module.imports):
            handler = self.get_handler_for_import(imp.module, imp.name)
            if handler is None:
                continue
            ft = module.types[imp.type_index]
            nparams = len(ft.params)
            c_args = [ctypes.c_uint32] * nparams
            c_ret = ctypes.c_uint32  # WASI returns errno as u32
            c_func_type = ctypes.CFUNCTYPE(c_ret, *c_args)

            def make_wrapper(h: Callable[..., int], np: int):
                def wrapper(*args):
                    return h(*args[:np]) & 0xFFFF_FFFF

                return wrapper

            wrapped = make_wrapper(handler, nparams)
            t = c_func_type(wrapped)
            self._keepalive_trampolines.append(t)
            addr = ctypes.cast(t, ctypes.c_void_p).value
            assert addr is not None
            trampolines[idx] = addr
        return trampolines

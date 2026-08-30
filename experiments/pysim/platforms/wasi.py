"""
experiments/pysim/wasi.py
WASI Preview 1 and Fireball Host Interface Bridge for Guest WASM Execution.
Supports both Interpreter and JIT (ctypes native trampolines).
Implements docs/components/tier1_core/system_syscall.md §5.7 and interface_wit.md §5.5-5.6.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PYSIM_DIR = (
    Path(__file__).resolve().parents[1]
    if any(
        d in str(Path(__file__))
        for d in ("tests", "scenarios", "core", "runtime", "jit", "platforms")
    )
    else Path(__file__).resolve().parent
)

for _p in [
    _PYSIM_DIR,
    _PYSIM_DIR / "core",
    _PYSIM_DIR / "runtime",
    _PYSIM_DIR / "jit",
    _PYSIM_DIR / "platforms",
]:
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import ctypes
from collections.abc import Callable
from typing import Any

from loader import fnv1a_32
from system import FbSyscallId, System
from system_containers import RadixBinaryTreeView
from wasm_module import Module


class WasiHostContext:
    """Provides WASI Preview 1 and Fireball host functions for WASM guest execution."""

    def __init__(self, sysv: System, guest_memory: bytearray | None = None, task_id: int = 1):
        self.sysv = sysv
        self.task_id = task_id
        self.guest_memory = guest_memory if guest_memory is not None else bytearray(64 * 1024)
        self.sysv.bind_guest(self.guest_memory, task_id=self.task_id)
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

        self._import_tree = RadixBinaryTreeView(keys, values, radix_table, radix_shift=radix_shift)

    def fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        return int(
            self.sysv.fireball_call(
                FbSyscallId.WASI_FD_WRITE, fd, iovs_ptr, iovs_len, nwritten_ptr, 0, 0
            )
        )

    def fd_read(self, fd: int, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        return int(
            self.sysv.fireball_call(
                FbSyscallId.WASI_FD_READ, fd, iovs_ptr, iovs_len, nread_ptr, 0, 0
            )
        )

    def fd_close(self, fd: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_FD_CLOSE, fd, 0, 0, 0, 0, 0))

    def clock_time_get(self, clock_id: int, precision: int, time_ptr: int) -> int:
        return int(
            self.sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, clock_id, 0, time_ptr, 0, 0, 0)
        )

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

    def build_interpreter_host_functions(self, module: Module) -> dict[int, Callable[..., int]]:
        """Maps all imported functions in the module to host function callables for the Interpreter."""
        host_funcs: dict[int, Callable[..., int]] = {}
        for idx, imp in enumerate(module.imports):
            handler = self.get_handler_for_import(imp.module, imp.name)
            if handler is not None:
                host_funcs[idx] = handler
        return host_funcs

    def build_jit_trampolines(self, module: Module) -> dict[int, int]:
        """Creates ctypes CFUNCTYPE native trampolines for JIT execution."""
        trampolines: dict[int, int] = {}
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

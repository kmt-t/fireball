"""
experiments/pysim/wasi.py

WASI Preview 1 and Fireball Host Interface Bridge for Guest WASM Execution.
Supports both Interpreter and JIT (ctypes native trampolines).
Implements docs/components/tier1_core/system_syscall.md §5.7 and interface_wit.md §5.5-5.6.
"""

from __future__ import annotations

import ctypes
from typing import Any, Callable

from system import FbSyscallId, System, WasiErrno
from wasm_module import Module


class WasiHostContext:
    """Provides WASI Preview 1 and Fireball host functions for WASM guest execution."""

    def __init__(self, sysv: System, guest_memory: bytearray | None = None, task_id: int = 1):
        self.sysv = sysv
        self.task_id = task_id
        self.guest_memory = guest_memory if guest_memory is not None else bytearray(64 * 1024)
        self.sysv.bind_guest(self.guest_memory, task_id=self.task_id)
        self._keepalive_trampolines: list[Any] = []

    def fd_write(self, fd: int, iovs_ptr: int, iovs_len: int, nwritten_ptr: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_FD_WRITE, fd, iovs_ptr, iovs_len, nwritten_ptr, 0, 0))

    def fd_read(self, fd: int, iovs_ptr: int, iovs_len: int, nread_ptr: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_FD_READ, fd, iovs_ptr, iovs_len, nread_ptr, 0, 0))

    def fd_close(self, fd: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_FD_CLOSE, fd, 0, 0, 0, 0, 0))

    def clock_time_get(self, clock_id: int, precision: int, time_ptr: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_CLOCK_TIME_GET, clock_id, 0, time_ptr, 0, 0, 0))

    def proc_exit(self, exit_code: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_PROC_EXIT, exit_code, 0, 0, 0, 0, 0))

    def random_get(self, buf_ptr: int, buf_len: int) -> int:
        return int(self.sysv.fireball_call(FbSyscallId.WASI_RANDOM_GET, buf_ptr, buf_len, 0, 0, 0, 0))

    def fireball_call(self, sys_id: int, a0: int = 0, a1: int = 0, a2: int = 0, a3: int = 0, a4: int = 0, a5: int = 0) -> int:
        return int(self.sysv.fireball_call(sys_id, a0, a1, a2, a3, a4, a5))

    def get_handler_for_import(self, module_name: str, field_name: str) -> Callable[..., int] | None:
        """Resolves an import name to the corresponding host function callable."""
        if module_name in ("wasi_snapshot_preview1", "wasi_unstable"):
            if field_name == "fd_write":
                return self.fd_write
            if field_name == "fd_read":
                return self.fd_read
            if field_name == "fd_close":
                return self.fd_close
            if field_name == "clock_time_get":
                return self.clock_time_get
            if field_name == "proc_exit":
                return self.proc_exit
            if field_name == "random_get":
                return self.random_get
        elif module_name in ("fireball", "env"):
            if field_name == "fireball_call":
                return self.fireball_call
            if field_name == "fd_write":
                return self.fd_write
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
            c_ret = ctypes.c_uint32 if ft.results else ctypes.c_uint32  # WASI returns errno as u32
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

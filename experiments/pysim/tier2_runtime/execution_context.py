"""experiments/pysim/tier2_runtime/execution_context.py
Shared WASM execution context contract used by runtime and debugger components.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterator

from interop_abi import ExecutionContextNative, NativeValueStack
from jit_abi import (
    JIT_CONTEXT_SIZE_BYTES,
)


class WASMContext:
    """Execution context for hybrid Tiered Interpreter/JIT execution."""

    __slots__ = (
        "_c_context",
        "_c_mem",
        "_cached_locals_view",
        "_jit_helper_keepalive",
        "fault",
        "local_stack",
        "memory",
        "stack",
        "stack_capacity",
    )

    def __init__(
        self,
        memory: bytearray | None = None,
        stack_capacity: int = 64,
    ):
        n_locals = 16
        self.fault: str | None = None
        self.stack_capacity = stack_capacity
        self.stack: NativeValueStack = NativeValueStack(capacity=stack_capacity)
        self.local_stack: NativeValueStack = NativeValueStack(capacity=n_locals)
        self.local_stack.set_size(n_locals)
        self.memory = memory
        if memory is not None:
            self._c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
        else:
            self._c_mem = None
        # This is the Python mirror of wasm_interop.hxx. It is a
        # fixed-layout Native structure, not a Python object graph. Word 8 remains the
        # 64-bit process-local helper pointer used by PIC JIT delegation.
        self._c_context = ExecutionContextNative()
        assert ctypes.sizeof(self._c_context) == JIT_CONTEXT_SIZE_BYTES
        self._jit_helper_keepalive: object | None = None
        self._cached_locals_view = self._LocalsView(self)

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(ctypes.pointer(self._c_context), ctypes.c_void_p)

    @property
    def native_context(self) -> ExecutionContextNative:
        """Return the Native ABI record consumed by interpreter and JIT."""
        return self._c_context

    @property
    def sp_ptr(self) -> ctypes.c_void_p:
        return self.stack.value_ptr(len(self.stack))

    @property
    def locals_ptr(self) -> ctypes.c_void_p:
        return self.local_stack.value_ptr()

    @property
    def mem_ptr(self) -> ctypes.c_void_p:
        if self._c_mem is not None:
            return ctypes.c_void_p(ctypes.addressof(self._c_mem))
        return ctypes.c_void_p(0)

    @property
    def jit_helper_ptr(self) -> int:
        """Return the context-owned address used by a complex JIT tail jump."""

        return int(self._c_context.complex_helper_ptr)

    def set_jit_helper(self, helper_addr: int, keepalive: object | None = None) -> None:
        """Install a non-zero CPS helper address in the execution context.

        ``keepalive`` is only for the Python simulator (for example a
        ``ctypes`` callback); the embedded implementation owns its function
        image independently.  The generated JIT code reads this slot through
        ``ctx`` and therefore remains position independent.
        """

        assert helper_addr > 0, "JIT helper address must be non-zero"
        assert helper_addr <= 0xFFFF_FFFF_FFFF_FFFF, "JIT helper address exceeds ABI width"
        self._c_context.complex_helper_ptr = helper_addr
        self._jit_helper_keepalive = keepalive

    def clear_jit_helper(self) -> None:
        """Remove the installed helper and release the simulator keepalive."""

        self._c_context.complex_helper_ptr = 0
        self._jit_helper_keepalive = None

    class _LocalsView:
        __slots__ = ("_ctx",)

        def __init__(self, ctx: WASMContext):
            self._ctx = ctx

        def __getitem__(self, idx: int) -> int:
            return self._ctx.local_stack[idx]

        def __setitem__(self, idx: int, val: int) -> None:
            self._ctx.local_stack[idx] = val

        def __len__(self) -> int:
            return len(self._ctx.local_stack)

        def __iter__(self) -> Iterator[int]:
            yield from self._ctx.local_stack

    @property
    def locals(self) -> WASMContext._LocalsView:
        return self._cached_locals_view

    @locals.setter
    def locals(self, values: tuple[int, ...]) -> None:
        assert len(values) <= len(self.local_stack)
        for i, v in enumerate(values):
            self.local_stack[i] = v

    def push(self, val: int) -> bool:
        assert self.stack.push_back(val & 0xFFFF_FFFF)
        return True

    def pop(self) -> int:
        val = self.stack.pop_back()
        return val

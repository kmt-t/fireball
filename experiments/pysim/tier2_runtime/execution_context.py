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
        "_c_locals",
        "_c_mem",
        "_c_result",
        "_jit_helper_keepalive",
        "_cached_locals_view",
        "_n_locals",
        "fault",
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
        self._c_locals = (ctypes.c_int64 * n_locals)()

        self._n_locals = n_locals
        self.fault: str | None = None
        self.stack_capacity = stack_capacity
        self.stack: NativeValueStack = NativeValueStack(capacity=stack_capacity)
        self.memory = memory
        if memory is not None:
            self._c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
        else:
            self._c_mem = None
        # A trace's residual value is VM operand-stack state, not a C return
        # value ({ExecutionContext_Layout}), so SPILL_RESULT_TO_SP writes it
        # here via the CPS sp argument.
        self._c_result = ctypes.c_int64()
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
        return ctypes.cast(ctypes.pointer(self._c_result), ctypes.c_void_p)

    @property
    def locals_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(self._c_locals, ctypes.c_void_p)

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
            return self._ctx._c_locals[idx] & 0xFFFF_FFFF

        def __setitem__(self, idx: int, val: int) -> None:
            self._ctx._c_locals[idx] = val & 0xFFFF_FFFF

        def __len__(self) -> int:
            return self._ctx._n_locals

        def __iter__(self) -> Iterator[int]:
            for i in range(self._ctx._n_locals):
                yield self._ctx._c_locals[i] & 0xFFFF_FFFF

    @property
    def locals(self) -> WASMContext._LocalsView:
        return self._cached_locals_view

    @locals.setter
    def locals(self, values: tuple[int, ...]) -> None:
        if len(values) > self._n_locals:
            self.fault = "WASM_LOCAL_STACK_CAPACITY"
            return
        for i, v in enumerate(values):
            self._c_locals[i] = v & 0xFFFF_FFFF

    def push(self, val: int) -> bool:
        if not self.stack.push_back(val & 0xFFFF_FFFF):
            self.fault = "WASM_EXECUTION_STACK_OVERFLOW"
            return False
        return True

    def pop(self) -> int:
        val = self.stack.pop_back()
        if val is None:
            self.fault = "WASM_EXECUTION_STACK_UNDERFLOW"
            return 0
        return val

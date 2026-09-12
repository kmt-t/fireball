"""experiments/pysim/tier2_runtime/execution_context.py
Shared WASM execution context contract used by runtime and debugger components.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterator

from system_containers import StaticVector


class WASMContext:
    """Execution context for hybrid Tiered Interpreter/JIT execution."""

    __slots__ = (
        "_c_context",
        "_c_locals",
        "_c_mem",
        "_c_result",
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
        self.stack: StaticVector[int] = StaticVector(capacity=stack_capacity)
        self.memory = memory
        if memory is not None:
            self._c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
        else:
            self._c_mem = None
        # A trace's residual value is VM operand-stack state, not a C return
        # value ({ExecutionContext_Layout}), so SPILL_RESULT_TO_SP writes it
        # here via the CPS sp argument.
        self._c_result = ctypes.c_int64()
        self._c_context = (ctypes.c_uint32 * 15)()
        self._cached_locals_view = self._LocalsView(self)

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        return ctypes.cast(self._c_context, ctypes.c_void_p)

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

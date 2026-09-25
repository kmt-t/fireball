"""experiments/pysim/tier2_runtime/execution_context.py
Shared WASM execution context contract used by runtime and debugger components.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterator

from interop_abi import ExecutionContextNative, NativeValueStack
from jit_abi import JIT_CONTEXT_SIZE_BYTES


class WASMContext:
    """Execution context for hybrid Tiered Interpreter/JIT execution."""

    __slots__ = (
        "_c_context",
        "_c_mem",
        "_cached_locals_view",
        "fault",
        "local_slot_words",
        "local_stack",
        "memory",
        "stack",
        "stack_capacity",
    )

    def __init__(
        self,
        memory: bytearray | None = None,
        stack_capacity: int = 64,
        local_slot_words: int = 1,
    ):
        """`local_slot_words` is the local-slot stride (1: 4-byte slots, 2: 8-byte slots)."""
        assert local_slot_words == 1 or local_slot_words == 2
        n_locals = 16
        self.local_slot_words = local_slot_words
        self.fault: int | None = None
        self.stack_capacity = stack_capacity
        self.stack: NativeValueStack = NativeValueStack(capacity=stack_capacity)
        self.local_stack: NativeValueStack = NativeValueStack(capacity=n_locals * local_slot_words)
        self.local_stack.set_size(n_locals * local_slot_words)
        self.memory = memory
        if memory is not None:
            self._c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
        else:
            self._c_mem = None
        # This is the Python mirror of wasm_interop.hxx: a fixed-layout Native
        # structure, not a Python object graph.
        self._c_context = ExecutionContextNative()
        self._c_context.sp_capacity = self.stack.capacity
        assert ctypes.sizeof(self._c_context) == JIT_CONTEXT_SIZE_BYTES
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

    class _LocalsView:
        __slots__ = ("_ctx",)

        def __init__(self, ctx: WASMContext):
            self._ctx = ctx

        def __getitem__(self, idx: int) -> int:
            assert 0 <= idx < len(self)
            return self._ctx.local_stack[idx * self._ctx.local_slot_words]

        def __setitem__(self, idx: int, val: int) -> None:
            assert 0 <= idx < len(self)
            self._ctx.local_stack[idx * self._ctx.local_slot_words] = val

        def __len__(self) -> int:
            return len(self._ctx.local_stack) // self._ctx.local_slot_words

        def __iter__(self) -> Iterator[int]:
            for index in range(len(self)):
                yield self[index]

    @property
    def locals(self) -> WASMContext._LocalsView:
        return self._cached_locals_view

    @locals.setter
    def locals(self, values: tuple[int, ...]) -> None:
        assert len(values) <= len(self.locals)
        for i, v in enumerate(values):
            self.locals[i] = v

    def push(self, val: int) -> bool:
        pushed = self.stack.push_back(val & 0xFFFF_FFFF)
        assert pushed
        return True

    def pop(self) -> int:
        val = self.stack.pop_back()
        return val

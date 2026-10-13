"""
experiments/pysim/tier2_runtime/interpreter/interpreter.py
A minimal reference interpreter for the wasm_opcodes subset, used as the
correctness oracle the JIT's output is checked against -- mirroring the
real project's own "interpreter + JIT, cross-checked" architecture
(docs/components/tier2_runtime/interpreter.md /
docs/components/tier3_plugins/jit_compiler.md), just without the ARM/Copy-and-
Patch specifics.
Execution model: `docs/specs/wasm_instruction_set.md` §1 mandates a real
**threaded interpreter** (`{ThreadedInterpreter}`). Opcode handlers in
`handlers.py` receive the same `(ctx, ip*, sp*)` state and return an optional
trap. A return handler publishes the named `RETURN_SENTINEL_IP`.
`_HANDLERS` stores those functions directly; there is no CPS adapter or
central switch/if-elif loop around them. Numeric/immediate helpers and trap
definitions live in `utilities.py` and `errors.py`. The interpreter uses the
existing `tier2_runtime.runtime.python_api` contract for runtime operations.
The one adaptation from the literal ARM/native design: native code tail-
calls the next handler directly (or dispatches via a jump table with no
return address at all), which Python cannot do without unbounded
recursion depth for long-running loops. So the continuation
is *returned* rather than tail-called, and a small trampoline in `_step`
re-dispatches it -- indirect threading instead of direct threading, same
handler-per-opcode shape, no stack growth per WASM instruction.
`ExecutionContext` owns fixed-capacity operand, local, and call-frame storage
shared by interpreter, JIT, and debugger execution. Control delimiters,
branch targets, and operand restoration points are resolved at load time;
opcode execution does not create or search a runtime control-frame stack. A
`CallFrame` is an activation descriptor and a window into the shared storage;
it does not own a per-function stack. The descriptor also carries decoded
instruction metadata needed to resume at `ip`, without adding a fifth handler
argument.
"""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from bump_allocator import BumpAllocator
from config import (
    FB_CONF_INTERPRETER_YIELD_THRESHOLD,
    FB_CONF_MAX_NESTING_DEPTH,
    FB_CONF_MAX_VALUE_STACK,
)
from system_containers import SequenceView as Sequence
from system_containers import StaticVector
from tier2_runtime.abi import native_abi as _native_abi
from tier2_runtime.abi.interpreter_abi import (
    EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD,
    EXECUTION_CONTEXT_FLAG_STOP_AT_BLOCK_BOUNDARY,
    NATIVE_VALUE_STACK_CAPACITY,
    CallFrameNative,
    ExecutionContextABI,
    FunctionExecutionViewNative,
    ModuleExecutionViewNative,
    NativeGlobalStorage,
    NativeValueStack,
    TableExecutionViewNative,
    UInt32Pointer,
)
from tier2_runtime.abi.native_stack_abi import (
    NativeCallFrameStack,
    _LocalStackWindow,
)
from tier2_runtime.interpreter.errors import (
    LOG_EVT_TRAP_BASE,
    NATIVE_DISPATCH_CALL_BOUNDARY,
    NATIVE_DISPATCH_DEBUG_STOP,
    NATIVE_DISPATCH_RUNTIME_API_BOUNDARY,
    NATIVE_DISPATCH_YIELD,
    RETURN_SENTINEL_IP,
    RETURN_SENTINEL_PC,
    Trap,
    TrapCode,
)
from tier2_runtime.interpreter.errors import (
    TRAP_LOG_EVENTS as TRAP_LOG_EVENTS,
)
from tier2_runtime.interpreter.execution_plugin import NativeExecutionPlugin
from tier2_runtime.interpreter.handlers import _BASIC_BLOCK_BOUNDARY, _HANDLERS
from tier2_runtime.interpreter.utilities import I32_MASK, I64_MASK, _to_u32
from tier2_runtime.observability.logging_interface import LoggerPort, LogLevel
from tier2_runtime.runtime.python_api import (
    PAGE_SIZE,
    HostFunction,
    PythonRuntimeAPI,
    RuntimeAPI,
    WasmNumber,
)
from tier2_runtime.runtime.recovery import Result
from tier2_runtime.syscall.hostcall import VdmaTransfer
from tier2_runtime.vmmio.controller import VMMIOController
from tier2_runtime.wasm.leb128 import decode_unsigned
from tier2_runtime.wasm.module import (
    F32,
    F64,
    I64,
    FunctionTable,
    Memory,
    Module,
    value_slot_width,
)
from tier2_runtime.wasm.opcodes import (
    CALL,
    CALL_INDIRECT,
    FC_MEMORY_COPY,
    FC_MEMORY_FILL,
    FC_PREFIX,
    I32_LOAD,
    MEMORY_GROW,
)

NativeDispatchEntryPoint = _native_abi.NativeDispatchEntryPoint


WasmHostFunction = HostFunction


FB_CONF_MAX_LOCAL_STACK = NATIVE_VALUE_STACK_CAPACITY


def _encode_public_args(
    values: Sequence[WasmNumber], param_types: Sequence[int]
) -> StaticVector[int]:
    assert len(values) == len(param_types)
    raw_args: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
    for value, value_type in zip(values, param_types, strict=True):
        if value_type == I64:
            raw = int(value) & I64_MASK
            raw_args.append(raw & I32_MASK)
            raw_args.append(raw >> 32)
        elif value_type == F32:
            bits = struct.unpack("<I", struct.pack("<f", float(value)))[0]
            raw_args.append(bits)
        elif value_type == F64:
            bits = struct.unpack("<Q", struct.pack("<d", float(value)))[0]
            raw_args.append(bits & I32_MASK)
            raw_args.append(bits >> 32)
        else:
            raw_args.append(int(value) & I32_MASK)
    return raw_args


@dataclass(slots=True)
class ExecEnv:
    """
    R2 (`env`): state shared across every call in this run -- the
        module, linear memory, globals, tables and runtime API.
        Never mutated per-instruction-dispatch, only by the opcodes that are
        specified to mutate it (global.set, stores, memory.grow).
    """

    module: Module
    memory: bytearray | None
    globals: NativeGlobalStorage
    tables: StaticVector[FunctionTable]
    runtime_api: RuntimeAPI
    vmmio: VMMIOController | None = None
    phys_mem: bytearray | None = None
    vdma_transfer: VdmaTransfer | None = None


@contextmanager
def _native_linear_memory_scope(
    context: ExecutionContext, memory: bytearray | None
) -> Iterator[None]:
    """Publish the cached linear-memory address while native code is running."""
    memory_size = len(memory) if memory is not None else 0
    context.linear_memory_host_base = context.linear_memory_address(memory)
    context.linear_memory_size = memory_size
    try:
        yield
    finally:
        context.linear_memory_host_base = None
        context.linear_memory_size = 0


@dataclass(slots=True)
class InterpreterBindings:
    """Concrete resources supplied while instantiating one WASM module."""

    memory: bytearray
    memory_decl: Memory
    imported_memory: bool
    host_functions: StaticVector[WasmHostFunction | None]
    globals: StaticVector[int]
    tables: StaticVector[FunctionTable]
    vdma_transfer: VdmaTransfer | None = None

    @classmethod
    def empty(cls) -> InterpreterBindings:
        """Create the explicit empty binding set for a module with no imports."""
        return cls(
            memory=bytearray(),
            memory_decl=Memory(min_pages=0, max_pages=None),
            imported_memory=False,
            host_functions=StaticVector(capacity=0),
            globals=StaticVector(capacity=0),
            tables=StaticVector(capacity=0),
        )

    @classmethod
    def with_memory(cls, memory: bytearray) -> InterpreterBindings:
        """Create bindings with a concrete linear-memory instance only."""
        return cls(
            memory=memory,
            memory_decl=Memory(min_pages=0, max_pages=None),
            imported_memory=False,
            host_functions=StaticVector(capacity=0),
            globals=StaticVector(capacity=0),
            tables=StaticVector(capacity=0),
        )

    @classmethod
    def with_memory_and_functions(
        cls,
        memory: bytearray,
        host_functions: StaticVector[WasmHostFunction | None],
        *,
        vdma_transfer: VdmaTransfer | None = None,
    ) -> InterpreterBindings:
        """Create bindings for a host memory and dense function-import table."""
        return cls(
            memory=memory,
            memory_decl=Memory(min_pages=0, max_pages=None),
            imported_memory=False,
            host_functions=host_functions,
            globals=StaticVector(capacity=0),
            tables=StaticVector(capacity=0),
            vdma_transfer=vdma_transfer,
        )


class CallFrameStack:
    """Python identity view backed by the native fixed-capacity call stack."""

    __slots__ = ("_frames", "_native")

    def __init__(self, native: NativeCallFrameStack, capacity: int):
        self._native = native
        self._frames: StaticVector[CallFrame] = StaticVector(capacity=capacity)

    def __len__(self) -> int:
        assert len(self._frames) == len(self._native)
        return len(self._frames)

    def __bool__(self) -> bool:
        return len(self) != 0

    def __getitem__(self, index: int) -> CallFrame:
        assert len(self._frames) == len(self._native)
        frame = self._frames[index]
        assert frame is not None
        return frame

    def push_back(self, frame: CallFrame) -> bool:
        assert len(self._frames) == len(self._native)
        native_index = len(self._native)
        if not self._native.push_back(frame.native):
            return False
        frame._native_slot = native_index
        if not self._frames.push_back(frame):
            self._native.pop_back()
            frame._native_slot = -1
            return False
        frame._native = None
        return True

    def pop_back(self) -> CallFrame:
        assert len(self._frames) == len(self._native)
        frame = self._frames.pop_back()
        assert frame is not None
        assert frame.native.func_index == self._native[-1].func_index
        self._native.pop_back()
        frame._native_slot = -1
        frame._native = None
        return frame

    def sync_from_native(self, context: ExecutionContext, env: ExecEnv) -> None:
        """Reconcile Python wrappers with the Native stack without copying records."""
        adapter = context.module_execution
        assert adapter is not None
        common = 0
        common_limit = min(len(self._frames), len(self._native))
        while common < common_limit:
            native_frame = self._native[common]
            frame = self._frames[common]
            native_local_base = int(native_frame.local_base or 0)
            native_local_offset = (native_local_base - context.local_stack.address) // 4
            if (
                frame._native_slot != common
                or frame.func_index != native_frame.func_index
                or frame.frame_offset != native_local_offset
            ):
                break
            common += 1
        while len(self._frames) > common:
            frame = self._frames.pop_back()
            assert frame is not None
            frame._native_slot = -1
            frame._native = None
        assert len(self._native) <= self._frames.capacity
        while len(self._frames) < len(self._native):
            index = len(self._frames)
            native_frame = self._native[index]
            template = adapter.template(native_frame.func_index)
            native_local_base = int(native_frame.local_base or 0)
            local_offset = (native_local_base - context.local_stack.address) // 4
            frame = CallFrame(
                context,
                native_frame.func_index,
                local_offset,
                env,
                template=template,
                native_slot=index,
            )
            self._frames.append(frame)
        local_stack_pointer = int(context.local_stack_pointer or 0)
        assert local_stack_pointer >= context.local_stack.address
        local_words = (local_stack_pointer - context.local_stack.address) // 4
        context.local_stack.set_size(local_words)


class ExecutionContext(ExecutionContextABI):
    """Single Python/native execution context and its owned fixed-capacity stacks."""

    _fields_ = (("debug_control", ctypes.c_void_p),)

    __slots__ = (
        "_allocator",
        "_arena_offset",
        "_arena_size",
        "_context_ptr",
        "_context_view",
        "_ip_pointer",
        "_linear_memory_address",
        "_linear_memory_owner",
        "_linear_memory_size",
        "_native_dispatch_call",
        "_native_result",
        "_native_step_call",
        "_workspace_released",
        "call_frame_stack",
        "local_stack",
        "module",
        "module_execution",
        "native_call_frames",
        "stack",
    )

    def __init__(
        self,
        module: Module | None = None,
        *,
        debug_control: int | None = None,
        allocator: BumpAllocator | None = None,
    ):
        """Own the ABI record and fixed-capacity stacks used by native execution."""
        super().__init__()
        self.stack: NativeValueStack = NativeValueStack(capacity=FB_CONF_MAX_VALUE_STACK)
        self.local_stack: NativeValueStack = NativeValueStack(capacity=FB_CONF_MAX_LOCAL_STACK)
        self.debug_control = debug_control
        self.current_block_index = 0xFFFF
        self._context_ptr = ctypes.cast(ctypes.pointer(self), ctypes.c_void_p)
        self._context_view = memoryview(self)
        self._ip_pointer = ctypes.cast(
            ctypes.byref(self, ExecutionContextABI.ip.offset),
            ctypes.POINTER(ctypes.c_uint32),
        )
        assert ctypes.addressof(self._ip_pointer.contents) == (
            ctypes.addressof(self) + ExecutionContextABI.ip.offset
        )
        self._linear_memory_address = 0
        self._linear_memory_owner: bytearray | None = None
        self._linear_memory_size = -1
        self._native_step_call = _native_abi.NativeStepCall()
        self._native_dispatch_call = _native_abi.NativeDispatchCall()
        self._native_result = _native_abi.NativeResult()
        self.module = module
        self.module_execution: NativeModuleExecution | None = None
        self.native_call_frames = NativeCallFrameStack(FB_CONF_MAX_NESTING_DEPTH)
        self.call_frame_stack = CallFrameStack(
            self.native_call_frames,
            FB_CONF_MAX_NESTING_DEPTH,
        )
        self.call_stack = self.native_call_frames.address
        self.call_frame_base = self.native_call_frames.frame_address(0)
        self.stack_base = self.stack.address
        self.stack_pointer = self.stack.value_ptr(len(self.stack)).value
        self.stack_limit = self.stack.address + self.stack.capacity * 4
        self.local_stack_base = self.local_stack.address
        self.local_stack_pointer = self.local_stack.value_ptr(0).value
        self.local_stack_limit = self.local_stack.address + self.local_stack.capacity * 4
        self.local_base = self.local_stack_base
        self._allocator = allocator
        self._arena_offset: int | None = None
        self._arena_size = (
            ctypes.sizeof(self)
            + ctypes.sizeof(self.stack.native)
            + ctypes.sizeof(self.local_stack.native)
            + ctypes.sizeof(self.native_call_frames.native)
            + ctypes.sizeof(self._native_step_call)
            + ctypes.sizeof(self._native_dispatch_call)
            + ctypes.sizeof(self._native_result)
        )
        self._workspace_released = False
        if allocator is not None:
            self._arena_offset = allocator.acquire(self._arena_size, alignment=8)

    @property
    def arena_offset(self) -> int | None:
        return self._arena_offset

    @property
    def arena_size(self) -> int:
        return self._arena_size

    def release_workspace(self) -> None:
        """Return the bounded native stacks for reuse after a call completes."""

        if self._allocator is None or self._workspace_released:
            return
        assert self._arena_offset is not None
        self._allocator.release(self._arena_offset, self._arena_size, alignment=8)
        self._workspace_released = True

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        """Address of this context's own ABI storage."""
        return self._context_ptr

    @property
    def ip_ptr(self) -> UInt32Pointer:
        """Pointer to the instruction cursor stored in this execution context."""
        return self._ip_pointer

    @property
    def context_view(self) -> memoryview:
        """Cached byte view of this context's own ABI storage."""
        return self._context_view

    @property
    def sp_ptr(self) -> ctypes.c_void_p:
        return self.stack.value_ptr(len(self.stack))

    @property
    def locals_ptr(self) -> ctypes.c_void_p:
        return self.local_stack.value_ptr()

    def attach_module_execution(self, execution: NativeModuleExecution) -> None:
        assert self.module_execution is None
        self.module_execution = execution

    def linear_memory_address(self, memory: bytearray | None) -> int:
        """Return a cached address, refreshing it only when memory grows or changes."""
        memory_size = len(memory) if memory is not None else 0
        if memory is None or memory_size == 0:
            self._linear_memory_owner = memory
            self._linear_memory_size = memory_size
            self._linear_memory_address = 0
            return 0
        if self._linear_memory_owner is not memory or self._linear_memory_size != memory_size:
            buffer_type = ctypes.c_ubyte * memory_size
            memory_buffer = buffer_type.from_buffer(memory)
            self._linear_memory_address = ctypes.addressof(memory_buffer)
            self._linear_memory_owner = memory
            self._linear_memory_size = memory_size
            del memory_buffer
        return self._linear_memory_address

    def begin_call_frame(
        self,
        raw_args: Sequence[int],
        func_index: int,
        env: ExecEnv,
    ) -> Result[CallFrame, Trap]:
        """Push one frame's locals directly into the context-owned Native stack."""
        frame_offset = len(self.local_stack)
        template = None
        if self.module_execution is not None:
            template = self.module_execution.template(func_index)
        frame = CallFrame(
            self,
            func_index=func_index,
            frame_offset=frame_offset,
            env=env,
            template=template,
        )
        local_slot_count = frame.local_slot_count
        assert len(raw_args) == frame.param_packed_slot_count
        if frame_offset + local_slot_count > self.local_stack.capacity:
            return Result.err(Trap(TrapCode.LOCAL_STACK_CAPACITY))
        if not self.call_frame_stack.push_back(frame):
            return Result.err(Trap(TrapCode.CALL_FRAME_CAPACITY))
        if not self.local_stack.push_zeroed(local_slot_count):
            self.call_frame_stack.pop_back()
            return Result.err(Trap(TrapCode.LOCAL_STACK_CAPACITY))
        assert self.module is not None
        function = self.module.functions[func_index - len(self.module.imports)]
        self.current_block_index = function.entry_block_index
        raw_offset = 0
        for index in range(frame.param_count):
            width = frame.local_layout.size_words(index)
            local_offset = frame_offset + frame.local_layout.offset(index)
            for word in range(width):
                self.local_stack.write_raw_at(local_offset + word, raw_args[raw_offset])
                raw_offset += 1
        assert raw_offset == len(raw_args)
        self.local_stack_pointer = self.local_stack.value_ptr(len(self.local_stack)).value
        self.local_base = self.local_stack.value_ptr(frame_offset).value
        return Result.ok(frame)

    def end_call_frame(self, frame: CallFrame) -> None:
        """Pop the active frame and its locals from the context stacks."""
        assert self.call_frame_stack
        assert self.call_frame_stack[-1] is frame
        self.local_stack.truncate(frame.frame_offset)
        self.call_frame_stack.pop_back()
        self.local_stack_pointer = self.local_stack.value_ptr(len(self.local_stack)).value
        self.local_base = (
            self.local_stack.value_ptr(self.call_frame_stack[-1].frame_offset).value
            if self.call_frame_stack
            else self.local_stack_base
        )


assert ExecutionContext.debug_control.offset == ctypes.sizeof(ExecutionContextABI)
assert ctypes.sizeof(ExecutionContext) == ctypes.sizeof(ExecutionContextABI) + ctypes.sizeof(
    ctypes.c_void_p
)


def _native_buffer_address(view: memoryview) -> int:
    """Borrow the address using the shared CPython buffer ABI definition."""
    if view.nbytes == 0:
        return 0
    lease = _native_abi.BufferLease(view)
    try:
        return lease.address
    finally:
        lease.release()


class CallFrame:
    """
    Activation descriptor for one function. `values` and `locals` are windows
    into `ExecutionContext` stacks; branch metadata is resolved at load time.
    """

    __slots__ = (
        "_locals",
        "_native",
        "_native_function_view",
        "_native_interpreter_annotations",
        "_native_slot",
        "code",
        "code_pc_offset",
        "context",
        "control_map",
        "env",
        "frame_offset",
        "local_count",
        "local_layout",
        "local_slot_count",
        "local_types",
        "param_count",
        "param_packed_slot_count",
        "result_arity",
        "values",
    )

    def __init__(
        self,
        context: ExecutionContext,
        func_index: int,
        frame_offset: int,
        env: ExecEnv,
        *,
        template: CallFrame | None = None,
        native_function_view: FunctionExecutionViewNative | None = None,
        native_slot: int = -1,
    ):
        module = context.module
        assert module is not None
        assert env is not None
        assert not module.is_import(func_index)
        assert native_slot == -1 or 0 <= native_slot < len(context.native_call_frames)
        self._native_slot = -1
        native_frame = context.native_call_frames[native_slot] if native_slot >= 0 else None
        function = module.functions[func_index - len(module.imports)]
        local_layout = function.local_layout_cache
        assert local_layout is not None
        self.context = context
        self.values = context.stack
        self.frame_offset = frame_offset
        self.local_count = len(local_layout)
        self.local_layout = local_layout
        local_slot_count = function.local_slot_count_cache
        param_count = function.param_count_cache
        param_packed_slot_count = function.param_packed_slot_count_cache
        result_arity = function.result_arity_cache
        assert local_slot_count is not None
        assert param_count is not None
        assert param_packed_slot_count is not None
        assert result_arity is not None
        self.local_slot_count = local_slot_count
        self.param_count = param_count
        self.param_packed_slot_count = param_packed_slot_count
        self.result_arity = result_arity
        assert self.result_arity <= 2, "multi-value function results exceed native return width"
        self._locals = _LocalStackWindow(
            context.local_stack,
            frame_offset,
            local_layout,
        )
        self.code = module.code_for(func_index)
        self.code_pc_offset = module.function_pc_offset(func_index)
        assert function.control_map is not None
        self.control_map = function.control_map
        if template is None:
            annotations = function.interpreter_annotation_pool
            assert annotations is not None
            self._native_interpreter_annotations = annotations
            function_view = native_function_view
            if function_view is None:
                function_view = FunctionExecutionViewNative()
            function_view.code = _native_buffer_address(self.code)
            function_view.code_size = len(self.code)
            function_view.code_pc_offset = self.code_pc_offset
            function_view.local_offsets = self.local_layout.offsets_address
            function_view.local_types = self.local_layout.types_address
            function_view.local_count = self.local_count
            function_view.local_slot_count = self.local_slot_count
            function_view.param_count = self.param_count
            function_view.param_packed_slot_count = self.param_packed_slot_count
            function_view.result_arity = self.result_arity
            function_view.function_index = func_index
            function_view.type_index = function.type_index
            function_view.is_import = 0
            function_view.module_view = 0
            function_view.interpreter_annotation_pool = annotations.native_address
            function_view.interpreter_annotation_byte_count = len(annotations)
            function_view.max_operand_stack_words = function.max_operand_stack_words
            function_view.entry_block_index = function.entry_block_index
            self._native_function_view = function_view
        else:
            assert native_function_view is None
            assert template.func_index == func_index
            self._native_interpreter_annotations = template._native_interpreter_annotations
            self._native_function_view = template._native_function_view
        self.env = env
        # Boundary PCs are retained for the scheduler contract. Branch edges
        # and stack restoration are loader-resolved in ControlMap.
        execution = context.module_execution
        function_view_address = (
            execution.function_view_address(func_index)
            if execution is not None
            else ctypes.addressof(self._native_function_view)
        )
        if native_frame is None:
            self._native = CallFrameNative(
                function_view=function_view_address,
                local_base=context.local_stack.value_ptr(self.frame_offset).value,
                operand_stack_base=context.stack.value_ptr(len(context.stack)).value,
                return_ip=0xFFFF_FFFF,
                boundary_next_pc=0xFFFF_FFFF,
                boundary_loops_to=0xFFFF_FFFF,
                return_block_index=0xFFFF,
            )
            assert native_slot == -1
        else:
            assert native_slot >= 0
            assert native_frame.func_index == func_index
            assert native_frame.function_view == function_view_address
            assert native_frame.local_base == context.local_stack.value_ptr(frame_offset).value
            self._native = None
            self._native_slot = native_slot

    @property
    def context_ptr(self) -> ctypes.c_void_p:
        return self.context.context_ptr

    @property
    def func_index(self) -> int:
        """Read function identity from the shared immutable function view."""
        return int(self._native_function_view.function_index)

    @property
    def native(self) -> CallFrameNative:
        """Return the flat native activation descriptor stored on the call stack."""
        if self._native_slot >= 0:
            return self.context.native_call_frames[self._native_slot]
        assert self._native is not None, "inactive call frame has no Native descriptor"
        return self._native

    @property
    def boundary_next_pc(self) -> int | None:
        value = int(self.native.boundary_next_pc)
        return None if value == 0xFFFF_FFFF else value

    @boundary_next_pc.setter
    def boundary_next_pc(self, value: int | None) -> None:
        self.native.boundary_next_pc = 0xFFFF_FFFF if value is None else value

    @property
    def boundary_loops_to(self) -> int | None:
        value = int(self.native.boundary_loops_to)
        return None if value == 0xFFFF_FFFF else value

    @boundary_loops_to.setter
    def boundary_loops_to(self, value: int | None) -> None:
        self.native.boundary_loops_to = 0xFFFF_FFFF if value is None else value

    @property
    def locals(self) -> _LocalStackWindow:
        return self._locals

    def set_runtime_boundary(self, next_pc: int | None, loops_to: int | None) -> None:
        """Publish the current block exit targets to the native handlers."""
        self.boundary_next_pc = next_pc
        self.boundary_loops_to = loops_to
        assert self.context.call_frame_stack
        assert self.context.call_frame_stack[-1] is self
        assert self.native.func_index == self.func_index


class NativeModuleExecution:
    """Own immutable module descriptors and borrow stable table buffers for C++."""

    __slots__ = (
        "_block_execution_counts",
        "_function_views",
        "_function_views_address",
        "_module_view",
        "_signature_address",
        "_table_views",
        "_templates",
        "bump_allocator",
        "globals",
        "jit_provider",
        "module",
        "tables",
    )

    def __init__(
        self,
        template_context: ExecutionContext,
        module: Module,
        env: ExecEnv,
        tables: Sequence[FunctionTable],
        allocator: BumpAllocator | None = None,
    ):
        if module.block_storage is None:
            module.build_basic_block_index(allocator)
        self.jit_provider = 0
        self.bump_allocator = allocator
        self.module = module
        self.tables = tables
        self.globals = env.globals
        function_count = len(module.imports) + len(module.functions)
        self._function_views = (FunctionExecutionViewNative * function_count)()
        self._function_views_address = (
            0 if function_count == 0 else ctypes.addressof(self._function_views)
        )
        for import_index in range(len(module.imports)):
            import_record = module.imports[import_index]
            function_type = module.func_type(import_index)
            assert function_type.params is not None and function_type.results is not None
            view = self._function_views[import_index]
            view.code = 0
            view.code_size = 0
            view.code_pc_offset = 0
            view.local_offsets = 0
            view.local_types = 0
            view.local_count = 0
            view.local_slot_count = 0
            view.function_index = import_index
            view.param_count = len(function_type.params)
            view.param_packed_slot_count = sum(
                value_slot_width(value_type) for value_type in function_type.params
            )
            view.result_arity = sum(
                value_slot_width(value_type) for value_type in function_type.results
            )
            view.type_index = import_record.type_index
            view.is_import = 1
            view.interpreter_annotation_pool = 0
            view.interpreter_annotation_byte_count = 0
            view.max_operand_stack_words = 0
            view.entry_block_index = 0xFFFF

        self._templates: StaticVector[CallFrame] = StaticVector(capacity=len(module.functions))
        for local_index in range(len(module.functions)):
            function_index = len(module.imports) + local_index
            function = module.functions[local_index]
            assert function.control_map is not None
            template = CallFrame(
                template_context,
                function_index,
                0,
                env,
                native_function_view=self._function_views[function_index],
            )
            self._templates.append(template)

        type_count = len(module.types)
        assert module.source is not None
        self._signature_address = _native_buffer_address(module.source)

        global_count = len(module.globals)
        module.build_native_global_widths(allocator)

        self._table_views = (TableExecutionViewNative * len(tables))()
        for table_index, table in enumerate(tables):
            descriptor = self._table_views[table_index]
            descriptor.function_indices = table.native_address
            descriptor.size = len(table)
        self._block_execution_counts = None
        self._module_view = ModuleExecutionViewNative(
            functions=self._function_views_address,
            function_count=function_count,
            imported_function_count=len(module.imports),
            types=module.types.native_address,
            type_count=type_count,
            signature_bytes=self._signature_address,
            tables=(0 if len(tables) == 0 else ctypes.addressof(self._table_views)),
            table_count=len(tables),
            globals=self.globals.native_address,
            global_widths=module.global_widths_address,
            global_count=global_count,
            blocks=module.blocks.native_address,
            block_count=len(module.blocks),
            block_execution_counts=0,
        )
        module_view_address = ctypes.addressof(self._module_view)
        for function_index in range(function_count):
            self._function_views[function_index].module_view = module_view_address
        arena_layout = (
            (
                ctypes.sizeof(self._function_views),
                ctypes.alignment(FunctionExecutionViewNative),
            ),
            (ctypes.sizeof(self._table_views), ctypes.alignment(TableExecutionViewNative)),
            (ctypes.sizeof(self._module_view), ctypes.alignment(ModuleExecutionViewNative)),
        )
        if allocator is not None:
            for size, alignment in arena_layout:
                allocator.allocate(size, alignment)

    def template(self, func_index: int) -> CallFrame:
        local_index = func_index - len(self.module.imports)
        assert 0 <= local_index < len(self._templates)
        return self._templates[local_index]

    def function_view_address(self, func_index: int) -> int:
        assert 0 <= func_index < len(self.module.imports) + len(self.module.functions)
        return self._function_views_address + func_index * ctypes.sizeof(
            FunctionExecutionViewNative
        )

    def enable_block_execution_counters(self) -> None:
        """Attach one bounded counter to each shared module block view."""

        if self._block_execution_counts is None:
            self._block_execution_counts = (ctypes.c_uint32 * len(self.module.blocks))()
            self._module_view.block_execution_counts = (
                0
                if len(self.module.blocks) == 0
                else ctypes.addressof(self._block_execution_counts)
            )


# The interpreter call's resumable continuation: (next_ip, frame, local_base).
# `next_ip == RETURN_SENTINEL_IP` is the explicit return boundary; `cont=None`
# is reserved for a fully finished or trapped call. Operand values live only
# in the Native stack.
_ActiveCont = tuple[int, CallFrame, _LocalStackWindow]
_Cont = _ActiveCont | None


class DebuggerAttachment(Protocol):
    halted: bool
    stop_signal: int


class InterpreterCall:
    """
    Resumable state for one in-progress `Interpreter.call()`, stepped by
    `Interpreter.step()`. Crosses the interpreter/runtime boundary as plain
    data -- never as a Python coroutine -- so a COOS scheduler can suspend and
    resume the Interpreter without taking ownership of its execution engine.

    `call_stack` holds every caller frame currently suspended on a WASM
    `call`/`call_indirect` that has not yet returned, as `(func_index,
    resume_cont)` pairs, deepest-caller-last. Entering or returning from a
    nested WASM call is always a mandatory `step()` boundary: the interpreter hands the callee's freshly-entered frame
    straight back to the runtime rather than running it itself, so a JIT
    trace cache gets exactly the same chance to intercept a nested call as
    it gets for the outermost one -- tiering never depends on call depth.
    """

    __slots__ = (
        "_frame",
        "_ip",
        "_locals",
        "call_stack",
        "context",
        "finished",
        "func_index",
        "results",
        "trap",
    )

    def __init__(
        self,
        func_index: int,
        context: ExecutionContext,
        cont: _Cont,
        finished: bool = False,
        results: StaticVector[WasmNumber] | None = None,
        trap: Trap | None = None,
    ):
        self.func_index = func_index
        self.context = context
        self._ip = 0
        self._frame: CallFrame | None = None
        self._locals: _LocalStackWindow | None = None
        self.call_stack: StaticVector[tuple[int, _ActiveCont]] = StaticVector(
            capacity=FB_CONF_MAX_NESTING_DEPTH
        )
        self.finished = finished
        self.results = results
        self.trap = trap
        self._set_cont(cont)

    def _set_cont(self, value: _Cont) -> None:
        if value is None:
            self._ip = 0
            self._frame = None
            self._locals = None
            return
        ip, frame, local_base = value
        self._ip = ip
        self._frame = frame
        self._locals = local_base

    def current_pc(self) -> int:
        """
        Return this call's Code-section-relative instruction address.

        The return sentinel is exposed as its reserved unified PC so the
        runtime can recognize it without consulting bytecode. It is never a
        valid BasicBlock lookup key. A missing continuation is not an address.
        """
        frame = self._frame
        assert frame is not None
        ip = self._ip
        if ip == RETURN_SENTINEL_IP:
            return RETURN_SENTINEL_PC
        assert 0 <= ip < len(frame.code)
        return frame.code_pc_offset + ip


@dataclass(frozen=True, slots=True)
class NativeInterpreterBoundary:
    """One native dispatch boundary returned to the Runtime scheduler."""

    call_state: InterpreterCall
    yield_requested: bool = False


class Interpreter:
    __slots__ = (
        "_env",
        "_native_dispatcher",
        "bump_allocator",
        "debugger",
        "globals",
        "initialization_trap",
        "logger",
        "memory",
        "memory_decl",
        "module",
        "phys_mem",
        "tables",
        "vmmio",
        "yield_threshold",
    )

    def __init__(
        self,
        module: Module,
        bindings: InterpreterBindings,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: LoggerPort | None = None,
        *,
        bump_allocator: BumpAllocator | None = None,
        native_dispatcher: NativeDispatchEntryPoint = _native_abi.RUN_DISPATCH,
        yield_threshold: int = FB_CONF_INTERPRETER_YIELD_THRESHOLD,
    ):
        self._native_dispatcher = native_dispatcher
        assert 1 <= yield_threshold <= 0xFFFF_FFFF
        self.yield_threshold = yield_threshold
        self.module = module
        self.initialization_trap: Trap | None = None
        self.bump_allocator = (
            bump_allocator if bump_allocator is not None else module.allocator
        ) or BumpAllocator()
        self.module.relocate_to(self.bump_allocator)
        self.logger = logger
        self.memory = bindings.memory
        if module.memory_import is not None:
            assert bindings.imported_memory
            assert len(bindings.memory) >= module.memory_import.min_limit * PAGE_SIZE
            assert bindings.memory_decl.min_pages >= module.memory_import.min_limit
            if module.memory_import.max_limit is not None:
                assert bindings.memory_decl.max_pages is not None
                assert bindings.memory_decl.max_pages <= module.memory_import.max_limit
            self.memory_decl = bindings.memory_decl
        else:
            assert not bindings.imported_memory
            self.memory_decl = module.memory if module.memory is not None else bindings.memory_decl
            if module.memory is not None:
                assert len(bindings.memory) >= module.memory.min_pages * PAGE_SIZE

        assert len(bindings.host_functions) == len(module.imports)
        self.globals = NativeGlobalStorage(
            capacity=len(module.globals), allocator=self.bump_allocator
        )
        global_import_index = 0
        assert len(bindings.globals) == module.global_import_count
        for global_value in module.globals:
            if global_value.imported:
                self.globals.append(bindings.globals[global_import_index])
                global_import_index += 1
            elif global_value.init_global_index is not None:
                assert global_value.init_global_index < len(self.globals)
                self.globals.append(self.globals[global_value.init_global_index])
            else:
                self.globals.append(global_value.init_value)
        self.tables: StaticVector[FunctionTable] = StaticVector(capacity=len(module.tables))
        table_import_index = 0
        assert len(bindings.tables) == module.table_import_count
        for table_index in range(len(module.tables)):
            table = module.tables[table_index]
            if table.imported:
                external_table = bindings.tables[table_import_index]
                table_import_index += 1
                self.tables.append(
                    module.table_contents(
                        table_index,
                        self.globals,
                        external_table,
                        allocator=self.bump_allocator,
                    )
                )
            else:
                self.tables.append(
                    module.table_contents(table_index, self.globals, allocator=self.bump_allocator)
                )
        function_count = len(module.imports) + len(module.functions)
        for table in self.tables:
            assert all(index is None or 0 <= index < function_count for index in table)
            table.seal()
        self.debugger: DebuggerAttachment | None = None
        self.vmmio = vmmio
        self.phys_mem = phys_mem
        self._env = ExecEnv(
            module,
            bindings.memory,
            self.globals,
            self.tables,
            PythonRuntimeAPI(
                module,
                bindings.memory,
                self.memory_decl,
                bindings.host_functions,
            ),
            vmmio=vmmio,
            phys_mem=phys_mem,
            vdma_transfer=bindings.vdma_transfer,
        )
        if self.module.start_function is not None:
            outcome = self.call(self.module.start_function, ())
            self.initialization_trap = outcome.error

    def attach_debugger(self, debugger: DebuggerAttachment) -> None:
        """
        Records the attached debugger. Unlike the legacy block harness's
        {DebuggerInterpreterComposition}, the threaded interpreter's `_HANDLERS`
        dispatch table is a single fixed table with no separate debug/normal
        variant to switch between -- breakpoint/step behavior for
        interpreter-only execution is driven by DebuggerManager itself.
        """
        self.debugger = debugger

    def detach_debugger(self) -> None:
        self.debugger = None

    def call(
        self, func_index: int, args: Sequence[WasmNumber]
    ) -> Result[StaticVector[WasmNumber], Trap]:
        """Runs a function to completion in one call."""
        call_state = self.start(func_index, args)
        return self._complete_call(call_state)

    def _complete_call(self, call_state: InterpreterCall) -> Result[StaticVector[WasmNumber], Trap]:
        """Run the Python reference interpreter until the call completes."""
        while not call_state.finished:
            call_state = self._step(call_state, stop_at_boundary=False)
        if call_state.trap is not None:
            return Result.err(call_state.trap)
        assert call_state.results is not None
        return Result.ok(call_state.results)

    def _abort_call(self, call_state: InterpreterCall, trap: Trap, ip: int) -> None:
        """Terminate every active frame and publish a runtime trap outcome.

        `ip` is passed explicitly so the trap event records the instruction that
        failed even when dispatch has already moved the resumable call state to a
        boundary or return sentinel.
        """
        if self.logger is not None:
            frame = call_state._frame
            assert frame is not None
            # GOTCHA-LOG-04: Code-section PC must be captured before frame teardown below.
            unified_pc = (
                RETURN_SENTINEL_PC if ip == RETURN_SENTINEL_IP else frame.code_pc_offset + ip
            )
            self.logger.log_event(
                LogLevel.ERROR, LOG_EVT_TRAP_BASE + int(trap.code), unified_pc, trap.detail
            )
        while call_state.context.call_frame_stack:
            frame = call_state.context.call_frame_stack[-1]
            call_state.context.end_call_frame(frame)
        while call_state.call_stack:
            call_state.call_stack.pop_back()
        call_state._set_cont(None)
        call_state.finished = True
        call_state.results = None
        call_state.trap = trap
        call_state.context.runtime_flags &= ~EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
        call_state.context.release_workspace()

    def run_iter(self, func_index: int, args: Sequence[WasmNumber]) -> Iterator[InterpreterCall]:
        """
        Drives a call basic-block by basic-block, yielding the (possibly still-unfinished)
        `call_state` after every `step()` and once more after it finishes.
        """
        call_state = self.start(func_index, args)
        while not call_state.finished:
            override = yield call_state
            call_state = override if override is not None else self.step(call_state)
        yield call_state

    def start(self, func_index: int, args: Sequence[WasmNumber]) -> InterpreterCall:
        """
        Sets up a resumable call, to be driven by repeated `step()` calls.
                A host import has nothing to step through: it resolves synchronously
                right here, so the returned call is already finished.
        """
        context = self._new_context()
        if self.initialization_trap is not None:
            context.release_workspace()
            return InterpreterCall(
                func_index, context, cont=None, finished=True, trap=self.initialization_trap
            )
        if self.module.is_import(func_index):
            results = self._env.runtime_api.invoke_import(func_index, args)
            trap = Trap(TrapCode.NO_HOST_HANDLER, func_index) if results is None else None
            call_state = InterpreterCall(
                func_index,
                context,
                cont=None,
                finished=True,
                results=results,
                trap=trap,
            )
            context.release_workspace()
            return call_state

        self._prepare_native_execution(context)
        raw_args = _encode_public_args(args, self.module.func_type(func_index).params)
        built = self._build_frame(func_index, raw_args, context)
        if not built.is_ok:
            context.release_workspace()
            return InterpreterCall(
                func_index,
                context,
                cont=None,
                finished=True,
                trap=built.error,
            )
        frame, locals_arr = built.unwrap()
        return InterpreterCall(func_index, context, cont=(0, frame, locals_arr))

    def _new_context(self) -> ExecutionContext:
        """Create the one context object used by Python and native execution."""
        return ExecutionContext(self.module, allocator=self.bump_allocator)

    def _prepare_native_execution(self, context: ExecutionContext) -> None:
        """Allow the strict native interpreter to install call descriptors."""
        return None

    def _build_frame(
        self, func_index: int, raw_args: StaticVector[int], context: ExecutionContext
    ) -> Result[tuple[CallFrame, _LocalStackWindow], Trap]:
        """
        Builds the initial frame + locals for a WASM (non-import) function
        activation. `raw_args` contains the packed parameter values as
        32-bit slots; the load-time local-width metadata places them into the
        aligned local layout exactly once at frame creation.
        The internal call path obtains it directly from the operand stack;
        the public entry path encodes host values once at that boundary.

        The Native local slots are pushed into `context` by
        `ExecutionContext.begin_call_frame`; the operand stack is also owned
        by that same context and is shared across the complete call chain.
        """
        fn = self.module.functions[func_index - len(self.module.imports)]
        param_packed_slot_count = fn.param_packed_slot_count_cache
        assert fn.local_layout_cache is not None
        assert param_packed_slot_count is not None
        assert len(raw_args) == param_packed_slot_count
        assert fn.control_map is not None
        if fn.max_operand_stack_words > context.stack.capacity - len(context.stack):
            return Result.err(Trap(TrapCode.OPERAND_STACK_CAPACITY))
        outcome = context.begin_call_frame(
            raw_args,
            func_index=func_index,
            env=self._env,
        )
        if not outcome.is_ok:
            assert outcome.error is not None
            return Result.err(outcome.error)
        frame = outcome.unwrap()
        return Result.ok((frame, frame.locals))

    def step(self, call_state: InterpreterCall) -> InterpreterCall:
        """
        Executes one basic block (up to the next boundary instruction) and returns
        `call_state`, mutated in place: still `finished == False` with a resumable
        scalar continuation fields, or `finished == True` with `.results` set once the outermost call
        actually returns.
        """
        return self._step(call_state, stop_at_boundary=True)

    def step_native(self, call_state: InterpreterCall) -> InterpreterCall:
        """Advance native execution to its next resumable boundary."""
        call_state = self.step_native_boundary(call_state).call_state
        if call_state._ip == RETURN_SENTINEL_IP and not call_state.finished:
            return self._finish_native_frame(call_state)
        return call_state

    def step_native_boundary(
        self,
        call_state: InterpreterCall,
        *,
        idle_budget: int = 0,
    ) -> NativeInterpreterBoundary:
        """Advance the Interpreter-owned native dispatcher to one scheduler boundary."""
        assert self.debugger is None, "native stepping does not support Python debugger hooks"
        if call_state.finished:
            return NativeInterpreterBoundary(call_state)
        if call_state._ip == RETURN_SENTINEL_IP:
            return NativeInterpreterBoundary(self._finish_native_frame(call_state))
        if self._try_native_step_to_boundary(call_state):
            return NativeInterpreterBoundary(call_state)
        if call_state.finished:
            return NativeInterpreterBoundary(call_state)
        if call_state._ip == RETURN_SENTINEL_IP:
            return NativeInterpreterBoundary(self._finish_native_frame(call_state))

        while True:
            native_status = self.run_native_dispatch(
                call_state,
                idle_budget=idle_budget,
            )
            if native_status == NATIVE_DISPATCH_RUNTIME_API_BOUNDARY:
                host_call = self.resolve_native_api_boundary(call_state)
                if host_call or call_state.finished or call_state._ip == RETURN_SENTINEL_IP:
                    return NativeInterpreterBoundary(call_state)
                continue
            break

        if native_status == 1:
            call_state._ip = RETURN_SENTINEL_IP
        elif native_status == 2:
            assert call_state.finished and call_state.trap is not None
        elif native_status == NATIVE_DISPATCH_YIELD:
            call_state.context.loop_jump_count = 0
        elif native_status == NATIVE_DISPATCH_DEBUG_STOP:
            # A statically composed debugger has stopped native execution.
            # This is a runtime boundary, not a different instruction driver.
            return NativeInterpreterBoundary(call_state)
        elif native_status == NATIVE_DISPATCH_CALL_BOUNDARY:
            return NativeInterpreterBoundary(call_state)
        else:
            assert False, f"unexpected C++ interpreter status: {native_status}"
        return NativeInterpreterBoundary(
            call_state,
            yield_requested=native_status == NATIVE_DISPATCH_YIELD,
        )

    def resolve_native_api_boundary(self, call_state: InterpreterCall) -> bool:
        """Run the Runtime API operation that stopped native dispatch."""
        frame = call_state._frame
        locals_arr = call_state._locals
        ip = call_state._ip
        assert frame is not None and locals_arr is not None
        assert 0 <= ip < len(frame.code)
        opcode = frame.code[ip]
        if opcode == CALL:
            callee_func_index, _ = decode_unsigned(frame.code, ip + 1)
            assert self.module.is_import(callee_func_index), (
                "C++ dispatcher returned a defined guest call instead of executing it"
            )
            trap = self._enter_or_resolve_call(call_state, opcode, ip, frame, locals_arr)
            if trap is not None:
                self._abort_call(call_state, trap, ip)
            else:
                call_state.context.runtime_flags |= EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
            return True
        if opcode == CALL_INDIRECT:
            _, off = decode_unsigned(frame.code, ip + 1)
            table_index, _ = decode_unsigned(frame.code, off)
            table_slot = _to_u32(frame.values[-1])
            assert table_index < len(self.tables)
            table = self.tables[table_index]
            assert table_slot < len(table)
            callee_index = table[table_slot]
            assert callee_index is not None
            callee_func_index = callee_index
            assert self.module.is_import(callee_func_index), (
                "C++ dispatcher returned a defined guest call instead of executing it"
            )
            trap = self._enter_or_resolve_call(call_state, opcode, ip, frame, locals_arr)
            if trap is not None:
                self._abort_call(call_state, trap, ip)
            else:
                call_state.context.runtime_flags |= EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
            return True
        if I32_LOAD <= opcode <= MEMORY_GROW:
            self._resolve_native_memory_boundary(call_state, opcode, ip)
            return False
        if opcode == FC_PREFIX:
            subopcode, _ = decode_unsigned(frame.code, ip + 1)
            assert subopcode == FC_MEMORY_COPY or subopcode == FC_MEMORY_FILL, (
                f"C++ interpreter does not implement 0xFC subopcode {subopcode}"
            )
            self._resolve_native_memory_boundary(call_state, opcode, ip)
            return False
        assert False, f"C++ interpreter does not implement opcode 0x{opcode:02X}"

    def _resolve_native_memory_boundary(
        self, call_state: InterpreterCall, opcode: int, ip: int
    ) -> InterpreterCall:
        """Run only the memory-manager operation C++ explicitly yielded."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        handler = _HANDLERS[opcode]
        assert handler is not None
        context = call_state.context
        assert context.call_frame_stack and context.call_frame_stack[-1] is frame
        self._set_current_block_index_at_ip(context, frame, ip)
        context.ip = ip
        trap = handler(context, context.ip_ptr, frame.values)
        if trap is not None:
            self._abort_call(call_state, trap, ip)
            return call_state
        next_ip = int(context.ip)
        block_changed = self._set_current_block_index_at_ip(context, frame, next_ip)
        if block_changed:
            context.runtime_flags |= EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
        call_state._ip = RETURN_SENTINEL_IP if next_ip >= len(frame.code) else next_ip
        return call_state

    def _set_current_block_index_at_ip(
        self, context: ExecutionContext, frame: CallFrame, instruction_ip: int
    ) -> bool:
        """Advance through the active block's precomputed edge metadata."""
        if instruction_ip >= len(frame.code):
            context.current_block_index = 0xFFFF
            return True
        target_pc = frame.code_pc_offset + instruction_ip
        current_index = int(context.current_block_index)
        assert current_index < len(self.module.blocks)
        source = self.module.blocks[current_index]
        if source.head_pc == target_pc:
            return True
        if source.next_pc == target_pc:
            target_index = source.next_block_index
        elif source.loops_to == target_pc:
            target_index = source.loops_to_block_index
        elif source.head_pc < target_pc <= source.head_pc + source.byte_span:
            return False
        else:
            assert False, (
                "native Runtime API continuation is outside its loader-known block: "
                f"index={current_index} head={source.head_pc} span={source.byte_span} "
                f"next={source.next_pc} loops={source.loops_to} target={target_pc}"
            )
        assert target_index is not None
        assert self.module.blocks[target_index].head_pc == target_pc
        context.current_block_index = target_index
        return True

    def _finish_native_frame(self, call_state: InterpreterCall) -> InterpreterCall:
        """Complete one C++ return boundary and restore its suspended caller."""
        frame = call_state._frame
        assert frame is not None and call_state._ip == RETURN_SENTINEL_IP
        call_state.context.end_call_frame(frame)
        if not call_state.call_stack:
            func_type = self.module.func_type(call_state.func_index)
            results: StaticVector[WasmNumber] = StaticVector(capacity=4)
            if func_type.results:
                result_type = func_type.results[0]
                if result_type == I64:
                    result_value = frame.values.pop_i64()
                elif result_type == F32:
                    result_value = frame.values.pop_f32()
                elif result_type == F64:
                    result_value = frame.values.pop_f64()
                else:
                    result_value = frame.values.pop_i32()
                assert result_value is not None
                results.append(result_value)
            call_state._set_cont(None)
            call_state.finished = True
            call_state.results = results
            call_state.context.runtime_flags &= ~EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
            call_state.context.release_workspace()
            return call_state

        suspended_caller = call_state.call_stack.pop_back()
        assert suspended_caller is not None
        parent_func_index, parent_cont = suspended_caller
        parent_ip, parent_frame, parent_locals = parent_cont
        call_state.func_index = parent_func_index
        call_state._ip = parent_ip
        call_state._frame = parent_frame
        call_state._locals = parent_locals
        call_state.context.runtime_flags |= EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
        return call_state

    def _native_rel_cursor(self, call_state: InterpreterCall) -> int:
        """Select a block's loader-provided pool start at native block entry."""
        frame = call_state._frame
        assert frame is not None and call_state._ip != RETURN_SENTINEL_IP
        context = call_state.context
        if context.runtime_flags & EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD:
            pc = frame.code_pc_offset + call_state._ip
            block_index = int(context.current_block_index)
            assert block_index < len(self.module.blocks)
            block = self.module.blocks[block_index]
            assert block.func_index == frame.func_index and block.head_pc == pc
            annotations = frame._native_interpreter_annotations
            assert annotations is not None and annotations.native_address != 0
            return annotations.native_address + block.interpreter_annotation_byte_offset
        rel = int(context.rel or 0)
        assert rel != 0
        return rel

    def step_native_control(self, call_state: InterpreterCall) -> InterpreterCall:
        """Execute one supported structured-control opcode through its C++ handler."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        assert call_state._ip != RETURN_SENTINEL_IP
        assert self.debugger is None, (
            "native control stepping does not support Python debugger hooks"
        )

        context = call_state.context
        rel = self._native_rel_cursor(call_state)
        execution = context.module_execution
        assert execution is not None
        with _native_linear_memory_scope(context, frame.env.memory):
            native_status, native_ip, native_size, native_trap = _native_abi.run_step(
                _native_abi.RUN_CONTROL_STEP,
                int(frame._native_function_view.code or 0),
                len(frame.code),
                int(context.context_ptr.value or 0),
                context.context_view.nbytes,
                frame.values.address,
                frame.values.native_bytes,
                locals_arr._storage.address,
                locals_arr._storage.native_bytes,
                len(frame.values),
                frame.values.capacity,
                call_state._ip,
                frame.local_slot_count,
                call=context._native_step_call,
                result=context._native_result,
                rel=rel,
            )

        frame.values.set_size(native_size)
        context.ip = native_ip
        context.call_frame_stack.sync_from_native(context, self._env)
        if context.call_frame_stack:
            active = context.call_frame_stack[-1]
            call_state.func_index = active.func_index
            call_state._frame = active
            call_state._locals = active.locals
            if native_status == 3 and native_ip >= len(active.code):
                native_ip = RETURN_SENTINEL_IP
            call_state._ip = native_ip

        if native_status == 3:
            return call_state
        if native_status == 1:
            call_state._ip = RETURN_SENTINEL_IP
            return self._finish_native_frame(call_state)
        if native_status == 2:
            self._abort_call(call_state, Trap(TrapCode(native_trap)), native_ip)
            return call_state
        if native_status == NATIVE_DISPATCH_RUNTIME_API_BOUNDARY:
            self.resolve_native_api_boundary(call_state)
            return call_state
        assert False, f"C++ control handler does not implement opcode at 0x{native_ip:04X}"

    def run_native_dispatch(
        self,
        call_state: InterpreterCall,
        idle_budget: int = 0,
    ) -> int:
        """Run native traces and C++ handlers over Python-owned ctypes buffers."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        assert call_state._ip != RETURN_SENTINEL_IP
        context = call_state.context
        execution = context.module_execution
        assert execution is not None
        context.code = int(frame._native_function_view.code or 0)
        context.code_size = len(frame.code)
        context.ip = call_state._ip
        context.stack_base = frame.values.address
        context.stack_pointer = frame.values.value_ptr(len(frame.values)).value
        context.stack_limit = frame.values.address + frame.values.capacity * 4
        context.local_stack_base = context.local_stack.address
        context.local_stack_pointer = context.local_stack.value_ptr(len(context.local_stack)).value
        context.local_stack_limit = context.local_stack.address + context.local_stack.capacity * 4
        context.local_base = context.local_stack.value_ptr(frame.frame_offset).value
        context.loop_jump_threshold = self.yield_threshold
        with _native_linear_memory_scope(context, frame.env.memory):
            (
                native_status,
                native_ip,
                native_size,
                native_trap,
            ) = _native_abi.run_native_dispatch(
                self._native_dispatcher,
                int(context.context_ptr.value or 0),
                frame.values.address,
                locals_arr._storage.address,
                call=context._native_dispatch_call,
                result=context._native_result,
                jit_provider=execution.jit_provider,
                idle_budget=idle_budget,
            )
        frame.values.set_size(native_size)
        context.ip = native_ip
        context.call_frame_stack.sync_from_native(context, self._env)
        if context.call_frame_stack:
            active = context.call_frame_stack[-1]
            call_state.func_index = active.func_index
            call_state._frame = active
            call_state._locals = active.locals
        call_state._ip = RETURN_SENTINEL_IP if native_status == 1 else native_ip
        if native_status == 2:
            self._abort_call(call_state, Trap(TrapCode(native_trap)), native_ip)
        return native_status

    def _try_native_step_to_boundary(self, call_state: InterpreterCall) -> bool:
        """Use native handlers up to the current block's next JIT boundary."""
        frame = call_state._frame
        locals_arr = call_state._locals
        if frame is None or locals_arr is None or self.debugger is not None:
            return False
        if call_state._ip == RETURN_SENTINEL_IP:
            return False
        if frame.boundary_next_pc is None and frame.boundary_loops_to is None:
            return False

        pc = call_state.current_pc()
        if pc == frame.boundary_next_pc or pc == frame.boundary_loops_to:
            return False

        context = call_state.context
        rel = self._native_rel_cursor(call_state)
        previous_flags = int(context.runtime_flags)
        context.runtime_flags = previous_flags | EXECUTION_CONTEXT_FLAG_STOP_AT_BLOCK_BOUNDARY
        try:
            with _native_linear_memory_scope(context, frame.env.memory):
                native_status, native_ip, native_size, native_trap = _native_abi.run_step(
                    _native_abi.RUN_STEP,
                    int(frame._native_function_view.code or 0),
                    len(frame.code),
                    int(context.context_ptr.value or 0),
                    context.context_view.nbytes,
                    frame.values.address,
                    frame.values.native_bytes,
                    locals_arr._storage.address,
                    locals_arr._storage.native_bytes,
                    len(frame.values),
                    frame.values.capacity,
                    call_state._ip,
                    frame.local_slot_count,
                    call=context._native_step_call,
                    result=context._native_result,
                    rel=rel,
                )
        finally:
            context.runtime_flags = (
                previous_flags & ~EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD
            ) | (int(context.runtime_flags) & EXECUTION_CONTEXT_FLAG_PENDING_BLOCK_HEAD)

        frame.values.set_size(native_size)
        context.ip = native_ip
        context.call_frame_stack.sync_from_native(context, self._env)
        if context.call_frame_stack:
            active = context.call_frame_stack[-1]
            call_state.func_index = active.func_index
            call_state._frame = active
            call_state._locals = active.locals
            if native_status == 3 and native_ip >= len(active.code):
                native_ip = RETURN_SENTINEL_IP
            call_state._ip = native_ip

        if native_status == 3:
            return True
        if native_status == 1:
            call_state._ip = RETURN_SENTINEL_IP
            return False
        if native_status == 2:
            self._abort_call(call_state, Trap(TrapCode(native_trap)), native_ip)
            return True
        if native_status == NATIVE_DISPATCH_RUNTIME_API_BOUNDARY:
            self.resolve_native_api_boundary(call_state)
            return True
        assert False, f"unexpected native interpreter status: {native_status}"

    def _step(self, call_state: InterpreterCall, stop_at_boundary: bool) -> InterpreterCall:
        """Run until a boundary or completion, depending on the driving caller."""
        while True:
            ip = call_state._ip
            frame = call_state._frame
            locals_arr = call_state._locals
            assert frame is not None and locals_arr is not None
            if ip != RETURN_SENTINEL_IP:
                code = frame.code
                assert 0 <= ip < len(code)
                op = code[ip]
                if op == CALL or op == CALL_INDIRECT:
                    trap = self._enter_or_resolve_call(call_state, op, ip, frame, locals_arr)
                    if trap is not None:
                        self._abort_call(call_state, trap, ip)
                        return call_state
                    if stop_at_boundary:
                        return call_state
                    continue
                is_boundary = _BASIC_BLOCK_BOUNDARY[op] != 0
                handler = _HANDLERS[op]
                assert handler is not None, f"interpreter: unhandled opcode 0x{op:02X}"
                # `context` and `values` are fixed for this frame's whole
                # lifetime (set once in CallFrame/InterpreterCall.__init__,
                # never reassigned) -- hoisted so this hottest of all loops
                # pays the attribute-lookup cost once per instruction
                # instead of four (context) and three (values) times.
                ctx = call_state.context
                values = frame.values
                assert ctx.call_frame_stack and ctx.call_frame_stack[-1] is frame
                ctx.ip = ip
                trap = handler(ctx, ctx.ip_ptr, values)
                if trap is not None:
                    self._abort_call(call_state, trap, ip)
                    return call_state
                next_ip = int(ctx.ip)
                result_frame = ctx.call_frame_stack[-1]
                assert result_frame is frame
                result_locals = locals_arr
                if next_ip >= len(code):
                    next_ip = RETURN_SENTINEL_IP
                call_state._ip = next_ip
                call_state._frame = result_frame
                call_state._locals = result_locals
                if call_state._ip != RETURN_SENTINEL_IP:
                    if is_boundary and stop_at_boundary:
                        return call_state
                    continue
                # The explicit return sentinel ends this frame; nested calls
                # consume it here and restore the suspended caller below.  A
                # boundary-driven caller gets one observable sentinel step so
                # RuntimeEngine can perform its top-level completion check.
                if stop_at_boundary:
                    return call_state

            ft = self.module.func_type(call_state.func_index)
            call_state.context.end_call_frame(frame)
            if not call_state.call_stack:
                results: StaticVector[WasmNumber] = StaticVector(capacity=4)
                # The Native operand stack contains raw slots only. The
                # caller's function signature selects the typed read here;
                # no return type is stored in the result buffer.
                if ft.results:
                    result_type = ft.results[0]
                    if result_type == I64:
                        result_value = frame.values.pop_i64()
                    elif result_type == F32:
                        result_value = frame.values.pop_f32()
                    elif result_type == F64:
                        result_value = frame.values.pop_f64()
                    else:
                        result_value = frame.values.pop_i32()
                    assert result_value is not None
                    results.append(result_value)
                call_state._set_cont(None)
                call_state.finished = True
                call_state.results = results
                call_state.context.release_workspace()
                return call_state
            # Return to the suspended caller frame -- always a mandatory
            # boundary, so the runtime gets the same chance to check its JIT
            # trace cache here as it does entering any other frame. The
            # operand stack is shared across this whole call chain (see
            # the result -- already sitting on the context-owned operand
            # stack -- needs no copy:
            # the caller's own next pop/push simply continues from here.
            suspended_caller = call_state.call_stack.pop_back()
            assert suspended_caller is not None
            parent_func_index, parent_cont = suspended_caller
            p_ip, p_frame, p_locals = parent_cont
            call_state.func_index = parent_func_index
            call_state._ip = p_ip
            call_state._frame = p_frame
            call_state._locals = p_locals
            if stop_at_boundary:
                return call_state

    def _enter_or_resolve_call(
        self,
        call_state: InterpreterCall,
        opcode: int,
        ip: int,
        frame: CallFrame,
        locals_arr: _LocalStackWindow,
    ) -> Trap | None:
        """
        Resolves the callee of a `call`/`call_indirect` directly from bytecode and either invokes a
        host import synchronously (there is nothing to step through) or
        pushes this frame onto `call_state.call_stack` and hands control to
        the callee's freshly-built entry frame.
        """
        if opcode == CALL:
            callee_func_index, next_ip = decode_unsigned(frame.code, ip + 1)
            callee_ft = self.module.func_type(callee_func_index)
        else:
            typeidx, off = decode_unsigned(frame.code, ip + 1)
            tableidx, next_ip = decode_unsigned(frame.code, off)
            table = frame.env.tables[tableidx]
            table_slot = _to_u32(frame.values.pop_back())
            if table_slot >= len(table):
                return Trap(TrapCode.TABLE_INDEX_OUT_OF_BOUNDS, table_slot)
            callee_func_index = table[table_slot]
            if callee_func_index is None:
                return Trap(TrapCode.TABLE_SLOT_UNINITIALIZED, table_slot)
            expected = self.module.type_at(typeidx)
            callee_ft = self.module.func_type(callee_func_index)
            if expected.params != callee_ft.params or expected.results != callee_ft.results:
                return Trap(TrapCode.INDIRECT_CALL_TYPE_MISMATCH)

        if self.module.is_import(callee_func_index):
            call_args: StaticVector[WasmNumber] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
            popped_args: StaticVector[WasmNumber] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
            for value_type in reversed(callee_ft.params):
                if value_type == I64:
                    value = frame.values.pop_i64()
                elif value_type == F32:
                    value = frame.values.pop_f32()
                elif value_type == F64:
                    value = frame.values.pop_f64()
                else:
                    value = frame.values.pop_i32()
                assert value is not None
                popped_args.append(value)
            for index in range(len(popped_args) - 1, -1, -1):
                call_args.append(popped_args[index])
            results = self._env.runtime_api.invoke_import(callee_func_index, call_args)
            if results is None:
                return Trap(TrapCode.NO_HOST_HANDLER, callee_func_index)
            for index, result in enumerate(results):
                result_type = callee_ft.results[index]
                if result_type == I64:
                    pushed = frame.values.push_i64(int(result))
                elif result_type == F32:
                    pushed = frame.values.push_f32(float(result))
                elif result_type == F64:
                    pushed = frame.values.push_f64(float(result))
                else:
                    pushed = frame.values.push_i32(int(result))
                if not pushed:
                    return Trap(TrapCode.OPERAND_STACK_CAPACITY)
            call_state._ip = next_ip
            call_state._frame = frame
            call_state._locals = locals_arr
            return None

        popped_raw_args: StaticVector[int] = StaticVector(capacity=FB_CONF_MAX_VALUE_STACK)
        for value_type in reversed(callee_ft.params):
            for _ in range(value_slot_width(value_type)):
                value = frame.values.pop_back()
                assert value is not None
                popped_raw_args.append(value)
        popped_raw_args.reverse_in_place()
        resume_cont = (next_ip, frame, locals_arr)
        built = self._build_frame(callee_func_index, popped_raw_args, call_state.context)
        if not built.is_ok:
            assert built.error is not None
            return built.error
        callee_frame, callee_locals = built.unwrap()
        callee_frame.native.return_ip = next_ip
        if not call_state.call_stack.push_back((call_state.func_index, resume_cont)):
            return Trap(TrapCode.CALL_STACK_CAPACITY)
        call_state.func_index = callee_func_index
        call_state._ip = 0
        call_state._frame = callee_frame
        call_state._locals = callee_locals
        return None


class NativeInterpreter(Interpreter):
    """Strict C++ interpreter; defined guest calls remain in native dispatch."""

    __slots__ = (
        "_execution_plugin",
        "_module_execution",
        "_native_dispatch_started",
    )

    def __init__(
        self,
        module: Module,
        bindings: InterpreterBindings,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: LoggerPort | None = None,
        *,
        bump_allocator: BumpAllocator | None = None,
        native_dispatcher: NativeDispatchEntryPoint = _native_abi.RUN_DISPATCH,
        execution_plugin: NativeExecutionPlugin | None = None,
        yield_threshold: int = FB_CONF_INTERPRETER_YIELD_THRESHOLD,
    ):
        self._execution_plugin = execution_plugin
        self._module_execution: NativeModuleExecution | None = None
        self._native_dispatch_started = False
        if execution_plugin is not None:
            native_dispatcher = execution_plugin.native_entry
        super().__init__(
            module,
            bindings,
            vmmio,
            phys_mem,
            logger,
            bump_allocator=bump_allocator,
            native_dispatcher=native_dispatcher,
            yield_threshold=yield_threshold,
        )

    def attach_execution_plugin(
        self,
        plugin: NativeExecutionPlugin,
    ) -> None:
        """Attach one JIT trace provider before the first native dispatch."""
        assert self.debugger is None, "native execution plugins cannot be combined with debugger"
        assert self._execution_plugin is None or self._execution_plugin is plugin
        if self._execution_plugin is plugin:
            return
        assert not self._native_dispatch_started
        if self._module_execution is not None:
            plugin.register_module(self.module)
            self._module_execution.jit_provider = plugin.bind_execution(self._module_execution)
        self._execution_plugin = plugin
        self._native_dispatcher = plugin.native_entry

    def step_native_boundary(
        self,
        call_state: InterpreterCall,
        *,
        idle_budget: int = 0,
    ) -> NativeInterpreterBoundary:
        """Record that native dispatch has started before advancing its boundary."""
        self._native_dispatch_started = True
        return super().step_native_boundary(
            call_state,
            idle_budget=idle_budget,
        )

    def attach_debugger(self, debugger: DebuggerAttachment) -> None:
        """Attach debugger execution control when no native execution plugin is active."""
        assert self._execution_plugin is None, (
            "native execution plugins cannot be combined with debugger"
        )
        super().attach_debugger(debugger)

    def idle_hook(self, budget: int = 4) -> int:
        """Give the attached JIT cache a bounded compile slice."""
        if self._execution_plugin is None:
            return 0
        return self._execution_plugin.idle_hook(budget)

    def call(
        self,
        func_index: int,
        args: Sequence[WasmNumber],
        idle_budget: int = 4,
    ) -> Result[StaticVector[WasmNumber], Trap]:
        """Run native dispatch to completion and service JIT compilation once."""
        return self._complete_call(self.start(func_index, args), idle_budget)

    def _prepare_native_execution(self, context: ExecutionContext) -> None:
        if self._module_execution is None:
            self._module_execution = NativeModuleExecution(
                context,
                self.module,
                self._env,
                self.tables,
                self.bump_allocator,
            )
            if self._execution_plugin is not None:
                self._execution_plugin.register_module(self.module)
                self._module_execution.jit_provider = self._execution_plugin.bind_execution(
                    self._module_execution
                )
        context.attach_module_execution(self._module_execution)

    def _complete_call(
        self, call_state: InterpreterCall, idle_budget: int = 4
    ) -> Result[StaticVector[WasmNumber], Trap]:
        while not call_state.finished:
            call_state = self.step_native_boundary(
                call_state,
                idle_budget=idle_budget,
            ).call_state
        if call_state.trap is not None:
            return Result.err(call_state.trap)
        assert call_state.results is not None
        self.idle_hook(idle_budget)
        return Result.ok(call_state.results)

    def step(self, call_state: InterpreterCall) -> InterpreterCall:
        return self.step_native(call_state)

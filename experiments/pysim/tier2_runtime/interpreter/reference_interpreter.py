"""Simple Python reference interpreter using ordinary per-call arrays."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, Self, cast

from bump_allocator import BumpAllocator
from config import (
    FB_CONF_MAX_NESTING_DEPTH,
    FB_CONF_MAX_VALUE_STACK,
)
from system_containers import SequenceView as Sequence
from system_containers import StaticVector
from tier2_runtime.interpreter.errors import (
    LOG_EVT_TRAP_BASE,
    RETURN_SENTINEL_IP,
    RETURN_SENTINEL_PC,
    Trap,
    TrapCode,
)
from tier2_runtime.interpreter.handlers import _HANDLERS
from tier2_runtime.interpreter.interpreter import DebuggerAttachment, InterpreterBindings
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
from tier2_runtime.wasm.leb128 import decode_unsigned_full
from tier2_runtime.wasm.module import (
    F32,
    F64,
    I32,
    I64,
    FunctionTable,
    LocalLayout,
    Memory,
    Module,
    value_slot_width,
)
from tier2_runtime.wasm.opcodes import (
    BLOCK,
    BR,
    BR_IF,
    BR_TABLE,
    CALL,
    CALL_INDIRECT,
    ELSE,
    END,
    IF,
    LOOP,
    RETURN,
)

_I32_MASK = 0xFFFF_FFFF
_I64_MASK = 0xFFFF_FFFF_FFFF_FFFF
_UNREACHABLE_TARGET = 0xFFFF_FFFF


class _RawStack:
    """A list-backed stack of WebAssembly 32-bit words."""

    __slots__ = ("_words",)

    def __init__(self) -> None:
        self._words: list[int] = []

    def __len__(self) -> int:
        return len(self._words)

    def __bool__(self) -> bool:
        return bool(self._words)

    def __iter__(self) -> Iterator[int]:
        return iter(self._words)

    def __eq__(self, other: Sequence[int]) -> bool:
        return len(self._words) == len(other) and all(
            self._words[index] == other[index] for index in range(len(self._words))
        )

    def __getitem__(self, index: int) -> int:
        return self._words[index]

    def raw_at(self, index: int) -> int:
        assert 0 <= index < len(self._words)
        return self._words[index]

    def raw_top(self) -> int:
        assert self._words
        return self._words[-1]

    def push_back(self, value: int) -> bool:
        self._words.append(value & _I32_MASK)
        return True

    def pop_back(self) -> int:
        assert self._words
        return self._words.pop()

    def truncate(self, size: int) -> None:
        assert 0 <= size <= len(self._words)
        del self._words[size:]

    def push_raw_from(self, source: _RawWordArray, start: int, count: int) -> bool:
        assert count >= 0 and 0 <= start and start + count <= len(source)
        self._words.extend(source[index] for index in range(start, start + count))
        return True

    def copy_raw_to(self, destination: _LocalArray, start: int, count: int) -> None:
        source_start = len(self._words) - count
        assert count >= 0 and source_start >= 0
        assert 0 <= start and start + count <= len(destination)
        for index in range(count):
            destination[start + index] = self._words[source_start + index]

    def pop_raw_to(self, destination: _LocalArray, start: int, count: int) -> None:
        self.copy_raw_to(destination, start, count)
        self.truncate(len(self._words) - count)

    def push_i32(self, value: int) -> bool:
        return self.push_back(value)

    def push_i64(self, value: int) -> bool:
        raw = value & _I64_MASK
        self._words.extend((raw & _I32_MASK, raw >> 32))
        return True

    def push_f32(self, value: float) -> bool:
        self._words.append(struct.unpack("<I", struct.pack("<f", value))[0])
        return True

    def push_f64(self, value: float) -> bool:
        raw = struct.unpack("<Q", struct.pack("<d", value))[0]
        self._words.extend((raw & _I32_MASK, raw >> 32))
        return True

    def pop_i32(self) -> int:
        value = self.pop_back()
        return value - (1 << 32) if value & 0x8000_0000 else value

    def pop_i64(self) -> int:
        assert len(self._words) >= 2
        high = self.pop_back()
        low = self.pop_back()
        raw = (high << 32) | low
        return raw - (1 << 64) if raw & (1 << 63) else raw

    def pop_f32(self) -> float:
        return struct.unpack("<f", struct.pack("<I", self.pop_back()))[0]

    def pop_f64(self) -> float:
        assert len(self._words) >= 2
        high = self.pop_back()
        low = self.pop_back()
        return struct.unpack("<d", struct.pack("<Q", (high << 32) | low))[0]


class _RawWordArray(Protocol):
    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> int: ...

    def __setitem__(self, index: int, value: int) -> None: ...


class _LocalArray:
    """A function's flat raw-word local array and loader-computed layout."""

    __slots__ = ("_words", "layout")

    def __init__(self, layout: LocalLayout, word_count: int):
        self.layout = layout
        self._words = [0] * word_count

    def __len__(self) -> int:
        return len(self._words)

    def __getitem__(self, index: int) -> int:
        return self._words[index]

    def __setitem__(self, index: int, value: int) -> None:
        self._words[index] = value & _I32_MASK

    def raw_span(self, index: int) -> tuple[int, int]:
        layout = self.layout
        return int(layout.offset(index)), int(layout.size_words(index))


class _ReferenceFrame:
    __slots__ = (
        "code",
        "code_pc_offset",
        "context",
        "control_map",
        "env",
        "func_index",
        "ip",
        "locals",
        "values",
    )

    def __init__(
        self,
        context: _ReferenceContext,
        func_index: int,
        env: _ReferenceEnv,
        raw_args: list[int],
    ):
        module = context.module
        function = module.functions[func_index - len(module.imports)]
        layout = function.local_layout_cache
        assert layout is not None
        assert function.control_map is not None
        self.context = context
        self.func_index = func_index
        self.env = env
        self.code = module.code_for(func_index)
        self.code_pc_offset = module.function_pc_offset(func_index)
        self.control_map = function.control_map
        self.values = _RawStack()
        self.locals = _LocalArray(layout, int(layout.total_words))
        self.ip = 0
        param_count = function.param_count_cache
        param_word_count = function.param_packed_slot_count_cache
        assert param_count is not None and param_word_count is not None
        assert len(raw_args) == param_word_count
        param_words = 0
        for local_index in range(param_count):
            local_offset, width = self.locals.raw_span(local_index)
            for word_index in range(width):
                self.locals[local_offset + word_index] = raw_args[param_words + word_index]
            param_words += width
        assert param_words == len(raw_args)


class _InstructionPointer:
    __slots__ = ("_context",)

    def __init__(self, context: _ReferenceContext):
        self._context = context

    def __getitem__(self, index: int) -> int:
        assert index == 0
        return self._context.ip

    def __setitem__(self, index: int, value: int) -> None:
        assert index == 0
        self._context.ip = value


class _ReferenceContext:
    __slots__ = ("call_frame_stack", "ip", "ip_ptr", "local_stack", "module")

    def __init__(self, module: Module):
        self.module = module
        self.call_frame_stack: list[_ReferenceFrame] = []
        self.local_stack: _RawWordArray = []
        self.ip = 0
        self.ip_ptr = _InstructionPointer(self)

    @property
    def stack(self) -> _RawStack:
        return self.call_frame_stack[-1].values if self.call_frame_stack else _RawStack()


@dataclass(slots=True)
class _ReferenceEnv:
    module: Module
    memory: bytearray | None
    globals: list[int]
    tables: Sequence[FunctionTable]
    runtime_api: RuntimeAPI
    vmmio: VMMIOController | None
    phys_mem: bytearray | None
    vdma_transfer: VdmaTransfer | None


class ReferenceCallState:
    """Resumable state whose only execution storage is a list of frames."""

    __slots__ = ("context", "finished", "func_index", "results", "trap")

    def __init__(self, context: _ReferenceContext, func_index: int):
        self.context = context
        self.func_index = func_index
        self.finished = False
        self.results: StaticVector[WasmNumber] | None = None
        self.trap: Trap | None = None

    @property
    def _frame(self) -> _ReferenceFrame | None:
        return self.context.call_frame_stack[-1] if self.context.call_frame_stack else None

    @property
    def _ip(self) -> int:
        frame = self._frame
        return 0 if frame is None else frame.ip

    @property
    def _locals(self) -> _LocalArray | None:
        frame = self._frame
        return None if frame is None else frame.locals

    @property
    def call_stack(self) -> list[_ReferenceFrame]:
        return self.context.call_frame_stack

    def current_pc(self) -> int:
        frame = self._frame
        assert frame is not None
        return (
            RETURN_SENTINEL_PC
            if frame.ip == RETURN_SENTINEL_IP
            else frame.code_pc_offset + frame.ip
        )


class _ReferenceHandler(Protocol):
    def __call__(
        self, ctx: _ReferenceContext, ip: _InstructionPointer, sp: _RawStack
    ) -> Trap | None: ...


class ReferenceInterpreter:
    """Straightforward reference execution over code, locals, and operand arrays."""

    __slots__ = (
        "_env",
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
    )

    def __init__(
        self,
        module: Module,
        bindings: InterpreterBindings | None = None,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: LoggerPort | None = None,
        *,
        bump_allocator: BumpAllocator | None = None,
        memory: bytearray | None = None,
        host_functions: Sequence[HostFunction | None] | None = None,
        imported_globals: Sequence[int] | None = None,
        imported_tables: Sequence[FunctionTable] | None = None,
        imported_memory: Memory | None = None,
        vdma_transfer: VdmaTransfer | None = None,
    ):
        self.module = module
        self.bump_allocator = bump_allocator or module.allocator
        assert self.bump_allocator is not None, "module has no loader allocator"
        self.module.relocate_to(self.bump_allocator)
        self.logger = logger
        self.vmmio = vmmio
        self.phys_mem = phys_mem
        self.debugger = None
        if bindings is None:
            memory_decl = imported_memory or module.memory or Memory(min_pages=0, max_pages=None)
            actual_memory = memory
            if actual_memory is None:
                actual_memory = bytearray(memory_decl.min_pages * PAGE_SIZE)
            actual_hosts = host_functions or StaticVector(capacity=len(module.imports))
            if not host_functions and len(module.imports):
                actual_hosts = StaticVector.of(
                    tuple(None for _ in range(len(module.imports))),
                    capacity=len(module.imports),
                )
            actual_globals = imported_globals or StaticVector(capacity=0)
            actual_tables = imported_tables or StaticVector(capacity=0)
            bindings = InterpreterBindings(
                memory=actual_memory,
                memory_decl=memory_decl,
                imported_memory=imported_memory is not None,
                host_functions=cast(StaticVector[HostFunction | None], actual_hosts),
                globals=cast(StaticVector[int], actual_globals),
                tables=cast(StaticVector[FunctionTable], actual_tables),
                vdma_transfer=vdma_transfer,
            )
        else:
            assert memory is None and host_functions is None
            assert imported_globals is None and imported_tables is None
            assert imported_memory is None and vdma_transfer is None

        if module.memory_import is not None:
            assert bindings.imported_memory
            assert len(bindings.memory) >= module.memory_import.min_limit * PAGE_SIZE
            assert bindings.memory_decl.min_pages >= module.memory_import.min_limit
            if module.memory_import.max_limit is not None:
                assert bindings.memory_decl.max_pages is not None
                assert bindings.memory_decl.max_pages <= module.memory_import.max_limit
        else:
            assert not bindings.imported_memory
            if module.memory is not None:
                assert len(bindings.memory) >= module.memory.min_pages * PAGE_SIZE
        self.memory = bindings.memory
        self.memory_decl = bindings.memory_decl
        assert len(bindings.host_functions) == len(module.imports)
        assert len(bindings.globals) == module.global_import_count
        globals_: list[int] = []
        imported_global_index = 0
        for global_value in module.globals:
            if global_value.imported:
                globals_.append(bindings.globals[imported_global_index])
                imported_global_index += 1
            elif global_value.init_global_index is not None:
                globals_.append(globals_[global_value.init_global_index])
            else:
                globals_.append(global_value.init_value)
        self.globals = globals_

        tables: StaticVector[FunctionTable] = StaticVector(capacity=len(module.tables))
        assert len(bindings.tables) == module.table_import_count
        imported_table_index = 0
        for table_index, table in enumerate(module.tables):
            if table.imported:
                external_table = bindings.tables[imported_table_index]
                imported_table_index += 1
                table_value = module.table_contents(
                    table_index,
                    self.globals,
                    external_table,
                    allocator=self.bump_allocator,
                )
            else:
                table_value = module.table_contents(
                    table_index, self.globals, allocator=self.bump_allocator
                )
            tables.append(table_value)
        function_count = len(module.imports) + len(module.functions)
        for table in tables:
            assert all(index is None or 0 <= index < function_count for index in table)
            table.seal()
        self.tables = tables
        runtime_api = PythonRuntimeAPI(
            module,
            bindings.memory,
            self.memory_decl,
            bindings.host_functions,
        )
        self._env = _ReferenceEnv(
            module,
            bindings.memory,
            self.globals,
            self.tables,
            runtime_api,
            vmmio,
            phys_mem,
            bindings.vdma_transfer,
        )
        self.initialization_trap: Trap | None = None
        if module.start_function is not None:
            self.initialization_trap = self.call(module.start_function, ()).error

    @classmethod
    def with_module_allocator(
        cls: type[Self],
        module: Module,
        bindings: InterpreterBindings,
        vmmio: VMMIOController | None = None,
        phys_mem: bytearray | None = None,
        logger: LoggerPort | None = None,
    ) -> Self:
        allocator = module.allocator
        assert allocator is not None, "module has no loader allocator"
        return cls(
            module,
            bindings,
            vmmio,
            phys_mem,
            logger,
            bump_allocator=allocator,
        )

    def attach_debugger(self, debugger: DebuggerAttachment) -> None:
        self.debugger = debugger

    def detach_debugger(self) -> None:
        self.debugger = None

    def idle_hook(self) -> int:
        return 0

    def start(self, func_index: int, args: Sequence[WasmNumber]) -> ReferenceCallState:
        context = _ReferenceContext(self.module)
        call = ReferenceCallState(context, func_index)
        if self.initialization_trap is not None:
            call.finished = True
            call.trap = self.initialization_trap
            return call
        if self.module.is_import(func_index):
            results = self._env.runtime_api.invoke_import(func_index, args)
            if results is None:
                call.trap = Trap(TrapCode.NO_HOST_HANDLER, func_index)
            else:
                call.results = results
            call.finished = True
            return call
        raw_args = list(_encode_args(args, self.module.func_type(func_index).params))
        trap = self._enter_frame(call, func_index, raw_args)
        if trap is not None:
            self._abort(call, trap, 0)
        return call

    def run_iter(self, func_index: int, args: Sequence[WasmNumber]) -> Iterator[ReferenceCallState]:
        call = self.start(func_index, args)
        while not call.finished:
            yield call
            call = self.step(call)
        yield call

    def step(self, call: ReferenceCallState) -> ReferenceCallState:
        """Execute one instruction or complete one returned activation."""
        if call.finished:
            return call
        frame = call._frame
        assert frame is not None
        ip = frame.ip
        if ip == RETURN_SENTINEL_IP or ip >= len(frame.code):
            self._return_frame(call, frame)
            return call
        assert 0 <= ip < len(frame.code)
        opcode = frame.code[ip]
        context = call.context
        context.ip = ip
        context.local_stack = frame.locals

        if opcode in (BLOCK, LOOP, IF, ELSE, END, BR, BR_IF, BR_TABLE, RETURN):
            self._execute_control(frame, opcode, ip)
            return call
        if opcode in (CALL, CALL_INDIRECT):
            trap = self._execute_call(call, frame, opcode, ip)
            if trap is not None:
                self._abort(call, trap, ip)
            return call

        handler = _HANDLERS[opcode]
        assert handler is not None, f"reference interpreter: unhandled opcode 0x{opcode:02X}"
        trap = cast(_ReferenceHandler, handler)(context, context.ip_ptr, frame.values)
        if trap is not None:
            self._abort(call, trap, ip)
        else:
            frame.ip = context.ip
        return call

    def call(
        self, func_index: int, args: Sequence[WasmNumber]
    ) -> Result[StaticVector[WasmNumber], Trap]:
        call = self.start(func_index, args)
        while not call.finished:
            self.step(call)
        if call.trap is not None:
            return Result.err(call.trap)
        assert call.results is not None
        return Result.ok(call.results)

    def _enter_frame(
        self, call: ReferenceCallState, func_index: int, raw_args: list[int]
    ) -> Trap | None:
        active_words = sum(len(active.values) for active in call.context.call_frame_stack)
        function = self.module.functions[func_index - len(self.module.imports)]
        if active_words + function.max_operand_stack_words > FB_CONF_MAX_VALUE_STACK:
            return Trap(TrapCode.OPERAND_STACK_CAPACITY)
        if len(call.context.call_frame_stack) >= FB_CONF_MAX_NESTING_DEPTH:
            return Trap(TrapCode.CALL_STACK_CAPACITY)
        frame = _ReferenceFrame(call.context, func_index, self._env, raw_args)
        call.context.call_frame_stack.append(frame)
        call.func_index = func_index
        return None

    def _execute_control(self, frame: _ReferenceFrame, opcode: int, ip: int) -> None:
        if opcode == BLOCK or opcode == LOOP:
            frame.ip = ip + 2
        elif opcode == IF:
            match_end, else_offset, _ = frame.control_map.block(ip)
            if frame.values.pop_i32() == 0:
                frame.ip = else_offset + 1 if else_offset is not None else match_end + 1
            else:
                frame.ip = ip + 2
        elif opcode == ELSE:
            frame.ip = frame.control_map.else_target(ip)
        elif opcode == END:
            frame.ip = ip + 1
        elif opcode == BR:
            self._apply_branch(frame, ip, 0)
        elif opcode == BR_IF:
            _depth, next_ip = decode_unsigned_full(frame.code, ip + 1)
            if frame.values.pop_i32() == 0:
                frame.ip = next_ip
            else:
                self._apply_branch(frame, ip, 0)
        elif opcode == BR_TABLE:
            selector = frame.values.pop_i32() & _I32_MASK
            edges = frame.control_map.branch_edges(ip)
            edge_index = selector if selector < len(edges) - 1 else len(edges) - 1
            self._apply_branch(frame, ip, edge_index)
        else:
            assert opcode == RETURN
            frame.ip = RETURN_SENTINEL_IP

    @staticmethod
    def _apply_branch(frame: _ReferenceFrame, ip: int, edge_index: int) -> None:
        target_ip, stack_base, result_words = frame.control_map.branch_edges(ip)[edge_index]
        values = frame.values
        assert 0 <= result_words <= 2
        assert stack_base + result_words <= len(values)
        result = values._words[-result_words:] if result_words else []
        values.truncate(stack_base)
        for raw_word in result:
            values.push_back(raw_word)
        frame.ip = RETURN_SENTINEL_IP if target_ip == _UNREACHABLE_TARGET else target_ip

    def _execute_call(
        self, call: ReferenceCallState, frame: _ReferenceFrame, opcode: int, ip: int
    ) -> Trap | None:
        if opcode == CALL:
            callee_index, next_ip = decode_unsigned_full(frame.code, ip + 1)
        else:
            expected_type_index, offset = decode_unsigned_full(frame.code, ip + 1)
            table_index, next_ip = decode_unsigned_full(frame.code, offset)
            if table_index >= len(self.tables):
                return Trap(TrapCode.TABLE_INDEX_OUT_OF_BOUNDS, table_index)
            table_slot = frame.values.pop_i32() & _I32_MASK
            table = self.tables[table_index]
            if table_slot >= len(table):
                return Trap(TrapCode.TABLE_INDEX_OUT_OF_BOUNDS, table_slot)
            entry = table[table_slot]
            if entry is None:
                return Trap(TrapCode.TABLE_SLOT_UNINITIALIZED, table_slot)
            callee_index = entry
            expected = self.module.type_at(expected_type_index)
            actual = self.module.func_type(callee_index)
            if expected.params != actual.params or expected.results != actual.results:
                return Trap(TrapCode.INDIRECT_CALL_TYPE_MISMATCH)

        function_type = self.module.func_type(callee_index)
        raw_arg_count = sum(value_slot_width(value_type) for value_type in function_type.params)
        if len(frame.values) < raw_arg_count:
            assert False, "validated call has fewer operands than parameters"
        raw_args = frame.values._words[-raw_arg_count:] if raw_arg_count else []
        frame.values.truncate(len(frame.values) - raw_arg_count)
        if self.module.is_import(callee_index):
            host_args = _decode_args(raw_args, function_type.params)
            host_results = self._env.runtime_api.invoke_import(callee_index, host_args)
            if host_results is None:
                return Trap(TrapCode.NO_HOST_HANDLER, callee_index)
            _push_results(frame.values, host_results, function_type.results)
            frame.ip = next_ip
            return None

        frame.ip = next_ip
        return self._enter_frame(call, callee_index, raw_args)

    def _return_frame(self, call: ReferenceCallState, frame: _ReferenceFrame) -> None:
        function_type = self.module.func_type(frame.func_index)
        result_types = function_type.results
        raw_result_count = sum(value_slot_width(value_type) for value_type in result_types)
        assert len(frame.values) >= raw_result_count
        raw_results = frame.values._words[-raw_result_count:] if raw_result_count else []
        call.context.call_frame_stack.pop()
        if not call.context.call_frame_stack:
            results = _decode_args(raw_results, result_types)
            call.results = StaticVector.of(results, capacity=max(1, len(results)))
            call.finished = True
            call.context.local_stack = []
            return
        caller = call.context.call_frame_stack[-1]
        call.context.local_stack = caller.locals
        for raw_word in raw_results:
            caller.values.push_back(raw_word)
        call.func_index = caller.func_index

    def _abort(self, call: ReferenceCallState, trap: Trap, ip: int) -> None:
        frame = call._frame
        if self.logger is not None and frame is not None:
            pc = RETURN_SENTINEL_PC if ip == RETURN_SENTINEL_IP else frame.code_pc_offset + ip
            self.logger.log_event(
                LogLevel.ERROR, LOG_EVT_TRAP_BASE + int(trap.code), pc, trap.detail
            )
        call.context.call_frame_stack.clear()
        call.context.local_stack = []
        call.finished = True
        call.results = None
        call.trap = trap


def _encode_args(values: Sequence[WasmNumber], value_types: Sequence[int]) -> list[int]:
    assert len(values) == len(value_types)
    raw_words: list[int] = []
    for value, value_type in zip(values, value_types, strict=True):
        if value_type == I64:
            raw = int(value) & _I64_MASK
            raw_words.extend((raw & _I32_MASK, raw >> 32))
        elif value_type == F32:
            raw_words.append(struct.unpack("<I", struct.pack("<f", float(value)))[0])
        elif value_type == F64:
            raw = struct.unpack("<Q", struct.pack("<d", float(value)))[0]
            raw_words.extend((raw & _I32_MASK, raw >> 32))
        else:
            assert value_type == I32
            raw_words.append(int(value) & _I32_MASK)
    return raw_words


def _decode_args(raw_words: list[int], value_types: Sequence[int]) -> list[WasmNumber]:
    values: list[WasmNumber] = []
    raw_index = 0
    for value_type in value_types:
        if value_type == I64:
            raw = raw_words[raw_index] | (raw_words[raw_index + 1] << 32)
            value = raw - (1 << 64) if raw & (1 << 63) else raw
            values.append(value)
            raw_index += 2
        elif value_type == F32:
            values.append(struct.unpack("<f", struct.pack("<I", raw_words[raw_index]))[0])
            raw_index += 1
        elif value_type == F64:
            raw = raw_words[raw_index] | (raw_words[raw_index + 1] << 32)
            values.append(struct.unpack("<d", struct.pack("<Q", raw))[0])
            raw_index += 2
        else:
            assert value_type == I32
            raw = raw_words[raw_index]
            values.append(raw - (1 << 32) if raw & 0x8000_0000 else raw)
            raw_index += 1
    assert raw_index == len(raw_words)
    return values


def _push_results(
    stack: _RawStack, values: Sequence[WasmNumber], value_types: Sequence[int]
) -> None:
    assert len(values) == len(value_types)
    for value, value_type in zip(values, value_types, strict=True):
        if value_type == I64:
            stack.push_i64(int(value))
        elif value_type == F32:
            stack.push_f32(float(value))
        elif value_type == F64:
            stack.push_f64(float(value))
        else:
            assert value_type == I32
            stack.push_i32(int(value))

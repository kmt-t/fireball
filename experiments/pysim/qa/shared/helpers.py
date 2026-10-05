"""Test helpers shared across pysim suites; kept outside every product tier."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from contextlib import contextmanager

from bump_allocator import BumpAllocator
from ipc_router import IPCMessage
from scheduler import Scheduler
from system_containers import StaticVector
from tier2_runtime.interpreter.interpreter import (
    Interpreter,
    InterpreterBindings,
    InterpreterCall,
    NativeInterpreter,
    WasmHostFunction,
    _Cont,
)
from tier2_runtime.memory.manager import MemoryManager
from tier2_runtime.observability.logger import Logger
from tier2_runtime.vmmio.controller import VMMIOController
from tier2_runtime.wasm.module import FunctionTable, Memory, Module

_WASM_SEC_TYPE = 1
_WASM_SEC_IMPORT = 2
_WASM_SEC_FUNCTION = 3
_WASM_SEC_MEMORY = 5
_WASM_SEC_GLOBAL = 6
_WASM_SEC_EXPORT = 7
_WASM_SEC_CODE = 10
_WASM_VAL_I32 = 0x7F
_WASM_EXTERNAL_FUNCTION = 0


def get_call_cont(call: InterpreterCall) -> _Cont:
    """Reconstruct a continuation only when QA inspects scalar call state."""
    frame = call._frame
    if frame is None:
        return None
    assert call._locals is not None
    return call._ip, frame, call._locals, call._tos


def _encode_leb128_u32(value: int) -> bytes:
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        output.append(byte)
        if value == 0:
            return bytes(output)


def _encode_leb128_s32(value: int) -> bytes:
    output = bytearray()
    more = True
    while more:
        byte = value & 0x7F
        value >>= 7
        if (value == 0 and (byte & 0x40) == 0) or (value == -1 and (byte & 0x40) != 0):
            more = False
        else:
            byte |= 0x80
        output.append(byte)
    return bytes(output)


def _build_test_wasm_binary(
    magic: bytes = b"\x00asm",
    version: int = 1,
    export_names: list[str] | None = None,
    corrupt_section_order: bool = False,
    corrupt_section_bounds: bool = False,
    invalid_type_idx: bool = False,
    memory_pages: int = 1,
) -> bytes:
    """Build the compact WASM fixture shared by parser and cross-tier tests."""
    output = bytearray(magic)
    output.extend(struct.pack("<I", version))

    type_payload = bytearray()
    type_payload.extend(_encode_leb128_u32(1))
    type_payload.append(0x60)
    type_payload.extend(_encode_leb128_u32(2))
    type_payload.extend([_WASM_VAL_I32, _WASM_VAL_I32])
    type_payload.extend(_encode_leb128_u32(1))
    type_payload.append(_WASM_VAL_I32)
    output.append(_WASM_SEC_TYPE)
    output.extend(_encode_leb128_u32(len(type_payload)))
    output.extend(type_payload)

    function_payload = bytearray()
    function_payload.extend(_encode_leb128_u32(1))
    function_payload.extend(_encode_leb128_u32(999 if invalid_type_idx else 0))
    output.append(_WASM_SEC_FUNCTION)
    output.extend(_encode_leb128_u32(len(function_payload)))
    output.extend(function_payload)

    memory_payload = bytearray()
    memory_payload.extend(_encode_leb128_u32(1))
    memory_payload.append(0x00)
    memory_payload.extend(_encode_leb128_u32(memory_pages))
    output.append(_WASM_SEC_MEMORY)
    output.extend(_encode_leb128_u32(len(memory_payload)))
    output.extend(memory_payload)

    global_payload = bytearray()
    global_payload.extend(_encode_leb128_u32(1))
    global_payload.append(_WASM_VAL_I32)
    global_payload.append(0x00)
    global_payload.append(0x41)
    global_payload.extend(_encode_leb128_s32(42))
    global_payload.append(0x0B)
    output.append(_WASM_SEC_GLOBAL)
    output.extend(_encode_leb128_u32(len(global_payload)))
    output.extend(global_payload)

    names = export_names or ["add", "main", "compute"]
    export_payload = bytearray()
    export_payload.extend(_encode_leb128_u32(len(names)))
    for name in names:
        encoded_name = name.encode("utf-8")
        export_payload.extend(_encode_leb128_u32(len(encoded_name)))
        export_payload.extend(encoded_name)
        export_payload.append(_WASM_EXTERNAL_FUNCTION)
        export_payload.extend(_encode_leb128_u32(0))
    if corrupt_section_order:
        output.append(_WASM_SEC_IMPORT)
        output.extend(_encode_leb128_u32(0))
    output.append(_WASM_SEC_EXPORT)
    output.extend(_encode_leb128_u32(len(export_payload)))
    output.extend(export_payload)

    code_body = bytearray()
    code_body.extend(_encode_leb128_u32(0))
    code_body.extend([0x20, 0x00, 0x20, 0x01, 0x6A, 0x0B])
    code_payload = bytearray()
    code_payload.extend(_encode_leb128_u32(1))
    code_payload.extend(_encode_leb128_u32(len(code_body)))
    code_payload.extend(code_body)
    output.append(_WASM_SEC_CODE)
    output.extend(_encode_leb128_u32(9999 if corrupt_section_bounds else len(code_payload)))
    output.extend(code_payload)
    return bytes(output)


def wat_to_wasm(wat_text: str) -> bytes:
    """Compile WAT through the QA-private wasmtime dependency."""
    import wasmtime

    return bytes(wasmtime.wat2wasm(wat_text))


def parse_single_function_module(code: bytes, results: tuple[int, ...] = ()) -> Module:
    """Load a single-function binary fixture through the product ROM parser."""
    from tier2_runtime.wasm.reader import parse

    type_payload = b"\x01\x60\x00" + _encode_leb128_u32(len(results)) + bytes(results)
    body = b"\x00" + code
    code_payload = b"\x01" + _encode_leb128_u32(len(body)) + body
    binary = (
        b"\x00asm\x01\x00\x00\x00"
        + bytes((_WASM_SEC_TYPE,))
        + _encode_leb128_u32(len(type_payload))
        + type_payload
        + b"\x03\x02\x01\x00"
        + bytes((_WASM_SEC_CODE,))
        + _encode_leb128_u32(len(code_payload))
        + code_payload
    )
    return parse(memoryview(binary))


def make_interpreter_bindings(
    module: Module,
    memory: bytearray | None = None,
    host_functions: StaticVector[WasmHostFunction | None] | None = None,
    imported_globals: StaticVector[int] | None = None,
    imported_tables: StaticVector[FunctionTable] | None = None,
    imported_memory: Memory | None = None,
) -> InterpreterBindings:
    """Build explicit runtime bindings for compact unit-test setup."""
    min_pages = 0
    if module.memory is not None:
        min_pages = module.memory.min_pages
    if module.memory_import is not None:
        min_pages = module.memory_import.min_limit
    if imported_memory is not None:
        min_pages = imported_memory.min_pages
    actual_memory = memory if memory is not None else bytearray(min_pages * 65536)
    actual_memory_decl = imported_memory
    if actual_memory_decl is None:
        actual_memory_decl = module.memory
    if actual_memory_decl is None:
        actual_memory_decl = Memory(min_pages=0, max_pages=None)
    actual_host_functions = host_functions
    if actual_host_functions is None:
        actual_host_functions = StaticVector.of(
            tuple(None for _ in range(len(module.imports))), capacity=len(module.imports)
        )
    actual_globals = imported_globals
    if actual_globals is None:
        actual_globals = StaticVector(capacity=0)
    actual_tables = imported_tables
    if actual_tables is None:
        actual_tables = StaticVector(capacity=0)
    return InterpreterBindings(
        memory=actual_memory,
        memory_decl=actual_memory_decl,
        imported_memory=imported_memory is not None,
        host_functions=actual_host_functions,
        globals=actual_globals,
        tables=actual_tables,
    )


def make_interpreter(
    module: Module,
    memory: bytearray | None = None,
    host_functions: StaticVector[WasmHostFunction | None] | None = None,
    vmmio: VMMIOController | None = None,
    phys_mem: bytearray | None = None,
    imported_globals: StaticVector[int] | None = None,
    imported_tables: StaticVector[FunctionTable] | None = None,
    imported_memory: Memory | None = None,
    logger: Logger | None = None,
) -> Interpreter:
    """Build the independent Python interpreter for test reference runs."""
    bindings = make_interpreter_bindings(
        module,
        memory,
        host_functions,
        imported_globals,
        imported_tables,
        imported_memory,
    )
    return Interpreter(module, bindings, vmmio=vmmio, phys_mem=phys_mem, logger=logger)


def make_native_interpreter(
    module: Module,
    memory: bytearray | None = None,
    host_functions: StaticVector[WasmHostFunction | None] | None = None,
    vmmio: VMMIOController | None = None,
    phys_mem: bytearray | None = None,
    imported_globals: StaticVector[int] | None = None,
    imported_tables: StaticVector[FunctionTable] | None = None,
    imported_memory: Memory | None = None,
    logger: Logger | None = None,
    bump_allocator: BumpAllocator | None = None,
) -> NativeInterpreter:
    """Build the independent C++ interpreter for RuntimeEngine test runs."""
    bindings = make_interpreter_bindings(
        module,
        memory,
        host_functions,
        imported_globals,
        imported_tables,
        imported_memory,
    )
    return NativeInterpreter(
        module,
        bindings,
        vmmio=vmmio,
        phys_mem=phys_mem,
        logger=logger,
        bump_allocator=bump_allocator,
    )


@contextmanager
def expect_assertion(message: str = "") -> Iterator[None]:
    """Require one assertion from the operation inside this context."""
    try:
        yield
    except AssertionError as error:
        if message:
            assert message in str(error)
    else:
        raise AssertionError("expected the operation to raise AssertionError")


def make_test_ipc_message(
    entries: tuple[tuple[int, int], ...] | list[tuple[int, int]] = (),
    memory_manager: MemoryManager | None = None,
) -> IPCMessage:
    """Builds IPC storage through the Tier 2 memory adapter for tests only."""
    from tier2_runtime.memory.manager import FB_CONF_MEMORY_POOL_SIZE

    if memory_manager is None:
        scheduler = Scheduler()
        task_id = scheduler.spawn("test_message_owner")
        task = scheduler.get_task(task_id)
        assert task is not None
        manager = MemoryManager(scheduler)
        assert manager.init_manager(0x00010000, FB_CONF_MEMORY_POOL_SIZE).is_ok
        with scheduler.task_context(task):
            return IPCMessage.from_entries(entries, memory_manager=manager)
    message = IPCMessage.from_entries(entries, memory_manager=memory_manager)
    return message

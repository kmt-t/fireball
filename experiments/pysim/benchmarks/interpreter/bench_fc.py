"""Measure the selected WASM 0xFC paths in the PySIM interpreter.

The native path requires the Clang-built ``_interpreter_native`` extension.
Measurements compare that path with the Python handler reference. Non-linear
``memory.copy`` endpoints intentionally return to Python and call the injected
vDMA service; their timings describe the PySIM boundary and service together.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import median
from typing import TypedDict

_PYSIM_DIR = Path(__file__).resolve()
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent

_BENCH_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BENCH_DIR))
from _bootstrap import configure_import_paths

configure_import_paths(_PYSIM_DIR, _BENCH_DIR)

from system import System
from system_containers import StaticVector
from tier2_runtime.hal_dispatch import HalBufferMapStatus
from tier2_runtime.hostcall import VdmaTransfer
from tier2_runtime.vmmio import FC_DYNAMIC, FC_PASSTHROUGH, FC_SHM
from tier2_runtime.wasm_module import Memory, Module
from tier2_runtime.wasm_reader import parse
from tier3_executer.interpreter.interpreter import (
    Interpreter,
    InterpreterBindings,
    WasmNumber,
)

PAGE_SIZE = 65_536
WARMUP_ITERATIONS = 64
CONVERSION_ITERATIONS = 2_048
COPY_CASES = ((16, 8_192), (256, 2_048), (4_096, 128))
FILL_CASES = ((16, 8_192), (256, 2_048), (4_096, 128))
VDMA_ITERATIONS = 512
ROUNDS = 3

_TYPE_I32 = 0x7F
_TYPE_I64 = 0x7E
_TYPE_F32 = 0x7D
_TYPE_F64 = 0x7C
_OP_LOCAL_GET = 0x20
_OP_LOCAL_SET = 0x21
_OP_BLOCK = 0x02
_OP_LOOP = 0x03
_OP_BR = 0x0C
_OP_BR_IF = 0x0D
_OP_DROP = 0x1A
_OP_I32_EQZ = 0x45
_OP_I32_CONST = 0x41
_OP_I32_SUB = 0x6B
_OP_FC_PREFIX = 0xFC
_OP_FC_MEMORY_COPY = 0x0A
_OP_FC_MEMORY_FILL = 0x0B


class ConversionMeasurement(TypedDict):
    subopcode: int
    iterations: int
    native_ns_per_instruction: float
    python_ns_per_instruction: float
    native_over_python_ratio: float


class MemoryMeasurement(TypedDict):
    length_bytes: int
    iterations: int
    native_ns_per_copy: float
    python_ns_per_copy: float
    native_over_python_ratio: float


class FillMeasurement(TypedDict):
    length_bytes: int
    iterations: int
    native_ns_per_fill: float
    python_ns_per_fill: float
    native_over_python_ratio: float


class VdmaMeasurement(TypedDict):
    route: str
    source_function_code: int
    destination_function_code: int
    iterations: int
    native_enabled_api_ns_per_transfer: float
    python_reference_ns_per_transfer: float
    vdma_calls_per_run: int


class HostInfo(TypedDict):
    system: str
    release: str
    machine: str
    processor: str
    python: str


class MeasurementSettings(TypedDict):
    rounds: int
    statistic: str
    iterations_scale: int


class SemanticChecks(TypedDict):
    saturating_conversion_cases: int
    linear_memory_copy_fill_cases: int
    vdma_routes: int


class BenchmarkReport(TypedDict):
    benchmark_id: str
    host: HostInfo
    native_extension: str
    measurement: MeasurementSettings
    semantic_checks: SemanticChecks
    saturating_conversion: list[ConversionMeasurement]
    linear_memory_copy: list[MemoryMeasurement]
    linear_memory_fill: list[FillMeasurement]
    vdma_memory_copy: list[VdmaMeasurement]


def _encode_u32(value: int) -> bytes:
    assert value >= 0
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _section(section_id: int, payload: bytes) -> bytes:
    return bytes((section_id,)) + _encode_u32(len(payload)) + payload


def _function_type(params: tuple[int, ...], results: tuple[int, ...]) -> bytes:
    return (
        b"\x60"
        + _encode_u32(len(params))
        + bytes(params)
        + _encode_u32(len(results))
        + bytes(results)
    )


def _single_conversion_body(subopcode: int) -> bytes:
    return b"\x00" + bytes((_OP_LOCAL_GET, 0, _OP_FC_PREFIX, subopcode, 0x0B))


def _loop_body(param_count: int, operation: bytes) -> bytes:
    local_index = param_count
    instructions = bytearray(
        (
            _OP_LOCAL_GET,
            param_count - 1,
            _OP_LOCAL_SET,
            local_index,
            _OP_BLOCK,
            0x40,
            _OP_LOOP,
            0x40,
            _OP_LOCAL_GET,
            local_index,
            _OP_I32_EQZ,
            _OP_BR_IF,
            1,
        )
    )
    instructions.extend(operation)
    instructions.extend(
        (
            _OP_LOCAL_GET,
            local_index,
            _OP_I32_CONST,
            1,
            _OP_I32_SUB,
            _OP_LOCAL_SET,
            local_index,
            _OP_BR,
            0,
            0x0B,
            0x0B,
            0x0B,
        )
    )
    local_declarations = b"\x01\x01" + bytes((_TYPE_I32,))
    return local_declarations + bytes(instructions)


def _conversion_loop_body(subopcode: int, counter_parameter: int) -> bytes:
    operation = bytes((_OP_LOCAL_GET, 0, _OP_FC_PREFIX, subopcode, _OP_DROP))
    return _loop_body(counter_parameter + 1, operation)


def _memory_copy_body(counter_parameter: int) -> bytes:
    operation = bytes(
        (
            _OP_LOCAL_GET,
            0,
            _OP_LOCAL_GET,
            1,
            _OP_LOCAL_GET,
            2,
            _OP_FC_PREFIX,
            _OP_FC_MEMORY_COPY,
            0,
            0,
        )
    )
    return _loop_body(counter_parameter + 1, operation)


def _memory_fill_body(counter_parameter: int) -> bytes:
    operation = bytes(
        (
            _OP_LOCAL_GET,
            0,
            _OP_LOCAL_GET,
            1,
            _OP_LOCAL_GET,
            2,
            _OP_FC_PREFIX,
            _OP_FC_MEMORY_FILL,
            0,
        )
    )
    return _loop_body(counter_parameter + 1, operation)


def _encode_body(body: bytes) -> bytes:
    return _encode_u32(len(body)) + body


def _benchmark_module() -> Module:
    types = (
        _function_type((_TYPE_F32,), (_TYPE_I32,)),
        _function_type((_TYPE_F64,), (_TYPE_I32,)),
        _function_type((_TYPE_F32,), (_TYPE_I64,)),
        _function_type((_TYPE_F64,), (_TYPE_I64,)),
        _function_type((_TYPE_F32, _TYPE_I32), ()),
        _function_type((_TYPE_F64, _TYPE_I32), ()),
        _function_type((_TYPE_I32, _TYPE_I32, _TYPE_I32), ()),
        _function_type((_TYPE_I32, _TYPE_I32, _TYPE_I32, _TYPE_I32), ()),
    )
    function_types = (0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 4, 4, 5, 5, 6, 7, 6, 7)
    bodies = [_single_conversion_body(subopcode) for subopcode in range(8)]
    bodies.extend(_conversion_loop_body(subopcode, 1) for subopcode in range(8))
    bodies.extend(
        (
            b"\x00"
            + bytes(
                (
                    _OP_LOCAL_GET,
                    0,
                    _OP_LOCAL_GET,
                    1,
                    _OP_LOCAL_GET,
                    2,
                    _OP_FC_PREFIX,
                    _OP_FC_MEMORY_COPY,
                    0,
                    0,
                    0x0B,
                )
            ),
            _memory_copy_body(3),
            b"\x00"
            + bytes(
                (
                    _OP_LOCAL_GET,
                    0,
                    _OP_LOCAL_GET,
                    1,
                    _OP_LOCAL_GET,
                    2,
                    _OP_FC_PREFIX,
                    _OP_FC_MEMORY_FILL,
                    0,
                    0x0B,
                )
            ),
            _memory_fill_body(3),
        )
    )
    type_payload = _encode_u32(len(types)) + b"".join(types)
    function_payload = _encode_u32(len(function_types)) + b"".join(
        _encode_u32(index) for index in function_types
    )
    memory_payload = b"\x01\x00\x01"
    code_payload = _encode_u32(len(bodies)) + b"".join(_encode_body(body) for body in bodies)
    binary = (
        b"\x00asm\x01\x00\x00\x00"
        + _section(1, type_payload)
        + _section(3, function_payload)
        + _section(5, memory_payload)
        + _section(10, code_payload)
    )
    module = parse(memoryview(binary))
    assert len(module.functions) == len(function_types)
    assert module.memory is not None and module.memory.min_pages == 1
    return module


class VdmaModel:
    """Own a real PySIM System and use its vMMIO-backed vDMA transfer service."""

    __slots__ = ("calls", "last_transfer", "linear_memory", "shared_block", "system")

    def __init__(self, linear_memory: bytearray):
        from tier2_runtime.memory import SharedBlock

        self.calls = 0
        self.last_transfer: tuple[int, int, int] | None = None
        self.linear_memory = linear_memory
        self.system = System()
        self.shared_block: SharedBlock | None = None
        self.system.bind_runtime(linear_memory)
        assert self.system.pool.map_for_io(1) == HalBufferMapStatus.MAPPED
        shared_result = self.system.memory_manager.allocate_shared(1024)
        assert shared_result.is_ok
        self.shared_block = shared_result.unwrap()
        for region, function_code in (
            ("linear", 0),
            ("dynamic", FC_DYNAMIC),
            ("shm", FC_SHM),
            ("passthrough", FC_PASSTHROUGH),
        ):
            address = self.address(region)
            view = self.backing_view(address, 64, is_write=True)
            view[:] = bytes((index + function_code * 17) & 0xFF for index in range(64))

    def address(self, region: str) -> int:
        offset = 0x100
        if region == "linear":
            return offset
        if region == "dynamic":
            return self.system.pool.buffer(1).virtual_address + offset
        if region == "shm":
            assert self.shared_block is not None
            return (FC_SHM << 28) | (self.shared_block.page_idx << 12) | offset
        assert region == "passthrough"
        return (FC_PASSTHROUGH << 28) | (2 << 12) | offset

    def backing_view(self, address: int, length: int, is_write: bool) -> memoryview:
        if address >> 31 == 0:
            assert address + length <= len(self.linear_memory)
            return memoryview(self.linear_memory)[address : address + length]
        backing, offset = self.system._vdma_region(address, length, is_write)
        assert backing is not None and offset is not None
        return memoryview(backing)[offset : offset + length]

    def transfer(self, source: int, destination: int, length: int) -> int:
        self.calls += 1
        self.last_transfer = (source, destination, length)
        return self.system.vdma_transfer(source, destination, length)

    def close(self) -> None:
        self.system.pool.unmap_after_io(1)
        if self.shared_block is not None:
            self.shared_block.drop()
            self.shared_block = None
        self.system.unbind_runtime()
        self.system.shutdown()


def _bindings(memory: bytearray, vdma_transfer: VdmaTransfer | None = None) -> InterpreterBindings:
    return InterpreterBindings(
        memory=memory,
        memory_decl=Memory(min_pages=0, max_pages=None),
        imported_memory=False,
        host_functions=StaticVector(capacity=0),
        globals=StaticVector(capacity=0),
        tables=StaticVector(capacity=0),
        vdma_transfer=vdma_transfer,
    )


def _call_python(
    interpreter: Interpreter, function_index: int, arguments: Sequence[WasmNumber]
) -> StaticVector[WasmNumber]:
    call_state = interpreter.start(function_index, arguments)
    while not call_state.finished:
        call_state = interpreter._step(call_state, stop_at_boundary=False)
    assert call_state.trap is None, call_state.trap
    assert call_state.results is not None
    return call_state.results


def _invoke(
    interpreter: Interpreter,
    function_index: int,
    arguments: Sequence[WasmNumber],
    native: bool,
) -> StaticVector[WasmNumber]:
    if native:
        return interpreter.call(function_index, arguments)
    return _call_python(interpreter, function_index, arguments)


def _measure(
    module: Module,
    function_index: int,
    arguments: Sequence[WasmNumber],
    iterations: int,
    native: bool,
    memory_factory: Callable[[], bytearray],
    transfer_factory: Callable[[bytearray], VdmaModel | None],
    validate: Callable[[Interpreter, VdmaModel | None, int], None],
) -> float:
    warm_memory = memory_factory()
    warm_transfer = transfer_factory(warm_memory)
    warm_interpreter = Interpreter(
        module,
        _bindings(warm_memory, warm_transfer.transfer if warm_transfer is not None else None),
    )
    warm_arguments = (*arguments[:-1], WARMUP_ITERATIONS)
    try:
        warm_result = _invoke(warm_interpreter, function_index, warm_arguments, native)
        assert len(warm_result) == 0
        validate(warm_interpreter, warm_transfer, WARMUP_ITERATIONS)
    finally:
        if warm_transfer is not None:
            warm_transfer.close()

    samples: list[float] = []
    for _ in range(ROUNDS):
        memory = memory_factory()
        transfer = transfer_factory(memory)
        interpreter = Interpreter(
            module,
            _bindings(memory, transfer.transfer if transfer is not None else None),
        )
        try:
            started = time.perf_counter()
            result = _invoke(interpreter, function_index, arguments, native)
            elapsed = time.perf_counter() - started
            assert len(result) == 0
            validate(interpreter, transfer, iterations)
            samples.append(elapsed * 1e9 / iterations)
        finally:
            if transfer is not None:
                transfer.close()
    return median(samples)


def _verify_conversion_semantics(module: Module) -> int:
    cases: tuple[tuple[int, float, int], ...] = (
        (0, float("nan"), 0),
        (0, float("inf"), 2_147_483_647),
        (0, float("-inf"), -2_147_483_648),
        (0, 2_147_483_648.0, 2_147_483_647),
        (0, -2_147_483_904.0, -2_147_483_648),
        (0, -1.75, -1),
        (1, float("nan"), 0),
        (1, float("inf"), -1),
        (1, float("-inf"), 0),
        (1, 4_294_967_296.0, -1),
        (1, -1.75, 0),
        (2, float("inf"), 2_147_483_647),
        (2, float("-inf"), -2_147_483_648),
        (2, 2_147_483_648.0, 2_147_483_647),
        (2, -2_147_483_649.0, -2_147_483_648),
        (2, -1.75, -1),
        (3, float("nan"), 0),
        (3, float("inf"), -1),
        (3, 4_294_967_296.0, -1),
        (3, -1.75, 0),
        (4, float("inf"), 9_223_372_036_854_775_807),
        (4, float("-inf"), -9_223_372_036_854_775_808),
        (4, 9_223_372_036_854_775_808.0, 9_223_372_036_854_775_807),
        (4, -9_223_372_036_854_775_808.0, -9_223_372_036_854_775_808),
        (4, -1.75, -1),
        (5, float("nan"), 0),
        (5, float("inf"), -1),
        (5, 18_446_744_073_709_551_616.0, -1),
        (5, -1.75, 0),
        (6, float("inf"), 9_223_372_036_854_775_807),
        (6, float("-inf"), -9_223_372_036_854_775_808),
        (6, 9_223_372_036_854_775_808.0, 9_223_372_036_854_775_807),
        (6, -9_223_372_036_854_775_808.0, -9_223_372_036_854_775_808),
        (6, -1.75, -1),
        (7, float("nan"), 0),
        (7, float("inf"), -1),
        (7, 18_446_744_073_709_551_616.0, -1),
        (7, -1.75, 0),
    )
    memory = bytearray(PAGE_SIZE)
    verified = 0
    for subopcode, value, expected in cases:
        native = Interpreter(module, _bindings(memory)).call(subopcode, (value,))
        reference = _call_python(Interpreter(module, _bindings(memory)), subopcode, (value,))
        assert len(native) == 1 and len(reference) == 1
        assert native[0] == reference[0] == expected, (subopcode, value, native, reference)
        verified += 1
    return verified


def _verify_linear_memory_semantics(module: Module) -> int:
    verified = 0
    for native in (False, True):
        memory = bytearray(PAGE_SIZE)
        memory[:64] = bytes(range(64))
        interpreter = Interpreter(module, _bindings(memory))
        original = bytes(memory)
        _invoke(interpreter, 16, (8, 4, 32), native)
        expected = bytearray(original)
        expected[8:40] = original[4:36]
        assert memory == expected

        _invoke(interpreter, 18, (48, 0x1AB, 16), native)
        assert memory[48:64] == bytes((0xAB,)) * 16
        verified += 2
    return verified


def _linear_copy_validator(
    length: int,
) -> Callable[[Interpreter, VdmaModel | None, int], None]:
    def validate(interpreter: Interpreter, transfer: VdmaModel | None, _iterations: int) -> None:
        assert transfer is None
        assert interpreter.memory is not None
        assert interpreter.memory[32_768 : 32_768 + length] == bytes(
            (index * 13 + 7) & 0xFF for index in range(length)
        )

    return validate


def _linear_fill_validator(
    destination: int, value: int, length: int
) -> Callable[[Interpreter, VdmaModel | None, int], None]:
    def validate(interpreter: Interpreter, transfer: VdmaModel | None, _iterations: int) -> None:
        assert transfer is None
        assert interpreter.memory is not None
        assert (
            interpreter.memory[destination : destination + length]
            == bytes((value & 0xFF,)) * length
        )

    return validate


def _vmmio_routes() -> tuple[tuple[str, str, str], ...]:
    return (
        ("linear_to_dynamic", "linear", "dynamic"),
        ("shm_to_linear", "shm", "linear"),
        ("dynamic_to_passthrough", "dynamic", "passthrough"),
        ("passthrough_to_shm", "passthrough", "shm"),
    )


def _vdma_validator(
    expected_source: int, expected_destination: int, length: int
) -> Callable[[Interpreter, VdmaModel | None, int], None]:
    def validate(interpreter: Interpreter, transfer: VdmaModel | None, iterations: int) -> None:
        assert transfer is not None
        assert transfer.calls == iterations
        assert transfer.last_transfer is not None
        source, destination, transfer_length = transfer.last_transfer
        assert (source, destination) == (expected_source, expected_destination)
        assert transfer_length == length
        source_view = transfer.backing_view(source, length, is_write=False)
        destination_view = transfer.backing_view(destination, length, is_write=True)
        source_fc = (source >> 28) & 0xF
        expected_payload = bytes((index + source_fc * 17) & 0xFF for index in range(length))
        assert source_view == expected_payload
        assert destination_view == expected_payload

    return validate


def run_benchmarks(iterations_scale: int = 1) -> BenchmarkReport:
    assert iterations_scale > 0
    module = _benchmark_module()
    semantic_conversion_cases = _verify_conversion_semantics(module)
    semantic_memory_cases = _verify_linear_memory_semantics(module)
    conversion_results: list[ConversionMeasurement] = []
    for subopcode in range(8):
        counter = CONVERSION_ITERATIONS * iterations_scale
        arguments: tuple[WasmNumber, ...] = (1.25, counter)
        if subopcode in (2, 3, 6, 7):
            arguments = (-1.25, counter)
        function_index = 8 + subopcode
        native_ns = _measure(
            module,
            function_index,
            arguments,
            counter,
            True,
            lambda: bytearray(PAGE_SIZE),
            lambda _memory: None,
            lambda _interpreter, transfer, _count: assert_no_vdma(transfer),
        )
        python_ns = _measure(
            module,
            function_index,
            arguments,
            counter,
            False,
            lambda: bytearray(PAGE_SIZE),
            lambda _memory: None,
            lambda _interpreter, transfer, _count: assert_no_vdma(transfer),
        )
        conversion_results.append(
            {
                "subopcode": subopcode,
                "iterations": counter,
                "native_ns_per_instruction": native_ns,
                "python_ns_per_instruction": python_ns,
                "native_over_python_ratio": native_ns / python_ns,
            }
        )

    linear_copy_results: list[MemoryMeasurement] = []
    linear_fill_results: list[FillMeasurement] = []
    for length, base_iterations in COPY_CASES:
        iterations = base_iterations * iterations_scale
        memory = bytearray(PAGE_SIZE)
        memory[1024 : 1024 + length] = bytes((index * 13 + 7) & 0xFF for index in range(length))
        arguments = (32_768, 1024, length, iterations)
        copy_validator = _linear_copy_validator(length)
        native_ns = _measure(
            module,
            17,
            arguments,
            iterations,
            True,
            lambda: bytearray(memory),
            lambda _memory: None,
            copy_validator,
        )
        python_ns = _measure(
            module,
            17,
            arguments,
            iterations,
            False,
            lambda: bytearray(memory),
            lambda _memory: None,
            copy_validator,
        )
        linear_copy_results.append(
            {
                "length_bytes": length,
                "iterations": iterations,
                "native_ns_per_copy": native_ns,
                "python_ns_per_copy": python_ns,
                "native_over_python_ratio": native_ns / python_ns,
            }
        )

    for length, base_iterations in FILL_CASES:
        iterations = base_iterations * iterations_scale
        destination = 32_768
        value = 0x1AB
        arguments = (destination, value, length, iterations)
        fill_validator = _linear_fill_validator(destination, value, length)
        native_ns = _measure(
            module,
            19,
            arguments,
            iterations,
            True,
            lambda: bytearray(PAGE_SIZE),
            lambda _memory: None,
            fill_validator,
        )
        python_ns = _measure(
            module,
            19,
            arguments,
            iterations,
            False,
            lambda: bytearray(PAGE_SIZE),
            lambda _memory: None,
            fill_validator,
        )
        linear_fill_results.append(
            {
                "length_bytes": length,
                "iterations": iterations,
                "native_ns_per_fill": native_ns,
                "python_ns_per_fill": python_ns,
                "native_over_python_ratio": native_ns / python_ns,
            }
        )

    vdma_results: list[VdmaMeasurement] = []
    route_probe = VdmaModel(bytearray(PAGE_SIZE))
    route_addresses = {
        region: route_probe.address(region)
        for region in ("linear", "dynamic", "shm", "passthrough")
    }
    route_probe.close()
    for label, source_region, destination_region in _vmmio_routes():
        source = route_addresses[source_region]
        destination = route_addresses[destination_region]
        iterations = VDMA_ITERATIONS * iterations_scale
        arguments = (destination, source, 64, iterations)
        validator = _vdma_validator(source, destination, 64)
        native_ns = _measure(
            module,
            17,
            arguments,
            iterations,
            True,
            lambda: bytearray(PAGE_SIZE),
            lambda memory: VdmaModel(memory),
            validator,
        )
        python_ns = _measure(
            module,
            17,
            arguments,
            iterations,
            False,
            lambda: bytearray(PAGE_SIZE),
            lambda memory: VdmaModel(memory),
            validator,
        )
        vdma_results.append(
            {
                "route": label,
                "source_function_code": (source >> 28) & 0xF,
                "destination_function_code": (destination >> 28) & 0xF,
                "iterations": iterations,
                "native_enabled_api_ns_per_transfer": native_ns,
                "python_reference_ns_per_transfer": python_ns,
                "vdma_calls_per_run": iterations,
            }
        )

    return {
        "benchmark_id": "PYSIM-INTP-FC-01",
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": _cpu_model(),
            "python": platform.python_version(),
        },
        "native_extension": "Clang-built _interpreter_native",
        "measurement": {
            "rounds": ROUNDS,
            "statistic": "median",
            "iterations_scale": iterations_scale,
        },
        "semantic_checks": {
            "saturating_conversion_cases": semantic_conversion_cases,
            "linear_memory_copy_fill_cases": semantic_memory_cases,
            "vdma_routes": len(_vmmio_routes()),
        },
        "saturating_conversion": conversion_results,
        "linear_memory_copy": linear_copy_results,
        "linear_memory_fill": linear_fill_results,
        "vdma_memory_copy": vdma_results,
    }


def assert_no_vdma(transfer: VdmaModel | None) -> None:
    assert transfer is None


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() in ("model name", "Hardware"):
                return value.strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def main() -> None:
    print(json.dumps(run_benchmarks(), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

"""Collect QA-only JIT trace and constant-pool diagnostics without timing them."""

from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PYSIM_DIR = ROOT / "experiments/pysim"
BENCH_DIR = PYSIM_DIR / "benchmarks"
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "aobench"))
sys.path.insert(0, str(PYSIM_DIR / "qa"))
sys.path.insert(0, str(PYSIM_DIR))

from _bootstrap import configure_import_paths

configure_import_paths(PYSIM_DIR, BENCH_DIR)

from bump_allocator import BumpAllocator
from ipc_router import FB_URI_HAL_STDOUT
from qa.private import jit_native_abi
from qa.private.jit_native_abi import NativeConstPoolDiagnostics, TraceQaState, compile_wasm
from qa.shared.jit_manager import JITRuntimeManager as QaJitRuntimeManager
from qa.shared.runtime_stats import RuntimeStatsEngine
from system import System
from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
from tier2_runtime.wasm.reader import parse, parse_standalone
from tier3_platform.drivers.hal.dummy import DummyDriver
from tier3_platform.drivers.hal.stream import StreamTransport
from tier3_platform.drivers.wasi.context import WasiHostContext

# JSON output is limited to concrete scalar, sequence, and mapping values.
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def diagnostics() -> dict[str, JsonValue]:
    result = NativeConstPoolDiagnostics()
    jit_native_abi.CONST_POOL_DIAGNOSTICS_READ(ctypes.byref(result))
    opcode_counts = {
        (f"0x{index:02x}" if index < 256 else f"0xfc:{index - 256:02x}"): count
        for index, count in enumerate(result.runtime_api_opcodes)
        if count
    }
    item_bytes = {
        "trace_header_references": int(result.item_bytes[0]),
        "slot_and_immediate_count": int(result.item_bytes[1]),
        "source_ip": int(result.item_bytes[2]),
        "runtime_immediates": int(result.item_bytes[3]),
        "duplicated_interpreter_annotations": int(result.item_bytes[4]),
        "runtime_immediate_span_bytes": int(result.item_bytes[5]),
        "interpreter_annotation_offsets": int(result.item_bytes[6]),
    }
    return {
        "trace_count": int(result.trace_count),
        "instruction_count": int(result.instruction_count),
        "average_instructions_per_trace": (
            int(result.instruction_count) / int(result.trace_count) if result.trace_count else 0.0
        ),
        "runtime_api_count": int(result.runtime_api_count),
        "runtime_api_opcodes": opcode_counts,
        "item_bytes": item_bytes,
        "pool_bytes": sum(item_bytes.values()),
    }


def measure_aobench() -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    wasm_path = BENCH_DIR / "aobench/aobench.wasm"
    wasm = wasm_path.read_bytes()
    wasm_hash = hashlib.sha256(wasm).hexdigest()
    for width in (64, 128, 192, 256, 320):
        height = width * 9 // 16
        module = parse_standalone(memoryview(wasm))
        system = System()
        system.transport = StreamTransport(1 << 20)
        wasi = WasiHostContext.create_default(system)
        system.start_hal_driver(
            DummyDriver(system.pool, transport=system.transport), FB_URI_HAL_STDOUT
        )
        functions = wasi.build_interpreter_host_functions(module)
        module.init_memory_data(wasi.guest_memory, ())
        plugin = QaJitRuntimeManager()
        runtime = RuntimeStatsEngine(jit_runtime=plugin, collect_runtime_stats=False)
        runtime.register_module_blocks(module)
        interpreter = NativeInterpreter(
            module,
            InterpreterBindings.with_memory_and_functions(wasi.guest_memory, functions),
            bump_allocator=runtime.bump_allocator,
            yield_threshold=4096,
        )
        main_index = module.export_func_index("main")
        jit_native_abi.CONST_POOL_DIAGNOSTICS_RESET()
        output_hash = ""
        for frame_index in range(4):
            if frame_index == 3:
                plugin.idle_hook()
            runtime.call(
                interpreter,
                main_index,
                [width, height],
            )
            frame = system.transport.drain_output()
            assert len(frame) == (width + 1) * height
            current_hash = hashlib.sha256(frame).hexdigest()
            if frame_index == 0:
                output_hash = current_hash
            assert current_hash == output_hash
        rows.append(
            {
                "width": width,
                "height": height,
                "frame_sha256": output_hash,
                "wasm_sha256": wasm_hash,
                "diagnostics": diagnostics(),
            }
        )
        plugin.close()
    return rows


def measure_other_module() -> dict[str, JsonValue]:
    module_path = PYSIM_DIR / "benchmarks/profile/guest/suite.wasm"
    wasm = module_path.read_bytes()
    module = parse(memoryview(wasm), allocator=BumpAllocator(8 * 1024 * 1024))
    manager = QaJitRuntimeManager(candidate_threshold=0)
    manager.register_module(module)
    jit_native_abi.CONST_POOL_DIAGNOSTICS_RESET()
    attempted = 0
    compiled = 0
    for block in module.blocks:
        if block.byte_span == 0:
            continue
        attempted += 1
        trace = TraceQaState()
        if compile_wasm(manager._block_input(block.head_pc, block), trace) is not None:
            compiled += 1
    manager.close()
    return {
        "path": str(module_path.relative_to(ROOT)),
        "wasm_sha256": hashlib.sha256(wasm).hexdigest(),
        "blocks_attempted": attempted,
        "traces_compiled": compiled,
        "diagnostics": diagnostics(),
    }


def main() -> None:
    assert jit_native_abi.BLOCK_COUNTERS_ENABLED() == 1
    output = {
        "diagnostic_build": str(jit_native_abi._NATIVE_LIBRARY_PATH),
        "diagnostic_library_sha256": hashlib.sha256(
            jit_native_abi._NATIVE_LIBRARY_PATH.read_bytes()
        ).hexdigest(),
        "aobench": measure_aobench(),
        "other_module_compile_all_blocks": measure_other_module(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

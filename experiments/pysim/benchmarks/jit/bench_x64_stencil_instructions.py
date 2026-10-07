"""Count native instructions in JIT traces for Clang-profiled local-pair n-grams."""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
PYSIM_DIR = REPO_ROOT / "experiments/pysim"
BENCHMARK_DIR = PYSIM_DIR / "benchmarks"
PROFILE_DIR = BENCHMARK_DIR / "profile/guest"
PROFILE_SOURCE = PROFILE_DIR / "suite.c"
SUPPORTED_OPERATIONS = (
    "i32.add",
    "i32.sub",
    "i32.mul",
    "i32.and",
    "i32.or",
    "i32.xor",
    "i32.eq",
    "i32.ne",
    "i32.lt_s",
    "i32.lt_u",
    "i32.gt_s",
    "i32.gt_u",
    "i32.le_s",
    "i32.le_u",
    "i32.ge_s",
    "i32.ge_u",
)
INSTRUCTION_LINE = re.compile(r"^\s*[0-9a-f]+:\s+(?:[0-9a-f]{2}\s+)+[a-z]", re.IGNORECASE)


def compile_profile_wasm(clang: str, output: Path) -> None:
    subprocess.run(
        [
            clang,
            "--target=wasm32",
            "-std=c23",
            "-mcpu=mvp",
            "-O2",
            "-ffreestanding",
            "-fno-builtin",
            "-nostdlib",
            "-Wl,--no-entry",
            "-Wl,--strip-all",
            "-Wl,-z,stack-size=4096",
            "-Wl,--initial-memory=65536",
            "-Wl,--max-memory=65536",
            "-o",
            str(output),
            str(PROFILE_SOURCE),
        ],
        check=True,
    )


def profile_frequencies(wasm: Path, wasm_objdump: str) -> Counter[str]:
    sys.path.insert(0, str(PROFILE_DIR))
    import analyze_wasm_ngrams

    functions = analyze_wasm_ngrams.read_functions(wasm, wasm_objdump)
    frequencies: Counter[str] = Counter()
    for function in functions:
        for index in range(len(function) - 2):
            if function[index][0] != "local.get" or function[index + 1][0] != "local.get":
                continue
            operation = function[index + 2][0]
            if operation in SUPPORTED_OPERATIONS:
                frequencies[operation] += 1
    return frequencies


def signed_i32(value: int) -> int:
    value &= 0xFFFF_FFFF
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value


def expected_result(operation: str, left: int, right: int) -> int:
    if operation == "i32.add":
        return (left + right) & 0xFFFF_FFFF
    if operation == "i32.sub":
        return (left - right) & 0xFFFF_FFFF
    if operation == "i32.mul":
        return (left * right) & 0xFFFF_FFFF
    if operation == "i32.and":
        return left & right
    if operation == "i32.or":
        return left | right
    if operation == "i32.xor":
        return left ^ right
    if operation == "i32.eq":
        return int(left == right)
    if operation == "i32.ne":
        return int(left != right)
    if operation == "i32.lt_s":
        return int(signed_i32(left) < signed_i32(right))
    if operation == "i32.lt_u":
        return int(left < right)
    if operation == "i32.gt_s":
        return int(signed_i32(left) > signed_i32(right))
    if operation == "i32.gt_u":
        return int(left > right)
    if operation == "i32.le_s":
        return int(signed_i32(left) <= signed_i32(right))
    if operation == "i32.le_u":
        return int(left <= right)
    if operation == "i32.ge_s":
        return int(signed_i32(left) >= signed_i32(right))
    if operation == "i32.ge_u":
        return int(left >= right)
    raise ValueError(f"unsupported operation: {operation}")


def disassembled_instruction_count(objdump: str, body: bytes) -> int:
    with tempfile.NamedTemporaryFile() as binary:
        binary.write(body)
        binary.flush()
        disassembly = subprocess.run(
            [objdump, "-D", "-b", "binary", "-m", "i386:x86-64", "-M", "intel", binary.name],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return sum(1 for line in disassembly.splitlines() if INSTRUCTION_LINE.match(line))


def measurement_rows(output: str) -> dict[str, tuple[int, int, int, int, int]]:
    rows: dict[str, tuple[int, int, int, int, int]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 6 or fields[0] not in SUPPORTED_OPERATIONS:
            continue
        static_ngrams, trace_bytes, body_bytes, body_instructions, executions = (
            int(value) for value in fields[1:]
        )
        rows[fields[0]] = (
            static_ngrams,
            trace_bytes,
            body_bytes,
            body_instructions,
            executions,
        )
    assert rows, "measurement output contains no instruction rows"
    return rows


def run_measurement_subprocess(
    compiler_library: Path, clang: str, wasm_objdump: str, objdump: str
) -> str:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--compiler-library",
            str(compiler_library.resolve()),
            "--clang",
            clang,
            "--wasm-objdump",
            wasm_objdump,
            "--objdump",
            objdump,
        ],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
        text=True,
    )
    return result.stdout


def compare_measurements(
    baseline_output: str,
    candidate_output: str,
    baseline_library: Path,
    candidate_library: Path,
) -> None:
    baseline_rows = measurement_rows(baseline_output)
    candidate_rows = measurement_rows(candidate_output)
    assert baseline_rows.keys() == candidate_rows.keys()
    print(f"baseline_library={baseline_library}")
    print(f"candidate_library={candidate_library}")
    for line in candidate_output.splitlines():
        if line.startswith("profile_compiler=") or line.startswith("profile_ngrams="):
            print(line)
    print(
        "operation static_ngrams baseline_instructions candidate_instructions "
        "saved_per_match weighted_static_saving"
    )
    weighted_saving = 0
    profitable_operations: list[str] = []
    for operation in SUPPORTED_OPERATIONS:
        if operation not in baseline_rows:
            continue
        baseline_frequency, _, _, baseline_instructions, _ = baseline_rows[operation]
        candidate_frequency, _, _, candidate_instructions, _ = candidate_rows[operation]
        assert baseline_frequency == candidate_frequency
        saved = baseline_instructions - candidate_instructions
        weighted = baseline_frequency * saved
        weighted_saving += weighted
        if baseline_frequency > 0 and saved > 0:
            profitable_operations.append(operation)
        print(
            f"{operation:9} {baseline_frequency:13d} {baseline_instructions:21d} "
            f"{candidate_instructions:22d} {saved:16d} {weighted:22d}"
        )
    selected = " | ".join(f"local.get local.get {operation}" for operation in profitable_operations)
    print(f"selected_profitable_ngrams={selected or 'none'}")
    print(f"frequency_weighted_static_saving_upper_bound={weighted_saving}")
    print(
        "Selection considers only measured, profiled local-pair operations with positive "
        "per-trace instruction savings; the weighted value assumes each static site is "
        "independently eligible."
    )


def run_measurement(
    compiler_library: Path,
    frequencies: Counter[str],
    operations: tuple[str, ...],
    objdump: str,
) -> None:
    original_pydll = ctypes.PyDLL

    def selected_pydll(
        name: str | os.PathLike[str] | None,
        mode: int = ctypes.DEFAULT_MODE,
        handle: int | None = None,
        use_errno: bool = False,
        use_last_error: bool = False,
    ):
        if name is not None and Path(name).name in ("libjit_probe.so", "libtrace_compiler.so"):
            name = compiler_library
        return original_pydll(
            name,
            mode=mode,
            handle=handle,
            use_errno=use_errno,
            use_last_error=use_last_error,
        )

    ctypes.PyDLL = selected_pydll
    sys.path.insert(0, str(BENCHMARK_DIR))
    from _bootstrap import configure_import_paths

    configure_import_paths(PYSIM_DIR, BENCHMARK_DIR)

    from config import JIT_TRACE_HEADER_BYTES
    from qa.shared.common_code import TRACE_ENTRY_STUB_BYTES
    from qa.shared.helpers import wat_to_wasm
    from qa.shared.jit_cache import JITTrace
    from qa.shared.runtime_support import make_runtime_engine
    from qa.shared.x64_jit import TraceCompiler
    from system_containers import StaticVector
    from tier2_runtime.interpreter.interpreter import InterpreterBindings, NativeInterpreter
    from tier2_runtime.wasm.module import LocalLayout, WasmOperand
    from tier2_runtime.wasm.reader import parse

    class RuntimeCompilerOnly(TraceCompiler):
        __slots__ = ()

        def compile_instructions(
            self,
            *,
            head_pc: int,
            instructions: Iterable[tuple[int, WasmOperand]],
            next_pc: int | None,
            loops_to: int | None,
            byte_length: int,
            local_layout: LocalLayout,
            context_helper: bool = False,
            helper_address: int = 0,
        ) -> JITTrace | None:
            raise AssertionError("benchmark traces must use the native WASM scanner")

    functions = " ".join(
        f'(func (export "{operation.replace(".", "_")}") (param i32 i32) (result i32) '
        f"local.get 0 local.get 1 {operation})"
        for operation in operations
    )
    module = parse(wat_to_wasm(f"(module {functions})"))
    engine = make_runtime_engine(
        jit_compiler=RuntimeCompilerOnly(),
        min_trace_bytes=1,
        candidate_threshold=0,
    )
    engine.register_module_blocks(module)
    memory = bytearray(65536)
    module.init_memory_data(memory, ())
    bindings = InterpreterBindings.with_memory_and_functions(memory, StaticVector(capacity=0))
    interpreter = NativeInterpreter(module, bindings, bump_allocator=engine.bump_allocator)
    manager = engine.jit_runtime
    assert manager is not None
    left = 0x9234_5678
    right = 0x1234_5678

    print(f"compiler_library={compiler_library}")
    print("operation static_ngrams trace_bytes body_bytes body_instructions executions")
    for operation in operations:
        export = operation.replace(".", "_")
        function_index = module.export_func_index(export)
        expected = expected_result(operation, left, right)
        for _ in range(3):
            actual = [
                value & 0xFFFF_FFFF
                for value in engine.call(interpreter, function_index, [left, right])
            ]
            assert actual == [expected], (operation, left, right, expected, actual)
        trace = manager.cache.find_trace(module.function_pc_offset(function_index))
        assert trace is not None and trace.raw_addr is not None
        code_blob_address = ctypes.cast(trace._native.code_blob, ctypes.c_void_p).value
        assert code_blob_address is not None
        code_blob = ctypes.string_at(code_blob_address, trace._native.blob_bytes)
        body = code_blob[JIT_TRACE_HEADER_BYTES + TRACE_ENTRY_STUB_BYTES :]
        instruction_count = disassembled_instruction_count(objdump, body)
        assert instruction_count > 0
        print(
            f"{operation:9} {frequencies[operation]:13d} {trace.size_bytes:11d} "
            f"{len(body):10d} {instruction_count:18d} {trace.exec_count:10d}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler-library", required=True, type=Path)
    parser.add_argument(
        "--baseline-library",
        type=Path,
        help="also compare this baseline library and list profiled n-grams with positive savings",
    )
    parser.add_argument("--clang", default=os.environ.get("CLANG", "clang"))
    parser.add_argument("--wasm-objdump", default=os.environ.get("WASM_OBJDUMP", "wasm-objdump"))
    parser.add_argument("--objdump", default=os.environ.get("OBJDUMP", "objdump"))
    arguments = parser.parse_args()

    if arguments.baseline_library is not None:
        baseline_output = run_measurement_subprocess(
            arguments.baseline_library,
            arguments.clang,
            arguments.wasm_objdump,
            arguments.objdump,
        )
        candidate_output = run_measurement_subprocess(
            arguments.compiler_library,
            arguments.clang,
            arguments.wasm_objdump,
            arguments.objdump,
        )
        compare_measurements(
            baseline_output,
            candidate_output,
            arguments.baseline_library,
            arguments.compiler_library,
        )
        return

    with tempfile.TemporaryDirectory(prefix="fireball-x64-stencil-profile-") as directory:
        wasm = Path(directory) / "suite.wasm"
        compile_profile_wasm(arguments.clang, wasm)
        frequencies = profile_frequencies(wasm, arguments.wasm_objdump)
        operations = tuple(
            operation for operation in SUPPORTED_OPERATIONS if frequencies[operation] > 0
        )
    assert operations, "Clang WASM profile contains no supported local.get/local.get/i32 n-grams"
    compiler_version = subprocess.run(
        [arguments.clang, "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    print(f"profile_compiler={compiler_version}")
    print(f"profile_source={PROFILE_SOURCE}")
    print(
        "profile_ngrams="
        + ",".join(f"{operation}:{frequencies[operation]}" for operation in operations)
    )
    run_measurement(arguments.compiler_library, frequencies, operations, arguments.objdump)


if __name__ == "__main__":
    main()

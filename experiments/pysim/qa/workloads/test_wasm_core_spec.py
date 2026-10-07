"""Execute selected upstream WebAssembly Core Spec Tests on Fireball's interpreter."""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import cast

import pytest
from qa.shared.helpers import make_interpreter
from tier2_runtime.interpreter.interpreter import Interpreter, InterpreterCall, Trap
from tier2_runtime.wasm.module import F32, F64, I32, I64, Module
from tier2_runtime.wasm.reader import parse

ROOT = Path(__file__).resolve().parents[4]
SPEC_REVISION = "970c4116e644e2bf7acb39aab8b733db14ccdf28"
WABT_VERSION = "1.0.36"
SPEC_SOURCE_DIR = ROOT / "build" / "wasm-core-spec" / SPEC_REVISION / "test" / "core"
SPEC_OUTPUT_DIR = ROOT / "build" / "wasm-core-spec" / SPEC_REVISION / "converted"

# This is an explicit subset of the upstream Core Test Suite. The selection
# covers MVP execution, numeric edge cases, memory bounds, and malformed input.
# Files that require proposals beyond Fireball's declared profile, or WAST
# constructs unsupported by the pinned WABT tool, are tracked outside this set.
CORE_SPEC_FILES = (
    "address",
    "comments",
    "const",
    "endianness",
    "f32",
    "f32_bitwise",
    "f32_cmp",
    "f64",
    "f64_bitwise",
    "f64_cmp",
    "float_exprs",
    "float_literals",
    "float_memory",
    "float_misc",
    "forward",
    "i32",
    "i64",
    "int_exprs",
    "int_literals",
    "labels",
    "left-to-right",
    "load",
    "local_get",
    "local_set",
    "memory_redundancy",
    "memory_size",
    "memory_trap",
    "nop",
    "return",
    "stack",
    "store",
    "switch",
    "traps",
    "unreachable",
    "utf8-custom-section-id",
    "utf8-import-field",
    "utf8-import-module",
)

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]


class UnsupportedFixture(Exception):
    """A valid spec case depends on an embedding fixture not provided here."""


def _as_object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict), f"expected JSON object, got {value!r}"
    return cast(JsonObject, value)


def _as_list(value: JsonValue | None) -> list[JsonValue]:
    assert isinstance(value, list), f"expected JSON array, got {value!r}"
    return value


def _as_string(value: JsonValue | None) -> str:
    assert isinstance(value, str), f"expected JSON string, got {value!r}"
    return value


def _as_integer(value: JsonValue | None) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected JSON integer, got {value!r}"
    )
    return value


def _module_globals(module: Module) -> tuple[int, ...]:
    values: list[int] = []
    for wasm_global in module.globals:
        if wasm_global.imported:
            raise UnsupportedFixture("module imports a global")
        if wasm_global.init_global_index is None:
            values.append(int(wasm_global.init_value))
        else:
            assert wasm_global.init_global_index < len(values)
            values.append(values[wasm_global.init_global_index])
    return tuple(values)


def _instantiate(binary: bytes) -> tuple[Module, Interpreter]:
    module = parse(binary)
    if module.imports or module.memory_import is not None or module.table_import_count:
        raise UnsupportedFixture("module requires an imported function, memory, table, or global")
    if module.memory is None:
        memory = bytearray()
    else:
        memory = bytearray(module.memory.min_pages * 65536)
    module.init_memory_data(memory, _module_globals(module))
    interpreter = make_interpreter(module, memory=memory)
    if module.start_function is not None:
        call = interpreter.start(module.start_function, ())
        while not call.finished:
            call = interpreter.step(call)
        if call.trap is not None:
            raise call.trap
    return module, interpreter


def _assert_rejected_module(binary: bytes, location: str) -> None:
    """Treat only parser rejection as a passing malformed/invalid assertion."""
    try:
        parse(binary)
    except (AssertionError, ValueError, IndexError, struct.error):
        return
    raise AssertionError(f"{location}: loader accepted rejected module")


def _number(value: JsonObject) -> int | float:
    value_type = _as_string(value.get("type"))
    raw_value = _as_string(value.get("value"))
    if value_type in ("i32", "i64"):
        return int(raw_value)
    if raw_value.startswith("nan:"):
        return float("nan")
    bits = int(raw_value)
    if value_type == "f32":
        return struct.unpack("<f", bits.to_bytes(4, "little"))[0]
    if value_type == "f64":
        return struct.unpack("<d", bits.to_bytes(8, "little"))[0]
    raise AssertionError(f"unsupported Core value type: {value_type}")


def _run_invocation(
    module: Module,
    interpreter: Interpreter,
    action: JsonObject,
) -> InterpreterCall:
    if "module" in action:
        raise UnsupportedFixture("action targets a named, separately registered module")
    function_name = _as_string(action.get("field"))
    arguments = tuple(
        _number(_as_object(argument)) for argument in _as_list(action.get("args", []))
    )
    function_index = module.export_func_index(function_name)
    call = interpreter.start(function_index, arguments)
    while not call.finished:
        call = interpreter.step(call)
    return call


def _run_action(
    module: Module,
    interpreter: Interpreter,
    action: JsonObject,
) -> tuple[int | float, ...] | Trap:
    action_type = _as_string(action.get("type"))
    if action_type == "invoke":
        call = _run_invocation(module, interpreter, action)
        if call.trap is not None:
            return call.trap
        assert call.results is not None
        return tuple(call.results)
    if action_type == "get":
        field = _as_string(action.get("field"))
        for exported in module.exports:
            assert module.source is not None
            exported_name = (
                module.source[exported.name_offset : exported.name_offset + exported.name_size]
                .tobytes()
                .decode("utf-8")
            )
            if exported_name == field and exported.kind == 3:
                value = int(interpreter.globals[exported.index])
                value_type = module.globals[exported.index].vtype
                if value_type == I32:
                    return (value & 0xFFFF_FFFF,)
                if value_type == I64:
                    return (value & 0xFFFF_FFFF_FFFF_FFFF,)
                if value_type == F32:
                    return (struct.unpack("<f", (value & 0xFFFF_FFFF).to_bytes(4, "little"))[0],)
                if value_type == F64:
                    return (struct.unpack("<d", value.to_bytes(8, "little"))[0],)
        raise AssertionError(f"no supported global export named {field!r}")
    raise UnsupportedFixture(f"unsupported action kind: {action_type}")


def _assert_values(
    actual: tuple[int | float, ...],
    expected_values: list[JsonValue],
    location: str,
) -> None:
    expected = tuple(_as_object(value) for value in expected_values)
    assert len(actual) == len(expected), f"{location}: result arity {actual!r} != {expected!r}"
    for actual_value, expected_value in zip(actual, expected, strict=True):
        value_type = _as_string(expected_value.get("type"))
        expected_raw = _as_string(expected_value.get("value"))
        if value_type == "i32":
            assert int(actual_value) & 0xFFFF_FFFF == int(expected_raw) & 0xFFFF_FFFF, (
                f"{location}: i32 mismatch {actual_value!r} != {expected_raw}"
            )
        elif value_type == "i64":
            assert (
                int(actual_value) & 0xFFFF_FFFF_FFFF_FFFF
                == int(expected_raw) & 0xFFFF_FFFF_FFFF_FFFF
            ), f"{location}: i64 mismatch {actual_value!r} != {expected_raw}"
        elif value_type in ("f32", "f64"):
            actual_float = float(actual_value)
            fmt = "<f" if value_type == "f32" else "<d"
            if expected_raw == "nan:canonical":
                expected_bits = 0x7FC0_0000 if value_type == "f32" else 0x7FF8_0000_0000_0000
                actual_bits = int.from_bytes(struct.pack(fmt, actual_float), "little")
                signless_bits = actual_bits & (expected_bits | (expected_bits - 1))
                assert signless_bits == expected_bits, f"{location}: canonical NaN bits differ"
            elif expected_raw == "nan:arithmetic":
                assert math.isnan(actual_float), f"{location}: expected NaN, got {actual_float!r}"
            else:
                expected_float = _number(expected_value)
                assert struct.pack(fmt, actual_float) == struct.pack(fmt, float(expected_float)), (
                    f"{location}: {value_type} bits differ"
                )
        else:
            raise AssertionError(f"{location}: unsupported expected type {value_type}")


@cache
def _wast2json() -> str:
    executable = shutil.which("wast2json")
    assert executable is not None, "WABT wast2json is required to run the Core Spec Suite"
    version = subprocess.run(
        [executable, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert version == WABT_VERSION, f"expected WABT {WABT_VERSION}, got {version}"
    return executable


def _converted_case(name: str) -> tuple[JsonObject, Path]:
    source = SPEC_SOURCE_DIR / f"{name}.wast"
    assert source.is_file(), (
        "WebAssembly Core Spec Tests are not prepared; run "
        "`python tools/guest_bindings/fetch_wasm_core_suite.py` first"
    )
    SPEC_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = SPEC_OUTPUT_DIR / f"{name}.json"
    subprocess.run(
        [_wast2json(), str(source), "-o", str(output)],
        check=True,
        cwd=SPEC_OUTPUT_DIR,
        capture_output=True,
        text=True,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    return _as_object(payload), SPEC_OUTPUT_DIR


def _execute_spec_file(name: str) -> tuple[int, dict[str, int]]:
    payload, binary_dir = _converted_case(name)
    active_module: tuple[Module, Interpreter] | None = None
    passed = 0
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for command_value in _as_list(payload.get("commands")):
        command = _as_object(command_value)
        command_type = _as_string(command.get("type"))
        line = _as_integer(command.get("line"))
        location = f"{name}.wast:{line}"
        if command_type == "module":
            wasm_file = binary_dir / _as_string(command.get("filename"))
            try:
                active_module = _instantiate(wasm_file.read_bytes())
            except UnsupportedFixture as error:
                active_module = None
                skip(str(error))
            continue
        if command_type in ("assert_malformed", "assert_invalid"):
            wasm_file = binary_dir / _as_string(command.get("filename"))
            _assert_rejected_module(wasm_file.read_bytes(), location)
            passed += 1
            continue
        if command_type == "assert_uninstantiable":
            wasm_file = binary_dir / _as_string(command.get("filename"))
            try:
                _instantiate(wasm_file.read_bytes())
            except UnsupportedFixture as error:
                skip(str(error))
            except Trap:
                passed += 1
            else:
                raise AssertionError(f"{location}: start function did not trap")
            continue
        if command_type in ("assert_return", "assert_trap", "action"):
            if active_module is None:
                skip("preceding module requires an unsupported embedding fixture")
                continue
            action = _as_object(command.get("action"))
            try:
                actual = _run_action(active_module[0], active_module[1], action)
            except UnsupportedFixture as error:
                skip(str(error))
                continue
            if command_type == "assert_trap":
                assert isinstance(actual, Trap), f"{location}: expected trap, got {actual!r}"
            elif command_type == "assert_return":
                assert not isinstance(actual, Trap), f"{location}: unexpected trap {actual!r}"
                _assert_values(
                    actual,
                    _as_list(command.get("expected")),
                    location,
                )
            else:
                assert not isinstance(actual, Trap), f"{location}: action trapped: {actual!r}"
            if command_type != "action":
                passed += 1
            continue
        if command_type == "assert_unlinkable":
            skip("linker/import-resolution assertion requires an embedding environment")
            continue
        if command_type == "register":
            continue
        raise AssertionError(f"{location}: unsupported Core test command {command_type!r}")
    return passed, skipped


@pytest.mark.parametrize("name", CORE_SPEC_FILES)
def test_official_core_spec_file_executes_on_fireball(
    name: str, core_spec_summary: list[tuple[str, int, dict[str, int]]]
) -> None:
    """Execute the supported commands from an immutable upstream Core test file."""
    passed, skipped = _execute_spec_file(name)
    skipped_count = sum(skipped.values())
    assert passed > 0, f"{name}: no upstream assertions executed; skips={skipped}"
    core_spec_summary.append((name, passed, skipped))
    reasons = "; ".join(f"{reason}: {count}" for reason, count in sorted(skipped.items()))
    print(f"{name}: {passed} Core assertions executed, {skipped_count} skipped ({reasons})")


@pytest.fixture(scope="module")
def core_spec_summary() -> Iterator[list[tuple[str, int, dict[str, int]]]]:
    """Print a suite-level total while keeping each upstream file independently visible."""
    results: list[tuple[str, int, dict[str, int]]] = []
    yield results
    assertions = sum(passed for _, passed, _ in results)
    skips: dict[str, int] = {}
    for _, _, file_skips in results:
        for reason, count in file_skips.items():
            skips[reason] = skips.get(reason, 0) + count
    reasons = "; ".join(f"{reason}: {count}" for reason, count in sorted(skips.items()))
    print(
        f"Official Core Spec selection: {len(results)}/{len(CORE_SPEC_FILES)} files, "
        f"{assertions} assertions, {sum(skips.values())} skipped ({reasons})"
    )

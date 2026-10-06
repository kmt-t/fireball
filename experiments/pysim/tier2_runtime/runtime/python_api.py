"""Python runtime services used at WebAssembly host boundaries."""

from __future__ import annotations

import math
import struct
from typing import Protocol

from system_containers import SequenceView as Sequence
from system_containers import StaticVector
from tier2_runtime.wasm.module import F32, F64, I32, I64, Memory, Module

PAGE_SIZE = 65536
MAX_MEMORY_PAGES = 65536


class WasmNumber(Protocol):
    """A numeric value accepted at the host runtime API boundary."""

    def __int__(self) -> int: ...

    def __float__(self) -> float: ...


class HostFunction(Protocol):
    """A Python callable bound to one imported WebAssembly function."""

    def __call__(self, *args: WasmNumber) -> WasmNumber | None: ...


class RuntimeAPI(Protocol):
    """Runtime services consumed through values, without interpreter state."""

    def invoke_import(
        self, function_index: int, args: Sequence[WasmNumber]
    ) -> StaticVector[WasmNumber] | None: ...

    def memory_size(self) -> int | None: ...

    def grow_memory(self, delta_pages: int) -> int: ...


class PythonRuntimeAPI:
    """Bound Python host services for one module instance."""

    __slots__ = ("_host_functions", "_memory", "_memory_decl", "_module")

    def __init__(
        self,
        module: Module,
        memory: bytearray | None,
        memory_decl: Memory,
        host_functions: Sequence[HostFunction | None],
    ) -> None:
        assert len(host_functions) == len(module.imports)
        self._module = module
        self._memory = memory
        self._memory_decl = memory_decl
        self._host_functions = host_functions

    def invoke_import(
        self, function_index: int, args: Sequence[WasmNumber]
    ) -> StaticVector[WasmNumber] | None:
        """Invoke one host import using typed values only; ``None`` means unbound."""
        assert self._module.is_import(function_index)
        handler = self._host_functions[function_index]
        if handler is None:
            return None

        function_type = self._module.func_type(function_index)
        assert function_type.params is not None and function_type.results is not None
        assert len(args) == len(function_type.params)
        host_args: StaticVector[WasmNumber] = StaticVector(capacity=len(args))
        for value, value_type in zip(args, function_type.params, strict=True):
            if value_type == I64:
                host_args.append(_to_i64(int(value)))
            elif value_type == F32:
                host_args.append(_to_f32(float(value)))
            elif value_type == F64:
                host_args.append(float(value))
            else:
                assert value_type == I32
                host_args.append(_to_i32(int(value)))

        result = handler(*host_args)
        results: StaticVector[WasmNumber] = StaticVector(capacity=1)
        if function_type.results:
            assert len(function_type.results) == 1 and result is not None
            result_type = function_type.results[0]
            if result_type == I64:
                results.append(_to_i64(int(result)))
            elif result_type == F32:
                results.append(_to_f32(float(result)))
            elif result_type == F64:
                results.append(float(result))
            else:
                assert result_type == I32
                results.append(_to_i32(int(result)))
        return results

    def memory_size(self) -> int | None:
        """Return the current page count, or ``None`` when no memory is bound."""
        if self._memory is None:
            return None
        return len(self._memory) // PAGE_SIZE

    def grow_memory(self, delta_pages: int) -> int:
        """Grow memory and return the old page count, or the WebAssembly -1 sentinel."""
        assert 0 <= delta_pages <= 0xFFFF_FFFF
        if self._memory is None:
            return -1
        old_pages = len(self._memory) // PAGE_SIZE
        maximum = self._memory_decl.max_pages
        if maximum is None or maximum > MAX_MEMORY_PAGES:
            maximum = MAX_MEMORY_PAGES
        if old_pages > maximum or delta_pages > maximum - old_pages:
            return -1
        self._memory.extend(bytes(delta_pages * PAGE_SIZE))
        return old_pages


def _to_i32(value: int) -> int:
    value &= 0xFFFF_FFFF
    return value - (1 << 32) if value & 0x8000_0000 else value


def _to_i64(value: int) -> int:
    value &= 0xFFFF_FFFF_FFFF_FFFF
    return value - (1 << 64) if value & 0x8000_0000_0000_0000 else value


def _to_f32(value: float) -> float:
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return math.copysign(float("inf"), value)


__all__ = ("PAGE_SIZE", "HostFunction", "PythonRuntimeAPI", "RuntimeAPI", "WasmNumber")

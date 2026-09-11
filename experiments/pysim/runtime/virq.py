"""
Cause-carrying vIRQ dispatch for the vSoC reference runtime.

The implementation follows runtime_vsoc.md and runtime_vmmio.md:
the event is always five fixed u32 words, the node hierarchy is static,
registrations become visible only at a safepoint, and dispatch never raises
an exception.  Registration and dispatch failures are represented by result
values and diagnostics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from interrupt_event import InterruptEvent
from recovery import Result
from system_containers import StaticVector
from wasm_module import I32

if TYPE_CHECKING:
    from wasm_module import Module


FB_CONF_HAL_MAX_DEVICES = 8
FB_CONF_VIRQ_CATEGORY_COUNT = 4
FB_CONF_VIRQ_MAX_NODES = 1 + FB_CONF_VIRQ_CATEGORY_COUNT + FB_CONF_HAL_MAX_DEVICES
FB_CONF_VIRQ_MAX_SOURCES = 3 + FB_CONF_HAL_MAX_DEVICES
INVALID_FUNCTION_INDEX = 0xFFFF_FFFF

VECTOR_DEVICE_BASE = 0x0100
VECTOR_SYSTEM = 0x1000
VECTOR_RUNTIME = 0x2000
VECTOR_FAULT = 0x3000


class VirqNode(IntEnum):
    ROOT = 0
    DEVICE = 1
    SYSTEM = 2
    RUNTIME = 3
    FAULT = 4

    @classmethod
    def device(cls, device_index: int) -> int:
        return 5 + device_index


class VirqDispatchResult(IntEnum):
    HANDLED = 0
    PASS_THROUGH = 1
    REJECT = 2


class RegistrationError(IntEnum):
    MODULE_UNAVAILABLE = 1
    NODE_OUT_OF_RANGE = 2
    FUNCTION_INDEX_INVALID = 3
    FUNCTION_SIGNATURE_INVALID = 4


class RegistrationStatus(IntEnum):
    PENDING = 0


@dataclass(frozen=True, slots=True)
class VirqSource:
    vector_id: int
    category_node: int
    device_node: int
    source_id: int


@dataclass(frozen=True, slots=True)
class DispatchResult:
    outcome: VirqDispatchResult
    error: str | None = None


VirqInvoker = Callable[[int, int, int, int, int, int], int]


class VirqDispatcher:
    """Static vIRQ hierarchy with safepoint-committed guest registrations."""

    __slots__ = (
        "_active_functions",
        "_faults",
        "_invoker",
        "_last_path",
        "_module",
        "_pending_functions",
        "_sources",
    )

    def __init__(
        self,
        module: Module | None,
        invoker: VirqInvoker,
        max_devices: int = FB_CONF_HAL_MAX_DEVICES,
    ):
        self._module = module
        self._invoker = invoker
        max_nodes = 1 + FB_CONF_VIRQ_CATEGORY_COUNT + max_devices
        self._active_functions: tuple[int, ...] = (INVALID_FUNCTION_INDEX,) * max_nodes
        self._pending_functions: StaticVector[int] = StaticVector(capacity=max_nodes)
        for _ in range(max_nodes):
            self._pending_functions.push_back(INVALID_FUNCTION_INDEX)
        self._sources: StaticVector[VirqSource] = StaticVector(
            capacity=3 + max_devices
        )
        self._sources.push_back(
            VirqSource(VECTOR_SYSTEM, int(VirqNode.SYSTEM), INVALID_FUNCTION_INDEX, 0)
        )
        self._sources.push_back(
            VirqSource(VECTOR_RUNTIME, int(VirqNode.RUNTIME), INVALID_FUNCTION_INDEX, 0)
        )
        self._sources.push_back(
            VirqSource(VECTOR_FAULT, int(VirqNode.FAULT), INVALID_FUNCTION_INDEX, 0)
        )
        for device_index in range(max_devices):
            self._sources.push_back(
                VirqSource(
                    VECTOR_DEVICE_BASE + device_index,
                    int(VirqNode.DEVICE),
                    VirqNode.device(device_index),
                    device_index,
                )
            )
        self._last_path: StaticVector[int] = StaticVector(capacity=max_nodes)
        self._faults: StaticVector[str] = StaticVector(capacity=max_nodes)

    @property
    def active_functions(self) -> tuple[int, ...]:
        return self._active_functions

    @property
    def source_table(self) -> tuple[VirqSource, ...]:
        return tuple(self._sources)

    @property
    def last_path(self) -> tuple[int, ...]:
        return tuple(self._last_path)

    @property
    def faults(self) -> tuple[str, ...]:
        return tuple(self._faults)

    def register_dispatcher(
        self, node_id: int, function_index: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Validate and retain a registration for the next safepoint."""

        if self._module is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        if not self._valid_node(node_id):
            return Result.err(RegistrationError.NODE_OUT_OF_RANGE)
        if not self._valid_function_index(function_index):
            return Result.err(RegistrationError.FUNCTION_INDEX_INVALID)
        if not self._expected_signature(function_index):
            return Result.err(RegistrationError.FUNCTION_SIGNATURE_INVALID)
        self._pending_functions[node_id] = function_index
        return Result.ok(RegistrationStatus.PENDING)

    def commit_safepoint(self) -> None:
        """Atomically publish the already validated pending registration table."""

        self._active_functions = tuple(self._pending_functions)

    def dispatch_interrupt_event(self, event: InterruptEvent) -> DispatchResult:
        """Dispatch one event at a safepoint through the static hierarchy."""

        self._last_path.clear()
        source = self._source_for(event)
        if source is None:
            return self._reject("UNREGISTERED_SOURCE")

        result = self._invoke(int(VirqNode.ROOT), event)
        if result == VirqDispatchResult.HANDLED:
            return DispatchResult(result)
        if result == VirqDispatchResult.REJECT:
            return self._reject("ROOT_REJECT")

        result = self._invoke(source.category_node, event)
        if result == VirqDispatchResult.HANDLED:
            return DispatchResult(result)
        if result == VirqDispatchResult.REJECT:
            return self._reject("CATEGORY_REJECT")

        if source.device_node != INVALID_FUNCTION_INDEX:
            result = self._invoke(source.device_node, event)
            if result == VirqDispatchResult.HANDLED:
                return DispatchResult(result)
            if result == VirqDispatchResult.REJECT:
                return self._reject("DEVICE_REJECT")
        return DispatchResult(VirqDispatchResult.PASS_THROUGH)

    def _valid_node(self, node_id: int) -> bool:
        return 0 <= node_id < len(self._active_functions)

    def _valid_function_index(self, function_index: int) -> bool:
        if function_index < 0 or self._module is None:
            return False
        return function_index < len(self._module.imports) + len(self._module.functions)

    def _expected_signature(self, function_index: int) -> bool:
        if self._module is None:
            return False
        if function_index < len(self._module.imports):
            type_index = self._module.imports[function_index].type_index
        else:
            type_index = self._module.functions[
                function_index - len(self._module.imports)
            ].type_index
        if type_index < 0 or type_index >= len(self._module.types):
            return False
        func_type = self._module.types[type_index]
        return func_type.params == (I32,) * 5 and func_type.results == (I32,)

    def _source_for(self, event: InterruptEvent) -> VirqSource | None:
        for source in self._sources:
            if source.vector_id == event.vector_id and source.source_id == event.source_id:
                return source
        return None

    def _invoke(self, node_id: int, event: InterruptEvent) -> VirqDispatchResult:
        function_index = self._active_functions[node_id]
        if function_index == INVALID_FUNCTION_INDEX:
            return VirqDispatchResult.PASS_THROUGH
        self._last_path.push_back(node_id)
        vector_id, source_id, cause_code, payload0, payload1 = event.words()
        raw_result = self._invoker(
            function_index,
            vector_id,
            source_id,
            cause_code,
            payload0,
            payload1,
        )
        if raw_result < int(VirqDispatchResult.HANDLED) or raw_result > int(
            VirqDispatchResult.REJECT
        ):
            return VirqDispatchResult.REJECT
        return VirqDispatchResult(raw_result)

    def _reject(self, reason: str) -> DispatchResult:
        self._faults.push_back(reason)
        return DispatchResult(VirqDispatchResult.REJECT, reason)

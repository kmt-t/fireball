"""Tier 2 Runtime lifecycle, scheduler boundary, and vIRQ delivery.

The Interpreter owns native dispatch and any optional execution plugin. Runtime
only asks the Interpreter to advance to the next resumable scheduler boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from bump_allocator import BumpAllocator
from tier2_runtime.interpreter.interpreter import (
    InterpreterCall,
    NativeInterpreter,
)
from tier2_runtime.runtime.recovery import Result
from tier2_runtime.vsoc.virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
    VirqFaultCode,
)
from tier2_runtime.wasm.module import BasicBlock, Module


class RuntimeDriveMode(IntEnum):
    """Select which layer owns advancing the guest between native dispatch boundaries."""

    SYNCHRONOUS = 1
    COOS = 2


@dataclass(frozen=True, slots=True)
class RuntimeBoundaryResult:
    """State returned after yield, fallback, trap, or completion from native dispatch."""

    call_state: InterpreterCall
    yield_requested: bool = False


__all__ = ("RuntimeBoundaryResult", "RuntimeDriveMode", "RuntimeEngine")


class RuntimeEngine:
    """Runtime coordinator for interpreter execution boundaries and vIRQ delivery."""

    __slots__ = (
        "_bump_allocator",
        "_virq",
        "_virq_interp",
        "drive_mode",
        "module",
    )

    def __init__(
        self,
        drive_mode: RuntimeDriveMode = RuntimeDriveMode.SYNCHRONOUS,
        bump_allocator: BumpAllocator | None = None,
    ):
        # RuntimeEngine is the runtime owner; loaders borrow this arena.
        self._bump_allocator = bump_allocator if bump_allocator is not None else BumpAllocator()
        self.drive_mode = drive_mode
        self.module: Module | None = None
        self._virq: VirqDispatcher | None = None
        self._virq_interp: NativeInterpreter | None = None

    @property
    def bump_allocator(self) -> BumpAllocator:
        """Return the arena shared by this runtime and its WASM module."""

        return self._bump_allocator

    def load_wasm(self, wasm_bytes: bytes) -> Module:
        """Parses raw WASM binary and binds all loader-owned basic blocks and Radix trees."""
        from tier2_runtime.wasm.reader import parse

        module = parse(memoryview(wasm_bytes), self._bump_allocator)
        self.register_module_blocks(module)
        return module

    def get_block(self, pc: int) -> BasicBlock | None:
        return self.module.get_block(pc) if self.module is not None else None

    def register_module_blocks(self, module: Module) -> None:
        """Binds the loader-owned immutable block index."""
        module.relocate_to(self._bump_allocator)
        if module.block_storage is None:
            module.build_basic_block_index(self._bump_allocator)
        self.module = module
        self._virq = VirqDispatcher(module, self._invoke_virq)

    def register_virq_dispatcher(
        self, node_id: int, function_index: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Validate a static vIRQ registration for the next COOS yield boundary."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.register_dispatcher(node_id, function_index)

    def unregister_virq_dispatcher(
        self, node_id: int
    ) -> Result[RegistrationStatus, RegistrationError]:
        """Stage removal of a vIRQ registration for the next COOS yield boundary."""
        if self._virq is None:
            return Result.err(RegistrationError.MODULE_UNAVAILABLE)
        return self._virq.unregister_dispatcher(node_id)

    def commit_virq_registrations(self) -> None:
        """Publish validated vIRQ registrations at the execution boundary."""
        if self._virq is not None:
            self._virq.commit_pending_registrations()

    def dispatch_interrupt_event(self, event: InterruptEvent) -> DispatchResult:
        """Dispatch one COOS event through the vIRQ hierarchy."""
        if self._virq is None:
            return DispatchResult(VirqDispatchResult.REJECT, VirqFaultCode.UNREGISTERED_SOURCE)
        return self._virq.dispatch_interrupt_event(event)

    def _invoke_virq(
        self,
        function_index: int,
        vector_id: int,
        source_id: int,
        cause_code: int,
        payload0: int,
        payload1: int,
    ) -> int:
        if self._virq_interp is None:
            return int(VirqDispatchResult.REJECT)
        results = self._virq_interp.call(
            function_index,
            (vector_id, source_id, cause_code, payload0, payload1),
        )
        if not results:
            return int(VirqDispatchResult.REJECT)
        return int(results[0]) & 0xFFFF_FFFF

    def idle_hook(self, budget: int = 4) -> int:
        """
        Ask the active Interpreter to service its optional execution plugin.
        """

        if self._virq_interp is None:
            return 0
        assert budget >= 0
        return self._virq_interp.idle_hook(budget)

    def run(
        self,
        interp: NativeInterpreter,
        call_state: InterpreterCall,
        idle_budget: int = 4,
    ) -> RuntimeBoundaryResult:
        """Advance the Interpreter once and return its scheduler boundary."""
        self._activate_interpreter(interp)
        return self._run_bound(interp, call_state, idle_budget)

    def _run_bound(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        """Advance an already-bound Interpreter through one scheduler boundary."""
        assert not call_state.finished, "cannot advance a completed interpreter call"
        return self._run_native_boundary(interp, call_state, idle_budget)

    def _activate_interpreter(self, interp: NativeInterpreter) -> None:
        """Activate one interpreter that borrows the constructor-selected arena."""
        assert interp.bump_allocator is self._bump_allocator, (
            "interpreter and runtime must be constructed with the same arena"
        )
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        self._virq_interp = interp

    def _run_native_boundary(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        """Ask the Interpreter to advance its own dispatcher to one boundary."""
        boundary = interp.step_native_boundary(call_state, idle_budget=idle_budget)
        return RuntimeBoundaryResult(
            boundary.call_state,
            boundary.yield_requested,
        )

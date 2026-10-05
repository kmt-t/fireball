"""Tier 2 Runtime execution boundary for the Interpreter and optional JIT extension.

The Runtime owns execution dispatch and continuation handling. An optional
Tier 3 JIT extension owns hotspot and cache state behind the bounded contract.
Execution model:
  Interpreter execution advances to a native dispatch boundary and preserves
  the shared execution context. An optional extension drives that dispatch
  through the same execution boundary contract.
  JIT cache policy and compilation remain inside that extension.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from functools import partial

from bump_allocator import BumpAllocator
from config import FB_CONF_RUNTIME_YIELD_THRESHOLD
from system_containers import StaticVector
from tier2_runtime.interpreter.interpreter import (
    NATIVE_DISPATCH_CALL_BOUNDARY,
    NATIVE_DISPATCH_YIELD,
    RETURN_SENTINEL_IP,
    InterpreterCall,
    NativeInterpreter,
    NativeModuleExecution,
    WasmNumber,
    select_native_dispatch_entry,
)
from tier2_runtime.runtime.execution_plugin import NativeExecutionPlugin
from tier2_runtime.runtime.recovery import Result
from tier2_runtime.vsoc.virq import (
    DispatchResult,
    InterruptEvent,
    RegistrationError,
    RegistrationStatus,
    VirqDispatcher,
    VirqDispatchResult,
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
    """Runtime coordinator for interpreter execution and optional JIT traces."""

    __slots__ = (
        "_bump_allocator",
        "_execution_initializer",
        "_native_dispatcher",
        "_virq",
        "_virq_interp",
        "drive_mode",
        "jit_runtime",
        "module",
        "yield_threshold",
    )

    def __init__(
        self,
        jit_runtime: NativeExecutionPlugin | None = None,
        drive_mode: RuntimeDriveMode = RuntimeDriveMode.SYNCHRONOUS,
        yield_threshold: int = FB_CONF_RUNTIME_YIELD_THRESHOLD,
        bump_allocator: BumpAllocator | None = None,
    ):
        backend_dispatcher = select_native_dispatch_entry(jit_runtime is not None)
        self._native_dispatcher = (
            backend_dispatcher if jit_runtime is None else jit_runtime.native_entry
        )
        self._execution_initializer: Callable[[NativeModuleExecution], int] | None = (
            None if jit_runtime is None else partial(jit_runtime.bind_execution, backend_dispatcher)
        )
        # RuntimeEngine is the runtime owner; loaders borrow this arena.
        self._bump_allocator = bump_allocator if bump_allocator is not None else BumpAllocator()
        self.jit_runtime = jit_runtime
        assert 1 <= yield_threshold <= 0xFFFF_FFFF
        self.yield_threshold = (
            jit_runtime.yield_threshold if jit_runtime is not None else yield_threshold
        )
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
        if self.jit_runtime is not None:
            self.jit_runtime.register_module(module)

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
            return DispatchResult(VirqDispatchResult.REJECT, "VIRQ_UNAVAILABLE")
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
        return results[0] & 0xFFFF_FFFF

    def idle_hook(self, budget: int = 4) -> int:
        """
        Gives the optional Tier 3 extension a bounded COOS idle slice.
        """

        if self.jit_runtime is None:
            return 0
        assert budget >= 0
        return self.jit_runtime.idle_hook(budget)

    def run(
        self,
        interp: NativeInterpreter,
        call_state: InterpreterCall,
        idle_budget: int = 4,
    ) -> RuntimeBoundaryResult:
        """Run native dispatch to a yield, fallback, trap, or completion boundary."""
        self._activate_interpreter(interp)
        return self._run_bound(interp, call_state, idle_budget)

    def _run_bound(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        """Advance an already-bound interpreter through the selected native boundary."""
        assert not call_state.finished, "cannot advance a completed interpreter call"
        return self._run_native_boundary(interp, call_state, idle_budget)

    def call(
        self,
        interp: NativeInterpreter,
        func_index: int,
        args: Sequence[WasmNumber],
        idle_budget: int = 4,
    ) -> StaticVector[WasmNumber]:
        """Complete one guest call in the caller-owned, non-COOS runtime mode."""
        assert self.drive_mode == RuntimeDriveMode.SYNCHRONOUS, (
            "COOS runtime calls must be advanced by System at each trace boundary"
        )
        self._activate_interpreter(interp)
        return self.complete_call(interp, interp.start(func_index, args), idle_budget)

    def complete_call(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int = 4
    ) -> StaticVector[WasmNumber]:
        """Finish a call by repeatedly using the same native-dispatch ``run`` path."""
        self._activate_interpreter(interp)
        if self.drive_mode == RuntimeDriveMode.COOS:
            assert call_state.finished, (
                "COOS runtime calls must be advanced by System at each trace boundary"
            )
        while not call_state.finished:
            call_state = self._run_bound(interp, call_state, idle_budget).call_state

        if self.jit_runtime is not None:
            self.idle_hook(budget=idle_budget)
        if call_state.trap is not None:
            assert False, call_state.trap.code
        assert call_state.results is not None
        return call_state.results

    def _activate_interpreter(self, interp: NativeInterpreter) -> None:
        """Activate one interpreter that borrows the constructor-selected arena."""
        assert interp.bump_allocator is self._bump_allocator, (
            "interpreter and runtime must be constructed with the same arena"
        )
        if self.module is None and interp.module is not None:
            self.register_module_blocks(interp.module)
        if interp.debugger is None:
            interp.configure_native_execution(self._native_dispatcher, self._execution_initializer)
        self._virq_interp = interp

    def _run_native_boundary(
        self, interp: NativeInterpreter, call_state: InterpreterCall, idle_budget: int
    ) -> RuntimeBoundaryResult:
        """Run C++ interpreter handlers through one count-based yield boundary."""
        if call_state._ip == RETURN_SENTINEL_IP:
            return RuntimeBoundaryResult(interp.step_native(call_state))
        assert call_state._frame is not None
        frame = call_state._frame
        native_status = interp.run_native_dispatch(
            call_state,
            self.yield_threshold,
            native_dispatcher=interp._native_dispatcher,
            idle_budget=idle_budget,
        )
        if native_status == 0:
            call_state = interp.resolve_native_call_boundary(call_state)
        elif native_status == NATIVE_DISPATCH_YIELD:
            frame.context.loop_jump_count = 0
        valid_native_status = (
            native_status == 0
            or native_status == 1
            or native_status == 2
            or native_status == NATIVE_DISPATCH_YIELD
            or native_status == NATIVE_DISPATCH_CALL_BOUNDARY
        )
        if not valid_native_status:
            assert False, f"unexpected native interpreter dispatch status: {native_status}"
        return RuntimeBoundaryResult(
            call_state,
            native_status == NATIVE_DISPATCH_YIELD,
        )

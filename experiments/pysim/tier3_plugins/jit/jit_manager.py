"""Lifetime and execution adapter for an opaque native plugin."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from functools import partial

from config import FB_CONF_RUNTIME_YIELD_THRESHOLD
from tier2_runtime.abi.native_abi import BufferLease
from tier2_runtime.interpreter.interpreter import (
    NATIVE_DISPATCH_CALL_BOUNDARY,
    NATIVE_DISPATCH_YIELD,
    RETURN_SENTINEL_IP,
    InterpreterCall,
    NativeDispatchEntryPoint,
    NativeInterpreter,
    NativeModuleExecution,
)
from tier2_runtime.runtime.engine import RuntimeBoundaryResult
from tier2_runtime.wasm.module import Module

from . import native_abi


class JITRuntimeManager:
    """Own native storage and borrow the runtime's module execution view."""

    __slots__ = (
        "_busy",
        "_execution",
        "_lease",
        "_pointer",
        "_region_provider",
        "module",
        "module_id",
        "yield_threshold",
    )

    def __init__(
        self,
        region_provider: Callable[[int, int], memoryview],
        yield_threshold: int = FB_CONF_RUNTIME_YIELD_THRESHOLD,
        module_id: int = 0,
    ) -> None:
        assert 1 <= yield_threshold <= 0xFFFFFFFF
        assert 0 <= module_id <= 0xFFFFFFFF
        self.yield_threshold = yield_threshold
        self.module_id = module_id
        self._busy = False
        self._region_provider = region_provider
        self._pointer: int | None = None
        self._lease: BufferLease | None = None
        self._execution: NativeModuleExecution | None = None
        self.module: Module | None = None

    def register_module(self, module: Module) -> None:
        self.close()
        self.module = module
        self._execution = None

    def _register_execution(self, execution: NativeModuleExecution | None) -> None:
        assert execution is not None and execution.module is self.module
        if execution is self._execution:
            return
        view = ctypes.byref(execution._module_view)
        self.close()
        size = native_abi.REQUIRED_BYTES(view)
        region = self._region_provider(size, native_abi.REGION_ALIGNMENT())
        assert not region.readonly and region.c_contiguous, (
            "plugin region must be writable and contiguous"
        )
        assert region.nbytes == size, "plugin region size must match its request"
        self._lease = BufferLease(region)
        self._pointer = native_abi.RUNTIME_INIT(
            self._lease.address,
            region.nbytes,
            view,
            1,
            self.module_id,
        )
        assert self._pointer is not None
        self._execution = execution

    def _begin(self) -> None:
        assert not self._busy
        self._busy = True

    def _check(self) -> None:
        self._busy = False
        assert self._pointer is not None
        assert native_abi.RUNTIME_ERROR(self._pointer) == 0, "native plugin contract violation"

    def run_boundary(
        self,
        interp: NativeInterpreter,
        call_state: InterpreterCall,
        idle_budget: int,
        native_dispatcher: NativeDispatchEntryPoint,
    ) -> RuntimeBoundaryResult:
        """Drive the interpreter/JIT dispatcher through one plugin boundary."""

        assert interp.debugger is None, "debugger-enabled calls must bypass the JIT plugin"
        assert 0 <= idle_budget <= 0x7FFF_FFFF
        if call_state._ip == RETURN_SENTINEL_IP:
            return RuntimeBoundaryResult(interp.step_native(call_state))
        assert call_state._frame is not None
        self._register_execution(call_state.context.module_execution)
        frame = call_state._frame

        dispatcher = partial(
            native_abi.RUNTIME_RUN,
            self._pointer,
            ctypes.cast(native_dispatcher, ctypes.c_void_p),
            idle_budget,
        )
        self._begin()
        (
            native_status,
            _dispatch_count,
            body_count,
            dispatcher_trace_transitions,
            control_count,
            interpreted_block_count,
        ) = interp.run_native_dispatch(
            call_state,
            self.yield_threshold,
            0,
            native_dispatcher=dispatcher,
        )
        self._check()
        if native_status == 0:
            call_state = interp.resolve_native_call_boundary(call_state)
        yield_requested = native_status == NATIVE_DISPATCH_YIELD
        if native_status == NATIVE_DISPATCH_YIELD:
            frame.context.loop_jump_count = 0
        valid_native_status = (
            native_status == 0
            or native_status == 1
            or native_status == 2
            or native_status == NATIVE_DISPATCH_YIELD
            or native_status == NATIVE_DISPATCH_CALL_BOUNDARY
        )
        if not valid_native_status:
            assert False, f"unexpected native JIT dispatch status: {native_status}"
        return RuntimeBoundaryResult(
            call_state,
            yield_requested,
            interpreted_block_count=interpreted_block_count,
            trace_execution_count=body_count,
            control_handler_count=control_count,
            trace_transition_count=dispatcher_trace_transitions,
        )

    def on_yield(self) -> None:
        if self._pointer is None:
            return
        self._begin()
        result = native_abi.RUNTIME_YIELD(self._pointer)
        self._check()
        assert result == 1

    def idle_hook(self, budget: int = 4) -> int:
        assert 0 <= budget <= 0x7FFF_FFFF
        if self._pointer is None:
            return 0
        self._begin()
        result = int(native_abi.RUNTIME_COMPILE(self._pointer, budget))
        self._check()
        assert result >= 0
        return result

    def flush_all(self) -> None:
        if self._pointer is None:
            return
        self._begin()
        result = native_abi.RUNTIME_FLUSH(self._pointer)
        self._check()
        assert result == 1

    def reset_stats(self) -> None:
        if self._pointer is not None:
            native_abi.RUNTIME_RESET_COUNTS(self._pointer)

    def close(self) -> None:
        if self._pointer is not None:
            native_abi.RUNTIME_CLOSE(self._pointer)
            self._pointer = None
        if self._lease is not None:
            self._lease.release()
            self._lease = None
        self._execution = None

    def __del__(self) -> None:
        self.close()

"""Test-side execution driver for debugger and GDB protocol tests.

The production ``RuntimeEngine`` owns the integrated Interpreter/JIT execution
loop. Debugger protocol tests need a block-at-a-time stepping surface, so this
module supplies that narrow test driver without adding test-only hooks to the
production runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from control_flow import iter_block_ops
from execution_context import WASMContext
from runtime_engine import JITRuntime, RuntimeEngine
from system_containers import StaticVector
from wasm_module import BasicBlock, WasmOperand
from wasm_opcodes import I32_ADD, I32_CONST, I32_MUL, I32_SUB, LOCAL_GET, LOCAL_SET, LOCAL_TEE


class _Debugger(Protocol):
    halted: bool
    stop_signal: int

    def has_breakpoint(self, pc: int) -> bool: ...

    def sample_pc(self, pc: int) -> None: ...

    def verify_assertions(self, memory: bytearray | None) -> None: ...


def _interp_i32_const(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    assert ctx.push(arg)


def _interp_i32_add(ctx: WASMContext, _arg: WasmOperand) -> None:
    right, left = ctx.pop(), ctx.pop()
    assert ctx.push((left + right) & 0xFFFF_FFFF)


def _interp_i32_sub(ctx: WASMContext, _arg: WasmOperand) -> None:
    right, left = ctx.pop(), ctx.pop()
    assert ctx.push((left - right) & 0xFFFF_FFFF)


def _interp_i32_mul(ctx: WASMContext, _arg: WasmOperand) -> None:
    right, left = ctx.pop(), ctx.pop()
    assert ctx.push((left * right) & 0xFFFF_FFFF)


def _interp_local_get(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    assert ctx.push(ctx.locals[arg])


def _interp_local_set(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    ctx.locals[arg] = ctx.pop()


def _interp_local_tee(ctx: WASMContext, arg: WasmOperand) -> None:
    assert arg is not None
    assert ctx.stack
    ctx.locals[arg] = ctx.stack[-1]


def _build_handlers() -> tuple[Callable[[WASMContext, WasmOperand], None] | None, ...]:
    handlers: StaticVector[Callable[[WASMContext, WasmOperand], None] | None] = StaticVector.of(
        tuple(None for _ in range(256)), capacity=256
    )
    handlers[I32_CONST] = _interp_i32_const
    handlers[I32_ADD] = _interp_i32_add
    handlers[I32_SUB] = _interp_i32_sub
    handlers[I32_MUL] = _interp_i32_mul
    handlers[LOCAL_GET] = _interp_local_get
    handlers[LOCAL_SET] = _interp_local_set
    handlers[LOCAL_TEE] = _interp_local_tee
    return tuple(handlers)


_HANDLERS = _build_handlers()


class RuntimeEngineDebugDriver(RuntimeEngine):
    """Test-only debugger driver backed by the production ``RuntimeEngine``.

    Debugger stepping always uses the interpreter.  A supplied JIT runtime is
    retained only by unrelated runtime tests; it is never an execution path
    for an attached debugger.
    """

    __slots__ = ("debugger",)

    def __init__(
        self,
        jit_runtime: JITRuntime | None = None,
        debug: bool = False,
    ) -> None:
        super().__init__(
            jit_runtime=jit_runtime,
            debug=debug,
        )
        self.debugger: _Debugger | None = None

    @property
    def handler_table(self) -> str:
        return "interpreter"

    @property
    def interp_blocks(self) -> int:
        return self.stat_interp_steps

    @property
    def jit_traces(self) -> int:
        return self.stat_jit_invocations

    def attach_debugger(self, debugger: _Debugger) -> None:
        assert self.jit_runtime is None, (
            "debugger-enabled runtime must use interpreter-only execution"
        )
        self.debugger = debugger

    def detach_debugger(self) -> None:
        self.debugger = None

    def _next_pc(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        if ctx.fault is not None:
            return None
        if block.loops_to is not None:
            condition = ctx.pop()
            return block.loops_to if condition != 0 else block.next_pc
        return block.next_pc

    def run_block_interpret(self, block: BasicBlock, ctx: WASMContext) -> int | None:
        assert self.module is not None
        function_index = block.head_pc >> 16
        code = self.module.code_for(function_index)
        for op, arg in iter_block_ops(code, block.head_pc & 0xFFFF, block.byte_span):
            handler = _HANDLERS[op]
            if handler is not None:
                handler(ctx, arg)
                if ctx.fault is not None:
                    return None
        return self._next_pc(block, ctx)

    def run_step(self, pc: int, ctx: WASMContext) -> int | None:
        """Execute one block for debugger protocol tests only."""
        block = self.get_block(pc)
        if block is None:
            return None
        debugger = self.debugger
        if debugger is not None:
            if debugger.has_breakpoint(pc):
                debugger.halted = True
                debugger.stop_signal = 5
                return pc
            debugger.sample_pc(pc)
        self.stat_interp_steps += 1
        next_pc = self.run_block_interpret(block, ctx)
        if debugger is not None:
            debugger.verify_assertions(ctx.memory)
            if next_pc is not None and debugger.has_breakpoint(next_pc):
                debugger.halted = True
                debugger.stop_signal = 5
        return next_pc


__all__ = ("RuntimeEngineDebugDriver",)

"""
experiments/pysim/tier3_jit/x64_jit.py
Pure Trace-based Copy-and-Patch JIT Compiler for Fireball.
Compiles individual HOT BasicBlocks / Traces into Position-Independent Code (PIC)
with 16-byte fixed headers (JITTraceHeader) and direct trace chaining.
Conforms strictly to docs/components/tier3_jit/jit_compiler.md and
docs/components/tier2_runtime/runtime_interpreter.md.
CPS 4-argument calling convention:
  RCX (R0): void* ctx            -- execution_context
  RDX (R1): void* sp             -- operand-stack pointer
  R8  (R2): void* local_base     -- locals array pointer
  R9  (R3): uint32_t tos         -- stack top value
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

import x64_stencils as st
from control_flow import iter_block_ops
from exec_memory import ExecutableBuffer
from jit_abi import JIT_CONTEXT_HELPER_PTR_OFFSET, JIT_CONTEXT_WORD_BYTES
from jit_cache import JITTrace, JITTraceHeader
from system_containers import FlatMapView, ReadOnlyFlatMapStorage, StaticVector
from wasm_module import (
    WASM_LOCAL_SLOT_BYTES,
    WASM_VALUE_SLOT_BYTES,
    BasicBlock,
    TraceBlock,
    WasmOperand,
)
from wasm_opcodes import (
    DROP,
    F32_ADD,
    F32_CONST,
    F32_DIV,
    F32_MUL,
    F32_SUB,
    F64_ADD,
    F64_CONST,
    F64_DIV,
    F64_MUL,
    F64_SUB,
    I32_ADD,
    I32_AND,
    I32_CONST,
    I32_DIV_S,
    I32_DIV_U,
    I32_EQ,
    I32_EQZ,
    I32_GE_S,
    I32_GE_U,
    I32_GT_S,
    I32_GT_U,
    I32_LE_S,
    I32_LE_U,
    I32_LT_S,
    I32_LT_U,
    I32_MUL,
    I32_NE,
    I32_OR,
    I32_REM_S,
    I32_REM_U,
    I32_SHL,
    I32_SHR_S,
    I32_SHR_U,
    I32_SUB,
    I32_XOR,
    I64_ADD,
    I64_CONST,
    I64_MUL,
    I64_SUB,
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)

IS_WINDOWS = sys.platform == "win32"
I32_MASK = 0xFFFFFFFF

# CPS 4-argument function pointer type matching interpreter opcode_handler
TRACE_FN_TYPE = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,  # arg0: ctx
    ctypes.c_void_p,  # arg1: sp
    ctypes.c_void_p,  # arg2: local_base
    ctypes.c_uint32,  # arg3: tos
)


def patch_at(code: bytearray, off: int, width: int, value: int) -> None:
    """Patch one already-resolved Copy-and-Patch site in O(1)."""

    assert width == 4 or width == 8
    code[off : off + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")


def emit(
    code: bytearray,
    stencil: st.Stencil,
    patches: tuple[tuple[st.Relocation, int], ...] = (),
) -> int:
    base = len(code)
    code += stencil.code
    for reloc_id, value in patches:
        width = 8 if reloc_id == st.Relocation.ADDR or reloc_id == st.Relocation.IMM64 else 4
        reloc_offset = stencil.reloc_offsets[int(reloc_id)]
        assert reloc_offset != st.NO_RELOCATION
        patch_at(code, base + reloc_offset, width, value)
    return base


def gen_pic_prologue() -> bytes:
    """
    Generates the PIC CPS 4-argument prologue for Windows or Linux:
        Saves callee-saved registers and maps arguments to execution registers:
          R10 = local_base
          R12 = sp
          R13 = ctx
          (tos passed in arg3: R9 on Win64, RCX on SysV)
    """
    code = bytearray()
    code += bytes((0x53,))  # push rbx
    code += bytes((0x41, 0x54))  # push r12
    code += bytes((0x41, 0x55))  # push r13
    code += bytes((0x41, 0x56))  # push r14
    code += bytes((0x41, 0x57))  # push r15
    if IS_WINDOWS:
        # Windows x64 ABI: (RCX=ctx, RDX=sp, R8=local_base, R9=tos)
        code += bytes((0x57,))  # push rdi
        code += bytes((0x48, 0x89, 0xE7))  # mov rdi, rsp
        code += bytes((0x4D, 0x89, 0xC2))  # mov r10, r8   (R10 = local_base)
        code += bytes((0x49, 0x89, 0xD4))  # mov r12, rdx  (R12 = sp)
        code += bytes((0x49, 0x89, 0xCD))  # mov r13, rcx  (R13 = ctx)
    else:
        # System V AMD64 ABI (Linux): (RDI=ctx, RSI=sp, RDX=local_base, RCX=tos)
        code += bytes((0x55,))  # push rbp
        code += bytes((0x48, 0x89, 0xE5))  # mov rbp, rsp
        code += bytes((0x49, 0x89, 0xD2))  # mov r10, rdx  (R10 = local_base)
        code += bytes((0x49, 0x89, 0xF4))  # mov r12, rsi  (R12 = sp)
        code += bytes((0x49, 0x89, 0xFD))  # mov r13, rdi  (R13 = ctx)
    return bytes(code)


def _make_fixed_emitter(
    stencil_bytes: bytes, depth_change: int
) -> Callable[[bytearray, WasmOperand], int]:
    def _emitter(code: bytearray, _arg: WasmOperand) -> int:
        code += stencil_bytes
        return depth_change

    return _emitter


def _emit_i32_const(code: bytearray, arg: WasmOperand) -> int:
    assert arg is not None
    emit(code, st.I32_CONST, ((st.Relocation.IMM, int(arg)),))
    return 1


def _emit_i64_const(code: bytearray, arg: WasmOperand) -> int:
    assert arg is not None
    emit(code, st.I64_CONST, ((st.Relocation.IMM64, int(arg) & 0xFFFF_FFFF_FFFF_FFFF),))
    return 1


def _emit_f32_const(code: bytearray, arg: WasmOperand) -> int:
    assert arg is not None
    emit(code, st.F32_CONST, ((st.Relocation.IMM, int(arg) & I32_MASK),))
    return 1


def _emit_f64_const(code: bytearray, arg: WasmOperand) -> int:
    assert arg is not None
    emit(code, st.F64_CONST, ((st.Relocation.IMM64, int(arg) & 0xFFFF_FFFF_FFFF_FFFF),))
    return 1


def _emit_local_get(code: bytearray, arg: WasmOperand) -> int:
    assert arg is not None
    emit(code, st.LOCAL_GET, ((st.Relocation.DISP, int(arg)),))
    return 1


def _emit_local_set(code: bytearray, arg: WasmOperand) -> int:
    assert arg is not None
    emit(code, st.LOCAL_SET, ((st.Relocation.DISP, int(arg)),))
    return -1


def _emit_local_tee(code: bytearray, arg: WasmOperand) -> int:
    assert arg is not None
    emit(code, st.LOCAL_TEE, ((st.Relocation.DISP, int(arg)),))
    return 0


_EMIT_STORAGE: ReadOnlyFlatMapStorage[int, Callable[[bytearray, WasmOperand], int]] = (
    ReadOnlyFlatMapStorage.create(
        [
            (I32_CONST, _emit_i32_const),
            (I64_CONST, _emit_i64_const),
            (F32_CONST, _emit_f32_const),
            (F64_CONST, _emit_f64_const),
            (I32_ADD, _make_fixed_emitter(st.I32_ADD.code, -1)),
            (I32_SUB, _make_fixed_emitter(st.I32_SUB.code, -1)),
            (I32_MUL, _make_fixed_emitter(st.I32_MUL.code, -1)),
            (I32_AND, _make_fixed_emitter(st.I32_AND.code, -1)),
            (I32_OR, _make_fixed_emitter(st.I32_OR.code, -1)),
            (I32_XOR, _make_fixed_emitter(st.I32_XOR.code, -1)),
            (I32_SHL, _make_fixed_emitter(st.I32_SHL.code, -1)),
            (I32_SHR_U, _make_fixed_emitter(st.I32_SHR_U.code, -1)),
            (I32_SHR_S, _make_fixed_emitter(st.I32_SHR_S.code, -1)),
            (I32_DIV_S, _make_fixed_emitter(st.I32_DIV_S.code, -1)),
            (I32_DIV_U, _make_fixed_emitter(st.I32_DIV_U.code, -1)),
            (I32_REM_S, _make_fixed_emitter(st.I32_REM_S.code, -1)),
            (I32_REM_U, _make_fixed_emitter(st.I32_REM_U.code, -1)),
            (I32_EQZ, _make_fixed_emitter(st.I32_EQZ.code, 0)),
            (I32_EQ, _make_fixed_emitter(st.I32_EQ.code, -1)),
            (I32_NE, _make_fixed_emitter(st.I32_NE.code, -1)),
            (I32_LT_S, _make_fixed_emitter(st.I32_LT_S.code, -1)),
            (I32_LT_U, _make_fixed_emitter(st.I32_LT_U.code, -1)),
            (I32_GT_S, _make_fixed_emitter(st.I32_GT_S.code, -1)),
            (I32_GT_U, _make_fixed_emitter(st.I32_GT_U.code, -1)),
            (I32_LE_S, _make_fixed_emitter(st.I32_LE_S.code, -1)),
            (I32_LE_U, _make_fixed_emitter(st.I32_LE_U.code, -1)),
            (I32_GE_S, _make_fixed_emitter(st.I32_GE_S.code, -1)),
            (I32_GE_U, _make_fixed_emitter(st.I32_GE_U.code, -1)),
            (DROP, _make_fixed_emitter(st.DROP.code, -1)),
            (LOCAL_GET, _emit_local_get),
            (LOCAL_SET, _emit_local_set),
            (LOCAL_TEE, _emit_local_tee),
        ]
    )
)
EMIT_MAP: FlatMapView[int, Callable[[bytearray, WasmOperand], int]] = _EMIT_STORAGE.view()

def _complex_helper_info(op: int) -> tuple[int, int] | None:
    """Return the direct context-member slot and raw result width."""

    if op == I64_ADD:
        return 0, 2
    if op == I64_SUB:
        return 1, 2
    if op == I64_MUL:
        return 2, 2
    if op == F32_ADD:
        return 3, 1
    if op == F32_SUB:
        return 4, 1
    if op == F32_MUL:
        return 5, 1
    if op == F32_DIV:
        return 6, 1
    if op == F64_ADD:
        return 7, 2
    if op == F64_SUB:
        return 8, 2
    if op == F64_MUL:
        return 9, 2
    if op == F64_DIV:
        return 10, 2
    return None


def _spill_hardware_stack_to_sp(code: bytearray, widths: StaticVector[int]) -> None:
    """Copy compile-time-known 8-byte hardware stack values to raw 32-bit slots."""

    value_count = len(widths)
    for value_index in range(value_count):
        source_offset = (value_count - value_index - 1) * WASM_VALUE_SLOT_BYTES
        destination_offset = sum(widths[index] for index in range(value_index)) * 4
        for word in range(widths[value_index]):
            src = source_offset + word * 4
            dst = destination_offset + word * 4
            if src == 0:
                code += bytes((0x8B, 0x04, 0x24))  # mov eax, [rsp]
            elif src < 128:
                code += bytes((0x8B, 0x44, 0x24, src))
            else:
                code += bytes((0x8B, 0x84, 0x24)) + src.to_bytes(4, "little")
            if dst == 0:
                code += bytes((0x41, 0x89, 0x04, 0x24))  # mov [r12], eax
            elif dst < 128:
                code += bytes((0x41, 0x89, 0x44, 0x24, dst))
            else:
                code += bytes((0x41, 0x89, 0x84, 0x24)) + dst.to_bytes(4, "little")


def _discard_hardware_stack(code: bytearray, value_count: int) -> None:
    assert value_count >= 0
    byte_count = value_count * WASM_VALUE_SLOT_BYTES
    if byte_count == 0:
        return
    if byte_count < 128:
        code += bytes((0x48, 0x83, 0xC4, byte_count))
    else:
        code += bytes((0x48, 0x81, 0xC4)) + byte_count.to_bytes(4, "little")


class TraceCompiler:
    """
    True Copy-and-Patch Trace Compiler for BasicBlocks producing Position-Independent Code (PIC).
        Appends machine-code stencils into continuous executable memory (`exec_memory.py`),
        emitting 16-byte physical headers (JITTraceHeader) at offset 0x00 and
        PIC code starting at offset 0x10.
    """

    # (pops, pushes) stack effect per opcode: a sorted flat_map_view over a
    # fixed, compile-time-known opcode integer vocabulary, never a dict or string.
    _STACK_EFFECT_ENTRIES: tuple[tuple[int, tuple[int, int]], ...] = tuple(
        sorted(
            [
                (I32_CONST, (0, 1)),
                (I64_CONST, (0, 1)),
                (F32_CONST, (0, 1)),
                (F64_CONST, (0, 1)),
                (LOCAL_GET, (0, 1)),
                (LOCAL_SET, (1, 0)),
                (LOCAL_TEE, (1, 1)),
                (DROP, (1, 0)),
                (I32_EQZ, (1, 1)),
                (I32_ADD, (2, 1)),
                (I32_SUB, (2, 1)),
                (I32_MUL, (2, 1)),
                (I32_DIV_S, (2, 1)),
                (I32_DIV_U, (2, 1)),
                (I32_REM_S, (2, 1)),
                (I32_REM_U, (2, 1)),
                (I32_AND, (2, 1)),
                (I32_OR, (2, 1)),
                (I32_XOR, (2, 1)),
                (I32_SHL, (2, 1)),
                (I32_SHR_S, (2, 1)),
                (I32_SHR_U, (2, 1)),
                (I32_EQ, (2, 1)),
                (I32_NE, (2, 1)),
                (I32_LT_S, (2, 1)),
                (I32_LT_U, (2, 1)),
                (I32_GT_S, (2, 1)),
                (I32_GT_U, (2, 1)),
                (I32_LE_S, (2, 1)),
                (I32_LE_U, (2, 1)),
                (I32_GE_S, (2, 1)),
                (I32_GE_U, (2, 1)),
                (I64_ADD, (2, 1)),
                (I64_SUB, (2, 1)),
                (I64_MUL, (2, 1)),
                (F32_ADD, (2, 1)),
                (F32_SUB, (2, 1)),
                (F32_MUL, (2, 1)),
                (F32_DIV, (2, 1)),
                (F64_ADD, (2, 1)),
                (F64_SUB, (2, 1)),
                (F64_MUL, (2, 1)),
                (F64_DIV, (2, 1)),
            ],
            key=lambda e: e[0],
        )
    )
    _STACK_EFFECT_ENTRIES_TUPLE: tuple[tuple[int, tuple[int, int]], ...] = tuple(
        _STACK_EFFECT_ENTRIES
    )
    STACK_EFFECTS: FlatMapView[int, tuple[int, int]] = FlatMapView(_STACK_EFFECT_ENTRIES_TUPLE)

    def compile_block(
        self,
        code: bytes,
        block: BasicBlock,
        local_widths: tuple[int, ...] | None = None,
    ) -> JITTrace | None:
        """
        Production entry point. `BasicBlock` supplies loader-computed control
        metadata; instructions are streamed from raw bytecode.
        """
        return self.compile_trace(
            block.head_pc,
            TraceBlock(
                head_pc=block.head_pc,
                instructions=iter_block_ops(code, block.head_pc & 0xFFFF, block.byte_span),
                next_pc=block.next_pc,
                loops_to=block.loops_to,
                byte_span=block.byte_span,
                local_widths=local_widths,
            ),
        )

    def compile_trace(
        self,
        head_pc: int,
        block: TraceBlock | None,
        *,
        tail_context_helper: bool = False,
    ) -> JITTrace | None:
        """
        Compiles a single loader-owned BasicBlock into a PIC native JITTrace
        using `_EMIT_TABLE` dispatch. `block.instructions` is streamed exactly once,
        never materialized into a list: `EMIT_MAP.find(op)` alone is the
        single "does this op have stencil support" signal (a `None` result
        means fall back to Tier 2 interpretation for this block) -- the
        stack-depth Trace Boundary Invariant is checked and the native code
        emitted for that same op right after, all within the one pass over
        the stream.
        """
        if block is None:
            return None
        header = JITTraceHeader(head_wasm_pc=head_pc)
        code = bytearray()
        code += gen_pic_prologue()
        sim_depth = 0
        stack_depth = 0
        stack_widths: StaticVector[int] = StaticVector(capacity=block.byte_span)
        helper_index: int | None = None
        result_words = 1
        saw_op = False
        local_widths = block.local_widths
        for op, arg in block.instructions:
            saw_op = True
            assert helper_index is None, "a complex helper must terminate a trace"
            emitter = EMIT_MAP.find(op)
            if emitter is None:
                helper_info = _complex_helper_info(op)
                if helper_info is None:
                    return None
                helper_index, helper_words = helper_info
                result_words = helper_words
                pops, pushes = self.STACK_EFFECTS[op]
                sim_depth -= pops
                assert sim_depth >= 0, "complex helper trace has operand-stack underflow"
                sim_depth += pushes
                assert sim_depth == 1, "complex helper trace must leave one result"
                _spill_hardware_stack_to_sp(code, stack_widths)
                _discard_hardware_stack(code, stack_depth)
                assert len(stack_widths) > 0
                stack_depth = 0
                stack_widths.clear()
                code += st.CONTEXT_HELPER_TAIL_JUMP.code
                helper_base = len(code) - len(st.CONTEXT_HELPER_TAIL_JUMP.code)
                helper_offset = st.CONTEXT_HELPER_TAIL_JUMP.reloc_offsets[
                    int(st.Relocation.HELPER_DISP)
                ]
                assert helper_offset != st.NO_RELOCATION
                patch_at(
                    code,
                    helper_base + helper_offset,
                    4,
                    JIT_CONTEXT_HELPER_PTR_OFFSET + helper_index * JIT_CONTEXT_WORD_BYTES,
                )
                break
            # Trace Boundary Invariant: block must be self-contained (stack depth never drops below 0)
            pops, pushes = self.STACK_EFFECTS[op]
            sim_depth -= pops
            if sim_depth < 0:
                # Depends on values on caller's operand stack -> execute safely in interpreter
                return None
            sim_depth += pushes
            emit_arg = arg
            local_width = 1
            if op == LOCAL_GET or op == LOCAL_SET or op == LOCAL_TEE:
                assert local_widths is not None
                local_index = int(arg)
                assert 0 <= local_index < len(local_widths)
                emit_arg = local_index * WASM_LOCAL_SLOT_BYTES
                local_width = local_widths[local_index]
            stack_depth += emitter(code, emit_arg)
            if op == I32_CONST or op == F32_CONST:
                assert stack_widths.push_back(1)
            elif op == I64_CONST or op == F64_CONST:
                assert stack_widths.push_back(2)
            elif op == LOCAL_GET:
                assert stack_widths.push_back(local_width)
            elif op == LOCAL_SET or op == DROP:
                stack_widths.pop_back()
            elif op == LOCAL_TEE:
                assert len(stack_widths) > 0
            elif op == I32_EQZ:
                assert len(stack_widths) > 0
            else:
                stack_widths.pop_back()
                stack_widths.pop_back()
                assert stack_widths.push_back(1)

        if not saw_op or sim_depth < 0 or sim_depth > 1 or stack_depth < 0 or stack_depth > 1:
            # Empty block, or multi-value stack outputs / underflow -- executed
            # safely by Tier 2 Interpreter instead.
            return None
        header_bytes = header.pack()
        # A trace's residual value is VM operand-stack state, not a C return
        # value -- {ExecutionContext_Layout} -- so it is written to memory
        # (via R12 / sp) rather than returned in RAX; the trace itself always
        # returns void.
        if helper_index is not None:
            # The complex helper already owns the terminal control transfer.
            # Its raw result is written to the shared stack base and committed
            # by the caller using ``result_words``.
            pass
        elif tail_context_helper:
            # A helper is a terminal complex-operation boundary.  The current
            # x64 implementation only delegates once the native operand stack
            # is empty, so the helper observes fully synchronized locals/SP.
            assert stack_depth == 0, "context-helper tail jump requires an empty native stack"
            helper_base = len(code)
            code += st.CONTEXT_HELPER_TAIL_JUMP.code
            helper_offset = st.CONTEXT_HELPER_TAIL_JUMP.reloc_offsets[
                int(st.Relocation.HELPER_DISP)
            ]
            assert helper_offset != st.NO_RELOCATION
            patch_at(code, helper_base + helper_offset, 4, JIT_CONTEXT_HELPER_PTR_OFFSET)
        else:
            if stack_depth == 1:
                code += st.SPILL_RESULT_TO_SP.code
            code += st.EPILOGUE_RETURN_VOID.code

        total_size = len(header_bytes) + len(code)
        header.trace_byte_size = total_size
        # Combine 16-byte header + PIC code stream
        full_blob = bytearray(header.pack()) + code
        buf = ExecutableBuffer(max(len(full_blob), 64))
        buf.write(0, bytes(full_blob))
        # Direct ctypes C function entry at +0x10 (past the 16-byte header)
        # Signature matches interpreter opcode handler:
        # void (*)(void* ctx, void* sp, void* local_base, uint32_t tos)
        fn = buf.function_at(
            16,
            None,
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32],
        )
        trace = JITTrace(
            head_pc=head_pc,
            fn=fn,
            size_bytes=total_size,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            has_return_val=(stack_depth > 0 or helper_index is not None),
            result_words=result_words if helper_index is not None else 1,
            buf=buf,
            raw_addr=buf.address_of(16),
        )
        trace.header = header
        return trace

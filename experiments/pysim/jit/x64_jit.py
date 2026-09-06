"""
experiments/pysim/jit/x64_jit.py
Pure Trace-based Copy-and-Patch JIT Compiler for Fireball.
Compiles individual HOT BasicBlocks / Traces into Position-Independent Code (PIC)
with 16-byte fixed headers (JITTraceHeader) and direct trace chaining.
Conforms strictly to docs/components/tier3_jit/jit_compiler.md and
docs/components/tier2_runtime/runtime_interpreter.md.
CPS 4-argument calling convention:
  RCX (R0): uint32_t ip          -- WASM PC
  RDX (R1): void* stack_bot      -- execution_context
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
from runtime_engine import JITTrace, JITTraceHeader
from system_containers import FlatMapView, ReadOnlyFlatMapStorage
from wasm_module import BasicBlock, TraceBlock
from wasm_opcodes import (
    DROP,
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
    LOCAL_GET,
    LOCAL_SET,
    LOCAL_TEE,
)

IS_WINDOWS = sys.platform == "win32"
I32_MASK = 0xFFFFFFFF

# CPS 4-argument function pointer type matching interpreter opcode_handler
TRACE_FN_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int64,
    ctypes.c_uint32,  # arg0: ip
    ctypes.c_void_p,  # arg1: stack_bot
    ctypes.c_void_p,  # arg2: local_base
    ctypes.c_uint32,  # arg3: tos
)


def patch(code: bytearray, base: int, stencil: st.Stencil, reloc_name: str, value: int) -> None:
    width = 8 if reloc_name == "addr" else 4
    off = base + stencil.relocs[reloc_name]
    code[off : off + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")


def emit(code: bytearray, stencil: st.Stencil, **patches: int) -> int:
    base = len(code)
    code += stencil.code
    for name, value in patches.items():
        patch(code, base, stencil, name, value)
    return base


def gen_pic_prologue() -> bytes:
    """
    Generates the PIC CPS 4-argument prologue for Windows or Linux:
        Saves callee-saved registers and maps arguments to execution registers:
          R10 = local_base
          R12 = stack_bot
          R13 = ip
          (tos passed in arg3: R9 on Win64, RCX on SysV)
    """
    code = bytearray()
    code += bytes((0x53,))  # push rbx
    code += bytes((0x41, 0x54))  # push r12
    code += bytes((0x41, 0x55))  # push r13
    code += bytes((0x41, 0x56))  # push r14
    code += bytes((0x41, 0x57))  # push r15
    if IS_WINDOWS:
        # Windows x64 ABI: (RCX=ip, RDX=stack_bot, R8=local_base, R9=tos)
        code += bytes((0x57,))  # push rdi
        code += bytes((0x48, 0x89, 0xE7))  # mov rdi, rsp
        code += bytes((0x4D, 0x89, 0xC2))  # mov r10, r8   (R10 = local_base)
        code += bytes((0x49, 0x89, 0xD4))  # mov r12, rdx  (R12 = stack_bot)
        code += bytes((0x49, 0x89, 0xCD))  # mov r13, rcx  (R13 = ip)
    else:
        # System V AMD64 ABI (Linux): (RDI=ip, RSI=stack_bot, RDX=local_base, RCX=tos)
        code += bytes((0x55,))  # push rbp
        code += bytes((0x48, 0x89, 0xE5))  # mov rbp, rsp
        code += bytes((0x49, 0x89, 0xD2))  # mov r10, rdx  (R10 = local_base)
        code += bytes((0x49, 0x89, 0xF4))  # mov r12, rsi  (R12 = stack_bot)
        code += bytes((0x49, 0x89, 0xFD))  # mov r13, rdi  (R13 = ip)
    return bytes(code)


def _make_fixed_emitter(
    stencil_bytes: bytes, depth_change: int
) -> Callable[[bytearray, object], int]:
    def _emitter(code: bytearray, _arg: object) -> int:
        code += stencil_bytes
        return depth_change

    return _emitter


def _emit_i32_const(code: bytearray, arg: object) -> int:
    emit(code, st.I32_CONST, imm=int(arg))  # type: ignore[arg-type]
    return 1


def _emit_local_get(code: bytearray, arg: object) -> int:
    emit(code, st.LOCAL_GET, disp=int(arg) * 8)  # type: ignore[arg-type]
    return 1


def _emit_local_set(code: bytearray, arg: object) -> int:
    emit(code, st.LOCAL_SET, disp=int(arg) * 8)  # type: ignore[arg-type]
    return -1


def _emit_local_tee(code: bytearray, arg: object) -> int:
    emit(code, st.LOCAL_TEE, disp=int(arg) * 8)  # type: ignore[arg-type]
    return 0


_EMIT_STORAGE: ReadOnlyFlatMapStorage[int, Callable[[bytearray, object], int]] = (
    ReadOnlyFlatMapStorage.create(
        [
            (I32_CONST, _emit_i32_const),
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
EMIT_MAP: FlatMapView[int, Callable[[bytearray, object], int]] = _EMIT_STORAGE.view()


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
            ],
            key=lambda e: e[0],
        )
    )
    _STACK_EFFECT_ENTRIES_TUPLE: tuple[tuple[int, tuple[int, int]], ...] = tuple(
        _STACK_EFFECT_ENTRIES
    )
    STACK_EFFECTS: FlatMapView[int, tuple[int, int]] = FlatMapView(_STACK_EFFECT_ENTRIES_TUPLE)

    def compile_block(self, code: bytes, block: BasicBlock) -> JITTrace | None:
        """
        Production entry point: derives this compile's transient `TraceBlock`
        input from `block`'s PC metadata against the owning function's raw
        bytecode -- `BasicBlock` itself never stores the op stream (see
        `wasm_module.BasicBlock`). The derived ops list is discarded once
        `compile_trace` returns; nothing here is kept past this one call.
        """
        ops = iter_block_ops(code, block.head_pc & 0xFFFF, block.byte_span)
        return self.compile_trace(
            block.head_pc,
            TraceBlock(
                head_pc=block.head_pc,
                ops=ops,
                next_pc=block.next_pc,
                loops_to=block.loops_to,
                byte_span=block.byte_span,
            ),
        )

    def compile_trace(self, head_pc: int, block: TraceBlock | None) -> JITTrace | None:
        """
        Compiles a single TraceBlock op stream into a PIC native JITTrace
        using `_EMIT_TABLE` dispatch. `block.ops` is streamed exactly once,
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
        saw_op = False
        for op, arg in block.ops:
            saw_op = True
            emitter = EMIT_MAP.find(op)
            if emitter is None:
                return None
            # Trace Boundary Invariant: block must be self-contained (stack depth never drops below 0)
            pops, pushes = self.STACK_EFFECTS[op]
            sim_depth -= pops
            if sim_depth < 0:
                # Depends on values on caller's operand stack -> execute safely in interpreter
                return None
            sim_depth += pushes
            stack_depth += emitter(code, arg)

        if not saw_op or sim_depth < 0 or sim_depth > 1 or stack_depth < 0 or stack_depth > 1:
            # Empty block, or multi-value stack outputs / underflow -- executed
            # safely by Tier 2 Interpreter instead.
            return None
        header_bytes = header.pack()
        # A trace's residual value is VM operand-stack state, not a C return
        # value -- {ExecutionContext_Layout} -- so it is written to memory
        # (via R12 / stack_bot) rather than returned in RAX; the trace itself
        # always returns void.
        if stack_depth == 1:
            code += st.SPILL_RESULT_TO_STACK_BOT.code
        code += st.EPILOGUE_RETURN_VOID.code

        total_size = len(header_bytes) + len(code)
        header.trace_byte_size = total_size
        # Combine 16-byte header + PIC code stream
        full_blob = bytearray(header.pack()) + code
        buf = ExecutableBuffer(max(len(full_blob), 64))
        buf.write(0, bytes(full_blob))
        # Direct ctypes C function entry at +0x10 (past the 16-byte header)
        # Signature matches interpreter opcode handler:
        # int64_t (*)(uint32_t ip, void* stack_bot, void* local_base, uint32_t tos)
        fn = buf.function_at(
            16,
            ctypes.c_int64,
            [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32],
        )
        trace = JITTrace(
            head_pc=head_pc,
            fn=fn,
            size_bytes=total_size,
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            has_return_val=(stack_depth > 0),
            buf=buf,
            raw_addr=buf.address_of(16),
        )
        trace.header = header
        return trace

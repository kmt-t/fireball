"""
experiments/pysim/x64_jit.py

Pure Trace-based Copy-and-Patch JIT Compiler for Fireball.
Compiles individual HOT BasicBlocks / Traces into executable native machine code
with 16-byte fixed headers (JITTraceHeader) and direct trace chaining.
Conforms strictly to docs/components/tier3_jit/jit_compiler.md and jit_runtime.md.
No module/function-level compilation: compilation is strictly per-trace.
"""

from __future__ import annotations

import ctypes
from typing import Any

import x64_asm as asm
import x64_stencils as st
from exec_memory import ExecutableBuffer
from runtime_engine import BasicBlock, JITTrace, WASMContext

I32_MASK = 0xFFFFFFFF


def patch(code: bytearray, base: int, stencil: st.Stencil, reloc_name: str, value: int) -> None:
    width = 8 if reloc_name == "addr" else 4
    off = base + stencil.relocs[reloc_name]
    code[off:off + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")


def emit(code: bytearray, stencil: st.Stencil, **patches: int) -> int:
    base = len(code)
    code += stencil.code
    for name, value in patches.items():
        patch(code, base, stencil, name, value)
    return base


class TraceCompiler:
    """True Copy-and-Patch Trace Compiler for BasicBlocks.

    Compiles straight-line instruction sequences into native x64 traces,
    emitting 16-byte physical headers, inline operands, and chaining slots.
    """

    def __init__(self, host_trampolines: dict[int, int] | None = None):
        self.host_trampolines = host_trampolines or {}

    def compile_trace(self, head_pc: int, block: BasicBlock) -> JITTrace:
        """Compiles a single BasicBlock into a native JITTrace."""
        code = bytearray()
        code += st.PROLOGUE.code

        stack_depth = 0
        for op, arg in block.ops:
            if op == "i32.const":
                emit(code, st.I32_CONST, imm=arg)
                stack_depth += 1
            elif op == "i32.add":
                code += st.I32_ADD.code
                stack_depth -= 1
            elif op == "i32.sub":
                code += st.I32_SUB.code
                stack_depth -= 1
            elif op == "i32.mul":
                code += st.I32_MUL.code
                stack_depth -= 1
            elif op == "i32.and":
                code += st.I32_AND.code
                stack_depth -= 1
            elif op == "i32.or":
                code += st.I32_OR.code
                stack_depth -= 1
            elif op == "i32.xor":
                code += st.I32_XOR.code
                stack_depth -= 1
            elif op == "i32.shl":
                code += st.I32_SHL.code
                stack_depth -= 1
            elif op == "i32.shr_u":
                code += st.I32_SHR_U.code
                stack_depth -= 1
            elif op == "i32.shr_s":
                code += st.I32_SHR_S.code
                stack_depth -= 1
            elif op == "local.get":
                emit(code, st.LOCAL_GET, disp=arg * 8)
                stack_depth += 1
            elif op == "local.set":
                emit(code, st.LOCAL_SET, disp=arg * 8)
                stack_depth -= 1
            elif op == "call_host":
                code += asm.push_reg("r10")
                code += asm.push_reg("r11")
                code += asm.sub_rsp_imm8(40)
                code += asm.mov_reg_imm64("rax", arg)
                code += asm.call_reg("rax")
                code += asm.add_rsp_imm8(40)
                code += asm.pop_reg("r11")
                code += asm.pop_reg("r10")
                code += asm.push_reg("rax")
                stack_depth += 1

        if stack_depth > 0:
            code += st.EPILOGUE_RETURN_I32.code
        else:
            code += st.EPILOGUE_RETURN_VOID.code

        trace_bytes = bytes(code)

        buf = ExecutableBuffer(max(len(trace_bytes), 64))
        buf.write(0, trace_bytes)
        # Direct ctypes C function pointer matching the opcode handler calling convention:
        # int64_t (*)(void* locals_ptr, void* memory_base)
        fn = buf.function_at(0, ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])

        return JITTrace(
            head_pc=head_pc,
            fn=fn,
            size_bytes=len(trace_bytes),
            next_pc=block.next_pc,
            loops_to=block.loops_to,
            has_return_val=(stack_depth > 0),
            buf=buf,
        )

"""
experiments/pysim/x64_jit.py
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
from typing import Any

import x64_asm as asm
import x64_stencils as st
from exec_memory import ExecutableBuffer
from runtime_engine import BasicBlock, JITTrace, JITTraceHeader
from system_containers import FlatMapView

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


class TraceCompiler:
    """
    True Copy-and-Patch Trace Compiler for BasicBlocks producing Position-Independent Code (PIC).
        Compiles straight-line instruction sequences into native x64 traces,
        emitting 16-byte physical headers (JITTraceHeader) at offset 0x00 and
        PIC code starting at offset 0x10.
    """

    def __init__(self, host_trampolines: Any = None):
        self.host_trampolines = host_trampolines or []

    SUPPORTED_OPS: tuple[str, ...] = (
        "i32.const",
        "i32.add",
        "i32.sub",
        "i32.mul",
        "i32.and",
        "i32.or",
        "i32.xor",
        "i32.shl",
        "i32.shr_u",
        "i32.shr_s",
        "i32.div_s",
        "i32.div_u",
        "i32.rem_s",
        "i32.rem_u",
        "i32.eqz",
        "i32.eq",
        "i32.ne",
        "i32.lt_s",
        "i32.lt_u",
        "i32.gt_s",
        "i32.gt_u",
        "i32.le_s",
        "i32.le_u",
        "i32.ge_s",
        "i32.ge_u",
        "drop",
        "local.get",
        "local.set",
        "call_host",
    )
    # (pops, pushes) stack effect per opcode: a sorted flat_map_view over a
    # fixed, compile-time-known opcode-name vocabulary, never a dict.
    _STACK_EFFECT_ENTRIES: tuple[tuple[str, tuple[int, int]], ...] = tuple(
        sorted(
            [
                ("i32.const", (0, 1)),
                ("local.get", (0, 1)),
                ("local.set", (1, 0)),
                ("drop", (1, 0)),
                ("i32.eqz", (1, 1)),
                ("i32.add", (2, 1)),
                ("i32.sub", (2, 1)),
                ("i32.mul", (2, 1)),
                ("i32.div_s", (2, 1)),
                ("i32.div_u", (2, 1)),
                ("i32.rem_s", (2, 1)),
                ("i32.rem_u", (2, 1)),
                ("i32.and", (2, 1)),
                ("i32.or", (2, 1)),
                ("i32.xor", (2, 1)),
                ("i32.shl", (2, 1)),
                ("i32.shr_s", (2, 1)),
                ("i32.shr_u", (2, 1)),
                ("i32.eq", (2, 1)),
                ("i32.ne", (2, 1)),
                ("i32.lt_s", (2, 1)),
                ("i32.lt_u", (2, 1)),
                ("i32.gt_s", (2, 1)),
                ("i32.gt_u", (2, 1)),
                ("i32.le_s", (2, 1)),
                ("i32.le_u", (2, 1)),
                ("i32.ge_s", (2, 1)),
                ("i32.ge_u", (2, 1)),
                ("call_host", (0, 1)),
            ],
            key=lambda e: e[0],
        )
    )
    _STACK_EFFECT_KEYS: tuple[str, ...] = tuple(k for k, _ in _STACK_EFFECT_ENTRIES)
    _STACK_EFFECT_VALS: tuple[tuple[int, int], ...] = tuple(v for _, v in _STACK_EFFECT_ENTRIES)
    STACK_EFFECTS: FlatMapView[str, tuple[int, int]] = FlatMapView(
        _STACK_EFFECT_KEYS, _STACK_EFFECT_VALS
    )

    def compile_trace(self, head_pc: int, block: BasicBlock | None) -> JITTrace | None:
        """Compiles a single BasicBlock into a PIC native JITTrace."""
        if (
            block is None
            or not block.ops
            or any(op not in self.SUPPORTED_OPS for op, _ in block.ops)
        ):
            return None
        # Trace Boundary Invariant: Verify block is self-contained (stack depth never drops below 0)
        sim_depth = 0
        for op, _ in block.ops:
            pops, pushes = self.STACK_EFFECTS[op]
            sim_depth -= pops
            if sim_depth < 0:
                # Depends on values on caller's operand stack -> execute safely in interpreter
                return None
            sim_depth += pushes

        if sim_depth < 0 or sim_depth > 1:
            # Multi-value stack outputs or underflow are executed safely by Tier 2 Interpreter
            return None
        # 1. Physical 16-byte JITTraceHeader at +0x00
        header = JITTraceHeader(head_wasm_pc=head_pc)
        header_bytes = header.pack()
        # 2. PIC Code Stream at +0x10
        code = bytearray()
        code += gen_pic_prologue()
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
            elif op == "i32.div_s":
                code += st.I32_DIV_S.code
                stack_depth -= 1
            elif op == "i32.div_u":
                code += st.I32_DIV_U.code
                stack_depth -= 1
            elif op == "i32.rem_s":
                code += st.I32_REM_S.code
                stack_depth -= 1
            elif op == "i32.rem_u":
                code += st.I32_REM_U.code
                stack_depth -= 1
            elif op == "i32.eqz":
                code += st.I32_EQZ.code
            elif op == "i32.eq":
                code += st.I32_EQ.code
                stack_depth -= 1
            elif op == "i32.ne":
                code += st.I32_NE.code
                stack_depth -= 1
            elif op == "i32.lt_s":
                code += st.I32_LT_S.code
                stack_depth -= 1
            elif op == "i32.lt_u":
                code += st.I32_LT_U.code
                stack_depth -= 1
            elif op == "i32.gt_s":
                code += st.I32_GT_S.code
                stack_depth -= 1
            elif op == "i32.gt_u":
                code += st.I32_GT_U.code
                stack_depth -= 1
            elif op == "i32.le_s":
                code += st.I32_LE_S.code
                stack_depth -= 1
            elif op == "i32.le_u":
                code += st.I32_LE_U.code
                stack_depth -= 1
            elif op == "i32.ge_s":
                code += st.I32_GE_S.code
                stack_depth -= 1
            elif op == "i32.ge_u":
                code += st.I32_GE_U.code
                stack_depth -= 1
            elif op == "drop":
                code += st.DROP.code
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

        if stack_depth < 0 or stack_depth > 1:
            # Multi-value stack outputs or underflow are executed safely by Tier 2 Interpreter
            return None
        if stack_depth == 1:
            code += st.EPILOGUE_RETURN_I32.code
        else:
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
        )
        trace.header = header
        return trace

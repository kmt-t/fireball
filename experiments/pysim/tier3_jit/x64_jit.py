"""
experiments/pysim/tier3_jit/x64_jit.py
Pure Trace-based Copy-and-Patch JIT Compiler for Fireball.
Compiles individual HOT BasicBlocks / Traces into Position-Independent Code (PIC)
with 56-byte x64 fixed headers (JITTraceHeader) and direct trace chaining.
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
from collections.abc import Iterable, Sequence

import x64_stencils as st
from common_code import (
    COMMON_TYPED_I32_HELPER_OFFSET,
    TRACE_ENTRY_STUB_BYTES,
    JITCodeCacheRegion,
)
from config import JIT_CACHE_ACTIVE_OFFSET_BYTES, JIT_X64_TRACE_HEADER_BYTES
from jit_cache import JITTrace, JITTraceHeader
from system_containers import ReadOnlyFlatMapView, StaticVector
from wasm_module import (
    WASM_LOCAL_SLOT_BYTES,
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


# Register-resident operand-cache instructions.  R9d is TOS, R11d is NOS,
# and R12 is the shared Native operand-stack write cursor.  These byte strings
# never touch RSP; RSP is reserved for the native call frame.
_MOV_NOS_FROM_TOS = bytes((0x45, 0x89, 0xCB))
_MOV_TOS_FROM_NOS = bytes((0x45, 0x89, 0xD9))
_STORE_TOS_TO_LOCAL = bytes((0x45, 0x89, 0x8A))
_STORE_TOS_TO_SP = bytes((0x45, 0x89, 0x8C, 0x24))
_STORE_NOS_TO_SP = bytes((0x45, 0x89, 0x9C, 0x24))
_LOAD_TOS_FROM_SP = bytes((0x45, 0x8B, 0x8C, 0x24))
_LOAD_NOS_FROM_SP = bytes((0x45, 0x8B, 0x9C, 0x24))

_STACK_LOCATION_TOS = -1
_STACK_LOCATION_NOS = -2


def _load_tos_imm32(value: int) -> bytes:
    return bytes((0x41, 0xB9)) + (value & I32_MASK).to_bytes(4, "little")


def _load_tos_local(offset: int) -> bytes:
    return bytes((0x45, 0x8B, 0x8A)) + (offset & I32_MASK).to_bytes(4, "little")


def _store_tos_local(offset: int) -> bytes:
    return _STORE_TOS_TO_LOCAL + (offset & I32_MASK).to_bytes(4, "little")


def _store_register_to_sp(code: bytearray, location: int, slot: int) -> None:
    assert location == _STACK_LOCATION_TOS or location == _STACK_LOCATION_NOS
    assert slot >= 0
    code += (_STORE_TOS_TO_SP if location == _STACK_LOCATION_TOS else _STORE_NOS_TO_SP)
    code += (slot * 4).to_bytes(4, "little")


def _load_register_from_sp(code: bytearray, location: int, slot: int) -> None:
    assert location == _STACK_LOCATION_TOS or location == _STACK_LOCATION_NOS
    assert slot >= 0
    code += (_LOAD_TOS_FROM_SP if location == _STACK_LOCATION_TOS else _LOAD_NOS_FROM_SP)
    code += (slot * 4).to_bytes(4, "little")


def _emit_raw_const_to_sp(code: bytearray, value: int, slot: int) -> None:
    code += _load_tos_imm32(value)
    _store_register_to_sp(code, _STACK_LOCATION_TOS, slot)


def _complex_helper_index(operation: int) -> int:
    if operation == I32_DIV_S:
        return 11
    if operation == I32_DIV_U:
        return 12
    if operation == I32_REM_S:
        return 13
    if operation == I32_REM_U:
        return 14
    if operation == I64_ADD:
        return 0
    if operation == I64_SUB:
        return 1
    if operation == I64_MUL:
        return 2
    if operation == F32_ADD:
        return 3
    if operation == F32_SUB:
        return 4
    if operation == F32_MUL:
        return 5
    if operation == F32_DIV:
        return 6
    if operation == F64_ADD:
        return 7
    if operation == F64_SUB:
        return 8
    if operation == F64_MUL:
        return 9
    if operation == F64_DIV:
        return 10
    return -1


def _complex_value_width(operation: int) -> int:
    if operation == I64_CONST or operation == F64_CONST:
        return 2
    assert operation == F32_CONST
    return 1


def _emit_register_push(
    code: bytearray,
    operation: int,
    arg: WasmOperand,
    stack_locations: StaticVector[int],
    spilled_words: int,
) -> int:
    """Push a value while recording its register or shared-stack location."""
    if len(stack_locations) >= 2 and stack_locations[-2] == _STACK_LOCATION_NOS:
        _store_register_to_sp(code, _STACK_LOCATION_NOS, spilled_words)
        stack_locations[-2] = spilled_words
        spilled_words += 1
    elif len(stack_locations) >= 2:
        assert stack_locations[-2] >= 0
    if stack_locations:
        assert stack_locations[-1] == _STACK_LOCATION_TOS
        code += _MOV_NOS_FROM_TOS
        stack_locations[-1] = _STACK_LOCATION_NOS
    assert arg is not None
    if operation == I32_CONST:
        code += _load_tos_imm32(int(arg))
    else:
        assert operation == LOCAL_GET
        code += _load_tos_local(int(arg))
    assert stack_locations.push_back(_STACK_LOCATION_TOS)
    return spilled_words


def _emit_register_binary(code: bytearray, operation: int) -> None:
    # The operation is intentionally encoded as a register operation:
    # R9=TOS receives (R11=NOS) op (R9=TOS).
    if operation == I32_ADD:
        code += bytes((0x45, 0x01, 0xD9))
    elif operation == I32_SUB:
        code += bytes((0x45, 0x29, 0xCB, 0x45, 0x89, 0xD9))
    elif operation == I32_MUL:
        code += bytes((0x45, 0x0F, 0xAF, 0xCB))
    elif operation == I32_AND:
        code += bytes((0x45, 0x21, 0xD9))
    elif operation == I32_OR:
        code += bytes((0x45, 0x09, 0xD9))
    elif operation == I32_XOR:
        code += bytes((0x45, 0x31, 0xD9))
    else:
        condition = 0
        if operation == I32_EQ:
            condition = 0x94
        elif operation == I32_NE:
            condition = 0x95
        elif operation == I32_LT_S:
            condition = 0x9C
        elif operation == I32_LT_U:
            condition = 0x92
        elif operation == I32_GT_S:
            condition = 0x9F
        elif operation == I32_GT_U:
            condition = 0x97
        elif operation == I32_LE_S:
            condition = 0x9E
        elif operation == I32_LE_U:
            condition = 0x96
        elif operation == I32_GE_S:
            condition = 0x9D
        elif operation == I32_GE_U:
            condition = 0x93
        else:
            assert False
        code += bytes((0x45, 0x39, 0xCB))
        code += bytes((0x0F, condition, 0xC0))
        code += bytes((0x44, 0x0F, 0xB6, 0xC8))


def _emit_register_eqz(code: bytearray) -> None:
    code += bytes((0x45, 0x85, 0xC9, 0x0F, 0x94, 0xC0, 0x44, 0x0F, 0xB6, 0xC8))


def _emit_register_shift(code: bytearray, operation: int) -> None:
    code += bytes((0x44, 0x89, 0xC9))
    code += bytes((0x45, 0x89, 0xD9))
    if operation == I32_SHL:
        code += bytes((0x41, 0xD3, 0xE1))
    elif operation == I32_SHR_S:
        code += bytes((0x41, 0xD3, 0xF9))
    else:
        assert operation == I32_SHR_U
        code += bytes((0x41, 0xD3, 0xE9))


def _emit_register_pop(code: bytearray, stack_locations: StaticVector[int], spilled_words: int) -> int:
    """Pop TOS and promote the next compile-time location into TOS."""
    assert stack_locations
    assert stack_locations[-1] == _STACK_LOCATION_TOS
    stack_locations.pop_back()
    if not stack_locations:
        return spilled_words
    if stack_locations[-1] == _STACK_LOCATION_NOS:
        stack_locations[-1] = _STACK_LOCATION_TOS
        return spilled_words
    assert stack_locations[-1] == spilled_words - 1
    _load_register_from_sp(code, _STACK_LOCATION_TOS, stack_locations[-1])
    stack_locations.pop_back()
    assert spilled_words > 0
    spilled_words -= 1
    assert stack_locations.push_back(_STACK_LOCATION_TOS)
    return spilled_words


def _emit_register_binary_with_spill(
    code: bytearray,
    operation: int,
    stack_locations: StaticVector[int],
    spilled_words: int,
) -> int:
    """Materialize NOS from shared Native storage when the cache is shallow."""
    assert len(stack_locations) >= 2
    assert stack_locations[-1] == _STACK_LOCATION_TOS
    if stack_locations[-2] >= 0:
        assert stack_locations[-2] == spilled_words - 1
        _load_register_from_sp(code, _STACK_LOCATION_NOS, stack_locations[-2])
        stack_locations[-2] = _STACK_LOCATION_NOS
        assert spilled_words > 0
        spilled_words -= 1
    assert stack_locations[-2] == _STACK_LOCATION_NOS
    _emit_register_binary(code, operation)
    stack_locations.pop_back()
    stack_locations.pop_back()
    assert stack_locations.push_back(_STACK_LOCATION_TOS)
    return spilled_words


class TraceCompiler:
    """
    True Copy-and-Patch Trace Compiler for BasicBlocks producing Position-Independent Code (PIC).
        Appends machine-code stencils into continuous executable memory (`exec_memory.py`),
        emitting 56-byte physical headers (JITTraceHeader) at offset 0x00 and
        a small entry stub at offset 0x38.  The stub and all exits route via
        the common AAPCS area selected by header offsets.
    """

    def __init__(self) -> None:
        # Standalone traces use the same shared-area ABI as cache-resident
        # traces.  The rotating cache supplies its own region at install time.
        self._standalone_region = JITCodeCacheRegion()

    # (pops, pushes) stack effect per opcode: a sorted flat_map_view over a
    # fixed, compile-time-known opcode integer vocabulary, never a dict or string.
    _STACK_EFFECT_ENTRIES: StaticVector[tuple[int, tuple[int, int]]] = StaticVector.of(
        sorted(
            (
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
            ),
            key=lambda e: e[0],
        )
    )
    STACK_EFFECTS: ReadOnlyFlatMapView[int, tuple[int, int]] = ReadOnlyFlatMapView(
        _STACK_EFFECT_ENTRIES
    )

    def compile_trace(
        self,
        head_pc: int,
        instructions: Iterable[tuple[int, WasmOperand]],
        next_pc: int | None,
        loops_to: int | None,
        byte_span: int,
        local_widths: Sequence[int],
        *,
        tail_context_helper: bool = False,
        helper_target_addr: int = 0,
    ) -> JITTrace | None:
        """
        Compiles a single loader-owned BasicBlock into a PIC native JITTrace
        `instructions` is streamed exactly once and never materialized.
        The compile-time cache map is ordered from NOS to TOS; its last entry
        is R9/TOS and its preceding entry is R11/NOS.  The map is only the
        compiler's proof of register placement.  The generated code never
        initializes or uses RSP as a WASM operand stack.
        """
        assert byte_span > 0
        header = JITTraceHeader(head_wasm_pc=head_pc)
        header.chain_next_pc = next_pc or 0
        # mov rax, <body address>; jmp <header.common_prologue_offset>
        code = bytearray(bytes((0x48, 0xB8)) + (0).to_bytes(8, "little"))
        code += bytes((0xE9, 0, 0, 0, 0))
        assert len(code) == TRACE_ENTRY_STUB_BYTES
        # Ordered bottom-to-top location map.  Negative values denote the two
        # register cache entries; non-negative values are slots already
        # written to the shared Native operand stack at [R12 + slot * 4].
        stack_locations: StaticVector[int] = StaticVector(capacity=byte_span + 1)
        spilled_words = 0
        helper_words = 0
        helper_index = -1
        typed_i32_helper = False
        saw_op = False
        for op, arg in instructions:
            saw_op = True
            helper_index = _complex_helper_index(op)
            if helper_index >= 0:
                assert arg is None
                if 11 <= helper_index <= 14:
                    # Integer division/remainder helpers receive typed scalar
                    # arguments from the register cache.  The helper writes
                    # its result through the explicit result-slot pointer.
                    if len(stack_locations) != 2 or spilled_words != 0 or helper_words != 0:
                        return None
                    assert stack_locations[0] == _STACK_LOCATION_NOS
                    assert stack_locations[1] == _STACK_LOCATION_TOS
                    stack_locations.clear()
                    typed_i32_helper = True
                else:
                    assert not stack_locations
                    assert helper_words == 4 or helper_words == 2
                    expected_words = 2 if 3 <= helper_index <= 6 else 4
                    assert helper_words == expected_words
                break
            stack_effect = self.STACK_EFFECTS.find(op)
            if stack_effect is None:
                return None
            pops, pushes = stack_effect
            if pops > len(stack_locations):
                # A basic block may not consume an operand owned by its caller.
                # The block extractor must split before that boundary.
                return None
            if op == I32_CONST or op == LOCAL_GET:
                assert pushes == 1
                if op == LOCAL_GET:
                    assert arg is not None
                    local_index = int(arg)
                    assert 0 <= local_index < len(local_widths)
                    if local_widths[local_index] != 1:
                        return None
                    arg = local_index * WASM_LOCAL_SLOT_BYTES
                spilled_words = _emit_register_push(
                    code, op, arg, stack_locations, spilled_words
                )
            elif op == I64_CONST or op == F32_CONST or op == F64_CONST:
                assert arg is not None
                if stack_locations:
                    # The wide/floating raw-slot path cannot preserve a live
                    # register-cached operand beneath the value. Decline this
                    # block so RuntimeEngine leaves it to the interpreter.
                    return None
                width = _complex_value_width(op)
                raw_value = int(arg)
                for word in range(width):
                    _emit_raw_const_to_sp(code, (raw_value >> (word * 32)) & I32_MASK, helper_words)
                    helper_words += 1
            elif op == LOCAL_SET or op == DROP:
                if op == LOCAL_SET:
                    assert arg is not None
                    local_index = int(arg)
                    assert 0 <= local_index < len(local_widths)
                    if local_widths[local_index] != 1:
                        return None
                    code += _store_tos_local(local_index * WASM_LOCAL_SLOT_BYTES)
                spilled_words = _emit_register_pop(code, stack_locations, spilled_words)
            elif op == LOCAL_TEE:
                assert arg is not None
                local_index = int(arg)
                assert 0 <= local_index < len(local_widths)
                if local_widths[local_index] != 1 or not stack_locations:
                    return None
                code += _store_tos_local(local_index * WASM_LOCAL_SLOT_BYTES)
            elif op == I32_EQZ:
                if not stack_locations or stack_locations[-1] != _STACK_LOCATION_TOS:
                    return None
                _emit_register_eqz(code)
            elif (
                op == I32_ADD
                or op == I32_SUB
                or op == I32_MUL
                or op == I32_AND
                or op == I32_OR
                or op == I32_XOR
                or op == I32_EQ
                or op == I32_NE
                or op == I32_LT_S
                or op == I32_LT_U
                or op == I32_GT_S
                or op == I32_GT_U
                or op == I32_LE_S
                or op == I32_LE_U
                or op == I32_GE_S
                or op == I32_GE_U
            ):
                spilled_words = _emit_register_binary_with_spill(
                    code, op, stack_locations, spilled_words
                )
            elif op == I32_SHL or op == I32_SHR_S or op == I32_SHR_U:
                assert len(stack_locations) >= 2
                assert stack_locations[-1] == _STACK_LOCATION_TOS
                if stack_locations[-2] >= 0:
                    assert stack_locations[-2] == spilled_words - 1
                    _load_register_from_sp(code, _STACK_LOCATION_NOS, stack_locations[-2])
                    stack_locations[-2] = _STACK_LOCATION_NOS
                    assert spilled_words > 0
                    spilled_words -= 1
                assert stack_locations[-2] == _STACK_LOCATION_NOS
                _emit_register_shift(code, op)
                stack_locations.pop_back()
                stack_locations.pop_back()
                assert stack_locations.push_back(_STACK_LOCATION_TOS)
            else:
                return None

        if (
            not saw_op
            or len(stack_locations) > 1
            or spilled_words != 0
            or (helper_index < 0 and helper_words != 0)
        ):
            # Empty blocks and traces with residual values below TOS are not
            # valid standalone JIT exits.  They remain interpreter work.
            return None
        # A trace's residual value is VM operand-stack state, not a C return
        # value -- {ExecutionContext_Layout} -- so it is written to memory
        # (via R12 / sp) rather than returned in RAX; the trace itself always
        # returns void.
        chain_header_patch_offset = -1
        chain_fallback_patch_offset = -1
        if tail_context_helper:
            assert helper_index < 0
            assert not stack_locations
            assert helper_target_addr > 0
            helper_base = len(code)
            # lea rax, [rip + header]; jmp common helper
            code += bytes((0x48, 0x8D, 0x05, 0, 0, 0, 0, 0xE9, 0, 0, 0, 0))
            helper_header_patch_offset = JIT_X64_TRACE_HEADER_BYTES + helper_base + 3
            helper_exit_patch_offset = JIT_X64_TRACE_HEADER_BYTES + helper_base + 8
            exit_patch_offset = -1
        elif helper_index >= 0:
            if helper_target_addr == 0:
                return None
            assert not stack_locations
            helper_base = len(code)
            code += bytes((0x48, 0x8D, 0x05, 0, 0, 0, 0, 0xE9, 0, 0, 0, 0))
            helper_header_patch_offset = JIT_X64_TRACE_HEADER_BYTES + helper_base + 3
            helper_exit_patch_offset = JIT_X64_TRACE_HEADER_BYTES + helper_base + 8
            exit_patch_offset = -1
        else:
            if stack_locations:
                assert stack_locations[0] == _STACK_LOCATION_TOS
                _store_register_to_sp(code, _STACK_LOCATION_TOS, 0)
            helper_header_patch_offset = -1
            helper_exit_patch_offset = -1
            if next_pc is not None and loops_to is None:
                # The context field stores a WASM PC rather than a native
                # pointer, so the x64 implementation publishes it as u32.
                code += bytes((0x41, 0xC7, 0x45, 0x00))
                code += (next_pc & I32_MASK).to_bytes(4, "little")
                # lea rax, [rip + header]
                chain_header_patch_offset = JIT_X64_TRACE_HEADER_BYTES + len(code) + 3
                code += bytes((0x48, 0x8D, 0x05, 0, 0, 0, 0))
                # mov rdx, [rax + x64 chain_target_addr]
                code += bytes((0x48, 0x8B, 0x50, 0x10))
                # test rdx, rdx; unresolved chains use the common epilogue
                code += bytes((0x48, 0x85, 0xD2))
                chain_fallback_patch_offset = JIT_X64_TRACE_HEADER_BYTES + len(code) + 2
                code += bytes((0x0F, 0x84, 0, 0, 0, 0))
                # A resolved target points past its entry stub, so the common
                # prologue is not entered a second time.
                code += bytes((0xFF, 0xE2))
                exit_patch_offset = -1
            else:
                exit_patch_offset = JIT_X64_TRACE_HEADER_BYTES + len(code) + 1
                code += bytes((0xE9, 0, 0, 0, 0))

        if tail_context_helper:
            helper_index = -1
        header.helper_index = helper_index
        if typed_i32_helper:
            header.common_helper_offset = COMMON_TYPED_I32_HELPER_OFFSET
        header.helper_target_addr = helper_target_addr
        total_size = JIT_X64_TRACE_HEADER_BYTES + len(code)
        header.trace_byte_size = total_size
        # Combine the fixed header and the PIC entry/body stream.  Relocation
        # sites are patched only when this blob is installed into a region.
        full_blob = bytearray(header.pack()) + code
        trace = JITTrace(
            head_pc=head_pc,
            size_bytes=total_size,
            next_pc=next_pc,
            loops_to=loops_to,
            has_return_val=bool(stack_locations) or helper_index >= 0,
            result_words=(
                2
                if helper_index >= 0
                and (helper_index <= 2 or 7 <= helper_index <= 10)
                else 1
            ),
            code_blob=bytes(full_blob),
            entry_body_patch_offset=JIT_X64_TRACE_HEADER_BYTES + 2,
            entry_prologue_patch_offset=JIT_X64_TRACE_HEADER_BYTES + 11,
            exit_patch_offset=exit_patch_offset,
            helper_header_patch_offset=helper_header_patch_offset,
            helper_exit_patch_offset=helper_exit_patch_offset,
            chain_header_patch_offset=chain_header_patch_offset,
            chain_fallback_patch_offset=chain_fallback_patch_offset,
            helper_index=helper_index,
            helper_target_addr=helper_target_addr,
        )
        trace.header = header
        assert JIT_CACHE_ACTIVE_OFFSET_BYTES + total_size <= self._standalone_region.region_bytes
        trace.code_offset = JIT_CACHE_ACTIVE_OFFSET_BYTES
        fn, raw_addr = self._standalone_region.install_trace(
            trace.code_offset,
            trace.code_blob,
            trace.entry_body_patch_offset,
            trace.entry_prologue_patch_offset,
            trace.exit_patch_offset,
            trace.helper_header_patch_offset,
            trace.helper_exit_patch_offset,
            trace.chain_header_patch_offset,
            trace.chain_fallback_patch_offset,
        )
        trace.fn = fn
        trace.raw_addr = raw_addr
        trace._exec_buf = self._standalone_region.buffer
        return trace

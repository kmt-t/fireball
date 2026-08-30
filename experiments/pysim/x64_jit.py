"""
experiments/pysim/x64_jit.py

The actual Copy-and-Patch JIT: walks a WASM function body (via
control_flow.decode_all) exactly once, copying each instruction's x64
stencil (x64_stencils.py) into a growing buffer and patching its
relocation slot(s) as soon as the patch value is known -- immediately for
straight-line opcodes, and via a small pending-fixup list for forward
branches (block/if targets aren't known until their END is reached), for
calls to another WASM function (a callee's final address isn't known
until every function in the module has been laid out), and for the shared
bounds-check trap stub (placed once, after every function).

Every jump/call stencil ends its relocation slot at the last 4 bytes of
the instruction, so "the address of the instruction after the jump" is
always `reloc_slot_offset + 4` -- one formula covers br/br_if/call/the
bounds-check's `ja`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import x64_asm as asm
import x64_stencils as st
from control_flow import Instr, decode_all
from wasm_module import FuncType, Module
from wasm_opcodes import (
    BLOCK, BR, BR_IF, BR_TABLE, CALL, CALL_INDIRECT, DROP, ELSE, END, GLOBAL_GET,
    GLOBAL_SET, I32_ADD, I32_AND, I32_CLZ, I32_CONST, I32_CTZ, I32_DIV_S, I32_DIV_U,
    I32_EQ, I32_EQZ, I32_GE_S, I32_GE_U, I32_GT_S, I32_GT_U, I32_LE_S, I32_LE_U,
    I32_LOAD, I32_LOAD8_S, I32_LOAD8_U, I32_LOAD16_S, I32_LOAD16_U, I32_LT_S,
    I32_LT_U, I32_MUL, I32_NE, I32_OR, I32_POPCNT, I32_REM_S, I32_REM_U, I32_ROTL,
    I32_ROTR, I32_SHL, I32_SHR_S, I32_SHR_U, I32_STORE, I32_STORE8, I32_STORE16,
    I32_SUB, I32_XOR, IF, LOCAL_GET, LOCAL_SET, LOCAL_TEE, LOOP, MEMORY_GROW,
    MEMORY_SIZE, NOP, RETURN, SELECT, UNREACHABLE,
)

I32_MASK = 0xFFFFFFFF
PAGE_SIZE = 65536

_SIMPLE_STENCIL = {
    I32_ADD: st.I32_ADD, I32_SUB: st.I32_SUB, I32_MUL: st.I32_MUL,
    I32_DIV_S: st.I32_DIV_S, I32_DIV_U: st.I32_DIV_U,
    I32_REM_S: st.I32_REM_S, I32_REM_U: st.I32_REM_U,
    I32_AND: st.I32_AND, I32_OR: st.I32_OR, I32_XOR: st.I32_XOR,
    I32_SHL: st.I32_SHL, I32_SHR_S: st.I32_SHR_S, I32_SHR_U: st.I32_SHR_U,
    I32_ROTL: st.I32_ROTL, I32_ROTR: st.I32_ROTR,
    I32_CLZ: st.I32_CLZ, I32_CTZ: st.I32_CTZ, I32_POPCNT: st.I32_POPCNT,
    I32_EQZ: st.I32_EQZ, I32_EQ: st.I32_EQ, I32_NE: st.I32_NE,
    I32_LT_S: st.I32_LT_S, I32_LT_U: st.I32_LT_U,
    I32_GT_S: st.I32_GT_S, I32_GT_U: st.I32_GT_U,
    I32_LE_S: st.I32_LE_S, I32_LE_U: st.I32_LE_U,
    I32_GE_S: st.I32_GE_S, I32_GE_U: st.I32_GE_U,
    DROP: st.DROP, SELECT: st.SELECT, UNREACHABLE: st.UNREACHABLE,
}

_MEMORY_STENCIL = {
    I32_LOAD: (st.I32_LOAD, 4), I32_LOAD8_S: (st.I32_LOAD8_S, 1), I32_LOAD8_U: (st.I32_LOAD8_U, 1),
    I32_LOAD16_S: (st.I32_LOAD16_S, 2), I32_LOAD16_U: (st.I32_LOAD16_U, 2),
    I32_STORE: (st.I32_STORE, 4), I32_STORE8: (st.I32_STORE8, 1), I32_STORE16: (st.I32_STORE16, 2),
}


def _to_i32(v: int) -> int:
    v &= I32_MASK
    return v - (1 << 32) if v & 0x8000_0000 else v


def _patch_rel32(code: bytearray, reloc_slot_offset: int, target_offset: int) -> None:
    next_instr_offset = reloc_slot_offset + 4
    rel = target_offset - next_instr_offset
    code[reloc_slot_offset:reloc_slot_offset + 4] = (rel & I32_MASK).to_bytes(4, "little")


def _patch_u32(code: bytearray, offset: int, value: int) -> None:
    code[offset:offset + 4] = (value & I32_MASK).to_bytes(4, "little")


def _patch_u64(code: bytearray, offset: int, value: int) -> None:
    code[offset:offset + 8] = (value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


@dataclass
class _Frame:
    kind: str                     # "block" | "loop" | "if"
    loop_target: int | None = None          # x64 offset to jump to (loop only)
    pending_fixups: list[int] = field(default_factory=list)  # reloc slots -> "after this frame's END"
    if_header_reloc: int | None = None       # IF's own conditional-jump reloc slot, if not yet resolved


class UnsupportedOpcode(NotImplementedError):
    pass


class FunctionCompiler:
    """Compiles one WASM function body into x64 machine code.

    Three things are deliberately NOT resolved here, since they need
    information this compiler doesn't have in isolation:
    - `call` targets for calls to another *local* WASM function (its final
      base address isn't known until every function in the module has been
      laid out) -- collected into `self.call_fixups`.
    - `call` targets for calls to a *host* import (a ctypes trampoline
      address, resolved by the caller of compile() and passed in as
      `host_trampolines`).
    - The bounds-check trap target, shared across the whole compiled
      module and placed once, after every function -- collected into
      `self.trap_fixups`.
    """

    def __init__(self, module: Module, func_index: int, mem_size_bytes: int,
                 globals_addr: int, host_trampolines: dict[int, int],
                 table_addr_base: int = 0, table_type_base: int = 0,
                 type_ids: dict[FuncType, int] | None = None):
        self.module = module
        self.func_index = func_index
        self.mem_size_bytes = mem_size_bytes
        self.globals_addr = globals_addr
        self.host_trampolines = host_trampolines
        self.table_addr_base = table_addr_base
        self.table_type_base = table_type_base
        self.type_ids = type_ids or {}
        self.code = bytearray()
        self.call_fixups: list[tuple[int, int]] = []   # (reloc_slot_offset, target_func_index)
        self.trap_fixups: list[int] = []
        self._return_fixups: list[int] = []

    def compile(self) -> bytes:
        fn = self.module.functions[self.func_index - len(self.module.imports)]
        ft = self.module.func_type(self.func_index)
        instrs = decode_all(fn.code)

        self.code += st.PROLOGUE.code
        self._compile_body(instrs, fn.code)

        epilogue_offset = len(self.code)
        for reloc in self._return_fixups:
            _patch_rel32(self.code, reloc, epilogue_offset)

        if ft.results:
            self.code += st.EPILOGUE_RETURN_I32.code
        else:
            self.code += st.EPILOGUE_RETURN_VOID.code

        return bytes(self.code)

    # ------------------------------------------------------------------ #
    def _emit(self, stencil: st.Stencil) -> int:
        base = len(self.code)
        self.code += stencil.code
        return base

    def _compile_body(self, instrs: dict[int, Instr], raw: bytes) -> None:
        frames: list[_Frame] = []
        pc = 0
        n = len(raw)

        while pc < n:
            ins = instrs[pc]
            op = ins.opcode

            if op == NOP:
                pass

            elif op == BLOCK:
                frames.append(_Frame(kind="block"))

            elif op == LOOP:
                frames.append(_Frame(kind="loop", loop_target=len(self.code)))

            elif op == IF:
                base = self._emit(st.BR_IF)  # placeholder; real header emitted below
                # IF needs "jump when FALSE", i.e. the inverse condition of
                # BR_IF's "jump when true" -- flip the Jcc condition byte
                # (0x85 JNZ -> 0x84 JZ) on our private copy of the stencil
                # bytes rather than adding a whole new stencil to the table.
                jz_bytes = bytearray(st.BR_IF.code)
                jz_bytes[4] = 0x84  # 0F 85 -> 0F 84 (JNZ -> JZ)
                self.code[base:base + len(jz_bytes)] = jz_bytes
                header_reloc = base + st.BR_IF.relocs["rel32"]
                frames.append(_Frame(kind="if", if_header_reloc=header_reloc))

            elif op == ELSE:
                frame = frames[-1]
                assert frame.kind == "if"
                # The true-branch fell through to here: it must skip the
                # else-branch entirely once it's done.
                skip_reloc = self._emit(st.BR) + st.BR.relocs["rel32"]
                frame.pending_fixups.append(skip_reloc)
                # Resolve the IF's own conditional jump to land exactly here.
                _patch_rel32(self.code, frame.if_header_reloc, len(self.code))
                frame.if_header_reloc = None

            elif op == END:
                if frames:
                    frame = frames.pop()
                    here = len(self.code)
                    if frame.if_header_reloc is not None:
                        _patch_rel32(self.code, frame.if_header_reloc, here)
                    for reloc in frame.pending_fixups:
                        _patch_rel32(self.code, reloc, here)

            elif op == BR:
                self._emit_branch(frames, ins.operand, conditional=False)

            elif op == BR_IF:
                self._emit_branch(frames, ins.operand, conditional=True)

            elif op == BR_TABLE:
                self._emit_br_table(frames, ins.br_table_labels, ins.operand)

            elif op == RETURN:
                reloc = self._emit(st.BR) + st.BR.relocs["rel32"]
                self._return_fixups.append(reloc)

            elif op == CALL:
                if self.module.is_import(ins.operand):
                    self._emit_host_call(ins.operand)
                else:
                    self._emit_call(ins.operand)

            elif op == CALL_INDIRECT:
                self._emit_call_indirect(ins.operand, ins.table_index)

            elif op == LOCAL_GET:
                base = self._emit(st.LOCAL_GET)
                _patch_u32(self.code, base + st.LOCAL_GET.relocs["disp"], ins.operand * 8)

            elif op == LOCAL_SET:
                base = self._emit(st.LOCAL_SET)
                _patch_u32(self.code, base + st.LOCAL_SET.relocs["disp"], ins.operand * 8)

            elif op == LOCAL_TEE:
                base = self._emit(st.LOCAL_TEE)
                _patch_u32(self.code, base + st.LOCAL_TEE.relocs["disp"], ins.operand * 8)

            elif op == GLOBAL_GET:
                base = self._emit(st.GLOBAL_GET)
                _patch_u64(self.code, base + st.GLOBAL_GET.relocs["addr"], self.globals_addr + ins.operand * 8)

            elif op == GLOBAL_SET:
                base = self._emit(st.GLOBAL_SET)
                _patch_u64(self.code, base + st.GLOBAL_SET.relocs["addr"], self.globals_addr + ins.operand * 8)

            elif op == I32_CONST:
                base = self._emit(st.I32_CONST)
                _patch_u32(self.code, base + st.I32_CONST.relocs["imm"], _to_i32(ins.const_value) & I32_MASK)

            elif op == MEMORY_SIZE:
                base = self._emit(st.I32_CONST)
                _patch_u32(self.code, base + st.I32_CONST.relocs["imm"], self.mem_size_bytes // PAGE_SIZE)

            elif op == MEMORY_GROW:
                raise UnsupportedOpcode(
                    "memory.grow is not supported by the JIT: this experiment treats linear "
                    "memory as fixed-size (bounds checks bake mem_size_bytes in at compile "
                    "time); the interpreter still supports it as the oracle for code that "
                    "never actually calls it."
                )

            elif op in _MEMORY_STENCIL:
                self._emit_memory_op(op, ins)

            elif op in _SIMPLE_STENCIL:
                self._emit(_SIMPLE_STENCIL[op])

            else:
                raise UnsupportedOpcode(f"0x{op:02X}")

            pc = ins.end_offset

    def _emit_memory_op(self, op: int, ins: Instr) -> None:
        stencil, width = _MEMORY_STENCIL[op]
        assert self.module.memory is not None, f"opcode 0x{op:02X} used with no memory section"
        max_addr = self.mem_size_bytes - ins.memarg[1] - width
        base = self._emit(stencil)
        _patch_u32(self.code, base + stencil.relocs["max_addr"], max_addr if max_addr >= 0 else 0)
        self.trap_fixups.append(base + stencil.relocs["trap"])
        _patch_u32(self.code, base + stencil.relocs["disp"], ins.memarg[1])

    def _resolve_branch_target(self, frames: list[_Frame], depth: int, reloc: int) -> None:
        """Shared by BR/BR_IF and every BR_TABLE arm: a loop's target
        address is already known (its entry point was recorded when the
        LOOP was compiled), so it's patched immediately; a block/if's
        target is its own END, not yet reached, so the reloc waits in that
        frame's pending_fixups until END resolves them all at once."""
        target_frame = frames[-1 - depth]
        if target_frame.kind == "loop":
            _patch_rel32(self.code, reloc, target_frame.loop_target)
        else:
            target_frame.pending_fixups.append(reloc)

    def _emit_branch(self, frames: list[_Frame], depth: int, conditional: bool) -> None:
        stencil = st.BR_IF if conditional else st.BR
        base = self._emit(stencil)
        reloc = base + stencil.relocs["rel32"]
        self._resolve_branch_target(frames, depth, reloc)

    def _emit_jcc(self, condition: str) -> int:
        """Appends a placeholder Jcc (for glue code that isn't a fixed
        stencil: br_table's compare chain, call_indirect's checks) and
        returns its reloc offset as an ABSOLUTE position in self.code."""
        base = len(self.code)
        jcc_bytes, local_reloc = asm.jcc_rel32_placeholder(condition)
        self.code += jcc_bytes
        return base + local_reloc

    def _emit_br_table(self, frames: list[_Frame], labels: list[int], default: int) -> None:
        """wasm_instruction_set.md 3.1's `br_table`: this experiment uses a
        linear compare-chain (`cmp eax, i; je target_i` for each label,
        falling through to an unconditional jump to `default`) rather than
        a real O(1) jump table -- simpler to get right, and every test
        program here has at most a handful of arms."""
        self.code += bytes((0x58,))   # pop rax (index)
        for i, depth in enumerate(labels):
            self.code += asm.cmp_reg_imm32("rax", i)
            reloc = self._emit_jcc("e")
            self._resolve_branch_target(frames, depth, reloc)
        base = self._emit(st.BR)
        reloc = base + st.BR.relocs["rel32"]
        self._resolve_branch_target(frames, default, reloc)

    def _emit_call(self, callee_index: int) -> None:
        """Calling convention glue (not a fixed stencil -- its size depends
        on the callee's param/local counts): saves our R10/R11 (they hold
        THIS function's locals-ptr/memory-base and would otherwise be
        clobbered by the callee's own prologue), reserves a fresh locals
        frame for the callee directly on the native stack, copies the
        popped WASM-stack arguments into it in WASM param order, zero-
        initializes any declared-but-not-parameter locals, calls, then
        restores everything and pushes the i32 result (if any).
        """
        callee_ft = self.module.func_type(callee_index)
        nparams = len(callee_ft.params)
        nlocals_total = len(self.module.locals_layout(callee_index))
        frame_bytes = max(nlocals_total, 1) * 8
        # sub/add rsp use an imm8 encoding below -- both operands must fit
        # a signed byte, which every function in this experiment's test
        # programs does comfortably.
        assert 0 <= frame_bytes <= 127, "callee locals frame too large for this JIT's imm8 sub/add rsp encoding"
        assert nparams * 8 <= 127, "too many call-site params for this JIT's imm8 add rsp encoding"

        self.code += bytes((0x41, 0x52))          # push r10
        self.code += bytes((0x41, 0x53))          # push r11
        self.code += bytes((0x48, 0x83, 0xEC, frame_bytes))   # sub rsp, frame_bytes

        # Original args now sit above our two pushes and the new frame, in
        # reverse push order: arg_{nparams-1} closest, arg_0 furthest.
        args_base = frame_bytes + 16
        for p in range(nparams):
            src_off = args_base + (nparams - 1 - p) * 8
            self.code += bytes((0x8B, 0x84, 0x24)) + (src_off & I32_MASK).to_bytes(4, "little")   # mov eax, [rsp+src_off]
            self.code += bytes((0x89, 0x84, 0x24)) + (p * 8 & I32_MASK).to_bytes(4, "little")      # mov [rsp+p*8], eax

        for j in range(nparams, nlocals_total):
            self.code += (bytes((0xC7, 0x84, 0x24)) + (j * 8 & I32_MASK).to_bytes(4, "little")
                           + (0).to_bytes(4, "little"))

        self.code += bytes((0x48, 0x89, 0xE1))     # mov rcx, rsp          (callee locals_ptr)
        self.code += bytes((0x4C, 0x89, 0xDA))     # mov rdx, r11          (callee memory_base)

        call_reloc = self._emit(st.CALL) + st.CALL.relocs["rel32"]
        self.call_fixups.append((call_reloc, callee_index))

        self.code += bytes((0x48, 0x83, 0xC4, frame_bytes))    # add rsp, frame_bytes
        self.code += bytes((0x41, 0x5B))          # pop r11
        self.code += bytes((0x41, 0x5A))          # pop r10
        if nparams:
            self.code += bytes((0x48, 0x83, 0xC4, nparams * 8))  # add rsp, nparams*8 (drop original args)
        if callee_ft.results:
            self.code += bytes((0x50,))            # push rax (callee's i32 result)

    # Registers available as scratch across a host-call site: r8/r9 double
    # as the 3rd/4th ABI argument registers, r12 holds the saved original
    # rsp (for exact, non-arithmetic restoration -- `and rsp,-16` cannot be
    # undone by an `add`, only by restoring a saved value), and r13-r15
    # hold up to 3 stack-passed arguments (fireball-call6's maximum: id +
    # 6 args = 7 total, 4 in registers + 3 on the stack) until the frame
    # they belong in has been carved out.
    _HOST_ARG_REGS = ["rcx", "rdx", "r8", "r9"]
    _HOST_STACK_TEMP_REGS = ["r13", "r14", "r15"]

    def _emit_host_call(self, callee_index: int) -> None:
        """`fireball_call`-shaped host bridge (system_syscall.md 3): calls
        a real ctypes trampoline (a Python callable wrapped as a genuine C
        function pointer) with up to 4 register args and any remainder on
        the stack per the Microsoft x64 ABI, realigning the stack to 16
        bytes + 32-byte shadow space around the call regardless of the
        WASM operand stack's depth at the call site (which this JIT does
        not statically track), and restoring the exact original rsp
        afterward -- not via arithmetic, which `and rsp,-16` makes unsafe.
        """
        ft = self.module.func_type(callee_index)
        nparams = len(ft.params)
        assert nparams <= 4 + len(self._HOST_STACK_TEMP_REGS), (
            f"host import with {nparams} params exceeds this JIT's host-call arity limit"
        )
        trampoline_addr = self.host_trampolines[callee_index]

        n_stack_args = max(0, nparams - 4)
        stack_bytes = 32 + n_stack_args * 8            # shadow space + stack-passed args
        if stack_bytes % 16 != 0:
            stack_bytes += 8                             # round up to a multiple of 16
        # After `and rsp,-16`, rsp is 16-aligned; the ABI requires rsp to
        # STILL be 16-aligned right before `call` executes (call's own
        # 8-byte return-address push is what brings the callee to the
        # usual "16-aligned minus 8" it sees at entry) -- so stack_bytes,
        # a pure subtraction from an already-16-aligned base, must itself
        # be a multiple of 16, not ≡8 as an earlier version of this had it
        # backwards (caught by test_host_call.py segfaulting outright).

        # Pop params in reverse (last param is topmost) BEFORE saving
        # r10/r11: the WASM args are already on top of the stack at this
        # point, so pushing anything first (even just to save it) would
        # bury them and pop something else instead -- a real bug this
        # method shipped with and test_host_call.py caught (wrong values
        # recorded, not a crash, since the pops still succeeded, just on
        # the wrong data). The low 4 params land directly in their final
        # ABI registers; any beyond that go to temps first since their
        # final home (a stack slot) doesn't exist until after realignment.
        for i in reversed(range(nparams)):
            if i < 4:
                self.code += asm.pop_reg(self._HOST_ARG_REGS[i])
            else:
                self.code += asm.pop_reg(self._HOST_STACK_TEMP_REGS[i - 4])

        self.code += asm.push_reg("r10")
        self.code += asm.push_reg("r11")

        self.code += asm.mov_reg_reg("r12", "rsp")
        self.code += asm.and_rsp_imm8(-16 & 0xFF)
        self.code += asm.sub_rsp_imm8(stack_bytes)
        for i in range(4, nparams):
            self.code += asm.mov_store_rsp_disp32(32 + (i - 4) * 8, self._HOST_STACK_TEMP_REGS[i - 4])

        self.code += asm.mov_reg_imm64("rax", trampoline_addr)
        self.code += asm.call_reg("rax")

        self.code += asm.mov_reg_reg("rsp", "r12")
        self.code += asm.pop_reg("r11")
        self.code += asm.pop_reg("r10")
        if ft.results:
            self.code += bytes((0x50,))   # push rax -- fireball_call always returns a u32

    def _emit_call_indirect(self, type_index: int, table_index: int) -> None:
        """wasm_instruction_set.md 3.1's `call_indirect`: looks up a
        function pointer from a host-side table (filled in by
        ModuleJIT.populate_tables() once every function's final address is
        known), bounds-checks the index, verifies the slot's recorded type
        signature against the declared type -- the actual callee isn't
        known until runtime, so unlike a direct `call` this can't be
        checked at compile time -- and calls through the resolved address.

        No table entry's local-variable count is known until the specific
        callee is resolved at runtime, so this reserves the largest locals
        frame *any* entry in the table could need; local.get/set only ever
        touch indices within whichever callee actually runs, so the extra
        (unused, zeroed) slots for a smaller callee are simply never read.
        """
        assert table_index == 0, "only a single table (WASM MVP's own limit) is supported"
        declared_type = self.module.types[type_index]
        nparams = len(declared_type.params)
        table = self.module.table_contents(table_index)
        table_size = len(table)
        candidate_nlocals = [len(self.module.locals_layout(fi)) for fi in table if fi is not None]
        nlocals_total = max(candidate_nlocals + [nparams])
        frame_bytes = max(nlocals_total, 1) * 8
        assert 0 <= frame_bytes <= 127, "callee locals frame too large for this JIT's imm8 sub/add rsp encoding"
        assert nparams * 8 <= 127, "too many call_indirect params for this JIT's imm8 add rsp encoding"
        type_id = self.type_ids[declared_type]

        # pop rcx (table slot index, on top of the args per call_indirect's
        # `[t1*, i32] -> [t2*]` stack signature); bounds check.
        self.code += bytes((0x59,))
        self.code += asm.cmp_reg_imm32("rcx", table_size)
        self.trap_fixups.append(self._emit_jcc("ae"))   # unsigned: index >= table_size

        # Type check against the slot's recorded signature id.
        self.code += asm.mov_reg_imm64("rbx", self.table_type_base)
        self.code += asm.cmp_dword_scaled_imm32("rbx", "rcx", 4, type_id)
        self.trap_fixups.append(self._emit_jcc("ne"))

        # Resolve the target's absolute address; a still-zero slot means
        # "never initialized by an element segment" per the WASM spec.
        self.code += asm.mov_reg_imm64("rbx", self.table_addr_base)
        self.code += asm.mov_load_scaled("rax", "rbx", "rcx", 8)
        self.code += asm.test_reg_reg("rax")
        self.trap_fixups.append(self._emit_jcc("z"))

        # From here it's the same shape as a direct WASM-to-WASM call
        # (save r10/r11, build the callee's locals frame, marshal args),
        # except the target lives in a register instead of a relocatable
        # rel32, so it has to survive the frame setup below -- stashed on
        # the stack rather than in a register, since every general-purpose
        # register is either live (r10/r11) or about to be scratch for the
        # arg copy loop.
        self.code += asm.push_reg("r10")
        self.code += asm.push_reg("r11")
        self.code += asm.push_reg("rax")
        self.code += asm.sub_rsp_imm8(frame_bytes)

        args_base = frame_bytes + 24   # +8 stashed target address, +16 r10/r11
        for p in range(nparams):
            src_off = args_base + (nparams - 1 - p) * 8
            self.code += asm.mov_load_rsp_disp32("rax", src_off)
            self.code += asm.mov_store_rsp_disp32(p * 8, "rax")
        for j in range(nparams, nlocals_total):
            self.code += (bytes((0xC7, 0x84, 0x24)) + (j * 8 & I32_MASK).to_bytes(4, "little")
                           + (0).to_bytes(4, "little"))

        self.code += asm.mov_reg_reg("rcx", "rsp")     # callee locals_ptr
        self.code += asm.mov_reg_reg("rdx", "r11")     # callee memory_base
        self.code += asm.mov_load_rsp_disp32("rax", frame_bytes)   # recover the stashed target address
        self.code += asm.call_reg("rax")

        self.code += asm.add_rsp_imm8(frame_bytes)
        self.code += asm.add_rsp_imm8(8)                # drop the stashed target address (not into a
                                                          # register: RAX is live with the call's result)
        self.code += asm.pop_reg("r11")
        self.code += asm.pop_reg("r10")
        if nparams:
            self.code += asm.add_rsp_imm8(nparams * 8)   # drop the original args
        if declared_type.results:
            self.code += bytes((0x50,))                  # push rax (the callee's i32 result)


class TraceJITCompiler:
    """True Trace-based Copy-and-Patch JIT Compiler.
    Compiles individual BasicBlocks / Traces into executable native machine code
    backed by JITTraceHeader and 16-byte physical memory layout.
    Mirroring docs/components/tier3_jit/jit_compiler.md.
    """

    def __init__(self, mem_size_bytes: int = 0, globals_addr: int = 0,
                 host_trampolines: dict[int, int] | None = None,
                 table_addr_base: int = 0, table_type_base: int = 0):
        self.mem_size_bytes = mem_size_bytes
        self.globals_addr = globals_addr
        self.host_trampolines = host_trampolines or {}
        self.table_addr_base = table_addr_base
        self.table_type_base = table_type_base

    def compile_trace(self, head_pc: int, block: Any, nlocals: int = 8) -> Any:
        """Compiles a single BasicBlock into a native JITTrace."""
        from runtime_engine import JITTrace

        # Assemble machine code for the trace basic block
        code = bytearray()
        # Prologue: enter trace with shadow space
        code += asm.push_reg("rbp")
        code += asm.mov_reg_reg("rbp", "rsp")
        code += asm.push_reg("r10")
        code += asm.push_reg("r11")
        code += asm.push_reg("rbx")
        code += asm.push_reg("rdi")
        code += asm.push_reg("rsi")
        code += asm.push_reg("r12")
        code += asm.push_reg("r13")
        code += asm.push_reg("r14")
        code += asm.push_reg("r15")

        # RCX = locals_ptr, RDX = memory_base
        code += asm.mov_reg_reg("r10", "rcx")  # R10 = locals_ptr
        code += asm.mov_reg_reg("r11", "rdx")  # R11 = memory_base

        ops = getattr(block, "ops", [])
        for op, arg in ops:
            if op == "i32.const":
                code += bytes((0x48, 0xB8)) + (arg & I32_MASK).to_bytes(8, "little")  # mov rax, imm64
                code += asm.push_reg("rax")
            elif op == "i32.add":
                code += asm.pop_reg("rcx")
                code += asm.pop_reg("rax")
                code += bytes((0x01, 0xC8))  # add eax, ecx
                code += asm.push_reg("rax")
            elif op == "i32.sub":
                code += asm.pop_reg("rcx")
                code += asm.pop_reg("rax")
                code += bytes((0x29, 0xC8))  # sub eax, ecx
                code += asm.push_reg("rax")
            elif op == "i32.mul":
                code += asm.pop_reg("rcx")
                code += asm.pop_reg("rax")
                code += bytes((0x0F, 0xAF, 0xC1))  # imul eax, ecx
                code += asm.push_reg("rax")
            elif op == "local.get":
                # mov eax, dword [r10 + arg*8]
                code += bytes((0x8B, 0x82)) + (arg * 8).to_bytes(4, "little")
                code += asm.push_reg("rax")
            elif op == "local.set":
                code += asm.pop_reg("rax")
                # mov dword [r10 + arg*8], eax
                code += bytes((0x89, 0x82)) + (arg * 8).to_bytes(4, "little")

        # Epilogue
        code += asm.pop_reg("r15")
        code += asm.pop_reg("r14")
        code += asm.pop_reg("r13")
        code += asm.pop_reg("r12")
        code += asm.pop_reg("rsi")
        code += asm.pop_reg("rdi")
        code += asm.pop_reg("rbx")
        code += asm.pop_reg("r11")
        code += asm.pop_reg("r10")
        code += asm.pop_reg("rbp")
        code += bytes((0xC3,))  # ret

        trace_bytes = bytes(code)

        def make_trace_fn():
            from exec_memory import ExecutableBuffer
            import ctypes
            buf = ExecutableBuffer(max(len(trace_bytes), 64))
            buf.write(0, trace_bytes)
            fn = buf.function_at(0, ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])

            def runner(ctx: Any) -> str:
                # Synchronize Python WASMContext with native memory
                n_loc = len(ctx.locals)
                locals_arr = (ctypes.c_int64 * max(n_loc, 1))(*ctx.locals)
                res = fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(0))
                # Write back locals
                for i in range(n_loc):
                    ctx.locals[i] = locals_arr[i] & 0xFFFF_FFFF
                return "OK"

            return runner

        next_pc = getattr(block, "next_pc", None)
        loops_to = getattr(block, "loops_to", None)
        return JITTrace(head_pc=head_pc, native_fn=make_trace_fn(), size_bytes=len(trace_bytes),
                        next_pc=next_pc, loops_to=loops_to)

    def compile_function_as_trace(self, module: Module, func_index: int) -> tuple[bytes, int]:
        """Compiles a complete function body as a contiguous JIT trace block."""
        type_ids: dict[FuncType, int] = {}
        for ft in module.types:
            type_ids.setdefault(ft, len(type_ids))
        fc = FunctionCompiler(module, func_index, self.mem_size_bytes, self.globals_addr,
                              self.host_trampolines, self.table_addr_base, self.table_type_base, type_ids)
        code = fc.compile()
        blob = bytearray(code)
        # Patch bounds-check traps
        trap_offset = len(blob)
        blob.extend(st.TRAP.code)
        for reloc_offset in fc.trap_fixups:
            _patch_rel32(blob, reloc_offset, trap_offset)
        return bytes(blob), 0

    def compile_module_traces(self, module: Module) -> tuple[bytes, list[int | None]]:
        """Compiles all function traces in a module and resolves inter-trace call relocations."""
        n_imports = len(module.imports)
        compiled: list[bytes] = []
        fixups_per_func: list[list[tuple[int, int]]] = []
        trap_fixups_per_func: list[list[int]] = []

        type_ids: dict[FuncType, int] = {}
        for ft in module.types:
            type_ids.setdefault(ft, len(type_ids))

        for local_i in range(len(module.functions)):
            func_index = n_imports + local_i
            fc = FunctionCompiler(module, func_index, self.mem_size_bytes,
                                   self.globals_addr, self.host_trampolines,
                                   self.table_addr_base, self.table_type_base, type_ids)
            compiled.append(fc.compile())
            fixups_per_func.append(fc.call_fixups)
            trap_fixups_per_func.append(fc.trap_fixups)

        offsets: list[int] = []
        cursor = 0
        for b in compiled:
            offsets.append(cursor)
            cursor += len(b)
        trap_offset = cursor
        cursor += len(st.TRAP.code)

        blob = bytearray(cursor)
        for off, b in zip(offsets, compiled):
            blob[off:off + len(b)] = b
        blob[trap_offset:trap_offset + len(st.TRAP.code)] = st.TRAP.code

        all_offsets: list[int | None] = [None] * n_imports + offsets

        for local_i, fixups in enumerate(fixups_per_func):
            base = offsets[local_i]
            for reloc_local_offset, callee_index in fixups:
                target = all_offsets[callee_index]
                if target is not None:
                    _patch_rel32(blob, base + reloc_local_offset, target)

        for local_i, trap_fixups in enumerate(trap_fixups_per_func):
            base = offsets[local_i]
            for reloc_local_offset in trap_fixups:
                _patch_rel32(blob, base + reloc_local_offset, trap_offset)

        return bytes(blob), all_offsets

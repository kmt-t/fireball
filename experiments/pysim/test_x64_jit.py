"""
experiments/pysim/test_x64_jit.py

End-to-end tests: build a real .wasm binary (wasm_builder.py) with no
existing WASM tooling of any kind, parse it back with the real binary
reader (wasm_reader.py) -- exercising the actual on-disk format, not an
in-memory shortcut -- JIT-compile it to x64 (x64_jit.py), execute the
result as real machine code (exec_memory.py + ctypes), and cross-check
against the independent reference interpreter (interpreter.py) so a bug
shared by both engines can't hide.
"""

from __future__ import annotations

import ctypes

import test_x64_asm
import wasm_reader
import x64_asm
from exec_memory import ExecutableBuffer
from interpreter import Interpreter
from wasm_builder import ModuleBuilder
from wasm_module import I32
from x64_jit import ModuleJIT


def _run_jit(module, func_index: int, args: list[int], memory: bytearray | None = None) -> int:
    jit = ModuleJIT(module, mem_size_bytes=len(memory) if memory is not None else 0)
    blob = jit.compile_all()

    buf = ExecutableBuffer(max(len(blob), 64))
    try:
        buf.write(0, blob)

        layout = module.locals_layout(func_index)
        LocalsArray = ctypes.c_int64 * max(len(layout), 1)
        locals_arr = LocalsArray(*[0] * max(len(layout), 1))
        for i, v in enumerate(args):
            locals_arr[i] = v

        mem_ptr = 0
        c_mem = None
        if memory is not None:
            c_mem = (ctypes.c_char * len(memory)).from_buffer(memory)
            mem_ptr = ctypes.addressof(c_mem)

        fn = buf.function_at(jit.func_offsets[func_index], ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        return fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(mem_ptr))
    finally:
        buf.close()


def _build_and_parse(builder: ModuleBuilder):
    raw = builder.build()
    return wasm_reader.parse(raw), raw


def _cross_check(builder: ModuleBuilder, func_index: int, args: list[int], memory_size: int = 0) -> int:
    """Runs the same freshly-parsed module through both engines and asserts
    they agree, returning the (agreed) result."""
    module, raw = _build_and_parse(builder)
    assert raw[:4] == b"\x00asm", "wasm_builder did not even produce a valid magic header"

    interp_mem = bytearray(memory_size) if memory_size else None
    interp = Interpreter(module, memory=interp_mem)
    interp_result = interp.call(func_index, list(args))[0]

    jit_mem = bytearray(memory_size) if memory_size else None
    jit_result = _run_jit(module, func_index, list(args), memory=jit_mem)

    assert jit_result == interp_result, (
        f"interpreter says {interp_result}, JIT says {jit_result} -- the two engines disagree"
    )
    if interp_mem is not None:
        assert bytes(jit_mem) == bytes(interp_mem), "interpreter and JIT left linear memory in different states"
    return jit_result


# ---------------------------------------------------------------------------
# straight-line arithmetic
# ---------------------------------------------------------------------------

def test_add_two_params():
    b = ModuleBuilder()
    f = b.add_function((I32, I32), (I32,), export_name="add")
    f.local_get(0).local_get(1).i32_add()
    assert _cross_check(b, 0, [3, 4]) == 7
    assert _cross_check(b, 0, [-10, 3]) == -7


def test_expression_with_locals_and_const():
    # (a + b) * 2 - 1, using a declared local to hold the sum
    b = ModuleBuilder()
    f = b.add_function((I32, I32), (I32,), export_name="calc")
    f.declare_local(I32)
    f.local_get(0).local_get(1).i32_add().local_set(2)
    f.local_get(2).i32_const(2).i32_mul().i32_const(1).i32_sub()
    assert _cross_check(b, 0, [5, 7]) == (5 + 7) * 2 - 1


# ---------------------------------------------------------------------------
# structured control flow: if/else
# ---------------------------------------------------------------------------

def test_if_else_selects_max_of_two_locals():
    b = ModuleBuilder()
    f = b.add_function((I32, I32), (I32,), export_name="max")
    f.local_get(0).local_get(1).i32_gt_s()
    f.if_()
    f.local_get(0)
    f.else_()
    f.local_get(1)
    f.end()
    assert _cross_check(b, 0, [3, 9]) == 9
    assert _cross_check(b, 0, [9, 3]) == 9
    assert _cross_check(b, 0, [5, 5]) == 5


def test_if_without_else_falls_through_when_condition_is_false():
    # local1 = 10; if (local0 != 0) { local1 = 99 }; return local1
    b = ModuleBuilder()
    f = b.add_function((I32,), (I32,), export_name="maybe_set")
    f.declare_local(I32)
    f.i32_const(10).local_set(1)
    f.local_get(0)
    f.if_()
    f.i32_const(99).local_set(1)
    f.end()
    f.local_get(1)
    assert _cross_check(b, 0, [0]) == 10
    assert _cross_check(b, 0, [1]) == 99


# ---------------------------------------------------------------------------
# structured control flow: loop / br_if (the classic block+loop+br_if idiom)
# ---------------------------------------------------------------------------

def _build_sum_1_to_n() -> ModuleBuilder:
    """sum(n): iteratively computes 1+2+...+n using block+loop+br_if,
    exactly the "loop { ...; br_if 0 (cond) }" / early-exit-via-outer-block
    idiom real WASM producers emit for a for-loop."""
    b = ModuleBuilder()
    f = b.add_function((I32,), (I32,), export_name="sum")
    f.declare_local(I32)  # local1 = accumulator
    f.declare_local(I32)  # local2 = i
    f.i32_const(0).local_set(1)      # acc = 0
    f.i32_const(1).local_set(2)      # i = 1
    f.block()                         # depth 1 (outer, for early exit)
    f.loop()                          # depth 0 (innermost, for iteration)
    f.local_get(2).local_get(0).i32_gt_s()
    f.br_if(1)                        # if i > n: break out of the block
    f.local_get(1).local_get(2).i32_add().local_set(1)   # acc += i
    f.local_get(2).i32_const(1).i32_add().local_set(2)    # i += 1
    f.br(0)                            # loop again
    f.end()                            # end loop
    f.end()                            # end block
    f.local_get(1)
    return b


def test_loop_sum_1_to_n_matches_gauss_formula():
    b = _build_sum_1_to_n()
    for n in (0, 1, 5, 10, 100):
        assert _cross_check(b, 0, [n]) == n * (n + 1) // 2


def _build_fib_iter() -> ModuleBuilder:
    """fib(n): iterative Fibonacci via loop+br_if, the same control-flow
    idiom as the sum test but with two accumulators swapped each iteration
    -- enough real dataflow through locals across loop back-edges to catch
    a loop-target miscompile that a single-accumulator loop might not."""
    b = ModuleBuilder()
    f = b.add_function((I32,), (I32,), export_name="fib")
    f.declare_local(I32)  # local1 = a (fib(i))
    f.declare_local(I32)  # local2 = b (fib(i+1))
    f.declare_local(I32)  # local3 = i
    f.declare_local(I32)  # local4 = tmp
    f.i32_const(0).local_set(1)
    f.i32_const(1).local_set(2)
    f.i32_const(0).local_set(3)
    f.block()
    f.loop()
    f.local_get(3).local_get(0).i32_ge_s()
    f.br_if(1)
    f.local_get(1).local_get(2).i32_add().local_set(4)   # tmp = a + b
    f.local_get(2).local_set(1)                            # a = b
    f.local_get(4).local_set(2)                            # b = tmp
    f.local_get(3).i32_const(1).i32_add().local_set(3)     # i += 1
    f.br(0)
    f.end()
    f.end()
    f.local_get(1)
    return b


def _python_fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def test_loop_fibonacci_matches_python_reference():
    b = _build_fib_iter()
    for n in (0, 1, 2, 5, 10, 20):
        assert _cross_check(b, 0, [n]) == _python_fib(n)


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------

def test_jit_can_sum_an_array_in_linear_memory():
    """sum_array(ptr, count): loop reading i32.load from linear memory --
    proves the JIT's memory_base (r11) plumbing survives real loop
    back-edges, not just a single straight-line load/store."""
    b = ModuleBuilder()
    b.add_memory(min_pages=1)
    f = b.add_function((I32, I32), (I32,), export_name="sum_array")
    f.declare_local(I32)  # local2 = acc
    f.declare_local(I32)  # local3 = i
    f.i32_const(0).local_set(2)
    f.i32_const(0).local_set(3)
    f.block()
    f.loop()
    f.local_get(3).local_get(1).i32_ge_s()
    f.br_if(1)
    f.local_get(2)
    f.local_get(0).local_get(3).i32_const(4).i32_mul().i32_add()
    f.i32_load()
    f.i32_add().local_set(2)
    f.local_get(3).i32_const(1).i32_add().local_set(3)
    f.br(0)
    f.end()
    f.end()
    f.local_get(2)

    module, _ = _build_and_parse(b)
    values = [10, 20, 30, 40, 5]
    memory = bytearray(64)
    for i, v in enumerate(values):
        memory[i * 4:i * 4 + 4] = v.to_bytes(4, "little")

    interp = Interpreter(module, memory=bytearray(memory))
    expected = interp.call(0, [0, len(values)])[0]
    assert expected == sum(values)

    jit_mem = bytearray(memory)
    got = _run_jit(module, 0, [0, len(values)], memory=jit_mem)
    assert got == sum(values)


# ---------------------------------------------------------------------------
# ABI: callee-saved registers must survive a call into JIT'd code
# ---------------------------------------------------------------------------

def test_calling_jitted_code_preserves_the_callers_callee_saved_registers():
    """Regression test for a real bug this build shipped and then caught:
    the Microsoft x64 ABI makes rbx/r12-r15 callee-saved, but every
    arithmetic stencil uses rbx and the call glue uses r12-r15, and the
    very first PROLOGUE/EPILOGUE never saved or restored any of them. That
    silently corrupted whatever the ctypes caller (CPython itself) kept in
    those registers, and depending on what CPython happened to be doing at
    the call site, either nothing observable happened or the interpreter
    segfaulted outright -- exactly the kind of bug that "the existing tests
    still pass" cannot catch, because passing was never the risk.

    This test sets every callee-saved register to a distinguishing
    sentinel in real assembly, calls into a JIT'd function that heavily
    exercises rbx (arithmetic) and r12-r15 (a nested WASM call), and
    asserts every sentinel is bit-for-bit unchanged on return.
    """
    b = ModuleBuilder()
    f = b.add_function((I32, I32), (I32,), export_name="churn")
    inner = b.add_function((I32,), (I32,))
    f.local_get(0).local_get(1).i32_add()
    f.local_get(0).local_get(1).i32_mul().i32_xor()
    f.local_get(0).call(inner_index := b.function_index(inner))
    f.i32_add()
    inner.local_get(0).i32_const(3).i32_mul().i32_const(1).i32_add()

    module, _ = _build_and_parse(b)
    jit = ModuleJIT(module)
    blob = jit.compile_all()

    buf = ExecutableBuffer(len(blob))
    try:
        buf.write(0, blob)
        target_offset = jit.func_offsets[0]

        sentinels = {
            "rbx": 0xB0B0B0B0B0B0B0B0,
            "r12": 0x1212121212121212,
            "r13": 0x1313131313131313,
            "r14": 0x1414141414141414,
            "r15": 0x1515151515151515,
        }
        harness = bytearray()
        for reg, val in sentinels.items():
            harness += x64_asm.mov_reg_imm64(reg, val)
        # Compiled functions take (locals_ptr, memory_base) in rcx/rdx;
        # call it with (a=5, b=7) via a small on-stack locals array.
        harness += x64_asm.sub_rsp_imm8(16)
        harness += x64_asm.mov_reg_imm64("rax", 5)
        harness += x64_asm.mov_store_rsp_disp32(0, "rax")
        harness += x64_asm.mov_reg_imm64("rax", 7)
        harness += x64_asm.mov_store_rsp_disp32(8, "rax")
        harness += x64_asm.mov_reg_reg("rcx", "rsp")
        harness += x64_asm.mov_reg_imm64("rdx", 0)
        harness += x64_asm.mov_reg_imm64("r10", buf.address_of(target_offset))
        harness += x64_asm.call_reg("r10")
        harness += x64_asm.add_rsp_imm8(16)
        harness += x64_asm.mov_reg_reg("r10", "rax")   # stash the wasm result out of the way

        # Now verify every sentinel: XOR each register against its expected
        # value (0 iff unchanged) and OR all the diffs together, so the
        # final checksum is 0 iff every callee-saved register survived.
        checksum_reg = "r11"
        harness += x64_asm.mov_reg_imm64(checksum_reg, 0)
        for reg, val in sentinels.items():
            harness += x64_asm.mov_reg_imm64("rax", val)
            harness += _xor_reg_reg("rax", reg)
            harness += _or_reg_reg(checksum_reg, "rax")
        harness += x64_asm.mov_reg_reg("rax", checksum_reg)

        code = test_x64_asm._wrap(bytes(harness))
        wrapper_buf = ExecutableBuffer(max(len(code), 64))
        try:
            wrapper_buf.write(0, code)
            wrapper_fn = wrapper_buf.function_at(0, ctypes.c_uint64, [ctypes.c_uint64, ctypes.c_uint64])
            corruption_mask = wrapper_fn(0, 0)
        finally:
            wrapper_buf.close()

        assert corruption_mask == 0, (
            f"callee-saved register(s) were clobbered by the call: mask={corruption_mask:#x}"
        )
    finally:
        buf.close()


def _xor_reg_reg(dst: str, src: str) -> bytes:
    dst_ext, dst_lo = x64_asm.REG_INFO[dst]
    src_ext, src_lo = x64_asm.REG_INFO[src]
    rex = 0x48 | (0x04 if src_ext else 0) | (0x01 if dst_ext else 0)
    modrm = 0xC0 | (src_lo << 3) | dst_lo
    return bytes((rex, 0x31, modrm))


def _or_reg_reg(dst: str, src: str) -> bytes:
    dst_ext, dst_lo = x64_asm.REG_INFO[dst]
    src_ext, src_lo = x64_asm.REG_INFO[src]
    rex = 0x48 | (0x04 if src_ext else 0) | (0x01 if dst_ext else 0)
    modrm = 0xC0 | (src_lo << 3) | dst_lo
    return bytes((rex, 0x09, modrm))


# ---------------------------------------------------------------------------
# calls (including self-recursion)
# ---------------------------------------------------------------------------

def _build_factorial_rec() -> ModuleBuilder:
    b = ModuleBuilder()
    f = b.add_function((I32,), (I32,), export_name="fact")
    # if (n <= 1) return 1; else return n * fact(n - 1);
    f.local_get(0).i32_const(1).i32_le_s()
    f.if_()
    f.i32_const(1)
    f.else_()
    f.local_get(0)
    f.local_get(0).i32_const(1).i32_sub()
    f.call(0)
    f.i32_mul()
    f.end()
    return b


def test_recursive_call_computes_factorial():
    b = _build_factorial_rec()
    for n in (0, 1, 2, 5, 10):
        import math
        assert _cross_check(b, 0, [n]) == math.factorial(n)


def _build_two_function_module() -> ModuleBuilder:
    """f0 calls f1(a, b) = a*a + b*b; f0(x) = f1(x, x+1)."""
    b = ModuleBuilder()
    f0 = b.add_function((I32,), (I32,), export_name="f0")
    f1 = b.add_function((I32, I32), (I32,))
    f0.local_get(0)
    f0.local_get(0).i32_const(1).i32_add()
    f0.call(1)
    f1.local_get(0).local_get(0).i32_mul()
    f1.local_get(1).local_get(1).i32_mul()
    f1.i32_add()
    return b


def test_call_to_a_different_function_with_two_params():
    b = _build_two_function_module()
    for x in (0, 3, -4):
        assert _cross_check(b, 0, [x]) == x * x + (x + 1) * (x + 1)


# ---------------------------------------------------------------------------
# extended i32 opcode set (wasm_instruction_set.md 3.4/3.5): clz/ctz/popcnt/
# rotl/rotr and sub-word memory access, through the full pipeline
# ---------------------------------------------------------------------------

def test_clz_ctz_popcnt_rotl_rotr_through_the_full_pipeline():
    b = ModuleBuilder()
    f = b.add_function((I32,), (I32,), export_name="mix")
    # (clz(n) + ctz(n)) ^ popcnt(rotl(n, 3))
    f.local_get(0).i32_clz()
    f.local_get(0).i32_ctz()
    f.i32_add()
    f.local_get(0).i32_const(3).i32_rotl().i32_popcnt()
    f.i32_xor()
    for n in (0, 1, 0x12345678, -1, 0x7FFFFFFF):
        _cross_check(b, 0, [n])


def test_subword_memory_access_through_the_full_pipeline():
    """store8/store16/load8_s/load8_u/load16_s/load16_u wired end to end,
    including the bounds check picking up each width correctly."""
    b = ModuleBuilder()
    b.add_memory(min_pages=1)
    f = b.add_function((I32,), (I32,), export_name="roundtrip")
    # store8(0, n); store16(4, n); return load8_s(0) + load8_u(0) + load16_s(4) + load16_u(4)
    f.i32_const(0).local_get(0).i32_store8()
    f.i32_const(4).local_get(0).i32_store16()
    f.i32_const(0).i32_load8_s()
    f.i32_const(0).i32_load8_u()
    f.i32_add()
    f.i32_const(4).i32_load16_s()
    f.i32_add()
    f.i32_const(4).i32_load16_u()
    f.i32_add()

    module, _ = _build_and_parse(b)
    for n in (0, 1, -1, 0x1234, -300):
        interp = Interpreter(module, memory=bytearray(64))
        expected = interp.call(0, [n])[0]
        got = _run_jit(module, 0, [n], memory=bytearray(64))
        assert got == expected, f"n={n}: interpreter={expected}, jit={got}"


def test_out_of_bounds_access_traps_through_the_full_pipeline():
    b = ModuleBuilder()
    b.add_memory(min_pages=1)
    f = b.add_function((I32,), (I32,), export_name="oob")
    f.local_get(0).i32_load()

    module, _ = _build_and_parse(b)
    memory = bytearray(16)
    assert _run_jit(module, 0, [12], memory=bytearray(memory)) == 0   # exactly in bounds
    try:
        _run_jit(module, 0, [13], memory=bytearray(memory))
        raise AssertionError("expected an out-of-bounds i32.load to trap")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# globals
# ---------------------------------------------------------------------------

def _run_jit_with_globals(module, func_index: int, args: list[int], global_values: list[int]) -> tuple[int, list[int]]:
    GlobalsArray = ctypes.c_int64 * max(len(global_values), 1)
    globals_arr = GlobalsArray(*(global_values or [0]))
    globals_addr = ctypes.addressof(globals_arr)

    jit = ModuleJIT(module, globals_addr=globals_addr)
    blob = jit.compile_all()
    buf = ExecutableBuffer(max(len(blob), 64))
    try:
        buf.write(0, blob)
        layout = module.locals_layout(func_index)
        LocalsArray = ctypes.c_int64 * max(len(layout), 1)
        locals_arr = LocalsArray(*([0] * max(len(layout), 1)))
        for i, v in enumerate(args):
            locals_arr[i] = v
        fn = buf.function_at(jit.func_offsets[func_index], ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        result = fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(0))
        return result, list(globals_arr)
    finally:
        buf.close()


def test_global_get_and_set_through_the_full_pipeline_and_persist_across_calls():
    b = ModuleBuilder()
    b.add_global(I32, mutable=True, init_value=10)
    f = b.add_function((I32,), (I32,), export_name="bump")
    # global0 += n; return global0
    f.global_get(0).local_get(0).i32_add().global_set(0)
    f.global_get(0)

    module, _ = _build_and_parse(b)

    interp = Interpreter(module)
    assert interp.call(0, [5])[0] == 15
    assert interp.call(0, [5])[0] == 20   # persists across calls on the same Interpreter
    assert interp.globals == [20]

    result, final_globals = _run_jit_with_globals(module, 0, [5], global_values=[10])
    assert result == 15
    assert final_globals == [15]


# ---------------------------------------------------------------------------
# br_table
# ---------------------------------------------------------------------------

def _build_switch3() -> ModuleBuilder:
    """switch3(n): 10/20/30 for n==0/1/2, 99 for anything else -- the
    classic "N case blocks + 1 shared-exit block" br_table idiom, so every
    case's `br` has to land on the SAME final `local.get 1`, not spill
    into the next case's or the default's body."""
    b = ModuleBuilder()
    f = b.add_function((I32,), (I32,), export_name="switch3")
    f.declare_local(I32)   # local1 = result
    f.block()   # $exit
    f.block()   # $default target
    f.block()
    f.block()
    f.block()   # innermost, case0's own block
    f.local_get(0)
    f.br_table([0, 1, 2], 3)
    f.end()
    f.i32_const(10); f.local_set(1); f.br(3)
    f.end()
    f.i32_const(20); f.local_set(1); f.br(2)
    f.end()
    f.i32_const(30); f.local_set(1); f.br(1)
    f.end()
    f.i32_const(99); f.local_set(1)
    f.end()
    f.local_get(1)
    return b


def test_br_table_dispatches_each_case_and_falls_back_to_default():
    b = _build_switch3()
    for n, expected in [(0, 10), (1, 20), (2, 30), (3, 99), (999, 99)]:
        assert _cross_check(b, 0, [n]) == expected


# ---------------------------------------------------------------------------
# call_indirect
# ---------------------------------------------------------------------------

def _run_jit_with_table(module, func_index: int, args: list[int], table_size: int) -> int:
    TableAddr = ctypes.c_uint64 * max(table_size, 1)
    TableType = ctypes.c_uint32 * max(table_size, 1)
    table_addr_buf = TableAddr()
    table_type_buf = TableType()

    jit = ModuleJIT(module, table_addr_base=ctypes.addressof(table_addr_buf),
                     table_type_base=ctypes.addressof(table_type_buf))
    blob = jit.compile_all()
    buf = ExecutableBuffer(max(len(blob), 64))
    try:
        buf.write(0, blob)
        jit.populate_tables(buf.base, table_addr_buf, table_type_buf)
        layout = module.locals_layout(func_index)
        LocalsArray = ctypes.c_int64 * max(len(layout), 1)
        locals_arr = LocalsArray(*([0] * max(len(layout), 1)))
        for i, v in enumerate(args):
            locals_arr[i] = v
        fn = buf.function_at(jit.func_offsets[func_index], ctypes.c_int64, [ctypes.c_void_p, ctypes.c_void_p])
        return fn(ctypes.cast(locals_arr, ctypes.c_void_p), ctypes.c_void_p(0))
    finally:
        buf.close()


def _build_indirect_dispatch() -> tuple[ModuleBuilder, int]:
    b = ModuleBuilder()
    add_f = b.add_function((I32, I32), (I32,))
    add_f.local_get(0).local_get(1).i32_add()
    sub_f = b.add_function((I32, I32), (I32,))
    sub_f.local_get(0).local_get(1).i32_sub()
    mul_f = b.add_function((I32, I32), (I32,))
    mul_f.local_get(0).local_get(1).i32_mul()

    b.add_table(min_size=4)
    b.add_element(0, 0, [b.function_index(add_f), b.function_index(sub_f), b.function_index(mul_f)])

    d = b.add_function((I32, I32, I32), (I32,), export_name="dispatch")
    d.local_get(1).local_get(2).local_get(0)
    d.call_indirect(0, 0)   # type index 0: add/sub/mul all share (i32,i32)->i32 as the first type seen
    return b, b.function_index(d)


def test_call_indirect_dispatches_through_a_real_table_to_the_correct_function():
    b, dispatch_index = _build_indirect_dispatch()
    module, _ = _build_and_parse(b)
    interp = Interpreter(module)

    for op, a, val, expected in [(0, 6, 7, 13), (1, 10, 3, 7), (2, 4, 5, 20)]:
        interp_result = interp.call(dispatch_index, [op, a, val])[0]
        assert interp_result == expected
        jit_result = _run_jit_with_table(module, dispatch_index, [op, a, val], table_size=4)
        assert jit_result == expected, f"op={op}: interpreter={interp_result}, jit={jit_result}"


def test_call_indirect_traps_on_out_of_bounds_table_index():
    b, dispatch_index = _build_indirect_dispatch()
    module, _ = _build_and_parse(b)
    try:
        _run_jit_with_table(module, dispatch_index, [99, 1, 1], table_size=4)
        raise AssertionError("expected an out-of-bounds call_indirect to trap")
    except OSError:
        pass


def test_call_indirect_traps_on_an_uninitialized_table_slot():
    b, dispatch_index = _build_indirect_dispatch()
    module, _ = _build_and_parse(b)
    # table_size=4 but only 3 slots (0-2) were populated by the element
    # segment in _build_indirect_dispatch; slot 3 is a real, in-bounds,
    # never-initialized hole.
    try:
        _run_jit_with_table(module, dispatch_index, [3, 1, 1], table_size=4)
        raise AssertionError("expected an uninitialized table slot to trap")
    except OSError:
        pass


def test_call_indirect_traps_on_a_type_signature_mismatch():
    b, _dispatch_index = _build_indirect_dispatch()
    # A function whose signature does NOT match type index 0 ((i32,i32)->i32),
    # placed in the same table so an indirect call declaring type 0 against
    # this slot must trap rather than silently misinterpreting its frame.
    mismatched = b.add_function((I32,), (I32,))
    mismatched.local_get(0)
    slot = 3
    b.add_element(0, slot, [b.function_index(mismatched)])

    caller = b.add_function((I32,), (I32,), export_name="call_slot3")
    caller.i32_const(1).i32_const(2)   # two bogus args for the (i32,i32)->i32 declared type
    caller.local_get(0)                 # the table slot to call (always 3 in this test)
    caller.call_indirect(0, 0)

    module, _ = _build_and_parse(b)
    try:
        _run_jit_with_table(module, b.function_index(caller), [slot], table_size=4)
        raise AssertionError("expected a call_indirect type mismatch to trap")
    except OSError:
        pass


ALL_TESTS = sorted(
    (v for k, v in globals().items() if k.startswith("test_") and callable(v)),
    key=lambda fn: fn.__code__.co_firstlineno,
)

if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"[PASS] All {len(ALL_TESTS)} x64 JIT end-to-end tests passed "
          f"(real .wasm bytes -> parsed -> JIT-compiled to x64 -> executed).")

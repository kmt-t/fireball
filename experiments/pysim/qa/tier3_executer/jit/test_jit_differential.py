"""
experiments/pysim/qa/tier3_executer/jit/test_jit_differential.py
Differential tests for the Tier 3 hybrid engine (interpreter + Copy-and-Patch JIT).

Every case runs the same WASM on three independent executors and requires identical results:
  1. the wasmtime native engine (independent of pysim),
  2. the Tier 3 interpreter (`Interpreter.call`), and
  3. the Tier 3 `RuntimeEngine` with the JIT enabled.

The targeted cases each pin one defect found by the large profiling workload
(docs/qa/bug_table/); the generated and clang-suite cases widen the coverage.
Traceability: docs/qa/tier3_executer/jit_runtime_test_spec.md (TEST-JITR-53 .. TEST-JITR-61).
"""

from __future__ import annotations

import random
from pathlib import Path

_TEST_FILE = Path(__file__).resolve()
_TESTS_DIR = _TEST_FILE.parents[2]
_PYSIM_DIR = _TESTS_DIR.parent


import wasmtime
from helpers import expect_assertion, make_interpreter, wat_to_wasm
from test_support import make_runtime_engine
from tier3_executer.interpreter.interpreter import Interpreter
from tier3_executer.jit.runtime_engine import RuntimeEngine
from tier3_executer.jit.x64_jit import TraceCompiler
from wasm_module import Module
from wasm_reader import parse

MASK32 = 0xFFFFFFFF
SUITE_WASM = _PYSIM_DIR / "benchmarks" / "profile" / "guest" / "suite.wasm"


def _wasmtime_result(wasm: bytes, export: str, args: list[int]) -> int:
    engine = wasmtime.Engine()
    store = wasmtime.Store(engine)
    instance = wasmtime.Instance(store, wasmtime.Module(engine, wasm), [])
    return instance.exports(store)[export](store, *args) & MASK32


def _guest(module: Module) -> Interpreter:
    """Interpreter over fresh linear memory with the active data segments applied."""
    memory = bytearray(module.memory.min_pages * 65536) if module.memory is not None else None
    if memory is not None:
        module.init_memory_data(memory, ())
    return make_interpreter(module, memory=memory)


def _tier2_result(wasm: bytes, export: str, args: list[int]) -> int:
    module = parse(wasm)
    return _guest(module).call(module.export_func_index(export), args)[0] & MASK32


def _tier3(
    wasm: bytes, export: str, args: list[int], compile_only: tuple[int, ...] | None = None
) -> tuple[int, RuntimeEngine]:
    """Run on the hybrid engine; hot blocks compile at the first yield.

    `compile_only` narrows the trackable-block bitmap to the given block heads, so every
    other block stays on the interpreter.
    """
    engine = make_runtime_engine(yield_threshold=3, candidate_threshold=0, jit_compiler=TraceCompiler())
    module = engine.load_wasm(wasm)
    if compile_only is not None:
        engine.jit_runtime.trackable.clear()
        for head_pc in compile_only:
            engine.jit_runtime.trackable.mark(head_pc)
    result = engine.call(_guest(module), module.export_func_index(export), args)
    return result[0] & MASK32, engine


def _check_three_ways(
    wat_or_wasm: str | bytes,
    export: str,
    args: list[int],
    compile_only: tuple[int, ...] | None = None,
) -> RuntimeEngine:
    wasm = wat_to_wasm(wat_or_wasm) if isinstance(wat_or_wasm, str) else wat_or_wasm
    expected = _wasmtime_result(wasm, export, args)
    assert _tier2_result(wasm, export, args) == expected, "Tier 2 diverged from wasmtime"
    tier3, engine = _tier3(wasm, export, args, compile_only)
    assert tier3 == expected, f"Tier 3 {tier3:#x} diverged from wasmtime {expected:#x}"
    return engine


# ---------------------------------------------------------------------------
# Targeted regression cases (one defect each)
# ---------------------------------------------------------------------------

LOOP_AFTER_HOT_BLOCK_WAT = """
(module
  (func $f (param $n i32) (result i32) (local $i i32) (local $s i32)
    (local.set $s (i32.add (local.get $s) (i32.const 7)))
    (local.set $s (i32.xor (local.get $s) (i32.const 21)))
    (local.set $s (i32.mul (local.get $s) (i32.const 3)))
    (block $exit
      (loop $l
        (br_if $exit (i32.ge_s (local.get $i) (local.get $n)))
        (local.set $s (i32.add (local.get $s) (i32.mul (local.get $i) (i32.const 3))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (local.get $s))
  (func (export "main") (param $reps i32) (result i32) (local $k i32) (local $acc i32)
    (loop $outer
      (local.set $acc (i32.add (local.get $acc) (call $f (i32.const 12))))
      (local.set $k (i32.add (local.get $k) (i32.const 1)))
      (br_if $outer (i32.lt_s (local.get $k) (local.get $reps))))
    (local.get $acc)))
"""


def resume_at_end_wat(pad: int) -> str:
    """A short `br_if` block (interpreter-only) whose not-taken path lands on an `end`.

    The `end` shares a 4-byte card with the long block that follows it, so a card-granular
    hot-block check would treat the `end` position as that block's head.  `pad` shifts the
    code alignment so that some value of it shares the card.
    """
    return f"""
(module
  (func (export "main") (param $n i32) (result i32)
    (local $i i32) (local $s i32) (local $flag i32)
    {"(nop)" * pad}
    (loop $l
      (local.set $flag (i32.and (local.get $i) (i32.const 1)))
      (block $b
        (br_if $b (local.get $flag)))
      (local.set $s (i32.xor (local.get $s) (i32.const 21)))
      (local.set $s (i32.add (local.get $s) (i32.const 3)))
      (local.set $s (i32.add (local.get $s) (local.get $i)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (local.get $n))))
    (local.get $s)))
"""


LOOP_EXIT_THEN_BR_WAT = """
(module
  (func $f (param $n i32) (result i32) (local $c i32) (local $s i32)
    (block $out
      (local.set $c (i32.const 0))
      (loop $lp
        (local.set $s (i32.add (local.get $s) (local.get $n)))
        (local.set $s (i32.xor (local.get $s) (local.get $c)))
        (local.set $c (i32.add (local.get $c) (i32.const 1)))
        (br_if $lp (i32.lt_s (local.get $c) (i32.const 4))))
      (br 0)
      (local.set $s (i32.const 999)))
    (local.get $s))
  (func (export "main") (param $reps i32) (result i32) (local $k i32) (local $acc i32)
    (loop $outer
      (local.set $acc (i32.add (i32.mul (local.get $acc) (i32.const 31)) (call $f (local.get $k))))
      (local.set $k (i32.add (local.get $k) (i32.const 1)))
      (br_if $outer (i32.lt_s (local.get $k) (local.get $reps))))
    (local.get $acc)))
"""

NESTED_BR_EXIT_WAT = """
(module
  (func (export "main") (param $n i32) (result i32) (local $i i32) (local $s i32)
    (loop $l
      (block $a
        (block $b
          (br_if $b (i32.and (local.get $i) (i32.const 1)))
          (local.set $s (i32.add (local.get $s) (i32.const 5)))
          (br_if $a (i32.and (local.get $i) (i32.const 2)))
          (local.set $s (i32.xor (local.get $s) (local.get $i))))
        (local.set $s (i32.add (local.get $s) (i32.const 3))))
      (block $c
        (br_if $c (i32.eqz (i32.and (local.get $i) (i32.const 4))))
        (local.set $s (i32.mul (local.get $s) (i32.const 3))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (local.get $n))))
    (local.get $s)))
"""

WIDE_LOCAL_FRAME_WAT = """
(module
  (func (export "main") (param $n i32) (result i32)
    (local $i i32) (local $s i32) (local $f f64)
    (loop $l
      (local.set $s (i32.add (local.get $s) (i32.mul (local.get $i) (i32.const 7))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (local.get $n))))
    (local.set $f (f64.const 2.5))
    (i32.add (local.get $s) (i32.trunc_f64_s (local.get $f)))))
"""

IF_TERMINATED_BLOCK_WAT = """
(module
  (func (export "main") (param $n i32) (result i32) (local $i i32) (local $s i32)
    (loop $l
      (local.set $s (i32.add (local.get $s) (local.get $i)))
      (if (i32.and (local.get $i) (i32.const 1))
        (then (local.set $s (i32.xor (local.get $s) (i32.const 21))))
        (else (local.set $s (i32.add (local.get $s) (i32.const 2)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (local.get $n))))
    (local.get $s)))
"""


MIXED_SLOT_WIDTH_WAT = """
(module
  (func $narrow (param $n i32) (result i32) (local $i i32) (local $s i32)
    (loop $l
      (local.set $s (i32.add (local.get $s) (i32.mul (local.get $i) (i32.const 5))))
      (local.set $s (i32.xor (local.get $s) (i32.shl (local.get $i) (i32.const 1))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (local.get $n))))
    (local.get $s))
  (func $wide (param $n i32) (result i32) (local $i i32) (local $s i32) (local $big i64)
    (local.set $big (i64.const 5000000000))
    (loop $l
      (local.set $s (i32.add (local.get $s) (i32.mul (local.get $i) (i32.const 9))))
      (local.set $s (i32.xor (local.get $s) (i32.shl (local.get $i) (i32.const 2))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (local.get $n))))
    (i32.add (local.get $s) (i32.wrap_i64 (i64.shr_u (local.get $big) (i64.const 3)))))
  (func (export "main") (param $reps i32) (result i32) (local $k i32) (local $acc i32)
    (loop $outer
      (local.set $acc
        (i32.add (i32.mul (local.get $acc) (i32.const 31))
          (i32.add (call $narrow (i32.add (local.get $k) (i32.const 8)))
                   (call $wide (i32.add (local.get $k) (i32.const 8))))))
      (local.set $k (i32.add (local.get $k) (i32.const 1)))
      (br_if $outer (i32.lt_s (local.get $k) (local.get $reps))))
    (local.get $acc)))
"""


def _assert_jit_ran(engine: RuntimeEngine) -> None:
    assert engine.stat_jit_invocations > 0, "the case never executed a compiled trace"


def test_jitr_53_loop_entered_after_hot_block_keeps_control_frames():
    """TEST-JITR-53: a compiled block that ends at `block` must not skip the frame push.

    Only the pre-loop block is compiled, so the loop's own branches run on the interpreter
    and resolve their target through the control-frame stack.
    """
    _assert_jit_ran(_check_three_ways(LOOP_AFTER_HOT_BLOCK_WAT, "main", [30], compile_only=(0,)))


def test_jitr_54_a_non_head_resume_point_never_enters_the_compile_queue():
    """TEST-JITR-54: an `end` position that shares a card with a block head is not a head."""
    for pad in range(4):
        _assert_jit_ran(_check_three_ways(resume_at_end_wat(pad), "main", [200]))


def test_jitr_55_stale_control_frames_are_dropped_before_structural_opcodes():
    """TEST-JITR-55: frames left by skipped `end`/`br` are truncated at every resume point.

    The loop exit falls through to a `br 0` that is neither a block head nor a structural
    opcode, so only the control-map depth calculation resolves the correct frame count.
    """
    _assert_jit_ran(_check_three_ways(NESTED_BR_EXIT_WAT, "main", [300]))
    _assert_jit_ran(_check_three_ways(LOOP_EXIT_THEN_BR_WAT, "main", [40]))


def test_jitr_56_jit_runs_in_a_frame_that_also_holds_a_wide_local():
    """TEST-JITR-56: locals own fixed slots, so an f64 local does not disable i32 traces."""
    _assert_jit_ran(_check_three_ways(WIDE_LOCAL_FRAME_WAT, "main", [200]))


def test_jitr_57_hot_block_ending_at_if_hands_the_condition_to_the_interpreter():
    """TEST-JITR-57: the `if` is executed by the interpreter, with its condition on the stack."""
    _assert_jit_ran(_check_three_ways(IF_TERMINATED_BLOCK_WAT, "main", [300]))


def test_jitr_60_frames_with_different_slot_widths_run_side_by_side():
    """TEST-JITR-60: 4-byte and 8-byte slot frames alternate on one engine, both compiled."""
    engine = _check_three_ways(MIXED_SLOT_WIDTH_WAT, "main", [40])
    _assert_jit_ran(engine)
    module = engine.module
    assert module is not None
    narrow, wide = module.functions[0], module.functions[1]
    assert narrow.local_width_map_cache is not None and wide.local_width_map_cache is not None
    assert (narrow.local_width_map_cache.slot_words, wide.local_width_map_cache.slot_words) == (
        1,
        2,
    )
    compiled_functions = {trace.head_pc >> 16 for _key, trace in engine.jit_runtime.cache.active.traces}
    assert compiled_functions >= {0, 1}, f"traces exist only for functions {compiled_functions}"


def _operand_overflow_wat() -> str:
    """`work` sums 40 values in one block; `pad` calls it with 30 values already pending."""
    work_pushes = " ".join(f"(i32.const {i + 1})" for i in range(40))
    work = f"(func $work (result i32) {work_pushes} {'(i32.add) ' * 39})"
    pad = f"(func $pad (result i32) {'(i32.const 7) ' * 30} (call $work) {'(i32.add) ' * 30})"
    return f"""
(module
  {work}
  {pad}
  (func (export "main") (result i32) (local $i i32) (local $acc i32)
    (loop $l
      (local.set $acc (i32.add (local.get $acc) (call $work)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $l (i32.lt_s (local.get $i) (i32.const 10))))
    (i32.add (local.get $acc) (call $pad))))
"""


def test_jitr_61_a_trace_that_would_overflow_the_operand_stack_runs_on_the_interpreter():
    """TEST-JITR-61: 30 pending values plus a 40-deep block exceed 64 words on every executor.

    `work` becomes a hot compiled block at an empty operand stack.  When `pad` calls it with 30
    values pending, the trace's spills would run past the capacity.  The engine must hand the
    block to the interpreter, which stops on the overflow, instead of the trace writing on.
    """
    wasm = wat_to_wasm(_operand_overflow_wat())
    interpreter_module = parse(wasm)
    with expect_assertion():
        _guest(interpreter_module).call(interpreter_module.export_func_index("main"), [])
    engine = make_runtime_engine(yield_threshold=3, candidate_threshold=0, jit_compiler=TraceCompiler())
    module = engine.load_wasm(wasm)
    with expect_assertion():
        engine.call(_guest(module), module.export_func_index("main"), [])
    assert engine.stat_jit_invocations > 0, "`work` never ran compiled before the overflow"


# ---------------------------------------------------------------------------
# Generated structured control flow
# ---------------------------------------------------------------------------


class _Generator:
    """Seeded generator of terminating structured-control-flow functions over i32 locals."""

    VARS = 4
    MAX_LOOPS = 4

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.labels: list[str] = []  # innermost last: "block" or "loop"
        self.loops_used = 0

    def expr(self, depth: int = 0) -> str:
        r = self.rng
        if depth >= 2 or r.random() < 0.35:
            return r.choice(
                [f"(local.get $v{r.randrange(self.VARS)})", f"(i32.const {r.randrange(-40, 40)})"]
            )
        op = r.choice(["i32.add", "i32.sub", "i32.mul", "i32.xor", "i32.and", "i32.or"])
        return f"({op} {self.expr(depth + 1)} {self.expr(depth + 1)})"

    def cond(self) -> str:
        r = self.rng
        cmp = r.choice(["i32.lt_s", "i32.gt_s", "i32.eq", "i32.ne", "i32.le_u"])
        return f"({cmp} {self.expr(1)} {self.expr(1)})"

    def block_targets(self) -> list[int]:
        """Relative depths of enclosing *blocks* (loops are left only by their counter)."""
        return [len(self.labels) - 1 - i for i, kind in enumerate(self.labels) if kind == "block"]

    def stmts(self, depth: int) -> str:
        return "\n".join(self.stmt(depth) for _ in range(self.rng.randrange(1, 4)))

    def stmt(self, depth: int) -> str:
        r = self.rng
        roll = r.random()
        targets = self.block_targets()
        if depth >= 4 or roll < 0.35:
            return f"(local.set $v{r.randrange(self.VARS)} {self.expr()})"
        if roll < 0.50 and targets:
            return f"(br_if {r.choice(targets)} {self.cond()})"
        if roll < 0.55 and targets:
            return f"(br {r.choice(targets)})"
        if roll < 0.70:
            self.labels.append("block")
            body = self.stmts(depth + 1)
            self.labels.pop()
            return f"(block\n{body})"
        if roll < 0.82:
            self.labels.append("if")
            then_body = self.stmts(depth + 1)
            else_body = self.stmts(depth + 1)
            self.labels.pop()
            return f"(if {self.cond()}\n(then {then_body})\n(else {else_body}))"
        if self.loops_used < self.MAX_LOOPS:
            counter = self.loops_used
            self.loops_used += 1
            bound = r.randrange(2, 6)
            self.labels.append("loop")
            body = self.stmts(depth + 1)
            self.labels.pop()
            return (
                f"(local.set $c{counter} (i32.const 0))\n(loop $lp{counter}\n{body}\n"
                f"(local.set $c{counter} (i32.add (local.get $c{counter}) (i32.const 1)))\n"
                f"(br_if $lp{counter} (i32.lt_s (local.get $c{counter}) (i32.const {bound}))))"
            )
        return f"(local.set $v{r.randrange(self.VARS)} {self.expr()})"

    def module(self) -> str:
        variables = " ".join(f"(local $v{i} i32)" for i in range(self.VARS))
        counters = " ".join(f"(local $c{i} i32)" for i in range(self.MAX_LOOPS))
        body = self.stmts(0)
        mixed = "(i32.add (i32.add (local.get $v0) (i32.mul (local.get $v1) (i32.const 3)))"
        mixed += " (i32.xor (local.get $v2) (i32.shl (local.get $v3) (i32.const 5))))"
        return f"""(module
  (func $work (param $a i32) (result i32) {variables} {counters}
    (local.set $v0 (local.get $a))
    (local.set $v1 (i32.const 11))
    (local.set $v2 (i32.const -3))
    (local.set $v3 (i32.const 97))
    {body}
    {mixed})
  (func (export "main") (param $reps i32) (result i32) (local $k i32) (local $acc i32)
    (loop $outer
      (local.set $acc (i32.add (i32.mul (local.get $acc) (i32.const 31)) (call $work (local.get $k))))
      (local.set $k (i32.add (local.get $k) (i32.const 1)))
      (br_if $outer (i32.lt_s (local.get $k) (local.get $reps))))
    (local.get $acc)))"""


GENERATED_SEEDS = tuple(range(60))


def test_jitr_58_generated_control_flow_matches_wasmtime_and_tier2():
    """TEST-JITR-58: seeded random block/loop/if/br nests agree on all three executors."""
    jit_invocations = 0
    native_control_handlers = 0
    for seed in GENERATED_SEEDS:
        wat = _Generator(seed).module()
        try:
            engine = _check_three_ways(wat, "main", [40])
        except AssertionError as error:
            raise AssertionError(f"generated program seed={seed}: {error}\n{wat}") from error
        jit_invocations += engine.stat_jit_invocations
        native_control_handlers += engine.stat_native_control_handlers
    assert jit_invocations > 200, f"generated programs barely used the JIT ({jit_invocations})"
    assert native_control_handlers > 0, "generated programs never exercised C++ control handlers"


# ---------------------------------------------------------------------------
# clang-generated kernel suite (benchmarks/profile/guest/suite.wasm)
# ---------------------------------------------------------------------------

# Work units are small enough for a quick run yet hot enough to compile traces.
CLANG_KERNEL_UNITS = (
    ("k_sha256", 8),
    ("k_crc32", 4),
    ("k_matmul", 1),
    ("k_fmatmul", 1),
    ("k_nbody", 40),
    ("k_mandel", 4),
    ("k_lz", 1),
    ("k_sieve", 1),
    ("k_expr", 40),
    ("k_vm", 4),
    ("k_dispatch", 400),
    ("k_hash", 3),
    ("k_int64", 60),
    ("k_life", 1),
    ("k_text", 2),
    ("k_fir", 1),
)


def test_jitr_59_clang_kernel_suite_matches_wasmtime_on_the_jit():
    """TEST-JITR-59: compiler-generated code (calls, br_table, f64, i64) runs identically."""
    wasm = SUITE_WASM.read_bytes()
    compiled = 0
    for kernel, units in CLANG_KERNEL_UNITS:
        expected = _wasmtime_result(wasm, kernel, [units])
        result, engine = _tier3(wasm, kernel, [units])
        assert result == expected, f"{kernel}({units}) = {result:#x}, wasmtime {expected:#x}"
        compiled += engine.stat_jit_invocations
    assert compiled > 1000, f"suite kernels barely used the JIT ({compiled} trace invocations)"


if __name__ == "__main__":
    test_jitr_53_loop_entered_after_hot_block_keeps_control_frames()
    test_jitr_54_a_non_head_resume_point_never_enters_the_compile_queue()
    test_jitr_55_stale_control_frames_are_dropped_before_structural_opcodes()
    test_jitr_56_jit_runs_in_a_frame_that_also_holds_a_wide_local()
    test_jitr_57_hot_block_ending_at_if_hands_the_condition_to_the_interpreter()
    test_jitr_60_frames_with_different_slot_widths_run_side_by_side()
    test_jitr_61_a_trace_that_would_overflow_the_operand_stack_runs_on_the_interpreter()
    test_jitr_58_generated_control_flow_matches_wasmtime_and_tier2()
    test_jitr_59_clang_kernel_suite_matches_wasmtime_on_the_jit()
    print("[PASS] All 9 Tier 3 JIT differential tests passed.")

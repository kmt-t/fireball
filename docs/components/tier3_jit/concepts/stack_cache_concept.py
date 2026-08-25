"""
docs/components/tier3_jit/concepts/stack_cache_concept.py
Reference Concept Implementation: Stack-Top Caching for Copy-and-Patch stencils
`{JIT_RegisterMapping}` `{ContextPointerRegister}` `{JIT_CopyAndPatch}`

The naive stencil set spends most of its emitted instructions shuffling the
operand stack: `i32.add` is POP/POP/ADD/PUSH, so 3 of 4 instructions are memory
traffic and only 1 is arithmetic. The stack machine itself, not the dispatch,
is the cost.

The standard remedy is to keep the top of the operand stack in registers and
select a stencil VARIANT according to how many operands are currently cached.
Copy-and-Patch pays nothing for this at compile time: the variants are all
pre-compiled byte sequences, so the extra table costs ROM, not cycles.

Register assignment (Cortex-M33 / AAPCS __fastcall convention):
    R0  = ip        (WASM PC / bytecode pointer)
    R1  = stack_bot (stack bottom context pointer `{ContextPointerRegister}`)
    R2  = env       (runtime environment pointer `{EnvironmentPointer}`)
    R3  = spill/scr (context spill: pinned mem_base/local_base or scratch, per-trace variant)
    R4  = TOS       (top of operand stack, JIT callee-saved cache)
    R5  = NOS       (next on stack, JIT callee-saved cache)
    R7  = FP        (AAPCS standard frame pointer - preserved)

Cache state is the number of operands currently held in registers: 0, 1 or 2.
A stencil declares what it consumes and produces, so the compiler tracks the
state statically and never emits a spill that is not required.
"""

from typing import Any, Callable


class WASMTrap(Exception):
    pass


MAX_CACHED = 2          # R4, R5


class Variant:
    """One stencil variant, valid for a specific incoming cache depth."""

    def __init__(self, code: list[str], depth_out: int, holes: tuple[str, ...] = ()):
        self.code = code
        self.depth_out = depth_out
        self.holes = holes

    def emit(self, **patch: Any) -> list[str]:
        for h in self.holes:
            if h not in patch:
                raise KeyError(f"missing relocation '{h}'")
        return [ln.format(**patch) for ln in self.code]


# Stencil table: op -> {incoming cache depth: Variant}
#
# Depth 0 = stack entirely in memory. Depth 1 = TOS in R4. Depth 2 = TOS in R4,
# NOS in R5. Entering a variant that would exceed MAX_CACHED spills first.
STENCILS: dict[str, dict[int, Variant]] = {
    "i32.const": {
        0: Variant(["MOVW R4, #{imm_lo}", "MOVT R4, #{imm_hi}"], 1, ("imm_lo", "imm_hi")),
        1: Variant(["MOV R5, R4", "MOVW R4, #{imm_lo}", "MOVT R4, #{imm_hi}"], 2,
                   ("imm_lo", "imm_hi")),
        2: Variant(["PUSH R5", "MOV R5, R4", "MOVW R4, #{imm_lo}", "MOVT R4, #{imm_hi}"], 2,
                   ("imm_lo", "imm_hi")),
    },
    "local.get": {
        0: Variant(["LDR R4, [R7, #{slot}]"], 1, ("slot",)),
        1: Variant(["MOV R5, R4", "LDR R4, [R7, #{slot}]"], 2, ("slot",)),
        2: Variant(["PUSH R5", "MOV R5, R4", "LDR R4, [R7, #{slot}]"], 2, ("slot",)),
    },
    "local.set": {
        1: Variant(["STR R4, [R7, #{slot}]"], 0, ("slot",)),
        2: Variant(["STR R4, [R7, #{slot}]", "MOV R4, R5"], 1, ("slot",)),
    },
    # Binary ops consume TOS and NOS, produce one value in TOS.
    "i32.add": {2: Variant(["ADD R4, R5, R4"], 1)},
    "i32.sub": {2: Variant(["SUB R4, R5, R4"], 1)},
    "i32.mul": {2: Variant(["MUL R4, R5, R4"], 1)},
    # Memory ops. `{MemoryBoundaryCheck}` `{FastAddressCheck}`
    # The bound check is a single mask because the guest RAM size is a power of
    # two; R8 holds ~(size-1).
    "i32.load": {
        1: Variant(["TST R4, R8", "BNE __trap_oob", "LDR R4, [R9, R4]"], 1),
        2: Variant(["TST R4, R8", "BNE __trap_oob", "LDR R4, [R9, R4]"], 2),
    },
    "i32.store": {
        2: Variant(["TST R5, R8", "BNE __trap_oob", "STR R4, [R9, R5]"], 0),
    },
    "backedge": {
        d: Variant(["LDR R1, [R6, #SAFEPOINT]", "CBNZ R1, __safepoint", "B #{target}"],
                   d, ("target",))
        for d in (0, 1, 2)
    },
}

# Refill from memory when a variant needs more operands than are cached.
REFILL = {
    (0, 1): ["POP R4"],
    (0, 2): ["POP R5", "POP R4"],
    (1, 2): ["POP R5", "MOV R5, R4", "MOV R4, R5"],   # conceptual; see _refill
}


class StackCachingCompiler:
    """Copy-and-Patch compiler that tracks operand-cache depth across stencils."""

    BYTES_PER_INSTRUCTION = 4

    def compile_block(self, ops: list[tuple[str, Any]],
                      loops_to: int | None = None) -> tuple[list[str], int]:
        """Returns (native listing, final cache depth)."""
        listing: list[str] = []
        depth = 0

        for op, arg in ops:
            table = STENCILS.get(op)
            if table is None:
                raise WASMTrap(f"NO_STENCIL_FOR: {op}")

            need = min(k for k in table)          # smallest depth this op supports
            if depth < need:
                listing += self._refill(depth, need)
                depth = need

            variant = table.get(depth) or table[max(table)]
            listing += self._emit(op, variant, arg)
            depth = variant.depth_out

        if loops_to is not None:
            # Operands must be back in memory before a backward edge, so that the
            # loop head always begins at a known cache depth.
            listing += self._spill(depth)
            depth = 0
            listing += STENCILS["backedge"][0].emit(target=loops_to)

        return listing, depth

    @staticmethod
    def _emit(op: str, variant: Variant, arg: Any) -> list[str]:
        if op == "i32.const":
            return variant.emit(imm_lo=arg & 0xFFFF, imm_hi=(arg >> 16) & 0xFFFF)
        if op in ("local.get", "local.set"):
            return variant.emit(slot=arg * 4)
        return variant.emit()

    @staticmethod
    def _refill(cur: int, need: int) -> list[str]:
        if cur == 0 and need == 1:
            return ["POP R4"]
        if cur == 0 and need == 2:
            return ["POP R5", "POP R4"]
        if cur == 1 and need == 2:
            return ["MOV R5, R4", "POP R4"]     # old TOS becomes NOS
        return []

    @staticmethod
    def _spill(depth: int) -> list[str]:
        if depth == 1:
            return ["PUSH R4"]
        if depth == 2:
            return ["PUSH R5", "PUSH R4"]
        return []


# ==============================================================================
# Tiny machine over the emitted listing, so a wrong stencil produces a wrong result
# ==============================================================================

def execute(listing: list[str], locals_: list[int], memory: bytearray | None = None,
            ram_mask: int = ~(8192 - 1) & 0xFFFF_FFFF,
            interrupt: bool = False) -> tuple[list[int], str]:
    R = {"R1": 0, "R4": 0, "R5": 0}
    stack: list[int] = []
    mem = memory if memory is not None else bytearray(8192)
    i = 0
    while i < len(listing):
        ins = listing[i]
        head = ins.split()[0]
        if head == "MOVW":
            R["R4"] = int(ins.split("#")[1])
        elif head == "MOVT":
            R["R4"] = (R["R4"] & 0xFFFF) | (int(ins.split("#")[1]) << 16)
        elif head == "MOV":
            dst, src = ins.replace(",", "").split()[1:3]
            R[dst] = R[src]
        elif head == "PUSH":
            stack.append(R[ins.split()[1]])
        elif head == "POP":
            if not stack:
                raise WASMTrap("STACK_UNDERFLOW")
            R[ins.split()[1]] = stack.pop()
        elif head in ("ADD", "SUB", "MUL"):
            d, a, b = ins.replace(",", "").split()[1:4]
            x, y = R[a], R[b]
            R[d] = ((x + y) if head == "ADD" else
                    (x - y) if head == "SUB" else (x * y)) & 0xFFFF_FFFF
        elif head == "LDR":
            if "SAFEPOINT" in ins:
                R["R1"] = 1 if interrupt else 0
            elif "[R7" in ins:
                slot = int(ins.split("#")[1].rstrip("]")) // 4
                if not 0 <= slot < len(locals_):
                    raise WASMTrap("LOCAL_INDEX_OUT_OF_RANGE")
                R["R4"] = locals_[slot]
            elif "[R9" in ins:
                a = R["R4"]
                R["R4"] = int.from_bytes(mem[a:a + 4], "little")
        elif head == "STR":
            if "[R7" in ins:
                slot = int(ins.split("#")[1].rstrip("]")) // 4
                if not 0 <= slot < len(locals_):
                    raise WASMTrap("LOCAL_INDEX_OUT_OF_RANGE")
                locals_[slot] = R["R4"]
            elif "[R9" in ins:
                a = R["R5"]
                mem[a:a + 4] = (R["R4"] & 0xFFFF_FFFF).to_bytes(4, "little")
        elif head == "TST":
            reg = ins.replace(",", "").split()[1]
            R["_z"] = (R[reg] & ram_mask) == 0
        elif head == "BNE":
            if not R.get("_z", True):
                return locals_, "TRAP_OOB"
        elif head == "CBNZ":
            if R["R1"]:
                return locals_, "SAFEPOINT_YIELD"
        elif head == "B":
            pass
        i += 1
    return locals_, "COMPLETED"


# ==============================================================================
# Verification tests
# ==============================================================================

LOOP_OPS = [("local.get", 1), ("local.get", 0), ("i32.mul", None), ("local.set", 1),
            ("local.get", 0), ("i32.const", 1), ("i32.sub", None), ("local.set", 0),
            ("local.get", 0)]

NAIVE = {
    "i32.const": 3, "i32.add": 4, "i32.sub": 4, "i32.mul": 4,
    "local.get": 2, "local.set": 2,
}


def test_stack_caching_cuts_memory_traffic():
    listing, _ = StackCachingCompiler().compile_block(LOOP_OPS)
    naive = sum(NAIVE[op] for op, _ in LOOP_OPS)
    mem_ops = sum(1 for i in listing if i.split()[0] in ("PUSH", "POP"))
    assert len(listing) < naive, f"cached {len(listing)} must beat naive {naive}"
    assert mem_ops == 0, f"this block needs no spill at all, got {mem_ops}: {listing}"


def test_binary_op_is_a_single_instruction():
    listing, depth = StackCachingCompiler().compile_block(
        [("local.get", 0), ("local.get", 1), ("i32.add", None)])
    assert listing[-1] == "ADD R4, R5, R4", listing
    assert depth == 1


def test_spill_only_when_the_cache_overflows():
    """A third live operand must spill NOS, and only then."""
    c = StackCachingCompiler()
    two, _ = c.compile_block([("local.get", 0), ("local.get", 1)])
    assert not any(i.startswith("PUSH") for i in two), two
    three, _ = c.compile_block([("local.get", 0), ("local.get", 1), ("local.get", 2)])
    assert sum(1 for i in three if i.startswith("PUSH")) == 1, three


def test_memory_access_carries_the_bound_check():
    listing, _ = StackCachingCompiler().compile_block(
        [("local.get", 0), ("i32.load", None)])
    assert "TST R4, R8" in listing and "BNE __trap_oob" in listing, listing


def test_out_of_bounds_load_traps():
    listing, _ = StackCachingCompiler().compile_block(
        [("local.get", 0), ("i32.load", None)])
    _, st = execute(listing, [0x4000])          # 0x4000 > 8KB
    assert st == "TRAP_OOB", st
    _, st = execute(listing, [0x100])
    assert st == "COMPLETED", st


def test_emitted_code_computes_the_right_answer():
    """5! via the cached-stencil listing, executed as native instructions."""
    c = StackCachingCompiler()
    loc = [5, 1]
    for _ in range(5):
        listing, _ = c.compile_block(LOOP_OPS)
        loc, st = execute(listing, loc)
        assert st == "COMPLETED"
        loc[1] = loc[1]
    assert loc[1] == 120, f"expected 120, got {loc[1]}"


def test_a_broken_stencil_is_detected():
    """Mutating MUL->ADD must change the computed result."""
    import copy
    saved = copy.deepcopy(STENCILS["i32.mul"])
    STENCILS["i32.mul"] = {2: Variant(["ADD R4, R5, R4"], 1)}
    try:
        c = StackCachingCompiler()
        loc = [5, 1]
        for _ in range(5):
            listing, _ = c.compile_block(LOOP_OPS)
            loc, _ = execute(listing, loc)
        assert loc[1] != 120, "a wrong stencil must not still yield 120"
    finally:
        STENCILS["i32.mul"] = saved


def test_backedge_spills_so_the_loop_head_state_is_known():
    listing, depth = StackCachingCompiler().compile_block(LOOP_OPS, loops_to=0x100)
    assert depth == 0, "cache must be empty across a backward edge"
    assert "CBNZ R1, __safepoint" in listing, "Safepoint at the backward edge"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    naive_total = sum(NAIVE[op] for op, _ in LOOP_OPS)
    cached, _ = StackCachingCompiler().compile_block(LOOP_OPS)
    print("[PASS] All stack-caching stencil tests passed.")
    print(f"       naive stencils : {naive_total} native instructions")
    print(f"       stack-cached   : {len(cached)} native instructions "
          f"({naive_total / len(cached):.2f}x fewer)")

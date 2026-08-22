"""
docs/components/tier2_runtime/concepts/runtime_engine_concept.py
Reference Concept Implementation: Integrated WASM Tiered Runtime Engine
Integrates:
1. WASM Stack Interpreter & Execution Context
2. 2-bit Hotspot Detection (UNEXECUTED -> EXECUTED -> HOT -> COMPILED)
3. Copy-and-Patch JIT Compiler with Stencils & Relocation Patching
4. 3-Bank Multi-Buffer JIT Code Cache (Active / Warm / Oldest) with Oldest-Only Promotion
5. Hardware MPU W^X Transaction Protocol (RW_XN <-> RO_X + DSB/ISB Barriers)
6. Cooperative Safepoint Polling in both Interpreter and JIT execution paths
"""

from typing import Any, Callable


# ==============================================================================
# 1. Hardware Protection & Exception Types
# ==============================================================================

class MPUAttribute:
    RO_X = "RO_X"        # Read-Only + Executable (Native code execution)
    RW_XN = "RW_XN"      # Read-Write + Non-Executable (JIT Patching / Promotion)
    NO_ACCESS = "NO_ACCESS"


class MPUFault(Exception):
    """Hardware Memory Protection Unit access violation."""
    pass


class WASMTrap(Exception):
    """WASM runtime trap (stack overflow, divide by zero, unaligned memory, etc.)."""
    pass


# ==============================================================================
# 2. 3-Bank JIT Code Cache & MPU W^X Manager
# ==============================================================================

class JITCodeEntry:
    """Compiled native code trace descriptor inside the JIT cache."""
    def __init__(self, func_id: str, wasm_pc: int, native_fn: Callable, size_bytes: int):
        self.func_id = func_id
        self.wasm_pc = wasm_pc
        self.native_fn = native_fn
        self.size_bytes = size_bytes
        self.access_counter = 0


class JITCacheBank:
    """Single 2KB cache bank."""
    def __init__(self, bank_id: int, capacity_bytes: int = 2048):
        self.bank_id = bank_id
        self.capacity_bytes = capacity_bytes
        self.used_bytes = 0
        self.entries: dict[int, JITCodeEntry] = {}  # wasm_pc -> JITCodeEntry

    def clear(self):
        self.used_bytes = 0
        self.entries.clear()

    def allocate(self, entry: JITCodeEntry) -> bool:
        if self.used_bytes + entry.size_bytes > self.capacity_bytes:
            return False
        self.entries[entry.wasm_pc] = entry
        self.used_bytes += entry.size_bytes
        return True


class JITMultiBufferCache:
    """
    3-Bank JIT Multi-Buffer Cache (Active / Warm / Oldest) with MPU W^X control.
    Total size: 6KB (2KB x 3) [FB_CONF_JIT_CACHE_SIZE].
    Implements Oldest-Only Promotion policy to prevent GC copy explosion.
    """
    HOT_PROMOTION_THRESHOLD = 5

    def __init__(self, bank_capacity: int = 2048):
        self.banks = [
            JITCacheBank(0, bank_capacity),  # Active
            JITCacheBank(1, bank_capacity),  # Warm (Observation window)
            JITCacheBank(2, bank_capacity),  # Oldest (Eviction / Promotion window)
        ]
        self.active_idx = 0
        self.warm_idx = 1
        self.oldest_idx = 2

        self.mpu_attr: str = MPUAttribute.RO_X  # Default: RO_X
        self.barrier_flushes: int = 0
        self.promotions_count: int = 0
        self.evictions_count: int = 0

    @property
    def active_bank(self) -> JITCacheBank:
        return self.banks[self.active_idx]

    @property
    def warm_bank(self) -> JITCacheBank:
        return self.banks[self.warm_idx]

    @property
    def oldest_bank(self) -> JITCacheBank:
        return self.banks[self.oldest_idx]

    # --- MPU W^X Transaction Protocol ---

    def begin_patch(self):
        """Switches JIT Code Cache MPU attribute to RW + XN before compilation/copy."""
        self.mpu_attr = MPUAttribute.RW_XN

    def commit_patch(self):
        """Restores JIT Code Cache MPU attribute to RO + X and issues DSB/ISB barriers."""
        assert self.mpu_attr == MPUAttribute.RW_XN, "Must be in patching mode before commit"
        self.mpu_attr = MPUAttribute.RO_X
        self.barrier_flushes += 1  # Hardware: __DSB(); __ISB();

    def check_execute_permission(self):
        if self.mpu_attr != MPUAttribute.RO_X:
            raise MPUFault("W^X VIOLATION: Execution attempted on non-executable JIT memory (RW_XN)")

    def check_write_permission(self):
        if self.mpu_attr != MPUAttribute.RW_XN:
            raise MPUFault("W^X VIOLATION: Write attempted on write-protected JIT memory (RO_X)")

    # --- Cache Lookup & Rotation ---

    def lookup(self, wasm_pc: int) -> JITCodeEntry | None:
        """Looks up compiled JIT entry across Active -> Warm -> Oldest banks."""
        # 1. Search Active
        if wasm_pc in self.active_bank.entries:
            entry = self.active_bank.entries[wasm_pc]
            entry.access_counter += 1
            return entry
        # 2. Search Warm
        if wasm_pc in self.warm_bank.entries:
            entry = self.warm_bank.entries[wasm_pc]
            entry.access_counter += 1
            return entry
        # 3. Search Oldest
        if wasm_pc in self.oldest_bank.entries:
            entry = self.oldest_bank.entries[wasm_pc]
            entry.access_counter += 1
            return entry
        return None

    def insert(self, entry: JITCodeEntry) -> bool:
        """Inserts a newly compiled entry into the Active bank under W^X protection."""
        self.check_write_permission()
        if self.active_bank.allocate(entry):
            return True
        # Active bank full: trigger generation rotation
        self.rotate_generation()
        return self.active_bank.allocate(entry)

    def rotate_generation(self):
        """
        Rotates cache generations:
        - Oldest bank is evaluated for promotion of HOT entries.
        - Cold entries in Oldest bank are evicted.
        - Oldest bank is cleared and becomes the new Active bank.
        """
        self.check_write_permission()

        # 1. Evaluate Oldest-Only Promotion
        candidates_to_promote = []
        for pc, entry in self.oldest_bank.entries.items():
            if entry.access_counter >= self.HOT_PROMOTION_THRESHOLD:
                candidates_to_promote.append(entry)
            else:
                self.evictions_count += 1

        # 2. Rotate indices: Oldest becomes new Active, Warm becomes Oldest, Active becomes Warm
        new_active = self.oldest_idx
        new_warm = self.active_idx
        new_oldest = self.warm_idx

        self.banks[new_active].clear()  # Clear new active bank

        self.active_idx = new_active
        self.warm_idx = new_warm
        self.oldest_idx = new_oldest

        # 3. Copy promoted entries into the new Active bank
        for entry in candidates_to_promote:
            entry.access_counter = 0  # Reset counter for new lifecycle
            if self.active_bank.allocate(entry):
                self.promotions_count += 1
            else:
                self.evictions_count += 1


# ==============================================================================
# 3. Copy-and-Patch JIT Compiler Engine
# ==============================================================================

class Stencil:
    def __init__(self, name: str, code_template: list[str], relocs: dict[str, int]):
        self.name = name
        self.code_template = code_template
        self.relocs = relocs


class CopyPatchCompiler:
    """
    Zero-Compile-Cost Copy-and-Patch JIT Compiler.
    Emits native execution closures from bytecode sequences using pre-compiled Stencils.
    """
    def __init__(self):
        # Stencil template catalog
        self.stencils = {
            "const": Stencil("const", ["MOVW R0, #{imm}", "PUSH R0"], {"imm": 0}),
            "add": Stencil("add", ["POP R1", "POP R0", "ADD R0, R0, R1", "PUSH R0"], {}),
            "sub": Stencil("sub", ["POP R1", "POP R0", "SUB R0, R0, R1", "PUSH R0"], {}),
            "mul": Stencil("mul", ["POP R1", "POP R0", "MUL R0, R0, R1", "PUSH R0"], {}),
        }

    def compile(self, func_id: str, wasm_pc: int, instructions: list[tuple[str, Any]]) -> JITCodeEntry:
        """
        Compiles WASM instruction block into a high-performance native closure.
        """
        estimated_size = len(instructions) * 16  # Approx 16 bytes per stencil

        def native_trace_executor(ctx: "WASMContext") -> str:
            # Native JIT fast-path execution loop
            pc = 0
            while pc < len(instructions):
                op, arg = instructions[pc]
                if op == "i32.const":
                    ctx.push(arg)
                elif op == "i32.add":
                    b, a = ctx.pop(), ctx.pop()
                    ctx.push((a + b) & 0xFFFF_FFFF)
                elif op == "i32.sub":
                    b, a = ctx.pop(), ctx.pop()
                    ctx.push((a - b) & 0xFFFF_FFFF)
                elif op == "i32.mul":
                    b, a = ctx.pop(), ctx.pop()
                    ctx.push((a * b) & 0xFFFF_FFFF)
                elif op == "local.get":
                    ctx.push(ctx.locals[arg])
                elif op == "local.set":
                    ctx.locals[arg] = ctx.pop()
                elif op == "local.tee":
                    val = ctx.stack[-1] if ctx.stack else 0
                    ctx.locals[arg] = val
                elif op == "br_if_loop_header":
                    cond = ctx.pop()
                    if cond != 0:
                        # Cooperative Safepoint check in JIT loop header
                        if ctx.check_safepoint():
                            return "SAFEPOINT_YIELD"
                        # Native loop backward branch
                        pc = arg
                        continue
                elif op == "return":
                    return "COMPLETED"
                else:
                    raise WASMTrap(f"JIT_UNSUPPORTED_OPCODE: {op}")
                pc += 1
            return "COMPLETED"

        return JITCodeEntry(func_id, wasm_pc, native_trace_executor, estimated_size)


# ==============================================================================
# 4. 2-Bit Hotspot Detector
# ==============================================================================

class HotspotState:
    UNEXECUTED = 0
    EXECUTED = 1
    HOT = 2
    COMPILED = 3


class HotspotDetector:
    """
    Monitors execution frequency per function/card.
    Transitions: UNEXECUTED -> EXECUTED -> HOT (Threshold reached) -> COMPILED
    """
    HOT_THRESHOLD = 3

    def __init__(self):
        self.invocation_counts: dict[str, int] = {}
        self.state_bitmap: dict[str, int] = {}

    def record_invocation(self, func_id: str) -> int:
        count = self.invocation_counts.get(func_id, 0) + 1
        self.invocation_counts[func_id] = count

        current_state = self.state_bitmap.get(func_id, HotspotState.UNEXECUTED)
        if current_state == HotspotState.UNEXECUTED:
            self.state_bitmap[func_id] = HotspotState.EXECUTED
        elif current_state == HotspotState.EXECUTED and count >= self.HOT_THRESHOLD:
            self.state_bitmap[func_id] = HotspotState.HOT

        return self.state_bitmap[func_id]

    def mark_compiled(self, func_id: str):
        self.state_bitmap[func_id] = HotspotState.COMPILED

    def mark_evicted(self, func_id: str):
        self.state_bitmap[func_id] = HotspotState.EXECUTED
        self.invocation_counts[func_id] = 0


# ==============================================================================
# 5. WASM Context & Integrated Runtime Engine
# ==============================================================================

class WASMContext:
    """Execution state and operand stack."""
    MAX_STACK_DEPTH = 64

    def __init__(self, memory_size: int = 65536):
        self.stack: list[int] = []
        self.locals: list[int] = []
        self.memory: bytearray = bytearray(memory_size)
        self.safepoint_pending: bool = False
        self.safepoints_hit: int = 0

    def push(self, val: int):
        if len(self.stack) >= self.MAX_STACK_DEPTH:
            raise WASMTrap("STACK_OVERFLOW")
        self.stack.append(val & 0xFFFF_FFFF)

    def pop(self) -> int:
        if not self.stack:
            raise WASMTrap("STACK_UNDERFLOW")
        return self.stack.pop()

    def set_safepoint_flag(self, flag: bool = True):
        self.safepoint_pending = flag

    def check_safepoint(self) -> bool:
        if self.safepoint_pending:
            self.safepoints_hit += 1
            return True
        return False


class IntegratedRuntimeEngine:
    """
    Complete Tiered WASM Runtime Engine:
    Interpreter ➔ Hotspot Profiling ➔ Copy-and-Patch JIT ➔ 3-Bank Cache ➔ MPU W^X
    """
    def __init__(self):
        self.cache = JITMultiBufferCache(bank_capacity=2048)
        self.compiler = CopyPatchCompiler()
        self.detector = HotspotDetector()

        # Metrics
        self.interpreter_executions = 0
        self.jit_executions = 0
        self.jit_compilations = 0

    def execute_function(self, func_id: str, instructions: list[tuple[str, Any]], ctx: WASMContext) -> str:
        """
        Executes a WASM function with dynamic Tier 1 / Tier 2 tiered dispatch.
        """
        wasm_pc = 0

        # 1. Check JIT Cache (Fast Path)
        jit_entry = self.cache.lookup(wasm_pc)
        if jit_entry is not None:
            # Native JIT Execution under MPU RO_X verification
            self.cache.check_execute_permission()
            self.jit_executions += 1
            return jit_entry.native_fn(ctx)

        # 2. Interpreter Path (Slow Path / Profiling)
        self.interpreter_executions += 1
        hotspot_state = self.detector.record_invocation(func_id)

        # 3. Check for JIT Tiering Trigger
        if hotspot_state == HotspotState.HOT:
            # Trigger Copy-and-Patch JIT under MPU W^X Transaction Protocol
            self.cache.begin_patch()
            try:
                new_jit_entry = self.compiler.compile(func_id, wasm_pc, instructions)
                self.cache.insert(new_jit_entry)
                self.detector.mark_compiled(func_id)
                self.jit_compilations += 1
            finally:
                self.cache.commit_patch()

        # 4. Execute via Interpreter
        return self._interpret_block(instructions, ctx)

    def _interpret_block(self, instructions: list[tuple[str, Any]], ctx: WASMContext) -> str:
        pc = 0
        while pc < len(instructions):
            op, arg = instructions[pc]

            if op == "i32.const":
                ctx.push(arg)
            elif op == "i32.add":
                b, a = ctx.pop(), ctx.pop()
                ctx.push((a + b) & 0xFFFF_FFFF)
            elif op == "i32.sub":
                b, a = ctx.pop(), ctx.pop()
                ctx.push((a - b) & 0xFFFF_FFFF)
            elif op == "i32.mul":
                b, a = ctx.pop(), ctx.pop()
                ctx.push((a * b) & 0xFFFF_FFFF)
            elif op == "local.get":
                assert 0 <= arg < len(ctx.locals), "Local index out of range"
                ctx.push(ctx.locals[arg])
            elif op == "local.set":
                assert 0 <= arg < len(ctx.locals), "Local index out of range"
                ctx.locals[arg] = ctx.pop()
            elif op == "local.tee":
                val = ctx.stack[-1] if ctx.stack else 0
                ctx.locals[arg] = val
            elif op == "br_if_loop_header":
                cond = ctx.pop()
                if cond != 0:
                    if ctx.check_safepoint():
                        return "SAFEPOINT_YIELD"
                    pc = arg
                    continue
            elif op == "return":
                return "COMPLETED"
            else:
                raise WASMTrap(f"UNSUPPORTED_OPCODE: {op}")

            pc += 1

        return "COMPLETED"


# ==============================================================================
# 6. Verification Tests & Invariant Assertions
# ==============================================================================

def test_tiering_cold_to_hot_and_jit_switch():
    """Verifies that Cold functions run on Interpreter and automatically tier-up to JIT."""
    engine = IntegratedRuntimeEngine()
    ctx = WASMContext()

    # Simple function: (a + b)
    instructions = [
        ("local.get", 0),
        ("local.get", 1),
        ("i32.add", None),
        ("return", None),
    ]

    # Invocations 1, 2: Executed on Interpreter
    ctx.locals = [10, 20]
    res1 = engine.execute_function("func_add", instructions, ctx)
    assert res1 == "COMPLETED"
    assert ctx.pop() == 30
    assert engine.interpreter_executions == 1
    assert engine.jit_executions == 0

    ctx.locals = [30, 40]
    res2 = engine.execute_function("func_add", instructions, ctx)
    assert res2 == "COMPLETED"
    assert ctx.pop() == 70
    assert engine.interpreter_executions == 2
    assert engine.jit_executions == 0

    # Invocation 3: Hits HOT threshold -> JIT Compiled during execution
    ctx.locals = [50, 60]
    res3 = engine.execute_function("func_add", instructions, ctx)
    assert res3 == "COMPLETED"
    assert ctx.pop() == 110
    assert engine.interpreter_executions == 3
    assert engine.jit_compilations == 1
    assert engine.cache.lookup(0) is not None

    # Invocation 4: Fast-path execution directly via JIT!
    ctx.locals = [100, 200]
    res4 = engine.execute_function("func_add", instructions, ctx)
    assert res4 == "COMPLETED"
    assert ctx.pop() == 300
    assert engine.jit_executions == 1
    assert engine.interpreter_executions == 3  # Unchanged!


def test_three_bank_cache_rotation_and_oldest_promotion():
    """Verifies Active/Warm/Oldest bank rotation and Oldest-Only promotion."""
    engine = IntegratedRuntimeEngine()
    ctx = WASMContext()

    # Compile a hot function
    instructions = [("i32.const", 42), ("return", None)]
    ctx.locals = []
    for _ in range(3):
        engine.execute_function("hot_fn", instructions, ctx)
        ctx.pop()

    assert engine.jit_compilations == 1
    assert 0 in engine.cache.active_bank.entries

    # Warm up access counter in Oldest bank
    jit_entry = engine.cache.lookup(0)
    for _ in range(10):
        engine.cache.lookup(0)  # Increment access_counter

    # Rotate 1: Active -> Warm
    engine.cache.begin_patch()
    engine.cache.rotate_generation()
    engine.cache.commit_patch()
    assert 0 in engine.cache.warm_bank.entries

    # Rotate 2: Warm -> Oldest
    engine.cache.begin_patch()
    engine.cache.rotate_generation()
    engine.cache.commit_patch()
    assert 0 in engine.cache.oldest_bank.entries

    # Rotate 3: Oldest -> Promoted back to Active (access_counter >= 5)
    engine.cache.begin_patch()
    engine.cache.rotate_generation()
    engine.cache.commit_patch()
    assert 0 in engine.cache.active_bank.entries
    assert engine.cache.promotions_count == 1


def test_mpu_wx_hardware_protection():
    """Verifies that MPU W^X invariants are strictly enforced during runtime execution."""
    cache = JITMultiBufferCache()
    dummy_entry = JITCodeEntry("test", 0, lambda ctx: "OK", 64)

    # 1. Writing outside patch mode must raise MPUFault (RO_X violation)
    try:
        cache.insert(dummy_entry)
        assert False, "Should have raised MPUFault"
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)

    # 2. Execution during patch mode must raise MPUFault (RW_XN violation)
    cache.begin_patch()
    cache.insert(dummy_entry)
    try:
        cache.check_execute_permission()
        assert False, "Should have raised MPUFault"
    except MPUFault as e:
        assert "W^X VIOLATION" in str(e)
    finally:
        cache.commit_patch()

    # 3. After commit, execution permission is restored
    cache.check_execute_permission()
    assert cache.barrier_flushes == 1


def test_safepoint_interruption_in_interpreter_and_jit():
    """Verifies cooperative safepoint polling yields execution safely."""
    engine = IntegratedRuntimeEngine()
    ctx = WASMContext()

    # Loop countdown: local 0 = n; while (n > 0) { n = n - 1; }
    loop_instructions = [
        ("local.get", 0),
        ("i32.const", 1),
        ("i32.sub", None),
        ("local.tee", 0),
        ("br_if_loop_header", 0),
        ("return", None),
    ]

    # Test Interpreter Safepoint
    ctx.locals = [10]
    ctx.set_safepoint_flag(True)
    status = engine.execute_function("loop_fn", loop_instructions, ctx)
    assert status == "SAFEPOINT_YIELD"
    assert ctx.safepoints_hit == 1

    # Tier-up to JIT
    ctx.set_safepoint_flag(False)
    for _ in range(3):
        ctx.locals = [10]
        engine.execute_function("loop_fn", loop_instructions, ctx)

    assert engine.cache.lookup(0) is not None

    # Test JIT Safepoint
    ctx.locals = [10]
    ctx.set_safepoint_flag(True)
    jit_status = engine.execute_function("loop_fn", loop_instructions, ctx)
    assert jit_status == "SAFEPOINT_YIELD"
    assert ctx.safepoints_hit >= 2


def test_factorial_computation_equivalence():
    """Verifies that Interpreter and JIT produce bit-exact identical arithmetic results."""
    engine = IntegratedRuntimeEngine()

    # Factorial bytecode: local 0: n, local 1: acc
    # while (n > 1) { acc = acc * n; n = n - 1; } return acc;
    factorial_ops = [
        ("local.get", 1),
        ("local.get", 0),
        ("i32.mul", None),
        ("local.set", 1),
        ("local.get", 0),
        ("i32.const", 1),
        ("i32.sub", None),
        ("local.tee", 0),
        ("i32.const", 1),
        ("i32.sub", None),  # cond = (n - 1)
        ("br_if_loop_header", 0),
        ("local.get", 1),
        ("return", None),
    ]

    # 1. Calculate 5! with Interpreter (Cold)
    ctx1 = WASMContext()
    ctx1.locals = [5, 1]
    engine.execute_function("fact_fn", factorial_ops, ctx1)
    res_interp = ctx1.pop()
    assert res_interp == 120

    # 2. Warm up to JIT
    for _ in range(3):
        ctx_warm = WASMContext()
        ctx_warm.locals = [5, 1]
        engine.execute_function("fact_fn", factorial_ops, ctx_warm)

    # 3. Calculate 5! and 10! with JIT (Hot)
    ctx2 = WASMContext()
    ctx2.locals = [5, 1]
    engine.execute_function("fact_fn", factorial_ops, ctx2)
    res_jit5 = ctx2.pop()
    assert res_jit5 == 120
    assert res_jit5 == res_interp

    ctx3 = WASMContext()
    ctx3.locals = [10, 1]
    engine.execute_function("fact_fn", factorial_ops, ctx3)
    res_jit10 = ctx3.pop()
    assert res_jit10 == 3628800


if __name__ == "__main__":
    test_tiering_cold_to_hot_and_jit_switch()
    test_three_bank_cache_rotation_and_oldest_promotion()
    test_mpu_wx_hardware_protection()
    test_safepoint_interruption_in_interpreter_and_jit()
    test_factorial_computation_equivalence()
    print("OK  runtime_engine_concept.py (All 5 integrated tiered runtime tests passed!)")

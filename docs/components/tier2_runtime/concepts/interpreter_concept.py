"""
docs/components/tier2_runtime/concepts/interpreter_concept.py
Reference Concept Implementation: WASM Stack Interpreter & Cooperative Safepoint
- Virtual operand stack management with bounds checks
- Bytecode execution loop (i32 arithmetic, locals, branches)
- Cooperative safepoint polling at loop headers for deterministic interruption
"""

from typing import Any


class WASMTrap(Exception):
    pass


class WASMInterpreter:
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
        """Cooperative safepoint check at loop headers and backward branches."""
        if self.safepoint_pending:
            self.safepoints_hit += 1
            return True
        return False

    def execute_block(self, instructions: list[tuple[str, Any]]) -> str:
        """
        Executes a sequence of WASM instructions.
        Returns execution status ("COMPLETED", "SAFEPOINT_YIELD", or raises WASMTrap).
        """
        pc = 0
        while pc < len(instructions):
            op, arg = instructions[pc]

            if op == "i32.const":
                self.push(arg)
            elif op == "i32.add":
                b, a = self.pop(), self.pop()
                self.push(a + b)
            elif op == "i32.sub":
                b, a = self.pop(), self.pop()
                self.push(a - b)
            elif op == "i32.mul":
                b, a = self.pop(), self.pop()
                self.push(a * b)
            elif op == "local.get":
                assert 0 <= arg < len(self.locals), "Local index out of range"
                self.push(self.locals[arg])
            elif op == "local.set":
                assert 0 <= arg < len(self.locals), "Local index out of range"
                self.locals[arg] = self.pop()
            elif op == "local.tee":
                val = self.stack[-1] if self.stack else 0
                self.locals[arg] = val
            elif op == "br_if_loop_header":
                # Conditional branch back to loop header with Safepoint Check
                cond = self.pop()
                if cond != 0:
                    if self.check_safepoint():
                        return "SAFEPOINT_YIELD"
                    pc = arg  # Jump to loop header target pc
                    continue
            elif op == "return":
                return "COMPLETED"
            else:
                raise WASMTrap(f"UNSUPPORTED_OPCODE: {op}")

            pc += 1

        return "COMPLETED"


# ==============================================================================
# Simulation / Verification Tests
# ==============================================================================

def test_wasm_factorial_computation():
    vm = WASMInterpreter()
    vm.locals = [5, 1]  # local 0: n = 5, local 1: acc = 1

    # Bytecode representing factorial loop:
    # 0: (Loop Header)
    #    local.get 0 (n)
    #    i32.const 0
    #    if n == 0 goto end
    #    local.get 1 (acc)
    #    local.get 0 (n)
    #    i32.mul
    #    local.set 1 (acc)
    #    local.get 0 (n)
    #    i32.const 1
    #    i32.sub
    #    local.set 0 (n)
    #    local.get 0 (n)
    #    br_if_loop_header 0
    instructions = [
        ("local.get", 1),  # [acc]
        ("local.get", 0),  # [acc, n]
        ("i32.mul", None), # [acc * n]
        ("local.set", 1),  # acc = acc * n
        ("local.get", 0),  # [n]
        ("i32.const", 1),  # [n, 1]
        ("i32.sub", None), # [n - 1]
        ("local.set", 0),  # n = n - 1
        ("local.get", 0),  # [n]
        ("br_if_loop_header", 0),  # if n > 0 repeat
        ("return", None)
    ]

    status = vm.execute_block(instructions)
    assert status == "COMPLETED"
    assert vm.locals[1] == 120, f"Expected 5! = 120, got {vm.locals[1]}"


def test_cooperative_safepoint_yield():
    vm = WASMInterpreter()
    vm.locals = [10]  # Counter = 10

    # Infinite loop that decrements counter
    instructions = [
        ("local.get", 0),
        ("i32.const", 1),
        ("i32.sub", None),
        ("local.set", 0),
        ("local.get", 0),
        ("br_if_loop_header", 0),
    ]

    # Trigger safepoint request externally
    vm.set_safepoint_flag(True)

    status = vm.execute_block(instructions)
    assert status == "SAFEPOINT_YIELD"
    assert vm.safepoints_hit == 1


def test_stack_overflow_trap():
    vm = WASMInterpreter()
    try:
        for i in range(70):
            vm.push(i)
        assert False, "Should have thrown STACK_OVERFLOW"
    except WASMTrap as e:
        assert "STACK_OVERFLOW" in str(e)


if __name__ == "__main__":
    test_wasm_factorial_computation()
    test_cooperative_safepoint_yield()
    test_stack_overflow_trap()
    print("[PASS] All WASM Interpreter concept tests passed successfully.")

"""
docs/components/tier2_runtime/concepts/debugger_concept.py
Reference Concept Implementation: Debugger Manager & GDB RSP Interpreter Fallback
`{RSPMinimalSet}` `{DebuggerLabelTableSwitch}` `{MemoryIsolation}` `{Debug_Integrated}` `{ContextPointerRegister}`

Architecture:
- JIT Fallback: When debugger attaches, JIT execution is bypassed and execution falls back to the Interpreter.
- Unified Stack Inspection: Inspects execution_context, CallFrames, locals, and operand stack from stack_bot.
- GDB Remote Serial Protocol (RSP): Minimal packet parser & responses ($g, $m, $M, $Z0, $z0, $s, $c, $?).
- Integrated Profiler & Test Tool: PC sampling counter and memory/register assertion hooks ({Debug_Integrated}).
"""


class WASMTrap(Exception):
    pass


# ==============================================================================
# 1. Unified Stack & Execution Context Model
# ==============================================================================


class CallFrame:
    """Inline CallFrame placed on the unified stack."""

    def __init__(self, parent_offset: int, return_pc: int, func_idx: int, local_base: int):
        self.parent_offset = parent_offset
        self.return_pc = return_pc
        self.func_idx = func_idx
        self.local_base = local_base


class ExecutionContext:
    """
    Execution Context resident at Stack Bottom (offset 0).
    `{ContextPointerRegister}`
    """

    def __init__(self, memory_size: int = 65536, stack_capacity: int = 512):
        self.pc: int = 0
        self.stack: list[int] = [0] * stack_capacity  # Unified stack buffer
        self.sp_offset: int = 0  # Current stack growth length
        self.current_frame: CallFrame | None = None
        self.memory: bytearray = bytearray(memory_size)
        self.is_debug_mode: bool = False
        self.halted: bool = False
        self.stop_signal: int = 5  # SIGTRAP (5)


# ==============================================================================
# 2. WASM Interpreter Engine (with Step & Debug Hook)
# ==============================================================================


class WASMInterpreter:
    def __init__(self):
        pass

    def push(self, ctx: ExecutionContext, val: int) -> None:
        if ctx.sp_offset >= len(ctx.stack):
            raise WASMTrap("STACK_OVERFLOW")
        ctx.stack[ctx.sp_offset] = val & 0xFFFF_FFFF
        ctx.sp_offset += 1

    def pop(self, ctx: ExecutionContext) -> int:
        if ctx.sp_offset <= 0:
            raise WASMTrap("STACK_UNDERFLOW")
        ctx.sp_offset -= 1
        return ctx.stack[ctx.sp_offset]

    def peek(self, ctx: ExecutionContext) -> int:
        if ctx.sp_offset <= 0:
            raise WASMTrap("STACK_UNDERFLOW")
        return ctx.stack[ctx.sp_offset - 1]

    def step(self, ctx: ExecutionContext, bytecode: list[tuple[str, object]]) -> str:
        """
        Executes exactly one WASM bytecode instruction.
        Returns: "CONTINUE", "RETURN", "TRAP", or raises WASMTrap.
        """
        if ctx.pc >= len(bytecode):
            return "TERMINATED"
        op, arg = bytecode[ctx.pc]
        if op == "i32.const":
            self.push(ctx, arg)
            ctx.pc += 1
        elif op == "i32.add":
            b, a = self.pop(ctx), self.pop(ctx)
            self.push(ctx, a + b)
            ctx.pc += 1
        elif op == "i32.sub":
            b, a = self.pop(ctx), self.pop(ctx)
            self.push(ctx, a - b)
            ctx.pc += 1
        elif op == "i32.mul":
            b, a = self.pop(ctx), self.pop(ctx)
            self.push(ctx, a * b)
            ctx.pc += 1
        elif op == "local.get":
            local_idx = arg
            val = ctx.stack[local_idx]
            self.push(ctx, val)
            ctx.pc += 1
        elif op == "local.set":
            local_idx = arg
            ctx.stack[local_idx] = self.pop(ctx)
            ctx.pc += 1
        elif op == "br":
            ctx.pc = arg
        elif op == "br_if":
            cond = self.pop(ctx)
            if cond != 0:
                ctx.pc = arg
            else:
                ctx.pc += 1
        elif op == "return":
            return "RETURN"
        elif op == "nop":
            ctx.pc += 1
        else:
            raise WASMTrap(f"UNKNOWN_OPCODE: {op}")
        return "CONTINUE"


# ==============================================================================
# 3. Debugger Manager & Integrated Profiler Engine
# ==============================================================================


class DebuggerManager:
    """
    Debugger Controller managing GDB RSP, Breakpoints, and Interpreter Fallback.
    `{RSPMinimalSet}` `{DebuggerLabelTableSwitch}` `{Debug_Integrated}`
    """

    def __init__(self, ctx: ExecutionContext, interpreter: WASMInterpreter):
        self.ctx = ctx
        self.interpreter = interpreter
        self.breakpoints: set[int] = set()
        self.attached: bool = False
        # Integrated Profiler & Test Tool statistics ({Debug_Integrated})
        self.pc_sample_counts: dict[int, int] = {}
        self.memory_assertions: list[tuple[int, int, str]] = []  # (addr, expected_val, desc)
        self.assertion_violations: list[str] = []

    def attach(self) -> None:
        """
        Attaches debugger, disables JIT (Interpreter Fallback), and switches to debug table.
        `{DebuggerLabelTableSwitch}`
        """
        self.attached = True
        self.ctx.is_debug_mode = True
        self.ctx.halted = True
        self.ctx.stop_signal = 5  # SIGTRAP

    def detach(self) -> None:
        self.attached = False
        self.ctx.is_debug_mode = False
        self.ctx.halted = False

    def add_breakpoint(self, pc: int) -> None:
        self.breakpoints.add(pc)

    def remove_breakpoint(self, pc: int) -> None:
        self.breakpoints.discard(pc)

    def add_memory_assertion(self, addr: int, expected: int, desc: str = "") -> None:
        self.memory_assertions.append((addr, expected, desc))

    def _sample_pc(self) -> None:
        """Records runtime PC execution frequency for profiling ({Debug_Integrated})."""
        pc = self.ctx.pc
        self.pc_sample_counts[pc] = self.pc_sample_counts.get(pc, 0) + 1

    def _verify_assertions(self) -> None:
        """Verifies dynamic memory assertions ({Debug_Integrated})."""
        for addr, expected, desc in self.memory_assertions:
            if addr < len(self.ctx.memory):
                val = self.ctx.memory[addr]
                if val != expected:
                    msg = f"ASSERTION_FAILED: addr 0x{addr:X} expected {expected} but got {val} ({desc})"
                    self.assertion_violations.append(msg)

    def step_instruction(self, bytecode: list[tuple[str, object]]) -> str:
        """Single-steps one instruction via Interpreter Fallback."""
        if not self.attached:
            raise RuntimeError("Debugger not attached")
        self._sample_pc()
        res = self.interpreter.step(self.ctx, bytecode)
        self._verify_assertions()
        self.ctx.halted = True
        self.ctx.stop_signal = 5  # SIGTRAP
        return res

    def continue_execution(self, bytecode: list[tuple[str, object]]) -> str:
        """Resumes execution until a breakpoint, termination, or trap is hit."""
        if not self.attached:
            raise RuntimeError("Debugger not attached")
        self.ctx.halted = False
        while not self.ctx.halted:
            # Check breakpoint
            if self.ctx.pc in self.breakpoints:
                self.ctx.halted = True
                self.ctx.stop_signal = 5
                return "BREAKPOINT_HIT"
            self._sample_pc()
            res = self.interpreter.step(self.ctx, bytecode)
            self._verify_assertions()
            if res in ("RETURN", "TERMINATED"):
                self.ctx.halted = True
                self.ctx.stop_signal = 0  # Process terminated cleanly
                return res
            if self.ctx.pc in self.breakpoints:
                self.ctx.halted = True
                self.ctx.stop_signal = 5
                return "BREAKPOINT_HIT"
        return "STOPPED"


# ==============================================================================
# 4. Minimal GDB RSP (Remote Serial Protocol) Parser
# ==============================================================================


class GDBRspProtocol:
    """
    GDB RSP Protocol Packet Formatter & Dispatcher.
    `{RSPMinimalSet}`
    """

    def __init__(self, dbg: DebuggerManager):
        self.dbg = dbg

    @staticmethod
    def calculate_checksum(data: str) -> str:
        cksum = sum(ord(c) for c in data) % 256
        return f"{cksum:02x}"

    @classmethod
    def format_packet(cls, payload: str) -> str:
        return f"${payload}#{cls.calculate_checksum(payload)}"

    def handle_packet(self, packet: str, bytecode: list[tuple[str, object]]) -> str:
        """
        Parses an incoming GDB RSP packet (e.g. '$?#3f', '$g#67', '$s#73') and returns response packet.
        """
        if packet.startswith("$") and "#" in packet:
            payload = packet[1 : packet.rfind("#")]
        else:
            payload = packet

        if not payload:
            return self.format_packet("")
        cmd = payload[0]
        # 1. Query Halt Reason ($?)
        if cmd == "?":
            return self.format_packet(f"S{self.dbg.ctx.stop_signal:02x}")
        # 2. Read General Registers ($g) -> PC (Reg 0), LR (Reg 1), SP (Reg 2), FP (Reg 3)
        elif cmd == "g":
            pc = self.dbg.ctx.pc
            sp = self.dbg.ctx.sp_offset
            lr = 0
            fp = 0

            # 32-bit registers formatted as 8 hex chars (little-endian)
            def to_hex32(val: int) -> str:
                b = val.to_bytes(4, byteorder="little")
                return b.hex()

            reg_data = to_hex32(pc) + to_hex32(lr) + to_hex32(sp) + to_hex32(fp)
            return self.format_packet(reg_data)
        # 3. Read Memory ($m<addr>,<length>)
        elif cmd == "m":
            parts = payload[1:].split(",")
            if len(parts) == 2:
                addr = int(parts[0], 16)
                length = int(parts[1], 16)
                if addr + length <= len(self.dbg.ctx.memory):
                    mem_slice = self.dbg.ctx.memory[addr : addr + length]
                    return self.format_packet(mem_slice.hex())
                else:
                    return self.format_packet("E01")  # Memory boundary check error
            return self.format_packet("E00")
        # 4. Write Memory ($M<addr>,<length>:<data>)
        elif cmd == "M":
            header, data_hex = payload[1:].split(":")
            addr_str, len_str = header.split(",")
            addr = int(addr_str, 16)
            length = int(len_str, 16)
            data_bytes = bytes.fromhex(data_hex)
            if addr + length <= len(self.dbg.ctx.memory):
                self.dbg.ctx.memory[addr : addr + length] = data_bytes
                return self.format_packet("OK")
            else:
                return self.format_packet("E01")
        # 5. Insert Breakpoint ($Z0,<addr>,<kind>)
        elif payload.startswith("Z0,"):
            parts = payload.split(",")
            pc = int(parts[1], 16)
            self.dbg.add_breakpoint(pc)
            return self.format_packet("OK")
        # 6. Remove Breakpoint ($z0,<addr>,<kind>)
        elif payload.startswith("z0,"):
            parts = payload.split(",")
            pc = int(parts[1], 16)
            self.dbg.remove_breakpoint(pc)
            return self.format_packet("OK")
        # 7. Single Step ($s)
        elif cmd == "s":
            self.dbg.step_instruction(bytecode)
            return self.format_packet(f"S{self.dbg.ctx.stop_signal:02x}")
        # 8. Continue ($c)
        elif cmd == "c":
            self.dbg.continue_execution(bytecode)
            return self.format_packet(f"S{self.dbg.ctx.stop_signal:02x}")
        # Unsupported command
        return self.format_packet("")


# ==============================================================================
# 5. Simulation & Verification Tests
# ==============================================================================


def test_debugger_step_and_registers() -> None:
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    dbg = DebuggerManager(ctx, interp)
    rsp = GDBRspProtocol(dbg)
    bytecode = [
        ("i32.const", 42),
        ("i32.const", 8),
        ("i32.add", None),
        ("return", None),
    ]
    dbg.attach()
    assert ctx.is_debug_mode is True
    # Query initial stop reason
    resp = rsp.handle_packet("$?#3f", bytecode)
    assert resp == "$S05#b8"
    # Step 1: i32.const 42
    resp = rsp.handle_packet("$s#73", bytecode)
    assert ctx.pc == 1
    assert ctx.sp_offset == 1
    assert ctx.stack[0] == 42
    # Step 2: i32.const 8
    resp = rsp.handle_packet("$s#73", bytecode)
    assert ctx.pc == 2
    assert ctx.sp_offset == 2
    assert ctx.stack[1] == 8
    # Step 3: i32.add -> result 50
    resp = rsp.handle_packet("$s#73", bytecode)
    assert ctx.pc == 3
    assert ctx.sp_offset == 1
    assert ctx.stack[0] == 50


def test_debugger_breakpoint_and_continue() -> None:
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    dbg = DebuggerManager(ctx, interp)
    rsp = GDBRspProtocol(dbg)
    # Loop computing sum 1..5
    # local 0 = counter (5), local 1 = sum (0)
    ctx.stack[0] = 5
    ctx.stack[1] = 0
    ctx.sp_offset = 2
    bytecode = [
        ("local.get", 0),  # PC 0
        ("local.get", 1),  # PC 1
        ("i32.add", None),  # PC 2
        ("local.set", 1),  # PC 3: sum += counter
        ("local.get", 0),  # PC 4
        ("i32.const", 1),  # PC 5
        ("i32.sub", None),  # PC 6
        ("local.set", 0),  # PC 7: counter -= 1
        ("local.get", 0),  # PC 8: check counter != 0
        ("br_if", 0),  # PC 9: loop back to PC 0 if counter > 0
        ("return", None),  # PC 10
    ]
    dbg.attach()
    # Set breakpoint at PC 3 ($Z0,3,1)
    resp = rsp.handle_packet("$Z0,3,1#45", bytecode)
    assert resp == "$OK#9a"
    assert 3 in dbg.breakpoints
    # Continue until breakpoint
    resp = rsp.handle_packet("$c#63", bytecode)
    assert resp == "$S05#b8"
    assert ctx.pc == 3
    assert ctx.stack[0] == 5  # counter
    assert interp.peek(ctx) == 5  # sum add result on stack top
    # Read registers ($g)
    resp = rsp.handle_packet("$g#67", bytecode)
    assert resp.startswith("$03000000")  # PC = 3 in hex little-endian


def test_debugger_integrated_profiler() -> None:
    ctx = ExecutionContext()
    interp = WASMInterpreter()
    dbg = DebuggerManager(ctx, interp)
    bytecode = [
        ("local.get", 0),
        ("i32.const", 1),
        ("i32.sub", None),
        ("local.set", 0),
        ("local.get", 0),
        ("br_if", 0),
        ("return", None),
    ]
    ctx.stack[0] = 10  # loop 10 times
    dbg.attach()
    dbg.continue_execution(bytecode)
    # Verify profiling counts ({Debug_Integrated})
    assert dbg.pc_sample_counts[0] == 10
    assert dbg.pc_sample_counts[5] == 10
    assert dbg.pc_sample_counts[6] == 1


if __name__ == "__main__":
    test_debugger_step_and_registers()
    test_debugger_breakpoint_and_continue()
    test_debugger_integrated_profiler()
    print("ALL DEBUGGER CONCEPT TESTS PASSED.")

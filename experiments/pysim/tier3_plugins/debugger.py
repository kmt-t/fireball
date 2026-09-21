"""
experiments/pysim/tier3_plugins/debugger.py
Debugger Manager & GDB RSP Protocol Engine for Fireball.
Conforms strictly to docs/components/tier2_runtime/debug_manager.md
and docs/specs/gdb_rsp_protocol.md.
Implements:
1. GDB RSP Minimal Command Set (?, g, G, m, M, Z0, z0, s, c) ({RSPMinimalSet})
2. Virtual Register Mapping (0:pc, 1:sp, 2:fp, 3:tos, 4..19:local0..15)
3. Breakpoint Management via sorted ReadOnlyFlatSetView semantics ({FlatViewNarrowing})
4. JIT Cache Invalidation on Memory Write ({Debugger_Jit_Flush})
5. Integrated Profiler (PC sampling frequency & memory assertions) ({Debug_Integrated})
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from typing import Protocol

from config import FB_CONF_DEBUG_MAX_ASSERTIONS, FB_CONF_DEBUG_MAX_BREAKPOINTS
from execution_context import WASMContext
from runtime_events import RuntimeEvent, RuntimeEventKind
from system_containers import MutableFlatMapStorage, ReadOnlyFlatMapView, StaticVector
from wasm_module import BasicBlock

# docs/components/tier1_core/system_config.md {Debug_Integrated}
# {META_NoStdVector}: max PC-sampling entries the profiler buffer holds.
FB_CONF_DEBUG_MAX_PC_SAMPLES = 64


class _DebuggerEngine(Protocol):
    """Tier 2 execution contract implemented by an injected runtime engine."""

    def attach_debugger(self, debugger: DebuggerManager) -> None: ...

    def detach_debugger(self) -> None: ...

    def flush_jit_cache(self) -> None: ...

    def run_block_interpret(self, block: BasicBlock, ctx: WASMContext) -> int | None: ...


class DebuggerManager:
    """Manages debug state, breakpoint sets, execution stepping, and integrated profiling."""

    def __init__(self, engine: _DebuggerEngine | None = None):
        self.engine = engine
        self.attached: bool = False
        self.halted: bool = False
        self.stop_signal: int = 5  # SIGTRAP (5)
        # Sorted breakpoint list (flat_set_view semantics with O(log N) binary search)
        self._breakpoints: StaticVector[int] = StaticVector(capacity=FB_CONF_DEBUG_MAX_BREAKPOINTS)
        # Integrated Profiler & Test Tool ({Debug_Integrated})
        self._pc_sample_storage: MutableFlatMapStorage[int, int] = MutableFlatMapStorage(
            capacity=FB_CONF_DEBUG_MAX_PC_SAMPLES
        )
        self.memory_assertions: StaticVector[tuple[int, int]] = StaticVector(
            capacity=FB_CONF_DEBUG_MAX_ASSERTIONS
        )
        self._assertion_violations: StaticVector[tuple[int, int, int]] = StaticVector(
            capacity=FB_CONF_DEBUG_MAX_ASSERTIONS
        )

    def attach(self) -> None:
        """Attaches debugger, halting execution and enabling interpreter debug handler table ({DebuggerLabelTableSwitch})."""
        self.attached = True
        self.halted = True
        self.stop_signal = 5
        if self.engine is not None:
            self.engine.attach_debugger(self)

    def detach(self) -> None:
        """Detaches debugger and restores normal zero-overhead execution."""
        self.attached = False
        self.halted = False
        if self.engine is not None:
            self.engine.detach_debugger()

    def add_breakpoint(self, pc: int) -> None:
        """Adds a breakpoint maintaining sorted order for flat_set_view O(log N) lookup."""
        idx = bisect.bisect_left(self._breakpoints, pc)
        if idx == len(self._breakpoints) or self._breakpoints[idx] != pc:
            assert self._breakpoints.insert_at(idx, pc)

    def remove_breakpoint(self, pc: int) -> None:
        """Removes a breakpoint if present."""
        idx = bisect.bisect_left(self._breakpoints, pc)
        if idx < len(self._breakpoints) and self._breakpoints[idx] == pc:
            self._breakpoints.pop_at(idx)

    def has_breakpoint(self, pc: int) -> bool:
        """O(log N) breakpoint existence check."""
        idx = bisect.bisect_left(self._breakpoints, pc)
        return idx < len(self._breakpoints) and self._breakpoints[idx] == pc

    def add_memory_assertion(self, addr: int, expected: int, desc: str = "") -> None:
        """Registers a dynamic memory assertion hook ({Debug_Integrated})."""
        self.memory_assertions.append((addr, expected))

    def sample_pc(self, pc: int) -> None:
        """Samples PC execution frequency ({Debug_Integrated})."""
        count = self._pc_sample_storage.view().find(pc)
        assert self._pc_sample_storage.insert(pc, 1 if count is None else count + 1)

    @property
    def pc_sample_counts(self) -> ReadOnlyFlatMapView[int, int]:
        """Returns a borrowed read-only view of the profiler's owned storage."""
        return self._pc_sample_storage.view()

    def verify_assertions(self, memory: bytearray | None) -> None:
        """Verifies memory assertions against current guest memory ({Debug_Integrated})."""
        if memory is None:
            return
        for addr, expected in self.memory_assertions:
            if addr < len(memory):
                val = memory[addr]
                if val != expected:
                    self._assertion_violations.append((addr, expected, val))

    @property
    def assertion_violations(self) -> StaticVector[str]:
        violations: StaticVector[str] = StaticVector(capacity=len(self._assertion_violations))
        for addr, expected, actual in self._assertion_violations:
            violations.append(f"ASSERTION_FAILED: addr 0x{addr:X} expected {expected} got {actual}")
        return violations

    def flush_jit_cache(self) -> None:
        """Invalidates all JIT cache banks when memory is rewritten by debugger ({Debugger_Jit_Flush})."""
        if self.engine is not None:
            self.engine.flush_jit_cache()

    def require_execution_engine(self) -> _DebuggerEngine:
        """Returns the injected execution engine; construction is an outer-layer responsibility."""
        assert self.engine is not None, "GDB execution requires an injected runtime engine"
        return self.engine

    def read_virtual_registers(self, pc: int, ctx: WASMContext) -> StaticVector[int]:
        """Returns 20 virtual registers: 0:pc, 1:sp, 2:fp, 3:tos, 4..19:local0..15."""
        sp = len(ctx.stack)
        fp = 0
        tos = ctx.stack[-1] if ctx.stack else 0
        regs: StaticVector[int] = StaticVector(capacity=20)
        for value in (pc, sp, fp, tos):
            regs.append(value)
        for i in range(16):
            regs.append(ctx.locals[i] if i < len(ctx.locals) else 0)
        return regs

    def write_virtual_registers(self, regs: Sequence[int], ctx: WASMContext) -> int:
        """Updates virtual registers from a 20-integer list. Returns new PC."""
        new_pc = regs[0] if len(regs) > 0 else 0
        # locals
        for i in range(16):
            if 4 + i < len(regs):
                ctx.locals[i] = regs[4 + i]
        return new_pc

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        """観測プラグイン契約。停止イベントだけをデバッガ状態へ反映する。"""
        if event.kind == RuntimeEventKind.DEBUG_STOP:
            self.halted = True
            self.stop_signal = 5


class GDBRspProtocol:
    """GDB Remote Serial Protocol (RSP) packet handler and dispatcher ({RSPMinimalSet})."""

    def __init__(self, dbg: DebuggerManager):
        self.dbg = dbg

    @staticmethod
    def calculate_checksum(payload: str) -> str:
        cksum = sum(ord(c) for c in payload) % 256
        return f"{cksum:02x}"

    @classmethod
    def format_packet(cls, payload: str) -> str:
        return f"${payload}#{cls.calculate_checksum(payload)}"

    def handle_packet(
        self,
        packet: str,
        current_pc: int,
        ctx: WASMContext,
        blocks: Mapping[int, BasicBlock],
    ) -> tuple[str, int]:
        """Handles an RSP packet payload and returns (response_packet, new_pc)."""
        # Strip framing if present
        raw = packet.strip()
        raw = raw.removeprefix("$")

        if raw.find("#") >= 0:
            raw = raw.split("#")[0]

        if not raw:
            return self.format_packet(""), current_pc
        cmd = raw[0]
        args = raw[1:]
        # ? - Query Halt Reason
        if cmd == "?":
            return self.format_packet(f"S{self.dbg.stop_signal:02x}"), current_pc
        # g - Read All Registers
        elif cmd == "g":
            regs = self.dbg.read_virtual_registers(current_pc, ctx)
            hex_payload = "".join(f"{r & 0xFFFF_FFFF:08x}" for r in regs)
            return self.format_packet(hex_payload), current_pc
        # G - Write All Registers
        elif cmd == "G":
            try:
                # 20 registers * 8 hex digits = 160 chars
                hex_data = args
                regs: StaticVector[int] = StaticVector(capacity=20)
                for i in range(0, len(hex_data), 8):
                    regs.append(int(hex_data[i : i + 8], 16))
                new_pc = self.dbg.write_virtual_registers(regs, ctx)
                return self.format_packet("OK"), new_pc
            except Exception:
                return self.format_packet("E01"), current_pc
        # m addr,len - Read Memory
        elif cmd == "m":
            try:
                addr_str, len_str = args.split(",")
                addr = int(addr_str, 16)
                length = int(len_str, 16)
                if ctx.memory is None or addr + length > len(ctx.memory):
                    return self.format_packet("E01"), current_pc
                mem_bytes = bytes(ctx.memory[addr : addr + length])
                return self.format_packet(mem_bytes.hex()), current_pc
            except Exception:
                return self.format_packet("E01"), current_pc
        # M addr,len:XX... - Write Memory & Flush JIT Cache ({Debugger_Jit_Flush})
        elif cmd == "M":
            try:
                header, hex_data = args.split(":")
                addr_str, len_str = header.split(",")
                addr = int(addr_str, 16)
                length = int(len_str, 16)
                data = bytes.fromhex(hex_data)
                if ctx.memory is None or addr + len(data) > len(ctx.memory) or len(data) != length:
                    return self.format_packet("E01"), current_pc
                ctx.memory[addr : addr + length] = data
                # Invalidate JIT cache on memory rewrite ({Debugger_Jit_Flush})
                self.dbg.flush_jit_cache()
                return self.format_packet("OK"), current_pc
            except Exception:
                return self.format_packet("E01"), current_pc
        # Z0,addr,kind - Insert Breakpoint
        elif cmd == "Z" and args.startswith("0,"):
            try:
                addr = int(args.split(",")[1], 16)
                self.dbg.add_breakpoint(addr)
                return self.format_packet("OK"), current_pc
            except Exception:
                return self.format_packet("E01"), current_pc
        # z0,addr,kind - Remove Breakpoint
        elif cmd == "z" and args.startswith("0,"):
            try:
                addr = int(args.split(",")[1], 16)
                self.dbg.remove_breakpoint(addr)
                return self.format_packet("OK"), current_pc
            except Exception:
                return self.format_packet("E01"), current_pc
        # s - Single Step Instruction
        elif cmd == "s":
            if blocks.get(current_pc) is None:
                return self.format_packet("W00"), current_pc
            self.dbg.sample_pc(current_pc)
            block = blocks[current_pc]
            # Execute single step via Interpreter
            engine = self.dbg.require_execution_engine()
            next_pc = engine.run_block_interpret(block, ctx)
            self.dbg.verify_assertions(ctx.memory)
            if next_pc is None:
                return self.format_packet("W00"), 0  # Process terminated
            self.dbg.stop_signal = 5
            return self.format_packet("S05"), next_pc
        # c - Continue Execution
        elif cmd == "c":
            engine = self.dbg.require_execution_engine()
            pc = current_pc
            while pc is not None:
                if self.dbg.has_breakpoint(pc) and pc != current_pc:
                    self.dbg.stop_signal = 5
                    return self.format_packet("S05"), pc
                if blocks.get(pc) is None:
                    return self.format_packet("W00"), pc
                self.dbg.sample_pc(pc)
                block = blocks[pc]
                # Run step in interpreter fallback mode
                pc = engine.run_block_interpret(block, ctx)
                self.dbg.verify_assertions(ctx.memory)
                if pc is not None and self.dbg.has_breakpoint(pc):
                    self.dbg.stop_signal = 5
                    return self.format_packet("S05"), pc
            return self.format_packet("W00"), 0
        # Unknown / Unsupported command
        return self.format_packet(""), current_pc

import inspect
import textwrap


def pytest_addoption(parser):
    parser.addoption("--refactor-mutant", default="none")


def pytest_collection_modifyitems(config, items):
    mode = config.getoption("--refactor-mutant")
    config._refactor_hits = [0]
    hits = config._refactor_hits
    if mode == "idle-unconditional":
        import scheduler

        source = textwrap.dedent(inspect.getsource(scheduler.Scheduler.run_until_idle))
        before = (
            "if not idle_hooks_called and not self._ready and len(self.interrupt_event_queue) == 0:"
        )
        assert source.count(before) == 1
        source = source.replace(before, "if not idle_hooks_called:")
        exec(compile(source, "<idle-regression>", "exec"), scheduler.__dict__)
        scheduler.Scheduler.run_until_idle = scheduler.run_until_idle
    elif mode == "control-trace-missing":
        from tier3_executer.jit.jit_manager import JITRuntimeManager

        original = JITRuntimeManager._compile_trace

        def compile_trace(self, pc, block):
            code = self.module.code_for(pc >> 16)
            end = (pc & 0xFFFF) + block.byte_span
            if end < len(code) and code[end] in (0x04, 0x0D, 0x0F):
                hits[0] += 1
                return None
            return original(self, pc, block)

        JITRuntimeManager._compile_trace = compile_trace
    elif mode == "control-trace-not-invoked":
        from tier3_executer.jit.jit_manager import JITRuntimeManager
        from tier3_executer.runtime_engine import EMPTY_NATIVE_DISPATCH_SNAPSHOT

        def dispatch_state(self):
            hits[0] += 1
            return EMPTY_NATIVE_DISPATCH_SNAPSHOT

        JITRuntimeManager.native_dispatch_state = dispatch_state
    elif mode == "chain-wrong-live-successor":
        from tier3_executer.jit.jit_cache import JITMultiBufferCache

        original = JITMultiBufferCache.insert

        def insert(self, trace):
            result = original(self, trace)
            for bank in (self.active, self.warm):
                for pc, source in bank.traces:
                    if source.chain_next is None:
                        continue
                    wrong = self.find_trace(pc + 0x20)
                    if wrong is not None and self.find_bank(wrong.head_pc) in (
                        self.active,
                        self.warm,
                    ):
                        logical_next = source.next_pc
                        source.next_pc = wrong.head_pc
                        self._link_chain(source, wrong)
                        source.next_pc = logical_next
                        hits[0] += 1
                        return result
            return result

        JITMultiBufferCache.insert = insert
    elif mode == "assembler-extra-align":
        from tier3_executer.jit import x64_asm

        original = x64_asm.and_rsp_imm8

        def align(value):
            hits[0] += 1
            return original(value) + x64_asm.sub_rsp_imm8(16)

        x64_asm.and_rsp_imm8 = align
    else:
        assert mode == "none"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    terminalreporter.write_line(
        f"REFACTOR mutant={config.getoption('--refactor-mutant')} hits={config._refactor_hits}"
    )

from __future__ import annotations

import inspect
import textwrap


def pytest_addoption(parser):
    parser.addoption("--review-mutant", default="none")


def pytest_sessionstart(session):
    if session.config.getoption("--review-mutant") == "jit-threshold-18":
        import jit_scoring

        jit_scoring.JIT_CANDIDATE_THRESHOLD = 18


def pytest_collection_modifyitems(config, items):
    mode = config.getoption("--review-mutant")
    if mode == "ipc-half-swap":
        from memory import SharedBlock

        original_write = SharedBlock.write_entry
        original_read = SharedBlock.read_entry
        calls = [0, 0]

        def write_entry(self, index, key, val):
            calls[0] += 1
            original_write(self, index, val, key)

        def read_entry(self, index):
            calls[1] += 1
            val, key = original_read(self, index)
            return key, val

        SharedBlock.write_entry = write_entry
        SharedBlock.read_entry = read_entry
        config._review_mutation_calls = calls
    elif mode == "recovery-extra-success":
        import recovery

        original = recovery.RecoveryManager.execute_with_recovery
        source = textwrap.dedent(inspect.getsource(original))
        before = "if res.is_ok:\n            return res"
        after = (
            "if res.is_ok:\n"
            "            if attempt < self.max_retries:\n"
            "                _review_early_success_hits[0] += 1\n"
            "                operation()\n"
            "            return res"
        )
        assert source.count(before) == 1
        calls = [0]
        recovery._review_early_success_hits = calls
        exec(
            compile(source.replace(before, after), "<review-recovery-mutant>", "exec"),
            recovery.__dict__,
        )
        recovery.RecoveryManager.execute_with_recovery = recovery.execute_with_recovery
        config._review_mutation_calls = calls
    elif mode == "virq-ignore-category-stop":
        import virq

        original = virq.VirqDispatcher.dispatch_interrupt_event
        source = textwrap.dedent(inspect.getsource(original))
        before = (
            "    if result == VirqDispatchResult.HANDLED:\n"
            "        return DispatchResult(result)\n"
            "    if result == VirqDispatchResult.REJECT:\n"
            "        return self._reject(VirqFaultCode.CATEGORY_REJECT)\n"
        )
        assert source.count(before) == 1
        after = "    _review_category_mutation_calls[0] += 1\n"
        calls = [0]
        virq._review_category_mutation_calls = calls
        exec(compile(source.replace(before, after), "<review-virq-mutant>", "exec"), virq.__dict__)
        virq.VirqDispatcher.dispatch_interrupt_event = virq.dispatch_interrupt_event
        config._review_mutation_calls = calls
    elif mode == "jit-threshold-18":
        from jit_scoring import JIT_CANDIDATE_THRESHOLD

        assert JIT_CANDIDATE_THRESHOLD == 18
        config._review_mutation_calls = [18]
    elif mode == "native-trap-no-log":
        from tier3_executer.interpreter import interpreter

        source = textwrap.dedent(inspect.getsource(interpreter.Interpreter._abort_call))
        before = "    if self.logger is not None:\n"
        assert source.count(before) == 1
        after = (
            "    if type(self).__name__ == 'NativeInterpreter':\n"
            "        _review_native_trap_calls[0] += 1\n"
            "    if self.logger is not None and type(self).__name__ != 'NativeInterpreter':\n"
        )
        calls = [0]
        interpreter._review_native_trap_calls = calls
        exec(
            compile(source.replace(before, after), "<review-native-log-mutant>", "exec"),
            interpreter.__dict__,
        )
        interpreter.Interpreter._abort_call = interpreter._abort_call
        config._review_mutation_calls = calls
    elif mode != "none":
        raise AssertionError(f"unknown mutant: {mode}")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    mode = config.getoption("--review-mutant")
    calls = getattr(config, "_review_mutation_calls", None)
    terminalreporter.write_line(f"REVIEW mutant={mode} mutation_calls={calls}")

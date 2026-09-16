"""Python-compatible entry point for the complete CPS interpreter."""

from __future__ import annotations

from types import ModuleType

try:
    import _interpreter_cps_native as _native_interpreter
except ImportError:
    _native_interpreter: ModuleType | None = None

if _native_interpreter is None:
    from interpreter import Interpreter, InterpreterBindings, _HANDLERS
    HANDLER_COUNT = sum(1 for handler in _HANDLERS if handler is not None)
else:
    import interpreter as _python_interpreter

    class Interpreter(_python_interpreter.Interpreter):
        """Use the direct .pyx CPS runner for every bytecode dispatch."""

        def _call_without_nested_calls(self, call_state):
            return _native_interpreter.run_cps_call(self, call_state)

        def _step(self, call_state, stop_at_boundary):
            return _native_interpreter.run_cps_step(
                self, call_state, stop_at_boundary
            )

    InterpreterBindings = _python_interpreter.InterpreterBindings
    HANDLER_COUNT = _native_interpreter.native_handler_count()

NATIVE_AVAILABLE = _native_interpreter is not None
BACKEND = "native" if NATIVE_AVAILABLE else "python"

# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False

"""Native CPS chain for the ordinary Python opcode handlers.

The opcode semantics remain in interpreter.py.  This module owns only the
typed C function-pointer chain and the two interpreter execution runners.
"""

import tier3_executer.interpreter.interpreter as _python_interpreter
import wasm_opcodes as _ops
from system_containers import StaticVector
from wasm_module import I32, I64, F32, F64
from tier3_executer.interpreter.interpreter import RETURN_SENTINEL_IP, _BASIC_BLOCK_BOUNDARY

CALL = getattr(_ops, "CALL")
CALL_INDIRECT = getattr(_ops, "CALL_INDIRECT")

ctypedef object (*handler_fn_t)(object, object, object, Py_ssize_t)
cdef handler_fn_t _CPS_HANDLER_TABLE[256]

cdef extern from *:
    """
    #include <Python.h>
    #if defined(__cplusplus)
    #define FIREBALL_MUSTTAIL [[clang::musttail]]
    #else
    #define FIREBALL_MUSTTAIL __attribute__((musttail))
    #endif
    typedef PyObject *(*fireball_cps_handler_fn)(
        PyObject *, PyObject *, PyObject *, Py_ssize_t
    );
    #if defined(__cplusplus)
    static thread_local fireball_cps_handler_fn fireball_cps_next;
    #else
    static _Thread_local fireball_cps_handler_fn fireball_cps_next;
    #endif
    static void fireball_cps_set_next(fireball_cps_handler_fn next) {
        fireball_cps_next = next;
    }
    static PyObject *fireball_cps_musttail_call(
        PyObject *ctx,
        PyObject *sp,
        PyObject *local_base,
        Py_ssize_t tos
    ) {
        FIREBALL_MUSTTAIL return fireball_cps_next(ctx, sp, local_base, tos);
    }
    """
    void _set_next "fireball_cps_set_next"(handler_fn_t next)
    object _musttail_call "fireball_cps_musttail_call"(
        object ctx, object sp, object local_base, Py_ssize_t tos
    )


cdef object _cps_continue(
    object ctx,
    object sp,
    object local_base,
    Py_ssize_t tos,
):
    cdef object frame = ctx.call_frame_stack[-1]
    cdef Py_ssize_t ip = ctx.native_context.ip
    cdef int opcode = frame.code[ip]
    cdef object handler = _python_interpreter._HANDLERS[opcode]
    if handler is None:
        return _python_interpreter.Trap(
            _python_interpreter.TrapCode.UNREACHABLE,
            opcode,
        )
    cdef object result = handler(ctx, sp, local_base, tos)
    if result is not None:
        return result
    if _BASIC_BLOCK_BOUNDARY[opcode] != 0:
        return None
    cdef Py_ssize_t next_ip = ctx.native_context.ip
    if next_ip < 0 or next_ip >= len(frame.code):
        return None
    cdef int next_opcode = frame.code[next_ip]
    if _BASIC_BLOCK_BOUNDARY[next_opcode] != 0:
        return None
    cdef Py_ssize_t next_tos = frame.values.raw_top() if frame.values else 0
    _set_next(_CPS_HANDLER_TABLE[next_opcode])
    return _musttail_call(ctx, sp, local_base, next_tos)


def native_handler_count() -> int:
    return sum(1 for handler in _python_interpreter._HANDLERS if handler is not None)


def native_cps_abi() -> str:
    return "handler_result(ctx, sp, local_base, tos)"


def run_cps_call(owner, call_state):
        """Complete a call without materializing CPS state at every instruction."""
        frame = call_state._frame
        locals_arr = call_state._locals
        assert frame is not None and locals_arr is not None
        ip = call_state._ip
        tos = call_state._tos
        while True:
            if ip == RETURN_SENTINEL_IP:
                break
            assert 0 <= ip < len(frame.code)
            opcode = frame.code[ip]
            call_state.context.bind_handler_state(ip, frame)
            if _BASIC_BLOCK_BOUNDARY[opcode] != 0:
                trap = _python_interpreter._HANDLERS[opcode](
                    call_state.context, frame.values, locals_arr, tos
                )
            else:
                trap = _CPS_HANDLER_TABLE[opcode](
                    call_state.context, frame.values, locals_arr, tos
                )
            if trap is not None:
                # `ip` is this loop's dispatch point, but a non-boundary opcode's
                # `_CPS_HANDLER_TABLE` entry musttail-chains through `_cps_continue`
                # for however many further instructions stay in the same basic
                # block, so the trap may have fired several instructions past `ip`.
                # `native_context.ip` is kept current by every chained handler
                # (GOTCHA-LOG-04), so it -- not the stale outer `ip` -- names the
                # actual trapping instruction.
                owner._abort_call(call_state, trap, int(call_state.context.native_context.ip))
                return None
            ip = int(call_state.context.native_context.ip)
            if ip >= len(frame.code):
                ip = RETURN_SENTINEL_IP

        func_type = owner.module.func_type(call_state.func_index)
        frame.frames.truncate(0)
        call_state.context.end_call_frame(frame)
        results: StaticVector[WasmNumber] = StaticVector(capacity=4)
        if func_type.results:
            result_type = func_type.results[0]
            if result_type == I64:
                result_value = frame.values.pop_i64()
            elif result_type == F32:
                result_value = frame.values.pop_f32()
            elif result_type == F64:
                result_value = frame.values.pop_f64()
            else:
                result_value = frame.values.pop_i32()
            results.append(result_value)
        call_state.cont = None
        call_state.finished = True
        call_state.results = results
        return results




def run_cps_step(owner, call_state, stop_at_boundary):
        """Run until a boundary or completion, depending on the driving caller."""
        while True:
            ip = call_state._ip
            frame = call_state._frame
            locals_arr = call_state._locals
            tos = call_state._tos
            assert frame is not None and locals_arr is not None
            if ip != RETURN_SENTINEL_IP:
                assert 0 <= ip < len(frame.code)
                op = frame.code[ip]
                if op == CALL or op == CALL_INDIRECT:
                    trap = owner._enter_or_resolve_call(
                        call_state, op, ip, frame, locals_arr, tos
                    )
                    if trap is not None:
                        owner._abort_call(call_state, trap, ip)
                        return call_state
                    if stop_at_boundary:
                        return call_state
                    continue
                is_boundary = _BASIC_BLOCK_BOUNDARY[op] != 0
                call_state.context.bind_handler_state(ip, frame)
                if is_boundary:
                    trap = _python_interpreter._HANDLERS[op](
                        call_state.context, frame.values, locals_arr, tos
                    )
                else:
                    trap = _CPS_HANDLER_TABLE[op](
                        call_state.context, frame.values, locals_arr, tos
                    )
                if trap is not None:
                    # See the matching comment in run_cps_call: a non-boundary
                    # opcode's handler may musttail-chain past `ip` before trapping,
                    # so read the trapping instruction from native_context.ip
                    # rather than this loop's now-stale dispatch point.
                    owner._abort_call(call_state, trap, int(call_state.context.native_context.ip))
                    return call_state
                next_ip = int(call_state.context.native_context.ip)
                result_frame = call_state.context.call_frame_stack[-1]
                assert result_frame is frame
                result_locals = locals_arr
                next_tos = frame.values.raw_top() if frame.values else 0
                if next_ip >= len(frame.code):
                    next_ip = RETURN_SENTINEL_IP
                call_state._ip = next_ip
                call_state._frame = result_frame
                call_state._locals = result_locals
                call_state._tos = next_tos
                if call_state._ip != RETURN_SENTINEL_IP:
                    if stop_at_boundary and is_boundary:
                        return call_state
                    continue
                # The explicit return sentinel ends this frame; nested calls
                # consume it here and restore the suspended caller below.  A
                # boundary-driven caller gets one observable sentinel step so
                # RuntimeEngine can perform its top-level completion check.
                if stop_at_boundary:
                    return call_state

            ft = owner.module.func_type(call_state.func_index)
            # The context-owned control-frame window is independent from the
            # LocalStack frame lifetime; discard any frames left by a JIT
            # boundary before releasing this call activation.
            frame.frames.truncate(0)
            call_state.context.end_call_frame(frame)
            if not call_state.call_stack:
                results: StaticVector[WasmNumber] = StaticVector(capacity=4)
                # The Native operand stack contains raw slots only. The
                # caller's function signature selects the typed read here;
                # no return type is stored in the result buffer.
                if ft.results:
                    result_type = ft.results[0]
                    if result_type == I64:
                        result_value = frame.values.pop_i64()
                    elif result_type == F32:
                        result_value = frame.values.pop_f32()
                    elif result_type == F64:
                        result_value = frame.values.pop_f64()
                    else:
                        result_value = frame.values.pop_i32()
                    assert result_value is not None
                    results.append(result_value)
                call_state.cont = None
                call_state.finished = True
                call_state.results = results
                return call_state
            # Return to the suspended caller frame -- always a mandatory
            # boundary, so the runtime gets the same chance to check its JIT
            # trace cache here as it does entering any other frame. The
            # operand stack is shared across this whole call chain (see
            # the result -- already sitting on the context-owned operand
            # stack -- needs no copy:
            # the caller's own next pop/push simply continues from here.
            parent_func_index, parent_cont = call_state.call_stack.pop_back()
            p_ip, p_frame, p_locals, _ = parent_cont
            call_state.func_index = parent_func_index
            call_state._ip = p_ip
            call_state._frame = p_frame
            call_state._locals = p_locals
            call_state._tos = 0
            if stop_at_boundary:
                return call_state




cdef void _initialize_cps_table() noexcept:
    cdef int opcode
    for opcode in range(256):
        if opcode < len(_python_interpreter._HANDLERS) and _python_interpreter._HANDLERS[opcode] is not None:
            _CPS_HANDLER_TABLE[opcode] = _cps_continue
        else:
            _CPS_HANDLER_TABLE[opcode] = NULL


_initialize_cps_table()

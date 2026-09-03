# cython: language_level=3, boundscheck=False, wraparound=False
"""
experiments/pysim/jit/native_trace_call.pyx
Optional native accelerator for RuntimeEngine._invoke_trace's hot path.

`ctypes.CFUNCTYPE` calls a compiled JITTrace through a libffi trampoline
(~1.1us/call measured, dominated by argument marshalling through libffi
rather than the trace body itself). This module instead casts the trace's
already-known entry address straight to a C function pointer and calls it,
matching x64_jit.py's CPS 4-argument convention exactly:
    int64_t (*)(uint32_t ip, void* stack_bot, void* local_base, uint32_t tos)

Built via build_native.ps1 / build_native.sh into native_trace_call.pyd /
.so alongside this file. RuntimeEngine imports it opportunistically -- when
absent (module not built), `_invoke_trace` falls back to the ctypes path,
so pysim's plain-Python regression suite runs unmodified either way.
"""

from libc.stdint cimport int64_t, uint32_t

ctypedef int64_t (*trace_fn_t)(uint32_t, void*, void*, uint32_t) noexcept nogil


def invoke_trace(
    unsigned long long fn_addr,
    unsigned int head_pc,
    unsigned long long local_base_addr,
    unsigned int tos,
):
    """Calls a compiled trace's native entry point directly as a C function pointer.

    `fn_addr` is the trace's raw entry address (`JITTrace.raw_addr`); R1
    (`stack_bot`) is always passed null, matching `_invoke_trace`'s ctypes
    path -- no currently-compilable op reads it.
    """
    cdef trace_fn_t fn = <trace_fn_t><void*><unsigned long long>fn_addr
    cdef void* local_base = <void*><unsigned long long>local_base_addr
    cdef int64_t res
    with nogil:
        res = fn(head_pc, <void*>0, local_base, tos)
    return res

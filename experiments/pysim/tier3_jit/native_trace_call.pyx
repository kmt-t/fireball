# cython: language_level=3, boundscheck=False, wraparound=False
"""
experiments/pysim/jit/native_trace_call.pyx
Optional native accelerator for RuntimeEngine._invoke_trace's hot path.

`ctypes.CFUNCTYPE` calls a compiled JITTrace through a libffi trampoline
(~1.1us/call measured, dominated by argument marshalling through libffi
rather than the trace body itself). This module instead casts the trace's
already-known entry address straight to a C function pointer and calls it,
matching x64_jit.py's CPS 4-argument convention exactly:
    void (*)(void* ctx, void* sp, void* local_base, uint32_t tos)

Built via build_native.ps1 / build_native.sh into native_trace_call.pyd /
.so alongside this file. RuntimeEngine imports it opportunistically -- when
absent (module not built), `_invoke_trace` falls back to the ctypes path,
so pysim's plain-Python regression suite runs unmodified either way.
"""

from libc.stdint cimport uint32_t, uint64_t, uintptr_t

ctypedef void (*trace_fn_t)(void*, void*, void*, uint32_t) noexcept nogil
ctypedef void (*helper_fn_t)(void*, uint32_t*, void*, uint32_t) noexcept nogil


cdef union _bits32:
    uint32_t u
    float f


cdef union _bits64:
    uint64_t u
    double f


cdef void _helper_i64_add(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef uint64_t result = (sp[0] | (<uint64_t>sp[1] << 32)) + (sp[2] | (<uint64_t>sp[3] << 32))
    sp[0] = <uint32_t>result
    sp[1] = <uint32_t>(result >> 32)


cdef void _helper_i64_sub(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef uint64_t result = (sp[0] | (<uint64_t>sp[1] << 32)) - (sp[2] | (<uint64_t>sp[3] << 32))
    sp[0] = <uint32_t>result
    sp[1] = <uint32_t>(result >> 32)


cdef void _helper_i64_mul(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef uint64_t result = (sp[0] | (<uint64_t>sp[1] << 32)) * (sp[2] | (<uint64_t>sp[3] << 32))
    sp[0] = <uint32_t>result
    sp[1] = <uint32_t>(result >> 32)


cdef void _helper_f32_add(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits32 a
    cdef _bits32 b
    cdef _bits32 result
    a.u = sp[0]
    b.u = sp[1]
    result.f = a.f + b.f
    sp[0] = result.u


cdef void _helper_f32_sub(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits32 a
    cdef _bits32 b
    cdef _bits32 result
    a.u = sp[0]
    b.u = sp[1]
    result.f = a.f - b.f
    sp[0] = result.u


cdef void _helper_f32_mul(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits32 a
    cdef _bits32 b
    cdef _bits32 result
    a.u = sp[0]
    b.u = sp[1]
    result.f = a.f * b.f
    sp[0] = result.u


cdef void _helper_f32_div(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits32 a
    cdef _bits32 b
    cdef _bits32 result
    a.u = sp[0]
    b.u = sp[1]
    result.f = a.f / b.f
    sp[0] = result.u


cdef void _helper_f64_add(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits64 a
    cdef _bits64 b
    cdef _bits64 result
    a.u = sp[0] | (<uint64_t>sp[1] << 32)
    b.u = sp[2] | (<uint64_t>sp[3] << 32)
    result.f = a.f + b.f
    sp[0] = <uint32_t>result.u
    sp[1] = <uint32_t>(result.u >> 32)


cdef void _helper_f64_sub(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits64 a
    cdef _bits64 b
    cdef _bits64 result
    a.u = sp[0] | (<uint64_t>sp[1] << 32)
    b.u = sp[2] | (<uint64_t>sp[3] << 32)
    result.f = a.f - b.f
    sp[0] = <uint32_t>result.u
    sp[1] = <uint32_t>(result.u >> 32)


cdef void _helper_f64_mul(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits64 a
    cdef _bits64 b
    cdef _bits64 result
    a.u = sp[0] | (<uint64_t>sp[1] << 32)
    b.u = sp[2] | (<uint64_t>sp[3] << 32)
    result.f = a.f * b.f
    sp[0] = <uint32_t>result.u
    sp[1] = <uint32_t>(result.u >> 32)


cdef void _helper_f64_div(
    void* _ctx, uint32_t* sp, void* _local_base, uint32_t _tos
) noexcept nogil:
    cdef _bits64 a
    cdef _bits64 b
    cdef _bits64 result
    a.u = sp[0] | (<uint64_t>sp[1] << 32)
    b.u = sp[2] | (<uint64_t>sp[3] << 32)
    result.f = a.f / b.f
    sp[0] = <uint32_t>result.u
    sp[1] = <uint32_t>(result.u >> 32)


cdef helper_fn_t _HELPERS[11]
_HELPERS[0] = _helper_i64_add
_HELPERS[1] = _helper_i64_sub
_HELPERS[2] = _helper_i64_mul
_HELPERS[3] = _helper_f32_add
_HELPERS[4] = _helper_f32_sub
_HELPERS[5] = _helper_f32_mul
_HELPERS[6] = _helper_f32_div
_HELPERS[7] = _helper_f64_add
_HELPERS[8] = _helper_f64_sub
_HELPERS[9] = _helper_f64_mul
_HELPERS[10] = _helper_f64_div


def helper_table_addresses():
    """Return the fixed built-in helper ABI table in slot order."""
    return tuple(
        <unsigned long long><uintptr_t>_HELPERS[index] for index in range(11)
    )


def invoke_trace(
    unsigned long long fn_addr,
    unsigned long long ctx_addr,
    unsigned long long sp_addr,
    unsigned long long local_base_addr,
    unsigned int tos,
):
    """Calls a compiled trace's native entry point directly as a C function pointer.

    `fn_addr` is the trace's raw entry address (`JITTrace.raw_addr`).
    `sp_addr` is the next raw slot in the shared operand stack: a trace with
    a residual value writes it there (via R12) instead of returning it.
    """
    cdef trace_fn_t fn = <trace_fn_t><void*><unsigned long long>fn_addr
    cdef void* ctx = <void*><unsigned long long>ctx_addr
    cdef void* sp = <void*><unsigned long long>sp_addr
    cdef void* local_base = <void*><unsigned long long>local_base_addr
    with nogil:
        fn(ctx, sp, local_base, tos)

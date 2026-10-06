#ifndef FIREBALL_PYSIM_JIT_RUNTIME_ABI_HXX
#define FIREBALL_PYSIM_JIT_RUNTIME_ABI_HXX

#include <cstddef>
#include <cstdint>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"
#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"

namespace fireball {
class JitRuntime;
} // namespace fireball

extern "C" {
FB_PYSIM_ABI_EXPORT std::size_t fb_jit_plugin_alignment();
FB_PYSIM_ABI_EXPORT std::size_t fb_jit_plugin_required_bytes(
    const fireball_wasm_module_execution_view_native *view);
FB_PYSIM_ABI_EXPORT const fb_native_execution_extension *fb_jit_plugin_init(
    std::uint8_t *region, std::size_t bytes,
    const fireball_wasm_module_execution_view_native *view,
    std::uint32_t module_id,
    int (*dispatch)(const fb_native_dispatch_call *, fb_native_result *));
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_run(const fb_native_dispatch_call *input,
                                           fb_native_result *result);
FB_PYSIM_ABI_EXPORT void
fb_jit_runtime_close(const fb_native_execution_extension *extension);
FB_PYSIM_ABI_EXPORT int
fb_jit_runtime_flush(const fb_native_execution_extension *extension);
FB_PYSIM_ABI_EXPORT int
fb_jit_runtime_compile(const fb_native_execution_extension *extension,
                       std::uint32_t budget);
}
#endif

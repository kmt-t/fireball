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
}  // namespace fireball

extern "C" {
FB_PYSIM_ABI_EXPORT std::size_t fb_jit_plugin_alignment();
FB_PYSIM_ABI_EXPORT std::size_t fb_jit_plugin_required_bytes(
    const fireball_wasm_module_execution_view_native* view);
FB_PYSIM_ABI_EXPORT fireball::JitRuntime* fb_jit_plugin_init(
    std::uint8_t* region, std::size_t bytes,
    const fireball_wasm_module_execution_view_native* view,
    std::uint32_t hotspots, std::uint32_t module_id,
    int (*dispatch)(const fb_native_dispatch_call*, fb_native_result*));
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_run(
    const fb_native_dispatch_call* input, fb_native_result* result);
FB_PYSIM_ABI_EXPORT void fb_jit_runtime_close(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_flush(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT void fb_jit_runtime_reset_counts(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_yield(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_compile(fireball::JitRuntime* cache, std::uint32_t budget);

// Internal initialization and arithmetic shared with the QA library.
FB_JIT_QA_EXPORT int fb_jit_runtime_bind_cards(
    fireball::JitRuntime* cache, std::uint8_t* states, std::uint32_t state_bytes,
    std::uint8_t* dirty, std::uint32_t dirty_bytes, std::uint32_t cards,
    std::uint32_t shift, std::uint32_t* cursor, std::uint32_t units, std::uint32_t scan_bytes);
FB_JIT_QA_EXPORT std::int64_t fb_jit_card_index(
    std::uint64_t bytes, std::uint32_t shift, std::uint32_t pc);
FB_JIT_QA_EXPORT std::int64_t fb_jit_card_count(std::uint64_t bytes, std::uint32_t shift);
FB_JIT_QA_EXPORT int fb_jit_runtime_bind_profile(
    fireball::JitRuntime* cache, fireball::jit_profile profile);
}
#endif

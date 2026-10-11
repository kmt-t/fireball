#include "../../experiments/pysim/native/tier3_plugins/jit/jit_types.hxx"
#include "../../experiments/pysim/tier2_runtime/abi/interpreter_abi.hxx"

#include <cstddef>
#include <cstdint>
#include <type_traits>

extern "C" bool FIREBALL_NATIVE_CPS_CALL
fb_abi_jit_trace_entry_probe(
    fireball_execution_context_native *, const std::uint8_t *, std::uint32_t *,
    const std::uint8_t *) noexcept {
  return false;
}

static_assert(std::is_same_v<decltype(&fb_abi_jit_trace_entry_probe),
                             fireball_native_jit_trace_entry>);

/* The generator compiles this file for the target ABI and extracts this
 * section, so layout values come from the production C++ declarations. */
#if defined(_WIN32)
#pragma section(".fb_abi", read)
extern "C" __declspec(allocate(".fb_abi")) const std::uint32_t
    fb_jit_abi_values[] = {
#else
extern "C" __attribute__((used, section(".fb_abi"))) const std::uint32_t
    fb_jit_abi_values[] = {
#endif
        static_cast<std::uint32_t>(offsetof(
            fireball_execution_context_native, local_base)),
        static_cast<std::uint32_t>(
            offsetof(fireball_execution_context_native, trap_code)),
        static_cast<std::uint32_t>(
            offsetof(fireball::jit_trace_header, chain_target_address)),
        static_cast<std::uint32_t>(
            offsetof(fireball::jit_trace_header, common_code_relative)),
        static_cast<std::uint32_t>(
            offsetof(fireball::jit_trace_header, const_rel_offset)),
        static_cast<std::uint32_t>(sizeof(fireball::jit_trace_header)),
        static_cast<std::uint32_t>(
            offsetof(fireball_execution_context_native, ip)),
        static_cast<std::uint32_t>(
            offsetof(fireball_execution_context_native, stack_pointer)),
        static_cast<std::uint32_t>(
            offsetof(fireball_execution_context_native, runtime_flags)),
        static_cast<std::uint32_t>(
            sizeof(fireball::jit_trace_boundary_metadata)),
        static_cast<std::uint32_t>(
            offsetof(fireball_execution_context_native, rel)),
};

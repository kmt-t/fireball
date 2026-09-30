#ifndef FIREBALL_PYSIM_TRACE_COMPILER_ABI_HXX
#define FIREBALL_PYSIM_TRACE_COMPILER_ABI_HXX

#include <cstdint>

#if defined(_WIN32)
#define FB_PYSIM_ABI_EXPORT __declspec(dllexport)
#else
#define FB_PYSIM_ABI_EXPORT __attribute__((visibility("default")))
#endif

extern "C" {

struct fb_jit_instruction {
  std::uint32_t opcode;
  std::uint32_t has_operand;
  std::uint64_t operand;
};

struct fb_jit_compile_result {
  std::uint32_t body_bytes;
  std::int32_t helper_index;
  std::uint32_t helper_words;
  std::uint32_t max_spilled_words;
  std::uint32_t stack_location_count;
  std::int32_t helper_header_patch_offset;
  std::int32_t helper_exit_patch_offset;
  std::int32_t exit_patch_offset;
  std::int32_t chain_dispatch_patch_offset;
};

// Returns 1 for a compiled trace, 0 when the trace is unsupported, and a
// negative value when the call or output buffer violates the ABI contract.
FB_PYSIM_ABI_EXPORT int fb_jit_compile_trace(
    const fb_jit_instruction* instructions, std::uint32_t instruction_count,
    std::uint32_t has_next_pc, std::uint32_t next_pc,
    std::uint32_t has_loops_to, std::uint32_t loops_to,
    std::uint32_t byte_span, const std::uint8_t* local_widths,
    std::uint32_t local_width_bytes, std::uint32_t local_count,
    std::uint32_t slot_words, std::uint32_t tail_context_helper,
    std::uintptr_t helper_target, std::uint8_t* output,
    std::uint32_t output_capacity, fb_jit_compile_result* result);

FB_PYSIM_ABI_EXPORT void fb_jit_common_chain_dispatcher(
    const std::uint8_t** bytes, std::uint32_t* byte_count,
    std::uint32_t* offset);

}

#endif

#ifndef FIREBALL_PYSIM_TRACE_COMPILER_ABI_HXX
#define FIREBALL_PYSIM_TRACE_COMPILER_ABI_HXX

#include <cstdint>

#include "jit_types.hxx"

#if defined(_WIN32)
#define FB_PYSIM_ABI_EXPORT __declspec(dllexport)
#else
#define FB_PYSIM_ABI_EXPORT __attribute__((visibility("default")))
#endif

namespace fireball {

struct jit_instruction {
  std::uint32_t opcode;
  std::uint32_t has_operand;
  std::uint64_t operand;
};

} // namespace fireball

extern "C" {
FB_JIT_QA_EXPORT int fb_jit_compile_instructions(
    const fireball::jit_instruction_block *block, std::uint8_t *output,
    std::uint32_t capacity, std::uint8_t *body_scratch,
    std::int16_t *stack_locations, std::uint32_t stack_location_capacity);
// Return only the cache-ready code blob; trace state is kept in its header.
FB_JIT_QA_EXPORT int
fb_jit_compile_block(const fireball::jit_wasm_block *block,
                     std::uint8_t *output, std::uint32_t capacity,
                     std::uint8_t *body_scratch, std::int16_t *stack_locations,
                     std::uint32_t stack_location_capacity);
}

#endif

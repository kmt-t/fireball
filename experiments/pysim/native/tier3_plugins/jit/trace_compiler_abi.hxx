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

struct jit_const_pool_diagnostics {
  std::uint64_t trace_count;
  std::uint64_t instruction_count;
  std::uint64_t runtime_api_count;
  std::uint64_t runtime_api_opcodes[512];
  std::uint64_t item_bytes[7];
};

} // namespace fireball

extern "C" {
int fb_jit_compile_instructions(const fireball::jit_instruction_block *block, std::uint8_t *output,
                                std::uint32_t capacity, std::uint8_t *body_scratch);
// Return only the cache-ready code blob; trace state is kept in its header.
int fb_jit_compile_block(const fireball::jit_wasm_block *block, std::uint8_t *output,
                         std::uint32_t capacity, std::uint8_t *body_scratch);
void fb_jit_const_pool_diagnostics_reset();
void fb_jit_const_pool_diagnostics_read(fireball::jit_const_pool_diagnostics *output);
}

#endif

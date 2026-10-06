#ifndef FIREBALL_PYSIM_JIT_TYPES_HXX
#define FIREBALL_PYSIM_JIT_TYPES_HXX
#include <cstddef>
#include <cstdint>
#if defined(FB_PYSIM_QA)
#if defined(_WIN32)
#define FB_JIT_QA_EXPORT __declspec(dllexport)
#else
#define FB_JIT_QA_EXPORT __attribute__((visibility("default")))
#endif
#else
#define FB_JIT_QA_EXPORT
#endif
namespace fireball {
struct jit_instruction;

struct jit_byte_view {
  const std::uint8_t *data;
  std::uint32_t bytes;
};
struct jit_local_layout {
  jit_byte_view widths;
  std::uint32_t count, slot_words;
};
struct jit_wasm_block {
  std::uint32_t head_pc, offset, byte_span, next_pc, loops_to, frame_depth;
  std::int64_t jit_score;
  jit_byte_view code;
  jit_local_layout locals;
  void *extension_data;
};
struct jit_instruction_block {
  std::uint32_t head_pc, next_pc, loops_to, byte_span, context_helper;
  std::uint64_t helper_target;
  const fireball::jit_instruction *instructions;
  std::uint32_t instruction_count;
  jit_local_layout locals;
};
struct jit_common_layout {
  std::uint32_t region_bytes, common_code_bytes;
  std::uint32_t prologue_offset, prologue_size, epilogue_offset, epilogue_size;
  std::uint32_t helper_offset, helper_size, chain_dispatcher_offset,
      chain_dispatcher_size;
  std::uint32_t absolute_pool_offset, absolute_pool_size;
  std::uint32_t context_helper_offset, header_bytes, entry_stub_bytes;
};
// The only per-trace runtime data lives beside its code. Runtime indexes keep
// a PC and a region-relative code offset; they never own a second descriptor.
struct jit_trace_header {
  std::uint64_t chain_target_address;
  std::uint64_t helper_target_address;
  std::int32_t common_code_relative;
  std::uint32_t head_pc;
  std::uint32_t blob_bytes;
  std::uint8_t stack_words;
  std::uint8_t result_words;
  std::uint8_t has_return_value;
  std::uint8_t chain_words;
  std::uint32_t frame_depth;
};
static_assert(offsetof(jit_trace_header, frame_depth) == 32);
static_assert(sizeof(jit_trace_header) == 40);

struct jit_profile {
  std::uint8_t *trackable;
  std::uint32_t mask_bytes, module_id, enabled, candidate_threshold,
      min_trace_bytes;
  std::uint64_t code_bytes;
  std::uint32_t history_capacity, queue_capacity;
};

} // namespace fireball
#endif

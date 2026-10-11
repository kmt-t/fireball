#ifndef FIREBALL_PYSIM_JIT_TYPES_HXX
#define FIREBALL_PYSIM_JIT_TYPES_HXX
#include <cstddef>
#include <cstdint>

#include "../../../tier2_runtime/abi/native_abi.hxx"

namespace fireball {
struct jit_instruction;

struct jit_byte_view {
  const std::uint8_t *data;
  std::uint32_t bytes;
};
struct jit_local_layout {
  jit_byte_view offsets;
  jit_byte_view sizes;
  jit_byte_view types;
  std::uint32_t count, total_words;
};
struct jit_wasm_block {
  std::uint32_t head_pc, offset, byte_span, next_pc, loops_to, frame_depth;
  std::int64_t jit_score;
  jit_byte_view code;
  jit_local_layout locals;
  void *extension_data;
  std::uint32_t block_index = 0xFFFF'FFFFu;
};
struct jit_instruction_block {
  std::uint32_t head_pc, next_pc, loops_to, byte_span;
  const fireball::jit_instruction *instructions;
  std::uint32_t instruction_count;
  jit_local_layout locals;
};
using jit_trace_boundary_metadata = fb_native_jit_trace_exit;
static_assert(offsetof(jit_trace_boundary_metadata, body_end_ip) == 0);
static_assert(offsetof(jit_trace_boundary_metadata, block_index) == 16);
static_assert(offsetof(jit_trace_boundary_metadata, frame_depth) == 20);
static_assert(offsetof(jit_trace_boundary_metadata, result_words) == 22);
struct jit_common_layout {
  std::uint32_t region_bytes, common_code_bytes;
  std::uint32_t chain_dispatcher_offset, chain_dispatcher_size;
  std::uint32_t header_bytes;
};
// The only per-trace runtime data lives beside its code. Runtime indexes keep
// a PC and a region-relative code offset; they never own a second descriptor.
struct jit_trace_header {
  std::uint64_t chain_target_address;
  std::int32_t common_code_relative;
  std::uint32_t head_pc;
  std::uint32_t blob_bytes;
  std::uint8_t stack_words;
  std::uint8_t result_words;
  std::uint8_t chain_words;
  std::uint8_t chain_body_count;
  std::uint32_t frame_depth;
  std::uint32_t block_index;
  std::uint32_t const_rel_offset;
};
static_assert(offsetof(jit_trace_header, common_code_relative) == 8);
static_assert(offsetof(jit_trace_header, head_pc) == 12);
static_assert(offsetof(jit_trace_header, blob_bytes) == 16);
static_assert(offsetof(jit_trace_header, stack_words) == 20);
static_assert(offsetof(jit_trace_header, result_words) == 21);
static_assert(offsetof(jit_trace_header, chain_words) == 22);
static_assert(offsetof(jit_trace_header, chain_body_count) == 23);
static_assert(offsetof(jit_trace_header, frame_depth) == 24);
static_assert(offsetof(jit_trace_header, block_index) == 28);
static_assert(offsetof(jit_trace_header, const_rel_offset) == 32);
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

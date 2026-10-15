#ifndef FIREBALL_PYSIM_JIT_TYPES_HXX
#define FIREBALL_PYSIM_JIT_TYPES_HXX
#include <cstddef>
#include <cstdint>

#include "../../../tier2_runtime/abi/native_abi.hxx"

namespace fireball {
struct jit_instruction;

struct jit_byte_view {
  const std::uint8_t *data;
  code_offset_t bytes;
};
struct jit_local_layout {
  jit_byte_view offsets;
  jit_byte_view types;
  local_count_t count, total_words;
};
struct jit_wasm_block {
  wasm_pc_t head_pc;
  code_offset_t offset, byte_span;
  wasm_pc_t next_pc, loops_to;
  frame_depth_t frame_depth;
  std::int64_t jit_score;
  jit_byte_view code;
  jit_local_layout locals;
  void *extension_data;
  block_index_t block_index = FIREBALL_INVALID_BLOCK_INDEX;
  block_index_t next_block_index = FIREBALL_INVALID_BLOCK_INDEX;
  block_index_t loops_to_block_index = FIREBALL_INVALID_BLOCK_INDEX;
  jit_byte_view interpreter_annotations;
  annotation_offset_t interpreter_annotation_byte_offset = 0;
};
static_assert(sizeof(jit_local_layout) == 40);
static_assert(offsetof(jit_wasm_block, jit_score) == 24);
static_assert(offsetof(jit_wasm_block, code) == 32);
static_assert(offsetof(jit_wasm_block, locals) == 48);
static_assert(offsetof(jit_wasm_block, extension_data) == 88);
static_assert(offsetof(jit_wasm_block, block_index) == 96);
static_assert(offsetof(jit_wasm_block, interpreter_annotations) == 104);
static_assert(offsetof(jit_wasm_block, interpreter_annotation_byte_offset) == 120);
static_assert(sizeof(jit_wasm_block) == 128);
struct jit_instruction_block {
  wasm_pc_t head_pc, next_pc, loops_to;
  code_offset_t byte_span;
  const fireball::jit_instruction *instructions;
  jit_body_count_t instruction_count;
  jit_local_layout locals;
};
using jit_trace_boundary_metadata = fb_native_jit_trace_exit;
static_assert(offsetof(jit_trace_boundary_metadata, body_end_ip) == 0);
static_assert(offsetof(jit_trace_boundary_metadata, block_index) == 16);
static_assert(offsetof(jit_trace_boundary_metadata, frame_depth) == 18);
static_assert(offsetof(jit_trace_boundary_metadata, result_words) == 19);
static_assert(sizeof(jit_trace_boundary_metadata) == 20);
struct jit_common_layout {
  code_offset_t region_bytes, common_code_bytes;
  code_offset_t chain_dispatcher_offset, chain_dispatcher_size;
  code_offset_t header_bytes;
};
enum class jit_trace_exit_kind : std::uint8_t {
  normal = 0,
  call_cps = 1,
};
// The only per-trace runtime data lives beside its code. Runtime indexes keep
// a PC and a region-relative code offset; they never own a second descriptor.
struct jit_trace_header {
  std::uint64_t chain_target_address;
  std::int32_t common_code_relative;
  wasm_pc_t head_pc;
  code_offset_t blob_bytes;
  jit_stack_words_t stack_words;
  jit_stack_words_t result_words;
  jit_stack_words_t chain_words;
  jit_body_count_t chain_body_count;
  frame_depth_t frame_depth;
  jit_trace_exit_kind exit_kind;
  block_index_t block_index;
  code_offset_t const_rel_offset;
};
static_assert(offsetof(jit_trace_header, common_code_relative) == 8);
static_assert(offsetof(jit_trace_header, head_pc) == 12);
static_assert(offsetof(jit_trace_header, blob_bytes) == 16);
static_assert(offsetof(jit_trace_header, stack_words) == 20);
static_assert(offsetof(jit_trace_header, result_words) == 21);
static_assert(offsetof(jit_trace_header, chain_words) == 22);
static_assert(offsetof(jit_trace_header, chain_body_count) == 23);
static_assert(offsetof(jit_trace_header, frame_depth) == 24);
static_assert(offsetof(jit_trace_header, exit_kind) == 25);
static_assert(offsetof(jit_trace_header, block_index) == 26);
static_assert(offsetof(jit_trace_header, const_rel_offset) == 28);
static_assert(sizeof(jit_trace_header) == 32);

struct jit_profile {
  std::uint8_t *trackable;
  std::uint32_t mask_bytes, module_id, enabled, candidate_threshold, min_trace_bytes;
  std::uint64_t code_bytes;
  std::uint32_t history_capacity, queue_capacity;
};

} // namespace fireball
#endif

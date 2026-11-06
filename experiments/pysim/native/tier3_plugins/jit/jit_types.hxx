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
  jit_byte_view code;
  jit_local_layout locals;
  block_index_t block_index = FIREBALL_INVALID_BLOCK_INDEX;
  block_index_t next_block_index = FIREBALL_INVALID_BLOCK_INDEX;
  block_index_t loops_to_block_index = FIREBALL_INVALID_BLOCK_INDEX;
  jit_byte_view interpreter_annotations;
  annotation_offset_t interpreter_annotation_byte_offset = 0;
  // Operand stack words when control reaches this block (fixed by WASM validation).
  std::uint16_t entry_stack_words = 0;
  // Module global value widths in words, one byte per global; bytes is the global count.
  jit_byte_view global_widths;
};
static_assert(sizeof(jit_local_layout) == 40);
static_assert(offsetof(jit_wasm_block, code) == 24);
static_assert(offsetof(jit_wasm_block, locals) == 40);
static_assert(offsetof(jit_wasm_block, block_index) == 80);
static_assert(offsetof(jit_wasm_block, interpreter_annotations) == 88);
static_assert(offsetof(jit_wasm_block, interpreter_annotation_byte_offset) == 104);
static_assert(offsetof(jit_wasm_block, entry_stack_words) == 108);
static_assert(offsetof(jit_wasm_block, global_widths) == 112);
static_assert(sizeof(jit_wasm_block) == 128);
using jit_trace_boundary_metadata = fb_native_jit_trace_exit;
static_assert(offsetof(jit_trace_boundary_metadata, body_end_ip) == 0);
static_assert(offsetof(jit_trace_boundary_metadata, block_index) == 16);
static_assert(offsetof(jit_trace_boundary_metadata, frame_depth) == 18);
static_assert(offsetof(jit_trace_boundary_metadata, interpreter_terminator_pending) == 19);
static_assert(sizeof(jit_trace_boundary_metadata) == 20);
struct jit_common_layout {
  code_offset_t region_bytes, common_code_bytes;
  code_offset_t chain_dispatcher_offset, chain_dispatcher_size;
  code_offset_t header_bytes;
};
// The only per-trace runtime data lives beside its code. Runtime indexes keep
// a PC and a region-relative code offset; they never own a second descriptor.
// The trace header is the ABI struct the Interpreter reads after a lookup hit.
using jit_trace_header = fb_native_jit_trace_header;

struct jit_profile {
  std::uint8_t *trackable;
  std::uint32_t mask_bytes, module_id, candidate_threshold, min_trace_bytes;
  std::uint64_t code_bytes;
  std::uint32_t history_capacity, queue_capacity;
};

} // namespace fireball
#endif

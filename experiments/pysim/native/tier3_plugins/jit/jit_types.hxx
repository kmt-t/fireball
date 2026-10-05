#ifndef FIREBALL_PYSIM_JIT_TYPES_HXX
#define FIREBALL_PYSIM_JIT_TYPES_HXX
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
  const std::uint8_t* data;
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
};
struct jit_instruction_block {
  std::uint32_t head_pc, next_pc, loops_to, byte_span, context_helper;
  std::uint64_t helper_target;
  const fireball::jit_instruction* instructions;
  std::uint32_t instruction_count;
  jit_local_layout locals;
};
struct jit_common_layout {
  std::uint32_t region_bytes, common_code_bytes;
  std::uint32_t prologue_offset, prologue_size, epilogue_offset, epilogue_size;
  std::uint32_t helper_offset, helper_size, chain_dispatcher_offset, chain_dispatcher_size;
  std::uint32_t absolute_pool_offset, absolute_pool_size;
  std::uint32_t context_helper_offset, header_bytes, entry_stub_bytes;
};
struct jit_trace_fixups {
  std::int32_t entry_body, entry_prologue, exit, helper_header, helper_exit, chain_dispatch;
  std::uint32_t helper_offset;
};
// Borrowed descriptors stay at stable addresses while resident. Mutable fields
// have one owner: the Tier 3 native cache, including during promotion.
struct jit_cache_trace {
  std::uint32_t head_pc;
  std::uint32_t size_bytes;
  std::uint32_t next_pc;
  std::uint32_t loops_to;
  std::uint32_t has_return_value;
  std::uint32_t result_words;
  std::uint32_t stack_words;
  std::uint32_t byte_span;
  std::uint32_t frame_depth;
  std::uint32_t dispatch_next_pc;
  std::uint32_t dispatch_loops_to;
  std::uint32_t code_offset;
  std::uint32_t chain_next_pc;
  std::uintptr_t entry_address;
  std::uint64_t chain_target_address;
  const std::uint8_t* code_blob;
  std::uint32_t blob_bytes;
  jit_trace_fixups fixups;
  jit_cache_trace* chain_terminal;
  std::uint32_t chain_words;
  std::uint32_t chain_bodies;
};

struct jit_profile {
  std::uint8_t* trackable;
  std::uint32_t mask_bytes, module_id, enabled, candidate_threshold, min_trace_bytes;
  std::uint64_t code_bytes;
  std::uint32_t history_capacity, queue_capacity, compiler_enabled;
  const jit_wasm_block* blocks;
  std::uint32_t block_count;
};

}  // namespace fireball
#endif

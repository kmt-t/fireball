#ifndef FIREBALL_PYSIM_JIT_INTERNAL_HXX
#define FIREBALL_PYSIM_JIT_INTERNAL_HXX
#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"
namespace fireball {
inline constexpr std::uint32_t kMaxTraceInstructions = 64;
inline constexpr std::uint32_t kMaxTraceOperations = kMaxTraceInstructions + 1;
inline constexpr std::uint32_t kMaxStackDepth = FIREBALL_NATIVE_VALUE_STACK_CAPACITY;
inline constexpr std::uint32_t kMaxRelocationConstants = 8 * kMaxTraceInstructions + 1;
inline constexpr std::uint32_t kMaxBodyBytes = 1536;
inline constexpr std::uint32_t kTraceBoundaryMetadataBytes = sizeof(jit_trace_boundary_metadata);

struct jit_compile_result {
  code_offset_t body_bytes;
  jit_stack_words_t max_stack_words;
  jit_stack_words_t result_words;
  code_offset_t relocation_bytes;
  annotation_offset_t interpreter_rel_bytes;
};

// Returns 1 for a compiled trace, 0 when the trace is unsupported, and a
// negative value when the call or output buffer violates the ABI contract.
int compile_instruction_body(const fireball::jit_instruction *instructions,
                             std::uint32_t instruction_count, std::uint32_t has_next_pc,
                             wasm_pc_t next_pc, std::uint32_t has_loops_to, wasm_pc_t loops_to,
                             code_offset_t byte_span, const frame_word_offset_t *local_offsets,
                             const std::uint8_t *local_types, local_count_t local_count,
                             local_count_t local_slot_count, std::uint8_t *output,
                             code_offset_t output_capacity, fireball::jit_compile_result *result);

int compile_wasm_trace(const std::uint8_t *code, code_offset_t code_bytes,
                       code_offset_t code_offset, code_offset_t byte_span,
                       std::uint32_t has_next_pc, wasm_pc_t next_pc, std::uint32_t has_loops_to,
                       wasm_pc_t loops_to, const std::uint8_t *interpreter_annotations,
                       annotation_offset_t interpreter_annotation_byte_count,
                       annotation_offset_t interpreter_annotation_byte_offset,
                       const frame_word_offset_t *local_offsets, const std::uint8_t *local_types,
                       local_count_t local_count, local_count_t local_slot_count,
                       std::uint8_t *output, code_offset_t output_capacity,
                       fireball::jit_compile_result *result);
std::int64_t chain_successor(std::uintptr_t address, code_offset_t bytes, code_offset_t offset,
                             code_offset_t span, wasm_pc_t next_pc);
int initialize_common_code(executable_memory *memory);
int patch_trace_chain(executable_memory *memory, code_offset_t offset, std::uint64_t target);
int patch_trace_alternate_chain(executable_memory *memory, code_offset_t offset,
                                std::uint64_t target);
int build_trace(const std::uint8_t *body, code_offset_t body_bytes,
                const fireball::jit_compile_result *result, wasm_pc_t head_pc,
                frame_depth_t frame_depth, block_index_t block_index, code_ip_t body_end_ip,
                wasm_pc_t next_pc, wasm_pc_t loops_to, block_index_t next_block_index,
                std::uint8_t *output, code_offset_t capacity);
} // namespace fireball
#endif

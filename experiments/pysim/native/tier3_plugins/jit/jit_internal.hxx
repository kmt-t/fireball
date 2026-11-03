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
// A runtime API operation writes four fixed relocations (slot, immediate count, immediate bytes,
// source IP), one per immediate, and the interpreter resume offset. A CPS call writes five.
inline constexpr std::uint32_t kMaxRuntimeApiImmediates = 3;
inline constexpr std::uint32_t kMaxRelocationsPerOperation = 4 + kMaxRuntimeApiImmediates + 1;
static_assert(kMaxRelocationsPerOperation >= 5);
// One trace header reference, then at most one operation per table entry.
inline constexpr std::uint32_t kMaxRelocationConstants =
    1 + kMaxTraceOperations * kMaxRelocationsPerOperation;
inline constexpr std::uint32_t kMaxBodyBytes = 1536;
inline constexpr std::uint32_t kTraceBoundaryMetadataBytes = sizeof(jit_trace_boundary_metadata);
inline constexpr code_ip_t kNoTraceExitIp = UINT32_MAX;

struct jit_compile_result {
  code_offset_t body_bytes;
  jit_stack_words_t max_stack_words;
  code_offset_t relocation_bytes;
  annotation_offset_t interpreter_rel_bytes;
  code_ip_t body_end_ip = kNoTraceExitIp;
  jit_trace_exit_kind exit_kind = jit_trace_exit_kind::normal;
};

int compile_wasm_trace(const std::uint8_t *code, code_offset_t code_bytes,
                       code_offset_t code_offset, code_offset_t byte_span,
                       std::uint32_t has_next_pc, wasm_pc_t next_pc, std::uint32_t has_loops_to,
                       wasm_pc_t loops_to, const std::uint8_t *interpreter_annotations,
                       annotation_offset_t interpreter_annotation_byte_count,
                       annotation_offset_t interpreter_annotation_byte_offset,
                       const jit_byte_view &global_widths, jit_stack_words_t entry_stack_words,
                       const frame_word_offset_t *local_offsets, const std::uint8_t *local_types,
                       local_count_t local_count, local_count_t local_slot_count,
                       std::uint8_t *output, code_offset_t output_capacity,
                       fireball::jit_compile_result *result);
std::int64_t chain_successor(std::uintptr_t address, code_offset_t bytes, code_offset_t offset,
                             code_offset_t span, wasm_pc_t next_pc);
int initialize_common_code(executable_memory *memory, const void *function_table);
int patch_trace_chain(executable_memory *memory, code_offset_t offset, std::uint64_t target);
int build_trace(const std::uint8_t *body, code_offset_t body_bytes,
                const fireball::jit_compile_result *result, wasm_pc_t head_pc,
                frame_depth_t frame_depth, block_index_t block_index, code_ip_t body_end_ip,
                wasm_pc_t next_pc, wasm_pc_t loops_to, block_index_t next_block_index,
                std::uint8_t *output, code_offset_t capacity);
} // namespace fireball
#endif

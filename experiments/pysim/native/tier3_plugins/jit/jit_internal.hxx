#ifndef FIREBALL_PYSIM_JIT_INTERNAL_HXX
#define FIREBALL_PYSIM_JIT_INTERNAL_HXX
#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"
namespace fireball {
inline constexpr std::uint32_t kMaxTraceInstructions = 64;
inline constexpr std::uint32_t kMaxTraceOperations = kMaxTraceInstructions + 1;
inline constexpr std::uint32_t kMaxStackDepth =
    FIREBALL_NATIVE_VALUE_STACK_CAPACITY;
inline constexpr std::uint32_t kMaxRelocationConstants =
    8 * kMaxTraceInstructions + 1;
inline constexpr std::uint32_t kMaxBodyBytes = 1536;
inline constexpr std::uint32_t kTraceBoundaryMetadataBytes =
    sizeof(jit_trace_boundary_metadata);

struct jit_compile_result {
  std::uint32_t body_bytes;
  std::uint32_t max_stack_words;
  std::uint32_t result_words;
  std::uint32_t relocation_bytes;
  std::uint32_t interpreter_rel_bytes;
};

// Returns 1 for a compiled trace, 0 when the trace is unsupported, and a
// negative value when the call or output buffer violates the ABI contract.
int compile_instruction_body(
    const fireball::jit_instruction *instructions,
    std::uint32_t instruction_count, std::uint32_t has_next_pc,
    std::uint32_t next_pc, std::uint32_t has_loops_to, std::uint32_t loops_to,
    std::uint32_t byte_span, const std::uint16_t *local_offsets,
    const std::uint8_t *local_sizes, const std::uint8_t *local_types,
    std::uint32_t local_count, std::uint32_t local_slot_count,
    std::uint8_t *output, std::uint32_t output_capacity,
    fireball::jit_compile_result *result);

int compile_wasm_trace(const std::uint8_t *code, std::uint32_t code_bytes,
                       std::uint32_t code_offset, std::uint32_t byte_span,
                       std::uint32_t has_next_pc, std::uint32_t next_pc,
                       std::uint32_t has_loops_to, std::uint32_t loops_to,
                       const std::uint16_t *local_offsets,
                       const std::uint8_t *local_sizes,
                       const std::uint8_t *local_types,
                       std::uint32_t local_count,
                       std::uint32_t local_slot_count, std::uint8_t *output,
                       std::uint32_t output_capacity,
                       fireball::jit_compile_result *result);
std::int64_t chain_successor(std::uintptr_t address, std::uint32_t bytes,
                             std::uint32_t offset, std::uint32_t span,
                             std::uint32_t next_pc);
int initialize_common_code(executable_memory *memory);
int patch_trace_chain(executable_memory *memory, std::uint32_t offset,
                      std::uint64_t target);
int build_trace(const std::uint8_t *body, std::uint32_t body_bytes,
                const fireball::jit_compile_result *result,
                std::uint32_t head_pc, std::uint32_t frame_depth,
                std::uint32_t block_index, std::uint32_t body_end_ip,
                std::uint32_t next_pc, std::uint32_t loops_to,
                std::uint8_t *output, std::uint32_t capacity);
} // namespace fireball
#endif

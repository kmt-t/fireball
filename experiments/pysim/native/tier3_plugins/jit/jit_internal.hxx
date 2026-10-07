#ifndef FIREBALL_PYSIM_JIT_INTERNAL_HXX
#define FIREBALL_PYSIM_JIT_INTERNAL_HXX
#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"
namespace fireball {
inline constexpr std::uint32_t kMaxTraceInstructions = 64;
inline constexpr std::uint32_t kMaxStackDepth = 16;
inline constexpr std::uint32_t kMaxBodyBytes = 512;

struct jit_compile_result {
  std::uint32_t body_bytes;
  std::int32_t helper_index;
  std::uint32_t helper_words;
  std::uint32_t max_spilled_words;
  std::uint32_t stack_location_count;
};

// Returns 1 for a compiled trace, 0 when the trace is unsupported, and a
// negative value when the call or output buffer violates the ABI contract.
int compile_instruction_body(
    const fireball::jit_instruction *instructions,
    std::uint32_t instruction_count, std::uint32_t has_next_pc,
    std::uint32_t next_pc, std::uint32_t has_loops_to, std::uint32_t loops_to,
    std::uint32_t byte_span, const std::uint16_t *local_offsets,
    const std::uint8_t *local_sizes, std::uint32_t local_count,
    std::uint32_t local_slot_count, std::uint32_t tail_context_helper,
    std::uintptr_t helper_target, std::uint8_t *output,
    std::uint32_t output_capacity, fireball::jit_compile_result *result,
    std::int16_t *stack_locations, std::uint32_t stack_location_capacity);

int compile_wasm_trace(const std::uint8_t *code, std::uint32_t code_bytes,
                       std::uint32_t code_offset, std::uint32_t byte_span,
                       std::uint32_t has_next_pc, std::uint32_t next_pc,
                       std::uint32_t has_loops_to, std::uint32_t loops_to,
                       const std::uint16_t *local_offsets,
                       const std::uint8_t *local_sizes,
                       std::uint32_t local_count,
                       std::uint32_t local_slot_count,
                       std::uint8_t *output, std::uint32_t output_capacity,
                       fireball::jit_compile_result *result,
                       std::int16_t *stack_locations,
                       std::uint32_t stack_location_capacity);
void chain_dispatcher_code(const std::uint8_t **bytes,
                           std::uint32_t *byte_count, std::uint32_t *offset);
std::int64_t chain_successor(std::uintptr_t address, std::uint32_t bytes,
                             std::uint32_t offset, std::uint32_t span,
                             std::uint32_t next_pc);
int initialize_common_code(executable_memory *memory);
int patch_trace_chain(executable_memory *memory, std::uint32_t offset,
                      std::uint64_t target);
int build_trace(const std::uint8_t *body, std::uint32_t body_bytes,
                const fireball::jit_compile_result *result,
                std::uint64_t helper_target, std::uint32_t head_pc,
                std::uint32_t frame_depth, std::uint8_t *output,
                std::uint32_t capacity);
} // namespace fireball
#endif

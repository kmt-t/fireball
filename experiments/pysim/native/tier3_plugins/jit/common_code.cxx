#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <initializer_list>
#include <limits>
#include <type_traits>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"

#define FIREBALL_CPS_CALL FIREBALL_NATIVE_CPS_CALL
// clang-format off
#include "../../tier2_runtime/interpreter/native_interpreter_utilities.hxx"
#include "../../tier2_runtime/interpreter/native_interpreter_runtime_apis.hxx"
#include "jit_runtime_apis.hxx"
// clang-format on
#undef FIREBALL_CPS_CALL

#include "common_code.hxx"
#include "common_code_abi.hxx"
#include "jit_internal.hxx"

namespace fireball {
namespace {
constexpr std::uint32_t region_bytes = FB_CONF_JIT_CACHE_REGION_BYTES;
constexpr std::uint32_t common_bytes = FB_CONF_JIT_CACHE_COMMON_CODE_BYTES;
constexpr std::uint32_t chain_offset =
    FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET;
constexpr std::uint32_t chain_bytes =
    FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES;
constexpr std::uint32_t header_bytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::uint32_t body_offset = header_bytes;
static_assert(sizeof(jit_trace_header) == header_bytes);
static_assert(offsetof(jit_trace_header, const_rel_offset) == 32);
constexpr std::uint32_t chain_size =
    fireball::pysim::jit::generated::common_chain_dispatcher_size;
constexpr std::uint32_t api_table_offset = chain_offset + chain_bytes;
constexpr std::size_t api_table_bytes =
    kJitRuntimeApiCount * sizeof(jit_runtime_api_pointer);
static_assert(fireball::pysim::jit::generated::common_chain_dispatcher_offset ==
              chain_offset);
static_assert(fireball::pysim::jit::generated::common_code_bytes <=
              common_bytes);
static_assert(body_offset <= 127 && common_bytes < region_bytes);
static_assert(chain_offset + chain_bytes <= common_bytes);
static_assert(chain_size <= chain_bytes);
static_assert(common_bytes < region_bytes);
static_assert(sizeof(jit_runtime_api_pointer) == sizeof(std::uintptr_t));
static_assert(api_table_bytes <= common_bytes - api_table_offset,
              "runtime API function table does not fit in common code area");
template <typename T> void little_endian(std::uint8_t *dest, T value) {
  for (std::size_t i = 0; i < sizeof(T); ++i)
    dest[i] = static_cast<std::uint8_t>(value >> (i * 8));
}
} // namespace
} // namespace fireball

extern "C" void fb_jit_common_layout(fireball::jit_common_layout *output) {
  using namespace fireball;
  if (output != nullptr)
    *output = {region_bytes, common_bytes, chain_offset, chain_size,
               header_bytes};
}
int fireball::initialize_common_code(fireball::executable_memory *memory) {
  using namespace fireball;
  if (!fb_jit_memory_no_rwx(memory) || !memory->patching ||
      memory->bytes != region_bytes)
    return 0;
  auto *output = reinterpret_cast<std::uint8_t *>(memory->base);
  std::memset(output, 0, common_bytes);
  std::memcpy(output,
              fireball::pysim::jit::generated::fb_jit_common_code_start.data(),
              fireball::pysim::jit::generated::common_code_bytes);
  std::memcpy(output + api_table_offset, kJitRuntimeApiFunctionTable.data(),
              api_table_bytes);
  return 1;
}
extern "C" int fb_jit_runtime_api_slot(std::uint32_t opcode,
                                       std::uint32_t fc_prefix,
                                       std::uint8_t *slot) {
  if (slot == nullptr)
    return 0;
  if (fc_prefix != 0) {
    if (opcode >= kJitRuntimeApiSlots.fc_opcode.size())
      return 0;
    const auto value = kJitRuntimeApiSlots.fc_opcode[opcode];
    if (value == 0xFF)
      return 0;
    *slot = value;
    return 1;
  }
  if (opcode >= kJitRuntimeApiSlots.opcode.size())
    return 0;
  const auto value = kJitRuntimeApiSlots.opcode[opcode];
  if (value == 0xFF)
    return 0;
  *slot = value;
  return 1;
}
extern "C" int
fb_jit_common_install(fireball::executable_memory *memory, std::uint32_t offset,
                      const std::uint8_t *blob, std::uint32_t blob_bytes,
                      std::uint32_t block_index, std::uintptr_t *entry) {
  using namespace fireball;
  if (!fb_jit_memory_no_rwx(memory) || memory->bytes != region_bytes)
    return 0;
  auto *region = reinterpret_cast<std::uint8_t *>(memory->base);
  const auto bytes = memory->bytes;
  if (blob == nullptr || entry == nullptr || offset < common_bytes ||
      offset >= bytes || blob_bytes < header_bytes ||
      blob_bytes > bytes - offset)
    return 0;
  const auto source = reinterpret_cast<std::uintptr_t>(blob);
  const auto base = reinterpret_cast<std::uintptr_t>(region);
  const auto source_in_region = source >= base && source - base < bytes;
  const auto *source_header =
      reinterpret_cast<const fireball::jit_trace_header *>(blob);
  const auto common_base = source_in_region
                               ? static_cast<std::int64_t>(source) +
                                     source_header->common_code_relative
                               : static_cast<std::int64_t>(base) + chain_offset;
  const auto destination = static_cast<std::int64_t>(base) + offset;
  const auto common_relative = common_base - destination;
  if (common_relative < std::numeric_limits<std::int32_t>::min() ||
      common_relative > std::numeric_limits<std::int32_t>::max())
    return 0;
  // Relocation state is part of the trace header. Promotion copies the code and
  // rewrites this single relative base; no patch-position table is retained.
  static_assert(region_bytes < static_cast<std::uint32_t>(
                                   std::numeric_limits<std::int32_t>::max()));
  if (!memory->patching && !fb_jit_memory_begin(memory))
    return 0;
  auto *dest = region + offset;
  std::memmove(dest, blob, blob_bytes);
  little_endian(dest +
                    offsetof(fireball::jit_trace_header, common_code_relative),
                static_cast<std::uint32_t>(common_relative));
  little_endian(dest + offsetof(fireball::jit_trace_header, block_index),
                block_index);
  if (!fb_jit_memory_commit(memory))
    return 0;
  *entry = reinterpret_cast<std::uintptr_t>(region + offset + header_bytes);
  return 1;
}
int fireball::patch_trace_chain(fireball::executable_memory *memory,
                                std::uint32_t offset, std::uint64_t target) {
  using namespace fireball;
  constexpr auto field = FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET;
  if (!fb_jit_memory_no_rwx(memory) || memory->bytes != region_bytes)
    return 0;
  auto *region = reinterpret_cast<std::uint8_t *>(memory->base);
  const auto bytes = memory->bytes;
  if (offset < common_bytes || offset >= bytes ||
      field + sizeof(target) > bytes - offset)
    return 0;
  if (!memory->patching && !fb_jit_memory_begin(memory))
    return 0;
  little_endian(region + offset + field, target);
  return fb_jit_memory_commit(memory);
}

int fireball::build_trace(const std::uint8_t *body, std::uint32_t bytes,
                          const fireball::jit_compile_result *r,
                          std::uint32_t head_pc, std::uint32_t frame_depth,
                          std::uint32_t block_index, std::uint32_t body_end_pc,
                          std::uint32_t next_pc, std::uint32_t loops_to,
                          std::uint8_t *output, std::uint32_t capacity) {
  using namespace fireball;
  if (body == nullptr || r == nullptr || output == nullptr ||
      capacity < header_bytes + kTraceBoundaryMetadataBytes ||
      bytes > capacity - header_bytes)
    return 0;
  constexpr auto relocation_alignment = alignof(std::uintptr_t);
  const auto unaligned_metadata_offset =
      static_cast<std::uint64_t>(header_bytes) + bytes;
  const auto metadata_offset = static_cast<std::uint32_t>(
      (unaligned_metadata_offset + relocation_alignment - 1) &
      ~(relocation_alignment - 1));
  const auto pool_offset = metadata_offset + kTraceBoundaryMetadataBytes;
  if (metadata_offset > capacity || pool_offset > capacity ||
      r->relocation_bytes > capacity - pool_offset ||
      pool_offset + r->relocation_bytes >
          std::numeric_limits<std::uint16_t>::max())
    return 0;
  const auto result_words = r->result_words;
  const auto words = r->max_stack_words;
  if (words > 0xFFu || result_words > 0xFFu)
    return 0;
  auto *header = reinterpret_cast<jit_trace_header *>(output);
  *header = {0,
             0,
             head_pc,
             static_cast<std::uint32_t>(pool_offset + r->relocation_bytes),
             static_cast<std::uint8_t>(words),
             static_cast<std::uint8_t>(result_words),
             static_cast<std::uint8_t>(words),
             0,
             frame_depth,
             block_index,
             pool_offset};
  std::memmove(output + header_bytes, body, bytes);
  // The caller may compile directly into the destination trace buffer. In
  // that case the source relocation pool starts where boundary metadata will
  // live, so move it before writing the metadata or clearing alignment bytes.
  if (r->relocation_bytes != 0) {
    std::memmove(output + pool_offset, body + bytes, r->relocation_bytes);
  }
  if (metadata_offset > header_bytes + bytes)
    std::memset(output + header_bytes + bytes, 0,
                metadata_offset - header_bytes - bytes);
  const jit_trace_boundary_metadata boundary_metadata{
      body_end_pc, next_pc, loops_to, r->interpreter_rel_words};
  std::memcpy(output + metadata_offset, &boundary_metadata,
              sizeof(boundary_metadata));
  return 1;
}

extern "C" int fb_jit_compile_block(const fireball::jit_wasm_block *block,
                                    std::uint8_t *output,
                                    std::uint32_t capacity,
                                    std::uint8_t *body_scratch) {
  using namespace fireball;
  if (block == nullptr || output == nullptr || capacity <= header_bytes ||
      body_scratch == nullptr ||
      capacity > header_bytes + kMaxBodyBytes + kTraceBoundaryMetadataBytes)
    return -1;
  const auto next = chain_successor(
      reinterpret_cast<std::uintptr_t>(block->code.data), block->code.bytes,
      block->offset, block->byte_span, block->next_pc);
  if (next < 0)
    return -1;
  fireball::jit_compile_result result{};
  const auto status = compile_wasm_trace(
      block->code.data, block->code.bytes, block->offset, block->byte_span,
      next != UINT32_MAX, static_cast<std::uint32_t>(next), 0, 0,
      reinterpret_cast<const std::uint16_t *>(block->locals.offsets.data),
      block->locals.sizes.data, block->locals.types.data, block->locals.count,
      block->locals.total_words, body_scratch, kMaxBodyBytes, &result);
  if (status != 1)
    return status;
  return build_trace(body_scratch, result.body_bytes, &result, block->head_pc,
                     block->frame_depth, block->block_index,
                     block->head_pc + block->byte_span, block->next_pc,
                     block->loops_to, output, capacity)
             ? 1
             : -1;
}

extern "C" int fb_jit_region_init(fireball::executable_memory *memory) {
  return fb_jit_memory_init(memory, fireball::region_bytes) &&
         fireball::initialize_common_code(memory);
}

extern "C" int
fb_jit_compile_instructions(const fireball::jit_instruction_block *block,
                            std::uint8_t *output, std::uint32_t capacity,
                            std::uint8_t *body_scratch) {
  using namespace fireball;
  if (block == nullptr || output == nullptr || capacity <= header_bytes ||
      body_scratch == nullptr ||
      capacity > header_bytes + kMaxBodyBytes + kTraceBoundaryMetadataBytes)
    return -1;
  fireball::jit_compile_result result{};
  const auto status = compile_instruction_body(
      block->instructions, block->instruction_count,
      block->next_pc != UINT32_MAX, block->next_pc,
      block->loops_to != UINT32_MAX, block->loops_to, block->byte_span,
      reinterpret_cast<const std::uint16_t *>(block->locals.offsets.data),
      block->locals.sizes.data, block->locals.types.data, block->locals.count,
      block->locals.total_words, body_scratch, kMaxBodyBytes, &result);
  if (status != 1)
    return status;
  return build_trace(body_scratch, result.body_bytes, &result, block->head_pc,
                     0, std::numeric_limits<std::uint32_t>::max(),
                     block->head_pc + block->byte_span, block->next_pc,
                     block->loops_to, output, capacity)
             ? 1
             : -1;
}

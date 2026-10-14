/** @file @brief 共通dispatcherの配置、読み取り専用API表の借用とtrace再配置。 */
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"

#include "jit_runtime_apis.hxx"

#include "common_code.hxx"
#include "common_code_abi.hxx"
#include "jit_internal.hxx"

namespace fireball {
namespace {
constexpr std::uint32_t region_bytes = FB_CONF_JIT_CACHE_REGION_BYTES;
constexpr std::uint32_t common_bytes = FB_CONF_JIT_CACHE_COMMON_CODE_BYTES;
constexpr std::uint32_t chain_offset = FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET;
constexpr std::uint32_t chain_bytes = FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES;
constexpr std::uint32_t header_bytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::uint32_t body_offset = header_bytes;
constexpr std::uint32_t trace_blob_overhead_bytes =
    kTraceBoundaryMetadataBytes + alignof(std::uintptr_t) - 1;
static_assert(sizeof(jit_trace_header) == header_bytes);
static_assert(offsetof(jit_trace_header, const_rel_offset) == 28);
constexpr std::uint32_t chain_size = fireball::pysim::jit::generated::common_chain_dispatcher_size;
constexpr std::uint32_t api_table_pointer_offset = chain_offset + chain_bytes;

static_assert(fireball::pysim::jit::generated::common_chain_dispatcher_offset == chain_offset);
static_assert(fireball::pysim::jit::generated::common_code_bytes <= common_bytes);
static_assert(body_offset <= 127 && common_bytes < region_bytes);
static_assert(chain_offset + chain_bytes <= common_bytes);
static_assert(chain_size <= chain_bytes);
static_assert(common_bytes < region_bytes);
static_assert(sizeof(jit_api::jit_runtime_api_pointer) == sizeof(std::uintptr_t));

template <typename T> void little_endian(std::uint8_t *dest, T value) {
  for (std::size_t i = 0; i < sizeof(T); ++i) {
    dest[i] = static_cast<std::uint8_t>(value >> (i * 8));
  }
}
} // namespace
} // namespace fireball

extern "C" void fb_jit_common_layout(fireball::jit_common_layout *output) {
  using namespace fireball;
  assert(output != nullptr);
  *output = {region_bytes, common_bytes, chain_offset, chain_size, header_bytes};
}
int fireball::initialize_common_code(fireball::executable_memory *memory) {
  using namespace fireball;
  assert(fb_jit_memory_valid_protection(memory) && memory->patching &&
         memory->bytes == region_bytes);
  auto *output = reinterpret_cast<std::uint8_t *>(memory->base);
  std::memcpy(output, fireball::pysim::jit::generated::fb_jit_common_code_start.data(),
              fireball::pysim::jit::generated::common_code_bytes);
  // The catalog remains in read-only storage for the plugin's lifetime.
  // Only its borrowed address belongs to this runtime's writable code area.
  const auto *api_table = jit_api::runtime_api_table_data();
  static_assert(sizeof(api_table) <= common_bytes - api_table_pointer_offset);
  std::memcpy(output + api_table_pointer_offset, &api_table, sizeof(api_table));
  return 1;
}
extern "C" int fb_jit_common_install(fireball::executable_memory *memory, code_offset_t offset,
                                     const std::uint8_t *blob, code_offset_t blob_bytes,
                                     block_index_t block_index, std::uintptr_t *entry) {
  using namespace fireball;
  assert(fb_jit_memory_valid_protection(memory) && memory->bytes == region_bytes);
  auto *region = reinterpret_cast<std::uint8_t *>(memory->base);
  const auto bytes = memory->bytes;
  assert(blob != nullptr && entry != nullptr && offset >= common_bytes && offset < bytes &&
         blob_bytes >= header_bytes && blob_bytes <= bytes - offset);
  const auto source = reinterpret_cast<std::uintptr_t>(blob);
  const auto base = reinterpret_cast<std::uintptr_t>(region);
  const auto source_in_region = source >= base && source - base < bytes;
  const auto *source_header = reinterpret_cast<const fireball::jit_trace_header *>(blob);
  const auto common_base =
      source_in_region ? static_cast<std::int64_t>(source) + source_header->common_code_relative
                       : static_cast<std::int64_t>(base) + chain_offset;
  const auto destination = static_cast<std::int64_t>(base) + offset;
  const auto common_relative = common_base - destination;
  assert(common_relative >= std::numeric_limits<std::int32_t>::min() &&
         common_relative <= std::numeric_limits<std::int32_t>::max());
  // Installation resolves the common-code base through the trace header;
  // no patch-position table is retained.
  static_assert(region_bytes <
                static_cast<std::uint32_t>(std::numeric_limits<std::int32_t>::max()));
  // An enclosing patch transaction owns the commit.
  const auto began = !memory->patching;
  if (began) {
    fb_jit_memory_begin(memory);
  }
  auto *dest = region + offset;
  std::memmove(dest, blob, blob_bytes);
  little_endian(dest + offsetof(fireball::jit_trace_header, common_code_relative),
                static_cast<std::uint32_t>(common_relative));
  little_endian(dest + offsetof(fireball::jit_trace_header, block_index), block_index);
  if (began) {
    fb_jit_memory_commit(memory);
  }
  *entry = reinterpret_cast<std::uintptr_t>(region + offset + header_bytes);
  return 1;
}
int fireball::patch_trace_chain(fireball::executable_memory *memory, code_offset_t offset,
                                std::uint64_t target) {
  using namespace fireball;
  constexpr auto field = FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET;
  assert(fb_jit_memory_valid_protection(memory) && memory->bytes == region_bytes);
  auto *region = reinterpret_cast<std::uint8_t *>(memory->base);
  [[maybe_unused]] const auto bytes = memory->bytes;
  assert(offset >= common_bytes && offset < bytes && field + sizeof(target) <= bytes - offset);
  const auto began = !memory->patching;
  if (began) {
    fb_jit_memory_begin(memory);
  }
  little_endian(region + offset + field, target);
  if (began) {
    fb_jit_memory_commit(memory);
  }
  return 1;
}

int fireball::build_trace(const std::uint8_t *body, code_offset_t bytes,
                          const fireball::jit_compile_result *r, wasm_pc_t head_pc,
                          frame_depth_t frame_depth, block_index_t block_index,
                          code_ip_t body_end_ip, wasm_pc_t next_pc, wasm_pc_t loops_to,
                          block_index_t next_block_index, std::uint8_t *output,
                          code_offset_t capacity) {
  using namespace fireball;
  if (body == nullptr || r == nullptr || output == nullptr ||
      capacity < header_bytes + kTraceBoundaryMetadataBytes || bytes > capacity - header_bytes) {
    return 0;
  }
  constexpr auto relocation_alignment = alignof(std::uintptr_t);
  const auto unaligned_metadata_offset = static_cast<std::uint64_t>(header_bytes) + bytes;
  const auto metadata_offset = static_cast<std::uint32_t>(
      (unaligned_metadata_offset + relocation_alignment - 1) & ~(relocation_alignment - 1));
  const auto pool_offset = metadata_offset + kTraceBoundaryMetadataBytes;
  if (metadata_offset > capacity || pool_offset > capacity ||
      r->relocation_bytes > capacity - pool_offset ||
      pool_offset + r->relocation_bytes > std::numeric_limits<std::uint16_t>::max()) {
    return 0;
  }
  const auto result_words = r->result_words;
  const auto words = r->max_stack_words;
  if (words > 0xFFu || result_words > 0xFFu) {
    return 0;
  }
  auto *header = reinterpret_cast<jit_trace_header *>(output);
  *header = {0,
             0,
             head_pc,
             static_cast<std::uint32_t>(pool_offset + r->relocation_bytes),
             static_cast<std::uint8_t>(words),
             static_cast<std::uint8_t>(result_words),
             static_cast<std::uint8_t>(words),
             1,
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
  if (metadata_offset > header_bytes + bytes) {
    std::memset(output + header_bytes + bytes, 0, metadata_offset - header_bytes - bytes);
  }
  const jit_trace_boundary_metadata boundary_metadata{body_end_ip,
                                                      next_pc,
                                                      loops_to,
                                                      r->interpreter_rel_bytes,
                                                      next_block_index,
                                                      frame_depth,
                                                      static_cast<operand_words_t>(result_words)};
  std::memcpy(output + metadata_offset, &boundary_metadata, sizeof(boundary_metadata));
  return 1;
}

extern "C" int fb_jit_compile_block(const fireball::jit_wasm_block *block, std::uint8_t *output,
                                    std::uint32_t capacity, std::uint8_t *body_scratch) {
  using namespace fireball;
  if (block == nullptr || output == nullptr || capacity <= header_bytes ||
      body_scratch == nullptr ||
      capacity > header_bytes + kMaxBodyBytes + trace_blob_overhead_bytes) {
    return -1;
  }
  const auto next =
      chain_successor(reinterpret_cast<std::uintptr_t>(block->code.data), block->code.bytes,
                      block->offset, block->byte_span, block->next_pc);
  if (next < 0) {
    return -1;
  }
  fireball::jit_compile_result result{};
  const auto status = compile_wasm_trace(
      block->code.data, block->code.bytes, block->offset, block->byte_span, next != UINT32_MAX,
      static_cast<std::uint32_t>(next), 0, 0, block->interpreter_annotations.data,
      block->interpreter_annotations.bytes, block->interpreter_annotation_byte_offset,
      reinterpret_cast<const std::uint16_t *>(block->locals.offsets.data), block->locals.types.data,
      block->locals.count, block->locals.total_words, body_scratch, kMaxBodyBytes, &result);
  if (status != 1) {
    return status;
  }
  return build_trace(body_scratch, result.body_bytes, &result, block->head_pc, block->frame_depth,
                     block->block_index, block->offset + block->byte_span, block->next_pc,
                     block->loops_to, block->next_block_index, output, capacity)
             ? 1
             : -1;
}

extern "C" int fb_jit_region_init(fireball::executable_memory *memory) {
  fb_jit_memory_init(memory, fireball::region_bytes);
  fireball::initialize_common_code(memory);
  return 1;
}

extern "C" int fb_jit_compile_instructions(const fireball::jit_instruction_block *block,
                                           std::uint8_t *output, std::uint32_t capacity,
                                           std::uint8_t *body_scratch) {
  using namespace fireball;
  if (block == nullptr || output == nullptr || capacity <= header_bytes ||
      body_scratch == nullptr ||
      capacity > header_bytes + kMaxBodyBytes + trace_blob_overhead_bytes) {
    return -1;
  }
  fireball::jit_compile_result result{};
  const auto status = compile_instruction_body(
      block->instructions, block->instruction_count, block->next_pc != UINT32_MAX, block->next_pc,
      block->loops_to != UINT32_MAX, block->loops_to, block->byte_span,
      reinterpret_cast<const std::uint16_t *>(block->locals.offsets.data), block->locals.types.data,
      block->locals.count, block->locals.total_words, body_scratch, kMaxBodyBytes, &result);
  if (status != 1) {
    return status;
  }
  return build_trace(body_scratch, result.body_bytes, &result, block->head_pc, 0,
                     FIREBALL_INVALID_BLOCK_INDEX, block->head_pc + block->byte_span,
                     block->next_pc, block->loops_to, FIREBALL_INVALID_BLOCK_INDEX, output,
                     capacity)
             ? 1
             : -1;
}

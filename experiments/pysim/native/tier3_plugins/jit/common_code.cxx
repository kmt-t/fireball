#include <array>
#include <bit>
#include <cassert>
#include <cstring>
#include <limits>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "common_code_abi.hxx"
#include "jit_internal.hxx"
#include "jit_runtime_api.hxx"

#ifndef FB_CONF_JIT_CACHE_ABSOLUTE_ADDRESS_POOL_OFFSET
#error "JIT absolute address pool offset must come from tier1_core/config.py"
#endif

namespace fireball {
namespace {
constexpr std::uint32_t region_bytes = FB_CONF_JIT_CACHE_REGION_BYTES;
constexpr std::uint32_t common_bytes = FB_CONF_JIT_CACHE_COMMON_CODE_BYTES;
constexpr std::uint32_t prologue_offset =
    FB_CONF_JIT_TRACE_COMMON_PROLOGUE_OFFSET;
constexpr std::uint32_t epilogue_offset =
    FB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET;
constexpr std::uint32_t context_helper_offset =
    FB_CONF_JIT_TRACE_COMMON_HELPER_OFFSET;
constexpr std::uint32_t typed_offset =
    FB_CONF_JIT_TRACE_TYPED_I32_HELPER_OFFSET;
constexpr std::uint32_t typed_count = FB_CONF_JIT_TRACE_TYPED_I32_HELPER_COUNT;
constexpr std::uint32_t helper_bytes = FB_CONF_JIT_TRACE_HELPER_ENTRY_BYTES;
constexpr std::uint32_t chain_offset =
    FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET;
constexpr std::uint32_t chain_bytes =
    FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES;
constexpr std::uint32_t header_bytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::uint32_t stub_bytes = FB_CONF_JIT_X64_TRACE_ENTRY_STUB_BYTES;
constexpr std::uint32_t body_offset = header_bytes + stub_bytes;
constexpr std::uint32_t absolute_offset =
    FB_CONF_JIT_CACHE_ABSOLUTE_ADDRESS_POOL_OFFSET;
constexpr std::uint32_t absolute_bytes =
    FB_CONF_JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES;
#if defined(_WIN32)
constexpr bool windows_abi = true;
#else
constexpr bool windows_abi = false;
#endif
static_assert(body_offset <= 127 && common_bytes < region_bytes);
static_assert(static_cast<std::uint32_t>(jit_local_runtime_api_index::count) *
                  sizeof(std::uintptr_t) <=
              absolute_bytes);

struct code_piece {
  std::array<std::uint8_t, 64> bytes{};
  std::uint32_t count = 0;
  template <typename... Values> constexpr void emit(Values... values) {
    ((bytes[count++] = static_cast<std::uint8_t>(values)), ...);
  }
  constexpr void u32(std::uint32_t value) {
    for (std::uint32_t i = 0; i < 4; ++i)
      emit(value >> (8 * i));
  }
};
constexpr code_piece prologue() {
  code_piece c;
  c.emit(0x53, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57);
  if constexpr (windows_abi) {
    c.emit(0x57, 0x48, 0x89, 0xE7, 0x4D, 0x89, 0xC2, 0x49, 0x89, 0xD4, 0x49,
           0x89, 0xCD);
  } else {
    c.emit(0x55, 0x48, 0x89, 0xE5, 0x49, 0x89, 0xD2, 0x49, 0x89, 0xF4, 0x49,
           0x89, 0xFD, 0x41, 0x89, 0xC9);
  }
  c.emit(0x49, 0x89, 0xC6, 0x49, 0x8D, 0x46, body_offset, 0xFF, 0xE0);
  return c;
}
constexpr void restore(code_piece &c) {
  c.emit(windows_abi ? 0x5F : 0x5D, 0x41, 0x5F, 0x41, 0x5E, 0x41, 0x5D, 0x41,
         0x5C, 0x5B);
}
constexpr code_piece epilogue() {
  code_piece c;
  c.emit(0x31, 0xC0);
  restore(c);
  c.emit(0xC3);
  return c;
}
constexpr code_piece context_helper() {
  code_piece c;
  if constexpr (windows_abi) {
    c.emit(0x4C, 0x89, 0xE9, 0x4C, 0x89, 0xE2, 0x4D, 0x89, 0xD0);
  } else {
    c.emit(0x4C, 0x89, 0xEF, 0x4C, 0x89, 0xE6, 0x4D, 0x89, 0xD2, 0x44, 0x89,
           0xC9);
  }
  c.emit(0x48, 0x8B, 0x80);
  c.u32(FB_CONF_JIT_X64_HELPER_TARGET_OFFSET);
  restore(c);
  c.emit(0xFF, 0xE0);
  return c;
}
constexpr code_piece i32_helper() {
  code_piece c;
  if constexpr (windows_abi) {
    c.emit(0x4C, 0x89, 0xE9, 0x44, 0x89, 0xDA, 0x45, 0x89, 0xC8, 0x4D, 0x89,
           0xE1);
  } else {
    c.emit(0x4C, 0x89, 0xEF, 0x44, 0x89, 0xDE, 0x44, 0x89, 0xCA, 0x4C, 0x89,
           0xE1);
  }
  c.emit(0x49, 0x8B, 0x86);
  c.u32(FB_CONF_JIT_X64_HELPER_TARGET_OFFSET);
  c.emit(0x48, 0x83, 0xEC, windows_abi ? 0x28 : 0x08, 0xFF, 0xD0, 0x48, 0x83,
         0xC4, windows_abi ? 0x28 : 0x08, 0xC3);
  return c;
}
constexpr auto prologue_code = prologue();
constexpr auto epilogue_code = epilogue();
constexpr auto helper_code = context_helper();
constexpr auto i32_code = i32_helper();
static_assert(prologue_offset + prologue_code.count <= epilogue_offset);
static_assert(epilogue_offset + epilogue_code.count <= context_helper_offset);
static_assert(context_helper_offset + helper_code.count <= absolute_offset);
static_assert(absolute_offset + absolute_bytes <= typed_offset);
static_assert(typed_offset + typed_count * helper_bytes <= chain_offset);
static_assert(chain_offset + chain_bytes <= common_bytes);
static_assert(helper_code.count <= helper_bytes &&
              i32_code.count <= helper_bytes);

extern "C" std::uint32_t
i32_div_s_helper(fireball_execution_context_native *context, std::uint32_t lhs,
                 std::uint32_t rhs, std::uint32_t *result) noexcept {
  const auto left = std::bit_cast<std::int32_t>(lhs);
  const auto right = std::bit_cast<std::int32_t>(rhs);
  if (right == 0) {
    context->trap_code = 15;
    return 15;
  }
  if (left == std::numeric_limits<std::int32_t>::min() && right == -1) {
    context->trap_code = 16;
    return 16;
  }
  *result =
      std::bit_cast<std::uint32_t>(static_cast<std::int32_t>(left / right));
  return 0;
}

extern "C" std::uint32_t
i32_div_u_helper(fireball_execution_context_native *context, std::uint32_t lhs,
                 std::uint32_t rhs, std::uint32_t *result) noexcept {
  if (rhs == 0) {
    context->trap_code = 15;
    return 15;
  }
  *result = lhs / rhs;
  return 0;
}

extern "C" std::uint32_t
i32_rem_s_helper(fireball_execution_context_native *context, std::uint32_t lhs,
                 std::uint32_t rhs, std::uint32_t *result) noexcept {
  const auto left = std::bit_cast<std::int32_t>(lhs);
  const auto right = std::bit_cast<std::int32_t>(rhs);
  if (right == 0) {
    context->trap_code = 15;
    return 15;
  }
  const auto remainder =
      left == std::numeric_limits<std::int32_t>::min() && right == -1
          ? 0
          : left % right;
  *result = std::bit_cast<std::uint32_t>(static_cast<std::int32_t>(remainder));
  return 0;
}

extern "C" std::uint32_t
i32_rem_u_helper(fireball_execution_context_native *context, std::uint32_t lhs,
                 std::uint32_t rhs, std::uint32_t *result) noexcept {
  if (rhs == 0) {
    context->trap_code = 15;
    return 15;
  }
  *result = lhs % rhs;
  return 0;
}

std::uintptr_t i32_helper_target(std::int32_t helper_index) noexcept {
  switch (helper_index) {
  case 11:
    return reinterpret_cast<std::uintptr_t>(&i32_div_s_helper);
  case 12:
    return reinterpret_cast<std::uintptr_t>(&i32_div_u_helper);
  case 13:
    return reinterpret_cast<std::uintptr_t>(&i32_rem_s_helper);
  case 14:
    return reinterpret_cast<std::uintptr_t>(&i32_rem_u_helper);
  default:
    return 0;
  }
}

template <typename T> void little_endian(std::uint8_t *dest, T value) {
  for (std::size_t i = 0; i < sizeof(T); ++i)
    dest[i] = static_cast<std::uint8_t>(value >> (i * 8));
}
} // namespace
} // namespace fireball

namespace {
struct local_api_span {
  std::uint32_t offset;
  std::uint32_t width;
};

local_api_span
runtime_local_span(const fireball_execution_context_native &context,
                   std::uint32_t local_index) noexcept {
  assert(context.call_stack != nullptr);
  assert(context.call_stack->size > context.call_base);
  const auto &frame = context.call_stack->frames[context.call_stack->size - 1];
  assert(local_index < frame.local_count);
  assert(frame.slot_words == 1 || frame.slot_words == 2 ||
         frame.slot_words == 4);
  if (frame.slot_words == 1)
    return {local_index, 1};

  assert(frame.local_width_map != nullptr);
  const auto packed = frame.local_width_map[local_index >> 2];
  const auto width_code = (packed >> ((local_index & 3u) * 2u)) & 3u;
  const auto width = 1u << width_code;
  assert(width <= frame.slot_words);
  return {local_index * frame.slot_words, width};
}
} // namespace

extern "C" void
fb_jit_runtime_local_get(fireball_execution_context_native *context,
                         std::uint32_t *local_base, std::uint32_t *stack_words,
                         std::uint32_t local_index) noexcept {
  assert(context != nullptr);
  assert(local_base != nullptr);
  assert(stack_words != nullptr);
  const auto span = runtime_local_span(*context, local_index);
  for (std::uint32_t word = 0; word < span.width; ++word)
    stack_words[word] = local_base[span.offset + word];
}

extern "C" void
fb_jit_runtime_local_set(fireball_execution_context_native *context,
                         std::uint32_t *local_base, std::uint32_t *stack_words,
                         std::uint32_t local_index) noexcept {
  assert(context != nullptr);
  assert(local_base != nullptr);
  assert(stack_words != nullptr);
  const auto span = runtime_local_span(*context, local_index);
  for (std::uint32_t word = 0; word < span.width; ++word)
    local_base[span.offset + word] = stack_words[word];
}

extern "C" void
fb_jit_runtime_local_tee(fireball_execution_context_native *context,
                         std::uint32_t *local_base, std::uint32_t *stack_words,
                         std::uint32_t local_index) noexcept {
  fb_jit_runtime_local_set(context, local_base, stack_words, local_index);
}

extern "C" void fb_jit_common_layout(fireball::jit_common_layout *output) {
  using namespace fireball;
  if (output != nullptr)
    *output = {region_bytes,          common_bytes,    prologue_offset,
               prologue_code.count,   epilogue_offset, epilogue_code.count,
               typed_offset,          helper_bytes,    chain_offset,
               chain_bytes,           absolute_offset, absolute_bytes,
               context_helper_offset, header_bytes,    stub_bytes};
}
int fireball::initialize_common_code(fireball::executable_memory *memory) {
  using namespace fireball;
  if (!fb_jit_memory_no_rwx(memory) || !memory->patching ||
      memory->bytes != region_bytes)
    return 0;
  auto *output = reinterpret_cast<std::uint8_t *>(memory->base);
  std::memset(output, 0, common_bytes);
  std::memcpy(output + prologue_offset, prologue_code.bytes.data(),
              prologue_code.count);
  std::memcpy(output + epilogue_offset, epilogue_code.bytes.data(),
              epilogue_code.count);
  std::memcpy(output + context_helper_offset, helper_code.bytes.data(),
              helper_code.count);
  for (std::uint32_t i = 0; i < typed_count; ++i) {
    const auto offset = typed_offset + i * helper_bytes;
    std::memcpy(output + offset, i32_code.bytes.data(), i32_code.count);
  }
  const std::array<jit_local_runtime_api,
                   static_cast<std::size_t>(jit_local_runtime_api_index::count)>
      local_apis = {&fb_jit_runtime_local_get, &fb_jit_runtime_local_set,
                    &fb_jit_runtime_local_tee};
  std::array<std::uintptr_t,
             static_cast<std::size_t>(jit_local_runtime_api_index::count)>
      api_addresses{};
  for (std::size_t index = 0; index < local_apis.size(); ++index)
    api_addresses[index] = reinterpret_cast<std::uintptr_t>(local_apis[index]);
  std::memcpy(output + absolute_offset, api_addresses.data(),
              sizeof(api_addresses));
  const std::uint8_t *chain = nullptr;
  std::uint32_t bytes = 0, offset = 0;
  chain_dispatcher_code(&chain, &bytes, &offset);
  if (chain == nullptr || bytes != chain_bytes || offset != chain_offset)
    return 0;
  std::memcpy(output + chain_offset, chain, bytes);
  return 1;
}
extern "C" int fb_jit_common_install(fireball::executable_memory *memory,
                                     std::uint32_t offset,
                                     const std::uint8_t *blob,
                                     std::uint32_t blob_bytes,
                                     std::uintptr_t *entry) {
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
                               : static_cast<std::int64_t>(base) +
                                     source_header->common_code_relative;
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
                          std::uint64_t helper_target, std::uint32_t head_pc,
                          std::uint32_t frame_depth, std::uint8_t *output,
                          std::uint32_t capacity) {
  using namespace fireball;
  if (body == nullptr || r == nullptr || output == nullptr ||
      capacity < header_bytes || bytes < stub_bytes ||
      bytes > capacity - header_bytes)
    return 0;
  const auto wide_result =
      r->helper_index >= 0 &&
      (r->helper_index <= 2 || (r->helper_index >= 7 && r->helper_index <= 10));
  const auto result_words =
      r->helper_words != 0
          ? r->helper_words
          : (r->stack_location_count != 0 || r->helper_index >= 0
                 ? (wide_result ? 2u : 1u)
                 : 0u);
  auto words = r->max_spilled_words > r->helper_words ? r->max_spilled_words
                                                      : r->helper_words;
  if (result_words > words)
    words = result_words;
  if (header_bytes + bytes > std::numeric_limits<std::uint16_t>::max() ||
      words > 0xFFu || result_words > 0xFFu)
    return 0;
  auto *header = reinterpret_cast<jit_trace_header *>(output);
  *header = {0,
             helper_target,
             0,
             head_pc,
             static_cast<std::uint32_t>(header_bytes + bytes),
             static_cast<std::uint8_t>(words),
             static_cast<std::uint8_t>(result_words),
             static_cast<std::uint8_t>(result_words != 0),
             static_cast<std::uint8_t>(words),
             frame_depth};
  std::memmove(output + header_bytes, body, bytes);
  return 1;
}

extern "C" int fb_jit_compile_block(const fireball::jit_wasm_block *block,
                                    std::uint8_t *output,
                                    std::uint32_t capacity,
                                    std::uint8_t *body_scratch,
                                    std::int16_t *stack_locations,
                                    std::uint32_t stack_location_capacity) {
  using namespace fireball;
  if (block == nullptr || output == nullptr || capacity <= header_bytes ||
      body_scratch == nullptr || stack_locations == nullptr ||
      stack_location_capacity < kMaxStackDepth ||
      capacity > header_bytes + kMaxBodyBytes)
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
      block->locals.widths.data, block->locals.widths.bytes,
      block->locals.count, block->locals.slot_words, body_scratch,
      kMaxBodyBytes, &result, stack_locations, stack_location_capacity);
  if (status != 1)
    return status;
  return build_trace(body_scratch, result.body_bytes, &result,
                     i32_helper_target(result.helper_index), block->head_pc,
                     block->frame_depth, output, capacity)
             ? 1
             : -1;
}

extern "C" int fb_jit_region_init(fireball::executable_memory *memory) {
  return fb_jit_memory_init(memory, fireball::region_bytes) &&
         fireball::initialize_common_code(memory);
}

extern "C" int fb_jit_compile_instructions(
    const fireball::jit_instruction_block *block, std::uint8_t *output,
    std::uint32_t capacity, std::uint8_t *body_scratch,
    std::int16_t *stack_locations, std::uint32_t stack_location_capacity) {
  using namespace fireball;
  if (block == nullptr || output == nullptr || capacity <= header_bytes ||
      body_scratch == nullptr || stack_locations == nullptr ||
      stack_location_capacity < kMaxStackDepth ||
      capacity > header_bytes + kMaxBodyBytes)
    return -1;
  fireball::jit_compile_result result{};
  const auto status = compile_instruction_body(
      block->instructions, block->instruction_count,
      block->next_pc != UINT32_MAX, block->next_pc,
      block->loops_to != UINT32_MAX, block->loops_to, block->byte_span,
      block->locals.widths.data, block->locals.widths.bytes,
      block->locals.count, block->locals.slot_words, block->context_helper,
      block->helper_target, body_scratch, kMaxBodyBytes, &result,
      stack_locations, stack_location_capacity);
  if (status != 1)
    return status;
  return build_trace(body_scratch, result.body_bytes, &result,
                     block->helper_target, block->head_pc, 0, output, capacity)
             ? 1
             : -1;
}

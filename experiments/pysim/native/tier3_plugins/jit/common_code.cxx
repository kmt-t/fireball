#include <array>
#include <cstring>
#include <limits>

#include "common_code_abi.hxx"
#include "jit_internal.hxx"

namespace fireball {
namespace {
constexpr std::uint32_t region_bytes = FB_CONF_JIT_CACHE_REGION_BYTES;
constexpr std::uint32_t common_bytes = FB_CONF_JIT_CACHE_COMMON_CODE_BYTES;
constexpr std::uint32_t prologue_offset = FB_CONF_JIT_TRACE_COMMON_PROLOGUE_OFFSET;
constexpr std::uint32_t epilogue_offset = FB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET;
constexpr std::uint32_t context_helper_offset = FB_CONF_JIT_TRACE_COMMON_HELPER_OFFSET;
constexpr std::uint32_t wide_offset = FB_CONF_JIT_TRACE_WIDE_HELPER_OFFSET;
constexpr std::uint32_t wide_count = FB_CONF_JIT_TRACE_WIDE_HELPER_COUNT;
constexpr std::uint32_t typed_offset = FB_CONF_JIT_TRACE_TYPED_I32_HELPER_OFFSET;
constexpr std::uint32_t typed_count = FB_CONF_JIT_TRACE_TYPED_I32_HELPER_COUNT;
constexpr std::uint32_t helper_bytes = FB_CONF_JIT_TRACE_HELPER_ENTRY_BYTES;
constexpr std::uint32_t chain_offset = FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET;
constexpr std::uint32_t chain_bytes = FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES;
constexpr std::uint32_t header_bytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::uint32_t stub_bytes = FB_CONF_JIT_X64_TRACE_ENTRY_STUB_BYTES;
constexpr std::uint32_t body_offset = header_bytes + stub_bytes;
constexpr std::uint32_t absolute_offset = 80;
constexpr std::uint32_t absolute_bytes = FB_CONF_JIT_CACHE_ABSOLUTE_ADDRESS_POOL_BYTES;
#if defined(_WIN32)
constexpr bool windows_abi = true;
#else
constexpr bool windows_abi = false;
#endif
static_assert(body_offset <= 127 && common_bytes < region_bytes);

struct code_piece {
  std::array<std::uint8_t, 64> bytes{};
  std::uint32_t count = 0;
  template <typename... Values>
  constexpr void emit(Values... values) {
    ((bytes[count++] = static_cast<std::uint8_t>(values)), ...);
  }
  constexpr void u32(std::uint32_t value) {
    for (std::uint32_t i = 0; i < 4; ++i) emit(value >> (8 * i));
  }
};
constexpr code_piece prologue() {
  code_piece c;
  c.emit(0x53, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57);
  if constexpr (windows_abi) {
    c.emit(0x57, 0x48, 0x89, 0xE7, 0x4D, 0x89, 0xC2, 0x49, 0x89, 0xD4, 0x49, 0x89, 0xCD);
  } else {
    c.emit(0x55, 0x48, 0x89, 0xE5, 0x49, 0x89, 0xD2, 0x49, 0x89, 0xF4, 0x49, 0x89, 0xFD, 0x44, 0x89,
           0xC9);
  }
  c.emit(0x4C, 0x8D, 0x70, -static_cast<std::int32_t>(body_offset), 0xFF, 0xE0);
  return c;
}
constexpr void restore(code_piece& c) {
  c.emit(windows_abi ? 0x5F : 0x5D, 0x41, 0x5F, 0x41, 0x5E, 0x41, 0x5D, 0x41, 0x5C, 0x5B);
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
    c.emit(0x4C, 0x89, 0xEF, 0x4C, 0x89, 0xE6, 0x4D, 0x89, 0xD2, 0x44, 0x89, 0xC9);
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
    c.emit(0x44, 0x89, 0xD9, 0x44, 0x89, 0xCA, 0x4D, 0x89, 0xE0);
  } else {
    c.emit(0x44, 0x89, 0xDF, 0x44, 0x89, 0xCE, 0x4C, 0x89, 0xE2);
  }
  c.emit(0x4C, 0x8B, 0xB0);
  c.u32(FB_CONF_JIT_X64_HELPER_TARGET_OFFSET);
  c.emit(0x48, 0x83, 0xEC, windows_abi ? 0x28 : 0x08, 0x41, 0xFF, 0xD6, 0x48, 0x83, 0xC4,
         windows_abi ? 0x28 : 0x08, 0xE9, 0, 0, 0, 0);
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
static_assert(typed_offset + typed_count * helper_bytes <= wide_offset);
static_assert(wide_offset + wide_count * helper_bytes <= chain_offset);
static_assert(chain_offset + chain_bytes <= common_bytes);
static_assert(helper_code.count <= helper_bytes && i32_code.count == helper_bytes);

template <typename T>
void little_endian(std::uint8_t* dest, T value) {
  for (std::size_t i = 0; i < sizeof(T); ++i) dest[i] = static_cast<std::uint8_t>(value >> (i * 8));
}
bool valid_patch(std::int32_t offset, std::uint32_t width, std::uint32_t blob_bytes) {
  return offset == -1 || (offset >= 0 && static_cast<std::uint32_t>(offset) <= blob_bytes &&
                          width <= blob_bytes - static_cast<std::uint32_t>(offset));
}
void patch_rel32(std::uint8_t* blob, std::uint32_t trace_offset, std::int32_t patch,
                 std::uint32_t target) {
  if (patch < 0) return;
  const auto delta = static_cast<std::int64_t>(target) - (trace_offset + patch + 4ll);
  little_endian(blob + patch, static_cast<std::uint32_t>(delta));
}
}  // namespace
}  // namespace fireball
extern "C" void fb_jit_common_layout(fireball::jit_common_layout* output) {
  using namespace fireball;
  if (output != nullptr)
    *output = {region_bytes,          common_bytes,        prologue_offset, prologue_code.count,
               epilogue_offset,       epilogue_code.count, wide_offset,     helper_bytes,
               chain_offset,          chain_bytes,         absolute_offset, absolute_bytes,
               context_helper_offset, header_bytes,        stub_bytes};
}
std::int32_t fireball::helper_offset(std::int32_t helper) {
  using namespace fireball;
  if (helper < 0) return -1;
  const auto index = static_cast<std::uint32_t>(helper);
  if (index < wide_count) return static_cast<std::int32_t>(wide_offset + index * helper_bytes);
  return index - wide_count < typed_count
             ? static_cast<std::int32_t>(typed_offset + (index - wide_count) * helper_bytes)
             : -1;
}
int fireball::initialize_common_code(fireball::executable_memory* memory) {
  using namespace fireball;
  if (!fb_jit_memory_no_rwx(memory) || !memory->patching || memory->bytes != region_bytes) return 0;
  auto* output = reinterpret_cast<std::uint8_t*>(memory->base);
  std::memset(output, 0, common_bytes);
  std::memcpy(output + prologue_offset, prologue_code.bytes.data(), prologue_code.count);
  std::memcpy(output + epilogue_offset, epilogue_code.bytes.data(), epilogue_code.count);
  std::memcpy(output + context_helper_offset, helper_code.bytes.data(), helper_code.count);
  for (std::uint32_t i = 0; i < wide_count; ++i)
    std::memcpy(output + wide_offset + i * helper_bytes, helper_code.bytes.data(),
                helper_code.count);
  for (std::uint32_t i = 0; i < typed_count; ++i) {
    const auto offset = typed_offset + i * helper_bytes;
    std::memcpy(output + offset, i32_code.bytes.data(), i32_code.count);
    patch_rel32(output + offset, offset, static_cast<std::int32_t>(i32_code.count - 4),
                epilogue_offset);
  }
  const std::uint8_t* chain = nullptr;
  std::uint32_t bytes = 0, offset = 0;
  chain_dispatcher_code(&chain, &bytes, &offset);
  if (chain == nullptr || bytes != chain_bytes || offset != chain_offset) return 0;
  std::memcpy(output + chain_offset, chain, bytes);
  return 1;
}
extern "C" int fb_jit_common_install(fireball::executable_memory* memory, std::uint32_t offset,
                                     const std::uint8_t* blob, std::uint32_t blob_bytes,
                                     const fireball::jit_trace_fixups* f, std::uintptr_t* entry) {
  using namespace fireball;
  if (!fb_jit_memory_no_rwx(memory) || memory->bytes != region_bytes) return 0;
  auto* region = reinterpret_cast<std::uint8_t*>(memory->base);
  const auto bytes = memory->bytes;
  if (blob == nullptr || f == nullptr || entry == nullptr || offset < common_bytes ||
      offset >= bytes || blob_bytes < header_bytes || blob_bytes > bytes - offset ||
      f->helper_offset >= common_bytes || !valid_patch(f->entry_body, 8, blob_bytes) ||
      !valid_patch(f->entry_prologue, 4, blob_bytes) || !valid_patch(f->exit, 4, blob_bytes) ||
      !valid_patch(f->helper_header, 4, blob_bytes) ||
      !valid_patch(f->helper_exit, 4, blob_bytes) ||
      !valid_patch(f->chain_dispatch, 4, blob_bytes) ||
      (f->helper_header >= 0 && f->helper_exit < 0))
    return 0;
  // All fallible validation precedes the first write. Region bounds also bound
  // every rel32 displacement; the common prefix and traces share one allocation.
  static_assert(region_bytes <
                static_cast<std::uint32_t>(std::numeric_limits<std::int32_t>::max()));
  if (!memory->patching && !fb_jit_memory_begin(memory)) return 0;
  auto* dest = region + offset;
  std::memmove(dest, blob, blob_bytes);
  if (f->entry_body >= 0)
    little_endian(dest + f->entry_body, static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(
                                            region + offset + body_offset)));
  patch_rel32(dest, offset, f->entry_prologue, prologue_offset);
  patch_rel32(dest, offset, f->exit, epilogue_offset);
  patch_rel32(dest, offset, f->chain_dispatch, chain_offset);
  if (f->helper_header >= 0) {
    patch_rel32(dest, offset, f->helper_header, offset);
    patch_rel32(dest, offset, f->helper_exit, f->helper_offset);
  }
  if (!fb_jit_memory_commit(memory)) return 0;
  *entry = reinterpret_cast<std::uintptr_t>(region + offset + header_bytes);
  return 1;
}
int fireball::patch_trace_chain(fireball::executable_memory* memory, std::uint32_t offset,
                                std::uint64_t target) {
  using namespace fireball;
  constexpr auto field = FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET;
  if (!fb_jit_memory_no_rwx(memory) || memory->bytes != region_bytes) return 0;
  auto* region = reinterpret_cast<std::uint8_t*>(memory->base);
  const auto bytes = memory->bytes;
  if (offset < common_bytes || offset >= bytes || field + sizeof(target) > bytes - offset) return 0;
  if (!memory->patching && !fb_jit_memory_begin(memory)) return 0;
  little_endian(region + offset + field, target);
  return fb_jit_memory_commit(memory);
}

extern "C" int fb_jit_pack_header(std::uint64_t chain_target, std::uint64_t helper_target,
                                  std::uint8_t* output, std::uint32_t capacity) {
  using namespace fireball;
  if (output == nullptr || capacity < header_bytes) return 0;
  static_assert(FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET + sizeof(chain_target) <= header_bytes);
  static_assert(FB_CONF_JIT_X64_HELPER_TARGET_OFFSET + sizeof(helper_target) <= header_bytes);
  std::memset(output, 0, header_bytes);
  little_endian(output + FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET, chain_target);
  little_endian(output + FB_CONF_JIT_X64_HELPER_TARGET_OFFSET, helper_target);
  return 1;
}
int fireball::build_trace(const std::uint8_t* body, std::uint32_t bytes,
                          const fireball::jit_compile_result* r, std::uint64_t helper_target,
                          fireball::jit_cache_trace* t, fireball::jit_trace_fixups* f,
                          std::uint8_t* output, std::uint32_t capacity) {
  using namespace fireball;
  if (body == nullptr || r == nullptr || t == nullptr || f == nullptr || output == nullptr ||
      capacity < header_bytes || bytes < stub_bytes || bytes > capacity - header_bytes)
    return 0;
  const auto helper = r->helper_index >= 0 ? helper_offset(r->helper_index)
                                           : static_cast<std::int32_t>(context_helper_offset);
  if (helper < 0) return 0;
  const auto wide_result =
      r->helper_index >= 0 &&
      (r->helper_index <= 2 || (r->helper_index >= 7 && r->helper_index <= 10));
  const auto result_words = wide_result ? 2u : 1u;
  auto words = r->max_spilled_words > r->helper_words ? r->max_spilled_words : r->helper_words;
  if (result_words > words) words = result_words;
  t->size_bytes = header_bytes + bytes;
  t->has_return_value = r->stack_location_count != 0 || r->helper_index >= 0;
  t->result_words = result_words;
  t->stack_words = words;
  *f = {static_cast<std::int32_t>(header_bytes + 2),
        static_cast<std::int32_t>(header_bytes + 11),
        r->exit_patch_offset,
        r->helper_header_patch_offset,
        r->helper_exit_patch_offset,
        r->chain_dispatch_patch_offset,
        static_cast<std::uint32_t>(helper)};
  if (!fb_jit_pack_header(0, helper_target, output, capacity)) return 0;
  std::memmove(output + header_bytes, body, bytes);
  return 1;
}

extern "C" int fb_jit_compile_block(const fireball::jit_wasm_block* block,
                                    fireball::jit_cache_trace* trace,
                                    fireball::jit_trace_fixups* fixups, std::uint8_t* output,
                                    std::uint32_t capacity) {
  using namespace fireball;
  if (block == nullptr || trace == nullptr || fixups == nullptr || output == nullptr ||
      capacity <= header_bytes)
    return -1;
  const auto next =
      chain_successor(reinterpret_cast<std::uintptr_t>(block->code.data), block->code.bytes,
                      block->offset, block->byte_span, block->next_pc);
  if (next < 0) return -1;
  fireball::jit_compile_result result{};
  const auto status = compile_wasm_trace(
      block->code.data, block->code.bytes, block->offset, block->byte_span, next != UINT32_MAX,
      static_cast<std::uint32_t>(next), 0, 0, block->locals.widths.data, block->locals.widths.bytes,
      block->locals.count, block->locals.slot_words, output + header_bytes, capacity - header_bytes,
      &result);
  if (status != 1) return status;
  trace->head_pc = block->head_pc;
  trace->next_pc = static_cast<std::uint32_t>(next);
  trace->loops_to = UINT32_MAX;
  return build_trace(output + header_bytes, result.body_bytes, &result, 0, trace, fixups, output,
                     capacity)
             ? 1
             : -1;
}

extern "C" int fb_jit_region_init(fireball::executable_memory* memory) {
  return fb_jit_memory_init(memory, fireball::region_bytes) &&
         fireball::initialize_common_code(memory);
}

extern "C" int fb_jit_compile_instructions(const fireball::jit_instruction_block* block,
                                           fireball::jit_cache_trace* trace,
                                           fireball::jit_trace_fixups* fixups, std::uint8_t* output,
                                           std::uint32_t capacity) {
  using namespace fireball;
  if (block == nullptr || trace == nullptr || fixups == nullptr || output == nullptr ||
      capacity <= header_bytes)
    return -1;
  fireball::jit_compile_result result{};
  const auto status = compile_instruction_body(
      block->instructions, block->instruction_count, block->next_pc != UINT32_MAX, block->next_pc,
      block->loops_to != UINT32_MAX, block->loops_to, block->byte_span, block->locals.widths.data,
      block->locals.widths.bytes, block->locals.count, block->locals.slot_words,
      block->context_helper, block->helper_target, output + header_bytes, capacity - header_bytes,
      &result);
  if (status != 1) return status;
  trace->head_pc = block->head_pc;
  trace->next_pc = block->next_pc;
  trace->loops_to = block->loops_to;
  return build_trace(output + header_bytes, result.body_bytes, &result, block->helper_target, trace,
                     fixups, output, capacity)
             ? 1
             : -1;
}

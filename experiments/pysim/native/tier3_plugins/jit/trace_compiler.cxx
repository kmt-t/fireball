/** @file @brief 検証済みWASMブロックから固定容量のx64 traceを合成する。 */
#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

#include "common_code_abi.hxx"
#include "jit_internal.hxx"
#include "jit_runtime_apis.hxx"
#include "stencils_x64.hxx"
#include "trace_compiler_abi.hxx"

using namespace fireball::pysim::jit;
using fireball::kMaxBodyBytes;
using fireball::kMaxRelocationConstants;
using fireball::kMaxStackDepth;
using fireball::kMaxTraceInstructions;
using fireball::kMaxTraceOperations;

#ifndef FB_CONF_JIT_X64_TRACE_HEADER_BYTES
#error "x64 JIT trace header size must come from tier1_core/config.py"
#endif
namespace {

#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
fireball::jit_const_pool_diagnostics const_pool_diagnostics{};

std::uint32_t diagnostic_opcode_index(std::uint32_t opcode) {
  return opcode <= 0xFFu ? opcode : 0x100u + (opcode & 0xFFu);
}

struct const_pool_diagnostic_scope {
  fireball::jit_const_pool_diagnostics saved = const_pool_diagnostics;
  bool committed = false;
  ~const_pool_diagnostic_scope() {
    if (!committed) {
      const_pool_diagnostics = saved;
    }
  }
};
#endif

constexpr std::size_t kTraceHeaderBytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::uint32_t kMaxLogicalLocals = 16;
// The normal success slot is removed while composing the next operation body,
// so successful operations fall through without an epilogue or transfer.
// Abort slots target the single trace-level return stub.
constexpr std::size_t kX64Rel32SlotBytes = 5;
constexpr std::size_t kMaxOperationStencilBytes = 512;
constexpr auto kNoLinkSlot = generated::no_link_slot;
static_assert(generated::trace_entry.start_slot_offset == FB_CONF_JIT_X64_TRACE_CHAIN_ENTRY_OFFSET);

constexpr std::uint8_t kWasmI32 = 0x7F;
constexpr std::uint8_t kWasmI64 = 0x7E;
constexpr std::uint8_t kWasmF32 = 0x7D;
constexpr std::uint8_t kWasmF64 = 0x7C;

constexpr int kDrop = 0x1A;
constexpr int kSelect = 0x1B;
constexpr int kLocalGet = 0x20;
constexpr int kLocalSet = 0x21;
constexpr int kLocalTee = 0x22;
constexpr int kI32Const = 0x41;
constexpr int kI64Const = 0x42;
constexpr int kF32Const = 0x43;
constexpr int kF64Const = 0x44;
constexpr int kI32Eqz = 0x45;
constexpr int kI32Eq = 0x46;
constexpr int kI32Ne = 0x47;
constexpr int kI32LtS = 0x48;
constexpr int kI32LtU = 0x49;
constexpr int kI32GtS = 0x4A;
constexpr int kI32GtU = 0x4B;
constexpr int kI32LeS = 0x4C;
constexpr int kI32LeU = 0x4D;
constexpr int kI32GeS = 0x4E;
constexpr int kI32GeU = 0x4F;
constexpr int kI32Add = 0x6A;
constexpr int kI32Sub = 0x6B;
constexpr int kI32Mul = 0x6C;
constexpr int kI32DivS = 0x6D;
constexpr int kI32DivU = 0x6E;
constexpr int kI32RemS = 0x6F;
constexpr int kI32RemU = 0x70;
constexpr int kI32And = 0x71;
constexpr int kI32Or = 0x72;
constexpr int kI32Xor = 0x73;
constexpr int kI32Shl = 0x74;
constexpr int kI32ShrS = 0x75;
constexpr int kI32ShrU = 0x76;
constexpr int kI64Add = 0x7C;
constexpr int kI64Sub = 0x7D;
constexpr int kI64Mul = 0x7E;
constexpr int kF32Add = 0x92;
constexpr int kF32Sub = 0x93;
constexpr int kF32Mul = 0x94;
constexpr int kF32Div = 0x95;
constexpr int kF64Add = 0xA0;
constexpr int kF64Sub = 0xA1;
constexpr int kF64Mul = 0xA2;
constexpr int kF64Div = 0xA3;
constexpr int kFcPrefix = 0xFC;

bool read_unsigned_leb(const std::uint8_t *code, code_ip_t &cursor, code_ip_t end,
                       std::uint32_t &value) {
  value = 0;
  std::uint32_t shift = 0;
  while (cursor < end) {
    const auto byte = code[cursor++];
    const auto payload = static_cast<std::uint32_t>(byte & 0x7Fu);
    const auto contributed = std::min(7u, 32u - shift);
    if (shift < 32u) {
      value |= payload << shift;
    }
    if ((byte & 0x80u) == 0) {
      if (contributed < 7u && (payload >> contributed) != 0) {
        return false;
      }
      return true;
    }
    if (shift >= 28u) {
      return false;
    }
    shift += 7u;
  }
  return false;
}

bool read_signed_leb(const std::uint8_t *code, code_ip_t &cursor, code_ip_t end,
                     std::uint64_t &value, std::uint32_t bits) {
  std::uint64_t decoded = 0;
  std::uint32_t shift = 0;
  bool negative = false;
  while (cursor < end) {
    const auto byte = code[cursor++];
    const auto payload = static_cast<std::uint32_t>(byte & 0x7Fu);
    const auto contributed = std::min(7u, bits - shift);
    if (shift < 64u) {
      decoded |= static_cast<std::uint64_t>(payload) << shift;
    }
    negative = (byte & 0x40u) != 0;
    if ((byte & 0x80u) == 0) {
      shift += contributed;
      if (negative && shift < 64u) {
        decoded |= (~std::uint64_t{0}) << shift;
      }
      if (bits == 32u) {
        decoded = static_cast<std::uint32_t>(decoded);
      }
      value = decoded;
      return true;
    }
    if (shift >= bits - 7u) {
      return false;
    }
    shift += 7u;
  }
  return false;
}

enum class relocation_kind : std::uint8_t { value, trace_header };

struct relocation_entry {
  std::uint64_t value = 0;
  std::uint8_t width = 0;
  relocation_kind kind = relocation_kind::value;
};

std::size_t widened_branch_size(const std::uint8_t *stencil_code,
                                const stencil_branch_fixup_view &branch) {
  if (branch.instruction_size == 2) {
    const auto opcode = stencil_code[branch.instruction_offset];
    if (opcode >= 0x70 && opcode <= 0x7f) {
      return 6;
    }
    if (opcode == 0xeb) {
      return 5;
    }
    return 0;
  }
  if (branch.instruction_size == 5 && stencil_code[branch.instruction_offset] == 0xe9) {
    return 5;
  }
  if (branch.instruction_size == 6 && stencil_code[branch.instruction_offset] == 0x0f &&
      stencil_code[branch.instruction_offset + 1] >= 0x80 &&
      stencil_code[branch.instruction_offset + 1] <= 0x8f) {
    return 6;
  }
  return 0;
}

using branch_prefix_table = std::array<std::size_t, kMaxOperationStencilBytes / 2 + 1>;

bool build_branch_prefix(const stencil_view &stencil, const std::uint8_t *stencil_code,
                         branch_prefix_table &prefix) {
  if (!stencil.branch_range_valid() || stencil.branch_count + 1 > prefix.size()) {
    return false;
  }
  prefix[0] = 0;
  std::size_t previous_end = 0;
  for (std::size_t index = 0; index < stencil.branch_count; ++index) {
    const auto branch = stencil.branch_fixup(index);
    if (branch.instruction_offset < previous_end || branch.instruction_offset > stencil.size ||
        branch.instruction_size > stencil.size - branch.instruction_offset) {
      return false;
    }
    const auto expanded_size = widened_branch_size(stencil_code, branch);
    if (expanded_size == 0 || expanded_size < branch.instruction_size ||
        prefix[index] >
            std::numeric_limits<std::size_t>::max() - (expanded_size - branch.instruction_size)) {
      return false;
    }
    previous_end = branch.instruction_offset + branch.instruction_size;
    prefix[index + 1] = prefix[index] + expanded_size - branch.instruction_size;
  }
  return true;
}

bool map_stencil_offset(std::size_t stencil_offset, const stencil_view &stencil,
                        const branch_prefix_table &prefix, std::size_t &mapped_offset) {
  if (stencil_offset > stencil.size || !stencil.branch_range_valid() ||
      stencil.branch_count + 1 > prefix.size()) {
    return false;
  }
  std::size_t first = 0;
  std::size_t last = stencil.branch_count;
  while (first < last) {
    const auto middle = first + (last - first) / 2;
    const auto branch = stencil.branch_fixup(middle);
    if (branch.instruction_offset + branch.instruction_size <= stencil_offset) {
      first = middle + 1;
    } else {
      last = middle;
    }
  }
  if (stencil_offset > std::numeric_limits<std::size_t>::max() - prefix[first]) {
    return false;
  }
  mapped_offset = stencil_offset + prefix[first];
  return true;
}

bool map_linked_offset(std::size_t stencil_offset, std::size_t next_slot_offset,
                       std::size_t downstream_size, std::size_t &mapped_offset) {
  if (stencil_offset <= next_slot_offset) {
    mapped_offset = stencil_offset;
    return true;
  }
  if (stencil_offset < next_slot_offset + kX64Rel32SlotBytes) {
    return false;
  }
  if (downstream_size < kX64Rel32SlotBytes ||
      stencil_offset >
          std::numeric_limits<std::size_t>::max() - (downstream_size - kX64Rel32SlotBytes)) {
    return false;
  }
  mapped_offset = stencil_offset + downstream_size - kX64Rel32SlotBytes;
  return true;
}

bool write_rel32(std::uint8_t *code, std::size_t code_size, std::size_t displacement_offset,
                 std::size_t next_offset) {
  if (next_offset > static_cast<std::size_t>(std::numeric_limits<std::int64_t>::max()) ||
      displacement_offset > static_cast<std::size_t>(std::numeric_limits<std::int64_t>::max()) ||
      displacement_offset > code_size || sizeof(std::int32_t) > code_size - displacement_offset) {
    return false;
  }
  const auto displacement =
      static_cast<std::int64_t>(next_offset) - static_cast<std::int64_t>(displacement_offset + 4);
  if (displacement < std::numeric_limits<std::int32_t>::min() ||
      displacement > std::numeric_limits<std::int32_t>::max()) {
    return false;
  }
  const auto encoded = static_cast<std::uint32_t>(static_cast<std::int32_t>(displacement));
  for (std::size_t byte = 0; byte < sizeof(encoded); ++byte) {
    code[displacement_offset + byte] = static_cast<std::uint8_t>(encoded >> (byte * 8));
  }
  return true;
}

bool expand_stencil_branches(const stencil_view &stencil, const std::uint8_t *stencil_code,
                             std::uint8_t *expanded_code, std::size_t expanded_capacity,
                             std::size_t &expanded_size) {
  if (!stencil.branch_range_valid()) {
    return false;
  }
  std::size_t source_cursor = 0;
  std::size_t output_cursor = 0;
  for (std::size_t index = 0; index < stencil.branch_count; ++index) {
    const auto branch = stencil.branch_fixup(index);
    if (branch.instruction_offset < source_cursor || branch.instruction_offset > stencil.size ||
        branch.instruction_size > stencil.size - branch.instruction_offset ||
        branch.target_offset >= stencil.size) {
      return false;
    }
    const auto gap = branch.instruction_offset - source_cursor;
    if (gap > expanded_capacity - output_cursor) {
      return false;
    }
    std::copy_n(stencil_code + source_cursor, gap, expanded_code + output_cursor);
    output_cursor += gap;
    const auto widened_size = widened_branch_size(stencil_code, branch);
    if (widened_size == 0 || widened_size > expanded_capacity - output_cursor) {
      return false;
    }
    const auto opcode = stencil_code[branch.instruction_offset];
    if (branch.instruction_size == 2 && opcode >= 0x70 && opcode <= 0x7f) {
      expanded_code[output_cursor] = 0x0f;
      expanded_code[output_cursor + 1] = static_cast<std::uint8_t>(opcode + 0x10);
      std::fill(expanded_code + output_cursor + 2, expanded_code + output_cursor + widened_size, 0);
    } else if (branch.instruction_size == 2 && opcode == 0xeb) {
      expanded_code[output_cursor] = 0xe9;
      std::fill(expanded_code + output_cursor + 1, expanded_code + output_cursor + widened_size, 0);
    } else {
      std::copy_n(stencil_code + branch.instruction_offset, widened_size,
                  expanded_code + output_cursor);
    }
    source_cursor = branch.instruction_offset + branch.instruction_size;
    output_cursor += widened_size;
  }
  if (source_cursor > stencil.size ||
      stencil.size - source_cursor > expanded_capacity - output_cursor) {
    return false;
  }
  std::copy_n(stencil_code + source_cursor, stencil.size - source_cursor,
              expanded_code + output_cursor);
  expanded_size = output_cursor + stencil.size - source_cursor;
  return true;
}

enum class local_memory_operation : std::uint8_t {
  load_narrow,
  load_wide,
  store_narrow,
  store_wide,
};

bool encode_local_memory_instruction(local_memory_operation operation, std::uint32_t byte_offset,
                                     std::array<std::uint8_t, 4> &encoded) {
  if ((byte_offset & 3u) != 0 || byte_offset > 127) {
    return false;
  }
  switch (operation) {
  case local_memory_operation::load_narrow:
    encoded = {0x41, 0x8b, 0x43, static_cast<std::uint8_t>(byte_offset)};
    return true;
  case local_memory_operation::load_wide:
    encoded = {0x49, 0x8b, 0x43, static_cast<std::uint8_t>(byte_offset)};
    return true;
  case local_memory_operation::store_narrow:
    encoded = {0x41, 0x89, 0x43, static_cast<std::uint8_t>(byte_offset)};
    return true;
  case local_memory_operation::store_wide:
    encoded = {0x49, 0x89, 0x43, static_cast<std::uint8_t>(byte_offset)};
    return true;
  }
  return false;
}

/** @brief 借用出力バッファと固定容量の操作・再配置情報を保持する。 */
struct trace_builder {
  std::uint8_t *body;
  std::size_t body_capacity;
  std::size_t body_size = 0;
  std::array<stencil_view, kMaxTraceOperations> operations{};
  std::array<std::uint64_t, kMaxTraceOperations> patch_values{};
  std::array<std::array<std::uint8_t, 4>, kMaxTraceOperations> local_instructions{};
  std::size_t operation_count = 0;
  std::array<relocation_entry, kMaxRelocationConstants> relocations{};
  std::size_t relocation_count = 0;
  std::size_t relocation_bytes = 0;

  bool append_node(stencil_view stencil) {
    if (stencil.symbol == nullptr || stencil.size == 0 || operation_count >= operations.size() ||
        (stencil.patch_slot_width == 0) != (stencil.patch_slot_offset == kNoLinkSlot) ||
        (stencil.patch_slot_width != 0 &&
         (stencil.patch_slot_offset > stencil.size ||
          stencil.patch_slot_width > stencil.size - stencil.patch_slot_offset)) ||
        (stencil.instruction_slot_width == 0) != (stencil.instruction_slot_offset == kNoLinkSlot) ||
        (stencil.instruction_slot_width != 0 &&
         (stencil.instruction_slot_width != 4 || stencil.instruction_slot_offset > stencil.size ||
          stencil.instruction_slot_width > stencil.size - stencil.instruction_slot_offset))) {
      return false;
    }
    operations[operation_count++] = stencil;
    return true;
  }

  bool append_patched(stencil_view stencil, std::uint64_t value, std::uint8_t width) {
    if (stencil.instruction_slot_width != 0 || stencil.patch_slot_width != width ||
        !append_node(stencil)) {
      return false;
    }
    patch_values[operation_count - 1] = value;
    return true;
  }

  bool append_local(stencil_view stencil, std::uint32_t byte_offset,
                    local_memory_operation operation, std::uint64_t patch_value = 0,
                    std::uint8_t patch_width = 0) {
    std::array<std::uint8_t, 4> encoded{};
    if (stencil.instruction_slot_width != encoded.size() ||
        stencil.patch_slot_width != patch_width ||
        !encode_local_memory_instruction(operation, byte_offset, encoded) ||
        !append_node(stencil)) {
      return false;
    }
    local_instructions[operation_count - 1] = encoded;
    patch_values[operation_count - 1] = patch_value;
    return true;
  }

  bool add_value(std::uint64_t value, std::uint8_t width) {
    if ((width != 1 && width != 2 && width != 4 && width != 8) ||
        relocation_count == relocations.size() || relocation_bytes > body_capacity ||
        width > body_capacity - relocation_bytes) {
      return false;
    }
    relocations[relocation_count++] = {value, width, relocation_kind::value};
    relocation_bytes += width;
    return true;
  }

  bool add_trace_header_reference() {
    if (relocation_count == relocations.size() || relocation_bytes > body_capacity ||
        4 > body_capacity - relocation_bytes) {
      return false;
    }
    relocations[relocation_count++] = {0, 4, relocation_kind::trace_header};
    relocation_bytes += 4;
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
    const_pool_diagnostics.item_bytes[0] += 4;
#endif
    return true;
  }

  bool begin_trace() { return add_trace_header_reference(); }

  bool append_runtime_api(std::uint8_t slot, code_ip_t source_ip,
                          const std::uint64_t *immediates = nullptr,
                          const std::uint8_t *immediate_widths = nullptr,
                          std::uint8_t immediate_count = 0, std::uint32_t wasm_opcode = 0,
                          annotation_offset_t interpreter_rel_after =
                              std::numeric_limits<annotation_offset_t>::max()) {
#if !defined(FB_CONF_JIT_BLOCK_COUNTERS) || !FB_CONF_JIT_BLOCK_COUNTERS
    static_cast<void>(wasm_opcode);
#endif
    if (immediate_count > 3 ||
        (immediate_count != 0 && (immediates == nullptr || immediate_widths == nullptr))) {
      return false;
    }
    const auto stencil = generated_stencil(asm_generated::runtime_api);
    std::uint8_t immediate_bytes = 0;
    for (std::uint8_t index = 0; index < immediate_count; ++index) {
      const auto width = immediate_widths[index];
      if ((width != 1 && width != 2 && width != 4 && width != 8) ||
          immediate_bytes > UINT8_MAX - width) {
        return false;
      }
      immediate_bytes = static_cast<std::uint8_t>(immediate_bytes + width);
    }
    if (immediate_bytes > UINT8_MAX - sizeof(annotation_offset_t)) {
      return false;
    }
    immediate_bytes = static_cast<std::uint8_t>(immediate_bytes + sizeof(annotation_offset_t));
    if (!append_node(stencil) || !add_value(slot, 1) || !add_value(immediate_count, 1) ||
        !add_value(immediate_bytes, 1) || !add_value(source_ip, sizeof(code_ip_t))) {
      return false;
    }
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
    const_pool_diagnostics.item_bytes[1] += 2;
    const_pool_diagnostics.item_bytes[2] += sizeof(code_ip_t);
    const_pool_diagnostics.item_bytes[5] += 1;
    ++const_pool_diagnostics.runtime_api_count;
    ++const_pool_diagnostics.runtime_api_opcodes[diagnostic_opcode_index(wasm_opcode)];
#endif
    for (std::uint8_t index = 0; index < immediate_count; ++index) {
      if (!add_value(immediates[index], immediate_widths[index])) {
        return false;
      }
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
      const_pool_diagnostics.item_bytes[3] += immediate_widths[index];
#endif
    }
    if (!add_value(interpreter_rel_after, sizeof(interpreter_rel_after))) {
      return false;
    }
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
    const_pool_diagnostics.item_bytes[6] += sizeof(interpreter_rel_after);
#endif
    return true;
  }

  bool append_terminal_runtime_api(std::uint8_t slot, code_ip_t source_ip,
                                   annotation_offset_t annotation_offset, bool has_annotation,
                                   std::uint32_t wasm_opcode,
                                   annotation_offset_t interpreter_rel_after) {
    const std::uint8_t count = has_annotation ? 1 : 0;
    const std::uint8_t payload_bytes = has_annotation ? sizeof(annotation_offset_t) : 0;
    const std::uint64_t annotation_value = annotation_offset;
    if (!append_runtime_api(slot, source_ip, has_annotation ? &annotation_value : nullptr,
                            has_annotation ? &payload_bytes : nullptr, count, wasm_opcode,
                            interpreter_rel_after)) {
      return false;
    }
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
    if (has_annotation) {
      const_pool_diagnostics.item_bytes[3] -= sizeof(annotation_offset_t);
      const_pool_diagnostics.item_bytes[6] += sizeof(annotation_offset_t);
    }
#endif
    return true;
  }

  bool append_cps_call(std::uint8_t slot, code_ip_t source_delta,
                       annotation_offset_t annotation_offset) {
    const auto stencil = generated_stencil(asm_generated::cps_call);
    return append_node(stencil) && add_value(slot, 1) && add_value(0, 1) && add_value(4, 1) &&
           add_value(source_delta, sizeof(source_delta)) &&
           add_value(annotation_offset, sizeof(annotation_offset));
  }

  bool append_linked(stencil_view stencil) {
    if (stencil.patch_slot_width != 0 || stencil.instruction_slot_width != 0 ||
        !append_node(stencil)) {
      return false;
    }
    return true;
  }

  bool finish(fireball::jit_compile_result &result) {
    if (body == nullptr) {
      return false;
    }

    const auto entry_stencil = generated_stencil(generated::trace_entry);
    const auto exit_stencil = generated_stencil(generated::trace_exit);
    const auto success_stencil = generated_stencil(generated::trace_return_success);
    const auto abort_stencil = generated_stencil(generated::trace_return_abort);
    branch_prefix_table branch_prefix{};
    if (entry_stencil.size == 0 || entry_stencil.size > scaffold_scratch.size() ||
        exit_stencil.size == 0 || exit_stencil.size > scaffold_scratch.size() ||
        success_stencil.size == 0 || success_stencil.size > body_capacity) {
      return false;
    }

    std::size_t expanded_size = 0;
    const auto *entry_code = generated::fb_jit_stencils_start.data() + entry_stencil.code_offset;
    if (entry_stencil.start_slot_offset == kNoLinkSlot ||
        entry_stencil.exit_slot_offset == kNoLinkSlot || !entry_stencil.branch_range_valid() ||
        entry_stencil.start_slot_offset > entry_stencil.size ||
        kX64Rel32SlotBytes > entry_stencil.size - entry_stencil.start_slot_offset ||
        entry_stencil.exit_slot_offset > entry_stencil.size ||
        kX64Rel32SlotBytes > entry_stencil.size - entry_stencil.exit_slot_offset ||
        entry_code[entry_stencil.start_slot_offset] != 0xe8 ||
        entry_code[entry_stencil.exit_slot_offset] != 0xe9 ||
        !build_branch_prefix(entry_stencil, entry_code, branch_prefix) ||
        !expand_stencil_branches(entry_stencil, entry_code, scaffold_scratch.data(),
                                 scaffold_scratch.size(), expanded_size)) {
      return false;
    }

    std::size_t expanded_start_slot = 0;
    std::size_t expanded_exit_slot = 0;
    if (!map_stencil_offset(entry_stencil.start_slot_offset, entry_stencil, branch_prefix,
                            expanded_start_slot) ||
        !map_stencil_offset(entry_stencil.exit_slot_offset, entry_stencil, branch_prefix,
                            expanded_exit_slot) ||
        expanded_exit_slot > expanded_size ||
        kX64Rel32SlotBytes > expanded_size - expanded_exit_slot) {
      return false;
    }
    const auto wrapper_size = expanded_size - kX64Rel32SlotBytes + exit_stencil.size;
    if (wrapper_size > scaffold_code.size() || wrapper_size > body_capacity) {
      return false;
    }
    const auto sequence_capacity = body_capacity - wrapper_size;
    if (success_stencil.size > sequence_capacity) {
      return false;
    }

    std::copy_n(scaffold_scratch.data(), expanded_exit_slot, scaffold_code.data());
    std::copy_n(generated::fb_jit_stencils_start.data() + exit_stencil.code_offset,
                exit_stencil.size, scaffold_code.data() + expanded_exit_slot);
    const auto wrapper_suffix_offset = expanded_exit_slot + kX64Rel32SlotBytes;
    const auto wrapper_suffix_size = expanded_size - wrapper_suffix_offset;
    std::copy_n(scaffold_scratch.data() + wrapper_suffix_offset, wrapper_suffix_size,
                scaffold_code.data() + expanded_exit_slot + exit_stencil.size);

    for (std::size_t branch_index = 0; branch_index < entry_stencil.branch_count; ++branch_index) {
      const auto branch = entry_stencil.branch_fixup(branch_index);
      std::size_t source_offset = 0;
      std::size_t target_offset = 0;
      std::size_t wrapper_source = 0;
      std::size_t wrapper_target = 0;
      if (!map_stencil_offset(branch.instruction_offset, entry_stencil, branch_prefix,
                              source_offset) ||
          !map_stencil_offset(branch.target_offset, entry_stencil, branch_prefix, target_offset) ||
          !map_linked_offset(source_offset, expanded_exit_slot, exit_stencil.size,
                             wrapper_source) ||
          !map_linked_offset(target_offset, expanded_exit_slot, exit_stencil.size,
                             wrapper_target)) {
        return false;
      }
      const auto instruction_size = widened_branch_size(entry_code, branch);
      const auto opcode = entry_code[branch.instruction_offset];
      const auto displacement_offset =
          branch.instruction_size == 2 ? (opcode == 0xeb ? 1u : 2u) : (opcode == 0xe9 ? 1u : 2u);
      if (wrapper_source > wrapper_size || instruction_size > wrapper_size - wrapper_source ||
          displacement_offset + sizeof(std::int32_t) > instruction_size ||
          !write_rel32(scaffold_code.data(), wrapper_size, wrapper_source + displacement_offset,
                       wrapper_target)) {
        return false;
      }
    }

    std::size_t wrapper_start_slot = 0;
    if (!map_linked_offset(expanded_start_slot, expanded_exit_slot, exit_stencil.size,
                           wrapper_start_slot) ||
        wrapper_start_slot > wrapper_size ||
        kX64Rel32SlotBytes > wrapper_size - wrapper_start_slot ||
        scaffold_code[wrapper_start_slot] != 0xe8) {
      return false;
    }

    std::array<std::size_t, kMaxTraceOperations> expanded_sizes{};
    std::array<std::size_t, kMaxTraceOperations> expanded_next_slots{};
    std::array<std::size_t, kMaxTraceOperations> expanded_abort_slots{};
    std::array<std::size_t, kMaxTraceOperations> operation_starts{};
    std::array<std::size_t, kMaxTraceOperations> downstream_sizes{};
    std::array<std::size_t, kMaxTraceOperations> abort_slots{};
    std::size_t abort_slot_count = 0;
    std::size_t sequence_size = success_stencil.size;

    const auto expand_operation = [&](std::size_t index, std::size_t &operation_size) {
      const auto &stencil = operations[index];
      if (stencil.symbol == nullptr || stencil.size == 0 || stencil.size > stencil_scratch.size() ||
          stencil.next_slot_offset == kNoLinkSlot || stencil.next_slot_offset > stencil.size ||
          kX64Rel32SlotBytes > stencil.size - stencil.next_slot_offset ||
          stencil.image_start == nullptr) {
        return false;
      }
      const auto *stencil_code = stencil.image_start + stencil.code_offset;
      if (stencil_code[stencil.next_slot_offset] != 0xe9 ||
          !build_branch_prefix(stencil, stencil_code, branch_prefix) ||
          !expand_stencil_branches(stencil, stencil_code, stencil_scratch.data(),
                                   stencil_scratch.size(), operation_size)) {
        return false;
      }
      if (stencil.patch_slot_width != 0) {
        std::size_t patch_offset = 0;
        if (!map_stencil_offset(stencil.patch_slot_offset, stencil, branch_prefix, patch_offset) ||
            patch_offset > operation_size ||
            stencil.patch_slot_width > operation_size - patch_offset) {
          return false;
        }
        const auto patch_value = patch_values[index];
        for (std::uint8_t byte = 0; byte < stencil.patch_slot_width; ++byte) {
          stencil_scratch[patch_offset + byte] =
              static_cast<std::uint8_t>(patch_value >> (byte * 8));
        }
      }
      if (stencil.instruction_slot_width != 0) {
        std::size_t instruction_offset = 0;
        if (!map_stencil_offset(stencil.instruction_slot_offset, stencil, branch_prefix,
                                instruction_offset) ||
            instruction_offset > operation_size ||
            stencil.instruction_slot_width > operation_size - instruction_offset) {
          return false;
        }
        std::copy_n(local_instructions[index].data(), stencil.instruction_slot_width,
                    stencil_scratch.data() + instruction_offset);
      }
      return true;
    };

    for (std::size_t index = 0; index < operation_count; ++index) {
      const auto &stencil = operations[index];
      if (stencil.image_start == nullptr) {
        return false;
      }
      const auto *stencil_code = stencil.image_start + stencil.code_offset;
      std::size_t operation_size = 0;
      std::size_t next_slot = 0;
      std::size_t abort_slot = kNoLinkSlot;
      if (!expand_operation(index, operation_size) ||
          !map_stencil_offset(stencil.next_slot_offset, stencil, branch_prefix, next_slot) ||
          next_slot > operation_size || kX64Rel32SlotBytes > operation_size - next_slot) {
        return false;
      }
      if (stencil.abort_slot_offset != kNoLinkSlot &&
          (stencil.abort_slot_offset > stencil.size ||
           kX64Rel32SlotBytes > stencil.size - stencil.abort_slot_offset ||
           stencil_code[stencil.abort_slot_offset] != 0xe9 ||
           !map_stencil_offset(stencil.abort_slot_offset, stencil, branch_prefix, abort_slot) ||
           abort_slot > operation_size || kX64Rel32SlotBytes > operation_size - abort_slot)) {
        return false;
      }
      expanded_sizes[index] = operation_size;
      expanded_next_slots[index] = next_slot;
      expanded_abort_slots[index] = abort_slot;
      if (abort_slot != kNoLinkSlot) {
        if (abort_slot_count == abort_slots.size()) {
          return false;
        }
        ++abort_slot_count;
      }
      const auto removed_slot_bytes = kX64Rel32SlotBytes;
      if (operation_size < removed_slot_bytes ||
          operation_size - removed_slot_bytes > sequence_capacity - sequence_size) {
        return false;
      }
      sequence_size += operation_size - removed_slot_bytes;
    }

    std::size_t prefix_after = 0;
    std::size_t suffix_after = 0;
    for (std::size_t remaining = operation_count; remaining != 0; --remaining) {
      const auto index = remaining - 1;
      const auto suffix_size =
          expanded_sizes[index] - (expanded_next_slots[index] + kX64Rel32SlotBytes);
      if (prefix_after > sequence_capacity ||
          success_stencil.size > sequence_capacity - prefix_after ||
          suffix_after > sequence_capacity - prefix_after - success_stencil.size) {
        return false;
      }
      downstream_sizes[index] = prefix_after + success_stencil.size + suffix_after;
      if (expanded_next_slots[index] > sequence_capacity - prefix_after ||
          suffix_size > sequence_capacity - suffix_after) {
        return false;
      }
      prefix_after += expanded_next_slots[index];
      suffix_after += suffix_size;
    }
    if (prefix_after != sequence_size - success_stencil.size - suffix_after ||
        sequence_size > sequence_capacity) {
      return false;
    }

    auto *trace_body = body + wrapper_size;
    std::size_t body_cursor = 0;
    for (std::size_t index = 0; index < operation_count; ++index) {
      std::size_t operation_size = 0;
      if (!expand_operation(index, operation_size) || operation_size != expanded_sizes[index]) {
        return false;
      }
      operation_starts[index] = body_cursor;
      const auto prefix_size = expanded_next_slots[index];
      if (prefix_size > sequence_size - body_cursor) {
        return false;
      }
      std::copy_n(stencil_scratch.data(), prefix_size, trace_body + body_cursor);
      body_cursor += prefix_size;
    }
    const auto success_offset = body_cursor;
    if (success_offset != prefix_after || success_stencil.size > sequence_size - body_cursor) {
      return false;
    }
    std::copy_n(generated::fb_jit_stencils_start.data() + success_stencil.code_offset,
                success_stencil.size, trace_body + body_cursor);
    body_cursor += success_stencil.size;

    for (std::size_t remaining = operation_count; remaining != 0; --remaining) {
      const auto index = remaining - 1;
      std::size_t operation_size = 0;
      if (!expand_operation(index, operation_size) || operation_size != expanded_sizes[index]) {
        return false;
      }
      const auto suffix_offset = expanded_next_slots[index] + kX64Rel32SlotBytes;
      const auto suffix_size = operation_size - suffix_offset;
      if (suffix_size > sequence_size - body_cursor) {
        return false;
      }
      std::copy_n(stencil_scratch.data() + suffix_offset, suffix_size, trace_body + body_cursor);
      body_cursor += suffix_size;
    }
    if (body_cursor != sequence_size) {
      return false;
    }

    for (std::size_t index = 0; index < operation_count; ++index) {
      const auto &stencil = operations[index];
      const auto *stencil_code = stencil.image_start + stencil.code_offset;
      if (!build_branch_prefix(stencil, stencil_code, branch_prefix)) {
        return false;
      }
      const auto next_slot = expanded_next_slots[index];
      const auto downstream_size = downstream_sizes[index];
      for (std::size_t branch_index = 0; branch_index < stencil.branch_count; ++branch_index) {
        const auto branch = stencil.branch_fixup(branch_index);
        std::size_t source_offset = 0;
        std::size_t target_offset = 0;
        std::size_t linked_source = 0;
        std::size_t linked_target = 0;
        if (!map_stencil_offset(branch.instruction_offset, stencil, branch_prefix, source_offset) ||
            !map_stencil_offset(branch.target_offset, stencil, branch_prefix, target_offset) ||
            !map_linked_offset(source_offset, next_slot, downstream_size, linked_source) ||
            !map_linked_offset(target_offset, next_slot, downstream_size, linked_target)) {
          return false;
        }
        const auto instruction_size = widened_branch_size(stencil_code, branch);
        const auto opcode = stencil_code[branch.instruction_offset];
        const auto displacement_offset =
            branch.instruction_size == 2 ? (opcode == 0xeb ? 1u : 2u) : (opcode == 0xe9 ? 1u : 2u);
        const auto source = operation_starts[index] + linked_source;
        const auto target = operation_starts[index] + linked_target;
        if (source > sequence_size || instruction_size > sequence_size - source ||
            displacement_offset + sizeof(std::int32_t) > instruction_size ||
            !write_rel32(trace_body, sequence_size, source + displacement_offset, target)) {
          return false;
        }
      }
      if (expanded_abort_slots[index] != kNoLinkSlot) {
        std::size_t linked_abort_slot = 0;
        if (!map_linked_offset(expanded_abort_slots[index], next_slot, downstream_size,
                               linked_abort_slot)) {
          return false;
        }
        const auto absolute_slot = wrapper_size + operation_starts[index] + linked_abort_slot;
        if (absolute_slot > body_capacity || kX64Rel32SlotBytes > body_capacity - absolute_slot) {
          return false;
        }
        abort_slots[index] = absolute_slot;
      }
    }

    const auto *success_code =
        generated::fb_jit_stencils_start.data() + success_stencil.code_offset;
#if defined(_WIN32)
    constexpr std::array<std::uint8_t, 4> kSuccessStatusInstruction{0x49, 0x83, 0xc8, 0x01};
#else
    constexpr std::array<std::uint8_t, 4> kSuccessStatusInstruction{0x48, 0x83, 0xca, 0x01};
#endif
    if (success_stencil.size <= kSuccessStatusInstruction.size() ||
        !std::equal(kSuccessStatusInstruction.begin(), kSuccessStatusInstruction.end(),
                    success_code)) {
      return false;
    }

    std::size_t terminal_size = 0;
    if (abort_slot_count != 0) {
      const auto *abort_code = generated::fb_jit_stencils_start.data() + abort_stencil.code_offset;
      if (abort_stencil.size == 0 || abort_stencil.next_slot_offset == kNoLinkSlot ||
          abort_stencil.next_slot_offset > abort_stencil.size ||
          kX64Rel32SlotBytes > abort_stencil.size - abort_stencil.next_slot_offset ||
          abort_code[abort_stencil.next_slot_offset] != 0xe9 ||
          abort_stencil.size > sequence_capacity - sequence_size) {
        return false;
      }
      terminal_size = abort_stencil.size;
      std::copy_n(abort_code, abort_stencil.size, trace_body + sequence_size);
      const auto abort_tail = sequence_size + abort_stencil.next_slot_offset;
      const auto shared_return_tail = success_offset + kSuccessStatusInstruction.size();
      if (!write_rel32(trace_body, sequence_size + terminal_size, abort_tail + 1,
                       shared_return_tail)) {
        return false;
      }
      const auto abort_target = wrapper_size + sequence_size;
      const auto total_body_size = wrapper_size + sequence_size + terminal_size;
      for (std::size_t index = 0; index < operation_count; ++index) {
        if (expanded_abort_slots[index] == kNoLinkSlot) {
          continue;
        }
        const auto slot_offset = abort_slots[index];
        if (slot_offset > total_body_size || kX64Rel32SlotBytes > total_body_size - slot_offset ||
            body[slot_offset] != 0xe9 ||
            !write_rel32(body, total_body_size, slot_offset + 1, abort_target)) {
          return false;
        }
      }
    }

    body_size = wrapper_size + sequence_size + terminal_size;
    if (body_size > body_capacity ||
        !write_rel32(scaffold_code.data(), wrapper_size, wrapper_start_slot + 1, wrapper_size)) {
      return false;
    }
    std::copy_n(scaffold_code.data(), wrapper_size, body);

    constexpr auto pool_alignment = alignof(std::uintptr_t);
    if (body_capacity < relocation_bytes + pool_alignment - 1 ||
        body_size > body_capacity - relocation_bytes - (pool_alignment - 1)) {
      return false;
    }

    const auto pool_offset =
        (kTraceHeaderBytes + body_size + pool_alignment - 1) & ~(pool_alignment - 1);
    const auto pool_body_offset = pool_offset - kTraceHeaderBytes;
    if (pool_offset < kTraceHeaderBytes || pool_body_offset > body_capacity ||
        relocation_bytes > body_capacity - pool_body_offset ||
        pool_offset + relocation_bytes > std::numeric_limits<std::uint16_t>::max()) {
      return false;
    }
    std::fill(body + body_size, body + pool_body_offset, 0);

    std::size_t pool_cursor = 0;
    for (std::size_t index = 0; index < relocation_count; ++index) {
      const auto &entry = relocations[index];
      std::uint64_t value = entry.value;
      if (entry.kind == relocation_kind::trace_header) {
        // build_trace inserts boundary metadata between the trace body and
        // this pool, so account for it when encoding header-relative slots.
        const auto slot_offset = pool_offset + fireball::kTraceBoundaryMetadataBytes + pool_cursor;
        if (slot_offset > static_cast<std::uint64_t>(std::numeric_limits<std::int32_t>::max())) {
          return false;
        }
        value = static_cast<std::uint32_t>(-static_cast<std::int32_t>(slot_offset));
      }
      for (std::uint8_t byte = 0; byte < entry.width; ++byte) {
        body[pool_body_offset + pool_cursor + byte] =
            static_cast<std::uint8_t>(value >> (byte * 8));
      }
      pool_cursor += entry.width;
    }
    result.body_bytes = static_cast<std::uint32_t>(pool_body_offset);
    result.max_stack_words = 0;
    result.result_words = 0;
    result.relocation_bytes = static_cast<std::uint32_t>(relocation_bytes);
    return true;
  }

  std::array<std::uint8_t, kMaxOperationStencilBytes> stencil_scratch{};
  std::array<std::uint8_t, kMaxOperationStencilBytes> scaffold_scratch{};
  std::array<std::uint8_t, kMaxOperationStencilBytes> scaffold_code{};
};

struct stack_model {
  std::array<std::uint8_t, kMaxStackDepth> widths{};
  std::size_t value_count = 0;
  jit_stack_words_t word_count = 0;
  jit_stack_words_t max_words = 0;

  bool push(std::uint8_t width) {
    if ((width != 1 && width != 2) || value_count == widths.size() ||
        width > kMaxStackDepth - word_count) {
      return false;
    }
    widths[value_count++] = width;
    word_count += width;
    max_words = std::max(max_words, word_count);
    return true;
  }

  bool top_is(std::uint8_t width) const {
    return value_count != 0 && widths[value_count - 1] == width;
  }

  bool pop(std::uint8_t width) {
    if (!top_is(width)) {
      return false;
    }
    --value_count;
    word_count -= width;
    return true;
  }

  bool binary(std::uint8_t width) {
    if (value_count < 2 || widths[value_count - 1] != width || widths[value_count - 2] != width) {
      return false;
    }
    value_count -= 2;
    word_count -= 2 * width;
    return push(width);
  }

  bool unary(std::uint8_t input_width, std::uint8_t output_width) {
    return pop(input_width) && push(output_width);
  }

  bool binary_to(std::uint8_t input_width, std::uint8_t output_width) {
    return pop(input_width) && pop(input_width) && push(output_width);
  }

  bool pop_any() { return value_count != 0 && pop(widths[value_count - 1]); }

  bool select() {
    if (value_count < 3 || widths[value_count - 2] != widths[value_count - 3]) {
      return false;
    }
    const auto width = widths[value_count - 2];
    return pop(1) && pop(width) && pop(width) && push(width);
  }
};

stencil_view get_stencil(generated::stencil_range range) { return generated_stencil(range); }

stencil_view get_stencil(asm_generated::stencil_range range) { return generated_stencil(range); }

stencil_view i32_binary_stencil(int op) {
  switch (op) {
  case kI32Add:
    return get_stencil(asm_generated::i32_add);
  case kI32Sub:
    return get_stencil(asm_generated::i32_sub);
  case kI32Mul:
    return get_stencil(asm_generated::i32_mul);
  case kI32DivS:
    return get_stencil(generated::i32_div_s);
  case kI32DivU:
    return get_stencil(generated::i32_div_u);
  case kI32RemS:
    return get_stencil(generated::i32_rem_s);
  case kI32RemU:
    return get_stencil(generated::i32_rem_u);
  case kI32And:
    return get_stencil(asm_generated::i32_and);
  case kI32Or:
    return get_stencil(asm_generated::i32_or);
  case kI32Xor:
    return get_stencil(asm_generated::i32_xor);
  case kI32Shl:
    return get_stencil(asm_generated::i32_shl);
  case kI32ShrS:
    return get_stencil(asm_generated::i32_shr_s);
  case kI32ShrU:
    return get_stencil(asm_generated::i32_shr_u);
  case kI32Eq:
    return get_stencil(asm_generated::i32_eq);
  case kI32Ne:
    return get_stencil(asm_generated::i32_ne);
  case kI32LtS:
    return get_stencil(asm_generated::i32_lt_s);
  case kI32LtU:
    return get_stencil(asm_generated::i32_lt_u);
  case kI32GtS:
    return get_stencil(asm_generated::i32_gt_s);
  case kI32GtU:
    return get_stencil(asm_generated::i32_gt_u);
  case kI32LeS:
    return get_stencil(asm_generated::i32_le_s);
  case kI32LeU:
    return get_stencil(asm_generated::i32_le_u);
  case kI32GeS:
    return get_stencil(asm_generated::i32_ge_s);
  case kI32GeU:
    return get_stencil(asm_generated::i32_ge_u);
  default:
    return {};
  }
}

stencil_view i32_immediate_stencil(int op) {
  switch (op) {
  case kI32Add:
    return get_stencil(asm_generated::i32_add_immediate);
  case kI32Sub:
    return get_stencil(asm_generated::i32_sub_immediate);
  case kI32Mul:
    return get_stencil(asm_generated::i32_mul_immediate);
  case kI32And:
    return get_stencil(asm_generated::i32_and_immediate);
  case kI32Or:
    return get_stencil(asm_generated::i32_or_immediate);
  case kI32Xor:
    return get_stencil(asm_generated::i32_xor_immediate);
  case kI32Shl:
    return get_stencil(asm_generated::i32_shl_immediate);
  case kI32ShrS:
    return get_stencil(asm_generated::i32_shr_s_immediate);
  case kI32ShrU:
    return get_stencil(asm_generated::i32_shr_u_immediate);
  default:
    return {};
  }
}

stencil_view wide_binary_stencil(int op) {
  switch (op) {
  case kI64Add:
    return get_stencil(generated::i64_add);
  case kI64Sub:
    return get_stencil(generated::i64_sub);
  case kI64Mul:
    return get_stencil(generated::i64_mul);
  case kF32Add:
    return get_stencil(generated::f32_add);
  case kF32Sub:
    return get_stencil(generated::f32_sub);
  case kF32Mul:
    return get_stencil(generated::f32_mul);
  case kF32Div:
    return get_stencil(generated::f32_div);
  case kF64Add:
    return get_stencil(generated::f64_add);
  case kF64Sub:
    return get_stencil(generated::f64_sub);
  case kF64Mul:
    return get_stencil(generated::f64_mul);
  case kF64Div:
    return get_stencil(generated::f64_div);
  default:
    return {};
  }
}

bool local_layout_entry(const frame_word_offset_t *offsets, const std::uint8_t *types,
                        local_count_t count, local_count_t total_words, int index,
                        code_offset_t &offset_bytes, operand_words_t &width, std::uint8_t &type) {
  if (index < 0 || static_cast<std::uint32_t>(index) >= count || offsets == nullptr ||
      types == nullptr) {
    return false;
  }
  const auto offset = offsets[index];
  const auto local_type = types[index];
  const bool narrow = local_type == kWasmI32 || local_type == kWasmF32;
  const bool wide = local_type == kWasmI64 || local_type == kWasmF64;
  const auto size = wide ? 2u : 1u;
  if ((!narrow && !wide) || offset + size > total_words || offset > 30) {
    return false;
  }
  offset_bytes = static_cast<std::uint32_t>(offset) * 4;
  width = size;
  type = local_type;
  return true;
}

stencil_view local_stencil(int operation, std::uint8_t type) {
  switch (operation) {
  case kLocalGet:
    switch (type) {
    case kWasmI32:
      return get_stencil(asm_generated::local_get_i32);
    case kWasmI64:
      return get_stencil(asm_generated::local_get_i64);
    case kWasmF32:
      return get_stencil(asm_generated::local_get_f32);
    case kWasmF64:
      return get_stencil(asm_generated::local_get_f64);
    default:
      return {};
    }
  case kLocalSet:
    switch (type) {
    case kWasmI32:
      return get_stencil(asm_generated::local_set_i32);
    case kWasmI64:
      return get_stencil(asm_generated::local_set_i64);
    case kWasmF32:
      return get_stencil(asm_generated::local_set_f32);
    case kWasmF64:
      return get_stencil(asm_generated::local_set_f64);
    default:
      return {};
    }
  case kLocalTee:
    switch (type) {
    case kWasmI32:
      return get_stencil(asm_generated::local_tee_i32);
    case kWasmI64:
      return get_stencil(asm_generated::local_tee_i64);
    case kWasmF32:
      return get_stencil(asm_generated::local_tee_f32);
    case kWasmF64:
      return get_stencil(asm_generated::local_tee_f64);
    default:
      return {};
    }
  default:
    return {};
  }
}

stencil_view fused_local_binary_stencil(int op) {
  switch (op) {
  case kI32Add:
    return get_stencil(asm_generated::i32_add_local);
  case kI32Sub:
    return get_stencil(asm_generated::i32_sub_local);
  case kI32Mul:
    return get_stencil(asm_generated::i32_mul_local);
  case kI32And:
    return get_stencil(asm_generated::i32_and_local);
  case kI32Or:
    return get_stencil(asm_generated::i32_or_local);
  case kI32Xor:
    return get_stencil(asm_generated::i32_xor_local);
  case kI32Eq:
    return get_stencil(asm_generated::i32_eq_local);
  case kI32Ne:
    return get_stencil(asm_generated::i32_ne_local);
  case kI32LtS:
    return get_stencil(asm_generated::i32_lt_s_local);
  case kI32LtU:
    return get_stencil(asm_generated::i32_lt_u_local);
  case kI32GtS:
    return get_stencil(asm_generated::i32_gt_s_local);
  case kI32GtU:
    return get_stencil(asm_generated::i32_gt_u_local);
  case kI32LeS:
    return get_stencil(asm_generated::i32_le_s_local);
  case kI32LeU:
    return get_stencil(asm_generated::i32_le_u_local);
  case kI32GeS:
    return get_stencil(asm_generated::i32_ge_s_local);
  case kI32GeU:
    return get_stencil(asm_generated::i32_ge_u_local);
  default:
    return {};
  }
}

bool is_i32_immediate_opcode(int op) {
  return op == kI32Add || op == kI32Sub || op == kI32Mul || op == kI32And || op == kI32Or ||
         op == kI32Xor || op == kI32Shl || op == kI32ShrS || op == kI32ShrU;
}

bool fold_i32_binary(int op, std::uint32_t left, std::uint32_t right, std::uint32_t &result) {
  switch (op) {
  case kI32Add:
    result = left + right;
    return true;
  case kI32Sub:
    result = left - right;
    return true;
  case kI32Mul:
    result = left * right;
    return true;
  case kI32And:
    result = left & right;
    return true;
  case kI32Or:
    result = left | right;
    return true;
  case kI32Xor:
    result = left ^ right;
    return true;
  case kI32Shl:
    result = left << (right & 31u);
    return true;
  case kI32ShrS:
    result = std::bit_cast<std::uint32_t>(std::bit_cast<std::int32_t>(left) >> (right & 31u));
    return true;
  case kI32ShrU:
    result = left >> (right & 31u);
    return true;
  case kI32Eq:
    result = left == right;
    return true;
  case kI32Ne:
    result = left != right;
    return true;
  case kI32LtS:
    result = std::bit_cast<std::int32_t>(left) < std::bit_cast<std::int32_t>(right);
    return true;
  case kI32LtU:
    result = left < right;
    return true;
  case kI32GtS:
    result = std::bit_cast<std::int32_t>(left) > std::bit_cast<std::int32_t>(right);
    return true;
  case kI32GtU:
    result = left > right;
    return true;
  case kI32LeS:
    result = std::bit_cast<std::int32_t>(left) <= std::bit_cast<std::int32_t>(right);
    return true;
  case kI32LeU:
    result = left <= right;
    return true;
  case kI32GeS:
    result = std::bit_cast<std::int32_t>(left) >= std::bit_cast<std::int32_t>(right);
    return true;
  case kI32GeU:
    result = left >= right;
    return true;
  default:
    return false;
  }
}

bool is_i32_binary(int op) { return i32_binary_stencil(op).size != 0; }

bool emit_constant(trace_builder &builder, stack_model &stack, int op, std::uint64_t value) {
  stencil_view stencil{};
  std::uint8_t width = 0;
  switch (op) {
  case kI32Const:
    stencil = get_stencil(asm_generated::i32_const);
    width = 1;
    break;
  case kF32Const:
    stencil = get_stencil(asm_generated::f32_const);
    width = 1;
    break;
  case kI64Const:
    stencil = get_stencil(generated::i64_const);
    width = 2;
    break;
  case kF64Const:
    stencil = get_stencil(generated::f64_const);
    width = 2;
    break;
  default:
    return false;
  }
  const auto immediate_width = width == 1 ? std::uint8_t{4} : std::uint8_t{8};
  return builder.append_patched(stencil, value, immediate_width) && stack.push(width);
}

bool emit_runtime_api(trace_builder &builder, stack_model &stack, int op, code_ip_t source_ip,
                      const std::uint64_t *immediates, const std::uint8_t *immediate_widths,
                      std::uint8_t immediate_count,
                      annotation_offset_t interpreter_rel_after);
bool compile_instructions(trace_builder &builder, const fireball::jit_instruction *instructions,
                          std::uint32_t instruction_count, const frame_word_offset_t *local_offsets,
                          const std::uint8_t *local_types, local_count_t local_count,
                          local_count_t local_slot_count, stack_model &stack,
                          const code_ip_t *source_offsets = nullptr,
                          const code_ip_t *source_end_offsets = nullptr,
                          const annotation_offset_t *interpreter_rel_after_offsets = nullptr) {
  if (local_count > kMaxLogicalLocals || local_slot_count > 2 * local_count ||
      (local_count != 0 && (local_offsets == nullptr || local_types == nullptr))) {
    return false;
  }

  for (std::uint32_t index = 0; index < instruction_count;) {
    const auto &instruction = instructions[index];
    const auto op = static_cast<int>(instruction.opcode);
    const bool has_operand = instruction.has_operand != 0;

    if (op == 0x02 || op == 0x03 || op == 0x05 || op == 0x0B) {
      ++index;
      continue;
    }

    if ((op == kI32Const || op == kI64Const || op == kF32Const || op == kF64Const ||
         op == kLocalGet || op == kLocalSet || op == kLocalTee) != has_operand) {
      return false;
    }

    if (op == kI32Const && index + 2 < instruction_count &&
        instructions[index + 1].opcode == kI32Const && instructions[index + 2].has_operand == 0) {
      std::uint32_t folded = 0;
      const auto next_op = static_cast<int>(instructions[index + 2].opcode);
      if (fold_i32_binary(next_op, static_cast<std::uint32_t>(instruction.operand),
                          static_cast<std::uint32_t>(instructions[index + 1].operand), folded)) {
        if (!emit_constant(builder, stack, kI32Const, folded)) {
          return false;
        }
        index += 3;
        continue;
      }
    }

    if (op == kI32Const && index + 1 < instruction_count &&
        instructions[index + 1].has_operand == 0) {
      const auto next_op = static_cast<int>(instructions[index + 1].opcode);
      const auto value = static_cast<std::uint32_t>(instruction.operand);
      if (next_op == kDrop) {
        index += 2;
        continue;
      }
      if (next_op == kI32Eqz) {
        if (!emit_constant(builder, stack, kI32Const, value == 0 ? 1 : 0)) {
          return false;
        }
        index += 2;
        continue;
      }
      if (is_i32_immediate_opcode(next_op) && stack.top_is(1)) {
        const bool identity =
            (((next_op == kI32Add || next_op == kI32Sub || next_op == kI32Or ||
               next_op == kI32Xor) &&
              value == 0) ||
             ((next_op == kI32Shl || next_op == kI32ShrS || next_op == kI32ShrU) &&
              (value & 31u) == 0) ||
             (next_op == kI32Mul && value == 1) || (next_op == kI32And && value == UINT32_MAX));
        if (!identity) {
          const auto immediate_stencil = i32_immediate_stencil(next_op);
          const auto immediate = (next_op == kI32Shl || next_op == kI32ShrS || next_op == kI32ShrU)
                                     ? static_cast<std::uint64_t>(value & 31u)
                                     : static_cast<std::uint64_t>(value);
          const auto immediate_width =
              (next_op == kI32Shl || next_op == kI32ShrS || next_op == kI32ShrU) ? std::uint8_t{1}
                                                                                 : std::uint8_t{4};
          if (!builder.append_patched(immediate_stencil, immediate, immediate_width)) {
            return false;
          }
        }
        index += 2;
        continue;
      }
    }

    if (op == kI32Const && index + 1 < instruction_count &&
        (instructions[index + 1].opcode == kLocalSet ||
         instructions[index + 1].opcode == kLocalTee) &&
        instructions[index + 1].has_operand != 0) {
      const auto local_index = static_cast<int>(instructions[index + 1].operand);
      code_offset_t offset = 0;
      operand_words_t width = 0;
      std::uint8_t type = 0;
      const auto set_op = static_cast<int>(instructions[index + 1].opcode);
      if (local_layout_entry(local_offsets, local_types, local_count, local_slot_count, local_index,
                             offset, width, type) &&
          type == kWasmI32) {
        const auto local_set_const = get_stencil(asm_generated::local_set_const_i32);
        if (local_set_const.symbol != nullptr) {
          if (!builder.append_local(local_set_const, offset, local_memory_operation::store_narrow,
                                    static_cast<std::uint32_t>(instruction.operand), 4)) {
            return false;
          }
          if (set_op == kLocalTee &&
              !emit_constant(builder, stack, kI32Const,
                             static_cast<std::uint32_t>(instruction.operand))) {
            return false;
          }
          index += 2;
          continue;
        }
      }
    }

    if (op == kLocalGet && index + 2 < instruction_count && has_operand &&
        instructions[index + 1].has_operand != 0 && instructions[index + 1].opcode == kLocalGet &&
        instructions[index + 2].has_operand == 0 && stack.word_count == 0) {
      const auto second_op = static_cast<int>(instructions[index + 2].opcode);
      if (is_i32_binary(second_op) && second_op != kI32DivS && second_op != kI32DivU &&
          second_op != kI32RemS && second_op != kI32RemU) {
        code_offset_t first_offset = 0, second_offset = 0;
        operand_words_t first_width = 0, second_width = 0;
        std::uint8_t first_type = 0, second_type = 0;
        if (local_layout_entry(local_offsets, local_types, local_count, local_slot_count,
                               static_cast<int>(instruction.operand), first_offset, first_width,
                               first_type) &&
            local_layout_entry(local_offsets, local_types, local_count, local_slot_count,
                               static_cast<int>(instructions[index + 1].operand), second_offset,
                               second_width, second_type) &&
            first_type == kWasmI32 && second_type == kWasmI32) {
          const auto fused_local = fused_local_binary_stencil(second_op);
          const auto first_local = local_stencil(kLocalGet, first_type);
          if (first_local.symbol != nullptr && fused_local.symbol != nullptr) {
            if (!builder.append_local(first_local, first_offset,
                                      local_memory_operation::load_narrow) ||
                !stack.push(1) ||
                !builder.append_local(fused_local, second_offset,
                                      local_memory_operation::load_narrow)) {
              return false;
            }
            index += 3;
            continue;
          }
        }
      }
    }

    if (op == kI32Const || op == kI64Const || op == kF32Const || op == kF64Const) {
      if (!emit_constant(builder, stack, op, instruction.operand)) {
        return false;
      }
      ++index;
      continue;
    }

    if (op == kLocalGet || op == kLocalSet || op == kLocalTee) {
      code_offset_t offset = 0;
      operand_words_t width = 0;
      std::uint8_t type = 0;
      if (!local_layout_entry(local_offsets, local_types, local_count, local_slot_count,
                              static_cast<int>(instruction.operand), offset, width, type)) {
        return false;
      }
      const auto local_op = local_stencil(op, type);
      if (local_op.symbol == nullptr) {
        return false;
      }
      const auto encoding =
          width == 1 ? local_memory_operation::load_narrow : local_memory_operation::load_wide;
      const auto store_encoding =
          width == 1 ? local_memory_operation::store_narrow : local_memory_operation::store_wide;
      if (op == kLocalGet) {
        if (!builder.append_local(local_op, offset, encoding) || !stack.push(width)) {
          return false;
        }
      } else if (op == kLocalSet) {
        if (!stack.pop(width) || !builder.append_local(local_op, offset, store_encoding)) {
          return false;
        }
      } else if (!stack.top_is(width) || !builder.append_local(local_op, offset, store_encoding)) {
        return false;
      }
      ++index;
      continue;
    }

    if (op == kDrop) {
      if (stack.value_count == 0) {
        return false;
      }
      const auto width = stack.widths[stack.value_count - 1];
      const auto stencil =
          width == 1 ? get_stencil(asm_generated::drop) : get_stencil(asm_generated::drop_wide);
      if (!stack.pop(width) || !builder.append_linked(stencil)) {
        return false;
      }
      ++index;
      continue;
    }
    if (op == kSelect) {
      if (stack.value_count < 3 ||
          stack.widths[stack.value_count - 2] != stack.widths[stack.value_count - 3]) {
        return false;
      }
      const auto width = stack.widths[stack.value_count - 2];
      const auto stencil = width == 1 ? get_stencil(asm_generated::select_i32)
                                      : get_stencil(asm_generated::select_i64);
      if (!stack.select() || !builder.append_linked(stencil)) {
        return false;
      }
      ++index;
      continue;
    }
    if (op == kI32Eqz) {
      if (!stack.top_is(1) || !builder.append_linked(get_stencil(asm_generated::i32_eqz))) {
        return false;
      }
      ++index;
      continue;
    }
    if (is_i32_binary(op)) {
      if (!stack.binary(1) || !builder.append_linked(i32_binary_stencil(op))) {
        return false;
      }
      ++index;
      continue;
    }
    const auto wide_stencil = wide_binary_stencil(op);
    if (wide_stencil.size != 0) {
      const auto width =
          (op >= kI64Add && op <= kI64Mul) || (op >= kF64Add && op <= kF64Div) ? 2 : 1;
      if (!stack.binary(static_cast<std::uint8_t>(width)) || !builder.append_linked(wide_stencil)) {
        return false;
      }
      ++index;
      continue;
    }
    if (op == 0x00) {
      const auto source_ip = source_offsets != nullptr ? source_offsets[index] : 0u;
      const auto interpreter_rel_after = interpreter_rel_after_offsets == nullptr
                                             ? std::numeric_limits<annotation_offset_t>::max()
                                             : interpreter_rel_after_offsets[index];
      if (!emit_runtime_api(builder, stack, op, source_ip, nullptr, nullptr, 0,
                            interpreter_rel_after)) {
        return false;
      }
      return true;
    }
    const bool wasm_memory_operation = op >= 0x28 && op <= 0x3E;
    const bool wasm_fc_operation =
        (op & 0xFF00) == 0xFC00 &&
        (((op & 0xFF) <= 7) || (op & 0xFF) == 0x0A || (op & 0xFF) == 0x0B);
    if (source_offsets != nullptr && source_end_offsets != nullptr &&
        (wasm_memory_operation || wasm_fc_operation)) {
      const auto subopcode = static_cast<std::uint32_t>(op & 0xFF);
      const auto packed_memory_indices = instruction.operand;
      if (wasm_fc_operation && subopcode == 0x0A && packed_memory_indices != 0) {
        return false;
      }
      if (wasm_fc_operation && subopcode == 0x0B &&
          static_cast<std::uint32_t>(packed_memory_indices) != 0) {
        return false;
      }
      const auto instruction_width =
          static_cast<std::uint8_t>(source_end_offsets[index] - source_offsets[index]);
      const std::uint64_t immediates[2] = {instruction_width,
                                           wasm_memory_operation
                                               ? static_cast<std::uint32_t>(instruction.operand)
                                               : static_cast<std::uint32_t>(packed_memory_indices)};
      const std::uint8_t immediate_widths[2] = {1, 4};
      const auto immediate_count = wasm_memory_operation ? 2u : (subopcode >= 0x0A ? 1u : 0u);
      const auto interpreter_rel_after = interpreter_rel_after_offsets == nullptr
                                             ? std::numeric_limits<annotation_offset_t>::max()
                                             : interpreter_rel_after_offsets[index];
      if (!emit_runtime_api(builder, stack, op, source_offsets[index],
                            immediate_count == 0 ? nullptr : immediates,
                            immediate_count == 0 ? nullptr : immediate_widths,
                            static_cast<std::uint8_t>(immediate_count), interpreter_rel_after)) {
        return false;
      }
      ++index;
      continue;
    }
    if (source_offsets == nullptr || source_end_offsets == nullptr) {
      return false;
    }
    std::array<std::uint64_t, 2> runtime_rel{};
    std::array<std::uint8_t, 2> runtime_rel_widths{};
    std::uint32_t runtime_rel_count = 0;
    const auto instruction_width = source_end_offsets[index] - source_offsets[index];
    if (op == 0x23 || op == 0x24) {
      runtime_rel[runtime_rel_count] = instruction_width;
      runtime_rel_widths[runtime_rel_count++] = 1;
      runtime_rel[runtime_rel_count] = static_cast<std::uint32_t>(instruction.operand);
      runtime_rel_widths[runtime_rel_count++] = 2;
    } else if (op != 0x3F && op != 0x40) {
      runtime_rel[runtime_rel_count] = instruction_width;
      runtime_rel_widths[runtime_rel_count++] = 1;
    }
    const auto interpreter_rel_after = interpreter_rel_after_offsets == nullptr
                                           ? std::numeric_limits<annotation_offset_t>::max()
                                           : interpreter_rel_after_offsets[index];
    if (!emit_runtime_api(builder, stack, op, source_offsets[index],
                          runtime_rel_count == 0 ? nullptr : runtime_rel.data(),
                          runtime_rel_count == 0 ? nullptr : runtime_rel_widths.data(),
                          static_cast<std::uint8_t>(runtime_rel_count), interpreter_rel_after)) {
      return false;
    }
    ++index;
  }
  return true;
}

int finish_trace(trace_builder &builder, stack_model &stack, fireball::jit_compile_result *result) {
  if (result == nullptr || !builder.finish(*result)) {
    return 0;
  }
  result->max_stack_words = stack.max_words;
  result->result_words = stack.word_count;
  return 1;
}

bool runtime_api_slot(int op, std::uint8_t &slot) {
  const bool fc_prefix = (op & 0xFF00) == 0xFC00;
  const auto opcode = static_cast<std::uint32_t>(fc_prefix ? op & 0xFF : op);
  return fb_jit_runtime_api_slot(opcode, fc_prefix ? 1u : 0u, &slot) == 1;
}

bool pop_values(stack_model &stack, std::uint32_t count, std::uint8_t width) {
  while (count != 0) {
    if (!stack.pop(width)) {
      return false;
    }
    --count;
  }
  return true;
}

bool apply_runtime_api_stack_effect(int op, stack_model &stack) {
  if (op == 0x00) {
    return true;
  }
  if ((op & 0xFF00) == 0xFC00) {
    const auto subopcode = op & 0xFF;
    if (subopcode <= 7) {
      const auto input_width =
          subopcode == 0 || subopcode == 1 || subopcode == 4 || subopcode == 5 ? 1u : 2u;
      const auto output_width = subopcode < 4 ? 1u : 2u;
      return stack.unary(static_cast<std::uint8_t>(input_width),
                         static_cast<std::uint8_t>(output_width));
    }
    if (subopcode == 0x0A || subopcode == 0x0B) {
      return pop_values(stack, 3, 1);
    }
    return false;
  }

  if (op == 0x01) {
    return true;
  }
  if (op == 0x1A) {
    return stack.pop_any();
  }
  if (op == 0x1B) {
    return stack.select();
  }
  if (op >= 0x28 && op <= 0x35) {
    const auto result_width = (op == 0x29 || (op >= 0x30 && op <= 0x35)) ? 2u : 1u;
    return stack.unary(1, static_cast<std::uint8_t>(result_width));
  }
  if (op >= 0x36 && op <= 0x3E) {
    const auto value_width = (op == 0x37 || (op >= 0x3C && op <= 0x3E)) ? 2u : 1u;
    return stack.pop(static_cast<std::uint8_t>(value_width)) && stack.pop(1);
  }
  if (op == 0x3F) {
    return stack.push(1);
  }
  if (op == 0x40) {
    return stack.unary(1, 1);
  }
  if (op >= 0x67 && op <= 0x69) {
    return stack.unary(1, 1);
  }
  if (op == 0x50) {
    return stack.unary(2, 1);
  }
  if (op >= 0x51 && op <= 0x5A) {
    return stack.binary_to(2, 1);
  }
  if (op >= 0x5B && op <= 0x60) {
    return stack.binary_to(1, 1);
  }
  if (op >= 0x61 && op <= 0x66) {
    return stack.binary_to(2, 1);
  }
  if (op >= 0x79 && op <= 0x7B) {
    return stack.unary(2, 2);
  }
  if (op >= 0x7C && op <= 0x8A) {
    return stack.binary_to(2, 2);
  }
  if (op >= 0x8B && op <= 0x91) {
    return stack.unary(1, 1);
  }
  if (op >= 0x92 && op <= 0x98) {
    return stack.binary_to(1, 1);
  }
  if (op >= 0x99 && op <= 0x9F) {
    return stack.unary(2, 2);
  }
  if (op >= 0xA0 && op <= 0xA6) {
    return stack.binary_to(2, 2);
  }
  switch (op) {
  case 0xA7:
    return stack.unary(2, 1);
  case 0xA8:
  case 0xA9:
    return stack.unary(1, 1);
  case 0xAA:
  case 0xAB:
    return stack.unary(2, 1);
  case 0xAC:
  case 0xAD:
    return stack.unary(1, 2);
  case 0xAE:
  case 0xAF:
    return stack.unary(1, 2);
  case 0xB0:
  case 0xB1:
    return stack.unary(2, 2);
  case 0xB2:
  case 0xB3:
    return stack.unary(1, 1);
  case 0xB4:
  case 0xB5:
  case 0xB6:
    return stack.unary(2, 1);
  case 0xB7:
  case 0xB8:
    return stack.unary(1, 2);
  case 0xB9:
  case 0xBA:
    return stack.unary(2, 2);
  case 0xBB:
    return stack.unary(1, 2);
  case 0xBC:
  case 0xBE:
    return stack.unary(1, 1);
  case 0xBD:
  case 0xBF:
    return stack.unary(2, 2);
  case 0xC0:
  case 0xC1:
    return stack.unary(1, 1);
  case 0xC2:
  case 0xC3:
  case 0xC4:
    return stack.unary(2, 2);
  default:
    return false;
  }
}

bool emit_runtime_api(trace_builder &builder, stack_model &stack, int op, code_ip_t source_ip,
                      const std::uint64_t *immediates, const std::uint8_t *immediate_widths,
                      std::uint8_t immediate_count,
                      annotation_offset_t interpreter_rel_after) {
  std::uint8_t slot = 0;
  if ((immediate_count != 0 && (immediates == nullptr || immediate_widths == nullptr)) ||
      !runtime_api_slot(op, slot)) {
    return false;
  }
  if (!apply_runtime_api_stack_effect(op, stack)) {
    return false;
  }
  return builder.append_runtime_api(slot, source_ip, immediates, immediate_widths, immediate_count,
                                    static_cast<std::uint32_t>(op), interpreter_rel_after);
}

bool is_trace_terminator_opcode(std::uint8_t opcode) {
  switch (opcode) {
  case 0x00: // unreachable
  case 0x04: // if
  case 0x0C: // br
  case 0x0D: // br_if
  case 0x0E: // br_table
  case 0x0F: // return
  case 0x10: // call exits to Interpreter CPS before execution
  case 0x11: // call_indirect exits to Interpreter CPS before execution
    return true;
  default:
    return false;
  }
}

int append_trace_terminator_runtime_api(trace_builder &builder, const std::uint8_t *code,
                                        code_offset_t code_bytes, code_ip_t source_ip,
                                        code_ip_t trace_start_ip,
                                        const std::uint8_t *interpreter_annotations,
                                        annotation_offset_t interpreter_annotation_byte_count,
                                        annotation_offset_t interpreter_annotation_byte_offset,
                                        annotation_offset_t &interpreter_rel_bytes,
                                        code_ip_t &terminal_end_ip) {
  terminal_end_ip = source_ip;
  if (source_ip >= code_bytes) {
    return 1;
  }

  auto cursor = source_ip;
  const auto opcode = code[cursor++];
  if (!is_trace_terminator_opcode(opcode)) {
    return 1;
  }
  if (opcode == 0x04 || opcode == 0x0C || opcode == 0x0D) {
    // Branch instructions remain unexecuted trace exits handled by Interpreter CPS.
    return 1;
  }
  if (opcode == 0x10 || opcode == 0x11) {
    std::uint32_t operand = 0;
    if (!read_unsigned_leb(code, cursor, code_bytes, operand)) {
      return -1;
    }
    if (opcode == 0x11) {
      std::uint32_t table_index = 0;
      if (!read_unsigned_leb(code, cursor, code_bytes, table_index)) {
        return -1;
      }
    }
    const auto annotation_offset_64 =
        static_cast<std::uint64_t>(interpreter_annotation_byte_offset) + interpreter_rel_bytes;
    const auto annotation_after_64 = annotation_offset_64 + 5u;
    if (annotation_after_64 > std::numeric_limits<annotation_offset_t>::max() ||
        interpreter_annotations == nullptr ||
        annotation_after_64 > interpreter_annotation_byte_count || source_ip < trace_start_ip) {
      return -1;
    }
    std::uint8_t handler_slot = 0;
    if (!fb_jit_cps_call_handler_slot(opcode, &handler_slot)) {
      return 0;
    }
    terminal_end_ip = cursor;
    interpreter_rel_bytes = static_cast<annotation_offset_t>(annotation_after_64 -
                                                               interpreter_annotation_byte_offset);
    return builder.append_cps_call(
               handler_slot, source_ip - trace_start_ip,
               static_cast<annotation_offset_t>(annotation_offset_64))
               ? 1
               : 0;
  }

  std::uint8_t slot = 0;
  if (!runtime_api_slot(opcode, slot)) {
    return 0;
  }

  std::uint32_t annotation_bytes = 0;
  switch (opcode) {
  case 0x04: {
    [[maybe_unused]] std::uint64_t block_type = 0;
    if (!read_signed_leb(code, cursor, code_bytes, block_type, 33u)) {
      return -1;
    }
    annotation_bytes = opcode == 0x04 ? 4u : 2u;
    break;
  }
  case 0x0C:
  case 0x0D: {
    std::uint32_t operand = 0;
    if (!read_unsigned_leb(code, cursor, code_bytes, operand)) {
      return -1;
    }
    annotation_bytes = opcode == 0x0D ? 3u : 2u;
    break;
  }
  case 0x0E: {
    std::uint32_t case_count = 0;
    if (!read_unsigned_leb(code, cursor, code_bytes, case_count)) {
      return -1;
    }
    if (case_count > UINT16_MAX || case_count > (kMaxBodyBytes - sizeof(std::uint16_t)) / 3u - 1u) {
      return 0;
    }
    for (std::uint32_t index = 0; index <= case_count; ++index) {
      std::uint32_t depth = 0;
      if (!read_unsigned_leb(code, cursor, code_bytes, depth)) {
        return -1;
      }
    }
    annotation_bytes = sizeof(std::uint16_t) + (case_count + 1u) * 3u;
    break;
  }
  default:
    break;
  }

  const auto annotation_offset_64 =
      static_cast<std::uint64_t>(interpreter_annotation_byte_offset) + interpreter_rel_bytes;
  if (annotation_offset_64 > UINT32_MAX) {
    return -1;
  }
  const auto annotation_offset = static_cast<annotation_offset_t>(annotation_offset_64);
  const auto interpreter_rel_after_64 = annotation_offset_64 + annotation_bytes;
  if (interpreter_rel_after_64 > std::numeric_limits<annotation_offset_t>::max()) {
    return -1;
  }
  if (annotation_bytes != 0 &&
      !(interpreter_annotations == nullptr && interpreter_annotation_byte_count == 0) &&
      (interpreter_annotations == nullptr ||
       annotation_offset > interpreter_annotation_byte_count ||
       annotation_bytes > interpreter_annotation_byte_count - annotation_offset)) {
    return -1;
  }

  return builder.append_terminal_runtime_api(slot, source_ip, annotation_offset,
                                             annotation_bytes != 0, opcode,
                                             static_cast<annotation_offset_t>(
                                                 interpreter_rel_after_64))
             ? 1
             : 0;
}

} // namespace

int fireball::compile_instruction_body(
    const fireball::jit_instruction *instructions, std::uint32_t instruction_count,
    std::uint32_t has_next_pc, wasm_pc_t next_pc, std::uint32_t has_loops_to, wasm_pc_t loops_to,
    code_offset_t byte_span, const frame_word_offset_t *local_offsets,
    const std::uint8_t *local_types, local_count_t local_count, local_count_t local_slot_count,
    std::uint8_t *output, code_offset_t output_capacity, fireball::jit_compile_result *result) {
  static_cast<void>(has_next_pc);
  static_cast<void>(next_pc);
  static_cast<void>(has_loops_to);
  static_cast<void>(loops_to);
  if (result == nullptr || output == nullptr || byte_span == 0 || instruction_count > byte_span ||
      (instruction_count != 0 && instructions == nullptr)) {
    return -1;
  }
  *result = {};
  result->body_end_ip = kNoTraceExitIp;
  if (instruction_count == 0 || instruction_count > kMaxTraceInstructions ||
      output_capacity > kMaxBodyBytes) {
    return 0;
  }

  trace_builder builder{output, output_capacity};
  stack_model stack{};
  if (!builder.begin_trace() ||
      !compile_instructions(builder, instructions, instruction_count, local_offsets, local_types,
                            local_count, local_slot_count, stack)) {
    return 0;
  }
  return finish_trace(builder, stack, result);
}

int fireball::compile_wasm_trace(
    const std::uint8_t *code, code_offset_t code_bytes, code_offset_t code_offset,
    code_offset_t byte_span, std::uint32_t has_next_pc, wasm_pc_t next_pc,
    std::uint32_t has_loops_to, wasm_pc_t loops_to, const std::uint8_t *interpreter_annotations,
    annotation_offset_t interpreter_annotation_byte_count,
    annotation_offset_t interpreter_annotation_byte_offset,
    const frame_word_offset_t *local_offsets, const std::uint8_t *local_types,
    local_count_t local_count, local_count_t local_slot_count, std::uint8_t *output,
    code_offset_t output_capacity, fireball::jit_compile_result *result) {
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
  const_pool_diagnostic_scope diagnostic_scope{};
#endif
  static_cast<void>(has_next_pc);
  static_cast<void>(next_pc);
  static_cast<void>(has_loops_to);
  static_cast<void>(loops_to);
  if (code == nullptr || byte_span == 0 || code_offset > code_bytes ||
      byte_span > code_bytes - code_offset || output == nullptr || result == nullptr) {
    return -1;
  }
  *result = {};
  result->body_end_ip = kNoTraceExitIp;
  if (output_capacity > kMaxBodyBytes) {
    return -1;
  }

  std::array<fireball::jit_instruction, kMaxTraceInstructions> instructions{};
  std::array<code_ip_t, kMaxTraceInstructions> source_offsets{};
  std::array<code_ip_t, kMaxTraceInstructions> source_end_offsets{};
  std::array<annotation_offset_t, kMaxTraceInstructions> interpreter_rel_after_offsets{};
  jit_body_count_t instruction_count = 0;
  annotation_offset_t interpreter_rel_bytes = 0;
  code_ip_t cursor = code_offset;
  const code_ip_t end = code_offset + byte_span;
  auto trace_end = end;
  auto call_exit = false;

  while (cursor < end) {
    const auto instruction_ip = cursor;
    const auto opcode = code[cursor++];
    if (opcode == 0x10 || opcode == 0x11) {
      // Keep the call itself and the remainder of this block for Interpreter CPS.
      trace_end = instruction_ip;
      call_exit = true;
      break;
    }
    if (opcode == 0x02 || opcode == 0x03) {
      [[maybe_unused]] std::uint64_t block_type = 0;
      if (!read_signed_leb(code, cursor, end, block_type, 33u)) {
        return -1;
      }
      if (opcode == 0x03) {
        interpreter_rel_bytes += sizeof(std::uint16_t);
      }
      continue;
    }
    if (opcode == 0x05 || opcode == 0x0B) {
      interpreter_rel_bytes += sizeof(std::uint16_t);
      continue;
    }
    if (instruction_count == instructions.size()) {
      return 0;
    }
    const auto instruction_index = instruction_count++;
    source_offsets[instruction_index] = instruction_ip;
    auto &instruction = instructions[instruction_index];
    instruction.opcode = opcode;
    instruction.has_operand = 0;
    instruction.operand = 0;

    if (opcode == kDrop || opcode == kSelect) {
      ++interpreter_rel_bytes;
    }

    if (opcode == kFcPrefix) {
      std::uint32_t subopcode = 0;
      if (!read_unsigned_leb(code, cursor, end, subopcode) || subopcode > 17) {
        return -1;
      }
      instruction.opcode = 0xFC00u | subopcode;
      std::uint32_t immediate_count = 0;
      if (subopcode == 8 || subopcode == 0x0A || subopcode == 0x0C || subopcode == 0x0E) {
        immediate_count = 2;
      } else if (subopcode == 9 || subopcode == 0x0B || subopcode == 0x0D ||
                 (subopcode >= 0x0F && subopcode <= 0x11)) {
        immediate_count = 1;
      }
      interpreter_rel_bytes += 2; // width byte and subopcode
      if (subopcode == 0x0A) {
        std::uint32_t destination_memory = 0;
        std::uint32_t source_memory = 0;
        if (!read_unsigned_leb(code, cursor, end, destination_memory) ||
            !read_unsigned_leb(code, cursor, end, source_memory)) {
          return -1;
        }
        instruction.operand = static_cast<std::uint64_t>(destination_memory) |
                              (static_cast<std::uint64_t>(source_memory) << 32);
      } else if (subopcode == 0x0B) {
        std::uint32_t memory_index = 0;
        if (!read_unsigned_leb(code, cursor, end, memory_index)) {
          return -1;
        }
        instruction.operand = memory_index;
      } else {
        while (immediate_count != 0) {
          std::uint32_t ignored = 0;
          if (!read_unsigned_leb(code, cursor, end, ignored)) {
            return -1;
          }
          --immediate_count;
        }
      }
      const auto annotation_offset_64 =
          static_cast<std::uint64_t>(interpreter_annotation_byte_offset) + interpreter_rel_bytes;
      if (annotation_offset_64 > std::numeric_limits<annotation_offset_t>::max()) {
        return -1;
      }
      interpreter_rel_after_offsets[instruction_index] =
          static_cast<annotation_offset_t>(annotation_offset_64);
      source_end_offsets[instruction_index] = cursor;
      continue;
    }

    if (opcode == kLocalGet || opcode == kLocalSet || opcode == kLocalTee) {
      std::uint32_t operand = 0;
      if (!read_unsigned_leb(code, cursor, end, operand)) {
        return -1;
      }
      interpreter_rel_bytes += 4; // width byte, frame word offset, and local width
      instruction.has_operand = 1;
      instruction.operand = operand;
    } else if (opcode == 0x23 || opcode == 0x24 || opcode == 0x25 || opcode == 0x26 ||
               opcode == 0x3F || opcode == 0x40) {
      std::uint32_t operand = 0;
      if (!read_unsigned_leb(code, cursor, end, operand)) {
        return -1;
      }
      if (opcode == 0x23 || opcode == 0x24) {
        interpreter_rel_bytes += 3; // width byte and global index
      }
      instruction.operand = operand;
    } else if (opcode >= 0x28 && opcode <= 0x3E) {
      std::uint32_t alignment = 0;
      std::uint32_t offset = 0;
      if (!read_unsigned_leb(code, cursor, end, alignment) ||
          !read_unsigned_leb(code, cursor, end, offset)) {
        return -1;
      }
      interpreter_rel_bytes += 5; // width byte and effective memory offset
      instruction.operand = offset;
    } else if (opcode == kI32Const || opcode == kI64Const) {
      std::uint64_t operand = 0;
      if (!read_signed_leb(code, cursor, end, operand, opcode == kI32Const ? 32u : 64u)) {
        return -1;
      }
      interpreter_rel_bytes += opcode == kI32Const ? 5u : 9u;
      instruction.has_operand = 1;
      instruction.operand = operand;
    } else if (opcode == kF32Const || opcode == kF64Const) {
      const auto width = opcode == kF32Const ? 4u : 8u;
      if (end - cursor < width) {
        return -1;
      }
      std::uint64_t operand = 0;
      for (std::uint32_t byte = 0; byte < width; ++byte) {
        operand |= static_cast<std::uint64_t>(code[cursor + byte]) << (byte * 8u);
      }
      cursor += width;
      instruction.has_operand = 1;
      instruction.operand = operand;
    }
    const auto annotation_offset_64 =
        static_cast<std::uint64_t>(interpreter_annotation_byte_offset) + interpreter_rel_bytes;
    if (annotation_offset_64 > std::numeric_limits<annotation_offset_t>::max()) {
      return -1;
    }
    interpreter_rel_after_offsets[instruction_index] =
        static_cast<annotation_offset_t>(annotation_offset_64);
    source_end_offsets[instruction_index] = cursor;
  }
  if (cursor != end && !call_exit) {
    return -1;
  }
  if (instruction_count == 0 && !call_exit) {
    return 0;
  }

  trace_builder builder{output, output_capacity};
  stack_model stack{};
  if (!builder.begin_trace() ||
      !compile_instructions(builder, instructions.data(), instruction_count, local_offsets,
                            local_types, local_count, local_slot_count, stack,
                            source_offsets.data(), source_end_offsets.data(),
                            interpreter_rel_after_offsets.data())) {
    return 0;
  }
  code_ip_t terminal_end_ip = trace_end;
  const auto terminator_status = append_trace_terminator_runtime_api(
      builder, code, code_bytes, trace_end, code_offset, interpreter_annotations,
      interpreter_annotation_byte_count, interpreter_annotation_byte_offset,
      interpreter_rel_bytes, terminal_end_ip);
  if (terminator_status != 1) {
    return terminator_status;
  }
  const auto status = finish_trace(builder, stack, result);
  if (status == 1) {
    result->interpreter_rel_bytes = interpreter_rel_bytes;
    if (call_exit || terminal_end_ip != trace_end) {
      result->body_end_ip = terminal_end_ip;
    }
    if (call_exit || terminal_end_ip != trace_end) {
      result->exit_kind = jit_trace_exit_kind::call_cps;
    }
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
    const auto has_terminator = trace_end < code_bytes && is_trace_terminator_opcode(code[trace_end]);
    ++const_pool_diagnostics.trace_count;
    const_pool_diagnostics.instruction_count += instruction_count + (has_terminator ? 1u : 0u);
    diagnostic_scope.committed = true;
#endif
  }
  return status;
}

std::int64_t fireball::chain_successor(std::uintptr_t code_address, code_offset_t code_bytes,
                                       code_offset_t offset, code_offset_t span,
                                       wasm_pc_t next_pc) {
  if (code_address == 0 || offset > code_bytes || span > code_bytes - offset) {
    return -1;
  }
  const auto *code = reinterpret_cast<const std::uint8_t *>(code_address);
  const auto end = offset + span;
  const auto terminator = end < code_bytes ? code[end] : 0x0B;
  switch (terminator) {
  case 0x04:
  case 0x0C:
  case 0x0D:
  case 0x0E:
  case 0x0F:
  case 0x10:
  case 0x11:
  case 0xFC:
    return UINT32_MAX;
  default:
    return next_pc;
  }
}

extern "C" void fb_jit_const_pool_diagnostics_reset() {
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
  const_pool_diagnostics = {};
#endif
}

extern "C" void fb_jit_const_pool_diagnostics_read(fireball::jit_const_pool_diagnostics *output) {
  assert(output != nullptr);
#if defined(FB_CONF_JIT_BLOCK_COUNTERS) && FB_CONF_JIT_BLOCK_COUNTERS
  *output = const_pool_diagnostics;
#else
  *output = {};
#endif
}

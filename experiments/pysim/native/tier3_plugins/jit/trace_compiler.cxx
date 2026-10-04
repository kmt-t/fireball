#include <array>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "trace_compiler_abi.hxx"
#include "stencils_x64.hxx"

using namespace fireball::pysim::jit;

#ifndef FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET
#error "JIT chain dispatcher offset must come from tier1_core/config.py"
#endif
#ifndef FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES
#error "JIT chain dispatcher size must come from tier1_core/config.py"
#endif
#ifndef FB_CONF_JIT_X64_TRACE_HEADER_BYTES
#error "x64 JIT trace header size must come from tier1_core/config.py"
#endif
#ifndef FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET
#error "x64 JIT chain target offset must come from tier1_core/config.py"
#endif
#ifndef FB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET
#error "JIT common epilogue offset must come from tier1_core/config.py"
#endif

namespace {

constexpr std::size_t kTraceHeaderBytes = FB_CONF_JIT_X64_TRACE_HEADER_BYTES;
constexpr std::size_t kMaxBodyBytes = 8192;
constexpr std::size_t kMaxStackLocations = 8192;
constexpr std::int32_t kTos = -1;
constexpr std::int32_t kNos = -2;
constexpr std::size_t kTraceEntryStubBytes = 15;

constexpr int kDrop = 0x1A;
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
constexpr int kI32RemU = 0x70;
constexpr int kI32And = 0x71;
constexpr int kI32Or = 0x72;
constexpr int kI32Xor = 0x73;
constexpr int kI32Shl = 0x74;
constexpr int kI32ShrS = 0x75;
constexpr int kI32ShrU = 0x76;
constexpr int kI64Add = 0x7C;
constexpr int kI64Mul = 0x7E;
constexpr int kF32Add = 0x92;
constexpr int kF32Div = 0x95;
constexpr int kF64Add = 0xA0;
constexpr int kF64Div = 0xA3;

constexpr std::size_t kSharedDispatcherBytes = FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_BYTES;
constexpr std::size_t kCommonEpilogueOffset = FB_CONF_JIT_TRACE_COMMON_EPILOGUE_OFFSET;
static_assert(kTraceHeaderBytes + kTraceEntryStubBytes <= 0x7Fu);
static_assert(FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET <= 0x7Fu);

enum class assembler_label : std::size_t { fallback, count };

struct relative_fixup {
  std::size_t displacement_offset = 0;
  assembler_label target = assembler_label::fallback;
};

class constexpr_x64_assembler {
 public:
  constexpr void emit(std::uint8_t value) {
    if (size_ < bytes_.size()) bytes_[size_++] = value;
  }

  template <typename... Values>
  constexpr void emit_bytes(Values... values) {
    (emit(static_cast<std::uint8_t>(values)), ...);
  }

  constexpr void mark(assembler_label label) {
    labels_[static_cast<std::size_t>(label)] = size_;
  }

  constexpr void jump_if(std::uint8_t condition, assembler_label target) {
    emit_bytes(0x0F, condition);
    const auto displacement_offset = size_;
    emit_bytes(0, 0, 0, 0);
    if (fixup_count_ < fixups_.size()) {
      fixups_[fixup_count_++] = relative_fixup{displacement_offset, target};
    }
  }

  constexpr std::size_t jump_to_offset(std::size_t target_offset) {
    emit(0xE9);
    const auto displacement_offset = size_;
    emit_bytes(0, 0, 0, 0);
    const auto next_offset = base_offset_ + size_;
    const auto delta = static_cast<std::int64_t>(target_offset) -
                       static_cast<std::int64_t>(next_offset);
    write_i32(bytes_, displacement_offset, static_cast<std::int32_t>(delta));
    return displacement_offset;
  }

  constexpr void set_base_offset(std::size_t base_offset) { base_offset_ = base_offset; }

  constexpr std::size_t size() const { return size_; }

  constexpr std::array<std::uint8_t, kSharedDispatcherBytes> finish() const {
    auto result = bytes_;
    for (std::size_t index = 0; index < fixup_count_; ++index) {
      const auto fixup = fixups_[index];
      const auto label_index = static_cast<std::size_t>(fixup.target);
      const auto delta = static_cast<std::int64_t>(labels_[label_index]) -
                         static_cast<std::int64_t>(fixup.displacement_offset + 4);
      write_i32(result, fixup.displacement_offset, static_cast<std::int32_t>(delta));
    }
    return result;
  }

 private:
  static constexpr void write_i32(std::array<std::uint8_t, kSharedDispatcherBytes>& target,
                                  std::size_t offset, std::int32_t value) {
    const auto bits = static_cast<std::uint32_t>(value);
    for (std::size_t byte = 0; byte < 4; ++byte) {
      target[offset + byte] = static_cast<std::uint8_t>((bits >> (byte * 8)) & 0xFFu);
    }
  }

  std::array<std::uint8_t, kSharedDispatcherBytes> bytes_{};
  std::array<std::size_t, static_cast<std::size_t>(assembler_label::count)> labels_{};
  std::array<relative_fixup, 4> fixups_{};
  std::size_t size_ = 0;
  std::size_t fixup_count_ = 0;
  std::size_t base_offset_ = 0;
};

constexpr std::array<std::uint8_t, kSharedDispatcherBytes> make_chain_dispatcher() {
  constexpr_x64_assembler assembler;
  assembler.set_base_offset(FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET);
  assembler.emit_bytes(0x49, 0x8B, 0x56,
                       FB_CONF_JIT_X64_CHAIN_TARGET_OFFSET);  // mov rdx, [r14+chain_target]
  assembler.emit_bytes(0x48, 0x85, 0xD2);                       // test rdx, rdx
  assembler.jump_if(0x84, assembler_label::fallback);          // jz fallback
  assembler.emit_bytes(0x4C, 0x8D, 0x72,
                       -static_cast<std::int8_t>(kTraceHeaderBytes + kTraceEntryStubBytes));
  assembler.emit_bytes(0xFF, 0xE2);  // jmp rdx; R14 now names the next trace header
  assembler.mark(assembler_label::fallback);
  assembler.jump_to_offset(kCommonEpilogueOffset);
  return assembler.finish();
}

inline constexpr auto kChainDispatcher = make_chain_dispatcher();
static_assert(kChainDispatcher.size() == kSharedDispatcherBytes);

struct trace_builder {
  std::array<std::uint8_t, kMaxBodyBytes> body{};
  std::size_t body_size = 0;
  std::array<std::int32_t, kMaxStackLocations> locations{};
  std::size_t location_count = 0;
  int spilled_words = 0;
  int max_spilled_words = 0;
  int helper_words = 0;
  int helper_index = -1;
  bool saw_op = false;
  bool declined = false;

  bool byte(std::uint8_t value) {
    if (body_size >= body.size()) return false;
    body[body_size++] = value;
    return true;
  }
  bool u32(std::uint32_t value) {
    for (int shift = 0; shift < 32; shift += 8)
      if (!byte(static_cast<std::uint8_t>(value >> shift))) return false;
    return true;
  }
};

template <std::size_t N>
bool append_stencil(trace_builder& builder, const std::array<std::uint8_t, N>& stencil) {
  if (builder.body_size + N > builder.body.size()) return false;
  for (std::size_t index = 0; index < N; ++index) {
    builder.body[builder.body_size + index] = stencil[index];
  }
  builder.body_size += N;
  return true;
}

template <std::size_t N>
bool append_u32_patch(trace_builder& builder, const std::array<std::uint8_t, N>& stencil,
                     std::uint32_t value) {
  return append_stencil(builder, stencil) && builder.u32(value);
}

bool is_binary(int op) {
  return op == kI32Add || op == kI32Sub || op == kI32Mul || op == kI32And ||
         op == kI32Or || op == kI32Xor || op == kI32Eq || op == kI32Ne ||
         op == kI32LtS || op == kI32LtU || op == kI32GtS || op == kI32GtU ||
         op == kI32LeS || op == kI32LeU || op == kI32GeS || op == kI32GeU ||
         (op >= kI64Add && op <= kI64Mul) ||
         (op >= kF32Add && op <= kF32Div) || (op >= kF64Add && op <= kF64Div);
}

bool stack_effect(int op, int& pops, int& pushes) {
  if (op == kI32Const || op == kI64Const || op == kF32Const || op == kF64Const ||
      op == kLocalGet) { pops = 0; pushes = 1; return true; }
  if (op == kLocalSet || op == kDrop) { pops = 1; pushes = 0; return true; }
  if (op == kLocalTee || op == kI32Eqz) { pops = 1; pushes = 1; return true; }
  if (is_binary(op) || op == kI32Shl || op == kI32ShrS || op == kI32ShrU) {
    pops = 2; pushes = 1; return true;
  }
  return false;
}

int helper_index(int op) {
  if (op >= kI32DivS && op <= kI32RemU) return op - kI32DivS + 11;
  if (op >= kI64Add && op <= kI64Mul) return op - kI64Add;
  if (op >= kF32Add && op <= kF32Div) return op - kF32Add + 3;
  if (op >= kF64Add && op <= kF64Div) return op - kF64Add + 7;
  return -1;
}

int local_words(const std::uint8_t* map, std::uint32_t map_bytes,
                std::uint32_t count, int index) {
  if (index < 0 || static_cast<unsigned int>(index) >= count) return 0;
  const auto byte_index = static_cast<std::size_t>(index) >> 2;
  if (byte_index >= map_bytes || map == nullptr) return 0;
  const auto byte = map[byte_index];
  return 1 << ((byte >> ((index & 3) * 2)) & 3u);
}

bool load_imm(trace_builder& b, std::uint32_t value) {
  return append_u32_patch(b, kLoadImmStencil, value);
}
bool load_local(trace_builder& b, std::uint32_t offset) {
  return append_u32_patch(b, kLoadLocalStencil, offset);
}
bool store_local(trace_builder& b, std::uint32_t offset) {
  return append_u32_patch(b, kStoreLocalStencil, offset);
}
bool store_sp(trace_builder& b, std::int32_t location, int slot) {
  if (slot < 0 || (location != kTos && location != kNos)) return false;
  return append_u32_patch(b, location == kTos ? kStoreTosStencil : kStoreNosStencil,
                          static_cast<std::uint32_t>(slot * 4));
}
bool load_sp(trace_builder& b, std::int32_t location, int slot) {
  if (slot < 0 || (location != kTos && location != kNos)) return false;
  return append_u32_patch(b, location == kTos ? kLoadTosStencil : kLoadNosStencil,
                          static_cast<std::uint32_t>(slot * 4));
}

bool emit_push(trace_builder& b, int op, std::uint64_t arg, std::uint32_t slot_bytes) {
  if (b.location_count >= 2 && b.locations[b.location_count - 2] == kNos) {
    if (!store_sp(b, kNos, b.spilled_words)) return false;
    b.locations[b.location_count - 2] = b.spilled_words++;
  } else if (b.location_count >= 2 && b.locations[b.location_count - 2] < 0) return false;
  if (b.location_count > 0) {
    if (b.locations[b.location_count - 1] != kTos ||
        !append_stencil(b, kMoveNosFromTosStencil)) return false;
    b.locations[b.location_count - 1] = kNos;
  }
  if (op == kI32Const) {
    if (!load_imm(b, static_cast<std::uint32_t>(arg))) return false;
  } else if (op == kLocalGet) {
    if (!load_local(b, static_cast<std::uint32_t>(arg * slot_bytes))) return false;
  } else return false;
  if (b.location_count >= b.locations.size()) return false;
  b.locations[b.location_count++] = kTos;
  return true;
}

bool emit_binary(trace_builder& b, int op) {
  if (op == kI32Add) return append_stencil(b, kI32AddStencil);
  if (op == kI32Sub) return append_stencil(b, kI32SubStencil);
  if (op == kI32Mul) return append_stencil(b, kI32MulStencil);
  if (op == kI32And) return append_stencil(b, kI32AndStencil);
  if (op == kI32Or) return append_stencil(b, kI32OrStencil);
  if (op == kI32Xor) return append_stencil(b, kI32XorStencil);
  if (op == kI32Eq) return append_stencil(b, kI32EqStencil);
  if (op == kI32Ne) return append_stencil(b, kI32NeStencil);
  if (op == kI32LtS) return append_stencil(b, kI32LtSStencil);
  if (op == kI32LtU) return append_stencil(b, kI32LtUStencil);
  if (op == kI32GtS) return append_stencil(b, kI32GtSStencil);
  if (op == kI32GtU) return append_stencil(b, kI32GtUStencil);
  if (op == kI32LeS) return append_stencil(b, kI32LeSStencil);
  if (op == kI32LeU) return append_stencil(b, kI32LeUStencil);
  if (op == kI32GeS) return append_stencil(b, kI32GeSStencil);
  if (op == kI32GeU) return append_stencil(b, kI32GeUStencil);
  return false;
}

bool emit_pop(trace_builder& b) {
  if (b.location_count == 0 || b.locations[b.location_count - 1] != kTos) return false;
  --b.location_count;
  if (b.location_count == 0) return true;
  if (b.locations[b.location_count - 1] == kNos) {
    b.locations[b.location_count - 1] = kTos;
    return true;
  }
  if (b.locations[b.location_count - 1] != b.spilled_words - 1 || b.spilled_words <= 0) return false;
  const auto slot = b.locations[b.location_count - 1];
  if (!load_sp(b, kTos, slot)) return false;
  --b.location_count; --b.spilled_words; b.locations[b.location_count++] = kTos;
  return true;
}

bool emit_binary_spill(trace_builder& b, int op) {
  if (b.location_count < 2 || b.locations[b.location_count - 1] != kTos) return false;
  auto& nos = b.locations[b.location_count - 2];
  if (nos >= 0) {
    if (nos != b.spilled_words - 1 || b.spilled_words <= 0 || !load_sp(b, kNos, nos)) return false;
    nos = kNos; --b.spilled_words;
  }
  if (nos != kNos || !emit_binary(b, op)) return false;
  b.location_count -= 2; b.locations[b.location_count++] = kTos;
  return true;
}

bool emit_shift(trace_builder& b, int op) {
  if (!append_stencil(b, kShiftPrefixStencil)) return false;
  if (op == kI32Shl) return append_stencil(b, kI32ShlStencil);
  if (op == kI32ShrS) return append_stencil(b, kI32ShrSStencil);
  if (op == kI32ShrU) return append_stencil(b, kI32ShrUStencil);
  return false;
}

bool compile_operations(trace_builder& b, const fb_jit_instruction* instructions,
                        std::uint32_t instruction_count, const std::uint8_t* map,
                        std::uint32_t map_bytes, std::uint32_t local_count,
                        std::uint32_t slot_bytes) {
  for (std::uint32_t instruction_index = 0; instruction_index < instruction_count;
       ++instruction_index) {
    const auto& instruction = instructions[instruction_index];
    const int op = static_cast<int>(instruction.opcode);
    auto arg = instruction.operand;
    const bool expects_operand = op == kI32Const || op == kI64Const ||
                                 op == kF32Const || op == kF64Const ||
                                 op == kLocalGet || op == kLocalSet ||
                                 op == kLocalTee;
    if ((instruction.has_operand != 0) != expects_operand) {
      b.declined = true;
      return true;
    }
    if (b.spilled_words > b.max_spilled_words) b.max_spilled_words = b.spilled_words;
    b.saw_op = true;
    b.helper_index = helper_index(op);
    if (b.helper_index >= 0) {
      if (b.helper_index >= 11) {
        if (b.location_count != 2 || b.spilled_words != 0 || b.helper_words != 0 ||
            b.locations[0] != kNos || b.locations[1] != kTos) {
          b.declined = true; return true;
        }
        b.location_count = 0;
      } else {
          const int expected =
              b.helper_index >= 3 && b.helper_index <= 6 ? 2 : 4;
        if (b.location_count != 0 || (b.helper_words != 4 && b.helper_words != 2) ||
            b.helper_words != expected) {
          b.declined = true; return true;
        }
      }
      break;
    }
    int pops = 0, pushes = 0;
    if (!stack_effect(op, pops, pushes) || pops > static_cast<int>(b.location_count)) {
      b.declined = true; return true;
    }
    bool ok = true;
    if (op == kI32Const || op == kLocalGet) {
      if (op == kLocalGet) {
        const int index = static_cast<int>(arg);
        if (local_words(map, map_bytes, local_count, index) != 1) ok = false;
        arg = static_cast<std::uint64_t>(index);
      }
      if (ok) ok = emit_push(b, op, arg, slot_bytes);
    } else if (op == kI64Const || op == kF32Const || op == kF64Const) {
      if (b.location_count != 0) ok = false;
      const int width = op == kF32Const ? 1 : 2;
      for (int word = 0; ok && word < width; ++word) {
        ok = load_imm(b, static_cast<std::uint32_t>(arg >> (word * 32))) &&
             store_sp(b, kTos, b.helper_words++);
      }
    } else if (op == kLocalSet || op == kDrop) {
      if (op == kLocalSet) {
        const int index = static_cast<int>(arg);
        ok = local_words(map, map_bytes, local_count, index) == 1 &&
             store_local(b, static_cast<std::uint32_t>(index * slot_bytes));
      }
      if (ok) ok = emit_pop(b);
    } else if (op == kLocalTee) {
      const int index = static_cast<int>(arg);
      ok = local_words(map, map_bytes, local_count, index) == 1 && b.location_count != 0 &&
           store_local(b, static_cast<std::uint32_t>(index * slot_bytes));
    } else if (op == kI32Eqz) {
      ok = b.location_count != 0 && b.locations[b.location_count - 1] == kTos &&
           append_stencil(b, kI32EqzStencil);
    } else if (is_binary(op)) {
      ok = emit_binary_spill(b, op);
    } else if (op == kI32Shl || op == kI32ShrS || op == kI32ShrU) {
      if (b.location_count < 2 || b.locations[b.location_count - 1] != kTos) ok = false;
      auto& nos = b.locations[b.location_count - 2];
      if (ok && nos >= 0) {
        ok = nos == b.spilled_words - 1 && b.spilled_words > 0 && load_sp(b, kNos, nos);
        if (ok) { nos = kNos; --b.spilled_words; }
      }
      if (ok && nos == kNos && emit_shift(b, op)) {
        b.location_count -= 2; b.locations[b.location_count++] = kTos;
      } else ok = false;
    } else ok = false;
    if (!ok) { b.declined = true; return true; }
  }
  return true;
}

}  // namespace

extern "C" int fb_jit_compile_trace(
    const fb_jit_instruction* instructions, std::uint32_t instruction_count,
    std::uint32_t has_next_pc, std::uint32_t next_pc,
    std::uint32_t has_loops_to, std::uint32_t loops_to,
    std::uint32_t byte_span, const std::uint8_t* local_widths,
    std::uint32_t local_width_bytes, std::uint32_t local_count,
    std::uint32_t slot_words, std::uint32_t tail_context_helper,
    std::uintptr_t helper_target, std::uint8_t* output,
    std::uint32_t output_capacity, fb_jit_compile_result* result) {
  static_cast<void>(loops_to);
  if (result == nullptr || output == nullptr || byte_span == 0 ||
      instruction_count > byte_span || (instruction_count != 0 && instructions == nullptr) ||
      (local_width_bytes != 0 && local_widths == nullptr) ||
      local_count > kMaxStackLocations ||
      (slot_words != 1 && slot_words != 2 && slot_words != 4)) {
    return -1;
  }
  *result = {};
  trace_builder builder;
  if (!append_stencil(builder, kEntryStencil)) return -2;
  const auto slot_bytes = slot_words * 4;
  if (!compile_operations(builder, instructions, instruction_count, local_widths,
                          local_width_bytes, local_count, slot_bytes)) {
    return -1;
  }
  if (builder.declined || !builder.saw_op || builder.location_count > 1 ||
      builder.spilled_words != 0 || (builder.helper_index < 0 && builder.helper_words != 0)) {
    return 0;
  }

  std::int32_t helper_header = -1;
  std::int32_t helper_exit = -1;
  std::int32_t exit_patch = -1;
  std::int32_t chain_dispatch = -1;
  if (tail_context_helper != 0) {
    if (builder.helper_index >= 0 || builder.location_count != 0 || helper_target == 0) return 0;
    const auto base = builder.body_size;
    if (!append_stencil(builder, kHelperTailStencil)) return 0;
    helper_header = static_cast<std::int32_t>(kTraceHeaderBytes + base + 3);
    helper_exit = static_cast<std::int32_t>(kTraceHeaderBytes + base + 8);
  } else if (builder.helper_index >= 0) {
    if (helper_target == 0 || builder.location_count != 0) return 0;
    const auto base = builder.body_size;
    if (!append_stencil(builder, kHelperTailStencil)) return 0;
    helper_header = static_cast<std::int32_t>(kTraceHeaderBytes + base + 3);
    helper_exit = static_cast<std::int32_t>(kTraceHeaderBytes + base + 8);
  } else {
    if (builder.location_count != 0 && !store_sp(builder, kTos, 0)) return 0;
    if (has_next_pc != 0 && has_loops_to == 0) {
      if (!append_u32_patch(builder, kPublishNextPcStencil, next_pc)) return 0;
      chain_dispatch = static_cast<std::int32_t>(kTraceHeaderBytes + builder.body_size + 1);
      if (!append_stencil(builder, kChainDispatchStencil)) return 0;
    } else {
      exit_patch = static_cast<std::int32_t>(kTraceHeaderBytes + builder.body_size + 1);
      if (!append_stencil(builder, kExitJumpStencil)) return 0;
    }
  }
  if (tail_context_helper != 0) builder.helper_index = -1;
  if (kTraceHeaderBytes + builder.body_size > 0xFFFF ||
      builder.body_size > output_capacity) {
    return -2;
  }

  std::memcpy(output, builder.body.data(), builder.body_size);
  result->body_bytes = static_cast<std::uint32_t>(builder.body_size);
  result->helper_index = builder.helper_index;
  result->helper_words = static_cast<std::uint32_t>(builder.helper_words);
  result->max_spilled_words = static_cast<std::uint32_t>(builder.max_spilled_words);
  result->stack_location_count = static_cast<std::uint32_t>(builder.location_count);
  result->helper_header_patch_offset = helper_header;
  result->helper_exit_patch_offset = helper_exit;
  result->exit_patch_offset = exit_patch;
  result->chain_dispatch_patch_offset = chain_dispatch;
  return 1;
}

extern "C" int fb_jit_compile_wasm_trace(
    const std::uint8_t* code, std::uint32_t code_bytes,
    std::uint32_t code_offset, std::uint32_t byte_span,
    std::uint32_t has_next_pc, std::uint32_t next_pc,
    std::uint32_t has_loops_to, std::uint32_t loops_to,
    const std::uint8_t* local_widths, std::uint32_t local_width_bytes,
    std::uint32_t local_count, std::uint32_t slot_words,
    std::uint8_t* output, std::uint32_t output_capacity,
    fb_jit_compile_result* result) {
  if (code == nullptr || byte_span == 0 || code_offset > code_bytes ||
      byte_span > code_bytes - code_offset || output == nullptr || result == nullptr) {
    return -1;
  }
  std::array<fb_jit_instruction, kMaxBodyBytes> instructions{};
  std::uint32_t instruction_count = 0;
  auto cursor = code_offset;
  const auto end = code_offset + byte_span;

  auto read_unsigned_leb = [&](std::uint32_t& value) -> bool {
    value = 0;
    std::uint32_t shift = 0;
    for (std::uint32_t count = 0; count < 5 && cursor < end; ++count) {
      const auto byte = code[cursor++];
      const auto payload = static_cast<std::uint32_t>(byte & 0x7Fu);
      const auto remaining = 32u - shift;
      if (remaining < 7u && payload >= (1u << remaining)) return false;
      value |= payload << shift;
      if ((byte & 0x80u) == 0) return true;
      shift += 7u;
    }
    return false;
  };

  auto read_signed_leb = [&](std::uint64_t& value, std::uint32_t bits) -> bool {
    std::uint64_t decoded = 0;
    std::uint32_t shift = 0;
    const auto max_bytes = (bits + 6u) / 7u;
    for (std::uint32_t count = 0; count < max_bytes && cursor < end; ++count) {
      const auto byte = code[cursor++];
      const auto payload = static_cast<std::uint64_t>(byte & 0x7Fu);
      const auto remaining = bits - shift;
      const auto contributed = remaining < 7u ? remaining : 7u;
      const bool negative = ((payload >> (contributed - 1u)) & 1u) != 0;
      if (remaining < 7u) {
        const auto unused = payload >> contributed;
        const auto expected = negative ? ((1u << (7u - contributed)) - 1u) : 0u;
        if (unused != expected) return false;
      }
      decoded |= (payload & ((std::uint64_t{1} << contributed) - 1u)) << shift;
      if ((byte & 0x80u) == 0) {
        shift += contributed;
        if (negative && shift < 64u) decoded |= (~std::uint64_t{0}) << shift;
        value = decoded;
        return true;
      }
      if (remaining <= 7u) return false;
      shift += 7u;
    }
    return false;
  };

  while (cursor < end) {
    if (instruction_count >= instructions.size()) return 0;
    const auto opcode = code[cursor++];
    auto& instruction = instructions[instruction_count++];
    instruction.opcode = opcode;
    instruction.has_operand = 0;
    instruction.operand = 0;

    if (opcode == kLocalGet || opcode == kLocalSet || opcode == kLocalTee) {
      std::uint32_t operand = 0;
      if (!read_unsigned_leb(operand)) return -1;
      instruction.has_operand = 1;
      instruction.operand = operand;
    } else if (opcode == kI32Const || opcode == kI64Const) {
      std::uint64_t operand = 0;
      if (!read_signed_leb(operand, opcode == kI32Const ? 32u : 64u)) return -1;
      instruction.has_operand = 1;
      instruction.operand = operand;
    } else if (opcode == kF32Const || opcode == kF64Const) {
      const auto width = opcode == kF32Const ? 4u : 8u;
      if (end - cursor < width) return -1;
      std::uint64_t operand = 0;
      for (std::uint32_t byte = 0; byte < width; ++byte) {
        operand |= static_cast<std::uint64_t>(code[cursor + byte]) << (byte * 8u);
      }
      cursor += width;
      instruction.has_operand = 1;
      instruction.operand = operand;
    } else if (opcode != kDrop && opcode != kI32Eqz && !is_binary(opcode) &&
               opcode != kI32Shl && opcode != kI32ShrS && opcode != kI32ShrU &&
               helper_index(opcode) < 0) {
      // Unsupported operators may carry immediates. Decline the complete
      // block before scanning any immediate bytes as if they were opcodes.
      return 0;
    }
  }
  if (cursor != end) return -1;
  return fb_jit_compile_trace(
      instructions.data(), instruction_count, has_next_pc, next_pc, has_loops_to,
      loops_to, byte_span, local_widths, local_width_bytes, local_count, slot_words,
      0, 0, output, output_capacity, result);
}

extern "C" void fb_jit_common_chain_dispatcher(const std::uint8_t** bytes,
                                                std::uint32_t* byte_count,
                                                std::uint32_t* offset) {
  if (bytes == nullptr || byte_count == nullptr || offset == nullptr) return;
  *bytes = kChainDispatcher.data();
  *byte_count = static_cast<std::uint32_t>(kChainDispatcher.size());
  *offset = FB_CONF_JIT_TRACE_COMMON_CHAIN_DISPATCH_OFFSET;
}

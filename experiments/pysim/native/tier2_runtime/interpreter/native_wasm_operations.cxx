/** @file @brief 共有のWASM命令意味論と固定スタック操作を実装する。 */
#include "native_wasm_operations.hxx"
#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#include <type_traits>

namespace fireball::wasm_runtime {

const fireball_call_frame_native &active_frame(const execution_context &current) {
  assert(current.call_stack != nullptr);
  assert(current.call_stack->size != 0);
  assert(current.call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(current.call_frame_base != nullptr);
  assert(current.call_frame_base >= current.call_stack->frames);
  assert(current.call_frame_base < current.call_stack->frames + current.call_stack->size);
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  assert(frame.function_view != nullptr);
  return frame;
}

const std::uint8_t *interpreter_rel_for_block_index(const fireball_call_frame_native &frame,
                                                    block_index_t block_index) {
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  assert(function.interpreter_annotation_pool != nullptr ||
         function.interpreter_annotation_byte_count == 0);
  assert(function.module_view != nullptr);
  const auto &module = *function.module_view;
  assert(module.blocks != nullptr);
  assert(block_index < module.block_count);
  const auto &block = module.blocks[block_index];
  assert(block.function_index == frame.function_view->function_index);
  const auto offset = block.interpreter_annotation_byte_offset;
  assert(offset != kSentinel);
  assert(offset <= function.interpreter_annotation_byte_count);
  if (function.interpreter_annotation_pool == nullptr) {
    assert(offset == 0 && function.interpreter_annotation_byte_count == 0);
    return nullptr;
  }
  return function.interpreter_annotation_pool + offset;
}

const std::uint8_t *interpreter_rel_after_jit_trace(const fireball_call_frame_native &frame,
                                                    block_index_t block_index,
                                                    annotation_offset_t consumed_bytes) {
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  assert(function.module_view != nullptr);
  const auto &module = *function.module_view;
  assert(module.blocks != nullptr);
  assert(block_index < module.block_count);
  assert(module.blocks[block_index].function_index == frame.function_view->function_index);
  const auto block_offset = module.blocks[block_index].interpreter_annotation_byte_offset;
  assert(block_offset != kSentinel);
  assert(block_offset <= function.interpreter_annotation_byte_count);
  assert(consumed_bytes <= function.interpreter_annotation_byte_count - block_offset);
  const auto byte_offset = block_offset + consumed_bytes;
  if (function.interpreter_annotation_pool == nullptr) {
    assert(byte_offset == 0 && function.interpreter_annotation_byte_count == 0);
    return nullptr;
  }
  return function.interpreter_annotation_pool + byte_offset;
}

template <typename T> bool opcode_eqz(T value) { return value == 0; }

template <typename T> bool opcode_signed_lt(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) < static_cast<std::make_signed_t<T>>(right);
}

template <typename T> bool opcode_signed_gt(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) > static_cast<std::make_signed_t<T>>(right);
}

template <typename T> bool opcode_signed_le(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) <= static_cast<std::make_signed_t<T>>(right);
}

template <typename T> bool opcode_signed_ge(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) >= static_cast<std::make_signed_t<T>>(right);
}

template <typename T> T opcode_clz(T value) { return static_cast<T>(std::countl_zero(value)); }

template <typename T> T opcode_ctz(T value) { return static_cast<T>(std::countr_zero(value)); }

template <typename T> T opcode_popcount(T value) { return static_cast<T>(std::popcount(value)); }

template <typename T> T opcode_shift_left(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  return static_cast<T>(left << (right & shift_mask));
}

template <typename T> T opcode_shift_right_signed(T left, T right) {
  using signed_type = std::make_signed_t<T>;
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  return static_cast<T>(static_cast<signed_type>(left) >> (right & shift_mask));
}

template <typename T> T opcode_shift_right_unsigned(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  return static_cast<T>(left >> (right & shift_mask));
}

template <typename T> T opcode_rotate_left(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  const auto shift = static_cast<unsigned>(right & shift_mask);
  return static_cast<T>((left << shift) |
                        (left >> ((std::numeric_limits<T>::digits - shift) & shift_mask)));
}

template <typename T> T opcode_rotate_right(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  const auto shift = static_cast<unsigned>(right & shift_mask);
  return static_cast<T>((left >> shift) |
                        (left << ((std::numeric_limits<T>::digits - shift) & shift_mask)));
}

template <typename T> bool opcode_div_signed(T left, T right, T &result, std::uint32_t &trap_code) {
  using signed_type = std::make_signed_t<T>;
  const auto signed_left = static_cast<signed_type>(left);
  const auto signed_right = static_cast<signed_type>(right);
  if (signed_right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  if (signed_left == std::numeric_limits<signed_type>::min() && signed_right == -1) {
    trap_code = kTrapIntegerOverflow;
    return false;
  }
  result = static_cast<T>(signed_left / signed_right);
  trap_code = 0;
  return true;
}

template <typename T>
bool opcode_div_unsigned(T left, T right, T &result, std::uint32_t &trap_code) {
  if (right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = left / right;
  trap_code = 0;
  return true;
}

template <typename T> bool opcode_rem_signed(T left, T right, T &result, std::uint32_t &trap_code) {
  using signed_type = std::make_signed_t<T>;
  const auto signed_left = static_cast<signed_type>(left);
  const auto signed_right = static_cast<signed_type>(right);
  if (signed_right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  if (signed_left == std::numeric_limits<signed_type>::min() && signed_right == -1) {
    result = 0;
  } else {
    result = static_cast<T>(signed_left % signed_right);
  }
  trap_code = 0;
  return true;
}

template <typename T>
bool opcode_rem_unsigned(T left, T right, T &result, std::uint32_t &trap_code) {
  if (right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = left % right;
  trap_code = 0;
  return true;
}

template <typename T> T opcode_extend8_signed(T value) {
  return static_cast<T>(static_cast<std::int8_t>(value));
}

template <typename T> T opcode_extend16_signed(T value) {
  return static_cast<T>(static_cast<std::int16_t>(value));
}

template <typename T> T opcode_float_abs(T value) { return std::fabs(value); }

template <typename T> T opcode_float_neg(T value) { return -value; }

template <typename T> T opcode_float_ceil(T value) { return std::ceil(value); }

template <typename T> T opcode_float_floor(T value) { return std::floor(value); }

template <typename T> T opcode_float_trunc(T value) { return std::trunc(value); }

template <typename T> T opcode_float_nearest(T value) { return std::nearbyint(value); }

template <typename T> T opcode_float_sqrt(T value) { return std::sqrt(value); }

template <typename T> T opcode_float_div(T left, T right) {
  if (right != 0) {
    return left / right;
  }
  if (left == 0 || std::isnan(left)) {
    return std::numeric_limits<T>::quiet_NaN();
  }
  const auto sign = std::signbit(left) == std::signbit(right) ? T{1} : T{-1};
  return std::copysign(std::numeric_limits<T>::infinity(), sign);
}

template <typename T> T opcode_float_min(T left, T right) {
  if (std::isnan(left) || std::isnan(right)) {
    return std::numeric_limits<T>::quiet_NaN();
  }
  if (left == 0 && right == 0) {
    return std::signbit(left) ? left : right;
  }
  return left < right ? left : right;
}

template <typename T> T opcode_float_max(T left, T right) {
  if (std::isnan(left) || std::isnan(right)) {
    return std::numeric_limits<T>::quiet_NaN();
  }
  if (left == 0 && right == 0) {
    return std::signbit(left) ? right : left;
  }
  return left > right ? left : right;
}

template <typename T> T opcode_float_copysign(T left, T right) {
  return std::copysign(left, right);
}
void record_cps_exit(execution_context &current, std::uint32_t kind) {
  current.runtime_flags = (current.runtime_flags & ~kCpsExitMask) | ((kind + 1) << kCpsExitShift);
}

std::uint32_t cps_exit_kind(const execution_context &current) {
  const auto encoded = (current.runtime_flags & kCpsExitMask) >> kCpsExitShift;
  assert(encoded != 0 && encoded <= 9);
  return encoded - 1;
}

bool cps_exit_pending(const execution_context &current) {
  return (current.runtime_flags & kCpsExitMask) != 0;
}

void clear_cps_exit(execution_context &current) { current.runtime_flags &= ~kCpsExitMask; }

bool runtime_api_boundary(execution_context &current, code_ip_t ip, const std::uint8_t *rel) {
  current.ip = ip;
  current.rel = rel;
  current.runtime_flags |= kRelCursorReadyFlag;
  current.trap_code = 0;
  record_cps_exit(current, kRuntimeApiBoundary);
  return false;
}

code_ip_t code_ip_for_block_index(const execution_context &current, block_index_t block_index) {
  assert(block_index != kInvalidBlockIndex);
  const auto &frame = active_frame(current);
  const auto *module = frame.function_view->module_view;
  assert(module != nullptr && module->blocks != nullptr);
  assert(block_index < module->block_count);
  const auto head_pc = module->blocks[block_index].head_pc;
  assert(head_pc >= frame.function_view->code_pc_offset);
  const auto ip = head_pc - frame.function_view->code_pc_offset;
  assert(ip < current.code_size);
  return ip;
}

void set_block_cursor(execution_context &current, code_ip_t ip, block_index_t block_index,
                      const std::uint8_t *&rel) {
  const auto &frame = active_frame(current);
  assert(ip < current.code_size);
  assert(frame.function_view != nullptr && frame.function_view->module_view != nullptr);
  const auto &module = *frame.function_view->module_view;
  assert(module.blocks != nullptr && block_index != kInvalidBlockIndex);
  assert(block_index < module.block_count);
  assert(module.blocks[block_index].head_pc == frame.function_view->code_pc_offset + ip);
  current.current_block_index = block_index;
  rel = interpreter_rel_for_block_index(frame, block_index);
  current.ip = ip;
  current.rel = rel;
  current.runtime_flags |= kRelCursorReadyFlag;
  current.trap_code = 0;
}

bool block_boundary(execution_context &current, code_ip_t ip, block_index_t block_index,
                    const std::uint8_t *&rel) {
  set_block_cursor(current, ip, block_index, rel);
  record_cps_exit(current, kBlockBoundary);
  return false;
}

bool continue_to_block(execution_context &current, code_ip_t ip, block_index_t block_index,
                       const std::uint8_t *&rel) {
  set_block_cursor(current, ip, block_index, rel);
  if ((current.runtime_flags & (kSingleControlStepFlag | kJitDispatchFlag)) != 0) {
    record_cps_exit(current, kBlockBoundary);
    return false;
  }
  return true;
}

bool continue_within_block(execution_context &current, code_ip_t ip,
                           const std::uint8_t *&rel) {
  if ((current.runtime_flags & kSingleControlStepFlag) == 0) {
    return true;
  }
  current.ip = ip;
  current.rel = rel;
  current.runtime_flags |= kRelCursorReadyFlag;
  current.trap_code = 0;
  record_cps_exit(current, kBlockBoundary);
  return false;
}

bool complete(execution_context &current) {
  current.ip = kSentinel;
  current.current_block_index = kInvalidBlockIndex;
  current.runtime_flags &= ~(kPendingBlockHeadFlag | kRelCursorReadyFlag);
  current.trap_code = 0;
  record_cps_exit(current, kComplete);
  return false;
}

bool trap(execution_context &current, std::uint32_t code) {
  current.trap_code = code;
  current.runtime_flags &= ~kRelCursorReadyFlag;
  record_cps_exit(current, kTrap);
  return false;
}

bool call_boundary(execution_context &current, code_ip_t ip, block_index_t block_index,
                   const std::uint8_t *&rel) {
  set_block_cursor(current, ip, block_index, rel);
  record_cps_exit(current, kCallBoundary);
  return false;
}

bool debug_stop(execution_context &current) {
  current.trap_code = 0;
  record_cps_exit(current, kDebugStop);
  return false;
}

std::uint8_t read_u8(const std::uint8_t *&rel) { return *rel++; }

std::uint16_t read_u16(const std::uint8_t *&rel) {
  std::uint16_t value;
  std::memcpy(&value, rel, sizeof(value));
  rel += sizeof(value);
  return value;
}

std::uint32_t read_u32(const std::uint8_t *&rel) {
  std::uint32_t value;
  std::memcpy(&value, rel, sizeof(value));
  rel += sizeof(value);
  return value;
}

std::uint8_t peek_u8(const std::uint8_t *rel) { return *rel; }

constexpr auto make_instruction_width_table() {
  // Zero marks the opcodes whose encoded width is stored in the annotation pool.
  std::array<std::uint8_t, 256> widths{};
  widths.fill(1);
  for (const auto opcode : {0x02u, 0x03u, 0x04u, 0x3Fu, 0x40u}) {
    widths[opcode] = 2;
  }
  widths[0x43u] = 5;
  widths[0x44u] = 9;
  for (const auto opcode :
       {0x0Du, 0x10u, 0x11u, 0x20u, 0x21u, 0x22u, 0x23u, 0x24u, 0x41u, 0x42u, 0xFCu}) {
    widths[opcode] = 0;
  }
  for (std::uint32_t opcode = 0x28u; opcode <= 0x3Eu; ++opcode) {
    widths[opcode] = 0;
  }
  return widths;
}

constexpr auto kInstructionWidthTable = make_instruction_width_table();

instruction_width_t instruction_width(const std::uint8_t *ip, const std::uint8_t *rel) {
  const auto fixed_width = kInstructionWidthTable[*ip];
  return fixed_width != 0 ? fixed_width : *rel;
}

instruction_width_t consume_instruction_width(const std::uint8_t *ip, const std::uint8_t *&rel) {
  const auto fixed_width = kInstructionWidthTable[*ip];
  if (fixed_width != 0) {
    return fixed_width;
  }
  return *rel++;
}

void advance_instruction_ip(const std::uint8_t *&ip, const std::uint8_t *&rel) {
  ip += consume_instruction_width(ip, rel);
}

code_ip_t instruction_offset(const execution_context &current, const std::uint8_t *ip) {
  assert(current.code != nullptr && ip >= current.code && ip <= current.code + current.code_size);
  return static_cast<std::uint32_t>(ip - current.code);
}

std::uint32_t peek_u32(const std::uint8_t *rel) {
  std::uint32_t value = 0;
  std::memcpy(&value, rel, sizeof(value));
  return value;
}

std::int32_t read_s32(const std::uint8_t *&rel) { return static_cast<std::int32_t>(read_u32(rel)); }

std::int64_t read_s64(const std::uint8_t *&rel) {
  std::uint64_t value;
  std::memcpy(&value, rel, sizeof(value));
  rel += sizeof(value);
  return static_cast<std::int64_t>(value);
}

bool pop_u64([[maybe_unused]] execution_context &current, std::uint32_t *&sp,
             std::uint64_t &value) {
  assert(sp >= current.stack_base + 2);
  const auto low = sp[-2];
  const auto high = sp[-1];
  sp -= 2;
  value = static_cast<std::uint64_t>(low) | (static_cast<std::uint64_t>(high) << 32);
  return true;
}

bool push_u64([[maybe_unused]] execution_context &current, std::uint32_t *&sp,
              std::uint64_t value) {
  assert(!(current.stack_limit - sp < 2));
  *sp++ = static_cast<std::uint32_t>(value);
  *sp++ = static_cast<std::uint32_t>(value >> 32);
  return true;
}

std::uint64_t combine_u64(std::uint32_t low, std::uint32_t high) {
  return static_cast<std::uint64_t>(low) | (static_cast<std::uint64_t>(high) << 32);
}

template <bool ResultIsBool>
void replace_u64_operands(std::uint32_t *&sp, std::uint64_t result, operand_words_t operand_words) {
  constexpr auto result_words = ResultIsBool ? 1u : 2u;
  auto *target = sp - operand_words;
  sp = target + result_words;
  target[0] = static_cast<std::uint32_t>(result);
  if constexpr (!ResultIsBool) {
    target[1] = static_cast<std::uint32_t>(result >> 32);
  }
}

float f32_from_bits(std::uint32_t value) { return std::bit_cast<float>(value); }

std::uint32_t f32_to_bits(float value) { return std::bit_cast<std::uint32_t>(value); }

double f64_from_bits(std::uint64_t value) { return std::bit_cast<double>(value); }

std::uint64_t f64_to_bits(double value) { return std::bit_cast<std::uint64_t>(value); }

bool pop_f32(execution_context &current, std::uint32_t *&sp, float &value) {
  std::uint32_t raw = 0;
  if (!pop(current, sp, raw)) {
    return false;
  }
  value = f32_from_bits(raw);
  return true;
}

bool push_f32(execution_context &current, std::uint32_t *&sp, float value) {
  return push(current, sp, f32_to_bits(value));
}

bool pop_f64(execution_context &current, std::uint32_t *&sp, double &value) {
  std::uint64_t raw = 0;
  if (!pop_u64(current, sp, raw)) {
    return false;
  }
  value = f64_from_bits(raw);
  return true;
}

bool push_f64(execution_context &current, std::uint32_t *&sp, double value) {
  return push_u64(current, sp, f64_to_bits(value));
}

fireball_call_frame_native &active_frame(execution_context &current) {
  assert(current.call_stack != nullptr);
  assert(current.call_stack->size != 0);
  assert(current.call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(current.call_frame_base != nullptr);
  assert(current.call_frame_base >= current.call_stack->frames);
  assert(current.call_frame_base < current.call_stack->frames + current.call_stack->size);
  auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  assert(frame.function_view != nullptr);
  return frame;
}

bool finish_current_function(execution_context &current, std::uint32_t *&local_base,
                             const std::uint8_t *&instruction_ip) {
  auto *stack = current.call_stack;
  assert(stack != nullptr);
  assert(stack->size != 0);
  if (&active_frame(current) == current.call_frame_base) {
    return true;
  }

  const auto finished = stack->frames[stack->size - 1];
  assert(finished.return_ip != kSentinel);
  --stack->size;
  current.local_stack_pointer = finished.local_base;

  auto &caller = stack->frames[stack->size - 1];
  assert(caller.function_view != nullptr);
  local_base = caller.local_base;
  current.local_base = local_base;
  current.code = caller.function_view->code;
  current.code_size = caller.function_view->code_size;
  current.current_block_index = finished.return_block_index;
  instruction_ip = current.code + finished.return_ip;
  return false;
}

bool finish_function_boundary(execution_context &current, std::uint32_t *&local_base,
                              const std::uint8_t *&instruction_ip,
                              const std::uint8_t *&rel) {
  if (finish_current_function(current, local_base, instruction_ip)) {
    return complete(current);
  }
  const auto next_ip = instruction_offset(current, instruction_ip);
  return block_boundary(current, next_ip, current.current_block_index, rel);
}

bool is_requested_block_boundary(const execution_context &current, code_ip_t ip) {
  const auto &frame = active_frame(current);
  const auto pc = frame.function_view->code_pc_offset + ip;
  return pc == frame.boundary_next_pc || pc == frame.boundary_loops_to;
}

bool record_loop_backedge(execution_context &current, code_ip_t source_ip, code_ip_t target_ip) {
  if (target_ip > source_ip) {
    return false;
  }
  if (current.loop_jump_count < UINT32_MAX) {
    ++current.loop_jump_count;
  }
  return true;
}

local_value_span local_span(const fireball_call_frame_native &frame, local_count_t index) {
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  assert(index < function.local_count);
  const frame_word_offset_t offset = function.local_offsets[index];
  const auto value_type = function.local_types[index];
  const operand_words_t width =
      static_cast<operand_words_t>(value_type == 0x7Eu || value_type == 0x7Cu ? 2u : 1u);
  assert(width == 1 || width == 2);
  assert(offset + width <= function.local_slot_count);
  return {offset, width};
}

void branch(execution_context &current, std::uint32_t *&sp, block_index_t target_block_index,
            operand_words_t target_stack_base_words, operand_words_t result_words) {
  const auto &call_frame = active_frame(current);
  const auto *module = call_frame.function_view->module_view;
  assert(module != nullptr);
  if (target_block_index != kInvalidBlockIndex) {
    assert(target_block_index < module->block_count);
    const auto &target_block = module->blocks[target_block_index];
    assert(target_block.head_pc >= call_frame.function_view->code_pc_offset);
    assert(target_block.head_pc - call_frame.function_view->code_pc_offset < current.code_size);
  }
  assert(result_words <= 2);
  assert(call_frame.operand_stack_base >= current.stack_base);
  assert(target_stack_base_words <=
         static_cast<std::uint32_t>(current.stack_limit - call_frame.operand_stack_base));
  auto *target_stack = call_frame.operand_stack_base + target_stack_base_words;
  assert(target_stack >= current.stack_base && target_stack + result_words <= current.stack_limit);
  assert(sp >= target_stack + result_words);
  const auto result0 = result_words >= 1 ? sp[-static_cast<std::ptrdiff_t>(result_words)] : 0;
  const auto result1 = result_words >= 2 ? sp[-1] : 0;
  if (result_words >= 1) {
    target_stack[0] = result0;
  }
  if (result_words >= 2) {
    target_stack[1] = result1;
  }
  sp = target_stack + result_words;
}

bool pop([[maybe_unused]] execution_context &current, std::uint32_t *&sp, std::uint32_t &value) {
  assert(sp > current.stack_base);
  value = sp[-1];
  --sp;
  return true;
}

bool push([[maybe_unused]] execution_context &current, std::uint32_t *&sp, std::uint32_t value) {
  assert(!(sp >= current.stack_limit));
  *sp++ = value;
  return true;
}

std::uint64_t opcode_extend32_signed(std::uint64_t value) {
  return static_cast<std::uint64_t>(static_cast<std::int64_t>(static_cast<std::int32_t>(value)));
}

bool linear_memory_range_is_valid(const execution_context &current, std::uint64_t offset,
                                  std::uint32_t length) {
  const auto wide_length = static_cast<std::uint64_t>(length);
  return offset <= current.linear_memory_size && wide_length <= current.linear_memory_size - offset;
}

bool conversion_i32_wrap_i64(execution_context &current, std::uint32_t *&sp,
                             std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value) || !push(current, sp, static_cast<std::uint32_t>(value))) {
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i32_trunc_f32_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !std::isfinite(value) || value < -2147483648.0f ||
      value >= 2147483648.0f) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  if (!push(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i32_trunc_f32_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !std::isfinite(value) || value <= -1.0f ||
      value >= 4294967296.0f) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  if (!push(current, sp, static_cast<std::uint32_t>(value))) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i32_trunc_f64_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !std::isfinite(value) || value < -2147483648.0 ||
      value >= 2147483648.0) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  if (!push(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i32_trunc_f64_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !std::isfinite(value) || value <= -1.0 ||
      value >= 4294967296.0) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  if (!push(current, sp, static_cast<std::uint32_t>(value))) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_trunc_f32_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !std::isfinite(value) || value < -9223372036854775808.0f ||
      value >= 9223372036854775808.0f) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result = value == -9223372036854775808.0f
                          ? 0x8000'0000'0000'0000ull
                          : static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  if (!push_u64(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_trunc_f32_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !std::isfinite(value) || value <= -1.0f ||
      value >= 18446744073709551616.0f) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  if (!push_u64(current, sp, static_cast<std::uint64_t>(value))) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_trunc_f64_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !std::isfinite(value) || value < -9223372036854775808.0 ||
      value >= 9223372036854775808.0) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result = value == -9223372036854775808.0
                          ? 0x8000'0000'0000'0000ull
                          : static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  if (!push_u64(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_trunc_f64_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !std::isfinite(value) || value <= -1.0 ||
      value >= 18446744073709551616.0) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  if (!push_u64(current, sp, static_cast<std::uint64_t>(value))) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i32_trunc_sat_f32_s(execution_context &current, std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value)) {
    return false;
  }
  std::uint32_t result = 0;
  if (std::isnan(value)) {
    result = 0;
  } else if (value <= -2147483648.0f) {
    result = 0x8000'0000u;
  } else if (value >= 2147483648.0f) {
    result = 0x7FFF'FFFFu;
  } else {
    result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  }
  return push(current, sp, result);
}

bool conversion_i32_trunc_sat_f32_u(execution_context &current, std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value)) {
    return false;
  }
  std::uint32_t result = 0;
  if (std::isnan(value) || value <= 0.0f) {
    result = 0;
  } else if (value >= 4294967296.0f) {
    result = 0xFFFF'FFFFu;
  } else {
    result = static_cast<std::uint32_t>(value);
  }
  return push(current, sp, result);
}

bool conversion_i32_trunc_sat_f64_s(execution_context &current, std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value)) {
    return false;
  }
  std::uint32_t result = 0;
  if (std::isnan(value)) {
    result = 0;
  } else if (value <= -2147483648.0) {
    result = 0x8000'0000u;
  } else if (value >= 2147483648.0) {
    result = 0x7FFF'FFFFu;
  } else {
    result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  }
  return push(current, sp, result);
}

bool conversion_i32_trunc_sat_f64_u(execution_context &current, std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value)) {
    return false;
  }
  std::uint32_t result = 0;
  if (std::isnan(value) || value <= 0.0) {
    result = 0;
  } else if (value >= 4294967296.0) {
    result = 0xFFFF'FFFFu;
  } else {
    result = static_cast<std::uint32_t>(value);
  }
  return push(current, sp, result);
}

bool conversion_i64_trunc_sat_f32_s(execution_context &current, std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value)) {
    return false;
  }
  std::uint64_t result = 0;
  if (std::isnan(value)) {
    result = 0;
  } else if (value <= -9223372036854775808.0f) {
    result = 0x8000'0000'0000'0000ull;
  } else if (value >= 9223372036854775808.0f) {
    result = 0x7FFF'FFFF'FFFF'FFFFull;
  } else {
    result = static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  }
  return push_u64(current, sp, result);
}

bool conversion_i64_trunc_sat_f32_u(execution_context &current, std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value)) {
    return false;
  }
  std::uint64_t result = 0;
  if (std::isnan(value) || value <= 0.0f) {
    result = 0;
  } else if (value >= 18446744073709551616.0f) {
    result = 0xFFFF'FFFF'FFFF'FFFFull;
  } else {
    result = static_cast<std::uint64_t>(value);
  }
  return push_u64(current, sp, result);
}

bool conversion_i64_trunc_sat_f64_s(execution_context &current, std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value)) {
    return false;
  }
  std::uint64_t result = 0;
  if (std::isnan(value)) {
    result = 0;
  } else if (value <= -9223372036854775808.0) {
    result = 0x8000'0000'0000'0000ull;
  } else if (value >= 9223372036854775808.0) {
    result = 0x7FFF'FFFF'FFFF'FFFFull;
  } else {
    result = static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  }
  return push_u64(current, sp, result);
}

bool conversion_i64_trunc_sat_f64_u(execution_context &current, std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value)) {
    return false;
  }
  std::uint64_t result = 0;
  if (std::isnan(value) || value <= 0.0) {
    result = 0;
  } else if (value >= 18446744073709551616.0) {
    result = 0xFFFF'FFFF'FFFF'FFFFull;
  } else {
    result = static_cast<std::uint64_t>(value);
  }
  return push_u64(current, sp, result);
}

bool conversion_i64_extend_i32_s(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value) || !push_u64(current, sp,
                                            static_cast<std::uint64_t>(static_cast<std::int64_t>(
                                                static_cast<std::int32_t>(value))))) {
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_extend_i32_u(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value) || !push_u64(current, sp, value)) {
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_f32_convert_i32_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return false;
  }
  return push_f32(current, sp, static_cast<float>(static_cast<std::int32_t>(value)));
}

bool conversion_f32_convert_i32_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return false;
  }
  return push_f32(current, sp, static_cast<float>(value));
}

bool conversion_f64_convert_i32_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return false;
  }
  return push_f64(current, sp, static_cast<double>(static_cast<std::int32_t>(value)));
}

bool conversion_f64_convert_i32_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return false;
  }
  return push_f64(current, sp, static_cast<double>(value));
}

bool conversion_f32_convert_i64_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value)) {
    return false;
  }
  return push_f32(current, sp, static_cast<float>(static_cast<std::int64_t>(value)));
}

bool conversion_f32_convert_i64_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value)) {
    return false;
  }
  return push_f32(current, sp, static_cast<float>(value));
}

bool conversion_f64_convert_i64_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value)) {
    return false;
  }
  return push_f64(current, sp, static_cast<double>(static_cast<std::int64_t>(value)));
}

bool conversion_f64_convert_i64_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value)) {
    return false;
  }
  return push_f64(current, sp, static_cast<double>(value));
}

bool conversion_f64_promote_f32(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !push_f64(current, sp, static_cast<double>(value))) {
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_f32_demote_f64(execution_context &current, std::uint32_t *&sp,
                               std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !push_f32(current, sp, static_cast<float>(value))) {
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_identity(execution_context &, std::uint32_t *&, std::uint32_t &trap_code) {
  trap_code = 0;
  return true;
}

bool runtime_nop(execution_context *, const std::uint8_t *&instruction_ip,
                 [[maybe_unused]] std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                 [[maybe_unused]] const std::uint8_t *&rel) {
  advance_instruction_ip(instruction_ip, rel);
  return true;
}

bool runtime_else(execution_context *context, const std::uint8_t *&instruction_ip,
                  [[maybe_unused]] std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto target_block_index = static_cast<block_index_t>(read_u16(rel));
  const auto next_ip = code_ip_for_block_index(current, target_block_index);
  instruction_ip = current.code + next_ip;
  return continue_to_block(current, next_ip, target_block_index, rel);
}

bool runtime_block(execution_context *context, const std::uint8_t *&instruction_ip,
                   [[maybe_unused]] std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                   const std::uint8_t *&rel) {
  if (*instruction_ip == 0x02u) {
    advance_instruction_ip(instruction_ip, rel);
    return continue_within_block(*context, instruction_offset(*context, instruction_ip), rel);
  }
  auto &current = *context;
  const auto target_block_index = static_cast<block_index_t>(read_u16(rel));
  const auto next_ip = code_ip_for_block_index(current, target_block_index);
  instruction_ip = current.code + next_ip;
  return continue_to_block(current, next_ip, target_block_index, rel);
}

bool runtime_br_if(execution_context *context, const std::uint8_t *&instruction_ip,
                   std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = instruction_offset(current, instruction_ip);
  const auto width = consume_instruction_width(instruction_ip, rel);
  std::uint32_t condition = 0;
  const auto target_block_index = static_cast<block_index_t>(read_u16(rel));
  const auto target_stack_base_words = read_u8(rel);
  const auto result_words = read_u8(rel);
  (void)pop(current, sp, condition);
  if (condition == 0) {
    const auto next_ip = source_ip + width;
    const auto &frame = active_frame(current);
    const auto *module = frame.function_view->module_view;
    assert(module != nullptr && current.current_block_index < module->block_count);
    const auto &source_block = module->blocks[current.current_block_index];
    assert(source_block.next_block_index != kInvalidBlockIndex);
    assert(source_block.next_pc == frame.function_view->code_pc_offset + next_ip);
    instruction_ip = current.code + next_ip;
    return block_boundary(current, next_ip, source_block.next_block_index, rel);
  }
  branch(current, sp, target_block_index, target_stack_base_words, result_words);
  if (target_block_index == kInvalidBlockIndex) {
    return finish_function_boundary(current, local_base, instruction_ip, rel);
  }
  const auto next_ip = code_ip_for_block_index(current, target_block_index);
  record_loop_backedge(current, source_ip, next_ip);
  instruction_ip = current.code + next_ip;
  return block_boundary(current, next_ip, target_block_index, rel);
}

bool apply_local_get(execution_context *context, std::uint32_t *&sp, std::uint32_t *&local_base,
                     const fireball_call_frame_native &frame, local_count_t index) {
  [[maybe_unused]] auto &current = *context;
  assert(sp >= current.stack_base && sp <= current.stack_limit);
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  if (function.local_slot_count == function.local_count) {
    assert(index < function.local_count);
    assert(!(sp == current.stack_limit));
    *sp++ = local_base[index];
  } else {
    const auto [offset, width] = local_span(frame, index);
    assert(!(current.stack_limit - sp < width));
    if (width == 1) {
      *sp++ = local_base[offset];
    } else {
      sp[0] = local_base[offset];
      sp[1] = local_base[offset + 1];
      sp += 2;
    }
  }
  return true;
}

bool apply_local_set(execution_context *context, std::uint32_t *&sp, std::uint32_t *&local_base,
                     const fireball_call_frame_native &frame, local_count_t index) {
  [[maybe_unused]] auto &current = *context;
  const auto &function = *frame.function_view;
  if (function.local_slot_count == function.local_count) {
    assert(index < function.local_count);
    assert(sp >= current.stack_base + 1);
    local_base[index] = sp[-1];
    --sp;
  } else {
    const auto [offset, width] = local_span(frame, index);
    assert(sp >= current.stack_base + width);
    if (width == 1) {
      local_base[offset] = sp[-1];
    } else {
      local_base[offset] = sp[-2];
      local_base[offset + 1] = sp[-1];
    }
    sp -= width;
  }
  return true;
}

bool apply_local_tee(execution_context *context, std::uint32_t *&sp, std::uint32_t *&local_base,
                     const fireball_call_frame_native &frame, local_count_t index) {
  [[maybe_unused]] auto &current = *context;
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  if (function.local_slot_count == function.local_count) {
    assert(index < function.local_count);
    assert(sp >= current.stack_base + 1);
    local_base[index] = sp[-1];
  } else {
    const auto [offset, width] = local_span(frame, index);
    assert(sp >= current.stack_base + width);
    if (width == 1) {
      local_base[offset] = sp[-1];
    } else {
      local_base[offset] = sp[-2];
      local_base[offset + 1] = sp[-1];
    }
  }
  return true;
}

bool runtime_local_get_annotated(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t *local_base, frame_word_offset_t offset,
                                 operand_words_t width);
bool runtime_local_set_annotated(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t *local_base, frame_word_offset_t offset,
                                 operand_words_t width, bool pop_value);

bool runtime_local_get_decoded(execution_context *context, std::uint32_t *&sp,
                               std::uint32_t *&local_base, local_count_t index) {
  auto &current = *context;
  return apply_local_get(context, sp, local_base, active_frame(current), index);
}

bool runtime_local_get(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto offset = static_cast<frame_word_offset_t>(read_u16(rel));
  const auto width = read_u8(rel);
  if (!runtime_local_get_annotated(current, sp, local_base, offset, width)) {
    return false;
  }
  instruction_ip += encoded_width;
  return true;
}

bool runtime_local_set_decoded(execution_context *context, std::uint32_t *&sp,
                               std::uint32_t *&local_base, local_count_t index) {
  auto &current = *context;
  return apply_local_set(context, sp, local_base, active_frame(current), index);
}

bool runtime_local_set(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto offset = static_cast<frame_word_offset_t>(read_u16(rel));
  const auto width = read_u8(rel);
  if (!runtime_local_set_annotated(current, sp, local_base, offset, width, true)) {
    return false;
  }
  instruction_ip += encoded_width;
  return true;
}

bool runtime_local_tee_decoded(execution_context *context, std::uint32_t *&sp,
                               std::uint32_t *&local_base, local_count_t index) {
  auto &current = *context;
  return apply_local_tee(context, sp, local_base, active_frame(current), index);
}

bool runtime_local_get_annotated(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t *local_base, frame_word_offset_t offset,
                                 operand_words_t width) {
  [[maybe_unused]] const auto &function = *active_frame(current).function_view;
  assert((width == 1 || width == 2) && offset + width <= function.local_slot_count);
  assert(!(current.stack_limit - sp < width));
  sp[0] = local_base[offset];
  if (width == 2) {
    sp[1] = local_base[offset + 1];
  }
  sp += width;
  return true;
}

bool runtime_local_set_annotated(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t *local_base, frame_word_offset_t offset,
                                 operand_words_t width, bool pop_value) {
  [[maybe_unused]] const auto &function = *active_frame(current).function_view;
  assert((width == 1 || width == 2) && offset + width <= function.local_slot_count);
  assert(sp >= current.stack_base + width);
  if (width == 1) {
    local_base[offset] = sp[-1];
  } else {
    local_base[offset] = sp[-2];
    local_base[offset + 1] = sp[-1];
  }
  if (pop_value) {
    sp -= width;
  }
  return true;
}

bool runtime_local_tee(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto offset = static_cast<frame_word_offset_t>(read_u16(rel));
  const auto width = read_u8(rel);
  if (!runtime_local_set_annotated(current, sp, local_base, offset, width, false)) {
    return false;
  }
  instruction_ip += encoded_width;
  return true;
}

bool runtime_global_get(execution_context *context, const std::uint8_t *&instruction_ip,
                        std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                        [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto index = static_cast<global_index_t>(read_u16(rel));

  const auto &frame = active_frame(current);
  const auto *module = frame.function_view->module_view;
  assert(module != nullptr && index < module->global_count);
  assert(module->globals != nullptr && module->global_widths != nullptr);

  const auto width = module->global_widths[index];
  assert(width == 1u || width == 2u);
  assert(!(current.stack_limit - sp < width));
  const auto value = module->globals[index];
  *sp++ = static_cast<std::uint32_t>(value);
  if (width == 2u) {
    *sp++ = static_cast<std::uint32_t>(value >> 32);
  }
  instruction_ip += encoded_width;
  return true;
}

bool runtime_global_set(execution_context *context, const std::uint8_t *&instruction_ip,
                        std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                        [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto index = static_cast<global_index_t>(read_u16(rel));

  const auto &frame = active_frame(current);
  const auto *module = frame.function_view->module_view;
  assert(module != nullptr && index < module->global_count);
  assert(module->globals != nullptr && module->global_widths != nullptr);

  const auto width = module->global_widths[index];
  assert(width == 1u || width == 2u);
  assert(!(sp < current.stack_base + width));
  auto value = static_cast<std::uint64_t>(sp[-static_cast<std::ptrdiff_t>(width)]);
  if (width == 2u) {
    value |= static_cast<std::uint64_t>(sp[-1]) << 32;
  }
  module->globals[index] = value;
  sp -= width;
  instruction_ip += encoded_width;
  return true;
}

bool runtime_drop(execution_context *context, const std::uint8_t *&instruction_ip,
                  std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  const auto width = read_u8(rel);
  assert(width == 1 || width == 2);
  assert(sp >= current.stack_base + width);
  sp -= width;
  advance_instruction_ip(instruction_ip, rel);
  return true;
}

bool runtime_select(execution_context *context, const std::uint8_t *&instruction_ip,
                    std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                    [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  const auto width = read_u8(rel);
  assert(width == 1 || width == 2);
  assert(sp >= current.stack_base + 1 + width * 2);
  const auto condition = sp[-1];
  --sp;
  if (condition == 0) {
    for (std::uint32_t word = 0; word < width; ++word) {
      sp[word - static_cast<std::ptrdiff_t>(2 * width)] =
          sp[word - static_cast<std::ptrdiff_t>(width)];
    }
  }
  sp -= width;
  advance_instruction_ip(instruction_ip, rel);
  return true;
}

bool runtime_end(execution_context *context, const std::uint8_t *&instruction_ip,
                 [[maybe_unused]] std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                 [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  advance_instruction_ip(instruction_ip, rel);
  const auto next_block_index = static_cast<block_index_t>(read_u16(rel));
  if (next_block_index == kInvalidBlockIndex) {
    return finish_function_boundary(current, local_base, instruction_ip, rel);
  }
  if (next_block_index == current.current_block_index) {
    return continue_within_block(current, instruction_offset(current, instruction_ip), rel);
  }
  const auto next_ip = code_ip_for_block_index(current, next_block_index);
  instruction_ip = current.code + next_ip;
  return continue_to_block(current, next_ip, next_block_index, rel);
}

bool runtime_return(execution_context *context, const std::uint8_t *&instruction_ip,
                    [[maybe_unused]] std::uint32_t *&sp,
                    [[maybe_unused]] std::uint32_t *&local_base,
                    [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  return finish_function_boundary(current, local_base, instruction_ip, rel);
}

bool runtime_br_table(execution_context *context, const std::uint8_t *&instruction_ip,
                      std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                      [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = instruction_offset(current, instruction_ip);
  assert(source_ip < current.code_size);

  constexpr std::size_t kBrTableCountBytes = sizeof(std::uint16_t);
  constexpr std::size_t kBrTableTargetBytes = 4;
  const auto *table_targets = rel + kBrTableCountBytes;
  const auto case_count = read_u16(rel);
  std::uint32_t selector = 0;
  (void)pop(current, sp, selector);

  const auto selected_index = selector < case_count ? selector : case_count;
  const auto *selected_rel =
      table_targets + static_cast<std::size_t>(selected_index) * kBrTableTargetBytes;
  auto *selected_block_rel = selected_rel;
  const auto selected_block_index = read_u16(selected_block_rel);
  const auto selected_stack_base_words = read_u8(selected_block_rel);
  const auto selected_result_words = read_u8(selected_block_rel);
  branch(current, sp, selected_block_index, selected_stack_base_words, selected_result_words);
  if (selected_block_index == kInvalidBlockIndex) {
    return finish_function_boundary(current, local_base, instruction_ip, rel);
  }
  const auto next_ip = code_ip_for_block_index(current, selected_block_index);
  record_loop_backedge(current, source_ip, next_ip);
  instruction_ip = current.code + next_ip;
  return block_boundary(current, next_ip, selected_block_index, rel);
}

bool runtime_i32_const(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto value = read_s32(rel);
  (void)push(current, sp, static_cast<std::uint32_t>(value));
  instruction_ip += encoded_width;
  return true;
}

template <bool (*Operation)(execution_context &, std::uint32_t *&, std::uint32_t &)>
bool runtime_conversion(execution_context *context, const std::uint8_t *&instruction_ip,
                        std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                        [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  std::uint32_t trap_code = 0;
  if (!Operation(current, sp, trap_code)) {
    assert(trap_code != 0);
    return trap(current, trap_code);
  }
  advance_instruction_ip(instruction_ip, rel);
  return true;
}

bool runtime_i64_const(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto value = read_s64(rel);
  (void)push_u64(current, sp, static_cast<std::uint64_t>(value));
  instruction_ip += encoded_width;
  return true;
}

bool runtime_f32_const(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  std::uint32_t bits = 0;
  std::memcpy(&bits, instruction_ip + 1, sizeof(bits));
  (void)push(*context, sp, static_cast<std::uint32_t>(bits));
  advance_instruction_ip(instruction_ip, rel);
  return true;
}

bool runtime_f64_const(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  std::uint64_t bits = 0;
  std::memcpy(&bits, instruction_ip + 1, sizeof(bits));
  (void)push_u64(*context, sp, bits);
  advance_instruction_ip(instruction_ip, rel);
  return true;
}

template <std::uint32_t Width> std::uint64_t sign_extend_memory_value(std::uint64_t value) {
  if constexpr (Width < sizeof(std::uint64_t)) {
    constexpr auto sign_bit = std::uint64_t{1} << (Width * 8 - 1);
    return (value ^ sign_bit) - sign_bit;
  }
  return value;
}

template <bool (*Operation)(execution_context &, std::uint32_t *&)>
bool runtime_fc_saturating_conversion_decoded(execution_context *context, std::uint32_t *&sp) {
  auto &current = *context;
  (void)Operation(current, sp);
  return true;
}

template <bool (*Operation)(execution_context &, std::uint32_t *&)>
bool runtime_fc_saturating_conversion(execution_context *context,
                                      const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                      [[maybe_unused]] std::uint32_t *&local_base,
                                      [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  (void)read_u8(rel);
  const auto next_ip = instruction_offset(current, instruction_ip) + encoded_width;
  (void)runtime_fc_saturating_conversion_decoded<Operation>(context, sp);
  instruction_ip = current.code + next_ip;
  return continue_within_block(current, next_ip, rel);
}

bool runtime_memory_manager_api_boundary(execution_context *context,
                                         const std::uint8_t *&instruction_ip,
                                         [[maybe_unused]] std::uint32_t *&sp,
                                         [[maybe_unused]] std::uint32_t *&local_base,
                                         const std::uint8_t *&rel) {
  const auto source_ip = instruction_offset(*context, instruction_ip);
  return runtime_api_boundary(*context, source_ip, rel);
}

template <auto Operation>
bool runtime_i32_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp == current.stack_base));
  sp[-1] = Operation(sp[-1]);
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <bool Indirect>
bool runtime_call(execution_context *context, const std::uint8_t *&instruction_ip,
                  std::uint32_t *&sp, std::uint32_t *&local_stack,
                  const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = instruction_offset(current, instruction_ip);
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  function_index_t function_index = 0;
  [[maybe_unused]] type_index_t expected_type_index = 0;
  std::uint32_t table_index = 0;
  std::uint32_t table_slot = 0;
  const auto &caller = active_frame(current);
  assert(caller.function_view->module_view != nullptr);
  const auto &module = *caller.function_view->module_view;

  if constexpr (Indirect) {
    expected_type_index = static_cast<type_index_t>(read_u16(rel));
    table_index = 0;
    if (table_index >= module.table_count || module.tables == nullptr) {
      return trap(current, kTrapTableIndexOutOfBounds);
    }
    const auto &table = module.tables[table_index];
    assert(!(sp == current.stack_base));
    table_slot = sp[-1];
    if (table_slot >= table.size || table.function_indices == nullptr) {
      return trap(current, kTrapTableIndexOutOfBounds);
    }
    const auto table_function_index = table.function_indices[table_slot];
    if (table_function_index == FIREBALL_INVALID_BLOCK_INDEX) {
      return trap(current, kTrapTableSlotUninitialized);
    }
    assert(table_function_index < module.function_count);
    function_index = static_cast<function_index_t>(table_function_index);
  } else {
    function_index = static_cast<function_index_t>(read_u16(rel));
  }

  const auto &target = module.functions[function_index];
  const auto return_ip = source_ip + encoded_width;
  const auto return_block_index = static_cast<block_index_t>(read_u16(rel));
  // Imports cross the host-call boundary; defined guest functions stay in this
  // native dispatcher and never return to Python opcode handling.
  if (target.is_import != 0) {
    current.current_block_index = return_block_index;
    return runtime_api_boundary(current, source_ip, rel);
  }
  if constexpr (Indirect) {
    // Imported calls are checked once by the host-call boundary. Defined
    // calls validate only the selected target before entering its body.
    assert(expected_type_index < module.type_count && target.type_index < module.type_count);
    assert(module.types != nullptr && module.signature_bytes != nullptr);
    if (expected_type_index != target.type_index) {
      const auto &expected = module.types[expected_type_index];
      const auto &actual = module.types[target.type_index];
      if (expected.param_count != actual.param_count ||
          expected.result_count != actual.result_count ||
          std::memcmp(module.signature_bytes + expected.param_offset,
                      module.signature_bytes + actual.param_offset, expected.param_count) != 0 ||
          std::memcmp(module.signature_bytes + expected.result_offset,
                      module.signature_bytes + actual.result_offset, expected.result_count) != 0) {
        return trap(current, kTrapIndirectCallTypeMismatch);
      }
    }
  }
  if (current.call_stack->size >= FIREBALL_NATIVE_CALL_STACK_CAPACITY) {
    return trap(current, kTrapCallStackCapacity);
  }
  if (current.local_stack_pointer + target.local_slot_count > current.local_stack_limit) {
    return trap(current, kTrapLocalStackCapacity);
  }

  const auto argument_words =
      static_cast<operand_words_t>(target.param_packed_slot_count + (Indirect ? 1u : 0u));
  assert(sp >= current.stack_base + argument_words);
  const auto argument_base = sp - static_cast<std::ptrdiff_t>(argument_words);
  if (target.max_operand_stack_words >
      static_cast<std::uint32_t>(current.stack_limit - argument_base)) {
    return trap(current, kTrapOperandStackCapacity);
  }

  auto *callee_local_base = current.local_stack_pointer;
  const auto local_end = callee_local_base + target.local_slot_count;
  for (frame_word_offset_t index = target.param_packed_slot_count;
       callee_local_base + index < local_end; ++index) {
    callee_local_base[index] = 0;
  }

  std::memcpy(callee_local_base, argument_base,
              target.param_packed_slot_count * sizeof(*callee_local_base));

  auto &callee = current.call_stack->frames[current.call_stack->size];
  callee.function_view = &target;
  callee.local_base = callee_local_base;
  callee.operand_stack_base = argument_base;
  callee.return_ip = return_ip;
  callee.boundary_next_pc = kSentinel;
  callee.boundary_loops_to = kSentinel;
  callee.return_block_index = return_block_index;

  sp = argument_base;
  current.local_stack_pointer = local_end;
  ++current.call_stack->size;
  current.code = target.code;
  current.code_size = target.code_size;
  assert(target.entry_block_index != kInvalidBlockIndex);
  current.current_block_index = target.entry_block_index;
  instruction_ip = current.code;
  local_stack = callee_local_base;
  current.local_base = local_stack;
  return call_boundary(current, 0, target.entry_block_index, rel);
}
template <auto Operation>
bool runtime_i32_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                        std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                        [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp < current.stack_base + 2));
  const auto result = Operation(sp[-2], sp[-1]);
  --sp;
  sp[-1] = result;
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation>
bool runtime_i32_checked_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                                std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                                [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  assert(!(sp < current.stack_base + 2));
  std::uint32_t result = 0;
  std::uint32_t trap_code = 0;
  if (!Operation(sp[-2], sp[-1], result, trap_code)) {
    return trap(current, trap_code);
  }
  --sp;
  sp[-1] = result;
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation, bool ResultIsBool>
bool runtime_i64_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                        std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                        [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp < current.stack_base + 4));
  const auto lhs = combine_u64(sp[-4], sp[-3]);
  const auto rhs = combine_u64(sp[-2], sp[-1]);
  const auto result = Operation(lhs, rhs);
  replace_u64_operands<ResultIsBool>(sp, result, 4);
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation, bool ResultIsBool>
bool runtime_i64_checked_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                                std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                                [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  assert(!(sp < current.stack_base + 4));
  const auto lhs = combine_u64(sp[-4], sp[-3]);
  const auto rhs = combine_u64(sp[-2], sp[-1]);
  std::uint64_t result = 0;
  std::uint32_t trap_code = 0;
  if (!Operation(lhs, rhs, result, trap_code)) {
    return trap(current, trap_code);
  }
  replace_u64_operands<ResultIsBool>(sp, result, 4);
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation, bool ResultIsBool>
bool runtime_i64_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp < current.stack_base + 2));
  const auto value = combine_u64(sp[-2], sp[-1]);
  const auto result = Operation(value);
  replace_u64_operands<ResultIsBool>(sp, result, 2);
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation, bool ResultIsBool>
bool runtime_f32_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                        std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                        [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp < current.stack_base + 2));
  const auto result = Operation(f32_from_bits(sp[-2]), f32_from_bits(sp[-1]));
  --sp;
  if constexpr (ResultIsBool) {
    sp[-1] = static_cast<std::uint32_t>(result);
  } else {
    sp[-1] = f32_to_bits(result);
  }
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation, bool ResultIsBool>
bool runtime_f64_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                        std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                        [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp < current.stack_base + 4));
  const auto lhs = f64_from_bits(combine_u64(sp[-4], sp[-3]));
  const auto rhs = f64_from_bits(combine_u64(sp[-2], sp[-1]));
  const auto result = Operation(lhs, rhs);
  if constexpr (ResultIsBool) {
    replace_u64_operands<true>(sp, static_cast<std::uint32_t>(result), 4);
  } else {
    replace_u64_operands<false>(sp, f64_to_bits(result), 4);
  }
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation>
bool runtime_f32_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp == current.stack_base));
  sp[-1] = f32_to_bits(Operation(f32_from_bits(sp[-1])));
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <auto Operation>
bool runtime_f64_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                       std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  [[maybe_unused]] auto &current = *context;
  assert(!(sp < current.stack_base + 2));
  const auto value = f64_from_bits(combine_u64(sp[-2], sp[-1]));
  replace_u64_operands<false>(sp, f64_to_bits(Operation(value)), 2);
  advance_instruction_ip(instruction_ip, rel);
  return true;
}
template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
bool runtime_memory_load_decoded(execution_context *context, std::uint32_t *&sp,
                                 [[maybe_unused]] std::uint32_t *&local_base,
                                 const std::uint8_t *&instruction_ip, code_ip_t source_ip,
                                 code_ip_t next_pc, std::uint32_t offset,
                                 const std::uint8_t *rel) {
  auto &current = *context;
  assert(!(sp == current.stack_base));

  const auto address = sp[-1];
  if ((address & 0x8000'0000u) != 0) {
    return runtime_api_boundary(current, source_ip, rel);
  }
  const auto effective_address = static_cast<std::uint64_t>(address) + offset;
  if (!linear_memory_range_is_valid(current, effective_address, Width)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (current.linear_memory_host_base == nullptr) {
    return runtime_api_boundary(current, source_ip, rel);
  }

  assert(!(current.stack_limit - (sp - 1) < ResultWords));
  std::uint64_t value = 0;
  for (std::uint32_t byte = 0; byte < Width; ++byte) {
    value |= static_cast<std::uint64_t>(current.linear_memory_host_base[effective_address + byte])
             << (byte * 8);
  }
  if constexpr (Signed) {
    value = sign_extend_memory_value<Width>(value);
  }

  if constexpr (ResultWords == 2) {
    sp[-1] = static_cast<std::uint32_t>(value);
    *sp++ = static_cast<std::uint32_t>(value >> 32);
  } else {
    sp[-1] = static_cast<std::uint32_t>(value);
  }
  instruction_ip = current.code + next_pc;
  return true;
}
template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
bool runtime_memory_load(execution_context *context, const std::uint8_t *&instruction_ip,
                         std::uint32_t *&sp, std::uint32_t *&local_base,
                         const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto offset = read_u32(rel);
  const auto source_ip = instruction_offset(current, instruction_ip);
  const auto next_ip = source_ip + encoded_width;
  return runtime_memory_load_decoded<Width, ResultWords, Signed>(
      context, sp, local_base, instruction_ip, source_ip, next_ip, offset, rel);
}
template <std::uint32_t Width, std::uint32_t ValueWords>
bool runtime_memory_store_decoded(execution_context *context, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t *&local_base,
                                  const std::uint8_t *&instruction_ip, code_ip_t source_ip,
                                  code_ip_t next_pc, std::uint32_t offset,
                                  const std::uint8_t *rel) {
  auto &current = *context;
  assert(!(sp < current.stack_base + ValueWords + 1));

  auto *address_stack_pointer = sp - ValueWords - 1;
  const auto address = sp[-static_cast<std::ptrdiff_t>(ValueWords + 1)];
  if ((address & 0x8000'0000u) != 0) {
    return runtime_api_boundary(current, source_ip, rel);
  }
  const auto effective_address = static_cast<std::uint64_t>(address) + offset;
  if (!linear_memory_range_is_valid(current, effective_address, Width)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (current.linear_memory_host_base == nullptr) {
    return runtime_api_boundary(current, source_ip, rel);
  }

  std::uint64_t value = sp[-static_cast<std::ptrdiff_t>(ValueWords)];
  if constexpr (ValueWords == 2) {
    value |= static_cast<std::uint64_t>(sp[-1]) << 32;
  }
  for (std::uint32_t byte = 0; byte < Width; ++byte) {
    current.linear_memory_host_base[effective_address + byte] =
        static_cast<std::uint8_t>(value >> (byte * 8));
  }
  sp = address_stack_pointer;
  instruction_ip = current.code + next_pc;
  return true;
}
template <std::uint32_t Width, std::uint32_t ValueWords>
bool runtime_memory_store(execution_context *context, const std::uint8_t *&instruction_ip,
                          std::uint32_t *&sp, std::uint32_t *&local_base,
                          const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  const auto offset = read_u32(rel);
  const auto source_ip = instruction_offset(current, instruction_ip);
  const auto next_ip = source_ip + encoded_width;
  return runtime_memory_store_decoded<Width, ValueWords>(context, sp, local_base, instruction_ip,
                                                         source_ip, next_ip, offset, rel);
}
bool runtime_if(execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                [[maybe_unused]] std::uint32_t *&local_base,
                [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  std::uint32_t condition = 0;
  (void)pop(current, sp, condition);
  const auto then_block_index = static_cast<block_index_t>(read_u16(rel));
  const auto else_block_index = static_cast<block_index_t>(read_u16(rel));
  const auto target_block_index = condition == 0 ? else_block_index : then_block_index;
  if (target_block_index == kInvalidBlockIndex) {
    return finish_function_boundary(current, local_base, instruction_ip, rel);
  }
  const auto next_ip = code_ip_for_block_index(current, target_block_index);
  instruction_ip = current.code + next_ip;
  return block_boundary(current, next_ip, target_block_index, rel);
}

bool runtime_br(execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                [[maybe_unused]] std::uint32_t *&local_base,
                [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = instruction_offset(current, instruction_ip);
  const auto target_block_index = static_cast<block_index_t>(read_u16(rel));
  const auto target_stack_base_words = read_u8(rel);
  const auto result_words = read_u8(rel);
  branch(current, sp, target_block_index, target_stack_base_words, result_words);
  if (target_block_index == kInvalidBlockIndex) {
    return finish_function_boundary(current, local_base, instruction_ip, rel);
  }
  const auto next_ip = code_ip_for_block_index(current, target_block_index);
  record_loop_backedge(current, source_ip, next_ip);
  instruction_ip = current.code + next_ip;
  return block_boundary(current, next_ip, target_block_index, rel);
}

bool runtime_fc_memory_copy_decoded(execution_context *context, std::uint32_t *&sp,
                                    const std::uint8_t *&instruction_ip, code_ip_t source_ip,
                                    code_ip_t next_pc, const std::uint8_t *rel) {
  auto &current = *context;
  assert(!(sp < current.stack_base + 3));

  auto *destination_stack_pointer = sp - 3;
  const auto destination = sp[-3];
  const auto source = sp[-2];
  const auto length = sp[-1];
  if (((destination | source) & 0x8000'0000u) != 0) {
    return runtime_api_boundary(current, source_ip, rel);
  }
  if (!linear_memory_range_is_valid(current, destination, length) ||
      !linear_memory_range_is_valid(current, source, length)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (length != 0) {
    if (current.linear_memory_host_base == nullptr) {
      return runtime_api_boundary(current, source_ip, rel);
    }
    auto *memory = current.linear_memory_host_base;
    std::memmove(memory + destination, memory + source, length);
  }
  sp = destination_stack_pointer;
  instruction_ip = current.code + next_pc;
  return true;
}

bool runtime_fc_memory_copy(execution_context *context, const std::uint8_t *&instruction_ip,
                            std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                            const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  [[maybe_unused]] const auto subopcode = read_u8(rel);
  assert(subopcode == 0x0A);
  const auto source_ip = instruction_offset(current, instruction_ip);
  const auto next_ip = source_ip + encoded_width;
  if (!runtime_fc_memory_copy_decoded(context, sp, instruction_ip, source_ip, next_ip, rel)) {
    return false;
  }
  return continue_within_block(current, next_ip, rel);
}

bool runtime_fc_memory_fill_decoded(execution_context *context, std::uint32_t *&sp,
                                    const std::uint8_t *&instruction_ip, code_ip_t source_ip,
                                    code_ip_t next_pc, const std::uint8_t *rel) {
  auto &current = *context;
  assert(!(sp < current.stack_base + 3));

  auto *destination_stack_pointer = sp - 3;
  const auto destination = sp[-3];
  const auto value = static_cast<std::uint8_t>(sp[-2]);
  const auto length = sp[-1];
  if (!linear_memory_range_is_valid(current, destination, length)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (length != 0) {
    if (current.linear_memory_host_base == nullptr) {
      return runtime_api_boundary(current, source_ip, rel);
    }
    std::memset(current.linear_memory_host_base + destination, value, length);
  }
  sp = destination_stack_pointer;
  instruction_ip = current.code + next_pc;
  return true;
}

bool runtime_fc_memory_fill(execution_context *context, const std::uint8_t *&instruction_ip,
                            std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                            const std::uint8_t *&rel) {
  auto &current = *context;
  const auto encoded_width = consume_instruction_width(instruction_ip, rel);
  [[maybe_unused]] const auto subopcode = read_u8(rel);
  assert(subopcode == 0x0B);
  const auto source_ip = instruction_offset(current, instruction_ip);
  const auto next_ip = source_ip + encoded_width;
  if (!runtime_fc_memory_fill_decoded(context, sp, instruction_ip, source_ip, next_ip, rel)) {
    return false;
  }
  return continue_within_block(current, next_ip, rel);
}

template bool runtime_call<false>(execution_context *context, const std::uint8_t *&instruction_ip,
                                  std::uint32_t *&sp, std::uint32_t *&local_stack,
                                  [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_call<true>(execution_context *context, const std::uint8_t *&instruction_ip,
                                 std::uint32_t *&sp, std::uint32_t *&local_stack,
                                 [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f32_convert_i32_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f32_convert_i32_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f32_convert_i64_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f32_convert_i64_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f32_demote_f64>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f64_convert_i32_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f64_convert_i32_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f64_convert_i64_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f64_convert_i64_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_f64_promote_f32>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i32_trunc_f32_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i32_trunc_f32_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i32_trunc_f64_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i32_trunc_f64_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i32_wrap_i64>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i64_extend_i32_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i64_extend_i32_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i64_trunc_f32_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i64_trunc_f32_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i64_trunc_f64_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_i64_trunc_f64_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_conversion<conversion_identity>(execution_context *context,
                                                      const std::uint8_t *&instruction_ip,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<&opcode_float_copysign<float>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<&opcode_float_div<float>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<&opcode_float_max<float>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<&opcode_float_min<float>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::equal_to<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::greater<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::greater_equal<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::less<>{}, true>(execution_context *context,
                                                      const std::uint8_t *&instruction_ip,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::less_equal<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::minus<>{}, false>(execution_context *context,
                                                        const std::uint8_t *&instruction_ip,
                                                        std::uint32_t *&sp,
                                                        [[maybe_unused]] std::uint32_t *&local_base,
                                                        [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::multiplies<>{}, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::not_equal_to<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_binary<std::plus<>{}, false>(execution_context *context,
                                                       const std::uint8_t *&instruction_ip,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_unary<&opcode_float_abs<float>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_unary<&opcode_float_ceil<float>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_unary<&opcode_float_floor<float>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_unary<&opcode_float_nearest<float>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_unary<&opcode_float_neg<float>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_unary<&opcode_float_sqrt<float>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f32_unary<&opcode_float_trunc<float>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<&opcode_float_copysign<double>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<&opcode_float_div<double>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<&opcode_float_max<double>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<&opcode_float_min<double>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::equal_to<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::greater<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::greater_equal<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::less<>{}, true>(execution_context *context,
                                                      const std::uint8_t *&instruction_ip,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::less_equal<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::minus<>{}, false>(execution_context *context,
                                                        const std::uint8_t *&instruction_ip,
                                                        std::uint32_t *&sp,
                                                        [[maybe_unused]] std::uint32_t *&local_base,
                                                        [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::multiplies<>{}, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::not_equal_to<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_binary<std::plus<>{}, false>(execution_context *context,
                                                       const std::uint8_t *&instruction_ip,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_unary<&opcode_float_abs<double>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_unary<&opcode_float_ceil<double>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_unary<&opcode_float_floor<double>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_unary<&opcode_float_nearest<double>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_unary<&opcode_float_neg<double>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_unary<&opcode_float_sqrt<double>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_f64_unary<&opcode_float_trunc<double>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_s>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_u>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_rotate_left<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_rotate_right<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_shift_left<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_shift_right_signed<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_shift_right_unsigned<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_signed_ge<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_signed_gt<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_signed_le<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<&opcode_signed_lt<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::bit_and<>{}>(execution_context *context,
                                                   const std::uint8_t *&instruction_ip,
                                                   std::uint32_t *&sp,
                                                   [[maybe_unused]] std::uint32_t *&local_base,
                                                   [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::bit_or<>{}>(execution_context *context,
                                                  const std::uint8_t *&instruction_ip,
                                                  std::uint32_t *&sp,
                                                  [[maybe_unused]] std::uint32_t *&local_base,
                                                  [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::bit_xor<>{}>(execution_context *context,
                                                   const std::uint8_t *&instruction_ip,
                                                   std::uint32_t *&sp,
                                                   [[maybe_unused]] std::uint32_t *&local_base,
                                                   [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::equal_to<>{}>(execution_context *context,
                                                    const std::uint8_t *&instruction_ip,
                                                    std::uint32_t *&sp,
                                                    [[maybe_unused]] std::uint32_t *&local_base,
                                                    [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::greater<>{}>(execution_context *context,
                                                   const std::uint8_t *&instruction_ip,
                                                   std::uint32_t *&sp,
                                                   [[maybe_unused]] std::uint32_t *&local_base,
                                                   [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::greater_equal<>{}>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::less<>{}>(execution_context *context,
                                                const std::uint8_t *&instruction_ip,
                                                std::uint32_t *&sp,
                                                [[maybe_unused]] std::uint32_t *&local_base,
                                                [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::less_equal<>{}>(execution_context *context,
                                                      const std::uint8_t *&instruction_ip,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::minus<>{}>(execution_context *context,
                                                 const std::uint8_t *&instruction_ip,
                                                 std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::multiplies<>{}>(execution_context *context,
                                                      const std::uint8_t *&instruction_ip,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::not_equal_to<>{}>(execution_context *context,
                                                        const std::uint8_t *&instruction_ip,
                                                        std::uint32_t *&sp,
                                                        [[maybe_unused]] std::uint32_t *&local_base,
                                                        [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_binary<std::plus<>{}>(execution_context *context,
                                                const std::uint8_t *&instruction_ip,
                                                std::uint32_t *&sp,
                                                [[maybe_unused]] std::uint32_t *&local_base,
                                                [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_checked_binary<&opcode_div_signed<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_checked_binary<&opcode_div_unsigned<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_checked_binary<&opcode_rem_signed<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_checked_binary<&opcode_rem_unsigned<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_unary<&opcode_clz<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_unary<&opcode_ctz<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_unary<&opcode_eqz<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_unary<&opcode_extend16_signed<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_unary<&opcode_extend8_signed<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i32_unary<&opcode_popcount<std::uint32_t>>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_rotate_left<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_rotate_right<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_shift_left<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_shift_right_signed<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_shift_right_unsigned<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_signed_ge<std::uint64_t>, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_signed_gt<std::uint64_t>, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_signed_le<std::uint64_t>, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<&opcode_signed_lt<std::uint64_t>, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::bit_and<>{}, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::bit_or<>{}, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::bit_xor<>{}, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::equal_to<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::greater<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::greater_equal<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::less<>{}, true>(execution_context *context,
                                                      const std::uint8_t *&instruction_ip,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::less_equal<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::minus<>{}, false>(execution_context *context,
                                                        const std::uint8_t *&instruction_ip,
                                                        std::uint32_t *&sp,
                                                        [[maybe_unused]] std::uint32_t *&local_base,
                                                        [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::multiplies<>{}, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::not_equal_to<>{}, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_binary<std::plus<>{}, false>(execution_context *context,
                                                       const std::uint8_t *&instruction_ip,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_checked_binary<&opcode_div_signed<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_checked_binary<&opcode_div_unsigned<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_checked_binary<&opcode_rem_signed<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_checked_binary<&opcode_rem_unsigned<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_unary<&opcode_clz<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_unary<&opcode_ctz<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_unary<&opcode_eqz<std::uint64_t>, true>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_unary<&opcode_extend16_signed<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_unary<&opcode_extend32_signed, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_unary<&opcode_extend8_signed<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_i64_unary<&opcode_popcount<std::uint64_t>, false>(
    execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<1, 1, false>(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp, std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<1, 1, true>(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<1, 2, false>(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp, std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<1, 2, true>(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<2, 1, false>(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp, std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<2, 1, true>(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<2, 2, false>(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp, std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<2, 2, true>(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<4, 1, false>(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp, std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<4, 2, false>(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp, std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<4, 2, true>(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load<8, 2, false>(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp, std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_store<1, 1>(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_store<1, 2>(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_store<2, 1>(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_store<2, 2>(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_store<4, 1>(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_store<4, 2>(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_store<8, 2>(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
template bool runtime_memory_load_decoded<4, 1, false>(execution_context *context,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       const std::uint8_t *&instruction_ip,
                                                       code_ip_t source_ip, code_ip_t next_pc,
                                                       std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<8, 2, false>(execution_context *context,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       const std::uint8_t *&instruction_ip,
                                                       code_ip_t source_ip, code_ip_t next_pc,
                                                       std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<1, 1, true>(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      const std::uint8_t *&instruction_ip,
                                                      code_ip_t source_ip, code_ip_t next_pc,
                                                      std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<1, 1, false>(execution_context *context,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       const std::uint8_t *&instruction_ip,
                                                       code_ip_t source_ip, code_ip_t next_pc,
                                                       std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<2, 1, true>(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      const std::uint8_t *&instruction_ip,
                                                      code_ip_t source_ip, code_ip_t next_pc,
                                                      std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<2, 1, false>(execution_context *context,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       const std::uint8_t *&instruction_ip,
                                                       code_ip_t source_ip, code_ip_t next_pc,
                                                       std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<1, 2, true>(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      const std::uint8_t *&instruction_ip,
                                                      code_ip_t source_ip, code_ip_t next_pc,
                                                      std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<1, 2, false>(execution_context *context,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       const std::uint8_t *&instruction_ip,
                                                       code_ip_t source_ip, code_ip_t next_pc,
                                                       std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<2, 2, true>(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      const std::uint8_t *&instruction_ip,
                                                      code_ip_t source_ip, code_ip_t next_pc,
                                                      std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<2, 2, false>(execution_context *context,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       const std::uint8_t *&instruction_ip,
                                                       code_ip_t source_ip, code_ip_t next_pc,
                                                       std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<4, 2, true>(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      [[maybe_unused]] std::uint32_t *&local_base,
                                                      const std::uint8_t *&instruction_ip,
                                                      code_ip_t source_ip, code_ip_t next_pc,
                                                      std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_load_decoded<4, 2, false>(execution_context *context,
                                                       std::uint32_t *&sp,
                                                       [[maybe_unused]] std::uint32_t *&local_base,
                                                       const std::uint8_t *&instruction_ip,
                                                       code_ip_t source_ip, code_ip_t next_pc,
                                                       std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_store_decoded<4, 1>(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 const std::uint8_t *&instruction_ip,
                                                 code_ip_t source_ip, code_ip_t next_pc,
                                                 std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_store_decoded<8, 2>(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 const std::uint8_t *&instruction_ip,
                                                 code_ip_t source_ip, code_ip_t next_pc,
                                                 std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_store_decoded<1, 1>(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 const std::uint8_t *&instruction_ip,
                                                 code_ip_t source_ip, code_ip_t next_pc,
                                                 std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_store_decoded<2, 1>(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 const std::uint8_t *&instruction_ip,
                                                 code_ip_t source_ip, code_ip_t next_pc,
                                                 std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_store_decoded<1, 2>(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 const std::uint8_t *&instruction_ip,
                                                 code_ip_t source_ip, code_ip_t next_pc,
                                                 std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_store_decoded<2, 2>(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 const std::uint8_t *&instruction_ip,
                                                 code_ip_t source_ip, code_ip_t next_pc,
                                                 std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool runtime_memory_store_decoded<4, 2>(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 const std::uint8_t *&instruction_ip,
                                                 code_ip_t source_ip, code_ip_t next_pc,
                                                 std::uint32_t offset,
                                                       const std::uint8_t *rel);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i32_trunc_sat_f32_s>(execution_context *context,
                                                                         std::uint32_t *&sp);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i32_trunc_sat_f32_u>(execution_context *context,
                                                                         std::uint32_t *&sp);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i32_trunc_sat_f64_s>(execution_context *context,
                                                                         std::uint32_t *&sp);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i32_trunc_sat_f64_u>(execution_context *context,
                                                                         std::uint32_t *&sp);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i64_trunc_sat_f32_s>(execution_context *context,
                                                                         std::uint32_t *&sp);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i64_trunc_sat_f32_u>(execution_context *context,
                                                                         std::uint32_t *&sp);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i64_trunc_sat_f64_s>(execution_context *context,
                                                                         std::uint32_t *&sp);
template bool
runtime_fc_saturating_conversion_decoded<conversion_i64_trunc_sat_f64_u>(execution_context *context,
                                                                         std::uint32_t *&sp);
} // namespace fireball::wasm_runtime

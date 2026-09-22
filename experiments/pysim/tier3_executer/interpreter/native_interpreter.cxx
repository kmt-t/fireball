#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "../../tier2_runtime/wasm_interop.hxx"

#include <cstddef>
#include <cstdint>
#include <bit>
#include <array>
#include <cmath>
#include <initializer_list>
#include <limits>

namespace {

#if defined(_WIN32)
#define FIREBALL_CPS_CALL __fastcall
#elif defined(__arm__) && defined(__clang__)
#define FIREBALL_CPS_CALL __attribute__((pcs("aapcs")))
#else
#define FIREBALL_CPS_CALL
#endif

constexpr std::uint32_t kFallback = 0;
constexpr std::uint32_t kComplete = 1;
constexpr std::uint32_t kTrap = 2;
constexpr std::uint32_t kTrapUnreachable = 9;
constexpr std::uint32_t kTrapDivideByZero = 15;
constexpr std::uint32_t kTrapIntegerOverflow = 16;
constexpr std::uint32_t kTrapInvalidConversion = 17;
constexpr std::uint32_t kSentinel = 0xFFFF'FFFFu;

using execution_context = fireball_execution_context_native;

struct step_result {
  std::uint32_t kind;
  std::uint32_t next_ip;
  std::uint32_t trap_code;
};

using handler_fn = FIREBALL_CPS_CALL step_result (*)(
    execution_context*, std::uint32_t*, std::uint32_t*, std::uint32_t);
using binary_operation_fn = bool (*)(std::uint32_t, std::uint32_t, std::uint32_t&,
                                     std::uint32_t&);

const std::array<handler_fn, 256>& handler_table_data();
const std::array<binary_operation_fn, 256>& binary_operation_table_data();

constexpr step_result fallback(std::uint32_t ip) {
  return {kFallback, ip, 0};
}

constexpr step_result complete() { return {kComplete, kSentinel, 0}; }

constexpr step_result trap(std::uint32_t code) { return {kTrap, kSentinel, code}; }

FIREBALL_CPS_CALL step_result dispatch(execution_context* context, std::uint32_t* sp,
                                       std::uint32_t* local_base, std::uint32_t tos);

std::uint32_t top_value(const execution_context& current, const std::uint32_t* sp) {
  return current.sp_offset == 0 ? 0 : sp[current.sp_offset - 1];
}

bool read_s32(const execution_context& current, std::uint32_t& ip, std::int32_t& value) {
  std::uint32_t raw = 0;
  std::uint32_t shift = 0;
  std::uint8_t byte = 0;
  while (ip < current.code_size && shift < 35) {
    byte = current.code[ip++];
    raw |= static_cast<std::uint32_t>(byte & 0x7Fu) << shift;
    shift += 7;
    if ((byte & 0x80u) == 0) {
      if ((byte & 0x40u) != 0 && shift < 32) {
        raw |= ~0u << shift;
      }
      value = static_cast<std::int32_t>(raw);
      return true;
    }
  }
  return false;
}

bool read_u32(const execution_context& current, std::uint32_t& ip, std::uint32_t& value) {
  std::uint32_t result = 0;
  std::uint32_t shift = 0;
  while (ip < current.code_size && shift < 35) {
    const auto byte = current.code[ip++];
    result |= static_cast<std::uint32_t>(byte & 0x7Fu) << shift;
    if ((byte & 0x80u) == 0) {
      value = result;
      return true;
    }
    shift += 7;
  }
  return false;
}

bool pop(execution_context& current, std::uint32_t* sp, std::uint32_t& value);
bool push(execution_context& current, std::uint32_t* sp, std::uint32_t value);

bool read_s64(const execution_context& current, std::uint32_t& ip, std::int64_t& value) {
  std::uint64_t raw = 0;
  std::uint32_t shift = 0;
  while (ip < current.code_size && shift < 70) {
    const auto byte = current.code[ip++];
    raw |= static_cast<std::uint64_t>(byte & 0x7Fu) << shift;
    shift += 7;
    if ((byte & 0x80u) == 0) {
      if ((byte & 0x40u) != 0 && shift < 64) raw |= ~0ull << shift;
      value = static_cast<std::int64_t>(raw);
      return true;
    }
  }
  return false;
}

bool pop_u64(execution_context& current, std::uint32_t* sp, std::uint64_t& value) {
  if (current.sp_offset < 2) return false;
  const auto low = sp[current.sp_offset - 2];
  const auto high = sp[current.sp_offset - 1];
  current.sp_offset -= 2;
  value = static_cast<std::uint64_t>(low) | (static_cast<std::uint64_t>(high) << 32);
  return true;
}

bool push_u64(execution_context& current, std::uint32_t* sp, std::uint64_t value) {
  if (current.sp_offset > 126) return false;
  sp[current.sp_offset++] = static_cast<std::uint32_t>(value);
  sp[current.sp_offset++] = static_cast<std::uint32_t>(value >> 32);
  return true;
}

float f32_from_bits(std::uint32_t value) { return std::bit_cast<float>(value); }
std::uint32_t f32_to_bits(float value) { return std::bit_cast<std::uint32_t>(value); }
double f64_from_bits(std::uint64_t value) { return std::bit_cast<double>(value); }
std::uint64_t f64_to_bits(double value) { return std::bit_cast<std::uint64_t>(value); }

bool pop_f32(execution_context& current, std::uint32_t* sp, float& value) {
  std::uint32_t raw = 0;
  if (!pop(current, sp, raw)) return false;
  value = f32_from_bits(raw);
  return true;
}

bool push_f32(execution_context& current, std::uint32_t* sp, float value) {
  return push(current, sp, f32_to_bits(value));
}

bool pop_f64(execution_context& current, std::uint32_t* sp, double& value) {
  std::uint64_t raw = 0;
  if (!pop_u64(current, sp, raw)) return false;
  value = f64_from_bits(raw);
  return true;
}

bool push_f64(execution_context& current, std::uint32_t* sp, double value) {
  return push_u64(current, sp, f64_to_bits(value));
}

const fireball_call_frame_native* active_frame(const execution_context& current) {
  if (current.call_stack == nullptr || current.call_stack->size == 0 ||
      current.call_base >= current.call_stack->size) {
    return nullptr;
  }
  return &current.call_stack->frames[current.call_stack->size - 1];
}

const fireball_control_map_entry_native* control_entry(
    const execution_context& current, std::uint32_t ip) {
  const auto* frame = active_frame(current);
  if (frame == nullptr || frame->control_map == nullptr || ip >= current.code_size) {
    return nullptr;
  }
  const auto* entries = static_cast<const fireball_control_map_entry_native*>(
      frame->control_map);
  return &entries[ip];
}

bool local_span(const execution_context& current, std::uint32_t index,
                std::uint32_t& offset, std::uint32_t& width) {
  const auto* frame = active_frame(current);
  if (frame == nullptr || index >= frame->local_count || frame->local_width_map == nullptr) {
    return false;
  }
  const auto* packed = frame->local_width_map;
  const auto width_code = (packed[index >> 2] >> ((index & 3u) * 2u)) & 3u;
  width = 1u << width_code;
  if (width > frame->slot_words || frame->local_base + index * frame->slot_words + width >
                                     frame->local_base + frame->local_slot_count) {
    return false;
  }
  offset = frame->local_base + index * frame->slot_words;
  return true;
}

bool branch(execution_context& current, std::uint32_t* sp, std::uint32_t depth,
            std::uint32_t& next_ip) {
  const auto frame_count = current.control_stack->size - current.control_base;
  if (depth > frame_count) return false;
  const auto* call_frame = active_frame(current);
  const auto outer_result_arity = call_frame == nullptr ? 0u : call_frame->result_arity;
  const auto target_index = frame_count - depth - 1;
  std::uint32_t result_arity = outer_result_arity;
  std::uint32_t saved_height = 0;
  std::uint32_t target_kind = 0;
  if (depth < frame_count) {
    const auto& target = current.control_stack->frames[current.control_base + target_index];
    saved_height = target.stack_height;
    target_kind = target.kind;
    result_arity = target_kind == 1u ? 0u : target.result_arity;
  }
  if (result_arity > 2 || saved_height > current.sp_offset ||
      result_arity > current.sp_offset - saved_height) {
    return false;
  }
  const auto result_start = current.sp_offset - result_arity;
  const auto result0 = result_arity >= 1 ? sp[result_start] : 0;
  const auto result1 = result_arity >= 2 ? sp[result_start + 1] : 0;
  current.sp_offset = saved_height;
  if (result_arity >= 1) sp[current.sp_offset++] = result0;
  if (result_arity >= 2) sp[current.sp_offset++] = result1;
  if (depth == frame_count) {
    current.control_stack->size = current.control_base;
    next_ip = kSentinel;
    return true;
  }
  if (target_kind == 1u) {
    current.control_stack->size = current.control_base + target_index + 1;
    next_ip = current.control_stack->frames[current.control_base + target_index].start + 2;
  } else {
    current.control_stack->size = current.control_base + target_index;
    next_ip = current.control_stack->frames[current.control_base + target_index].match_end + 1;
  }
  return true;
}

bool pop(execution_context& current, std::uint32_t* sp, std::uint32_t& value) {
  if (current.sp_offset == 0) {
    return false;
  }
  value = sp[--current.sp_offset];
  return true;
}

bool push(execution_context& current, std::uint32_t* sp, std::uint32_t value) {
  if (current.sp_offset >= 128) {
    return false;
  }
  sp[current.sp_offset++] = value;
  return true;
}

FIREBALL_CPS_CALL step_result unary_i32_eqz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value) || !push(current, sp, value == 0 ? 1u : 0u)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result unary_i32_clz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return fallback(current.ip);
  }
  const std::uint32_t result = value == 0 ? 32u : static_cast<std::uint32_t>(__builtin_clz(value));
  if (!push(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result unary_i32_ctz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) {
    return fallback(current.ip);
  }
  const std::uint32_t result = value == 0 ? 32u : static_cast<std::uint32_t>(__builtin_ctz(value));
  if (!push(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result unary_i32_popcnt(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value) || !push(current, sp, __builtin_popcount(value))) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result unary_i32_extend8(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value) ||
      !push(current, sp, static_cast<std::uint32_t>(static_cast<std::int32_t>(
          static_cast<std::int8_t>(value))))) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result unary_i32_extend16(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  std::uint32_t value = 0;
  if (!pop(current, sp, value) ||
      !push(current, sp, static_cast<std::uint32_t>(static_cast<std::int32_t>(
          static_cast<std::int16_t>(value))))) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

template <std::uint32_t (*Operation)(std::uint32_t, std::uint32_t)>
bool evaluate_plain(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t& result,
                    std::uint32_t& trap_code) {
  result = Operation(lhs, rhs);
  trap_code = 0;
  return true;
}

constexpr std::uint32_t op_eq(std::uint32_t lhs, std::uint32_t rhs) { return lhs == rhs; }
constexpr std::uint32_t op_ne(std::uint32_t lhs, std::uint32_t rhs) { return lhs != rhs; }
constexpr std::uint32_t op_lt_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) < static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_lt_u(std::uint32_t lhs, std::uint32_t rhs) { return lhs < rhs; }
constexpr std::uint32_t op_gt_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) > static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_gt_u(std::uint32_t lhs, std::uint32_t rhs) { return lhs > rhs; }
constexpr std::uint32_t op_le_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) <= static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_le_u(std::uint32_t lhs, std::uint32_t rhs) { return lhs <= rhs; }
constexpr std::uint32_t op_ge_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) >= static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_ge_u(std::uint32_t lhs, std::uint32_t rhs) { return lhs >= rhs; }
constexpr std::uint32_t op_add(std::uint32_t lhs, std::uint32_t rhs) { return lhs + rhs; }
constexpr std::uint32_t op_sub(std::uint32_t lhs, std::uint32_t rhs) { return lhs - rhs; }
constexpr std::uint32_t op_mul(std::uint32_t lhs, std::uint32_t rhs) { return lhs * rhs; }
constexpr std::uint32_t op_and(std::uint32_t lhs, std::uint32_t rhs) { return lhs & rhs; }
constexpr std::uint32_t op_or(std::uint32_t lhs, std::uint32_t rhs) { return lhs | rhs; }
constexpr std::uint32_t op_xor(std::uint32_t lhs, std::uint32_t rhs) { return lhs ^ rhs; }
constexpr std::uint32_t op_shl(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs << (rhs & 31u);
}
constexpr std::uint32_t op_shr_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::uint32_t>(static_cast<std::int32_t>(lhs) >> (rhs & 31u));
}
constexpr std::uint32_t op_shr_u(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs >> (rhs & 31u);
}
constexpr std::uint32_t op_rotl(std::uint32_t lhs, std::uint32_t rhs) {
  return (lhs << (rhs & 31u)) | (lhs >> ((32u - rhs) & 31u));
}
constexpr std::uint32_t op_rotr(std::uint32_t lhs, std::uint32_t rhs) {
  return (lhs >> (rhs & 31u)) | (lhs << ((32u - rhs) & 31u));
}

bool evaluate_div_s(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t& result,
                    std::uint32_t& trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  if (lhs == 0x8000'0000u && rhs == 0xFFFF'FFFFu) {
    trap_code = kTrapIntegerOverflow;
    return false;
  }
  result = static_cast<std::uint32_t>(static_cast<std::int32_t>(lhs) /
                                      static_cast<std::int32_t>(rhs));
  trap_code = 0;
  return true;
}

bool evaluate_rem_s(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t& result,
                    std::uint32_t& trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs == 0x8000'0000u && rhs == 0xFFFF'FFFFu
               ? 0
               : static_cast<std::uint32_t>(static_cast<std::int32_t>(lhs) %
                                             static_cast<std::int32_t>(rhs));
  trap_code = 0;
  return true;
}

bool evaluate_div_u(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t& result,
                    std::uint32_t& trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs / rhs;
  trap_code = 0;
  return true;
}

bool evaluate_rem_u(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t& result,
                    std::uint32_t& trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs % rhs;
  trap_code = 0;
  return true;
}

using i64_binary_operation_fn = bool (*)(std::uint64_t, std::uint64_t, std::uint64_t&,
                                         std::uint32_t&);
using i64_unary_operation_fn = std::uint64_t (*)(std::uint64_t);
using f32_binary_operation_fn = float (*)(float, float);
using f64_binary_operation_fn = double (*)(double, double);
using f32_unary_operation_fn = float (*)(float);
using f64_unary_operation_fn = double (*)(double);

const std::array<i64_binary_operation_fn, 256>& i64_binary_table_data();
const std::array<i64_unary_operation_fn, 256>& i64_unary_table_data();
const std::array<f32_binary_operation_fn, 256>& f32_binary_table_data();
const std::array<f64_binary_operation_fn, 256>& f64_binary_table_data();
const std::array<f32_unary_operation_fn, 256>& f32_unary_table_data();
const std::array<f64_unary_operation_fn, 256>& f64_unary_table_data();
const std::array<bool, 256>& numeric_result_is_bool_data();

template <std::uint64_t (*Operation)(std::uint64_t, std::uint64_t)>
bool evaluate_i64_plain(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t& result,
                        std::uint32_t& trap_code) {
  result = Operation(lhs, rhs);
  trap_code = 0;
  return true;
}

constexpr std::uint64_t i64_eq(std::uint64_t a, std::uint64_t b) { return a == b; }
constexpr std::uint64_t i64_ne(std::uint64_t a, std::uint64_t b) { return a != b; }
constexpr std::uint64_t i64_lt_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) < static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_lt_u(std::uint64_t a, std::uint64_t b) { return a < b; }
constexpr std::uint64_t i64_gt_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) > static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_gt_u(std::uint64_t a, std::uint64_t b) { return a > b; }
constexpr std::uint64_t i64_le_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) <= static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_le_u(std::uint64_t a, std::uint64_t b) { return a <= b; }
constexpr std::uint64_t i64_ge_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) >= static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_ge_u(std::uint64_t a, std::uint64_t b) { return a >= b; }
constexpr std::uint64_t i64_add(std::uint64_t a, std::uint64_t b) { return a + b; }
constexpr std::uint64_t i64_sub(std::uint64_t a, std::uint64_t b) { return a - b; }
constexpr std::uint64_t i64_mul(std::uint64_t a, std::uint64_t b) { return a * b; }
constexpr std::uint64_t i64_and(std::uint64_t a, std::uint64_t b) { return a & b; }
constexpr std::uint64_t i64_or(std::uint64_t a, std::uint64_t b) { return a | b; }
constexpr std::uint64_t i64_xor(std::uint64_t a, std::uint64_t b) { return a ^ b; }
constexpr std::uint64_t i64_shl(std::uint64_t a, std::uint64_t b) { return a << (b & 63u); }
constexpr std::uint64_t i64_shr_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::uint64_t>(static_cast<std::int64_t>(a) >> (b & 63u));
}
constexpr std::uint64_t i64_shr_u(std::uint64_t a, std::uint64_t b) { return a >> (b & 63u); }
constexpr std::uint64_t i64_rotl(std::uint64_t a, std::uint64_t b) {
  const auto shift = b & 63u;
  return (a << shift) | (a >> ((64u - shift) & 63u));
}
constexpr std::uint64_t i64_rotr(std::uint64_t a, std::uint64_t b) {
  const auto shift = b & 63u;
  return (a >> shift) | (a << ((64u - shift) & 63u));
}

bool i64_div_s(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t& result,
               std::uint32_t& trap_code) {
  const auto a = static_cast<std::int64_t>(lhs);
  const auto b = static_cast<std::int64_t>(rhs);
  if (b == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  if (a == INT64_MIN && b == -1) {
    trap_code = kTrapIntegerOverflow;
    return false;
  }
  const auto ua = a < 0 ? 0ull - lhs : lhs;
  const auto ub = b < 0 ? 0ull - rhs : rhs;
  auto quotient = ua / ub;
  if ((a < 0) != (b < 0)) quotient = 0ull - quotient;
  result = quotient;
  trap_code = 0;
  return true;
}

bool i64_div_u(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t& result,
               std::uint32_t& trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs / rhs;
  trap_code = 0;
  return true;
}

bool i64_rem_s(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t& result,
               std::uint32_t& trap_code) {
  const auto a = static_cast<std::int64_t>(lhs);
  const auto b = static_cast<std::int64_t>(rhs);
  if (b == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  if (a == INT64_MIN && b == -1) {
    result = 0;
  } else {
    const auto ua = a < 0 ? 0ull - lhs : lhs;
    const auto ub = b < 0 ? 0ull - rhs : rhs;
    auto remainder = ua % ub;
    if (a < 0) remainder = 0ull - remainder;
    result = remainder;
  }
  trap_code = 0;
  return true;
}

bool i64_rem_u(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t& result,
               std::uint32_t& trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs % rhs;
  trap_code = 0;
  return true;
}

constexpr std::uint64_t i64_eqz(std::uint64_t value) { return value == 0; }
std::uint64_t i64_clz(std::uint64_t value) { return value == 0 ? 64 : __builtin_clzll(value); }
std::uint64_t i64_ctz(std::uint64_t value) { return value == 0 ? 64 : __builtin_ctzll(value); }
std::uint64_t i64_popcnt(std::uint64_t value) { return __builtin_popcountll(value); }
std::uint64_t i64_extend8(std::uint64_t value) {
  return static_cast<std::uint64_t>(static_cast<std::int64_t>(static_cast<std::int8_t>(value)));
}
std::uint64_t i64_extend16(std::uint64_t value) {
  return static_cast<std::uint64_t>(static_cast<std::int64_t>(static_cast<std::int16_t>(value)));
}
std::uint64_t i64_extend32(std::uint64_t value) {
  return static_cast<std::uint64_t>(static_cast<std::int64_t>(static_cast<std::int32_t>(value)));
}

float f32_div(float a, float b) {
  if (b != 0.0f) return a / b;
  if (a == 0.0f || std::isnan(a)) return std::numeric_limits<float>::quiet_NaN();
  return std::copysign(std::numeric_limits<float>::infinity(),
                       std::signbit(a) == std::signbit(b) ? 1.0f : -1.0f);
}
double f64_div(double a, double b) {
  if (b != 0.0) return a / b;
  if (a == 0.0 || std::isnan(a)) return std::numeric_limits<double>::quiet_NaN();
  return std::copysign(std::numeric_limits<double>::infinity(),
                       std::signbit(a) == std::signbit(b) ? 1.0 : -1.0);
}

template <typename T>
T float_min(T a, T b) {
  if (std::isnan(a) || std::isnan(b)) return std::numeric_limits<T>::quiet_NaN();
  if (a == 0 && b == 0) return std::signbit(a) ? a : b;
  return a < b ? a : b;
}

template <typename T>
T float_max(T a, T b) {
  if (std::isnan(a) || std::isnan(b)) return std::numeric_limits<T>::quiet_NaN();
  if (a == 0 && b == 0) return std::signbit(a) ? b : a;
  return a > b ? a : b;
}

float f32_add(float a, float b) { return a + b; }
float f32_sub(float a, float b) { return a - b; }
float f32_mul(float a, float b) { return a * b; }
float f32_min(float a, float b) { return float_min(a, b); }
float f32_max(float a, float b) { return float_max(a, b); }
float f32_copysign(float a, float b) { return std::copysign(a, b); }
float f32_eq(float a, float b) { return a == b; }
float f32_ne(float a, float b) { return a != b; }
float f32_lt(float a, float b) { return a < b; }
float f32_gt(float a, float b) { return a > b; }
float f32_le(float a, float b) { return a <= b; }
float f32_ge(float a, float b) { return a >= b; }
double f64_add(double a, double b) { return a + b; }
double f64_sub(double a, double b) { return a - b; }
double f64_mul(double a, double b) { return a * b; }
double f64_min(double a, double b) { return float_min(a, b); }
double f64_max(double a, double b) { return float_max(a, b); }
double f64_copysign(double a, double b) { return std::copysign(a, b); }
double f64_eq(double a, double b) { return a == b; }
double f64_ne(double a, double b) { return a != b; }
double f64_lt(double a, double b) { return a < b; }
double f64_gt(double a, double b) { return a > b; }
double f64_le(double a, double b) { return a <= b; }
double f64_ge(double a, double b) { return a >= b; }

float f32_abs(float a) { return std::fabs(a); }
float f32_neg(float a) { return -a; }
float f32_ceil(float a) { return std::ceil(a); }
float f32_floor(float a) { return std::floor(a); }
float f32_trunc(float a) { return std::trunc(a); }
float f32_nearest(float a) { return std::nearbyint(a); }
float f32_sqrt(float a) { return std::sqrt(a); }
double f64_abs(double a) { return std::fabs(a); }
double f64_neg(double a) { return -a; }
double f64_ceil(double a) { return std::ceil(a); }
double f64_floor(double a) { return std::floor(a); }
double f64_trunc(double a) { return std::trunc(a); }
double f64_nearest(double a) { return std::nearbyint(a); }
double f64_sqrt(double a) { return std::sqrt(a); }

using conversion_operation_fn = bool (*)(execution_context&, std::uint32_t*, std::uint32_t&);
const std::array<conversion_operation_fn, 256>& conversion_table_data();

template <typename T>
bool truncate_i32(T value, bool is_signed, std::uint32_t& result) {
  if (!std::isfinite(value)) return false;
  if (is_signed) {
    if (value < static_cast<T>(-2147483648.0) || value >= static_cast<T>(2147483648.0)) {
      return false;
    }
    result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
    return true;
  }
  if (value <= static_cast<T>(-1.0) || value >= static_cast<T>(4294967296.0)) return false;
  result = static_cast<std::uint32_t>(value);
  return true;
}

template <typename T>
bool truncate_i64(T value, bool is_signed, std::uint64_t& result) {
  if (!std::isfinite(value)) return false;
  if (is_signed) {
    if (value < static_cast<T>(-9223372036854775808.0) ||
        value >= static_cast<T>(9223372036854775808.0)) {
      return false;
    }
    if (value == static_cast<T>(-9223372036854775808.0)) {
      result = 0x8000'0000'0000'0000ull;
      return true;
    }
    result = static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
    return true;
  }
  if (value <= static_cast<T>(-1.0) || value >= static_cast<T>(18446744073709551616.0)) {
    return false;
  }
  result = static_cast<std::uint64_t>(value);
  return true;
}

bool conversion_i32_wrap_i64(execution_context& current, std::uint32_t* sp,
                             std::uint32_t& trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value) || !push(current, sp, static_cast<std::uint32_t>(value))) {
    return false;
  }
  trap_code = 0;
  return true;
}

template <typename T, bool Signed>
bool conversion_truncate_i32(execution_context& current, std::uint32_t* sp,
                             std::uint32_t& trap_code) {
  T value = 0;
  const bool popped = [&]() {
    if constexpr (sizeof(T) == sizeof(float)) {
      return pop_f32(current, sp, value);
    } else {
      return pop_f64(current, sp, value);
    }
  }();
  std::uint32_t result = 0;
  if (!popped || !truncate_i32(value, Signed, result) || !push(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

template <typename T, bool Signed>
bool conversion_truncate_i64(execution_context& current, std::uint32_t* sp,
                             std::uint32_t& trap_code) {
  T value = 0;
  const bool popped = [&]() {
    if constexpr (sizeof(T) == sizeof(float)) {
      return pop_f32(current, sp, value);
    } else {
      return pop_f64(current, sp, value);
    }
  }();
  std::uint64_t result = 0;
  if (!popped || !truncate_i64(value, Signed, result) || !push_u64(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_extend_i32_s(execution_context& current, std::uint32_t* sp,
                                 std::uint32_t& trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value) ||
      !push_u64(current, sp, static_cast<std::uint64_t>(static_cast<std::int64_t>(
          static_cast<std::int32_t>(value))))) {
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_extend_i32_u(execution_context& current, std::uint32_t* sp,
                                 std::uint32_t& trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value) || !push_u64(current, sp, value)) return false;
  trap_code = 0;
  return true;
}

template <typename T>
bool conversion_pop_i32(execution_context& current, std::uint32_t* sp, std::uint32_t&,
                        bool is_signed, bool to_f32) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value)) return false;
  const auto integer = is_signed ? static_cast<double>(static_cast<std::int32_t>(value))
                                 : static_cast<double>(value);
  if (to_f32) return push_f32(current, sp, static_cast<float>(integer));
  return push_f64(current, sp, integer);
}

template <typename T>
bool conversion_pop_i64(execution_context& current, std::uint32_t* sp, std::uint32_t&,
                        bool is_signed, bool to_f32) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value)) return false;
  const auto integer = is_signed ? static_cast<long double>(static_cast<std::int64_t>(value))
                                 : static_cast<long double>(value);
  if (to_f32) return push_f32(current, sp, static_cast<float>(integer));
  return push_f64(current, sp, static_cast<double>(integer));
}

bool conversion_f32_convert_i32_s(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i32<float>(c, s, t, true, true);
}
bool conversion_f32_convert_i32_u(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i32<float>(c, s, t, false, true);
}
bool conversion_f64_convert_i32_s(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i32<double>(c, s, t, true, false);
}
bool conversion_f64_convert_i32_u(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i32<double>(c, s, t, false, false);
}
bool conversion_f32_convert_i64_s(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i64<float>(c, s, t, true, true);
}
bool conversion_f32_convert_i64_u(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i64<float>(c, s, t, false, true);
}
bool conversion_f64_convert_i64_s(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i64<double>(c, s, t, true, false);
}
bool conversion_f64_convert_i64_u(execution_context& c, std::uint32_t* s, std::uint32_t& t) {
  return conversion_pop_i64<double>(c, s, t, false, false);
}

bool conversion_f64_promote_f32(execution_context& current, std::uint32_t* sp,
                                std::uint32_t& trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !push_f64(current, sp, static_cast<double>(value))) return false;
  trap_code = 0;
  return true;
}

bool conversion_f32_demote_f64(execution_context& current, std::uint32_t* sp,
                               std::uint32_t& trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !push_f32(current, sp, static_cast<float>(value))) return false;
  trap_code = 0;
  return true;
}

bool conversion_identity(execution_context&, std::uint32_t*, std::uint32_t& trap_code) {
  trap_code = 0;
  return true;
}

FIREBALL_CPS_CALL step_result binary_i32(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  const std::uint8_t opcode = current.code[current.ip];
  const auto operation = binary_operation_table_data()[opcode];
  if (operation == nullptr) return fallback(current.ip);
  std::uint32_t rhs = 0;
  std::uint32_t lhs = 0;
  if (!pop(current, sp, rhs) || !pop(current, sp, lhs)) return fallback(current.ip);
  std::uint32_t result = 0;
  std::uint32_t trap_code = 0;
  if (!operation(lhs, rhs, result, trap_code)) return trap(trap_code);
  if (!push(current, sp, result)) return fallback(current.ip);
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_i64_const(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::int64_t value = 0;
  if (!read_s64(current, operand_ip, value) ||
      !push_u64(current, sp, static_cast<std::uint64_t>(value))) {
    return fallback(current.ip);
  }
  current.ip = operand_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_f32_const(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  if (current.ip + 5 > current.code_size ||
      !push(current, sp, static_cast<std::uint32_t>(current.code[current.ip + 1]) |
                         (static_cast<std::uint32_t>(current.code[current.ip + 2]) << 8) |
                         (static_cast<std::uint32_t>(current.code[current.ip + 3]) << 16) |
                         (static_cast<std::uint32_t>(current.code[current.ip + 4]) << 24))) {
    return fallback(current.ip);
  }
  current.ip += 5;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_f64_const(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  if (current.ip + 9 > current.code_size) return fallback(current.ip);
  std::uint64_t raw = 0;
  for (std::uint32_t word = 0; word < 8; ++word) {
    raw |= static_cast<std::uint64_t>(current.code[current.ip + 1 + word]) << (word * 8);
  }
  if (!push_u64(current, sp, raw)) return fallback(current.ip);
  current.ip += 9;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_i64_unary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto operation = i64_unary_table_data()[current.code[current.ip]];
  std::uint64_t value = 0;
  if (operation == nullptr || !pop_u64(current, sp, value)) return fallback(current.ip);
  const auto result = operation(value);
  if (numeric_result_is_bool_data()[current.code[current.ip]]) {
    if (!push(current, sp, static_cast<std::uint32_t>(result))) return fallback(current.ip);
  } else if (!push_u64(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_i64_binary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto operation = i64_binary_table_data()[current.code[current.ip]];
  std::uint64_t rhs = 0;
  std::uint64_t lhs = 0;
  if (operation == nullptr || !pop_u64(current, sp, rhs) || !pop_u64(current, sp, lhs)) {
    return fallback(current.ip);
  }
  std::uint64_t result = 0;
  std::uint32_t trap_code = 0;
  if (!operation(lhs, rhs, result, trap_code)) return trap(trap_code);
  if (numeric_result_is_bool_data()[current.code[current.ip]]) {
    if (!push(current, sp, static_cast<std::uint32_t>(result))) return fallback(current.ip);
  } else if (!push_u64(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_f32_unary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto operation = f32_unary_table_data()[current.code[current.ip]];
  float value = 0.0f;
  if (operation == nullptr || !pop_f32(current, sp, value) ||
      !push_f32(current, sp, operation(value))) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_f32_binary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto operation = f32_binary_table_data()[current.code[current.ip]];
  float rhs = 0.0f;
  float lhs = 0.0f;
  if (operation == nullptr || !pop_f32(current, sp, rhs) || !pop_f32(current, sp, lhs)) {
    return fallback(current.ip);
  }
  const auto result = operation(lhs, rhs);
  if (numeric_result_is_bool_data()[current.code[current.ip]]) {
    if (!push(current, sp, static_cast<std::uint32_t>(result))) return fallback(current.ip);
  } else if (!push_f32(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_f64_unary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto operation = f64_unary_table_data()[current.code[current.ip]];
  double value = 0.0;
  if (operation == nullptr || !pop_f64(current, sp, value) ||
      !push_f64(current, sp, operation(value))) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_f64_binary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto operation = f64_binary_table_data()[current.code[current.ip]];
  double rhs = 0.0;
  double lhs = 0.0;
  if (operation == nullptr || !pop_f64(current, sp, rhs) || !pop_f64(current, sp, lhs)) {
    return fallback(current.ip);
  }
  const auto result = operation(lhs, rhs);
  if (numeric_result_is_bool_data()[current.code[current.ip]]) {
    if (!push(current, sp, static_cast<std::uint32_t>(result))) return fallback(current.ip);
  } else if (!push_f64(current, sp, result)) {
    return fallback(current.ip);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_conversion(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto operation = conversion_table_data()[current.code[current.ip]];
  std::uint32_t trap_code = 0;
  if (operation == nullptr || !operation(current, sp, trap_code)) {
    return trap_code == 0 ? fallback(current.ip) : trap(trap_code);
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_nop(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_else(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  if (current.control_stack->size <= current.control_base) {
    return fallback(current.ip);
  }
  const auto frame = current.control_stack->frames[current.control_stack->size - 1];
  current.control_stack->size -= 1;
  current.ip = frame.match_end + 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_block(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto* entry = control_entry(current, current.ip);
  if (entry == nullptr || entry->match_end == kSentinel ||
      current.control_stack->size >= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY) {
    return fallback(current.ip);
  }
  auto& frame = current.control_stack->frames[current.control_stack->size++];
  frame.kind = current.code[current.ip] == 0x03 ? 1u : 0u;
  frame.start = current.ip;
  frame.match_end = entry->match_end;
  frame.stack_height = current.sp_offset;
  frame.result_arity = static_cast<std::uint16_t>(entry->result_arity);
  current.ip = entry->next_pc;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_if(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto* entry = control_entry(current, current.ip);
  std::uint32_t condition = 0;
  if (entry == nullptr || entry->match_end == kSentinel || !pop(current, sp, condition)) {
    return fallback(current.ip);
  }
  if (condition == 0 && entry->else_offset == kSentinel) {
    current.ip = entry->match_end + 1;
    [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
  }
  if (current.control_stack->size >= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY) {
    return fallback(current.ip);
  }
  auto& frame = current.control_stack->frames[current.control_stack->size++];
  frame.kind = 2u;
  frame.start = current.ip;
  frame.match_end = entry->match_end;
  frame.stack_height = current.sp_offset;
  frame.result_arity = static_cast<std::uint16_t>(entry->result_arity);
  current.ip = condition == 0 ? entry->else_offset + 1 : entry->next_pc;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_br(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t depth = 0;
  std::uint32_t next_ip = 0;
  if (!read_u32(current, operand_ip, depth) || !branch(current, sp, depth, next_ip)) {
    return fallback(current.ip);
  }
  if (next_ip == kSentinel) return complete();
  current.ip = next_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_br_if(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t depth = 0;
  std::uint32_t condition = 0;
  if (!read_u32(current, operand_ip, depth) || !pop(current, sp, condition)) {
    return fallback(current.ip);
  }
  if (condition == 0) {
    current.ip = operand_ip;
    [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
  }
  std::uint32_t next_ip = 0;
  if (!branch(current, sp, depth, next_ip)) return fallback(current.ip);
  if (next_ip == kSentinel) return complete();
  current.ip = next_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_local_get(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  std::uint32_t offset = 0;
  std::uint32_t width = 0;
  if (!read_u32(current, operand_ip, index) || !local_span(current, index, offset, width)) {
    return fallback(current.ip);
  }
  for (std::uint32_t word = 0; word < width; ++word) {
    if (!push(current, sp, local_base[offset + word])) return fallback(current.ip);
  }
  current.ip = operand_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_local_set(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  std::uint32_t offset = 0;
  std::uint32_t width = 0;
  if (!read_u32(current, operand_ip, index) || !local_span(current, index, offset, width) ||
      current.sp_offset < width) {
    return fallback(current.ip);
  }
  const auto source = current.sp_offset - width;
  for (std::uint32_t word = 0; word < width; ++word) {
    local_base[offset + word] = sp[source + word];
  }
  current.sp_offset -= width;
  current.ip = operand_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_local_tee(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  std::uint32_t offset = 0;
  std::uint32_t width = 0;
  if (!read_u32(current, operand_ip, index) || !local_span(current, index, offset, width) ||
      current.sp_offset < width) {
    return fallback(current.ip);
  }
  const auto source = current.sp_offset - width;
  for (std::uint32_t word = 0; word < width; ++word) {
    local_base[offset + word] = sp[source + word];
  }
  current.ip = operand_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_drop(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto* entry = control_entry(current, current.ip);
  const auto width = entry == nullptr ? 0u : entry->operand_width;
  if (width == 0 || current.sp_offset < width) return fallback(current.ip);
  current.sp_offset -= width;
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_select(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto* entry = control_entry(current, current.ip);
  const auto width = entry == nullptr ? 0u : entry->operand_width;
  if ((width != 1 && width != 2) || current.sp_offset < 1 + width * 2) {
    return fallback(current.ip);
  }
  const auto condition = sp[--current.sp_offset];
  const auto b = current.sp_offset - width;
  const auto a = b - width;
  if (condition == 0) {
    for (std::uint32_t word = 0; word < width; ++word) sp[a + word] = sp[b + word];
  }
  current.sp_offset = a + width;
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_end(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  if (current.control_stack->size > current.control_base) {
    current.control_stack->size -= 1;
  }
  current.ip += 1;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_return(
    execution_context* context, std::uint32_t*, std::uint32_t*, std::uint32_t) {
  context->ip = kSentinel;
  return complete();
}

FIREBALL_CPS_CALL step_result h_unreachable(
    execution_context*, std::uint32_t*, std::uint32_t*, std::uint32_t) {
  return trap(kTrapUnreachable);
}

FIREBALL_CPS_CALL step_result h_i32_const(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t) {
  auto& current = *context;
  auto operand_ip = current.ip + 1;
  std::int32_t value = 0;
  if (!read_s32(current, operand_ip, value) ||
      !push(current, sp, static_cast<std::uint32_t>(value))) {
    return fallback(current.ip);
  }
  current.ip = operand_ip;
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_binary(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return binary_i32(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_eqz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_eqz(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_clz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_clz(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_ctz(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base, std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_ctz(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_popcnt(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_popcnt(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_extend8(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_extend8(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result h_unary_extend16(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t tos) {
  [[clang::musttail]] return unary_i32_extend16(context, sp, local_base, tos);
}

FIREBALL_CPS_CALL step_result dispatch(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
  std::uint32_t tos) {
  auto& current = *context;
  current.cf_offset = current.control_stack->size;
  if (current.ip >= current.code_size) {
    return complete();
  }
  current.stack_checkpoint = current.sp_offset;
  const auto handler = handler_table_data()[current.code[current.ip]];
  if (handler == nullptr) {
    return fallback(current.ip);
  }
  [[clang::musttail]] return handler(context, sp, local_base, tos);
}

constexpr auto make_binary_operation_table() {
  std::array<binary_operation_fn, 256> table{};
  table[0x46] = evaluate_plain<op_eq>;
  table[0x47] = evaluate_plain<op_ne>;
  table[0x48] = evaluate_plain<op_lt_s>;
  table[0x49] = evaluate_plain<op_lt_u>;
  table[0x4A] = evaluate_plain<op_gt_s>;
  table[0x4B] = evaluate_plain<op_gt_u>;
  table[0x4C] = evaluate_plain<op_le_s>;
  table[0x4D] = evaluate_plain<op_le_u>;
  table[0x4E] = evaluate_plain<op_ge_s>;
  table[0x4F] = evaluate_plain<op_ge_u>;
  table[0x6A] = evaluate_plain<op_add>;
  table[0x6B] = evaluate_plain<op_sub>;
  table[0x6C] = evaluate_plain<op_mul>;
  table[0x6D] = evaluate_div_s;
  table[0x6E] = evaluate_div_u;
  table[0x6F] = evaluate_rem_s;
  table[0x70] = evaluate_rem_u;
  table[0x71] = evaluate_plain<op_and>;
  table[0x72] = evaluate_plain<op_or>;
  table[0x73] = evaluate_plain<op_xor>;
  table[0x74] = evaluate_plain<op_shl>;
  table[0x75] = evaluate_plain<op_shr_s>;
  table[0x76] = evaluate_plain<op_shr_u>;
  table[0x77] = evaluate_plain<op_rotl>;
  table[0x78] = evaluate_plain<op_rotr>;
  return table;
}

constexpr auto make_i64_unary_table() {
  std::array<i64_unary_operation_fn, 256> table{};
  table[0x50] = i64_eqz;
  table[0x79] = i64_clz;
  table[0x7A] = i64_ctz;
  table[0x7B] = i64_popcnt;
  table[0xC2] = i64_extend8;
  table[0xC3] = i64_extend16;
  table[0xC4] = i64_extend32;
  return table;
}

constexpr auto make_i64_binary_table() {
  std::array<i64_binary_operation_fn, 256> table{};
  table[0x51] = evaluate_i64_plain<i64_eq>;
  table[0x52] = evaluate_i64_plain<i64_ne>;
  table[0x53] = evaluate_i64_plain<i64_lt_s>;
  table[0x54] = evaluate_i64_plain<i64_lt_u>;
  table[0x55] = evaluate_i64_plain<i64_gt_s>;
  table[0x56] = evaluate_i64_plain<i64_gt_u>;
  table[0x57] = evaluate_i64_plain<i64_le_s>;
  table[0x58] = evaluate_i64_plain<i64_le_u>;
  table[0x59] = evaluate_i64_plain<i64_ge_s>;
  table[0x5A] = evaluate_i64_plain<i64_ge_u>;
  table[0x7C] = evaluate_i64_plain<i64_add>;
  table[0x7D] = evaluate_i64_plain<i64_sub>;
  table[0x7E] = evaluate_i64_plain<i64_mul>;
  table[0x7F] = i64_div_s;
  table[0x80] = i64_div_u;
  table[0x81] = i64_rem_s;
  table[0x82] = i64_rem_u;
  table[0x83] = evaluate_i64_plain<i64_and>;
  table[0x84] = evaluate_i64_plain<i64_or>;
  table[0x85] = evaluate_i64_plain<i64_xor>;
  table[0x86] = evaluate_i64_plain<i64_shl>;
  table[0x87] = evaluate_i64_plain<i64_shr_s>;
  table[0x88] = evaluate_i64_plain<i64_shr_u>;
  table[0x89] = evaluate_i64_plain<i64_rotl>;
  table[0x8A] = evaluate_i64_plain<i64_rotr>;
  return table;
}

constexpr auto make_f32_binary_table() {
  std::array<f32_binary_operation_fn, 256> table{};
  table[0x5B] = f32_eq;
  table[0x5C] = f32_ne;
  table[0x5D] = f32_lt;
  table[0x5E] = f32_gt;
  table[0x5F] = f32_le;
  table[0x60] = f32_ge;
  table[0x92] = f32_add;
  table[0x93] = f32_sub;
  table[0x94] = f32_mul;
  table[0x95] = f32_div;
  table[0x96] = f32_min;
  table[0x97] = f32_max;
  table[0x98] = f32_copysign;
  return table;
}

constexpr auto make_f64_binary_table() {
  std::array<f64_binary_operation_fn, 256> table{};
  table[0x61] = f64_eq;
  table[0x62] = f64_ne;
  table[0x63] = f64_lt;
  table[0x64] = f64_gt;
  table[0x65] = f64_le;
  table[0x66] = f64_ge;
  table[0xA0] = f64_add;
  table[0xA1] = f64_sub;
  table[0xA2] = f64_mul;
  table[0xA3] = f64_div;
  table[0xA4] = f64_min;
  table[0xA5] = f64_max;
  table[0xA6] = f64_copysign;
  return table;
}

constexpr auto make_f32_unary_table() {
  std::array<f32_unary_operation_fn, 256> table{};
  table[0x8B] = f32_abs;
  table[0x8C] = f32_neg;
  table[0x8D] = f32_ceil;
  table[0x8E] = f32_floor;
  table[0x8F] = f32_trunc;
  table[0x90] = f32_nearest;
  table[0x91] = f32_sqrt;
  return table;
}

constexpr auto make_f64_unary_table() {
  std::array<f64_unary_operation_fn, 256> table{};
  table[0x99] = f64_abs;
  table[0x9A] = f64_neg;
  table[0x9B] = f64_ceil;
  table[0x9C] = f64_floor;
  table[0x9D] = f64_trunc;
  table[0x9E] = f64_nearest;
  table[0x9F] = f64_sqrt;
  return table;
}

constexpr auto make_numeric_result_is_bool() {
  std::array<bool, 256> table{};
  table[0x50] = true;
  for (const auto opcode : {0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A,
                            0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x62, 0x63, 0x64,
                            0x65, 0x66}) {
    table[opcode] = true;
  }
  return table;
}

constexpr auto make_conversion_table() {
  std::array<conversion_operation_fn, 256> table{};
  table[0xA7] = conversion_i32_wrap_i64;
  table[0xA8] = conversion_truncate_i32<float, true>;
  table[0xA9] = conversion_truncate_i32<float, false>;
  table[0xAA] = conversion_truncate_i32<double, true>;
  table[0xAB] = conversion_truncate_i32<double, false>;
  table[0xAC] = conversion_i64_extend_i32_s;
  table[0xAD] = conversion_i64_extend_i32_u;
  table[0xAE] = conversion_truncate_i64<float, true>;
  table[0xAF] = conversion_truncate_i64<float, false>;
  table[0xB0] = conversion_truncate_i64<double, true>;
  table[0xB1] = conversion_truncate_i64<double, false>;
  table[0xB2] = conversion_f32_convert_i32_s;
  table[0xB3] = conversion_f32_convert_i32_u;
  table[0xB4] = conversion_f32_convert_i64_s;
  table[0xB5] = conversion_f32_convert_i64_u;
  table[0xB6] = conversion_f32_demote_f64;
  table[0xB7] = conversion_f64_convert_i32_s;
  table[0xB8] = conversion_f64_convert_i32_u;
  table[0xB9] = conversion_f64_convert_i64_s;
  table[0xBA] = conversion_f64_convert_i64_u;
  table[0xBB] = conversion_f64_promote_f32;
  table[0xBC] = conversion_identity;
  table[0xBD] = conversion_identity;
  table[0xBE] = conversion_identity;
  table[0xBF] = conversion_identity;
  return table;
}

constexpr auto make_handler_table() {
  std::array<handler_fn, 256> table{};
  table[0x00] = h_unreachable;
  table[0x01] = h_nop;
  table[0x02] = h_block;
  table[0x03] = h_block;
  table[0x04] = h_if;
  table[0x05] = h_else;
  table[0x0B] = h_end;
  table[0x0C] = h_br;
  table[0x0D] = h_br_if;
  table[0x0F] = h_return;
  table[0x1A] = h_drop;
  table[0x1B] = h_select;
  table[0x20] = h_local_get;
  table[0x21] = h_local_set;
  table[0x22] = h_local_tee;
  table[0x41] = h_i32_const;
  table[0x42] = h_i64_const;
  table[0x43] = h_f32_const;
  table[0x44] = h_f64_const;
  table[0x45] = h_unary_eqz;
  for (const auto opcode : {0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F,
                            0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F, 0x70, 0x71, 0x72, 0x73,
                            0x74, 0x75, 0x76, 0x77, 0x78}) {
    table[opcode] = h_binary;
  }
  table[0x50] = h_i64_unary;
  for (const auto opcode : {0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A,
                            0x7C, 0x7D, 0x7E, 0x7F, 0x80, 0x81, 0x82, 0x83, 0x84, 0x85,
                            0x86, 0x87, 0x88, 0x89, 0x8A}) {
    table[opcode] = h_i64_binary;
  }
  for (const auto opcode : {0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x92, 0x93, 0x94, 0x95,
                            0x96, 0x97, 0x98}) {
    table[opcode] = h_f32_binary;
  }
  for (const auto opcode : {0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0xA0, 0xA1, 0xA2, 0xA3,
                            0xA4, 0xA5, 0xA6}) {
    table[opcode] = h_f64_binary;
  }
  for (const auto opcode : {0x79, 0x7A, 0x7B, 0xC2, 0xC3, 0xC4}) {
    table[opcode] = h_i64_unary;
  }
  table[0x67] = h_unary_clz;
  table[0x68] = h_unary_ctz;
  table[0x69] = h_unary_popcnt;
  table[0xC0] = h_unary_extend8;
  table[0xC1] = h_unary_extend16;
  for (const auto opcode : {0x8B, 0x8C, 0x8D, 0x8E, 0x8F, 0x90, 0x91}) {
    table[opcode] = h_f32_unary;
  }
  for (const auto opcode : {0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E, 0x9F}) {
    table[opcode] = h_f64_unary;
  }
  for (const auto opcode : {0xA7, 0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB0,
                            0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA,
                            0xBB, 0xBC, 0xBD, 0xBE, 0xBF}) {
    table[opcode] = h_conversion;
  }
  return table;
}

constexpr auto kHandlerTable = make_handler_table();
constexpr auto kBinaryOperationTable = make_binary_operation_table();
constexpr auto kI64BinaryTable = make_i64_binary_table();
constexpr auto kI64UnaryTable = make_i64_unary_table();
constexpr auto kF32BinaryTable = make_f32_binary_table();
constexpr auto kF64BinaryTable = make_f64_binary_table();
constexpr auto kF32UnaryTable = make_f32_unary_table();
constexpr auto kF64UnaryTable = make_f64_unary_table();
constexpr auto kNumericResultIsBool = make_numeric_result_is_bool();
constexpr auto kConversionTable = make_conversion_table();

const std::array<handler_fn, 256>& handler_table_data() { return kHandlerTable; }

const std::array<binary_operation_fn, 256>& binary_operation_table_data() {
  return kBinaryOperationTable;
}

const std::array<i64_binary_operation_fn, 256>& i64_binary_table_data() {
  return kI64BinaryTable;
}

const std::array<i64_unary_operation_fn, 256>& i64_unary_table_data() {
  return kI64UnaryTable;
}

const std::array<f32_binary_operation_fn, 256>& f32_binary_table_data() {
  return kF32BinaryTable;
}

const std::array<f64_binary_operation_fn, 256>& f64_binary_table_data() {
  return kF64BinaryTable;
}

const std::array<f32_unary_operation_fn, 256>& f32_unary_table_data() {
  return kF32UnaryTable;
}

const std::array<f64_unary_operation_fn, 256>& f64_unary_table_data() {
  return kF64UnaryTable;
}

const std::array<bool, 256>& numeric_result_is_bool_data() {
  return kNumericResultIsBool;
}

const std::array<conversion_operation_fn, 256>& conversion_table_data() {
  return kConversionTable;
}

struct buffer_guard {
  Py_buffer view{};
  bool active = false;

  ~buffer_guard() {
    if (active) {
      PyBuffer_Release(&view);
    }
  }
};

PyObject* run_step(PyObject*, PyObject* args) {
  PyObject* code_object = nullptr;
  PyObject* context_object = nullptr;
  PyObject* stack_object = nullptr;
  PyObject* locals_object = nullptr;
  PyObject* control_object = nullptr;
  unsigned int stack_size = 0;
  unsigned int ip = 0;
  unsigned int local_slots = 0;
  unsigned int control_base = 0;
  if (!PyArg_ParseTuple(args, "OOOOOIIII", &code_object, &context_object, &stack_object,
                        &locals_object, &control_object, &stack_size, &ip, &local_slots,
                        &control_base)) {
    return nullptr;
  }
  buffer_guard code_buffer;
  if (PyObject_GetBuffer(code_object, &code_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  code_buffer.active = true;
  const auto* raw_code = static_cast<const std::uint8_t*>(code_buffer.view.buf);
  const auto code_size = code_buffer.view.len;

  buffer_guard context_buffer;
  if (PyObject_GetBuffer(context_object, &context_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  context_buffer.active = true;

  buffer_guard stack_buffer;
  if (PyObject_GetBuffer(stack_object, &stack_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  stack_buffer.active = true;
  buffer_guard locals_buffer;
  if (PyObject_GetBuffer(locals_object, &locals_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  locals_buffer.active = true;
  buffer_guard control_buffer;
  if (PyObject_GetBuffer(control_object, &control_buffer.view, PyBUF_SIMPLE) != 0) {
    return nullptr;
  }
  control_buffer.active = true;
  const auto stack_bytes = static_cast<Py_ssize_t>(128 * sizeof(std::uint32_t));
  const auto context_bytes = static_cast<Py_ssize_t>(sizeof(fireball_execution_context_native));
  const auto control_bytes = static_cast<Py_ssize_t>(sizeof(fireball_control_stack_native));
  const auto local_bytes = static_cast<Py_ssize_t>(local_slots * sizeof(std::uint32_t));
  if (stack_size > 128 || context_buffer.view.len < context_bytes ||
      stack_buffer.view.len < stack_bytes || control_buffer.view.len < control_bytes ||
      locals_buffer.view.len < local_bytes ||
      ip > static_cast<unsigned int>(code_size)) {
    PyErr_SetString(PyExc_ValueError, "invalid native interpreter buffer");
    return nullptr;
  }

  auto* execution_context =
      static_cast<fireball_execution_context_native*>(context_buffer.view.buf);
  auto* control_stack = static_cast<fireball_control_stack_native*>(control_buffer.view.buf);
  if (control_base > control_stack->size || control_stack->size > 32) {
    PyErr_SetString(PyExc_ValueError, "invalid native control stack window");
    return nullptr;
  }
  if (execution_context->call_stack != nullptr) {
    if (execution_context->call_stack->size > FIREBALL_NATIVE_CALL_STACK_CAPACITY ||
        execution_context->call_base > execution_context->call_stack->size) {
      PyErr_SetString(PyExc_ValueError, "invalid native call stack window");
      return nullptr;
    }
    execution_context->call_offset = execution_context->call_stack->size;
  }
  auto* stack = static_cast<std::uint32_t*>(stack_buffer.view.buf);
  auto* locals = static_cast<std::uint32_t*>(locals_buffer.view.buf);
  execution_context->ip = ip;
  execution_context->code = raw_code;
  execution_context->code_size = static_cast<std::uint32_t>(code_size);
  execution_context->control_stack = control_stack;
  execution_context->control_base = control_base;
  execution_context->sp_offset = stack_size;
  execution_context->cf_offset = control_stack->size;
  execution_context->stack_checkpoint = stack_size;
  const step_result result =
      dispatch(execution_context, stack, locals, top_value(*execution_context, stack));
  if (result.kind == kFallback) {
    execution_context->sp_offset = execution_context->stack_checkpoint;
    execution_context->ip = result.next_ip;
    return Py_BuildValue("IIII", kFallback, result.next_ip, execution_context->sp_offset, 0);
  }
  if (result.kind == kTrap) {
    return Py_BuildValue("IIII", kTrap, kSentinel, execution_context->sp_offset,
                         result.trap_code);
  }
  return Py_BuildValue("IIII", kComplete, kSentinel, execution_context->sp_offset, 0);
}

PyMethodDef module_methods[] = {
    {"run_step", run_step, METH_VARARGS, "Run the native CPS handler table until a boundary."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_interpreter_native",
    "Native CPS handlers for the Tier 3 interpreter.",
    -1,
    module_methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

}  // namespace

PyMODINIT_FUNC PyInit__interpreter_native() { return PyModule_Create(&module_definition); }

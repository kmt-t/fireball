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

#ifndef FB_CONF_NATIVE_JIT_TRACE_CAPACITY
#error "Native JIT dispatch capacity must come from tier1_core/config.py"
#endif
#ifndef FB_CONF_NATIVE_JIT_BLOCK_CAPACITY
#error "Native JIT block-observation capacity must come from tier1_core/config.py"
#endif
#ifndef FB_CONF_RUNTIME_PROFILE_STATS
#error "Runtime profile stats selection must come from tier1_core/config.py"
#endif
#ifndef FB_CONF_JIT_HOTSPOT_PROFILING
#error "JIT hotspot profiling selection must come from tier1_core/config.py"
#endif

static_assert(FB_CONF_RUNTIME_PROFILE_STATS == 0 || FB_CONF_RUNTIME_PROFILE_STATS == 1);
static_assert(FB_CONF_JIT_HOTSPOT_PROFILING == 0 || FB_CONF_JIT_HOTSPOT_PROFILING == 1);

namespace {

#if defined(_WIN32)
#define FIREBALL_CPS_CALL __fastcall
#else
#define FIREBALL_CPS_CALL
#endif

constexpr std::uint32_t kFallback = 0;
constexpr std::uint32_t kComplete = 1;
constexpr std::uint32_t kTrap = 2;
constexpr std::uint32_t kBlockBoundary = 3;
constexpr std::uint32_t kOldestTraceHit = 4;
constexpr std::uint32_t kStopAtBlockBoundaryFlag = 1u << 0;
constexpr std::uint32_t kStopAfterControlFlag = 1u << 1;
constexpr std::uint32_t kDispatchYield = 5;
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

constexpr step_result block_boundary(std::uint32_t ip) {
  return {kBlockBoundary, ip, 0};
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
  if (current.sp_capacity < 2 || current.sp_offset > current.sp_capacity - 2) return false;
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

bool is_requested_block_boundary(const execution_context& current) {
  if ((current.runtime_flags & kStopAtBlockBoundaryFlag) == 0) return false;
  const auto* frame = active_frame(current);
  if (frame == nullptr) return false;
  const auto pc = (frame->func_index << 16) | (current.ip & 0xFFFFu);
  return pc == frame->boundary_next_pc || pc == frame->boundary_loops_to;
}

bool should_stop_after_control(const execution_context& current) {
  return (current.runtime_flags & kStopAfterControlFlag) != 0;
}

void record_loop_backedge(execution_context& current, std::uint32_t source_ip,
                          std::uint32_t target_ip) {
  if (target_ip < source_ip && current.loop_jump_count < UINT32_MAX) {
    ++current.loop_jump_count;
  }
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
      result_arity > current.sp_capacity - saved_height ||
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
  if (current.sp_offset >= current.sp_capacity) {
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
  const auto* call_frame = active_frame(current);
  current.ip = call_frame != nullptr && call_frame->boundary_next_pc != kSentinel
                   ? call_frame->boundary_next_pc & 0xFFFFu
                   : frame.match_end + 1;
  if (should_stop_after_control(current)) return block_boundary(current.ip);
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
  if (should_stop_after_control(current)) return block_boundary(current.ip);
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
    if (should_stop_after_control(current)) return block_boundary(current.ip);
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
  if (should_stop_after_control(current)) return block_boundary(current.ip);
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_br(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto source_ip = current.ip;
  auto operand_ip = current.ip + 1;
  std::uint32_t depth = 0;
  std::uint32_t next_ip = 0;
  if (!read_u32(current, operand_ip, depth) || !branch(current, sp, depth, next_ip)) {
    return fallback(current.ip);
  }
  if (next_ip == kSentinel) return complete();
  const auto* call_frame = active_frame(current);
  if (call_frame != nullptr && call_frame->boundary_next_pc != kSentinel) {
    next_ip = call_frame->boundary_next_pc & 0xFFFFu;
  }
  record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  if (should_stop_after_control(current)) return block_boundary(current.ip);
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_br_if(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto source_ip = current.ip;
  auto operand_ip = current.ip + 1;
  std::uint32_t depth = 0;
  std::uint32_t condition = 0;
  if (!read_u32(current, operand_ip, depth) || !pop(current, sp, condition)) {
    return fallback(current.ip);
  }
  if (condition == 0) {
    current.ip = operand_ip;
    if (should_stop_after_control(current)) return block_boundary(current.ip);
    [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
  }
  std::uint32_t next_ip = 0;
  if (!branch(current, sp, depth, next_ip)) return fallback(current.ip);
  if (next_ip == kSentinel) return complete();
  const auto* call_frame = active_frame(current);
  if (call_frame != nullptr && call_frame->boundary_loops_to != kSentinel) {
    next_ip = call_frame->boundary_loops_to & 0xFFFFu;
  }
  record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  if (should_stop_after_control(current)) return block_boundary(current.ip);
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
  if (should_stop_after_control(current)) return block_boundary(current.ip);
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
}

FIREBALL_CPS_CALL step_result h_return(
    execution_context* context, std::uint32_t*, std::uint32_t*, std::uint32_t) {
  if (should_stop_after_control(*context)) return block_boundary(kSentinel);
  context->ip = kSentinel;
  return complete();
}

FIREBALL_CPS_CALL step_result h_br_table(
    execution_context* context, std::uint32_t* sp, std::uint32_t* local_base,
    std::uint32_t) {
  auto& current = *context;
  const auto source_ip = current.ip;
  auto operand_ip = current.ip + 1;
  std::uint32_t target_count = 0;
  std::uint32_t selector = 0;
  const auto* table = control_entry(current, source_ip);
  if (table == nullptr || table->br_table_targets == nullptr ||
      !read_u32(current, operand_ip, target_count) || !pop(current, sp, selector) ||
      target_count + 1 != table->br_table_target_count) {
    return fallback(current.ip);
  }

  const auto selected_index = selector < target_count ? selector : target_count;
  const auto selected_depth = table->br_table_targets[selected_index];

  std::uint32_t next_ip = 0;
  if (!branch(current, sp, selected_depth, next_ip)) return fallback(current.ip);
  if (next_ip == kSentinel) return complete();
  record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  if (should_stop_after_control(current)) {
    return block_boundary(current.ip);
  }
  [[clang::musttail]] return dispatch(context, sp, local_base, top_value(current, sp));
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
  if (is_requested_block_boundary(current)) return block_boundary(current.ip);
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
  table[0x0E] = h_br_table;
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

struct buffer_guard {
  Py_buffer view{};
  bool active = false;

  ~buffer_guard() {
    if (active) {
      PyBuffer_Release(&view);
    }
  }
};

struct native_trace_descriptor {
  std::uint32_t head_pc;
  std::uintptr_t entry_address;
  std::uint32_t byte_span;
  std::uint32_t result_words;
  std::uint32_t has_return_value;
  std::uint32_t stack_words;
  std::uint32_t frame_depth;
  std::uint32_t next_pc;
  std::uint32_t loops_to;
  std::uint32_t chain_next_pc;
  std::uint32_t chain_stack_words;
  std::uint32_t promote_on_hit;
};

constexpr std::uint32_t kNoPc = 0xFFFF'FFFFu;
constexpr std::uint32_t kNativeTraceDispatchEntryWords = 12;

using native_trace_entry_fn = void (*)(void*, std::uint32_t*, std::uint32_t*, std::uint32_t);

bool read_dispatch_entry(PyObject* value, native_trace_descriptor& output) {
  PyObject* sequence = PySequence_Fast(value, "native trace entry must be a sequence");
  if (sequence == nullptr) return false;
  if (PySequence_Fast_GET_SIZE(sequence) != kNativeTraceDispatchEntryWords) {
    PyErr_SetString(PyExc_ValueError, "native trace entry has an invalid field count");
    Py_DECREF(sequence);
    return false;
  }
  auto* fields = PySequence_Fast_ITEMS(sequence);
  const auto head_pc = PyLong_AsUnsignedLong(fields[0]);
  const auto entry_address = PyLong_AsUnsignedLongLong(fields[1]);
  const auto byte_span = PyLong_AsUnsignedLong(fields[2]);
  const auto result_words = PyLong_AsUnsignedLong(fields[3]);
  const auto has_return_value = PyLong_AsUnsignedLong(fields[4]);
  const auto stack_words = PyLong_AsUnsignedLong(fields[5]);
  const auto frame_depth = PyLong_AsUnsignedLong(fields[6]);
  const auto next_pc = PyLong_AsUnsignedLong(fields[7]);
  const auto loops_to = PyLong_AsUnsignedLong(fields[8]);
  const auto chain_next_pc = PyLong_AsUnsignedLong(fields[9]);
  const auto chain_stack_words = PyLong_AsUnsignedLong(fields[10]);
  const auto promote_on_hit = PyLong_AsUnsignedLong(fields[11]);
  const bool conversion_failed = PyErr_Occurred() != nullptr;
  if (!conversion_failed && head_pc <= UINT32_MAX && byte_span <= UINT32_MAX &&
      result_words <= UINT32_MAX && has_return_value <= 1 && stack_words <= UINT32_MAX &&
      frame_depth <= UINT32_MAX && next_pc <= UINT32_MAX && loops_to <= UINT32_MAX &&
      chain_next_pc <= UINT32_MAX && chain_stack_words <= UINT32_MAX &&
      promote_on_hit <= 1 && entry_address != 0) {
    output = native_trace_descriptor{
        static_cast<std::uint32_t>(head_pc), static_cast<std::uintptr_t>(entry_address),
        static_cast<std::uint32_t>(byte_span), static_cast<std::uint32_t>(result_words),
        static_cast<std::uint32_t>(has_return_value), static_cast<std::uint32_t>(stack_words),
        static_cast<std::uint32_t>(frame_depth), static_cast<std::uint32_t>(next_pc),
        static_cast<std::uint32_t>(loops_to), static_cast<std::uint32_t>(chain_next_pc),
        static_cast<std::uint32_t>(chain_stack_words),
        static_cast<std::uint32_t>(promote_on_hit)};
  } else if (!conversion_failed) {
    PyErr_SetString(PyExc_ValueError, "native trace entry field is outside its ABI range");
  }
  Py_DECREF(sequence);
  return !conversion_failed && !PyErr_Occurred();
}

bool is_control_terminator(std::uint8_t opcode) {
  return opcode == 0x02 || opcode == 0x03 || opcode == 0x04 || opcode == 0x05 ||
         opcode == 0x0B || opcode == 0x0C || opcode == 0x0D || opcode == 0x0E ||
         opcode == 0x0F;
}

const native_trace_descriptor* find_dispatch_entry(
    const std::array<native_trace_descriptor, FB_CONF_NATIVE_JIT_TRACE_CAPACITY>& entries,
    std::size_t count, std::uint32_t pc) {
  std::size_t low = 0;
  std::size_t high = count;
  while (low < high) {
    const auto middle = low + (high - low) / 2;
    if (entries[middle].head_pc < pc) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  return low < count && entries[low].head_pc == pc ? &entries[low] : nullptr;
}

std::size_t find_trackable_block_index(
    const std::array<std::uint32_t, FB_CONF_NATIVE_JIT_BLOCK_CAPACITY>& block_heads,
    std::size_t count, std::uint32_t pc) {
  std::size_t low = 0;
  std::size_t high = count;
  while (low < high) {
    const auto middle = low + (high - low) / 2;
    if (block_heads[middle] < pc) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  return low < count && block_heads[low] == pc ? low : count;
}

template <bool CollectStats>
const native_trace_descriptor* terminal_dispatch_entry(
    const std::array<native_trace_descriptor, FB_CONF_NATIVE_JIT_TRACE_CAPACITY>& entries,
    std::size_t count, const native_trace_descriptor* start, std::uint32_t& body_count) {
  auto* current = start;
  for (std::size_t depth = 0; depth < count; ++depth) {
    if constexpr (CollectStats) ++body_count;
    if (current->chain_next_pc == kNoPc) return current;
    const auto* next = find_dispatch_entry(entries, count, current->chain_next_pc);
    if (next == nullptr || next->head_pc == current->head_pc) return nullptr;
    current = next;
  }
  return nullptr;
}

template <bool CollectStats, bool CollectHotspots>
PyObject* run_native_dispatch_impl(PyObject*, PyObject* args) {
  PyObject* code_object = nullptr;
  PyObject* context_object = nullptr;
  PyObject* stack_object = nullptr;
  PyObject* locals_object = nullptr;
  PyObject* control_object = nullptr;
  PyObject* entries_object = nullptr;
  PyObject* trackable_blocks_object = nullptr;
  unsigned int stack_size = 0;
  unsigned int stack_capacity = 0;
  unsigned int initial_ip = 0;
  unsigned int local_base = 0;
  unsigned int local_slots = 0;
  unsigned int control_base = 0;
  unsigned int function_index = 0;
  unsigned int yield_threshold = 0;
  unsigned int execution_count = 0;
  int collect_stats_arg = 0;
  int collect_hotspots_arg = 0;
  if (!PyArg_ParseTuple(args, "OOOOOOOIIIIIIIIIpp", &code_object, &context_object, &stack_object,
                        &locals_object, &control_object, &entries_object,
                        &trackable_blocks_object, &stack_size,
                        &stack_capacity, &initial_ip, &local_base, &local_slots, &control_base,
                        &function_index, &yield_threshold, &execution_count,
                        &collect_stats_arg, &collect_hotspots_arg)) {
    return nullptr;
  }
  if ((collect_stats_arg != 0) != CollectStats ||
      (collect_hotspots_arg != 0) != CollectHotspots) {
    PyErr_SetString(PyExc_RuntimeError, "native dispatcher feature specialization mismatch");
    return nullptr;
  }
  if (yield_threshold == 0) {
    PyErr_SetString(PyExc_ValueError, "native JIT dispatch yield threshold must be positive");
    return nullptr;
  }

  buffer_guard code_buffer;
  if (PyObject_GetBuffer(code_object, &code_buffer.view, PyBUF_SIMPLE) != 0) return nullptr;
  code_buffer.active = true;
  buffer_guard context_buffer;
  if (PyObject_GetBuffer(context_object, &context_buffer.view, PyBUF_SIMPLE) != 0) return nullptr;
  context_buffer.active = true;
  buffer_guard stack_buffer;
  if (PyObject_GetBuffer(stack_object, &stack_buffer.view, PyBUF_SIMPLE) != 0) return nullptr;
  stack_buffer.active = true;
  buffer_guard locals_buffer;
  if (PyObject_GetBuffer(locals_object, &locals_buffer.view, PyBUF_SIMPLE) != 0) return nullptr;
  locals_buffer.active = true;
  buffer_guard control_buffer;
  if (PyObject_GetBuffer(control_object, &control_buffer.view, PyBUF_SIMPLE) != 0) return nullptr;
  control_buffer.active = true;

  const auto context_bytes = static_cast<Py_ssize_t>(sizeof(fireball_execution_context_native));
  const auto stack_bytes = static_cast<Py_ssize_t>(128 * sizeof(std::uint32_t));
  const auto local_bytes = static_cast<Py_ssize_t>((local_base + local_slots) * sizeof(std::uint32_t));
  const auto control_bytes = static_cast<Py_ssize_t>(sizeof(fireball_control_stack_native));
  if (stack_capacity > 128 || stack_size > stack_capacity ||
      context_buffer.view.len < context_bytes || stack_buffer.view.len < stack_bytes ||
      locals_buffer.view.len < local_bytes || control_buffer.view.len < control_bytes ||
      initial_ip > static_cast<unsigned int>(code_buffer.view.len) || function_index > 0xFFFFu) {
    PyErr_SetString(PyExc_ValueError, "invalid native JIT dispatch buffer or execution state");
    return nullptr;
  }

  // Only the prefix below entry_count is initialized and read by dispatch.
  std::array<native_trace_descriptor, FB_CONF_NATIVE_JIT_TRACE_CAPACITY> entries;
  PyObject* sequence = PySequence_Fast(entries_object, "native trace table must be a sequence");
  if (sequence == nullptr) return nullptr;
  const auto entry_count = PySequence_Fast_GET_SIZE(sequence);
  if (entry_count < 0 ||
      static_cast<std::size_t>(entry_count) > FB_CONF_NATIVE_JIT_TRACE_CAPACITY) {
    PyErr_SetString(PyExc_ValueError, "native trace table exceeds its build-time capacity");
    Py_DECREF(sequence);
    return nullptr;
  }
  auto** raw_entries = PySequence_Fast_ITEMS(sequence);
  bool entries_valid = true;
  for (Py_ssize_t index = 0; index < entry_count; ++index) {
    if (!read_dispatch_entry(raw_entries[index], entries[static_cast<std::size_t>(index)])) {
      entries_valid = false;
      break;
    }
    const auto& current = entries[static_cast<std::size_t>(index)];
    if ((current.head_pc >> 16) != function_index ||
        (index > 0 && entries[static_cast<std::size_t>(index - 1)].head_pc >= current.head_pc) ||
        current.byte_span == 0 || current.result_words == 0 || current.stack_words == 0 ||
        current.frame_depth > FIREBALL_NATIVE_CONTROL_STACK_CAPACITY) {
      PyErr_SetString(PyExc_ValueError, "native trace table is unsorted or has invalid metadata");
      entries_valid = false;
      break;
    }
  }
  Py_DECREF(sequence);
  if (!entries_valid) return nullptr;

  // Only the prefix below trackable_count is initialized and read by dispatch.
  std::array<std::uint32_t, FB_CONF_NATIVE_JIT_BLOCK_CAPACITY> trackable_blocks;
  PyObject* trackable_sequence =
      PySequence_Fast(trackable_blocks_object, "trackable block table must be a sequence");
  if (trackable_sequence == nullptr) return nullptr;
  const auto trackable_count = PySequence_Fast_GET_SIZE(trackable_sequence);
  if (trackable_count < 0 ||
      static_cast<std::size_t>(trackable_count) > FB_CONF_NATIVE_JIT_BLOCK_CAPACITY) {
    PyErr_SetString(PyExc_ValueError, "trackable block table exceeds its build-time capacity");
    Py_DECREF(trackable_sequence);
    return nullptr;
  }
  auto** raw_trackable_blocks = PySequence_Fast_ITEMS(trackable_sequence);
  bool trackable_blocks_valid = true;
  for (Py_ssize_t index = 0; index < trackable_count; ++index) {
    const auto pc = PyLong_AsUnsignedLong(raw_trackable_blocks[index]);
    if (PyErr_Occurred() != nullptr || pc > UINT32_MAX || (pc >> 16) != function_index ||
        (index > 0 && trackable_blocks[static_cast<std::size_t>(index - 1)] >= pc)) {
      if (PyErr_Occurred() == nullptr) {
        PyErr_SetString(PyExc_ValueError, "trackable block table is unsorted or invalid");
      }
      trackable_blocks_valid = false;
      break;
    }
    trackable_blocks[static_cast<std::size_t>(index)] = static_cast<std::uint32_t>(pc);
  }
  Py_DECREF(trackable_sequence);
  if (!trackable_blocks_valid) return nullptr;

  auto* execution_context =
      static_cast<fireball_execution_context_native*>(context_buffer.view.buf);
  auto* control_stack = static_cast<fireball_control_stack_native*>(control_buffer.view.buf);
  auto* stack = static_cast<std::uint32_t*>(stack_buffer.view.buf);
  auto* local_stack = static_cast<std::uint32_t*>(locals_buffer.view.buf);
  auto* locals = local_stack + local_base;
  const auto* code = static_cast<const std::uint8_t*>(code_buffer.view.buf);
  const auto code_size = static_cast<std::uint32_t>(code_buffer.view.len);
  if (control_stack->size > FIREBALL_NATIVE_CONTROL_STACK_CAPACITY ||
      control_base > control_stack->size || execution_context->call_stack == nullptr ||
      execution_context->call_stack->size == 0 ||
      execution_context->call_stack->size > FIREBALL_NATIVE_CALL_STACK_CAPACITY ||
      execution_context->call_stack->frames[execution_context->call_stack->size - 1].func_index !=
          function_index) {
    PyErr_SetString(PyExc_ValueError, "invalid native JIT dispatch call or control stack");
    return nullptr;
  }
  auto& call_frame =
      execution_context->call_stack->frames[execution_context->call_stack->size - 1];
  execution_context->sp_capacity = stack_capacity;
  execution_context->ip = initial_ip;
  execution_context->code = code;
  execution_context->code_size = code_size;
  execution_context->control_stack = control_stack;
  execution_context->control_base = control_base;
  execution_context->sp_offset = stack_size;
  execution_context->cf_offset = control_stack->size;
  execution_context->stack_checkpoint = stack_size;
  execution_context->loop_jump_threshold = yield_threshold;

  auto current_pc = (function_index << 16) | initial_ip;
  std::uint32_t trace_count = 0;
  std::uint32_t status = kFallback;
  std::uint32_t trap_code = 0;
  std::uint32_t body_count = 0;
  std::uint32_t dispatcher_trace_transitions = 0;
  std::uint32_t control_handler_count = 0;
  std::uint32_t eligible_block_visits = 0;
  std::uint32_t interpreted_block_count = 0;
  bool control_handler_pending_trace = false;
  std::array<std::uint8_t,
             CollectHotspots ? FB_CONF_NATIVE_JIT_BLOCK_CAPACITY : 0>
      observed_visit_counts{};
  while (true) {
    const auto* start = find_dispatch_entry(entries, static_cast<std::size_t>(entry_count), current_pc);
    if (start != nullptr && start->promote_on_hit != 0) {
      if constexpr (CollectStats) {
        if (control_handler_pending_trace) ++dispatcher_trace_transitions;
      }
      execution_context->ip = current_pc & 0xFFFFu;
      status = kOldestTraceHit;
      break;
    }
    if (start == nullptr || stack_size + start->chain_stack_words > stack_capacity) {
      if constexpr (CollectStats) ++interpreted_block_count;
      const auto interpreter_ip = current_pc & 0xFFFFu;
      bool hotness_yield = false;
      if constexpr (CollectHotspots) {
        const auto block_index = find_trackable_block_index(
            trackable_blocks, static_cast<std::size_t>(trackable_count), current_pc);
        if (block_index < static_cast<std::size_t>(trackable_count)) {
          ++eligible_block_visits;
          auto& visit_count = observed_visit_counts[block_index];
          if (visit_count < 2) ++visit_count;
          hotness_yield = execution_count + eligible_block_visits >= yield_threshold;
        }
      }
      call_frame.boundary_next_pc = kNoPc;
      call_frame.boundary_loops_to = kNoPc;
      execution_context->ip = interpreter_ip;
      execution_context->sp_offset = stack_size;
      execution_context->stack_checkpoint = stack_size;
      const auto previous_flags = execution_context->runtime_flags;
      execution_context->runtime_flags =
          (previous_flags & ~kStopAtBlockBoundaryFlag) | kStopAfterControlFlag;
      const auto result = dispatch(execution_context, stack, local_stack,
                                   top_value(*execution_context, stack));
      execution_context->runtime_flags = previous_flags;
      if (result.kind == kFallback) {
        execution_context->sp_offset = execution_context->stack_checkpoint;
        execution_context->ip = result.next_ip;
        stack_size = execution_context->sp_offset;
        status = kFallback;
        break;
      }
      if (result.kind == kTrap) {
        trap_code = result.trap_code;
        stack_size = execution_context->sp_offset;
        status = kTrap;
        break;
      }
      stack_size = execution_context->sp_offset;
      if (result.kind == kComplete || result.next_ip == kNoPc ||
          result.next_ip >= code_size) {
        execution_context->ip = kNoPc;
        status = kComplete;
        break;
      }
      if (result.kind != kBlockBoundary) {
        PyErr_SetString(PyExc_RuntimeError, "native interpreter returned an invalid status");
        return nullptr;
      }
      if constexpr (CollectStats) {
        ++control_handler_count;
        control_handler_pending_trace = true;
      }
      execution_context->ip = result.next_ip;
      stack_size = execution_context->sp_offset;
      const auto next_pc = (function_index << 16) | result.next_ip;
      if (execution_context->loop_jump_count >= yield_threshold) {
        current_pc = next_pc;
        status = kDispatchYield;
        break;
      }
      current_pc = next_pc;
      if constexpr (CollectHotspots) {
        if (hotness_yield) {
          status = kDispatchYield;
          break;
        }
      }
      continue;
    }
    std::uint32_t chain_body_count = 0;
    const auto* terminal = terminal_dispatch_entry<CollectStats>(
        entries, static_cast<std::size_t>(entry_count), start, chain_body_count);
    if (terminal == nullptr) {
      PyErr_SetString(PyExc_RuntimeError, "native trace chain target is absent from its snapshot");
      return nullptr;
    }
    if (stack_size + terminal->stack_words > stack_capacity ||
        terminal->frame_depth > FIREBALL_NATIVE_CONTROL_STACK_CAPACITY - control_base) {
      execution_context->ip = start->head_pc & 0xFFFFu;
      status = kFallback;
      break;
    }

    if constexpr (CollectStats) {
      if (control_handler_pending_trace) {
        ++dispatcher_trace_transitions;
        control_handler_pending_trace = false;
      }
    }

    const auto expected_frames = control_base + terminal->frame_depth;
    if (control_stack->size > expected_frames) control_stack->size = expected_frames;
    if (control_stack->size < control_base) {
      PyErr_SetString(PyExc_ValueError, "native JIT trace depth is below its control base");
      return nullptr;
    }
    call_frame.boundary_next_pc = terminal->next_pc;
    call_frame.boundary_loops_to = terminal->loops_to;
    const auto trace_entry = reinterpret_cast<native_trace_entry_fn>(start->entry_address);
    trace_entry(execution_context, stack + stack_size, locals, 0);
    if constexpr (CollectStats) {
      ++trace_count;
      body_count += chain_body_count;
    }
    if (terminal->has_return_value != 0) stack_size += terminal->result_words;
    execution_context->sp_offset = stack_size;

    const auto terminal_ip = (terminal->head_pc & 0xFFFFu) + terminal->byte_span;
    if (terminal_ip >= code_size) {
      execution_context->ip = kNoPc;
      status = kComplete;
      break;
    }
    const auto opcode = code[terminal_ip];
    if (!is_control_terminator(opcode)) {
      execution_context->ip = terminal_ip;
      status = kFallback;
      break;
    }

    execution_context->ip = terminal_ip;
    execution_context->stack_checkpoint = stack_size;
    const auto previous_flags = execution_context->runtime_flags;
    execution_context->runtime_flags |= kStopAfterControlFlag;
    const auto handler = handler_table_data()[opcode];
    if constexpr (CollectStats) ++control_handler_count;
    const auto result = handler == nullptr
                            ? fallback(terminal_ip)
                            : handler(execution_context, stack, local_stack,
                                      top_value(*execution_context, stack));
    execution_context->runtime_flags = previous_flags;
    if (result.kind == kFallback) {
      execution_context->sp_offset = execution_context->stack_checkpoint;
      execution_context->ip = result.next_ip;
      status = kFallback;
      break;
    }
    if (result.kind == kTrap) {
      trap_code = result.trap_code;
      status = kTrap;
      break;
    }
    if (result.kind == kComplete || result.next_ip == kNoPc || result.next_ip >= code_size) {
      execution_context->ip = kNoPc;
      status = kComplete;
      break;
    }
    if (result.kind != kBlockBoundary) {
      PyErr_SetString(PyExc_RuntimeError, "native control handler returned an invalid status");
      return nullptr;
    }
    if constexpr (CollectStats) control_handler_pending_trace = true;
    execution_context->ip = result.next_ip;
    stack_size = execution_context->sp_offset;
    const auto next_pc = (function_index << 16) | result.next_ip;
    if (execution_context->loop_jump_count >= yield_threshold) {
      current_pc = next_pc;
      status = kDispatchYield;
      break;
    }
    current_pc = next_pc;
  }
  Py_ssize_t observed_count = 0;
  if constexpr (CollectHotspots) {
    for (Py_ssize_t index = 0; index < trackable_count; ++index) {
      if (observed_visit_counts[static_cast<std::size_t>(index)] != 0) ++observed_count;
    }
  }
  PyObject* visits = PyTuple_New(observed_count);
  if (visits == nullptr) return nullptr;
  Py_ssize_t visit_index = 0;
  if constexpr (CollectHotspots) {
    for (Py_ssize_t index = 0; index < trackable_count; ++index) {
      const auto count = observed_visit_counts[static_cast<std::size_t>(index)];
      if (count == 0) continue;
      PyObject* visit = Py_BuildValue("II", trackable_blocks[static_cast<std::size_t>(index)], count);
      if (visit == nullptr) {
        Py_DECREF(visits);
        return nullptr;
      }
      PyTuple_SET_ITEM(visits, visit_index++, visit);
    }
  }
  if (visit_index != observed_count) {
    Py_DECREF(visits);
    PyErr_SetString(PyExc_RuntimeError, "native block observation count is inconsistent");
    return nullptr;
  }
  PyObject* result = Py_BuildValue(
      "IIIIIIIIIIO", status, execution_context->ip, stack_size, trap_code, trace_count,
      body_count, dispatcher_trace_transitions, control_handler_count, eligible_block_visits,
      interpreted_block_count, visits);
  Py_DECREF(visits);
  return result;
}

PyObject* run_native_dispatch(PyObject* self, PyObject* args) {
  if (!PyTuple_Check(args) || PyTuple_GET_SIZE(args) != 18) {
    PyErr_SetString(PyExc_TypeError, "native dispatcher expects 18 arguments");
    return nullptr;
  }
  const auto collect_stats = PyObject_IsTrue(PyTuple_GET_ITEM(args, 16));
  if (collect_stats < 0) return nullptr;
  const auto collect_hotspots = PyObject_IsTrue(PyTuple_GET_ITEM(args, 17));
  if (collect_hotspots < 0) return nullptr;

#if FB_CONF_RUNTIME_PROFILE_STATS == 0
  if (collect_stats != 0) {
    PyErr_SetString(PyExc_RuntimeError,
                    "runtime profile stats were compiled out by configuration");
    return nullptr;
  }
#endif
#if FB_CONF_JIT_HOTSPOT_PROFILING == 0
  if (collect_hotspots != 0) {
    PyErr_SetString(PyExc_RuntimeError,
                    "JIT hotspot profiling was compiled out by configuration");
    return nullptr;
  }
#endif

#if FB_CONF_RUNTIME_PROFILE_STATS == 1
  if (collect_stats != 0) {
#if FB_CONF_JIT_HOTSPOT_PROFILING == 1
    return collect_hotspots != 0 ? run_native_dispatch_impl<true, true>(self, args)
                                 : run_native_dispatch_impl<true, false>(self, args);
#else
    return run_native_dispatch_impl<true, false>(self, args);
#endif
  }
#endif
#if FB_CONF_JIT_HOTSPOT_PROFILING == 1
  return collect_hotspots != 0 ? run_native_dispatch_impl<false, true>(self, args)
                               : run_native_dispatch_impl<false, false>(self, args);
#else
  return run_native_dispatch_impl<false, false>(self, args);
#endif
}

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

PyObject* run_native_step(PyObject* args, bool direct_control) {
  PyObject* code_object = nullptr;
  PyObject* context_object = nullptr;
  PyObject* stack_object = nullptr;
  PyObject* locals_object = nullptr;
  PyObject* control_object = nullptr;
  unsigned int stack_size = 0;
  unsigned int stack_capacity = 0;
  unsigned int ip = 0;
  unsigned int local_slots = 0;
  unsigned int control_base = 0;
  if (!PyArg_ParseTuple(args, "OOOOOIIIII", &code_object, &context_object, &stack_object,
                        &locals_object, &control_object, &stack_size, &stack_capacity, &ip,
                        &local_slots, &control_base)) {
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
  if (stack_capacity > 128 || stack_size > stack_capacity ||
      context_buffer.view.len < context_bytes ||
      stack_buffer.view.len < stack_bytes || control_buffer.view.len < control_bytes ||
      locals_buffer.view.len < local_bytes ||
      ip > static_cast<unsigned int>(code_size)) {
    PyErr_Format(PyExc_ValueError,
                 "invalid native interpreter buffer (stack %u/%u, context %zd/%zd, "
                 "values %zd/%zd, control %zd/%zd, locals %zd/%zd, ip %u/%zd)",
                 stack_size, stack_capacity, context_buffer.view.len, context_bytes,
                 stack_buffer.view.len, stack_bytes, control_buffer.view.len, control_bytes,
                 locals_buffer.view.len, local_bytes, ip, code_size);
    return nullptr;
  }

  auto* execution_context =
      static_cast<fireball_execution_context_native*>(context_buffer.view.buf);
  auto* control_stack = static_cast<fireball_control_stack_native*>(control_buffer.view.buf);
  execution_context->sp_capacity = stack_capacity;
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
  step_result result{};
  if (direct_control) {
    if (ip >= static_cast<unsigned int>(code_size)) {
      PyErr_SetString(PyExc_ValueError, "native control step requires a bytecode opcode");
      return nullptr;
    }
    const auto opcode = raw_code[ip];
    switch (opcode) {
      case 0x02:
      case 0x03:
      case 0x04:
      case 0x05:
      case 0x0B:
      case 0x0C:
      case 0x0D:
      case 0x0E:
      case 0x0F:
        break;
      default:
        PyErr_SetString(PyExc_ValueError, "opcode is not a native control terminator");
        return nullptr;
    }
    const auto previous_flags = execution_context->runtime_flags;
    execution_context->runtime_flags |= kStopAfterControlFlag;
    const auto handler = handler_table_data()[opcode];
    result = handler == nullptr
                 ? fallback(ip)
                 : handler(execution_context, stack, locals,
                           top_value(*execution_context, stack));
    execution_context->runtime_flags = previous_flags;
  } else {
    result = dispatch(execution_context, stack, locals,
                      top_value(*execution_context, stack));
  }
  if (result.kind == kFallback) {
    execution_context->sp_offset = execution_context->stack_checkpoint;
    execution_context->ip = result.next_ip;
    return Py_BuildValue("IIII", kFallback, result.next_ip, execution_context->sp_offset, 0);
  }
  if (result.kind == kBlockBoundary) {
    execution_context->ip = result.next_ip;
    return Py_BuildValue("IIII", kBlockBoundary, result.next_ip,
                         execution_context->sp_offset, 0);
  }
  if (result.kind == kTrap) {
    return Py_BuildValue("IIII", kTrap, execution_context->ip, execution_context->sp_offset,
                         result.trap_code);
  }
  return Py_BuildValue("IIII", kComplete, kSentinel, execution_context->sp_offset, 0);
}

PyObject* run_step(PyObject*, PyObject* args) { return run_native_step(args, false); }

PyObject* run_control_step(PyObject*, PyObject* args) {
  return run_native_step(args, true);
}

PyMethodDef module_methods[] = {
    {"run_step", run_step, METH_VARARGS, "Run the native CPS handler table until a boundary."},
    {"run_control_step", run_control_step, METH_VARARGS,
     "Run one structured-control opcode through its native handler."},
    {"run_native_dispatch", run_native_dispatch, METH_VARARGS,
     "Run native JIT traces and C++ interpreter handlers until a yield or exit."},
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

PyMODINIT_FUNC PyInit__interpreter_native() {
  PyObject* module = PyModule_Create(&module_definition);
  if (module == nullptr) return nullptr;
  if (PyModule_AddIntConstant(module, "RUNTIME_PROFILE_STATS_ENABLED",
                              FB_CONF_RUNTIME_PROFILE_STATS) < 0 ||
      PyModule_AddIntConstant(module, "JIT_HOTSPOT_PROFILING_ENABLED",
                              FB_CONF_JIT_HOTSPOT_PROFILING) < 0) {
    Py_DECREF(module);
    return nullptr;
  }
  return module;
}

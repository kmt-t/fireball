/** @file @brief JIT専用Runtime APIへのABI変換とopcode対応表を実装する。 */
#include "jit_runtime_apis.hxx"
#include "../../tier2_runtime/interpreter/native_wasm_operations.hxx"
#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#include <type_traits>

// Tier 3 JIT runtime-call ABI and opcode-to-JIT-API catalog.
namespace fireball::jit_api {
using namespace fireball::wasm_runtime;

using jit_runtime_api_pointer = bool (*)(execution_context *, std::uint32_t *&, std::uint32_t *&,
                                         const std::uint8_t *);

inline bool jit_runtime_unreachable(execution_context *context, [[maybe_unused]] std::uint32_t *&sp,
                                    [[maybe_unused]] std::uint32_t *&local_base,
                                    [[maybe_unused]] const std::uint8_t *rel) {
  return trap(*context, kTrapUnreachable);
}

/**
 * @brief コンパイラが配置した固定幅即値を、整列に依存せず読み出す。
 * @tparam Value 即値レコードに格納された値の型。
 * @tparam Index Value単位の固定位置。
 * @pre immediate_recordは対象値を含む有効な定数領域である。
 */
template <class Value, std::size_t Index>
  requires(std::is_trivially_copyable_v<Value>)
inline Value load_jit_immediate(const std::uint8_t *immediate_record) {
  Value value;
  std::memcpy(&value, immediate_record + Index * sizeof(Value), sizeof(Value));
  return value;
}

template <class Value>
  requires(std::is_trivially_copyable_v<Value>)
inline Value load_jit_value(const std::uint8_t *bytes) {
  Value value;
  std::memcpy(&value, bytes, sizeof(Value));
  return value;
}

template <auto RuntimeApi>
inline bool jit_runtime_api_passthrough(execution_context *context, std::uint32_t *&sp,
                                        std::uint32_t *&local_base, const std::uint8_t *rel) {
  assert(rel != nullptr);
  const std::uint8_t *instruction_ip = context->code + context->ip;
  auto *cursor = rel;
  const auto completed = RuntimeApi(context, instruction_ip, sp, local_base, cursor);
  if (!cps_exit_pending(*context)) {
    context->ip = instruction_offset(*context, instruction_ip);
  }
  return completed;
}

template <auto RuntimeApi>
inline bool jit_runtime_api_annotated(execution_context *context, std::uint32_t *&sp,
                                      std::uint32_t *&local_base,
                                      const std::uint8_t *annotation_offset_record) {
  assert(annotation_offset_record != nullptr);
  auto &frame = active_frame(*context);
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  const auto annotation_offset = load_jit_value<annotation_offset_t>(annotation_offset_record);
  assert(annotation_offset <= function.interpreter_annotation_byte_count);
  assert(function.interpreter_annotation_pool != nullptr ||
         function.interpreter_annotation_byte_count == 0);
  const auto *annotation = function.interpreter_annotation_pool == nullptr
                               ? nullptr
                               : function.interpreter_annotation_pool + annotation_offset;
  const std::uint8_t *instruction_ip = context->code + context->ip;
  auto *cursor = annotation;
  const auto completed = RuntimeApi(context, instruction_ip, sp, local_base, cursor);
  if (!cps_exit_pending(*context)) {
    context->ip = instruction_offset(*context, instruction_ip);
  }
  return completed;
}

template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
inline bool jit_runtime_memory_load(execution_context *context, std::uint32_t *&sp,
                                    std::uint32_t *&local_base, const std::uint8_t *rel) {
  assert(rel != nullptr);
  const auto source_ip = context->ip;
  const auto *instruction_ip = context->code + source_ip;
  const auto instruction_width = load_jit_value<std::uint8_t>(rel);
  const auto offset = load_jit_value<std::uint32_t>(rel + sizeof(instruction_width));
  const auto completed = runtime_memory_load_decoded<Width, ResultWords, Signed>(
      context, sp, local_base, instruction_ip, source_ip, source_ip + instruction_width, offset);
  if (!cps_exit_pending(*context)) {
    context->ip = instruction_offset(*context, instruction_ip);
  }
  return completed;
}

template <std::uint32_t Width, std::uint32_t ValueWords>
inline bool jit_runtime_memory_store(execution_context *context, std::uint32_t *&sp,
                                     std::uint32_t *&local_base, const std::uint8_t *rel) {
  assert(rel != nullptr);
  const auto source_ip = context->ip;
  const auto *instruction_ip = context->code + source_ip;
  const auto instruction_width = load_jit_value<std::uint8_t>(rel);
  const auto offset = load_jit_value<std::uint32_t>(rel + sizeof(instruction_width));
  const auto completed = runtime_memory_store_decoded<Width, ValueWords>(
      context, sp, local_base, instruction_ip, source_ip, source_ip + instruction_width, offset);
  if (!cps_exit_pending(*context)) {
    context->ip = instruction_offset(*context, instruction_ip);
  }
  return completed;
}

template <bool (*Operation)(execution_context &, std::uint32_t *&)>
inline bool jit_runtime_fc_saturating_conversion(execution_context *context, std::uint32_t *&sp,
                                                 [[maybe_unused]] std::uint32_t *&local_base,
                                                 [[maybe_unused]] const std::uint8_t *rel) {
  return runtime_fc_saturating_conversion_decoded<Operation>(context, sp);
}

inline bool jit_runtime_fc_memory_copy(execution_context *context, std::uint32_t *&sp,
                                       [[maybe_unused]] std::uint32_t *&local_base,
                                       const std::uint8_t *rel) {
  assert(rel != nullptr);
  const auto source_ip = context->ip;
  const auto *instruction_ip = context->code + source_ip;
  const auto instruction_width = load_jit_value<std::uint8_t>(rel);
  return runtime_fc_memory_copy_decoded(context, sp, instruction_ip, source_ip,
                                        source_ip + instruction_width);
}

inline bool jit_runtime_fc_memory_fill(execution_context *context, std::uint32_t *&sp,
                                       [[maybe_unused]] std::uint32_t *&local_base,
                                       const std::uint8_t *rel) {
  assert(rel != nullptr);
  const auto source_ip = context->ip;
  const auto *instruction_ip = context->code + source_ip;
  const auto instruction_width = load_jit_value<std::uint8_t>(rel);
  return runtime_fc_memory_fill_decoded(context, sp, instruction_ip, source_ip,
                                        source_ip + instruction_width);
}

constexpr auto make_opcode_jit_runtime_api_table() {
  std::array<jit_runtime_api_pointer, 256> apis{};
  apis[0x00] = &jit_runtime_unreachable;
  apis[0x01] = &jit_runtime_api_passthrough<&runtime_nop>;
  apis[0x02] = &jit_runtime_api_annotated<&runtime_block>;
  apis[0x03] = &jit_runtime_api_annotated<&runtime_block>;
  apis[0x04] = &jit_runtime_api_annotated<&runtime_if>;
  apis[0x05] = &jit_runtime_api_annotated<&runtime_else>;
  apis[0x0B] = &jit_runtime_api_annotated<&runtime_end>;
  apis[0x0C] = &jit_runtime_api_annotated<&runtime_br>;
  apis[0x0D] = &jit_runtime_api_annotated<&runtime_br_if>;
  apis[0x0E] = &jit_runtime_api_annotated<&runtime_br_table>;
  apis[0x0F] = &jit_runtime_api_passthrough<&runtime_return>;
  apis[0x10] = &jit_runtime_api_annotated<&runtime_call<false>>;
  apis[0x11] = &jit_runtime_api_annotated<&runtime_call<true>>;
  apis[0x1A] = &jit_runtime_api_passthrough<&runtime_drop>;
  apis[0x1B] = &jit_runtime_api_passthrough<&runtime_select>;
  apis[0x23] = &jit_runtime_api_passthrough<&runtime_global_get>;
  apis[0x24] = &jit_runtime_api_passthrough<&runtime_global_set>;
  apis[0x28] = &jit_runtime_memory_load<4, 1, false>;
  apis[0x29] = &jit_runtime_memory_load<8, 2, false>;
  apis[0x2A] = &jit_runtime_memory_load<4, 1, false>;
  apis[0x2B] = &jit_runtime_memory_load<8, 2, false>;
  apis[0x2C] = &jit_runtime_memory_load<1, 1, true>;
  apis[0x2D] = &jit_runtime_memory_load<1, 1, false>;
  apis[0x2E] = &jit_runtime_memory_load<2, 1, true>;
  apis[0x2F] = &jit_runtime_memory_load<2, 1, false>;
  apis[0x30] = &jit_runtime_memory_load<1, 2, true>;
  apis[0x31] = &jit_runtime_memory_load<1, 2, false>;
  apis[0x32] = &jit_runtime_memory_load<2, 2, true>;
  apis[0x33] = &jit_runtime_memory_load<2, 2, false>;
  apis[0x34] = &jit_runtime_memory_load<4, 2, true>;
  apis[0x35] = &jit_runtime_memory_load<4, 2, false>;
  apis[0x36] = &jit_runtime_memory_store<4, 1>;
  apis[0x37] = &jit_runtime_memory_store<8, 2>;
  apis[0x38] = &jit_runtime_memory_store<4, 1>;
  apis[0x39] = &jit_runtime_memory_store<8, 2>;
  apis[0x3A] = &jit_runtime_memory_store<1, 1>;
  apis[0x3B] = &jit_runtime_memory_store<2, 1>;
  apis[0x3C] = &jit_runtime_memory_store<1, 2>;
  apis[0x3D] = &jit_runtime_memory_store<2, 2>;
  apis[0x3E] = &jit_runtime_memory_store<4, 2>;
  apis[0x3F] = &jit_runtime_api_passthrough<&runtime_memory_manager_api_boundary>;
  apis[0x40] = &jit_runtime_api_passthrough<&runtime_memory_manager_api_boundary>;
  apis[0x41] = &jit_runtime_api_passthrough<&runtime_i32_const>;
  apis[0x42] = &jit_runtime_api_passthrough<&runtime_i64_const>;
  apis[0x43] = &jit_runtime_api_passthrough<&runtime_f32_const>;
  apis[0x44] = &jit_runtime_api_passthrough<&runtime_f64_const>;
  apis[0x45] = &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_eqz<std::uint32_t>>>;
  apis[0x46] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::equal_to<>{}>>;
  apis[0x47] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::not_equal_to<>{}>>;
  apis[0x48] = &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_lt<std::uint32_t>>>;
  apis[0x49] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::less<>{}>>;
  apis[0x4A] = &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_gt<std::uint32_t>>>;
  apis[0x4B] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::greater<>{}>>;
  apis[0x4C] = &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_le<std::uint32_t>>>;
  apis[0x4D] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::less_equal<>{}>>;
  apis[0x4E] = &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_ge<std::uint32_t>>>;
  apis[0x4F] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::greater_equal<>{}>>;
  apis[0x50] = &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_eqz<std::uint64_t>, true>>;
  apis[0x51] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::equal_to<>{}, true>>;
  apis[0x52] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::not_equal_to<>{}, true>>;
  apis[0x53] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_lt<std::uint64_t>, true>>;
  apis[0x54] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::less<>{}, true>>;
  apis[0x55] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_gt<std::uint64_t>, true>>;
  apis[0x56] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::greater<>{}, true>>;
  apis[0x57] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_le<std::uint64_t>, true>>;
  apis[0x58] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::less_equal<>{}, true>>;
  apis[0x59] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_ge<std::uint64_t>, true>>;
  apis[0x5A] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::greater_equal<>{}, true>>;
  apis[0x5B] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::equal_to<>{}, true>>;
  apis[0x5C] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::not_equal_to<>{}, true>>;
  apis[0x5D] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::less<>{}, true>>;
  apis[0x5E] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::greater<>{}, true>>;
  apis[0x5F] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::less_equal<>{}, true>>;
  apis[0x60] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::greater_equal<>{}, true>>;
  apis[0x61] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::equal_to<>{}, true>>;
  apis[0x62] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::not_equal_to<>{}, true>>;
  apis[0x63] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::less<>{}, true>>;
  apis[0x64] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::greater<>{}, true>>;
  apis[0x65] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::less_equal<>{}, true>>;
  apis[0x66] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::greater_equal<>{}, true>>;
  apis[0x67] = &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_clz<std::uint32_t>>>;
  apis[0x68] = &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_ctz<std::uint32_t>>>;
  apis[0x69] = &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_popcount<std::uint32_t>>>;
  apis[0x6A] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::plus<>{}>>;
  apis[0x6B] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::minus<>{}>>;
  apis[0x6C] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::multiplies<>{}>>;
  apis[0x6D] =
      &jit_runtime_api_passthrough<&runtime_i32_checked_binary<&opcode_div_signed<std::uint32_t>>>;
  apis[0x6E] = &jit_runtime_api_passthrough<
      &runtime_i32_checked_binary<&opcode_div_unsigned<std::uint32_t>>>;
  apis[0x6F] =
      &jit_runtime_api_passthrough<&runtime_i32_checked_binary<&opcode_rem_signed<std::uint32_t>>>;
  apis[0x70] = &jit_runtime_api_passthrough<
      &runtime_i32_checked_binary<&opcode_rem_unsigned<std::uint32_t>>>;
  apis[0x71] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::bit_and<>{}>>;
  apis[0x72] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::bit_or<>{}>>;
  apis[0x73] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::bit_xor<>{}>>;
  apis[0x74] = &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_shift_left<std::uint32_t>>>;
  apis[0x75] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_shift_right_signed<std::uint32_t>>>;
  apis[0x76] = &jit_runtime_api_passthrough<
      &runtime_i32_binary<&opcode_shift_right_unsigned<std::uint32_t>>>;
  apis[0x77] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_rotate_left<std::uint32_t>>>;
  apis[0x78] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_rotate_right<std::uint32_t>>>;
  apis[0x79] = &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_clz<std::uint64_t>, false>>;
  apis[0x7A] = &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_ctz<std::uint64_t>, false>>;
  apis[0x7B] =
      &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_popcount<std::uint64_t>, false>>;
  apis[0x7C] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::plus<>{}, false>>;
  apis[0x7D] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::minus<>{}, false>>;
  apis[0x7E] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::multiplies<>{}, false>>;
  apis[0x7F] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_div_signed<std::uint64_t>, false>>;
  apis[0x80] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_div_unsigned<std::uint64_t>, false>>;
  apis[0x81] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_rem_signed<std::uint64_t>, false>>;
  apis[0x82] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_rem_unsigned<std::uint64_t>, false>>;
  apis[0x83] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::bit_and<>{}, false>>;
  apis[0x84] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::bit_or<>{}, false>>;
  apis[0x85] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::bit_xor<>{}, false>>;
  apis[0x86] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_shift_left<std::uint64_t>, false>>;
  apis[0x87] = &jit_runtime_api_passthrough<
      &runtime_i64_binary<&opcode_shift_right_signed<std::uint64_t>, false>>;
  apis[0x88] = &jit_runtime_api_passthrough<
      &runtime_i64_binary<&opcode_shift_right_unsigned<std::uint64_t>, false>>;
  apis[0x89] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_rotate_left<std::uint64_t>, false>>;
  apis[0x8A] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_rotate_right<std::uint64_t>, false>>;
  apis[0x8B] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_abs<float>>>;
  apis[0x8C] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_neg<float>>>;
  apis[0x8D] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_ceil<float>>>;
  apis[0x8E] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_floor<float>>>;
  apis[0x8F] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_trunc<float>>>;
  apis[0x90] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_nearest<float>>>;
  apis[0x91] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_sqrt<float>>>;
  apis[0x92] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::plus<>{}, false>>;
  apis[0x93] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::minus<>{}, false>>;
  apis[0x94] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::multiplies<>{}, false>>;
  apis[0x95] = &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_div<float>, false>>;
  apis[0x96] = &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_min<float>, false>>;
  apis[0x97] = &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_max<float>, false>>;
  apis[0x98] =
      &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_copysign<float>, false>>;
  apis[0x99] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_abs<double>>>;
  apis[0x9A] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_neg<double>>>;
  apis[0x9B] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_ceil<double>>>;
  apis[0x9C] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_floor<double>>>;
  apis[0x9D] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_trunc<double>>>;
  apis[0x9E] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_nearest<double>>>;
  apis[0x9F] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_sqrt<double>>>;
  apis[0xA0] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::plus<>{}, false>>;
  apis[0xA1] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::minus<>{}, false>>;
  apis[0xA2] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::multiplies<>{}, false>>;
  apis[0xA3] = &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_div<double>, false>>;
  apis[0xA4] = &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_min<double>, false>>;
  apis[0xA5] = &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_max<double>, false>>;
  apis[0xA6] =
      &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_copysign<double>, false>>;
  apis[0xA7] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_wrap_i64>>;
  apis[0xA8] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f32_s>>;
  apis[0xA9] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f32_u>>;
  apis[0xAA] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f64_s>>;
  apis[0xAB] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f64_u>>;
  apis[0xAC] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_extend_i32_s>>;
  apis[0xAD] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_extend_i32_u>>;
  apis[0xAE] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f32_s>>;
  apis[0xAF] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f32_u>>;
  apis[0xB0] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f64_s>>;
  apis[0xB1] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f64_u>>;
  apis[0xB2] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i32_s>>;
  apis[0xB3] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i32_u>>;
  apis[0xB4] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i64_s>>;
  apis[0xB5] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i64_u>>;
  apis[0xB6] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_demote_f64>>;
  apis[0xB7] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i32_s>>;
  apis[0xB8] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i32_u>>;
  apis[0xB9] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i64_s>>;
  apis[0xBA] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i64_u>>;
  apis[0xBB] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_promote_f32>>;
  apis[0xBC] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[0xBD] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[0xBE] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[0xBF] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[0xC0] =
      &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_extend8_signed<std::uint32_t>>>;
  apis[0xC1] =
      &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_extend16_signed<std::uint32_t>>>;
  apis[0xC2] = &jit_runtime_api_passthrough<
      &runtime_i64_unary<&opcode_extend8_signed<std::uint64_t>, false>>;
  apis[0xC3] = &jit_runtime_api_passthrough<
      &runtime_i64_unary<&opcode_extend16_signed<std::uint64_t>, false>>;
  apis[0xC4] = &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_extend32_signed, false>>;
  return apis;
}

inline constexpr auto kOpcodeJitRuntimeApis = make_opcode_jit_runtime_api_table();
static_assert(kOpcodeJitRuntimeApis[0x20] == nullptr && kOpcodeJitRuntimeApis[0x21] == nullptr &&
                  kOpcodeJitRuntimeApis[0x22] == nullptr,
              "local operations must be emitted by the JIT encoder");

constexpr auto make_fc_opcode_jit_runtime_api_table() {
  std::array<jit_runtime_api_pointer, 12> apis{};
  apis[0x00] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_s>;
  apis[0x01] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_u>;
  apis[0x02] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_s>;
  apis[0x03] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_u>;
  apis[0x04] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_s>;
  apis[0x05] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_u>;
  apis[0x06] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_s>;
  apis[0x07] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_u>;
  apis[0x0A] = &jit_runtime_fc_memory_copy;
  apis[0x0B] = &jit_runtime_fc_memory_fill;
  return apis;
}

inline constexpr auto kFcOpcodeJitRuntimeApis = make_fc_opcode_jit_runtime_api_table();

constexpr std::size_t registered_jit_runtime_api_count() {
  std::size_t count = 0;
  for (const auto api : kOpcodeJitRuntimeApis) {
    count += api != nullptr ? 1u : 0u;
  }
  for (const auto api : kFcOpcodeJitRuntimeApis) {
    count += api != nullptr ? 1u : 0u;
  }
  return count;
}

constexpr auto kJitRuntimeApiCount = registered_jit_runtime_api_count();
static_assert(kJitRuntimeApiCount <= 0xFF,
              "JIT runtime API table indices must fit the trace constant");

struct jit_runtime_api_slot_tables {
  std::array<std::uint8_t, kOpcodeJitRuntimeApis.size()> opcode{};
  std::array<std::uint8_t, kFcOpcodeJitRuntimeApis.size()> fc_opcode{};
};

constexpr auto make_jit_runtime_api_slot_tables() {
  jit_runtime_api_slot_tables slots{};
  slots.opcode.fill(0xFF);
  slots.fc_opcode.fill(0xFF);
  std::uint16_t next_slot = 0;
  for (std::size_t index = 0; index < slots.opcode.size(); ++index) {
    if (kOpcodeJitRuntimeApis[index] != nullptr) {
      slots.opcode[index] = static_cast<std::uint8_t>(next_slot++);
    }
  }
  for (std::size_t index = 0; index < slots.fc_opcode.size(); ++index) {
    if (kFcOpcodeJitRuntimeApis[index] != nullptr) {
      slots.fc_opcode[index] = static_cast<std::uint8_t>(next_slot++);
    }
  }
  return slots;
}

inline constexpr auto kJitRuntimeApiSlots = make_jit_runtime_api_slot_tables();

constexpr auto make_jit_runtime_api_function_table() {
  std::array<jit_runtime_api_pointer, kJitRuntimeApiCount> functions{};
  std::size_t next = 0;
  for (const auto api : kOpcodeJitRuntimeApis) {
    if (api != nullptr) {
      functions[next++] = api;
    }
  }
  for (const auto api : kFcOpcodeJitRuntimeApis) {
    if (api != nullptr) {
      functions[next++] = api;
    }
  }
  return functions;
}

inline constexpr auto kJitRuntimeApiFunctionTable = make_jit_runtime_api_function_table();
static_assert(kJitRuntimeApiCount == kJitRuntimeApiFunctionTable.size());

/** @brief プラグインの保存領域にある読み取り専用API表の先頭を借用する。 */
const jit_runtime_api_pointer *runtime_api_table_data() {
  return kJitRuntimeApiFunctionTable.data();
}
} // namespace fireball::jit_api

extern "C" int fb_jit_runtime_api_slot(std::uint32_t opcode, std::uint32_t fc_prefix,
                                       std::uint8_t *slot) {
  using namespace fireball::jit_api;
  assert(slot != nullptr);
  if (fc_prefix != 0) {
    if (opcode >= kJitRuntimeApiSlots.fc_opcode.size()) {
      return 0;
    }
    const auto value = kJitRuntimeApiSlots.fc_opcode[opcode];
    if (value == 0xFF) {
      return 0;
    }
    *slot = value;
    return 1;
  }
  if (opcode >= kJitRuntimeApiSlots.opcode.size()) {
    return 0;
  }
  const auto value = kJitRuntimeApiSlots.opcode[opcode];
  if (value == 0xFF) {
    return 0;
  }
  *slot = value;
  return 1;
}

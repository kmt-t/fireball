/** @file @brief JIT専用Runtime APIへのABI変換とopcode対応表を実装する。 */
#include "jit_runtime_apis.hxx"
#include "../../../tier2_runtime/abi/wasm_opcodes.hxx"
#include "../../tier2_runtime/interpreter/native_wasm_operations.hxx"
#include "jit_types.hxx"
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
  return trap(*context, kTrapUnreachable, context->ip);
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

inline void publish_interpreter_rel_after_runtime_boundary(execution_context *context,
                                                           const std::uint8_t *rel) {
  if (!cps_exit_pending(*context) || cps_exit_kind(*context) != kRuntimeApiBoundary) {
    return;
  }
  assert(rel != nullptr);
  const auto immediate_bytes = rel[-5];
  assert(immediate_bytes >= sizeof(annotation_offset_t));
  const auto annotation_record = rel + immediate_bytes - sizeof(annotation_offset_t);
  const auto annotation_offset = load_jit_value<annotation_offset_t>(annotation_record);
  if (annotation_offset == std::numeric_limits<annotation_offset_t>::max()) {
    return;
  }
  const auto &function = *active_frame(*context).function_view;
  assert(annotation_offset <= function.interpreter_annotation_byte_count);
  assert(function.interpreter_annotation_pool != nullptr ||
         function.interpreter_annotation_byte_count == 0);
  if (function.interpreter_annotation_pool == nullptr) {
    assert(annotation_offset == 0);
    context->rel = nullptr;
  } else {
    context->rel = function.interpreter_annotation_pool + annotation_offset;
  }
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
  publish_interpreter_rel_after_runtime_boundary(context, rel);
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
  publish_interpreter_rel_after_runtime_boundary(context, annotation_offset_record);
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
      context, sp, local_base, instruction_ip, source_ip, source_ip + instruction_width, offset,
      nullptr);
  if (!cps_exit_pending(*context)) {
    context->ip = instruction_offset(*context, instruction_ip);
  }
  publish_interpreter_rel_after_runtime_boundary(context, rel);
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
      context, sp, local_base, instruction_ip, source_ip, source_ip + instruction_width, offset,
      nullptr);
  if (!cps_exit_pending(*context)) {
    context->ip = instruction_offset(*context, instruction_ip);
  }
  publish_interpreter_rel_after_runtime_boundary(context, rel);
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
  const auto completed = runtime_fc_memory_copy_decoded(context, sp, instruction_ip, source_ip,
                                                        source_ip + instruction_width, nullptr);
  publish_interpreter_rel_after_runtime_boundary(context, rel);
  return completed;
}

inline bool jit_runtime_fc_memory_fill(execution_context *context, std::uint32_t *&sp,
                                       [[maybe_unused]] std::uint32_t *&local_base,
                                       const std::uint8_t *rel) {
  assert(rel != nullptr);
  const auto source_ip = context->ip;
  const auto *instruction_ip = context->code + source_ip;
  const auto instruction_width = load_jit_value<std::uint8_t>(rel);
  const auto completed = runtime_fc_memory_fill_decoded(context, sp, instruction_ip, source_ip,
                                                        source_ip + instruction_width, nullptr);
  publish_interpreter_rel_after_runtime_boundary(context, rel);
  return completed;
}

/**
 * @brief The threshold Runtime API of a backward br inside a trace. The record holds the offset of
 * the br's own annotation, which holds the target block. While the next count stays below the
 * threshold, the trace counts the backedge (the handler's record_loop_backedge) and continues. At
 * the threshold nothing is counted: the CPS exit at the br's position hands it to the Interpreter
 * handler (runtime_br), which counts it once and yields.
 */
inline bool jit_runtime_backedge(execution_context *context, [[maybe_unused]] std::uint32_t *&sp,
                                 [[maybe_unused]] std::uint32_t *&local_base,
                                 const std::uint8_t *rel) {
  assert(rel != nullptr);
  const auto &function = *active_frame(*context).function_view;
  const auto annotation_offset = load_jit_value<annotation_offset_t>(rel);
  assert(annotation_offset < function.interpreter_annotation_byte_count);
  assert(function.interpreter_annotation_pool != nullptr);
  const std::uint8_t *annotation = function.interpreter_annotation_pool + annotation_offset;
  const auto target_block = load_jit_value<std::uint16_t>(annotation);
  const auto source_ip = context->ip;
  if (count_jit_backedge(*context, source_ip, target_block)) {
    return true;
  }
  return cps_exit_at(*context, source_ip, annotation, kBlockBoundary);
}

constexpr auto make_opcode_jit_runtime_api_table() {
  std::array<jit_runtime_api_pointer, 256> apis{};
  apis[kUnreachable] = &jit_runtime_unreachable;
  apis[kNop] = &jit_runtime_api_passthrough<&runtime_nop>;
  apis[kBr] = &jit_runtime_backedge;
  apis[kBrTable] = &jit_runtime_api_annotated<&runtime_br_table<false>>;
  apis[kReturn] = &jit_runtime_api_passthrough<&runtime_return<false>>;
  apis[kDrop] = &jit_runtime_api_passthrough<&runtime_drop>;
  apis[kSelect] = &jit_runtime_api_passthrough<&runtime_select>;
  apis[kGlobalGet] = &jit_runtime_api_passthrough<&runtime_global_get>;
  apis[kGlobalSet] = &jit_runtime_api_passthrough<&runtime_global_set>;
  apis[kI32Load] = &jit_runtime_memory_load<4, 1, false>;
  apis[kI64Load] = &jit_runtime_memory_load<8, 2, false>;
  apis[kF32Load] = &jit_runtime_memory_load<4, 1, false>;
  apis[kF64Load] = &jit_runtime_memory_load<8, 2, false>;
  apis[kI32Load8S] = &jit_runtime_memory_load<1, 1, true>;
  apis[kI32Load8U] = &jit_runtime_memory_load<1, 1, false>;
  apis[kI32Load16S] = &jit_runtime_memory_load<2, 1, true>;
  apis[kI32Load16U] = &jit_runtime_memory_load<2, 1, false>;
  apis[kI64Load8S] = &jit_runtime_memory_load<1, 2, true>;
  apis[kI64Load8U] = &jit_runtime_memory_load<1, 2, false>;
  apis[kI64Load16S] = &jit_runtime_memory_load<2, 2, true>;
  apis[kI64Load16U] = &jit_runtime_memory_load<2, 2, false>;
  apis[kI64Load32S] = &jit_runtime_memory_load<4, 2, true>;
  apis[kI64Load32U] = &jit_runtime_memory_load<4, 2, false>;
  apis[kI32Store] = &jit_runtime_memory_store<4, 1>;
  apis[kI64Store] = &jit_runtime_memory_store<8, 2>;
  apis[kF32Store] = &jit_runtime_memory_store<4, 1>;
  apis[kF64Store] = &jit_runtime_memory_store<8, 2>;
  apis[kI32Store8] = &jit_runtime_memory_store<1, 1>;
  apis[kI32Store16] = &jit_runtime_memory_store<2, 1>;
  apis[kI64Store8] = &jit_runtime_memory_store<1, 2>;
  apis[kI64Store16] = &jit_runtime_memory_store<2, 2>;
  apis[kI64Store32] = &jit_runtime_memory_store<4, 2>;
  apis[kMemorySize] = &jit_runtime_api_passthrough<&runtime_memory_manager_api_boundary>;
  apis[kMemoryGrow] = &jit_runtime_api_passthrough<&runtime_memory_manager_api_boundary>;
  apis[kI32Const] = &jit_runtime_api_passthrough<&runtime_i32_const>;
  apis[kI64Const] = &jit_runtime_api_passthrough<&runtime_i64_const>;
  apis[kF32Const] = &jit_runtime_api_passthrough<&runtime_f32_const>;
  apis[kF64Const] = &jit_runtime_api_passthrough<&runtime_f64_const>;
  apis[kI32Eqz] = &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_eqz<std::uint32_t>>>;
  apis[kI32Eq] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::equal_to<>{}>>;
  apis[kI32Ne] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::not_equal_to<>{}>>;
  apis[kI32LtS] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_lt<std::uint32_t>>>;
  apis[kI32LtU] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::less<>{}>>;
  apis[kI32GtS] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_gt<std::uint32_t>>>;
  apis[kI32GtU] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::greater<>{}>>;
  apis[kI32LeS] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_le<std::uint32_t>>>;
  apis[kI32LeU] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::less_equal<>{}>>;
  apis[kI32GeS] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_signed_ge<std::uint32_t>>>;
  apis[kI32GeU] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::greater_equal<>{}>>;
  apis[kI64Eqz] =
      &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_eqz<std::uint64_t>, true>>;
  apis[kI64Eq] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::equal_to<>{}, true>>;
  apis[kI64Ne] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::not_equal_to<>{}, true>>;
  apis[kI64LtS] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_lt<std::uint64_t>, true>>;
  apis[kI64LtU] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::less<>{}, true>>;
  apis[kI64GtS] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_gt<std::uint64_t>, true>>;
  apis[kI64GtU] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::greater<>{}, true>>;
  apis[kI64LeS] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_le<std::uint64_t>, true>>;
  apis[kI64LeU] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::less_equal<>{}, true>>;
  apis[kI64GeS] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_signed_ge<std::uint64_t>, true>>;
  apis[kI64GeU] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::greater_equal<>{}, true>>;
  apis[kF32Eq] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::equal_to<>{}, true>>;
  apis[kF32Ne] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::not_equal_to<>{}, true>>;
  apis[kF32Lt] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::less<>{}, true>>;
  apis[kF32Gt] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::greater<>{}, true>>;
  apis[kF32Le] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::less_equal<>{}, true>>;
  apis[kF32Ge] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::greater_equal<>{}, true>>;
  apis[kF64Eq] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::equal_to<>{}, true>>;
  apis[kF64Ne] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::not_equal_to<>{}, true>>;
  apis[kF64Lt] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::less<>{}, true>>;
  apis[kF64Gt] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::greater<>{}, true>>;
  apis[kF64Le] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::less_equal<>{}, true>>;
  apis[kF64Ge] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::greater_equal<>{}, true>>;
  apis[kI32Clz] = &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_clz<std::uint32_t>>>;
  apis[kI32Ctz] = &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_ctz<std::uint32_t>>>;
  apis[kI32Popcnt] =
      &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_popcount<std::uint32_t>>>;
  apis[kI32Add] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::plus<>{}>>;
  apis[kI32Sub] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::minus<>{}>>;
  apis[kI32Mul] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::multiplies<>{}>>;
  apis[kI32DivS] =
      &jit_runtime_api_passthrough<&runtime_i32_checked_binary<&opcode_div_signed<std::uint32_t>>>;
  apis[kI32DivU] = &jit_runtime_api_passthrough<
      &runtime_i32_checked_binary<&opcode_div_unsigned<std::uint32_t>>>;
  apis[kI32RemS] =
      &jit_runtime_api_passthrough<&runtime_i32_checked_binary<&opcode_rem_signed<std::uint32_t>>>;
  apis[kI32RemU] = &jit_runtime_api_passthrough<
      &runtime_i32_checked_binary<&opcode_rem_unsigned<std::uint32_t>>>;
  apis[kI32And] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::bit_and<>{}>>;
  apis[kI32Or] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::bit_or<>{}>>;
  apis[kI32Xor] = &jit_runtime_api_passthrough<&runtime_i32_binary<std::bit_xor<>{}>>;
  apis[kI32Shl] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_shift_left<std::uint32_t>>>;
  apis[kI32ShrS] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_shift_right_signed<std::uint32_t>>>;
  apis[kI32ShrU] = &jit_runtime_api_passthrough<
      &runtime_i32_binary<&opcode_shift_right_unsigned<std::uint32_t>>>;
  apis[kI32Rotl] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_rotate_left<std::uint32_t>>>;
  apis[kI32Rotr] =
      &jit_runtime_api_passthrough<&runtime_i32_binary<&opcode_rotate_right<std::uint32_t>>>;
  apis[kI64Clz] =
      &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_clz<std::uint64_t>, false>>;
  apis[kI64Ctz] =
      &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_ctz<std::uint64_t>, false>>;
  apis[kI64Popcnt] =
      &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_popcount<std::uint64_t>, false>>;
  apis[kI64Add] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::plus<>{}, false>>;
  apis[kI64Sub] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::minus<>{}, false>>;
  apis[kI64Mul] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::multiplies<>{}, false>>;
  apis[kI64DivS] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_div_signed<std::uint64_t>, false>>;
  apis[kI64DivU] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_div_unsigned<std::uint64_t>, false>>;
  apis[kI64RemS] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_rem_signed<std::uint64_t>, false>>;
  apis[kI64RemU] = &jit_runtime_api_passthrough<
      &runtime_i64_checked_binary<&opcode_rem_unsigned<std::uint64_t>, false>>;
  apis[kI64And] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::bit_and<>{}, false>>;
  apis[kI64Or] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::bit_or<>{}, false>>;
  apis[kI64Xor] = &jit_runtime_api_passthrough<&runtime_i64_binary<std::bit_xor<>{}, false>>;
  apis[kI64Shl] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_shift_left<std::uint64_t>, false>>;
  apis[kI64ShrS] = &jit_runtime_api_passthrough<
      &runtime_i64_binary<&opcode_shift_right_signed<std::uint64_t>, false>>;
  apis[kI64ShrU] = &jit_runtime_api_passthrough<
      &runtime_i64_binary<&opcode_shift_right_unsigned<std::uint64_t>, false>>;
  apis[kI64Rotl] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_rotate_left<std::uint64_t>, false>>;
  apis[kI64Rotr] =
      &jit_runtime_api_passthrough<&runtime_i64_binary<&opcode_rotate_right<std::uint64_t>, false>>;
  apis[kF32Abs] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_abs<float>>>;
  apis[kF32Neg] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_neg<float>>>;
  apis[kF32Ceil] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_ceil<float>>>;
  apis[kF32Floor] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_floor<float>>>;
  apis[kF32Trunc] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_trunc<float>>>;
  apis[kF32Nearest] =
      &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_nearest<float>>>;
  apis[kF32Sqrt] = &jit_runtime_api_passthrough<&runtime_f32_unary<&opcode_float_sqrt<float>>>;
  apis[kF32Add] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::plus<>{}, false>>;
  apis[kF32Sub] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::minus<>{}, false>>;
  apis[kF32Mul] = &jit_runtime_api_passthrough<&runtime_f32_binary<std::multiplies<>{}, false>>;
  apis[kF32Div] =
      &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_div<float>, false>>;
  apis[kF32Min] =
      &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_min<float>, false>>;
  apis[kF32Max] =
      &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_max<float>, false>>;
  apis[kF32Copysign] =
      &jit_runtime_api_passthrough<&runtime_f32_binary<&opcode_float_copysign<float>, false>>;
  apis[kF64Abs] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_abs<double>>>;
  apis[kF64Neg] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_neg<double>>>;
  apis[kF64Ceil] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_ceil<double>>>;
  apis[kF64Floor] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_floor<double>>>;
  apis[kF64Trunc] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_trunc<double>>>;
  apis[kF64Nearest] =
      &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_nearest<double>>>;
  apis[kF64Sqrt] = &jit_runtime_api_passthrough<&runtime_f64_unary<&opcode_float_sqrt<double>>>;
  apis[kF64Add] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::plus<>{}, false>>;
  apis[kF64Sub] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::minus<>{}, false>>;
  apis[kF64Mul] = &jit_runtime_api_passthrough<&runtime_f64_binary<std::multiplies<>{}, false>>;
  apis[kF64Div] =
      &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_div<double>, false>>;
  apis[kF64Min] =
      &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_min<double>, false>>;
  apis[kF64Max] =
      &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_max<double>, false>>;
  apis[kF64Copysign] =
      &jit_runtime_api_passthrough<&runtime_f64_binary<&opcode_float_copysign<double>, false>>;
  apis[kI32WrapI64] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_wrap_i64>>;
  apis[kI32TruncF32S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f32_s>>;
  apis[kI32TruncF32U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f32_u>>;
  apis[kI32TruncF64S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f64_s>>;
  apis[kI32TruncF64U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i32_trunc_f64_u>>;
  apis[kI64ExtendI32S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_extend_i32_s>>;
  apis[kI64ExtendI32U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_extend_i32_u>>;
  apis[kI64TruncF32S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f32_s>>;
  apis[kI64TruncF32U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f32_u>>;
  apis[kI64TruncF64S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f64_s>>;
  apis[kI64TruncF64U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_i64_trunc_f64_u>>;
  apis[kF32ConvertI32S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i32_s>>;
  apis[kF32ConvertI32U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i32_u>>;
  apis[kF32ConvertI64S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i64_s>>;
  apis[kF32ConvertI64U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_convert_i64_u>>;
  apis[kF32DemoteF64] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f32_demote_f64>>;
  apis[kF64ConvertI32S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i32_s>>;
  apis[kF64ConvertI32U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i32_u>>;
  apis[kF64ConvertI64S] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i64_s>>;
  apis[kF64ConvertI64U] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_convert_i64_u>>;
  apis[kF64PromoteF32] =
      &jit_runtime_api_passthrough<&runtime_conversion<conversion_f64_promote_f32>>;
  apis[kI32ReinterpretF32] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[kI64ReinterpretF64] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[kF32ReinterpretI32] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[kF64ReinterpretI64] = &jit_runtime_api_passthrough<&runtime_conversion<conversion_identity>>;
  apis[kI32Extend8S] =
      &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_extend8_signed<std::uint32_t>>>;
  apis[kI32Extend16S] =
      &jit_runtime_api_passthrough<&runtime_i32_unary<&opcode_extend16_signed<std::uint32_t>>>;
  apis[kI64Extend8S] = &jit_runtime_api_passthrough<
      &runtime_i64_unary<&opcode_extend8_signed<std::uint64_t>, false>>;
  apis[kI64Extend16S] = &jit_runtime_api_passthrough<
      &runtime_i64_unary<&opcode_extend16_signed<std::uint64_t>, false>>;
  apis[kI64Extend32S] =
      &jit_runtime_api_passthrough<&runtime_i64_unary<&opcode_extend32_signed, false>>;
  return apis;
}

inline constexpr auto kOpcodeJitRuntimeApis = make_opcode_jit_runtime_api_table();
static_assert(kOpcodeJitRuntimeApis[kLocalGet] == nullptr &&
                  kOpcodeJitRuntimeApis[kLocalSet] == nullptr &&
                  kOpcodeJitRuntimeApis[kLocalTee] == nullptr,
              "local operations must be emitted by the JIT encoder");

constexpr auto make_fc_opcode_jit_runtime_api_table() {
  std::array<jit_runtime_api_pointer, 12> apis{};
  apis[kFcI32TruncSatF32S] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_s>;
  apis[kFcI32TruncSatF32U] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_u>;
  apis[kFcI32TruncSatF64S] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_s>;
  apis[kFcI32TruncSatF64U] = &jit_runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_u>;
  apis[kFcI64TruncSatF32S] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_s>;
  apis[kFcI64TruncSatF32U] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_u>;
  apis[kFcI64TruncSatF64S] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_s>;
  apis[kFcI64TruncSatF64U] = &jit_runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_u>;
  apis[kFcMemoryCopy] = &jit_runtime_fc_memory_copy;
  apis[kFcMemoryFill] = &jit_runtime_fc_memory_fill;
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
static_assert(kJitRuntimeApiCount + 2 <= 0x100,
              "CPS call handler indices must fit the trace constant");
static_assert(sizeof(jit_runtime_api_pointer) == sizeof(fireball_native_cps_handler));
static_assert(sizeof(jit_runtime_api_pointer) == sizeof(std::uintptr_t));

/** @brief プラグインの保存領域にある読み取り専用API表の先頭を借用する。 */
const jit_runtime_api_pointer *runtime_api_table_data() {
  return kJitRuntimeApiFunctionTable.data();
}

std::size_t runtime_function_table_bytes() {
  return (kJitRuntimeApiCount + 2) * sizeof(std::uintptr_t);
}

const void *initialize_runtime_function_table(void *storage,
                                              const fireball_native_cps_handler *handlers) {
  assert(storage != nullptr && handlers != nullptr);
  auto *table_bytes = static_cast<std::uint8_t *>(storage);
  for (std::size_t index = 0; index < kJitRuntimeApiCount; ++index) {
    std::memcpy(table_bytes + index * sizeof(std::uintptr_t), &kJitRuntimeApiFunctionTable[index],
                sizeof(jit_runtime_api_pointer));
  }
  for (std::size_t index = 0; index < 2; ++index) {
    assert(handlers[index] != nullptr);
    std::memcpy(table_bytes + (kJitRuntimeApiCount + index) * sizeof(std::uintptr_t),
                &handlers[index], sizeof(fireball_native_cps_handler));
  }
  return storage;
}

std::uint8_t jit_call_handler_slot(bool indirect) {
  return static_cast<std::uint8_t>(kJitRuntimeApiCount + (indirect ? 1u : 0u));
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

extern "C" int fb_jit_cps_call_handler_slot(std::uint32_t opcode, std::uint8_t *slot) {
  assert(slot != nullptr);
  assert(opcode == fireball::kCall || opcode == fireball::kCallIndirect);
  *slot = fireball::jit_api::jit_call_handler_slot(opcode == fireball::kCallIndirect);
  return 1;
}

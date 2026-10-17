#pragma once
/** @file @brief InterpreterとJITが共有するWASM命令処理の宣言。
 * @details Runtime APIのalways_inline指定はLTOで命令呼出しを融合するための契約である。
 * 実装をヘッダへ戻さず、命令ごとの引数カーソルの退避・復元を避ける。
 */
#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"
#include <cstdint>

namespace fireball::wasm_runtime {

constexpr std::uint32_t kComplete = 1;
constexpr std::uint32_t kTrap = 2;
constexpr std::uint32_t kBlockBoundary = 3;
constexpr std::uint32_t kCallBoundary = 4;
constexpr std::uint32_t kRuntimeApiBoundary = 8;
constexpr std::uint32_t kStopAtBlockBoundaryFlag = 1u << 0;
constexpr std::uint32_t kSingleControlStepFlag = 1u << 2;
constexpr std::uint32_t kJitDispatchFlag = 1u << 3;
constexpr std::uint32_t kRelCursorReadyFlag = 1u << 4;
constexpr std::uint32_t kPendingBlockHeadFlag = 1u << 5;
constexpr std::uint32_t kCpsExitShift = 8;
constexpr std::uint32_t kCpsExitMask = fb_native_cps_exit_mask;
constexpr std::uint32_t kDispatchYield = 5;
constexpr std::uint32_t kNativeCallBoundary = 6;
constexpr std::uint32_t kDebugStop = 7;
constexpr std::uint32_t kTrapLocalStackCapacity = 1;
constexpr std::uint32_t kTrapCallStackCapacity = 3;
constexpr std::uint32_t kTrapOperandStackCapacity = 4;
constexpr std::uint32_t kNativeErrorInternal = 4;
constexpr std::uint32_t kTrapTableIndexOutOfBounds = 6;
constexpr std::uint32_t kTrapTableSlotUninitialized = 7;
constexpr std::uint32_t kTrapIndirectCallTypeMismatch = 8;
constexpr std::uint32_t kTrapUnreachable = 9;
constexpr std::uint32_t kTrapMemoryOutOfBounds = 13;
constexpr std::uint32_t kTrapDivideByZero = 15;
constexpr std::uint32_t kTrapIntegerOverflow = 16;
constexpr std::uint32_t kTrapInvalidConversion = 17;
constexpr std::uint32_t kSentinel = 0xFFFF'FFFFu;
constexpr block_index_t kInvalidBlockIndex = FIREBALL_INVALID_BLOCK_INDEX;
constexpr std::uint32_t kOpcodeFcPrefix = 0xFC;

using execution_context = fireball_execution_context_native;

/** @brief 有効な命令カーソルをコード先頭からの位置へ変換する。 */
code_ip_t instruction_offset(const execution_context &current, const std::uint8_t *ip);

/** @brief 現在実行中の呼び出しフレームを参照する。 */
fireball_call_frame_native &active_frame(execution_context &current);
/** @brief 現在実行中の呼び出しフレームを参照する。 */
const fireball_call_frame_native &active_frame(const execution_context &current);

static_assert(sizeof(void *) == 8, "native interpreter requires a 64-bit host ABI");
static_assert(sizeof(execution_context) == 144,
              "x86-64 execution_context ABI layout must remain 144 bytes");
static_assert(offsetof(execution_context, trap_code) == 0x70);
static_assert(offsetof(execution_context, local_base) == 0x78);
/** @brief CPSの実行境界理由を状態へ記録する。 */
void record_cps_exit(execution_context &current, std::uint32_t kind);
/** @brief 記録されたCPSの実行境界理由を取得する。 */
std::uint32_t cps_exit_kind(const execution_context &current);
/** @brief 未処理の実行境界が記録されているかを調べる。 */
bool cps_exit_pending(const execution_context &current);
/** @brief 処理済みの実行境界記録を消去する。 */
void clear_cps_exit(execution_context &current);
/** @brief `runtime_api_boundary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_api_boundary(execution_context &current, code_ip_t ip,
                                                 const std::uint8_t *rel);
bool block_boundary(execution_context &current, code_ip_t ip, block_index_t block_index,
                    const std::uint8_t *&rel);
bool continue_to_block(execution_context &current, code_ip_t ip, block_index_t block_index,
                       const std::uint8_t *&rel);
/** @brief WASM関数の完了を記録する。 */
bool complete(execution_context &current);
/** @brief WASMのtrap理由を記録し、通常継続を停止する。 */
bool trap(execution_context &current, std::uint32_t code);
/** @brief 呼び出し先の入口を確定してCPSを停止する。 */
bool call_boundary(execution_context &current, code_ip_t ip, block_index_t block_index,
                   const std::uint8_t *&rel);
/** @brief `debug_stop`に対応する実行状態または値を求める。 */
bool debug_stop(execution_context &current);
/** @brief Interpreter注釈プールの固定幅値を読み出す。 */
std::uint8_t read_u8(const std::uint8_t *&rel);
/** @brief Interpreter注釈プールのlittle-endian u16を読み出す。 */
std::uint16_t read_u16(const std::uint8_t *&rel);
/** @brief Interpreter注釈プールの固定幅値を読み出す。 */
std::uint32_t read_u32(const std::uint8_t *&rel);
/** @brief Interpreter注釈プールのu8値を先読みする。 */
std::uint8_t peek_u8(const std::uint8_t *rel);
/** @brief `instruction_width`に対応する実行状態または値を求める。 */
instruction_width_t instruction_width(const std::uint8_t *ip, const std::uint8_t *rel);
/** @brief デコード済み命令幅を消費し、命令カーソルを進める。 */
void advance_instruction_ip(const std::uint8_t *&ip, const std::uint8_t *&rel);
/** @brief デコード済み命令幅のレコードを消費する。 */
instruction_width_t consume_instruction_width(const std::uint8_t *ip, const std::uint8_t *&rel);
/** @brief 有効な命令カーソルをコード先頭からの位置へ変換する。 */
code_ip_t instruction_offset(const execution_context &current, const std::uint8_t *ip);
/** @brief Interpreter注釈プールの固定幅値を読み出す。 */
std::uint32_t peek_u32(const std::uint8_t *rel);

/** @brief 検証済みの下限をassertし、共有オペランドスタックから値を取り出す。 */
bool pop(execution_context &current, std::uint32_t *&sp, std::uint32_t &value);
/** @brief 関数入口で保証した上限をassertし、共有オペランドスタックへ値を積む。 */
bool push(execution_context &current, std::uint32_t *&sp, std::uint32_t value);
/** @brief Interpreter注釈プールの固定幅値を読み出す。 */
std::int32_t read_s32(const std::uint8_t *&rel);
/** @brief Interpreter注釈プールの固定幅値を読み出す。 */
std::int64_t read_s64(const std::uint8_t *&rel);
/** @brief 検証済みの下限をassertし、共有オペランドスタックから値を取り出す。 */
bool pop_u64(execution_context &current, std::uint32_t *&sp, std::uint64_t &value);
/** @brief 関数入口で保証した上限をassertし、共有オペランドスタックへ値を積む。 */
bool push_u64(execution_context &current, std::uint32_t *&sp, std::uint64_t value);
/** @brief `combine_u64`に対応する実行状態または値を求める。 */
std::uint64_t combine_u64(std::uint32_t low, std::uint32_t high);
template <bool ResultIsBool>
/** @brief `replace_u64_operands`に対応する実行状態または値を求める。 */
void replace_u64_operands(std::uint32_t *&sp, std::uint64_t result, operand_words_t operand_words);
/** @brief `f32_from_bits`に対応する実行状態または値を求める。 */
float f32_from_bits(std::uint32_t value);
/** @brief `f32_to_bits`に対応する実行状態または値を求める。 */
std::uint32_t f32_to_bits(float value);
/** @brief `f64_from_bits`に対応する実行状態または値を求める。 */
double f64_from_bits(std::uint64_t value);
/** @brief `f64_to_bits`に対応する実行状態または値を求める。 */
std::uint64_t f64_to_bits(double value);
/** @brief 検証済みの下限をassertし、共有オペランドスタックから値を取り出す。 */
bool pop_f32(execution_context &current, std::uint32_t *&sp, float &value);
/** @brief 関数入口で保証した上限をassertし、共有オペランドスタックへ値を積む。 */
bool push_f32(execution_context &current, std::uint32_t *&sp, float value);
/** @brief 検証済みの下限をassertし、共有オペランドスタックから値を取り出す。 */
bool pop_f64(execution_context &current, std::uint32_t *&sp, double &value);
/** @brief 関数入口で保証した上限をassertし、共有オペランドスタックへ値を積む。 */
bool push_f64(execution_context &current, std::uint32_t *&sp, double value);
/** @brief 現在実行中の呼び出しフレームを参照する。 */
fireball_call_frame_native &active_frame(execution_context &current);

/** @brief 現在実行中の呼び出しフレームを参照する。 */
const fireball_call_frame_native &active_frame(const execution_context &current);

/** @brief 基本ブロック入口のInterpreter注釈プールを参照する。 */
const std::uint8_t *interpreter_rel_for_block_index(const fireball_call_frame_native &frame,
                                                    block_index_t block_index);

/** @brief JIT実行後のInterpreter注釈カーソルを復元する。 */
const std::uint8_t *interpreter_rel_after_jit_trace(const fireball_call_frame_native &frame,
                                                    block_index_t block_index,
                                                    annotation_offset_t consumed_bytes);
/** @brief 関数の終了を反映し、必要なら呼び出し元を復元する。 */
bool finish_current_function(execution_context &current, std::uint32_t *&local_base,
                             const std::uint8_t *&instruction_ip);
/** @brief `is_requested_block_boundary`に対応する実行状態または値を求める。 */
bool is_requested_block_boundary(const execution_context &current, code_ip_t ip);
/** @brief `record_loop_backedge`に対応する実行状態または値を求める。 */
bool record_loop_backedge(execution_context &current, code_ip_t source_ip, code_ip_t target_ip);

struct local_value_span {
  frame_word_offset_t offset;
  operand_words_t width;
};
/** @brief 検証済みローカル変数のスロット位置と幅を返す。 */
local_value_span local_span(const fireball_call_frame_native &frame, local_count_t index);
/** @brief WASMの分岐を適用し、次の命令位置を返す。 */
void branch(execution_context &current, std::uint32_t *&sp, block_index_t target_block_index,
            operand_words_t target_stack_base_words, operand_words_t result_words);
/** @brief 検証済みの下限をassertし、共有オペランドスタックから値を取り出す。 */
bool pop(execution_context &current, std::uint32_t *&sp, std::uint32_t &value);
/** @brief 関数入口で保証した上限をassertし、共有オペランドスタックへ値を積む。 */
bool push(execution_context &current, std::uint32_t *&sp, std::uint32_t value);

// Named opcode operations keep the API catalog readable and independently
// callable; no anonymous callable bodies are embedded in handler tables.
template <typename T> bool opcode_eqz(T value);

template <typename T> bool opcode_signed_lt(T left, T right);

template <typename T> bool opcode_signed_gt(T left, T right);

template <typename T> bool opcode_signed_le(T left, T right);

template <typename T> bool opcode_signed_ge(T left, T right);

template <typename T> T opcode_clz(T value);

template <typename T> T opcode_ctz(T value);

template <typename T> T opcode_popcount(T value);

template <typename T> T opcode_shift_left(T left, T right);

template <typename T> T opcode_shift_right_signed(T left, T right);

template <typename T> T opcode_shift_right_unsigned(T left, T right);

template <typename T> T opcode_rotate_left(T left, T right);

template <typename T> T opcode_rotate_right(T left, T right);

template <typename T> bool opcode_div_signed(T left, T right, T &result, std::uint32_t &trap_code);

template <typename T>
/** @brief `opcode_div_unsigned`のWASM数値演算を実行する。 */
bool opcode_div_unsigned(T left, T right, T &result, std::uint32_t &trap_code);

template <typename T> bool opcode_rem_signed(T left, T right, T &result, std::uint32_t &trap_code);

template <typename T>
/** @brief `opcode_rem_unsigned`のWASM数値演算を実行する。 */
bool opcode_rem_unsigned(T left, T right, T &result, std::uint32_t &trap_code);

template <typename T> T opcode_extend8_signed(T value);

template <typename T> T opcode_extend16_signed(T value);
/** @brief `opcode_extend32_signed`のWASM数値演算を実行する。 */
std::uint64_t opcode_extend32_signed(std::uint64_t value);

template <typename T> T opcode_float_abs(T value);

template <typename T> T opcode_float_neg(T value);

template <typename T> T opcode_float_ceil(T value);

template <typename T> T opcode_float_floor(T value);

template <typename T> T opcode_float_trunc(T value);

template <typename T> T opcode_float_nearest(T value);

template <typename T> T opcode_float_sqrt(T value);

template <typename T> T opcode_float_div(T left, T right);

template <typename T> T opcode_float_min(T left, T right);

template <typename T> T opcode_float_max(T left, T right);

template <typename T> T opcode_float_copysign(T left, T right);

// One-operation runtime APIs. These update interpreter state and never dispatch
// the next opcode; CPS handlers are thin adapters around this shared interface.

using runtime_api_pointer = bool (*)(execution_context *, const std::uint8_t *&, std::uint32_t *&,
                                     std::uint32_t *&, const std::uint8_t *&);
template <auto Operation>
/** @brief `runtime_i32_unary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool
runtime_i32_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                  std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel);

// Opcode semantics are declared beside each handler-table entry below.
/** @brief WASMの線形メモリアクセス範囲を判定する。 */
bool linear_memory_range_is_valid(const execution_context &current, std::uint64_t offset,
                                  std::uint32_t length);
/** @brief `conversion_i32_wrap_i64`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_wrap_i64(execution_context &current, std::uint32_t *&sp,
                             std::uint32_t &trap_code);
/** @brief `conversion_i32_trunc_f32_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_f32_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i32_trunc_f32_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_f32_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i32_trunc_f64_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_f64_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i32_trunc_f64_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_f64_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i64_trunc_f32_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_f32_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i64_trunc_f32_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_f32_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i64_trunc_f64_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_f64_s(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i64_trunc_f64_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_f64_u(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_i32_trunc_sat_f32_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_sat_f32_s(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i32_trunc_sat_f32_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_sat_f32_u(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i32_trunc_sat_f64_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_sat_f64_s(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i32_trunc_sat_f64_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i32_trunc_sat_f64_u(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i64_trunc_sat_f32_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_sat_f32_s(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i64_trunc_sat_f32_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_sat_f32_u(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i64_trunc_sat_f64_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_sat_f64_s(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i64_trunc_sat_f64_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_trunc_sat_f64_u(execution_context &current, std::uint32_t *&sp);
/** @brief `conversion_i64_extend_i32_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_extend_i32_s(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &trap_code);
/** @brief `conversion_i64_extend_i32_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_i64_extend_i32_u(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &trap_code);
/** @brief `conversion_f32_convert_i32_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f32_convert_i32_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f32_convert_i32_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f32_convert_i32_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f64_convert_i32_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f64_convert_i32_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f64_convert_i32_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f64_convert_i32_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f32_convert_i64_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f32_convert_i64_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f32_convert_i64_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f32_convert_i64_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f64_convert_i64_s`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f64_convert_i64_s(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f64_convert_i64_u`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f64_convert_i64_u(execution_context &current, std::uint32_t *&sp,
                                  [[maybe_unused]] std::uint32_t &trap_code);
/** @brief `conversion_f64_promote_f32`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f64_promote_f32(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &trap_code);
/** @brief `conversion_f32_demote_f64`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_f32_demote_f64(execution_context &current, std::uint32_t *&sp,
                               std::uint32_t &trap_code);
/** @brief `conversion_identity`の数値変換を実行し、WASMの変換エラーを明示する。 */
bool conversion_identity(execution_context &, std::uint32_t *&, std::uint32_t &trap_code);

template <bool Indirect>
/** @brief `runtime_call`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool
runtime_call(execution_context *context, const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
             std::uint32_t *&local_stack, [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_nop`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_nop(execution_context *, const std::uint8_t *&instruction_ip,
                                        [[maybe_unused]] std::uint32_t *&sp,
                                        [[maybe_unused]] std::uint32_t *&local_base,
                                        [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_else`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_else(execution_context *context,
                                         const std::uint8_t *&instruction_ip,
                                         [[maybe_unused]] std::uint32_t *&sp,
                                         [[maybe_unused]] std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_block`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_block(execution_context *context,
                                          const std::uint8_t *&instruction_ip,
                                          [[maybe_unused]] std::uint32_t *&sp,
                                          [[maybe_unused]] std::uint32_t *&local_base,
                                          [[maybe_unused]] const std::uint8_t *&rel);

/** @brief `runtime_if`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_if(execution_context *context,
                                       const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                       [[maybe_unused]] std::uint32_t *&local_base,
                                       [[maybe_unused]] const std::uint8_t *&rel);

/** @brief `runtime_br`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_br(execution_context *context,
                                       const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                       [[maybe_unused]] std::uint32_t *&local_base,
                                       [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_br_if`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_br_if(execution_context *context,
                                          const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                          [[maybe_unused]] std::uint32_t *&local_base,
                                          [[maybe_unused]] const std::uint8_t *&rel);

// These helpers mutate only the local and operand state. The instruction
// wrapper advances ip after the operation succeeds.
/** @brief `apply_local_get`に対応する実行状態または値を求める。 */
bool apply_local_get(execution_context *context, std::uint32_t *&sp, std::uint32_t *&local_base,
                     const fireball_call_frame_native &frame, local_count_t index);
/** @brief `apply_local_set`に対応する実行状態または値を求める。 */
bool apply_local_set(execution_context *context, std::uint32_t *&sp, std::uint32_t *&local_base,
                     const fireball_call_frame_native &frame, local_count_t index);
/** @brief `apply_local_tee`に対応する実行状態または値を求める。 */
bool apply_local_tee(execution_context *context, std::uint32_t *&sp, std::uint32_t *&local_base,
                     const fireball_call_frame_native &frame, local_count_t index);
/** @brief
 * `runtime_local_get_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_local_get_decoded(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      std::uint32_t *&local_base,
                                                      local_count_t index);
/** @brief `runtime_local_get`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_local_get(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
/** @brief
 * `runtime_local_set_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_local_set_decoded(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      std::uint32_t *&local_base,
                                                      local_count_t index);
/** @brief `runtime_local_set`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_local_set(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
/** @brief
 * `runtime_local_tee_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_local_tee_decoded(execution_context *context,
                                                      std::uint32_t *&sp,
                                                      std::uint32_t *&local_base,
                                                      local_count_t index);
/** @brief `runtime_local_tee`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_local_tee(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp, std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_global_get`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_global_get(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp,
                                               [[maybe_unused]] std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_global_set`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_global_set(execution_context *context,
                                               const std::uint8_t *&instruction_ip,
                                               std::uint32_t *&sp,
                                               [[maybe_unused]] std::uint32_t *&local_base,
                                               [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_drop`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_drop(execution_context *context,
                                         const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                         [[maybe_unused]] std::uint32_t *&local_base,
                                         [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_select`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_select(execution_context *context,
                                           const std::uint8_t *&instruction_ip, std::uint32_t *&sp,
                                           [[maybe_unused]] std::uint32_t *&local_base,
                                           [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_end`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_end(execution_context *context,
                                        const std::uint8_t *&instruction_ip,
                                        [[maybe_unused]] std::uint32_t *&sp,
                                        [[maybe_unused]] std::uint32_t *&local_base,
                                        [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_return`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_return(execution_context *context,
                                           const std::uint8_t *&instruction_ip,
                                           [[maybe_unused]] std::uint32_t *&sp,
                                           [[maybe_unused]] std::uint32_t *&local_base,
                                           [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_br_table`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_br_table(execution_context *context,
                                             const std::uint8_t *&instruction_ip,
                                             std::uint32_t *&sp,
                                             [[maybe_unused]] std::uint32_t *&local_base,
                                             [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_i32_const`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_i32_const(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp,
                                              [[maybe_unused]] std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation>
/** @brief `runtime_i32_binary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_i32_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                   std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation>
/** @brief
   `runtime_i32_checked_binary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_i32_checked_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                           std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                           [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation, bool ResultIsBool>
/** @brief `runtime_i64_binary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_i64_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                   std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation, bool ResultIsBool>
/** @brief
   `runtime_i64_checked_binary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_i64_checked_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                           std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                           [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation, bool ResultIsBool>
/** @brief `runtime_i64_unary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool
runtime_i64_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                  std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation, bool ResultIsBool>
/** @brief `runtime_f32_binary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_f32_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                   std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation, bool ResultIsBool>
/** @brief `runtime_f64_binary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_f64_binary(execution_context *context, const std::uint8_t *&instruction_ip,
                   std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation>
/** @brief `runtime_f32_unary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool
runtime_f32_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                  std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel);

template <auto Operation>
/** @brief `runtime_f64_unary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool
runtime_f64_unary(execution_context *context, const std::uint8_t *&instruction_ip,
                  std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel);
template <bool (*Operation)(execution_context &, std::uint32_t *&, std::uint32_t &)>
/** @brief `runtime_conversion`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_conversion(execution_context *context, const std::uint8_t *&instruction_ip,
                   std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_i64_const`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_i64_const(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp,
                                              [[maybe_unused]] std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_f32_const`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_f32_const(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp,
                                              [[maybe_unused]] std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
/** @brief `runtime_f64_const`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_f64_const(execution_context *context,
                                              const std::uint8_t *&instruction_ip,
                                              std::uint32_t *&sp,
                                              [[maybe_unused]] std::uint32_t *&local_base,
                                              [[maybe_unused]] const std::uint8_t *&rel);
template <std::uint32_t Width> std::uint64_t sign_extend_memory_value(std::uint64_t value);

template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
/** @brief
   `runtime_memory_load_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_memory_load_decoded(execution_context *context, std::uint32_t *&sp,
                            [[maybe_unused]] std::uint32_t *&local_base,
                            const std::uint8_t *&instruction_ip, code_ip_t source_ip,
                            code_ip_t next_pc, std::uint32_t offset,
                            const std::uint8_t *rel);

template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
/** @brief `runtime_memory_load`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_memory_load(execution_context *context,
                                                const std::uint8_t *&instruction_ip,
                                                std::uint32_t *&sp, std::uint32_t *&local_base,
                                                [[maybe_unused]] const std::uint8_t *&rel);

template <std::uint32_t Width, std::uint32_t ValueWords>
/** @brief
   `runtime_memory_store_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_memory_store_decoded(execution_context *context, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t *&local_base,
                             const std::uint8_t *&instruction_ip, code_ip_t source_ip,
                             code_ip_t next_pc, std::uint32_t offset,
                             const std::uint8_t *rel);

template <std::uint32_t Width, std::uint32_t ValueWords>
/** @brief `runtime_memory_store`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_memory_store(execution_context *context,
                                                 const std::uint8_t *&instruction_ip,
                                                 std::uint32_t *&sp, std::uint32_t *&local_base,
                                                 [[maybe_unused]] const std::uint8_t *&rel);
template <bool (*Operation)(execution_context &, std::uint32_t *&)>
/** @brief
   `runtime_fc_saturating_conversion_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_fc_saturating_conversion_decoded(execution_context *context,
                                                                     std::uint32_t *&sp);
template <bool (*Operation)(execution_context &, std::uint32_t *&)>
/** @brief
   `runtime_fc_saturating_conversion`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_fc_saturating_conversion(execution_context *context, const std::uint8_t *&instruction_ip,
                                 std::uint32_t *&sp, [[maybe_unused]] std::uint32_t *&local_base,
                                 [[maybe_unused]] const std::uint8_t *&rel);

/** @brief
 * `runtime_fc_memory_copy_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_fc_memory_copy_decoded(execution_context *context,
                                                           std::uint32_t *&sp,
                                                           const std::uint8_t *&instruction_ip,
                                                           code_ip_t source_ip, code_ip_t next_pc,
                                                           const std::uint8_t *rel);

/** @brief
 * `runtime_fc_memory_copy`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_fc_memory_copy(execution_context *context,
                                                   const std::uint8_t *&instruction_ip,
                                                   std::uint32_t *&sp,
                                                   [[maybe_unused]] std::uint32_t *&local_base,
                                                   [[maybe_unused]] const std::uint8_t *&rel);

/** @brief
 * `runtime_fc_memory_fill_decoded`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool runtime_fc_memory_fill_decoded(execution_context *context,
                                                           std::uint32_t *&sp,
                                                           const std::uint8_t *&instruction_ip,
                                                           code_ip_t source_ip, code_ip_t next_pc,
                                                           const std::uint8_t *rel);

/** @brief
 * `runtime_fc_memory_fill`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。 */
[[gnu::always_inline]] bool runtime_fc_memory_fill(execution_context *context,
                                                   const std::uint8_t *&instruction_ip,
                                                   std::uint32_t *&sp,
                                                   [[maybe_unused]] std::uint32_t *&local_base,
                                                   [[maybe_unused]] const std::uint8_t *&rel);
/** @brief
 * `runtime_memory_manager_api_boundary`の一操作を実行し、通常継続ならtrue、trapまたは境界ならfalseを返す。
 */
[[gnu::always_inline]] bool
runtime_memory_manager_api_boundary(execution_context *context, const std::uint8_t *&instruction_ip,
                                    [[maybe_unused]] std::uint32_t *&sp,
                                    [[maybe_unused]] std::uint32_t *&local_base,
                                    const std::uint8_t *&rel);

} // namespace fireball::wasm_runtime

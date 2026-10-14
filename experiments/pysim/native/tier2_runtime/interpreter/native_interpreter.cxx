/** @file
 * @brief Native InterpreterのCPS命令処理と実行境界を実装する。
 * @details ホスト契約は入口でassertし、WASMの実行エラーはtrapとして返す。
 */
#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"

#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <initializer_list>
#include <limits>
#include <type_traits>

#define FIREBALL_CPS_CALL FIREBALL_NATIVE_CPS_CALL

/** @details CPSハンドラ表とmusttailの遷移先は同じ翻訳単位に置く。
 * Runtime API、命令意味論、境界ディスパッチの実装をヘッダへ公開しない。
 */
#include "native_interpreter_runtime.hxx"

/** @brief DebuggerとCPS handlerの直接末尾呼び出しをこの翻訳単位で組み立てる。 */
#include "native_wasm_operations.hxx"
using namespace fireball::wasm_runtime;

namespace {
struct debug_context {
  execution_context execution;
  fb_native_debug_control *control;
};

static_assert(offsetof(debug_context, execution) == 0);
static_assert(offsetof(debug_context, control) == sizeof(execution_context));

struct debugger_aspect {
  [[gnu::always_inline]] static inline bool before_instruction(execution_context &context,
                                                               const std::uint8_t *ip) {
    auto &control = *reinterpret_cast<debug_context *>(&context)->control;
    if (control.enabled == 0) {
      return false;
    }
    const auto &frame = active_frame(context);
    const auto pc = frame.function_view->code_pc_offset + instruction_offset(context, ip);
    control.current_pc = pc;
    if (control.single_step != 0 && control.executed != 0) {
      control.stopped = 1;
      return true;
    }
    if (control.executed != 0) {
      if (control.breakpoint_count != 0) {
        const auto *first = std::lower_bound(control.breakpoints,
                                             control.breakpoints + control.breakpoint_count, pc);
        if (first != control.breakpoints + control.breakpoint_count && *first == pc) {
          control.stopped = 1;
          return true;
        }
      }
    }
    control.executed = 1;
    return false;
  }
};

/** @brief Pythonのrun_step経路だけが要求するブロック境界検査を選択する。 */
struct block_step_aspect {};

// Inlining this boundary makes each handler tail-jump through the opcode table.
template <typename Debugger = void>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
dispatch_next(execution_context *context, const std::uint8_t *ip, std::uint32_t *sp,
              const std::uint8_t *rel);

template <typename Debugger>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
continue_cps(execution_context *context, const std::uint8_t *ip, std::uint32_t *sp,
             const std::uint8_t *rel) {
  [[clang::musttail]] return dispatch_next<Debugger>(context, ip, sp, rel);
}

} // namespace

// Interpreter CPS handlers and opcode dispatch.
namespace {

template <typename Debugger>
inline FIREBALL_CPS_CALL void h_unreachable(execution_context *context,
                                            const std::uint8_t *instruction_ip, std::uint32_t *sp,
                                            [[maybe_unused]] const std::uint8_t *rel) {
  context->ip = instruction_offset(*context, instruction_ip);
  context->stack_pointer = sp;
  trap(*context, kTrapUnreachable);
  return;
}

constexpr std::uint8_t kOpcodeControlTerminator = 1u << 0;

inline constexpr auto make_opcode_attributes() {
  std::array<std::uint8_t, 256> attributes{};
  for (const auto opcode :
       {0x02u, 0x03u, 0x04u, 0x05u, 0x0Bu, 0x0Cu, 0x0Du, 0x0Eu, 0x0Fu, 0x10u, 0x11u}) {
    attributes[opcode] |= kOpcodeControlTerminator;
  }
  return attributes;
}

constexpr auto kOpcodeAttributes = make_opcode_attributes();

inline bool opcode_is_control_terminator(std::uint8_t opcode) {
  return (kOpcodeAttributes[opcode] & kOpcodeControlTerminator) != 0;
}

inline bool opcode_is_block_body(std::uint8_t opcode) {
  // A JIT body may start with a direct stencil or a Runtime API lowering.
  // Control opcodes and the separately dispatched 0xFC prefix remain
  // boundaries.
  return opcode != 0xFCu && !opcode_is_control_terminator(opcode);
}

template <typename Debugger>
inline FIREBALL_CPS_CALL void
h_unsupported_opcode(execution_context *context, const std::uint8_t *instruction_ip,
                     std::uint32_t *sp, [[maybe_unused]] const std::uint8_t *rel) {
  context->ip = instruction_offset(*context, instruction_ip);
  context->stack_pointer = sp;
  trap(*context, kTrapUnreachable);
  return;
}

template <typename Debugger, auto RuntimeApi>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
h_runtime_api(execution_context *context, const std::uint8_t *instruction_ip, std::uint32_t *sp,
              const std::uint8_t *rel) {
  static_assert(std::is_same_v<decltype(RuntimeApi), runtime_api_pointer>,
                "interpreter Runtime APIs must share one prototype");
  auto *local_base = context->local_base;
  const bool should_continue = RuntimeApi(context, instruction_ip, sp, local_base, rel);
  context->local_base = local_base;
  if (!should_continue) {
    if (cps_exit_kind(*context) == kTrap) {
      context->ip = instruction_offset(*context, instruction_ip);
    }
    context->rel = rel;
    context->stack_pointer = sp;
    assert(cps_exit_pending(*context));
    return;
  }
  assert(!cps_exit_pending(*context));
  [[clang::musttail]] return continue_cps<Debugger>(context, instruction_ip, sp, rel);
}

using native_opcode_handler = fireball_native_cps_handler;
static_assert(std::is_same_v<decltype(&h_unreachable<void>), native_opcode_handler>);
static_assert(std::is_same_v<decltype(&h_unsupported_opcode<void>), native_opcode_handler>);
static_assert(std::is_same_v<decltype(&h_runtime_api<void, &runtime_nop>), native_opcode_handler>);

template <typename Debugger, auto RuntimeApi>
inline constexpr native_opcode_handler runtime_api_handler = &h_runtime_api<Debugger, RuntimeApi>;

template <typename Debugger> constexpr auto make_fc_opcode_handlers() {
  std::array<native_opcode_handler, 12> handlers{};
  handlers.fill(&h_unsupported_opcode<Debugger>);
  handlers[0x00] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_s>>;
  handlers[0x01] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_u>>;
  handlers[0x02] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_s>>;
  handlers[0x03] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_u>>;
  handlers[0x04] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_s>>;
  handlers[0x05] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_u>>;
  handlers[0x06] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_s>>;
  handlers[0x07] =
      runtime_api_handler<Debugger,
                          &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_u>>;
  handlers[0x0A] = runtime_api_handler<Debugger, &runtime_fc_memory_copy>;
  handlers[0x0B] = runtime_api_handler<Debugger, &runtime_fc_memory_fill>;
  return handlers;
}

template <typename Debugger>
inline constexpr auto kFcOpcodeHandlers = make_fc_opcode_handlers<Debugger>();

template <typename Debugger>
inline FIREBALL_CPS_CALL void h_fc_dispatch(execution_context *context,
                                            const std::uint8_t *instruction_ip [[maybe_unused]],
                                            std::uint32_t *sp, const std::uint8_t *rel) {
  const auto subopcode = peek_u8(rel + sizeof(instruction_width_t));
  // The selected Runtime API consumes the width record, subopcode, and operands.
  if (subopcode >= kFcOpcodeHandlers<Debugger>.size()) {
    [[clang::musttail]] return h_unsupported_opcode<Debugger>(context, instruction_ip, sp, rel);
  }
  [[clang::musttail]] return kFcOpcodeHandlers<Debugger>[subopcode](context, instruction_ip, sp,
                                                                    rel);
}

static_assert(std::is_same_v<decltype(&h_fc_dispatch<void>), native_opcode_handler>);

template <typename Debugger> constexpr auto make_opcode_handlers() {
  std::array<native_opcode_handler, 256> handlers{};
  handlers.fill(&h_unsupported_opcode<Debugger>);
  handlers[0x00] = &h_unreachable<Debugger>;
  handlers[0x01] = runtime_api_handler<Debugger, &runtime_nop>;
  handlers[0x02] = runtime_api_handler<Debugger, &runtime_block>; // block
  handlers[0x03] = runtime_api_handler<Debugger, &runtime_block>; // loop
  handlers[0x04] = runtime_api_handler<Debugger, &runtime_if>;
  handlers[0x05] = runtime_api_handler<Debugger, &runtime_else>;
  handlers[0x0B] = runtime_api_handler<Debugger, &runtime_end>;
  handlers[0x0C] = runtime_api_handler<Debugger, &runtime_br>;
  handlers[0x0D] = runtime_api_handler<Debugger, &runtime_br_if>;
  handlers[0x0E] = runtime_api_handler<Debugger, &runtime_br_table>;
  handlers[0x0F] = runtime_api_handler<Debugger, &runtime_return>;
  handlers[0x10] = runtime_api_handler<Debugger, &runtime_call<false>>; // call
  handlers[0x11] = runtime_api_handler<Debugger, &runtime_call<true>>;  // call_indirect
  handlers[0x1A] = runtime_api_handler<Debugger, &runtime_drop>;
  handlers[0x1B] = runtime_api_handler<Debugger, &runtime_select>;
  handlers[0x20] = runtime_api_handler<Debugger, &runtime_local_get>;
  handlers[0x21] = runtime_api_handler<Debugger, &runtime_local_set>;
  handlers[0x22] = runtime_api_handler<Debugger, &runtime_local_tee>;
  handlers[0x23] = runtime_api_handler<Debugger, &runtime_global_get>;
  handlers[0x24] = runtime_api_handler<Debugger, &runtime_global_set>;
  handlers[0x28] = runtime_api_handler<Debugger, &runtime_memory_load<4, 1, false>>; // i32.load
  handlers[0x29] = runtime_api_handler<Debugger, &runtime_memory_load<8, 2, false>>; // i64.load
  handlers[0x2A] = runtime_api_handler<Debugger, &runtime_memory_load<4, 1, false>>; // f32.load
  handlers[0x2B] = runtime_api_handler<Debugger, &runtime_memory_load<8, 2, false>>; // f64.load
  handlers[0x2C] = runtime_api_handler<Debugger, &runtime_memory_load<1, 1, true>>;  // i32.load8_s
  handlers[0x2D] = runtime_api_handler<Debugger, &runtime_memory_load<1, 1, false>>; // i32.load8_u
  handlers[0x2E] = runtime_api_handler<Debugger, &runtime_memory_load<2, 1, true>>;  // i32.load16_s
  handlers[0x2F] = runtime_api_handler<Debugger, &runtime_memory_load<2, 1, false>>; // i32.load16_u
  handlers[0x30] = runtime_api_handler<Debugger, &runtime_memory_load<1, 2, true>>;  // i64.load8_s
  handlers[0x31] = runtime_api_handler<Debugger, &runtime_memory_load<1, 2, false>>; // i64.load8_u
  handlers[0x32] = runtime_api_handler<Debugger, &runtime_memory_load<2, 2, true>>;  // i64.load16_s
  handlers[0x33] = runtime_api_handler<Debugger, &runtime_memory_load<2, 2, false>>; // i64.load16_u
  handlers[0x34] = runtime_api_handler<Debugger, &runtime_memory_load<4, 2, true>>;  // i64.load32_s
  handlers[0x35] = runtime_api_handler<Debugger, &runtime_memory_load<4, 2, false>>; // i64.load32_u
  handlers[0x36] = runtime_api_handler<Debugger, &runtime_memory_store<4, 1>>;       // i32.store
  handlers[0x37] = runtime_api_handler<Debugger, &runtime_memory_store<8, 2>>;       // i64.store
  handlers[0x38] = runtime_api_handler<Debugger, &runtime_memory_store<4, 1>>;       // f32.store
  handlers[0x39] = runtime_api_handler<Debugger, &runtime_memory_store<8, 2>>;       // f64.store
  handlers[0x3A] = runtime_api_handler<Debugger, &runtime_memory_store<1, 1>>;       // i32.store8
  handlers[0x3B] = runtime_api_handler<Debugger, &runtime_memory_store<2, 1>>;       // i32.store16
  handlers[0x3C] = runtime_api_handler<Debugger, &runtime_memory_store<1, 2>>;       // i64.store8
  handlers[0x3D] = runtime_api_handler<Debugger, &runtime_memory_store<2, 2>>;       // i64.store16
  handlers[0x3E] = runtime_api_handler<Debugger, &runtime_memory_store<4, 2>>;       // i64.store32
  handlers[0x3F] = runtime_api_handler<Debugger,
                                       &runtime_memory_manager_api_boundary>; // memory.size
  handlers[0x40] = runtime_api_handler<Debugger,
                                       &runtime_memory_manager_api_boundary>; // memory.grow
  handlers[0x41] = runtime_api_handler<Debugger, &runtime_i32_const>;
  handlers[0x42] = runtime_api_handler<Debugger, &runtime_i64_const>;
  handlers[0x43] = runtime_api_handler<Debugger, &runtime_f32_const>;
  handlers[0x44] = runtime_api_handler<Debugger, &runtime_f64_const>;
  handlers[0x45] =
      runtime_api_handler<Debugger, &runtime_i32_unary<&opcode_eqz<std::uint32_t>>>;      // i32.eqz
  handlers[0x46] = runtime_api_handler<Debugger, &runtime_i32_binary<std::equal_to<>{}>>; // i32.eq
  handlers[0x47] =
      runtime_api_handler<Debugger, &runtime_i32_binary<std::not_equal_to<>{}>>; // i32.ne
  handlers[0x48] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<&opcode_signed_lt<std::uint32_t>>>;     // i32.lt_s
  handlers[0x49] = runtime_api_handler<Debugger, &runtime_i32_binary<std::less<>{}>>; // i32.lt_u
  handlers[0x4A] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<&opcode_signed_gt<std::uint32_t>>>;        // i32.gt_s
  handlers[0x4B] = runtime_api_handler<Debugger, &runtime_i32_binary<std::greater<>{}>>; // i32.gt_u
  handlers[0x4C] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<&opcode_signed_le<std::uint32_t>>>; // i32.le_s
  handlers[0x4D] =
      runtime_api_handler<Debugger, &runtime_i32_binary<std::less_equal<>{}>>; // i32.le_u
  handlers[0x4E] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<&opcode_signed_ge<std::uint32_t>>>; // i32.ge_s
  handlers[0x4F] =
      runtime_api_handler<Debugger, &runtime_i32_binary<std::greater_equal<>{}>>; // i32.ge_u
  handlers[0x50] =
      runtime_api_handler<Debugger,
                          &runtime_i64_unary<&opcode_eqz<std::uint64_t>, true>>; // i64.eqz
  handlers[0x51] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::equal_to<>{}, true>>; // i64.eq
  handlers[0x52] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::not_equal_to<>{}, true>>; // i64.ne
  handlers[0x53] =
      runtime_api_handler<Debugger,
                          &runtime_i64_binary<&opcode_signed_lt<std::uint64_t>, true>>; // i64.lt_s
  handlers[0x54] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::less<>{}, true>>; // i64.lt_u
  handlers[0x55] =
      runtime_api_handler<Debugger,
                          &runtime_i64_binary<&opcode_signed_gt<std::uint64_t>, true>>; // i64.gt_s
  handlers[0x56] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::greater<>{}, true>>; // i64.gt_u
  handlers[0x57] =
      runtime_api_handler<Debugger,
                          &runtime_i64_binary<&opcode_signed_le<std::uint64_t>, true>>; // i64.le_s
  handlers[0x58] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::less_equal<>{}, true>>; // i64.le_u
  handlers[0x59] =
      runtime_api_handler<Debugger,
                          &runtime_i64_binary<&opcode_signed_ge<std::uint64_t>, true>>; // i64.ge_s
  handlers[0x5A] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::greater_equal<>{}, true>>; // i64.ge_u
  handlers[0x5B] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::equal_to<>{}, true>>; // f32.eq
  handlers[0x5C] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::not_equal_to<>{}, true>>; // f32.ne
  handlers[0x5D] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::less<>{}, true>>; // f32.lt
  handlers[0x5E] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::greater<>{}, true>>; // f32.gt
  handlers[0x5F] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::less_equal<>{}, true>>; // f32.le
  handlers[0x60] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::greater_equal<>{}, true>>; // f32.ge
  handlers[0x61] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::equal_to<>{}, true>>; // f64.eq
  handlers[0x62] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::not_equal_to<>{}, true>>; // f64.ne
  handlers[0x63] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::less<>{}, true>>; // f64.lt
  handlers[0x64] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::greater<>{}, true>>; // f64.gt
  handlers[0x65] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::less_equal<>{}, true>>; // f64.le
  handlers[0x66] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::greater_equal<>{}, true>>; // f64.ge
  handlers[0x67] =
      runtime_api_handler<Debugger, &runtime_i32_unary<&opcode_clz<std::uint32_t>>>; // i32.clz
  handlers[0x68] =
      runtime_api_handler<Debugger, &runtime_i32_unary<&opcode_ctz<std::uint32_t>>>; // i32.ctz
  handlers[0x69] =
      runtime_api_handler<Debugger,
                          &runtime_i32_unary<&opcode_popcount<std::uint32_t>>>;        // i32.popcnt
  handlers[0x6A] = runtime_api_handler<Debugger, &runtime_i32_binary<std::plus<>{}>>;  // i32.add
  handlers[0x6B] = runtime_api_handler<Debugger, &runtime_i32_binary<std::minus<>{}>>; // i32.sub
  handlers[0x6C] =
      runtime_api_handler<Debugger, &runtime_i32_binary<std::multiplies<>{}>>; // i32.mul
  handlers[0x6D] = runtime_api_handler<
      Debugger,
      &runtime_i32_checked_binary<&opcode_div_signed<std::uint32_t>>>; // i32.div_s
  handlers[0x6E] = runtime_api_handler<
      Debugger, &runtime_i32_checked_binary<&opcode_div_unsigned<std::uint32_t>>>; // i32.div_u
  handlers[0x6F] = runtime_api_handler<
      Debugger,
      &runtime_i32_checked_binary<&opcode_rem_signed<std::uint32_t>>>; // i32.rem_s
  handlers[0x70] = runtime_api_handler<
      Debugger, &runtime_i32_checked_binary<&opcode_rem_unsigned<std::uint32_t>>>; // i32.rem_u
  handlers[0x71] = runtime_api_handler<Debugger, &runtime_i32_binary<std::bit_and<>{}>>; // i32.and
  handlers[0x72] = runtime_api_handler<Debugger, &runtime_i32_binary<std::bit_or<>{}>>;  // i32.or
  handlers[0x73] = runtime_api_handler<Debugger, &runtime_i32_binary<std::bit_xor<>{}>>; // i32.xor
  handlers[0x74] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<&opcode_shift_left<std::uint32_t>>>; // i32.shl
  handlers[0x75] = runtime_api_handler<
      Debugger, &runtime_i32_binary<&opcode_shift_right_signed<std::uint32_t>>>; // i32.shr_s
  handlers[0x76] = runtime_api_handler<
      Debugger, &runtime_i32_binary<&opcode_shift_right_unsigned<std::uint32_t>>>; // i32.shr_u
  handlers[0x77] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<&opcode_rotate_left<std::uint32_t>>>; // i32.rotl
  handlers[0x78] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<&opcode_rotate_right<std::uint32_t>>>; // i32.rotr
  handlers[0x79] =
      runtime_api_handler<Debugger,
                          &runtime_i64_unary<&opcode_clz<std::uint64_t>, false>>; // i64.clz
  handlers[0x7A] =
      runtime_api_handler<Debugger,
                          &runtime_i64_unary<&opcode_ctz<std::uint64_t>, false>>; // i64.ctz
  handlers[0x7B] =
      runtime_api_handler<Debugger,
                          &runtime_i64_unary<&opcode_popcount<std::uint64_t>, false>>; // i64.popcnt
  handlers[0x7C] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::plus<>{}, false>>; // i64.add
  handlers[0x7D] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::minus<>{}, false>>; // i64.sub
  handlers[0x7E] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::multiplies<>{}, false>>; // i64.mul
  handlers[0x7F] =
      runtime_api_handler<Debugger, &runtime_i64_checked_binary<&opcode_div_signed<std::uint64_t>,
                                                                false>>; // i64.div_s
  handlers[0x80] =
      runtime_api_handler<Debugger, &runtime_i64_checked_binary<&opcode_div_unsigned<std::uint64_t>,
                                                                false>>; // i64.div_u
  handlers[0x81] =
      runtime_api_handler<Debugger, &runtime_i64_checked_binary<&opcode_rem_signed<std::uint64_t>,
                                                                false>>; // i64.rem_s
  handlers[0x82] =
      runtime_api_handler<Debugger, &runtime_i64_checked_binary<&opcode_rem_unsigned<std::uint64_t>,
                                                                false>>; // i64.rem_u
  handlers[0x83] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::bit_and<>{}, false>>; // i64.and
  handlers[0x84] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::bit_or<>{}, false>>; // i64.or
  handlers[0x85] =
      runtime_api_handler<Debugger, &runtime_i64_binary<std::bit_xor<>{}, false>>; // i64.xor
  handlers[0x86] =
      runtime_api_handler<Debugger,
                          &runtime_i64_binary<&opcode_shift_left<std::uint64_t>, false>>; // i64.shl
  handlers[0x87] =
      runtime_api_handler<Debugger, &runtime_i64_binary<&opcode_shift_right_signed<std::uint64_t>,
                                                        false>>; // i64.shr_s
  handlers[0x88] =
      runtime_api_handler<Debugger, &runtime_i64_binary<&opcode_shift_right_unsigned<std::uint64_t>,
                                                        false>>; // i64.shr_u
  handlers[0x89] =
      runtime_api_handler<Debugger, &runtime_i64_binary<&opcode_rotate_left<std::uint64_t>,
                                                        false>>; // i64.rotl
  handlers[0x8A] =
      runtime_api_handler<Debugger, &runtime_i64_binary<&opcode_rotate_right<std::uint64_t>,
                                                        false>>; // i64.rotr
  handlers[0x8B] =
      runtime_api_handler<Debugger, &runtime_f32_unary<&opcode_float_abs<float>>>; // f32.abs
  handlers[0x8C] =
      runtime_api_handler<Debugger, &runtime_f32_unary<&opcode_float_neg<float>>>; // f32.neg
  handlers[0x8D] =
      runtime_api_handler<Debugger, &runtime_f32_unary<&opcode_float_ceil<float>>>; // f32.ceil
  handlers[0x8E] =
      runtime_api_handler<Debugger, &runtime_f32_unary<&opcode_float_floor<float>>>; // f32.floor
  handlers[0x8F] =
      runtime_api_handler<Debugger, &runtime_f32_unary<&opcode_float_trunc<float>>>; // f32.trunc
  handlers[0x90] =
      runtime_api_handler<Debugger,
                          &runtime_f32_unary<&opcode_float_nearest<float>>>; // f32.nearest
  handlers[0x91] =
      runtime_api_handler<Debugger, &runtime_f32_unary<&opcode_float_sqrt<float>>>; // f32.sqrt
  handlers[0x92] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::plus<>{}, false>>; // f32.add
  handlers[0x93] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::minus<>{}, false>>; // f32.sub
  handlers[0x94] =
      runtime_api_handler<Debugger, &runtime_f32_binary<std::multiplies<>{}, false>>; // f32.mul
  handlers[0x95] =
      runtime_api_handler<Debugger,
                          &runtime_f32_binary<&opcode_float_div<float>, false>>; // f32.div
  handlers[0x96] =
      runtime_api_handler<Debugger,
                          &runtime_f32_binary<&opcode_float_min<float>, false>>; // f32.min
  handlers[0x97] =
      runtime_api_handler<Debugger,
                          &runtime_f32_binary<&opcode_float_max<float>, false>>; // f32.max
  handlers[0x98] = runtime_api_handler<Debugger, &runtime_f32_binary<&opcode_float_copysign<float>,
                                                                     false>>; // f32.copysign
  handlers[0x99] =
      runtime_api_handler<Debugger, &runtime_f64_unary<&opcode_float_abs<double>>>; // f64.abs
  handlers[0x9A] =
      runtime_api_handler<Debugger, &runtime_f64_unary<&opcode_float_neg<double>>>; // f64.neg
  handlers[0x9B] =
      runtime_api_handler<Debugger, &runtime_f64_unary<&opcode_float_ceil<double>>>; // f64.ceil
  handlers[0x9C] =
      runtime_api_handler<Debugger, &runtime_f64_unary<&opcode_float_floor<double>>>; // f64.floor
  handlers[0x9D] =
      runtime_api_handler<Debugger, &runtime_f64_unary<&opcode_float_trunc<double>>>; // f64.trunc
  handlers[0x9E] =
      runtime_api_handler<Debugger,
                          &runtime_f64_unary<&opcode_float_nearest<double>>>; // f64.nearest
  handlers[0x9F] =
      runtime_api_handler<Debugger, &runtime_f64_unary<&opcode_float_sqrt<double>>>; // f64.sqrt
  handlers[0xA0] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::plus<>{}, false>>; // f64.add
  handlers[0xA1] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::minus<>{}, false>>; // f64.sub
  handlers[0xA2] =
      runtime_api_handler<Debugger, &runtime_f64_binary<std::multiplies<>{}, false>>; // f64.mul
  handlers[0xA3] =
      runtime_api_handler<Debugger,
                          &runtime_f64_binary<&opcode_float_div<double>, false>>; // f64.div
  handlers[0xA4] =
      runtime_api_handler<Debugger,
                          &runtime_f64_binary<&opcode_float_min<double>, false>>; // f64.min
  handlers[0xA5] =
      runtime_api_handler<Debugger,
                          &runtime_f64_binary<&opcode_float_max<double>, false>>; // f64.max
  handlers[0xA6] = runtime_api_handler<Debugger, &runtime_f64_binary<&opcode_float_copysign<double>,
                                                                     false>>; // f64.copysign
  handlers[0xA7] =
      runtime_api_handler<Debugger, &runtime_conversion<conversion_i32_wrap_i64>>; // i32.wrap_i64
  handlers[0xA8] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i32_trunc_f32_s>>; // i32.trunc_f32_s
  handlers[0xA9] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i32_trunc_f32_u>>; // i32.trunc_f32_u
  handlers[0xAA] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i32_trunc_f64_s>>; // i32.trunc_f64_s
  handlers[0xAB] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i32_trunc_f64_u>>; // i32.trunc_f64_u
  handlers[0xAC] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i64_extend_i32_s>>; // i64.extend_i32_s
  handlers[0xAD] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i64_extend_i32_u>>; // i64.extend_i32_u
  handlers[0xAE] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i64_trunc_f32_s>>; // i64.trunc_f32_s
  handlers[0xAF] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i64_trunc_f32_u>>; // i64.trunc_f32_u
  handlers[0xB0] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i64_trunc_f64_s>>; // i64.trunc_f64_s
  handlers[0xB1] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_i64_trunc_f64_u>>; // i64.trunc_f64_u
  handlers[0xB2] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f32_convert_i32_s>>; // f32.convert_i32_s
  handlers[0xB3] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f32_convert_i32_u>>; // f32.convert_i32_u
  handlers[0xB4] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f32_convert_i64_s>>; // f32.convert_i64_s
  handlers[0xB5] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f32_convert_i64_u>>; // f32.convert_i64_u
  handlers[0xB6] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f32_demote_f64>>; // f32.demote_f64
  handlers[0xB7] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f64_convert_i32_s>>; // f64.convert_i32_s
  handlers[0xB8] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f64_convert_i32_u>>; // f64.convert_i32_u
  handlers[0xB9] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f64_convert_i64_s>>; // f64.convert_i64_s
  handlers[0xBA] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f64_convert_i64_u>>; // f64.convert_i64_u
  handlers[0xBB] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_f64_promote_f32>>; // f64.promote_f32
  handlers[0xBC] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_identity>>; // i32.reinterpret_f32
  handlers[0xBD] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_identity>>; // i64.reinterpret_f64
  handlers[0xBE] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_identity>>; // f32.reinterpret_i32
  handlers[0xBF] =
      runtime_api_handler<Debugger,
                          &runtime_conversion<conversion_identity>>; // f64.reinterpret_i64
  handlers[0xC0] = runtime_api_handler<
      Debugger, &runtime_i32_unary<&opcode_extend8_signed<std::uint32_t>>>; // i32.extend8_s
  handlers[0xC1] = runtime_api_handler<
      Debugger, &runtime_i32_unary<&opcode_extend16_signed<std::uint32_t>>>; // i32.extend16_s
  handlers[0xC2] =
      runtime_api_handler<Debugger, &runtime_i64_unary<&opcode_extend8_signed<std::uint64_t>,
                                                       false>>; // i64.extend8_s
  handlers[0xC3] =
      runtime_api_handler<Debugger, &runtime_i64_unary<&opcode_extend16_signed<std::uint64_t>,
                                                       false>>; // i64.extend16_s
  handlers[0xC4] =
      runtime_api_handler<Debugger,
                          &runtime_i64_unary<&opcode_extend32_signed, false>>; // i64.extend32_s
  handlers[kOpcodeFcPrefix] = &h_fc_dispatch<Debugger>;
  return handlers;
}

template <typename Debugger>
inline constexpr auto kOpcodeHandlers = make_opcode_handlers<Debugger>();

template <typename Debugger>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
dispatch_next(execution_context *context, const std::uint8_t *ip, std::uint32_t *sp,
              const std::uint8_t *rel) {
  auto &current = *context;
  assert(current.code != nullptr && ip >= current.code && ip <= current.code + current.code_size);
  const auto ip_offset = instruction_offset(current, ip);
  assert(ip_offset < current.code_size);
  if constexpr (std::is_same_v<Debugger, block_step_aspect>) {
    if ((current.runtime_flags & kStopAtBlockBoundaryFlag) != 0 &&
        is_requested_block_boundary(current, ip_offset)) {
      current.stack_pointer = sp;
      block_boundary(current, ip_offset);
      return;
    }
  }

  if constexpr (std::is_same_v<Debugger, debugger_aspect>) {
    if (Debugger::before_instruction(current, ip)) {
      current.ip = ip_offset;
      current.rel = rel;
      current.stack_pointer = sp;
      debug_stop(current);
      return;
    }
  }

  const auto opcode = *ip;
  [[clang::musttail]] return kOpcodeHandlers<Debugger>[opcode](context, ip, sp, rel);
}

} // namespace

// Native dispatch orchestration and Runtime API entry adapters.
// Depends on utilities and the opcode handler tables.
namespace fireball::interpreter {

constexpr std::uint32_t kNoPc = 0xFFFF'FFFFu;
constexpr block_index_t kNoBlockIndex = FIREBALL_INVALID_BLOCK_INDEX;

#if FB_CONF_JIT_BLOCK_COUNTERS
constexpr bool kCollectJitBlockCounters = true;
#else
constexpr bool kCollectJitBlockCounters = false;
#endif

template <bool CollectStats, bool WithJit, bool WithExtension, bool ReturnAtBlockBoundary,
          typename Debugger>
inline constexpr bool kDispatchAtControlBoundary =
    CollectStats || WithJit || WithExtension || !std::is_void_v<Debugger> ||
    ReturnAtBlockBoundary || kCollectJitBlockCounters;

struct native_dispatch_stats {
  std::uint32_t trace_count = 0;
  std::uint32_t body_count = 0;
  std::uint32_t dispatcher_trace_transitions = 0;
  std::uint32_t control_handler_count = 0;
  std::uint32_t interpreted_block_count = 0;
  std::uint32_t block_search_cache_hits = 0;
  std::uint32_t block_search_cache_misses = 0;
  bool control_handler_pending_trace = false;
};

struct native_dispatch_observations {
  std::uint32_t eligible_block_visits = 0;
};

struct empty_dispatch_metrics {};

// Clang 18 miscompiles the no-stats extension variant when these empty members
// use
// [[no_unique_address]], so keep their distinct storage.
template <bool CollectStats, bool WithExtension> struct native_dispatch_metrics {
  std::conditional_t<CollectStats, native_dispatch_stats, empty_dispatch_metrics> stats;
  std::conditional_t<WithExtension, native_dispatch_observations, empty_dispatch_metrics>
      observations;
};

template <bool WithExtension>
inline const fb_native_dispatch_call &
base_dispatch_input(const dispatch_input_t<WithExtension> &input) {
  if constexpr (WithExtension) {
    return input.call;
  } else {
    return input;
  }
}

template <bool WithExtension>
inline const fb_native_execution_extension *
execution_extension(const dispatch_input_t<WithExtension> &input) {
  if constexpr (WithExtension) {
    return input.extension;
  } else {
    return nullptr;
  }
}

template <bool CollectStats, bool WithJit, bool WithExtension, bool ReturnAtBlockBoundary,
          typename ResultType = fb_native_result>
struct native_dispatch_state {
  static_assert(!CollectStats || !std::is_same_v<ResultType, fb_native_result>);
  const fb_native_dispatch_call &input;
  const fb_native_execution_extension *extension;
  const fb_native_jit_provider *jit_provider;
  execution_context &context;
  std::uint32_t status = kNativeErrorInternal;
  std::uint32_t trap_code = 0;
  [[no_unique_address]] native_dispatch_metrics<
      CollectStats, WithExtension && !std::is_same_v<ResultType, fb_native_result>> metrics;
};

template <bool CollectStats, bool WithJit, bool WithExtension, bool ReturnAtBlockBoundary,
          typename ResultType>
inline void
prepare_native_dispatch(native_dispatch_state<CollectStats, WithJit, WithExtension,
                                              ReturnAtBlockBoundary, ResultType> &state) {
  [[maybe_unused]] const auto &input = state.input;
  auto &context = state.context;
  assert(input.stack != nullptr && input.locals != nullptr);
  assert(context.call_stack != nullptr && context.call_stack->size != 0 &&
         context.call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(context.stack_base == input.stack);
  assert(context.stack_pointer >= context.stack_base &&
         context.stack_pointer <= context.stack_limit);
  assert(context.loop_jump_threshold != 0 && context.code != nullptr);
  assert(context.local_stack_base == input.locals);
  assert(context.local_stack_pointer >= context.local_stack_base &&
         context.local_stack_pointer <= context.local_stack_limit);
  const auto &frame = active_frame(context);
  const auto &function = *frame.function_view;
  assert(frame.local_base >= context.local_stack_base);
  assert(frame.local_base + function.local_slot_count <= context.local_stack_limit);
  assert(context.ip <= context.code_size);
  if ((context.runtime_flags & kPendingBlockHeadFlag) != 0 && context.ip < context.code_size) {
    assert(function.module_view != nullptr && function.module_view->blocks != nullptr);
    assert(context.current_block_index < function.module_view->block_count);
    assert(function.module_view->blocks[context.current_block_index].head_pc ==
           function.code_pc_offset + context.ip);
  }
  context.code = function.code;
  context.code_size = function.code_size;
  context.local_base = frame.local_base;
}

template <bool CollectStats, bool WithExtension, bool ReturnAtBlockBoundary, typename ResultType>
inline std::uint32_t
execute_jit_trace(native_dispatch_state<CollectStats, true, WithExtension, ReturnAtBlockBoundary,
                                        ResultType> &state,
                  fireball_call_frame_native &frame, block_index_t block_index, wasm_pc_t pc,
                  const std::uint8_t *&dispatch_rel) {
  auto &context = state.context;
  fb_native_jit_trace trace{};
  const auto lookup =
      state.jit_provider->lookup(state.jit_provider->owner, block_index, pc, &trace);
  assert(lookup == 0 || lookup == 1);
  if (lookup == 0) {
    return 0;
  }
  assert(trace.entry_address != 0 && trace.rel != nullptr && trace.body_count != 0);
  const auto *trace_exit = reinterpret_cast<const fb_native_jit_trace_exit *>(
      trace.rel - sizeof(fb_native_jit_trace_exit));
  const auto *module = frame.function_view->module_view;
  assert(module != nullptr && block_index < module->block_count);
  [[maybe_unused]] const auto &source_block = module->blocks[block_index];
  assert(source_block.head_pc == pc);
  if (trace.frame_depth != source_block.frame_depth) {
    return 0;
  }
  assert(source_block.next_pc == trace_exit->next_pc);
  assert(source_block.next_block_index == trace_exit->block_index);
  if (trace_exit->block_index != kNoBlockIndex) {
    assert(trace_exit->block_index < module->block_count);
    assert(module->blocks[trace_exit->block_index].head_pc == trace_exit->next_pc);
  }
  // The function entry reserved its loader-proven peak, including this trace.
  assert(trace.stack_words <=
         static_cast<std::uint32_t>(context.stack_limit - context.stack_pointer));
  context.local_base = frame.local_base;
  context.code = frame.function_view->code;
  context.code_size = frame.function_view->code_size;
  context.runtime_flags &= ~fb_native_cps_exit_mask;
  // Control Runtime APIs consume the loader's static edge for this block.
  frame.boundary_next_pc = trace_exit->next_pc;
  frame.boundary_loops_to = trace_exit->loops_to;
  const auto *previous_rel = context.rel;
  context.rel = trace.rel;
  const auto completed = reinterpret_cast<fireball_native_jit_trace_entry>(trace.entry_address)(
      &context, context.code + context.ip, context.stack_pointer, trace.rel);
  if constexpr (WithExtension) {
    state.extension->trace_executed(&state.input, trace.trace_cookie);
  }
  if (!completed) {
    assert(context.trap_code != 0 || cps_exit_pending(context));
    context.rel = previous_rel;
    if (cps_exit_pending(context) && cps_exit_kind(context) == kRuntimeApiBoundary) {
      if (context.ip == trace_exit->body_end_ip) {
        context.rel =
            interpreter_rel_after_jit_trace(frame, block_index, trace_exit->interpreter_rel_bytes);
        context.current_block_index = trace_exit->block_index;
      }
    }
    return trace.body_count;
  }
  assert(context.trap_code == 0 && !cps_exit_pending(context));
  const auto *completed_trace_exit =
      reinterpret_cast<const fb_native_jit_trace_exit *>(context.rel);
  assert(completed_trace_exit != nullptr);
  assert(completed_trace_exit->body_end_ip <= frame.function_view->code_size);
  assert(context.ip == completed_trace_exit->body_end_ip);
  const auto completed_block_index = context.current_block_index;
  assert(completed_block_index < module->block_count);
  const auto &completed_block = module->blocks[completed_block_index];
  assert(completed_block.function_index == frame.function_view->function_index);
  assert(completed_trace_exit->next_pc == completed_block.next_pc);
  assert(completed_trace_exit->block_index == completed_block.next_block_index);
  frame.boundary_next_pc = completed_trace_exit->next_pc;
  frame.boundary_loops_to = completed_trace_exit->loops_to;
  assert(completed_trace_exit->result_words <=
         static_cast<std::uint32_t>(context.stack_limit - context.stack_pointer));
  context.stack_pointer += completed_trace_exit->result_words;
  const auto branch_terminator_pending =
      context.ip < context.code_size &&
      (context.code[context.ip] == 0x04 || context.code[context.ip] == 0x0C ||
       context.code[context.ip] == 0x0D);
  // The Interpreter CPS handler still needs the source block for an unexecuted
  // branch terminator after the trace has returned.
  if (!branch_terminator_pending) {
    context.current_block_index = completed_trace_exit->block_index;
  }
  dispatch_rel = interpreter_rel_after_jit_trace(frame, completed_block_index,
                                                 completed_trace_exit->interpreter_rel_bytes);
  context.rel = dispatch_rel;
  return trace.body_count;
}

template <bool CollectStats, bool WithJit, bool WithExtension, bool ReturnAtBlockBoundary = false,
          typename ResultType = fb_native_result>
inline void
record_dispatch_segment_stats(native_dispatch_state<CollectStats, WithJit, WithExtension,
                                                    ReturnAtBlockBoundary, ResultType> &state,
                              std::uint32_t executed_bodies) {
  if constexpr (CollectStats) {
    if (executed_bodies == 0) {
      ++state.metrics.stats.interpreted_block_count;
    } else {
      ++state.metrics.stats.trace_count;
      state.metrics.stats.body_count += executed_bodies;
      if (state.metrics.stats.control_handler_pending_trace) {
        ++state.metrics.stats.dispatcher_trace_transitions;
        state.metrics.stats.control_handler_pending_trace = false;
      }
    }
  }
}

template <bool CollectStats, bool WithJit, bool WithExtension, bool ReturnAtBlockBoundary,
          typename Debugger, typename ResultType>
inline bool handle_runtime_exit(native_dispatch_state<CollectStats, WithJit, WithExtension,
                                                      ReturnAtBlockBoundary, ResultType> &state,
                                std::uint32_t exit_kind) {
  auto &context = state.context;
  switch (exit_kind) {
  case kDebugStop:
    context.runtime_flags &= ~kPendingBlockHeadFlag;
    if constexpr (!std::is_void_v<Debugger>) {
      state.status = kDebugStop;
      return false;
    } else {
      assert(false && "debug stop requires a debugger handler table");
      __builtin_unreachable();
    }
  case kCallBoundary: {
    // The Runtime API installed the callee frame. Resolve its entry BasicBlock
    // here so block metadata and the matching rel cursor are set together.
    context.runtime_flags |= kPendingBlockHeadFlag;
    if constexpr (CollectStats) {
      ++state.metrics.stats.control_handler_count;
    }
    if constexpr (CollectStats) {
      state.metrics.stats.control_handler_pending_trace = true;
    }
    if (context.loop_jump_count >= context.loop_jump_threshold) {
      state.status = kDispatchYield;
      return false;
    }
    if constexpr (ReturnAtBlockBoundary) {
      state.status = kNativeCallBoundary;
      return false;
    }
    return true;
  }
  case kTrap:
    context.runtime_flags &= ~kPendingBlockHeadFlag;
    state.trap_code = context.trap_code;
    state.status = kTrap;
    return false;
  case kRuntimeApiBoundary:
    context.runtime_flags &= ~kPendingBlockHeadFlag;
    state.status = kRuntimeApiBoundary;
    return false;
  case kComplete:
    context.runtime_flags &= ~kPendingBlockHeadFlag;
    context.ip = kNoPc;
    state.status = kComplete;
    return false;
  case kBlockBoundary: {
    context.runtime_flags |= kPendingBlockHeadFlag;
    if constexpr (ReturnAtBlockBoundary) {
      state.status = kBlockBoundary;
      return false;
    }
    if constexpr (CollectStats) {
      ++state.metrics.stats.control_handler_count;
      state.metrics.stats.control_handler_pending_trace = true;
    }
    if (context.loop_jump_count >= context.loop_jump_threshold) {
      state.status = kDispatchYield;
      return false;
    }
    return true;
  }
  default:
    assert(false && "unknown CPS exit kind");
    __builtin_unreachable();
  }
}

template <bool CollectStats, bool WithJit, bool WithExtension, bool ReturnAtBlockBoundary,
          typename Debugger, typename ResultType>
int run_native_dispatch_abi(const dispatch_input_t<WithExtension> *input, ResultType *result) {
  assert(result != nullptr && input != nullptr);
  const auto &call = base_dispatch_input<WithExtension>(*input);
  const auto *extension = execution_extension<WithExtension>(*input);
  assert(call.context != nullptr);
  *result = {};

  if constexpr (WithExtension) {
    assert(extension != nullptr);
    assert(extension->observe != nullptr);
    assert(extension->trace_executed != nullptr);
  } else {
    assert(extension == nullptr);
  }
  const auto *jit_provider = call.jit_provider;
  if constexpr (WithJit) {
    assert(jit_provider != nullptr && jit_provider->owner != 0);
    assert(jit_provider->lookup != nullptr && jit_provider->observe_block != nullptr &&
           jit_provider->finish_boundary != nullptr);
    assert(jit_provider->card_shift < 32 &&
           (jit_provider->card_count == 0 || jit_provider->card_states != nullptr));
  } else {
    assert(jit_provider == nullptr);
  }

  if constexpr (!std::is_void_v<Debugger>) {
    [[maybe_unused]] auto *debug = static_cast<debug_context *>(call.context);
    assert(debug != nullptr && debug->control != nullptr);
    assert(debug->control->breakpoint_count == 0 || debug->control->breakpoints != nullptr);
  }
  auto &context = *static_cast<execution_context *>(call.context);
  native_dispatch_state<CollectStats, WithJit, WithExtension, ReturnAtBlockBoundary, ResultType>
      state{call, extension, jit_provider, context, kNativeErrorInternal, 0, {}};
  prepare_native_dispatch(state);
  // Boundary overrides are valid only within one Runtime dispatch.
  for (frame_depth_t index = 0; index < context.call_stack->size; ++index) {
    auto &frame = context.call_stack->frames[index];
    frame.boundary_next_pc = kNoPc;
    frame.boundary_loops_to = kNoPc;
  }

  const auto previous_runtime_flags = context.runtime_flags;
  if (context.ip == 0) {
    context.runtime_flags |= kPendingBlockHeadFlag;
  }
  context.runtime_flags &= ~kStopAtBlockBoundaryFlag;
  if constexpr (kDispatchAtControlBoundary<CollectStats, WithJit, WithExtension,
                                           ReturnAtBlockBoundary, Debugger>) {
    // Stats, JIT lookup, QA measurements, debugging, and block counters consume
    // each boundary.
    context.runtime_flags |= kStopAfterControlFlag;
  } else {
    // Production dispatch stays in CPS until an exit or the loop threshold.
    context.runtime_flags &= ~kStopAfterControlFlag;
  }
  while (true) {
    auto &frame = active_frame(context);
    std::uint32_t executed_bodies = 0;
    const std::uint8_t *dispatch_rel = nullptr;
    auto block_index = (context.runtime_flags & kPendingBlockHeadFlag) != 0
                           ? context.current_block_index
                           : kNoBlockIndex;
    const auto ip = context.ip;
    const auto block_head =
        (context.runtime_flags & kPendingBlockHeadFlag) != 0 && ip < context.code_size;
    if constexpr (std::is_void_v<Debugger>) {
      if constexpr (WithJit) {
        if (block_head && opcode_is_block_body(context.code[ip])) {
          const auto pc = frame.function_view->code_pc_offset + ip;
          const auto card = pc >> state.jit_provider->card_shift;
          assert(card < state.jit_provider->card_count &&
                 state.jit_provider->card_states != nullptr);
          const auto card_state =
              (state.jit_provider->card_states[card >> 2] >> ((card & 3u) * 2u)) & 3u;
          if (card_state == 3u) {
            executed_bodies =
                execute_jit_trace<CollectStats, WithExtension, ReturnAtBlockBoundary, ResultType>(
                    state, frame, block_index, pc, dispatch_rel);
          }
        }
      }
    }
    record_dispatch_segment_stats(state, executed_bodies);
    if (executed_bodies != 0 && cps_exit_pending(context)) {
      // A JIT terminal Runtime API already performed the operation and set the
      // interpreter continuation. Resume at the next dispatch iteration.
    } else if (executed_bodies != 0 && context.trap_code != 0) {
      record_cps_exit(context, kTrap);
    } else if (context.ip >= context.code_size) {
      complete(context);
    } else {
      clear_cps_exit(context);
      const auto interpreter_ip = context.ip;
      if (executed_bodies == 0 && (context.runtime_flags & kPendingBlockHeadFlag) != 0) {
        block_index = context.current_block_index;
        assert(frame.function_view->module_view != nullptr &&
               block_index < frame.function_view->module_view->block_count);
        assert(frame.function_view->module_view->blocks[block_index].head_pc ==
               frame.function_view->code_pc_offset + interpreter_ip);
      }
#if FB_CONF_JIT_BLOCK_COUNTERS
      if (executed_bodies == 0 && (context.runtime_flags & kPendingBlockHeadFlag) != 0) {
        assert(block_index != kNoBlockIndex);
        fb_native_record_block_execution(frame.function_view->module_view, block_index);
      }
#endif
      [[maybe_unused]] bool observed_interpreter_body = false;
      if constexpr (WithExtension || WithJit) {
        observed_interpreter_body = executed_bodies == 0 &&
                                    (context.runtime_flags & kPendingBlockHeadFlag) != 0 &&
                                    interpreter_ip < context.code_size &&
                                    opcode_is_block_body(context.code[interpreter_ip]);
      }
      const std::uint8_t *rel = dispatch_rel;
      if (executed_bodies == 0) {
        if ((context.runtime_flags & kPendingBlockHeadFlag) != 0) {
          assert(block_index != kNoBlockIndex);
          context.rel = interpreter_rel_for_block_index(frame, block_index);
        }
        rel = context.rel;
      }
      // Every terminal handler writes a fresh CPS exit kind.
      dispatch_next<Debugger>(&context, context.code + context.ip, context.stack_pointer, rel);
      const auto pc = frame.function_view->code_pc_offset + interpreter_ip;
      if constexpr (WithJit) {
        if (observed_interpreter_body) {
          [[maybe_unused]] const auto observed =
              state.jit_provider->observe_block(state.jit_provider->owner, block_index, pc);
          assert(observed == 0 || observed == 1);
        }
      }
      if constexpr (WithExtension) {
        if (observed_interpreter_body && state.extension->observe(&state.input, pc)) {
          if constexpr (!std::is_same_v<ResultType, fb_native_result>) {
            ++state.metrics.observations.eligible_block_visits;
          }
        }
      }
    }
    const auto exit_kind = cps_exit_kind(context);
    // Keep JIT targets for the Runtime API operation that resumes this segment.
    if constexpr (WithJit) {
      if (executed_bodies != 0 && exit_kind != kRuntimeApiBoundary) {
        frame.boundary_next_pc = kNoPc;
        frame.boundary_loops_to = kNoPc;
      }
    }
    // Every stopped iteration has a WASM execution status in the shared state.
    if (!handle_runtime_exit<CollectStats, WithJit, WithExtension, ReturnAtBlockBoundary, Debugger,
                             ResultType>(state, exit_kind)) {
      break;
    }
  }

  context.runtime_flags = (previous_runtime_flags & ~kPendingBlockHeadFlag) |
                          (context.runtime_flags & kPendingBlockHeadFlag);
  result->status = state.status;
  // Internal block boundaries stay in this loop; publish the stack only at the
  // Runtime handoff.
  context.trap_code = state.trap_code;
  if constexpr (CollectStats) {
    result->trace_count = state.metrics.stats.trace_count;
    result->body_count = state.metrics.stats.body_count;
    result->dispatcher_trace_transitions = state.metrics.stats.dispatcher_trace_transitions;
    result->control_handler_count = state.metrics.stats.control_handler_count;
    result->interpreted_block_count = state.metrics.stats.interpreted_block_count;
    result->block_search_cache_hits = state.metrics.stats.block_search_cache_hits;
    result->block_search_cache_misses = state.metrics.stats.block_search_cache_misses;
  }
  if constexpr (WithExtension && !std::is_same_v<ResultType, fb_native_result>) {
    result->eligible_block_visits = state.metrics.observations.eligible_block_visits;
  }
  if constexpr (WithJit) {
    [[maybe_unused]] const auto finished =
        jit_provider->finish_boundary(jit_provider->owner, result->status, call.idle_budget);
    assert(finished == 1);
  }
  return 1;
}

int run_native_step_abi(const fb_native_step_call *call, fb_native_result *output,
                        bool direct_control) {
  assert(call != nullptr && output != nullptr);
  *output = {};
  assert(call->code != nullptr && call->context != nullptr);
  assert(call->stack != nullptr && call->locals != nullptr);
  assert(call->code_bytes <= std::numeric_limits<std::uint32_t>::max());
  assert(call->stack_capacity <= 128 && call->stack_size <= call->stack_capacity);
  assert(call->context_bytes >= sizeof(fireball_execution_context_native));
  assert(call->stack_bytes >= 128 * sizeof(std::uint32_t));
  assert(call->locals_bytes >=
         static_cast<std::uint64_t>(call->local_slots) * sizeof(std::uint32_t));
  assert(call->ip < call->code_bytes);

  const auto *raw_code = call->code;
  auto *execution_context = static_cast<fireball_execution_context_native *>(call->context);
  auto *stack = call->stack;
  auto *locals = call->locals;
  execution_context->stack_base = stack;
  execution_context->stack_pointer = stack + call->stack_size;
  execution_context->stack_limit = stack + call->stack_capacity;
  execution_context->local_stack_base = locals;
  execution_context->local_stack_limit = locals + call->locals_bytes / sizeof(std::uint32_t);
  assert(execution_context->call_stack != nullptr);
  assert(execution_context->call_stack->size != 0);
  assert(execution_context->call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(execution_context->call_frame_base != nullptr);
  assert(execution_context->call_frame_base >= execution_context->call_stack->frames &&
         execution_context->call_frame_base <
             execution_context->call_stack->frames + execution_context->call_stack->size);

  execution_context->ip = call->ip;
  execution_context->code = raw_code;
  execution_context->code_size = static_cast<std::uint32_t>(call->code_bytes);

  execution_context->trap_code = 0;
  const auto &active =
      execution_context->call_stack->frames[execution_context->call_stack->size - 1];
  assert(active.function_view != nullptr);
  auto *local_base = active.local_base;
  auto *local_stack_pointer = active.local_base + active.function_view->local_slot_count;
  assert(local_stack_pointer <= execution_context->local_stack_limit);
  execution_context->local_stack_pointer = local_stack_pointer;
  execution_context->local_base = local_base;
  auto *sp = execution_context->stack_pointer;
  clear_cps_exit(*execution_context);
  if (direct_control) {
    assert(opcode_is_control_terminator(raw_code[call->ip]) ||
           raw_code[call->ip] == kOpcodeFcPrefix);
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    const auto previous_flags = execution_context->runtime_flags;
    execution_context->runtime_flags |= kStopAfterControlFlag;
    (void)dispatch_next<block_step_aspect>(execution_context, raw_code + call->ip, sp, call->rel);
    execution_context->runtime_flags =
        previous_flags | (execution_context->runtime_flags & kCpsExitMask);
  } else {
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    (void)dispatch_next<block_step_aspect>(execution_context, raw_code + call->ip, sp, call->rel);
  }
  const auto exit_kind = cps_exit_kind(*execution_context);
  switch (exit_kind) {
  case kRuntimeApiBoundary:
    output->status = kRuntimeApiBoundary;
    break;
  case kBlockBoundary:
    execution_context->runtime_flags |= kPendingBlockHeadFlag;
    output->status = kBlockBoundary;
    break;
  case kTrap:
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    output->status = kTrap;
    break;
  case kComplete:
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    execution_context->ip = execution_context->code_size;
    output->status = kComplete;
    break;
  default:
    assert(false && "single-step CPS returned at an invalid execution boundary");
    __builtin_unreachable();
  }
  clear_cps_exit(*execution_context);
  return 1;
}

} // namespace fireball::interpreter

#ifdef FB_INTERPRETER_MEASURED_RESULT
namespace fireball::interpreter {
// QA owns the result type. Only the QA build emits these dispatcher variants.
template int
run_native_dispatch_abi<false, false, false, false, void, FB_INTERPRETER_MEASURED_RESULT>(
    const fb_native_dispatch_call *, FB_INTERPRETER_MEASURED_RESULT *);
template int
run_native_dispatch_abi<false, false, true, false, void, FB_INTERPRETER_MEASURED_RESULT>(
    const fb_native_dispatch_extension_call *, FB_INTERPRETER_MEASURED_RESULT *);
template int
run_native_dispatch_abi<true, false, false, false, void, FB_INTERPRETER_MEASURED_RESULT>(
    const fb_native_dispatch_call *, FB_INTERPRETER_MEASURED_RESULT *);
template int
run_native_dispatch_abi<true, false, true, false, void, FB_INTERPRETER_MEASURED_RESULT>(
    const fb_native_dispatch_extension_call *, FB_INTERPRETER_MEASURED_RESULT *);
template int
run_native_dispatch_abi<true, true, false, false, void, FB_INTERPRETER_MEASURED_RESULT>(
    const fb_native_dispatch_call *, FB_INTERPRETER_MEASURED_RESULT *);
template int run_native_dispatch_abi<true, true, true, false, void, FB_INTERPRETER_MEASURED_RESULT>(
    const fb_native_dispatch_extension_call *, FB_INTERPRETER_MEASURED_RESULT *);
template int run_native_dispatch_abi<false, true, true, false, void, fb_native_result>(
    const fb_native_dispatch_extension_call *, fb_native_result *);
} // namespace fireball::interpreter
#endif

using namespace fireball::interpreter;

extern "C" FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_step(const fb_native_step_call *call,
                                                              fb_native_result *result) {
  return run_native_step_abi(call, result, false);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_control_step(const fb_native_step_call *call, fb_native_result *result) {
  return run_native_step_abi(call, result, true);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_dispatch(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_native_dispatch_abi<false, false, false>(call, result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_dispatch_jit(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_native_dispatch_abi<false, true, false>(call, result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_dispatch_block(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_native_dispatch_abi<false, false, false, true>(call, result);
}

extern "C" FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_debug_dispatch(const fb_native_dispatch_call *call, fb_native_result *result) {
  return run_native_dispatch_abi<false, false, false, false, debugger_aspect>(call, result);
}

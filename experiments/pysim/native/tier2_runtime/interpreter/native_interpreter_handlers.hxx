#ifndef FIREBALL_NATIVE_INTERPRETER_HANDLERS_HXX
#define FIREBALL_NATIVE_INTERPRETER_HANDLERS_HXX

// Interpreter CPS handlers and opcode dispatch.
namespace {

template <typename Debugger>
inline FIREBALL_CPS_CALL void
h_unreachable(execution_context *context,
              const std::uint8_t *instruction_ip [[maybe_unused]],
              std::uint32_t *sp,
              [[maybe_unused]] const std::uint8_t *rel) {
  context->stack_pointer = sp;
  trap(*context, kTrapUnreachable);
  return;
}

constexpr std::uint8_t kOpcodeControlTerminator = 1u << 0;
constexpr std::uint8_t kOpcodeBlockBody = 1u << 1;

inline constexpr auto make_opcode_attributes() {
  std::array<std::uint8_t, 256> attributes{};
  for (const auto opcode : {0x02u, 0x03u, 0x04u, 0x05u, 0x0Bu, 0x0Cu, 0x0Du,
                            0x0Eu, 0x0Fu, 0x10u, 0x11u}) {
    attributes[opcode] |= kOpcodeControlTerminator;
  }
  // Match Loader's _IS_BB_OPCODE ordinary instructions. CALL remains a
  // control boundary and must not create a control-only hotspot visit.
  for (const auto opcode :
       {0x41u, 0x6Au, 0x6Bu, 0x6Cu, 0x6Du, 0x6Eu, 0x71u, 0x72u, 0x73u, 0x74u,
        0x75u, 0x76u, 0x20u, 0x21u, 0x22u, 0x23u, 0x24u, 0x45u, 0x46u, 0x47u,
        0x48u, 0x49u, 0x4Au, 0x4Bu, 0x4Cu, 0x4Du, 0x4Eu, 0x4Fu, 0x1Au, 0x1Bu,
        0x28u, 0x2Cu, 0x2Du, 0x2Eu, 0x2Fu, 0x36u, 0x3Au, 0x3Bu, 0x3Fu, 0x40u}) {
    attributes[opcode] |= kOpcodeBlockBody;
  }
  return attributes;
}

constexpr auto kOpcodeAttributes = make_opcode_attributes();

inline bool opcode_is_control_terminator(std::uint8_t opcode) {
  return (kOpcodeAttributes[opcode] & kOpcodeControlTerminator) != 0;
}

inline bool opcode_is_block_body(std::uint8_t opcode) {
  return (kOpcodeAttributes[opcode] & kOpcodeBlockBody) != 0;
}

template <typename Debugger>
inline FIREBALL_CPS_CALL void
h_unsupported_opcode(execution_context *context,
                     const std::uint8_t *instruction_ip [[maybe_unused]],
                     std::uint32_t *sp,
                     [[maybe_unused]] const std::uint8_t *rel) {
  context->stack_pointer = sp;
  trap(*context, kTrapUnreachable);
  return;
}

template <typename Debugger, auto RuntimeApi>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
h_runtime_api(execution_context *context,
              [[maybe_unused]] const std::uint8_t *instruction_ip,
              std::uint32_t *sp, const std::uint8_t *rel) {
  static_assert(std::is_same_v<decltype(RuntimeApi), runtime_api_pointer>,
                "interpreter Runtime APIs must share one prototype");
  auto *local_base = context->local_base;
  const bool should_continue = RuntimeApi(context, sp, local_base, rel);
  context->local_base = local_base;
  if (!should_continue) {
    context->stack_pointer = sp;
    assert(cps_exit_pending(*context));
    return;
  }
  assert(!cps_exit_pending(*context));
  [[clang::musttail]] return continue_cps<Debugger>(
      context, context->code + context->ip, sp, rel);
}

using native_opcode_handler = fireball_native_cps_handler;
static_assert(
    std::is_same_v<decltype(&h_unreachable<void>), native_opcode_handler>);
static_assert(std::is_same_v<decltype(&h_unsupported_opcode<void>),
                             native_opcode_handler>);
static_assert(std::is_same_v<decltype(&h_runtime_api<void, &runtime_nop>),
                             native_opcode_handler>);

template <typename Debugger, auto RuntimeApi>
inline constexpr native_opcode_handler runtime_api_handler =
    &h_runtime_api<Debugger, RuntimeApi>;

template <typename Debugger> constexpr auto make_fc_opcode_handlers() {
  std::array<native_opcode_handler, 12> handlers{};
  handlers.fill(&h_unsupported_opcode<Debugger>);
  handlers[0x00] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_s>>;
  handlers[0x01] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f32_u>>;
  handlers[0x02] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_s>>;
  handlers[0x03] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i32_trunc_sat_f64_u>>;
  handlers[0x04] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_s>>;
  handlers[0x05] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f32_u>>;
  handlers[0x06] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_s>>;
  handlers[0x07] = runtime_api_handler<
      Debugger,
      &runtime_fc_saturating_conversion<conversion_i64_trunc_sat_f64_u>>;
  handlers[0x0A] = runtime_api_handler<Debugger, &runtime_fc_memory_copy>;
  handlers[0x0B] = runtime_api_handler<Debugger, &runtime_fc_memory_fill>;
  return handlers;
}

template <typename Debugger>
inline constexpr auto kFcOpcodeHandlers = make_fc_opcode_handlers<Debugger>();

template <typename Debugger>
inline FIREBALL_CPS_CALL void
h_fc_dispatch(execution_context *context,
              const std::uint8_t *instruction_ip [[maybe_unused]],
              std::uint32_t *sp, const std::uint8_t *rel) {
  const auto subopcode = peek_u32(rel);
  // The selected Runtime API consumes the subopcode and its operands from rel.
  if (subopcode >= kFcOpcodeHandlers<Debugger>.size()) {
    [[clang::musttail]] return h_unsupported_opcode<Debugger>(
        context, instruction_ip, sp, rel);
  }
  [[clang::musttail]] return kFcOpcodeHandlers<Debugger>[subopcode](
      context, instruction_ip, sp, rel);
}

static_assert(
    std::is_same_v<decltype(&h_fc_dispatch<void>), native_opcode_handler>);

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
  handlers[0x11] =
      runtime_api_handler<Debugger, &runtime_call<true>>; // call_indirect
  handlers[0x1A] = runtime_api_handler<Debugger, &runtime_drop>;
  handlers[0x1B] = runtime_api_handler<Debugger, &runtime_select>;
  handlers[0x20] = runtime_api_handler<Debugger, &runtime_local_get>;
  handlers[0x21] = runtime_api_handler<Debugger, &runtime_local_set>;
  handlers[0x22] = runtime_api_handler<Debugger, &runtime_local_tee>;
  handlers[0x23] = runtime_api_handler<Debugger, &runtime_global_get>;
  handlers[0x24] = runtime_api_handler<Debugger, &runtime_global_set>;
  handlers[0x28] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<4, 1, false>>; // i32.load
  handlers[0x29] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<8, 2, false>>; // i64.load
  handlers[0x2A] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<4, 1, false>>; // f32.load
  handlers[0x2B] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<8, 2, false>>; // f64.load
  handlers[0x2C] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<1, 1, true>>; // i32.load8_s
  handlers[0x2D] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<1, 1, false>>; // i32.load8_u
  handlers[0x2E] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<2, 1, true>>; // i32.load16_s
  handlers[0x2F] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<2, 1, false>>; // i32.load16_u
  handlers[0x30] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<1, 2, true>>; // i64.load8_s
  handlers[0x31] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<1, 2, false>>; // i64.load8_u
  handlers[0x32] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<2, 2, true>>; // i64.load16_s
  handlers[0x33] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<2, 2, false>>; // i64.load16_u
  handlers[0x34] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<4, 2, true>>; // i64.load32_s
  handlers[0x35] =
      runtime_api_handler<Debugger,
                          &runtime_memory_load<4, 2, false>>; // i64.load32_u
  handlers[0x36] =
      runtime_api_handler<Debugger, &runtime_memory_store<4, 1>>; // i32.store
  handlers[0x37] =
      runtime_api_handler<Debugger, &runtime_memory_store<8, 2>>; // i64.store
  handlers[0x38] =
      runtime_api_handler<Debugger, &runtime_memory_store<4, 1>>; // f32.store
  handlers[0x39] =
      runtime_api_handler<Debugger, &runtime_memory_store<8, 2>>; // f64.store
  handlers[0x3A] =
      runtime_api_handler<Debugger, &runtime_memory_store<1, 1>>; // i32.store8
  handlers[0x3B] =
      runtime_api_handler<Debugger, &runtime_memory_store<2, 1>>; // i32.store16
  handlers[0x3C] =
      runtime_api_handler<Debugger, &runtime_memory_store<1, 2>>; // i64.store8
  handlers[0x3D] =
      runtime_api_handler<Debugger, &runtime_memory_store<2, 2>>; // i64.store16
  handlers[0x3E] =
      runtime_api_handler<Debugger, &runtime_memory_store<4, 2>>; // i64.store32
  handlers[0x3F] =
      runtime_api_handler<Debugger,
                          &runtime_memory_manager_api_boundary>; // memory.size
  handlers[0x40] =
      runtime_api_handler<Debugger,
                          &runtime_memory_manager_api_boundary>; // memory.grow
  handlers[0x41] = runtime_api_handler<Debugger, &runtime_i32_const>;
  handlers[0x42] = runtime_api_handler<Debugger, &runtime_i64_const>;
  handlers[0x43] = runtime_api_handler<Debugger, &runtime_f32_const>;
  handlers[0x44] = runtime_api_handler<Debugger, &runtime_f64_const>;
  handlers[0x45] = runtime_api_handler<
      Debugger, &runtime_i32_unary<&opcode_eqz<std::uint32_t>>>; // i32.eqz
  handlers[0x46] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::equal_to<>{}>>; // i32.eq
  handlers[0x47] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::not_equal_to<>{}>>; // i32.ne
  handlers[0x48] = runtime_api_handler<
      Debugger,
      &runtime_i32_binary<&opcode_signed_lt<std::uint32_t>>>; // i32.lt_s
  handlers[0x49] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::less<>{}>>; // i32.lt_u
  handlers[0x4A] = runtime_api_handler<
      Debugger,
      &runtime_i32_binary<&opcode_signed_gt<std::uint32_t>>>; // i32.gt_s
  handlers[0x4B] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::greater<>{}>>; // i32.gt_u
  handlers[0x4C] = runtime_api_handler<
      Debugger,
      &runtime_i32_binary<&opcode_signed_le<std::uint32_t>>>; // i32.le_s
  handlers[0x4D] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::less_equal<>{}>>; // i32.le_u
  handlers[0x4E] = runtime_api_handler<
      Debugger,
      &runtime_i32_binary<&opcode_signed_ge<std::uint32_t>>>; // i32.ge_s
  handlers[0x4F] = runtime_api_handler<
      Debugger, &runtime_i32_binary<std::greater_equal<>{}>>; // i32.ge_u
  handlers[0x50] = runtime_api_handler<
      Debugger,
      &runtime_i64_unary<&opcode_eqz<std::uint64_t>, true>>; // i64.eqz
  handlers[0x51] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::equal_to<>{}, true>>; // i64.eq
  handlers[0x52] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::not_equal_to<>{}, true>>; // i64.ne
  handlers[0x53] = runtime_api_handler<
      Debugger,
      &runtime_i64_binary<&opcode_signed_lt<std::uint64_t>, true>>; // i64.lt_s
  handlers[0x54] =
      runtime_api_handler<Debugger,
                          &runtime_i64_binary<std::less<>{}, true>>; // i64.lt_u
  handlers[0x55] = runtime_api_handler<
      Debugger,
      &runtime_i64_binary<&opcode_signed_gt<std::uint64_t>, true>>; // i64.gt_s
  handlers[0x56] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::greater<>{}, true>>; // i64.gt_u
  handlers[0x57] = runtime_api_handler<
      Debugger,
      &runtime_i64_binary<&opcode_signed_le<std::uint64_t>, true>>; // i64.le_s
  handlers[0x58] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::less_equal<>{}, true>>; // i64.le_u
  handlers[0x59] = runtime_api_handler<
      Debugger,
      &runtime_i64_binary<&opcode_signed_ge<std::uint64_t>, true>>; // i64.ge_s
  handlers[0x5A] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::greater_equal<>{}, true>>; // i64.ge_u
  handlers[0x5B] = runtime_api_handler<
      Debugger, &runtime_f32_binary<std::equal_to<>{}, true>>; // f32.eq
  handlers[0x5C] = runtime_api_handler<
      Debugger, &runtime_f32_binary<std::not_equal_to<>{}, true>>; // f32.ne
  handlers[0x5D] =
      runtime_api_handler<Debugger,
                          &runtime_f32_binary<std::less<>{}, true>>; // f32.lt
  handlers[0x5E] = runtime_api_handler<
      Debugger, &runtime_f32_binary<std::greater<>{}, true>>; // f32.gt
  handlers[0x5F] = runtime_api_handler<
      Debugger, &runtime_f32_binary<std::less_equal<>{}, true>>; // f32.le
  handlers[0x60] = runtime_api_handler<
      Debugger, &runtime_f32_binary<std::greater_equal<>{}, true>>; // f32.ge
  handlers[0x61] = runtime_api_handler<
      Debugger, &runtime_f64_binary<std::equal_to<>{}, true>>; // f64.eq
  handlers[0x62] = runtime_api_handler<
      Debugger, &runtime_f64_binary<std::not_equal_to<>{}, true>>; // f64.ne
  handlers[0x63] =
      runtime_api_handler<Debugger,
                          &runtime_f64_binary<std::less<>{}, true>>; // f64.lt
  handlers[0x64] = runtime_api_handler<
      Debugger, &runtime_f64_binary<std::greater<>{}, true>>; // f64.gt
  handlers[0x65] = runtime_api_handler<
      Debugger, &runtime_f64_binary<std::less_equal<>{}, true>>; // f64.le
  handlers[0x66] = runtime_api_handler<
      Debugger, &runtime_f64_binary<std::greater_equal<>{}, true>>; // f64.ge
  handlers[0x67] = runtime_api_handler<
      Debugger, &runtime_i32_unary<&opcode_clz<std::uint32_t>>>; // i32.clz
  handlers[0x68] = runtime_api_handler<
      Debugger, &runtime_i32_unary<&opcode_ctz<std::uint32_t>>>; // i32.ctz
  handlers[0x69] = runtime_api_handler<
      Debugger,
      &runtime_i32_unary<&opcode_popcount<std::uint32_t>>>; // i32.popcnt
  handlers[0x6A] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::plus<>{}>>; // i32.add
  handlers[0x6B] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::minus<>{}>>; // i32.sub
  handlers[0x6C] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::multiplies<>{}>>; // i32.mul
  handlers[0x6D] =
      runtime_api_handler<Debugger,
                          &runtime_i32_checked_binary<
                              &opcode_div_signed<std::uint32_t>>>; // i32.div_s
  handlers[0x6E] = runtime_api_handler<
      Debugger, &runtime_i32_checked_binary<
                    &opcode_div_unsigned<std::uint32_t>>>; // i32.div_u
  handlers[0x6F] =
      runtime_api_handler<Debugger,
                          &runtime_i32_checked_binary<
                              &opcode_rem_signed<std::uint32_t>>>; // i32.rem_s
  handlers[0x70] = runtime_api_handler<
      Debugger, &runtime_i32_checked_binary<
                    &opcode_rem_unsigned<std::uint32_t>>>; // i32.rem_u
  handlers[0x71] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::bit_and<>{}>>; // i32.and
  handlers[0x72] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::bit_or<>{}>>; // i32.or
  handlers[0x73] =
      runtime_api_handler<Debugger,
                          &runtime_i32_binary<std::bit_xor<>{}>>; // i32.xor
  handlers[0x74] = runtime_api_handler<
      Debugger,
      &runtime_i32_binary<&opcode_shift_left<std::uint32_t>>>; // i32.shl
  handlers[0x75] = runtime_api_handler<
      Debugger, &runtime_i32_binary<
                    &opcode_shift_right_signed<std::uint32_t>>>; // i32.shr_s
  handlers[0x76] = runtime_api_handler<
      Debugger, &runtime_i32_binary<
                    &opcode_shift_right_unsigned<std::uint32_t>>>; // i32.shr_u
  handlers[0x77] = runtime_api_handler<
      Debugger,
      &runtime_i32_binary<&opcode_rotate_left<std::uint32_t>>>; // i32.rotl
  handlers[0x78] = runtime_api_handler<
      Debugger,
      &runtime_i32_binary<&opcode_rotate_right<std::uint32_t>>>; // i32.rotr
  handlers[0x79] = runtime_api_handler<
      Debugger,
      &runtime_i64_unary<&opcode_clz<std::uint64_t>, false>>; // i64.clz
  handlers[0x7A] = runtime_api_handler<
      Debugger,
      &runtime_i64_unary<&opcode_ctz<std::uint64_t>, false>>; // i64.ctz
  handlers[0x7B] = runtime_api_handler<
      Debugger,
      &runtime_i64_unary<&opcode_popcount<std::uint64_t>, false>>; // i64.popcnt
  handlers[0x7C] =
      runtime_api_handler<Debugger,
                          &runtime_i64_binary<std::plus<>{}, false>>; // i64.add
  handlers[0x7D] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::minus<>{}, false>>; // i64.sub
  handlers[0x7E] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::multiplies<>{}, false>>; // i64.mul
  handlers[0x7F] = runtime_api_handler<
      Debugger, &runtime_i64_checked_binary<&opcode_div_signed<std::uint64_t>,
                                            false>>; // i64.div_s
  handlers[0x80] = runtime_api_handler<
      Debugger, &runtime_i64_checked_binary<&opcode_div_unsigned<std::uint64_t>,
                                            false>>; // i64.div_u
  handlers[0x81] = runtime_api_handler<
      Debugger, &runtime_i64_checked_binary<&opcode_rem_signed<std::uint64_t>,
                                            false>>; // i64.rem_s
  handlers[0x82] = runtime_api_handler<
      Debugger, &runtime_i64_checked_binary<&opcode_rem_unsigned<std::uint64_t>,
                                            false>>; // i64.rem_u
  handlers[0x83] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::bit_and<>{}, false>>; // i64.and
  handlers[0x84] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::bit_or<>{}, false>>; // i64.or
  handlers[0x85] = runtime_api_handler<
      Debugger, &runtime_i64_binary<std::bit_xor<>{}, false>>; // i64.xor
  handlers[0x86] = runtime_api_handler<
      Debugger,
      &runtime_i64_binary<&opcode_shift_left<std::uint64_t>, false>>; // i64.shl
  handlers[0x87] = runtime_api_handler<
      Debugger, &runtime_i64_binary<&opcode_shift_right_signed<std::uint64_t>,
                                    false>>; // i64.shr_s
  handlers[0x88] = runtime_api_handler<
      Debugger, &runtime_i64_binary<&opcode_shift_right_unsigned<std::uint64_t>,
                                    false>>; // i64.shr_u
  handlers[0x89] = runtime_api_handler<
      Debugger, &runtime_i64_binary<&opcode_rotate_left<std::uint64_t>,
                                    false>>; // i64.rotl
  handlers[0x8A] = runtime_api_handler<
      Debugger, &runtime_i64_binary<&opcode_rotate_right<std::uint64_t>,
                                    false>>; // i64.rotr
  handlers[0x8B] = runtime_api_handler<
      Debugger, &runtime_f32_unary<&opcode_float_abs<float>>>; // f32.abs
  handlers[0x8C] = runtime_api_handler<
      Debugger, &runtime_f32_unary<&opcode_float_neg<float>>>; // f32.neg
  handlers[0x8D] = runtime_api_handler<
      Debugger, &runtime_f32_unary<&opcode_float_ceil<float>>>; // f32.ceil
  handlers[0x8E] = runtime_api_handler<
      Debugger, &runtime_f32_unary<&opcode_float_floor<float>>>; // f32.floor
  handlers[0x8F] = runtime_api_handler<
      Debugger, &runtime_f32_unary<&opcode_float_trunc<float>>>; // f32.trunc
  handlers[0x90] = runtime_api_handler<
      Debugger,
      &runtime_f32_unary<&opcode_float_nearest<float>>>; // f32.nearest
  handlers[0x91] = runtime_api_handler<
      Debugger, &runtime_f32_unary<&opcode_float_sqrt<float>>>; // f32.sqrt
  handlers[0x92] =
      runtime_api_handler<Debugger,
                          &runtime_f32_binary<std::plus<>{}, false>>; // f32.add
  handlers[0x93] = runtime_api_handler<
      Debugger, &runtime_f32_binary<std::minus<>{}, false>>; // f32.sub
  handlers[0x94] = runtime_api_handler<
      Debugger, &runtime_f32_binary<std::multiplies<>{}, false>>; // f32.mul
  handlers[0x95] = runtime_api_handler<
      Debugger,
      &runtime_f32_binary<&opcode_float_div<float>, false>>; // f32.div
  handlers[0x96] = runtime_api_handler<
      Debugger,
      &runtime_f32_binary<&opcode_float_min<float>, false>>; // f32.min
  handlers[0x97] = runtime_api_handler<
      Debugger,
      &runtime_f32_binary<&opcode_float_max<float>, false>>; // f32.max
  handlers[0x98] =
      runtime_api_handler<Debugger,
                          &runtime_f32_binary<&opcode_float_copysign<float>,
                                              false>>; // f32.copysign
  handlers[0x99] = runtime_api_handler<
      Debugger, &runtime_f64_unary<&opcode_float_abs<double>>>; // f64.abs
  handlers[0x9A] = runtime_api_handler<
      Debugger, &runtime_f64_unary<&opcode_float_neg<double>>>; // f64.neg
  handlers[0x9B] = runtime_api_handler<
      Debugger, &runtime_f64_unary<&opcode_float_ceil<double>>>; // f64.ceil
  handlers[0x9C] = runtime_api_handler<
      Debugger, &runtime_f64_unary<&opcode_float_floor<double>>>; // f64.floor
  handlers[0x9D] = runtime_api_handler<
      Debugger, &runtime_f64_unary<&opcode_float_trunc<double>>>; // f64.trunc
  handlers[0x9E] = runtime_api_handler<
      Debugger,
      &runtime_f64_unary<&opcode_float_nearest<double>>>; // f64.nearest
  handlers[0x9F] = runtime_api_handler<
      Debugger, &runtime_f64_unary<&opcode_float_sqrt<double>>>; // f64.sqrt
  handlers[0xA0] =
      runtime_api_handler<Debugger,
                          &runtime_f64_binary<std::plus<>{}, false>>; // f64.add
  handlers[0xA1] = runtime_api_handler<
      Debugger, &runtime_f64_binary<std::minus<>{}, false>>; // f64.sub
  handlers[0xA2] = runtime_api_handler<
      Debugger, &runtime_f64_binary<std::multiplies<>{}, false>>; // f64.mul
  handlers[0xA3] = runtime_api_handler<
      Debugger,
      &runtime_f64_binary<&opcode_float_div<double>, false>>; // f64.div
  handlers[0xA4] = runtime_api_handler<
      Debugger,
      &runtime_f64_binary<&opcode_float_min<double>, false>>; // f64.min
  handlers[0xA5] = runtime_api_handler<
      Debugger,
      &runtime_f64_binary<&opcode_float_max<double>, false>>; // f64.max
  handlers[0xA6] =
      runtime_api_handler<Debugger,
                          &runtime_f64_binary<&opcode_float_copysign<double>,
                                              false>>; // f64.copysign
  handlers[0xA7] = runtime_api_handler<
      Debugger, &runtime_conversion<conversion_i32_wrap_i64>>; // i32.wrap_i64
  handlers[0xA8] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i32_trunc_f32_s>>; // i32.trunc_f32_s
  handlers[0xA9] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i32_trunc_f32_u>>; // i32.trunc_f32_u
  handlers[0xAA] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i32_trunc_f64_s>>; // i32.trunc_f64_s
  handlers[0xAB] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i32_trunc_f64_u>>; // i32.trunc_f64_u
  handlers[0xAC] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i64_extend_i32_s>>; // i64.extend_i32_s
  handlers[0xAD] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i64_extend_i32_u>>; // i64.extend_i32_u
  handlers[0xAE] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i64_trunc_f32_s>>; // i64.trunc_f32_s
  handlers[0xAF] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i64_trunc_f32_u>>; // i64.trunc_f32_u
  handlers[0xB0] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i64_trunc_f64_s>>; // i64.trunc_f64_s
  handlers[0xB1] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_i64_trunc_f64_u>>; // i64.trunc_f64_u
  handlers[0xB2] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f32_convert_i32_s>>; // f32.convert_i32_s
  handlers[0xB3] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f32_convert_i32_u>>; // f32.convert_i32_u
  handlers[0xB4] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f32_convert_i64_s>>; // f32.convert_i64_s
  handlers[0xB5] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f32_convert_i64_u>>; // f32.convert_i64_u
  handlers[0xB6] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f32_demote_f64>>; // f32.demote_f64
  handlers[0xB7] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f64_convert_i32_s>>; // f64.convert_i32_s
  handlers[0xB8] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f64_convert_i32_u>>; // f64.convert_i32_u
  handlers[0xB9] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f64_convert_i64_s>>; // f64.convert_i64_s
  handlers[0xBA] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f64_convert_i64_u>>; // f64.convert_i64_u
  handlers[0xBB] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_f64_promote_f32>>; // f64.promote_f32
  handlers[0xBC] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_identity>>; // i32.reinterpret_f32
  handlers[0xBD] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_identity>>; // i64.reinterpret_f64
  handlers[0xBE] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_identity>>; // f32.reinterpret_i32
  handlers[0xBF] = runtime_api_handler<
      Debugger,
      &runtime_conversion<conversion_identity>>; // f64.reinterpret_i64
  handlers[0xC0] =
      runtime_api_handler<Debugger, &runtime_i32_unary<&opcode_extend8_signed<
                                        std::uint32_t>>>; // i32.extend8_s
  handlers[0xC1] =
      runtime_api_handler<Debugger, &runtime_i32_unary<&opcode_extend16_signed<
                                        std::uint32_t>>>; // i32.extend16_s
  handlers[0xC2] = runtime_api_handler<
      Debugger, &runtime_i64_unary<&opcode_extend8_signed<std::uint64_t>,
                                   false>>; // i64.extend8_s
  handlers[0xC3] = runtime_api_handler<
      Debugger, &runtime_i64_unary<&opcode_extend16_signed<std::uint64_t>,
                                   false>>; // i64.extend16_s
  handlers[0xC4] = runtime_api_handler<
      Debugger,
      &runtime_i64_unary<&opcode_extend32_signed, false>>; // i64.extend32_s
  handlers[kOpcodeFcPrefix] = &h_fc_dispatch<Debugger>;
  return handlers;
}

template <typename Debugger>
inline constexpr auto kOpcodeHandlers = make_opcode_handlers<Debugger>();

template <typename Debugger>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
dispatch_next(execution_context *context, const std::uint8_t *ip,
              std::uint32_t *sp, const std::uint8_t *rel) {
  auto &current = *context;
  assert(current.code != nullptr && ip >= current.code &&
         ip <= current.code + current.code_size);
  assert(ip == current.code + current.ip);
  if (current.ip >= current.code_size) {
    current.stack_pointer = sp;
    trap(current, kTrapUnreachable);
    return;
  }
  if ((current.runtime_flags & kStopAtBlockBoundaryFlag) != 0 &&
      is_requested_block_boundary(current)) {
    current.stack_pointer = sp;
    block_boundary(current, current.ip);
    return;
  }

  if constexpr (std::is_same_v<Debugger, debugger_aspect>) {
    if (Debugger::before_instruction(current)) {
      current.stack_pointer = sp;
      debug_stop(current);
      return;
    }
  }

  const auto opcode = *ip;
  [[clang::musttail]] return kOpcodeHandlers<Debugger>[opcode](context, ip, sp,
                                                               rel);
}

} // namespace

#endif

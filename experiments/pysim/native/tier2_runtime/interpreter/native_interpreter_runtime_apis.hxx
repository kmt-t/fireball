#ifndef FIREBALL_NATIVE_INTERPRETER_RUNTIME_APIS_HXX
#define FIREBALL_NATIVE_INTERPRETER_RUNTIME_APIS_HXX

// One-operation runtime APIs. These update interpreter state and never dispatch
// the next opcode; CPS handlers are thin adapters around this shared interface.
namespace {

using runtime_api_pointer = bool (*)(execution_context *, std::uint32_t *&,
                                     std::uint32_t *&, const std::uint8_t *&);
template <auto Operation>
[[gnu::always_inline]] inline bool
runtime_i32_unary(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp == current.stack_base)
    return trap(current, kTrapOperandStackCapacity);
  sp[-1] = Operation(sp[-1]);
  ++current.ip;
  return true;
}

// Opcode semantics are declared beside each handler-table entry below.

inline bool linear_memory_range_is_valid(const execution_context &current,
                                         std::uint64_t offset,
                                         std::uint32_t length) {
  const auto wide_length = static_cast<std::uint64_t>(length);
  return offset <= current.linear_memory_size &&
         wide_length <= current.linear_memory_size - offset;
}

inline bool conversion_i32_wrap_i64(execution_context &current,
                                    std::uint32_t *&sp,
                                    std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value) ||
      !push(current, sp, static_cast<std::uint32_t>(value))) {
    return false;
  }
  trap_code = 0;
  return true;
}

inline bool conversion_i32_trunc_f32_s(execution_context &current,
                                       std::uint32_t *&sp,
                                       std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !std::isfinite(value) ||
      value < -2147483648.0f || value >= 2147483648.0f) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result =
      static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  if (!push(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

inline bool conversion_i32_trunc_f32_u(execution_context &current,
                                       std::uint32_t *&sp,
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

inline bool conversion_i32_trunc_f64_s(execution_context &current,
                                       std::uint32_t *&sp,
                                       std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !std::isfinite(value) ||
      value < -2147483648.0 || value >= 2147483648.0) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result =
      static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  if (!push(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

inline bool conversion_i32_trunc_f64_u(execution_context &current,
                                       std::uint32_t *&sp,
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

inline bool conversion_i64_trunc_f32_s(execution_context &current,
                                       std::uint32_t *&sp,
                                       std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) || !std::isfinite(value) ||
      value < -9223372036854775808.0f || value >= 9223372036854775808.0f) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result =
      value == -9223372036854775808.0f
          ? 0x8000'0000'0000'0000ull
          : static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  if (!push_u64(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

inline bool conversion_i64_trunc_f32_u(execution_context &current,
                                       std::uint32_t *&sp,
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

inline bool conversion_i64_trunc_f64_s(execution_context &current,
                                       std::uint32_t *&sp,
                                       std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) || !std::isfinite(value) ||
      value < -9223372036854775808.0 || value >= 9223372036854775808.0) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  const auto result =
      value == -9223372036854775808.0
          ? 0x8000'0000'0000'0000ull
          : static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  if (!push_u64(current, sp, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

inline bool conversion_i64_trunc_f64_u(execution_context &current,
                                       std::uint32_t *&sp,
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

inline bool conversion_i32_trunc_sat_f32_s(execution_context &current,
                                           std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value))
    return false;
  std::uint32_t result = 0;
  if (std::isnan(value))
    result = 0;
  else if (value <= -2147483648.0f)
    result = 0x8000'0000u;
  else if (value >= 2147483648.0f)
    result = 0x7FFF'FFFFu;
  else
    result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  return push(current, sp, result);
}

inline bool conversion_i32_trunc_sat_f32_u(execution_context &current,
                                           std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value))
    return false;
  std::uint32_t result = 0;
  if (std::isnan(value) || value <= 0.0f)
    result = 0;
  else if (value >= 4294967296.0f)
    result = 0xFFFF'FFFFu;
  else
    result = static_cast<std::uint32_t>(value);
  return push(current, sp, result);
}

inline bool conversion_i32_trunc_sat_f64_s(execution_context &current,
                                           std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value))
    return false;
  std::uint32_t result = 0;
  if (std::isnan(value))
    result = 0;
  else if (value <= -2147483648.0)
    result = 0x8000'0000u;
  else if (value >= 2147483648.0)
    result = 0x7FFF'FFFFu;
  else
    result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  return push(current, sp, result);
}

inline bool conversion_i32_trunc_sat_f64_u(execution_context &current,
                                           std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value))
    return false;
  std::uint32_t result = 0;
  if (std::isnan(value) || value <= 0.0)
    result = 0;
  else if (value >= 4294967296.0)
    result = 0xFFFF'FFFFu;
  else
    result = static_cast<std::uint32_t>(value);
  return push(current, sp, result);
}

inline bool conversion_i64_trunc_sat_f32_s(execution_context &current,
                                           std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value))
    return false;
  std::uint64_t result = 0;
  if (std::isnan(value))
    result = 0;
  else if (value <= -9223372036854775808.0f)
    result = 0x8000'0000'0000'0000ull;
  else if (value >= 9223372036854775808.0f)
    result = 0x7FFF'FFFF'FFFF'FFFFull;
  else
    result = static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  return push_u64(current, sp, result);
}

inline bool conversion_i64_trunc_sat_f32_u(execution_context &current,
                                           std::uint32_t *&sp) {
  float value = 0;
  if (!pop_f32(current, sp, value))
    return false;
  std::uint64_t result = 0;
  if (std::isnan(value) || value <= 0.0f)
    result = 0;
  else if (value >= 18446744073709551616.0f)
    result = 0xFFFF'FFFF'FFFF'FFFFull;
  else
    result = static_cast<std::uint64_t>(value);
  return push_u64(current, sp, result);
}

inline bool conversion_i64_trunc_sat_f64_s(execution_context &current,
                                           std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value))
    return false;
  std::uint64_t result = 0;
  if (std::isnan(value))
    result = 0;
  else if (value <= -9223372036854775808.0)
    result = 0x8000'0000'0000'0000ull;
  else if (value >= 9223372036854775808.0)
    result = 0x7FFF'FFFF'FFFF'FFFFull;
  else
    result = static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  return push_u64(current, sp, result);
}

inline bool conversion_i64_trunc_sat_f64_u(execution_context &current,
                                           std::uint32_t *&sp) {
  double value = 0;
  if (!pop_f64(current, sp, value))
    return false;
  std::uint64_t result = 0;
  if (std::isnan(value) || value <= 0.0)
    result = 0;
  else if (value >= 18446744073709551616.0)
    result = 0xFFFF'FFFF'FFFF'FFFFull;
  else
    result = static_cast<std::uint64_t>(value);
  return push_u64(current, sp, result);
}

inline bool conversion_i64_extend_i32_s(execution_context &current,
                                        std::uint32_t *&sp,
                                        std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value) ||
      !push_u64(current, sp,
                static_cast<std::uint64_t>(static_cast<std::int64_t>(
                    static_cast<std::int32_t>(value))))) {
    return false;
  }
  trap_code = 0;
  return true;
}

inline bool conversion_i64_extend_i32_u(execution_context &current,
                                        std::uint32_t *&sp,
                                        std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value) || !push_u64(current, sp, value))
    return false;
  trap_code = 0;
  return true;
}

inline bool
conversion_f32_convert_i32_s(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value))
    return false;
  return push_f32(current, sp,
                  static_cast<float>(static_cast<std::int32_t>(value)));
}

inline bool
conversion_f32_convert_i32_u(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value))
    return false;
  return push_f32(current, sp, static_cast<float>(value));
}

inline bool
conversion_f64_convert_i32_s(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value))
    return false;
  return push_f64(current, sp,
                  static_cast<double>(static_cast<std::int32_t>(value)));
}

inline bool
conversion_f64_convert_i32_u(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, value))
    return false;
  return push_f64(current, sp, static_cast<double>(value));
}

inline bool
conversion_f32_convert_i64_s(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value))
    return false;
  return push_f32(current, sp,
                  static_cast<float>(static_cast<std::int64_t>(value)));
}

inline bool
conversion_f32_convert_i64_u(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value))
    return false;
  return push_f32(current, sp, static_cast<float>(value));
}

inline bool
conversion_f64_convert_i64_s(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value))
    return false;
  return push_f64(current, sp,
                  static_cast<double>(static_cast<std::int64_t>(value)));
}

inline bool
conversion_f64_convert_i64_u(execution_context &current, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, value))
    return false;
  return push_f64(current, sp, static_cast<double>(value));
}

inline bool conversion_f64_promote_f32(execution_context &current,
                                       std::uint32_t *&sp,
                                       std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, value) ||
      !push_f64(current, sp, static_cast<double>(value)))
    return false;
  trap_code = 0;
  return true;
}

inline bool conversion_f32_demote_f64(execution_context &current,
                                      std::uint32_t *&sp,
                                      std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, value) ||
      !push_f32(current, sp, static_cast<float>(value)))
    return false;
  trap_code = 0;
  return true;
}

inline bool conversion_identity(execution_context &, std::uint32_t *&,
                                std::uint32_t &trap_code) {
  trap_code = 0;
  return true;
}

template <bool Indirect>
[[gnu::always_inline]] inline bool
runtime_call(execution_context *context, std::uint32_t *&sp,
             std::uint32_t *&local_stack,
             [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = current.ip;
  std::uint32_t function_index = 0;
  std::uint32_t table_index = 0;
  std::uint32_t table_slot = 0;
  const auto &caller = active_frame(current);
  assert(caller.function_view->module_view != nullptr);
  const auto &module = *caller.function_view->module_view;

  if constexpr (Indirect) {
    (void)read_u32(rel);
    table_index = read_u32(rel);
    if (table_index >= module.table_count || module.tables == nullptr) {
      return trap(current, kTrapTableIndexOutOfBounds);
    }
    const auto &table = module.tables[table_index];
    if (sp == current.stack_base)
      return trap(current, kTrapOperandStackCapacity);
    table_slot = sp[-1];
    if (table_slot >= table.size || table.function_indices == nullptr) {
      return trap(current, kTrapTableIndexOutOfBounds);
    }
    function_index = table.function_indices[table_slot];
    if (function_index == kSentinel)
      return trap(current, kTrapTableSlotUninitialized);
  } else {
    function_index = read_u32(rel);
  }

  const auto &target = module.functions[function_index];
  // Imports cross the host-call boundary; defined guest functions stay in this
  // native dispatcher and never return to Python opcode handling.
  if (target.is_import != 0)
    return runtime_api_boundary(current, source_ip);
  if (current.call_stack->size >= FIREBALL_NATIVE_CALL_STACK_CAPACITY) {
    return trap(current, kTrapCallStackCapacity);
  }
  if (current.local_stack_pointer + target.local_slot_count >
      current.local_stack_limit) {
    return trap(current, kTrapLocalStackCapacity);
  }

  auto *callee_local_base = current.local_stack_pointer;
  const auto local_end = callee_local_base + target.local_slot_count;
  for (std::uint32_t index = target.param_packed_slot_count;
       callee_local_base + index < local_end; ++index) {
    callee_local_base[index] = 0;
  }

  const auto argument_words =
      target.param_packed_slot_count + (Indirect ? 1u : 0u);
  if (sp < current.stack_base + argument_words)
    return trap(current, kTrapOperandStackCapacity);
  const auto argument_base = sp - static_cast<std::ptrdiff_t>(argument_words);
  const auto *arguments = argument_base;
  auto *argument = arguments;
  for (std::uint32_t index = 0; index < target.param_count; ++index) {
    const auto offset = target.local_offsets[index];
    const auto width = target.local_sizes[index];
    for (std::uint32_t word = 0; word < width; ++word) {
      callee_local_base[offset + word] = *argument++;
    }
  }

  auto &callee = current.call_stack->frames[current.call_stack->size];
  callee.function_view = &target;
  callee.local_base = callee_local_base;
  callee.control_frame_base =
      current.control_stack->frames + current.control_stack->size;
  callee.func_index = function_index;
  callee.return_ip =
      current.ip + encoded_instruction_size<Indirect ? 2u : 1u>(current);
  callee.boundary_next_pc = kSentinel;
  callee.boundary_loops_to = kSentinel;

  sp = argument_base;
  current.local_stack_pointer = local_end;
  ++current.call_stack->size;
  current.ip = 0;
  current.code = target.code;
  current.code_size = target.code_size;
  current.control_frame_base = callee.control_frame_base;
  local_stack = callee_local_base;
  current.local_base = local_stack;
  rel = interpreter_rel_for_block(callee, 0);
  if ((current.runtime_flags & kStopAtDefinedCallBoundaryFlag) != 0) {
    return call_boundary(current);
  }
  return true;
}

[[gnu::always_inline]] inline bool
runtime_nop(execution_context *context, [[maybe_unused]] std::uint32_t *&sp,
            [[maybe_unused]] std::uint32_t *&local_base,
            [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  current.ip += 1;
  return true;
}

[[gnu::always_inline]] inline bool
runtime_else(execution_context *context, [[maybe_unused]] std::uint32_t *&sp,
             [[maybe_unused]] std::uint32_t *&local_base,
             [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (current.control_stack->frames + current.control_stack->size <=
      current.control_frame_base)
    return trap(current, kTrapUnreachable);
  const auto frame =
      current.control_stack->frames[current.control_stack->size - 1];
  current.control_stack->size -= 1;
  const auto &call_frame = active_frame(current);
  const auto *entry = control_entry(current, frame.start);
  assert(entry != nullptr && entry->match_end != kSentinel);
  current.ip = call_frame.boundary_next_pc != kSentinel
                   ? call_frame.boundary_next_pc -
                         call_frame.function_view->code_pc_offset
                   : entry->match_end + 1;
  prune_control_frames_after_static_jump(current, current.ip);
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_block(execution_context *context, [[maybe_unused]] std::uint32_t *&sp,
              [[maybe_unused]] std::uint32_t *&local_base,
              [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  if (entry == nullptr || entry->match_end == kSentinel)
    return trap(current, kTrapUnreachable);
  if (current.control_stack->size >= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY)
    return trap(current, kTrapControlFrameCapacity);
  auto &frame = current.control_stack->frames[current.control_stack->size++];
  frame.start = current.ip;
  frame.stack_height = sp;
  current.ip = entry->next_pc;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_if(execution_context *context, std::uint32_t *&sp,
           [[maybe_unused]] std::uint32_t *&local_base,
           [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  std::uint32_t condition = 0;
  if (entry == nullptr || entry->match_end == kSentinel)
    return trap(current, kTrapUnreachable);
  if (!pop(current, sp, condition))
    return trap(current, kTrapOperandStackCapacity);
  if (condition == 0 && entry->else_offset == kSentinel) {
    current.ip = entry->match_end + 1;
    rel = interpreter_rel_for_current_block(current, current.ip);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return true;
  }
  if (current.control_stack->size >= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY) {
    return trap(current, kTrapControlFrameCapacity);
  }
  auto &frame = current.control_stack->frames[current.control_stack->size++];
  frame.start = current.ip;
  frame.stack_height = sp;
  current.ip = condition == 0 ? entry->else_offset + 1 : entry->next_pc;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_br(execution_context *context, std::uint32_t *&sp,
           [[maybe_unused]] std::uint32_t *&local_base,
           [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = current.ip;
  const auto depth = read_u32(rel);
  std::uint32_t next_ip = 0;
  if (!branch(current, sp, depth, next_ip))
    return trap(current, kTrapUnreachable);
  if (next_ip == kSentinel) {
    const bool outermost = &active_frame(current) == current.call_frame_base;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base, rel))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return true;
  }
  const auto &call_frame = active_frame(current);
  if (call_frame.boundary_next_pc != kSentinel) {
    next_ip =
        call_frame.boundary_next_pc - call_frame.function_view->code_pc_offset;
  }
  prune_control_frames_after_static_jump(current, next_ip);
  const bool loop_backedge = record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current) ||
      (loop_backedge && current.loop_jump_count >= current.loop_jump_threshold))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_br_if(execution_context *context, std::uint32_t *&sp,
              [[maybe_unused]] std::uint32_t *&local_base,
              [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = current.ip;
  std::uint32_t depth = 0;
  std::uint32_t condition = 0;
  depth = read_u32(rel);
  if (!pop(current, sp, condition))
    return trap(current, kTrapOperandStackCapacity);
  if (condition == 0) {
    current.ip = source_ip + encoded_instruction_size<1>(current);
    rel = interpreter_rel_for_current_block(current, current.ip);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return true;
  }
  std::uint32_t next_ip = 0;
  if (!branch(current, sp, depth, next_ip))
    return trap(current, kTrapUnreachable);
  if (next_ip == kSentinel) {
    const bool outermost = &active_frame(current) == current.call_frame_base;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base, rel))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return true;
  }
  const auto &call_frame = active_frame(current);
  if (call_frame.boundary_loops_to != kSentinel) {
    next_ip =
        call_frame.boundary_loops_to - call_frame.function_view->code_pc_offset;
  }
  prune_control_frames_after_static_jump(current, next_ip);
  const bool loop_backedge = record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current) ||
      (loop_backedge && current.loop_jump_count >= current.loop_jump_threshold))
    return block_boundary(current, current.ip);
  return true;
}

// These helpers mutate only the local and operand state. The instruction
// wrapper advances ip after the operation succeeds.
[[gnu::always_inline]] inline bool
apply_local_get(execution_context *context, std::uint32_t *&sp,
                std::uint32_t *&local_base,
                const fireball_call_frame_native &frame, std::uint32_t index) {
  auto &current = *context;
  assert(sp >= current.stack_base && sp <= current.stack_limit);
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  if (function.local_slot_count == function.local_count) {
    assert(index < function.local_count);
    if (sp == current.stack_limit)
      return trap(current, kTrapOperandStackCapacity);
    *sp++ = local_base[index];
  } else {
    const auto [offset, width] = local_span(frame, index);
    if (current.stack_limit - sp < width)
      return trap(current, kTrapOperandStackCapacity);
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

[[gnu::always_inline]] inline bool
apply_local_set(execution_context *context, std::uint32_t *&sp,
                std::uint32_t *&local_base,
                const fireball_call_frame_native &frame, std::uint32_t index) {
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

[[gnu::always_inline]] inline bool
apply_local_tee(execution_context *context, std::uint32_t *&sp,
                std::uint32_t *&local_base,
                const fireball_call_frame_native &frame, std::uint32_t index) {
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

[[gnu::always_inline]] inline bool
runtime_local_get_decoded(execution_context *context, std::uint32_t *&sp,
                          std::uint32_t *&local_base, std::uint32_t index) {
  auto &current = *context;
  return apply_local_get(context, sp, local_base, active_frame(current), index);
}

[[gnu::always_inline]] inline bool
runtime_local_get(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto index = read_u32(rel);
  if (!runtime_local_get_decoded(context, sp, local_base, index))
    return false;
  current.ip += encoded_instruction_size<1>(current);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_local_set_decoded(execution_context *context, std::uint32_t *&sp,
                          std::uint32_t *&local_base, std::uint32_t index) {
  auto &current = *context;
  return apply_local_set(context, sp, local_base, active_frame(current), index);
}

[[gnu::always_inline]] inline bool
runtime_local_set(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto index = read_u32(rel);
  if (!runtime_local_set_decoded(context, sp, local_base, index))
    return false;
  current.ip += encoded_instruction_size<1>(current);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_local_tee_decoded(execution_context *context, std::uint32_t *&sp,
                          std::uint32_t *&local_base, std::uint32_t index) {
  auto &current = *context;
  return apply_local_tee(context, sp, local_base, active_frame(current), index);
}

[[gnu::always_inline]] inline bool
runtime_local_tee(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto index = read_u32(rel);
  if (!runtime_local_tee_decoded(context, sp, local_base, index))
    return false;
  current.ip += encoded_instruction_size<1>(current);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_global_get(execution_context *context, std::uint32_t *&sp,
                   [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto index = read_u32(rel);

  const auto &frame = active_frame(current);
  const auto *module = frame.function_view->module_view;
  assert(module != nullptr && index < module->global_count);
  assert(module->globals != nullptr && module->global_widths != nullptr);

  const auto width = module->global_widths[index];
  assert(width == 1u || width == 2u);
  if (current.stack_limit - sp < width) {
    return trap(current, kTrapOperandStackCapacity);
  }
  const auto value = module->globals[index];
  *sp++ = static_cast<std::uint32_t>(value);
  if (width == 2u)
    *sp++ = static_cast<std::uint32_t>(value >> 32);
  current.ip += encoded_instruction_size<1>(current);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_global_set(execution_context *context, std::uint32_t *&sp,
                   [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto index = read_u32(rel);

  const auto &frame = active_frame(current);
  const auto *module = frame.function_view->module_view;
  assert(module != nullptr && index < module->global_count);
  assert(module->globals != nullptr && module->global_widths != nullptr);

  const auto width = module->global_widths[index];
  assert(width == 1u || width == 2u);
  if (sp < current.stack_base + width) {
    return trap(current, kTrapOperandStackCapacity);
  }
  auto value =
      static_cast<std::uint64_t>(sp[-static_cast<std::ptrdiff_t>(width)]);
  if (width == 2u)
    value |= static_cast<std::uint64_t>(sp[-1]) << 32;
  module->globals[index] = value;
  sp -= width;
  current.ip += encoded_instruction_size<1>(current);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_drop(execution_context *context, std::uint32_t *&sp,
             [[maybe_unused]] std::uint32_t *&local_base,
             [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  const auto width = entry == nullptr ? 0u : entry->operand_width;
  if (width == 0 || sp < current.stack_base + width)
    return trap(current, kTrapOperandStackCapacity);
  sp -= width;
  current.ip += 1;
  return true;
}

[[gnu::always_inline]] inline bool
runtime_select(execution_context *context, std::uint32_t *&sp,
               [[maybe_unused]] std::uint32_t *&local_base,
               [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  const auto width = entry == nullptr ? 0u : entry->operand_width;
  if ((width != 1 && width != 2) || sp < current.stack_base + 1 + width * 2) {
    return trap(current, kTrapOperandStackCapacity);
  }
  const auto condition = sp[-1];
  --sp;
  if (condition == 0) {
    for (std::uint32_t word = 0; word < width; ++word)
      sp[word - static_cast<std::ptrdiff_t>(2 * width)] =
          sp[word - static_cast<std::ptrdiff_t>(width)];
  }
  sp -= width;
  current.ip += 1;
  return true;
}

[[gnu::always_inline]] inline bool
runtime_end(execution_context *context, [[maybe_unused]] std::uint32_t *&sp,
            [[maybe_unused]] std::uint32_t *&local_base,
            [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (current.control_stack->frames + current.control_stack->size >
      current.control_frame_base) {
    current.control_stack->size -= 1;
  }
  current.ip += 1;
  if (current.ip >= current.code_size) {
    const bool outermost = &active_frame(current) == current.call_frame_base;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base, rel))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return true;
  }
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_return(execution_context *context, [[maybe_unused]] std::uint32_t *&sp,
               [[maybe_unused]] std::uint32_t *&local_base,
               [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const bool outermost = &active_frame(current) == current.call_frame_base;
  if (outermost && should_stop_after_control(current))
    return block_boundary(current, kSentinel);
  if (finish_current_function(current, local_base, rel))
    return complete(current);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_br_table(execution_context *context, std::uint32_t *&sp,
                 [[maybe_unused]] std::uint32_t *&local_base,
                 [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto source_ip = current.ip;
  if (source_ip >= current.code_size)
    return trap(current, kTrapUnreachable);

  const auto case_count = read_u32(rel);
  if (case_count >= current.code_size) {
    return trap(current, kTrapUnreachable);
  }

  std::uint32_t selector = 0;
  if (!pop(current, sp, selector))
    return trap(current, kTrapOperandStackCapacity);

  const auto selected_index = selector < case_count ? selector : case_count;
  std::uint32_t selected_depth = 0;
  for (std::uint32_t index = 0; index <= case_count; ++index) {
    const auto depth = read_u32(rel);
    if (index == selected_index)
      selected_depth = depth;
  }
  std::uint32_t next_ip = 0;
  if (!branch(current, sp, selected_depth, next_ip))
    return trap(current, kTrapUnreachable);
  if (next_ip == kSentinel) {
    const bool outermost = &active_frame(current) == current.call_frame_base;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base, rel))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return true;
  }
  const bool loop_backedge = record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current) ||
      (loop_backedge &&
       current.loop_jump_count >= current.loop_jump_threshold)) {
    return block_boundary(current, current.ip);
  }
  return true;
}

[[gnu::always_inline]] inline bool
runtime_i32_const(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto value = read_s32(rel);
  if (!push(current, sp, static_cast<std::uint32_t>(value))) {
    return trap(current, kTrapOperandStackCapacity);
  }
  current.ip += encoded_instruction_size<1>(current);
  return true;
}

template <auto Operation>
[[gnu::always_inline]] inline bool
runtime_i32_binary(execution_context *context, std::uint32_t *&sp,
                   [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 2)
    return trap(current, kTrapOperandStackCapacity);
  const auto result = Operation(sp[-2], sp[-1]);
  --sp;
  sp[-1] = result;
  ++current.ip;
  return true;
}

template <auto Operation>
[[gnu::always_inline]] inline bool
runtime_i32_checked_binary(execution_context *context, std::uint32_t *&sp,
                           [[maybe_unused]] std::uint32_t *&local_base,
                           [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 2)
    return trap(current, kTrapOperandStackCapacity);
  std::uint32_t result = 0;
  std::uint32_t trap_code = 0;
  if (!Operation(sp[-2], sp[-1], result, trap_code))
    return trap(current, trap_code);
  --sp;
  sp[-1] = result;
  ++current.ip;
  return true;
}

template <auto Operation, bool ResultIsBool>
[[gnu::always_inline]] inline bool
runtime_i64_binary(execution_context *context, std::uint32_t *&sp,
                   [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 4)
    return trap(current, kTrapOperandStackCapacity);
  const auto lhs = combine_u64(sp[-4], sp[-3]);
  const auto rhs = combine_u64(sp[-2], sp[-1]);
  const auto result = Operation(lhs, rhs);
  replace_u64_operands<ResultIsBool>(sp, result, 4);
  ++current.ip;
  return true;
}

template <auto Operation, bool ResultIsBool>
[[gnu::always_inline]] inline bool
runtime_i64_checked_binary(execution_context *context, std::uint32_t *&sp,
                           [[maybe_unused]] std::uint32_t *&local_base,
                           [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 4)
    return trap(current, kTrapOperandStackCapacity);
  const auto lhs = combine_u64(sp[-4], sp[-3]);
  const auto rhs = combine_u64(sp[-2], sp[-1]);
  std::uint64_t result = 0;
  std::uint32_t trap_code = 0;
  if (!Operation(lhs, rhs, result, trap_code))
    return trap(current, trap_code);
  replace_u64_operands<ResultIsBool>(sp, result, 4);
  ++current.ip;
  return true;
}

template <auto Operation, bool ResultIsBool>
[[gnu::always_inline]] inline bool
runtime_i64_unary(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 2)
    return trap(current, kTrapOperandStackCapacity);
  const auto value = combine_u64(sp[-2], sp[-1]);
  const auto result = Operation(value);
  replace_u64_operands<ResultIsBool>(sp, result, 2);
  ++current.ip;
  return true;
}

template <auto Operation, bool ResultIsBool>
[[gnu::always_inline]] inline bool
runtime_f32_binary(execution_context *context, std::uint32_t *&sp,
                   [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 2)
    return trap(current, kTrapOperandStackCapacity);
  const auto result = Operation(f32_from_bits(sp[-2]), f32_from_bits(sp[-1]));
  --sp;
  if constexpr (ResultIsBool)
    sp[-1] = static_cast<std::uint32_t>(result);
  else
    sp[-1] = f32_to_bits(result);
  ++current.ip;
  return true;
}

template <auto Operation, bool ResultIsBool>
[[gnu::always_inline]] inline bool
runtime_f64_binary(execution_context *context, std::uint32_t *&sp,
                   [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 4)
    return trap(current, kTrapOperandStackCapacity);
  const auto lhs = f64_from_bits(combine_u64(sp[-4], sp[-3]));
  const auto rhs = f64_from_bits(combine_u64(sp[-2], sp[-1]));
  const auto result = Operation(lhs, rhs);
  if constexpr (ResultIsBool)
    replace_u64_operands<true>(sp, static_cast<std::uint32_t>(result), 4);
  else
    replace_u64_operands<false>(sp, f64_to_bits(result), 4);
  ++current.ip;
  return true;
}

template <auto Operation>
[[gnu::always_inline]] inline bool
runtime_f32_unary(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp == current.stack_base)
    return trap(current, kTrapOperandStackCapacity);
  sp[-1] = f32_to_bits(Operation(f32_from_bits(sp[-1])));
  ++current.ip;
  return true;
}

template <auto Operation>
[[gnu::always_inline]] inline bool
runtime_f64_unary(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 2)
    return trap(current, kTrapOperandStackCapacity);
  const auto value = f64_from_bits(combine_u64(sp[-2], sp[-1]));
  replace_u64_operands<false>(sp, f64_to_bits(Operation(value)), 2);
  ++current.ip;
  return true;
}

template <bool (*Operation)(execution_context &, std::uint32_t *&,
                            std::uint32_t &)>
[[gnu::always_inline]] inline bool
runtime_conversion(execution_context *context, std::uint32_t *&sp,
                   [[maybe_unused]] std::uint32_t *&local_base,
                   [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  std::uint32_t trap_code = 0;
  if (!Operation(current, sp, trap_code)) {
    return trap(current,
                trap_code == 0 ? kTrapOperandStackCapacity : trap_code);
  }
  ++current.ip;
  return true;
}

[[gnu::always_inline]] inline bool
runtime_i64_const(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto value = read_s64(rel);
  if (!push_u64(current, sp, static_cast<std::uint64_t>(value))) {
    return trap(current, kTrapOperandStackCapacity);
  }
  current.ip += encoded_instruction_size<1>(current);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_f32_const(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  std::uint32_t bits = 0;
  std::memcpy(&bits, current.code + current.ip + 1, sizeof(bits));
  if (!push(current, sp, static_cast<std::uint32_t>(bits)))
    return trap(current, kTrapOperandStackCapacity);
  current.ip += 5;
  return true;
}

[[gnu::always_inline]] inline bool
runtime_f64_const(execution_context *context, std::uint32_t *&sp,
                  [[maybe_unused]] std::uint32_t *&local_base,
                  [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  std::uint64_t bits = 0;
  std::memcpy(&bits, current.code + current.ip + 1, sizeof(bits));
  if (!push_u64(current, sp, bits))
    return trap(current, kTrapOperandStackCapacity);
  current.ip += 9;
  return true;
}

template <std::uint32_t Width>
inline std::uint64_t sign_extend_memory_value(std::uint64_t value) {
  if constexpr (Width < sizeof(std::uint64_t)) {
    constexpr auto sign_bit = std::uint64_t{1} << (Width * 8 - 1);
    return (value ^ sign_bit) - sign_bit;
  }
  return value;
}

template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
[[gnu::always_inline]] inline bool
runtime_memory_load_decoded(execution_context *context, std::uint32_t *&sp,
                            [[maybe_unused]] std::uint32_t *&local_base,
                            std::uint32_t next_pc, std::uint32_t offset) {
  auto &current = *context;
  if (sp == current.stack_base)
    return trap(current, kTrapOperandStackCapacity);

  const auto address = sp[-1];
  if ((address & 0x8000'0000u) != 0)
    return runtime_api_boundary(current, current.ip);
  const auto effective_address = static_cast<std::uint64_t>(address) + offset;
  if (!linear_memory_range_is_valid(current, effective_address, Width)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (current.linear_memory_host_base == nullptr)
    return runtime_api_boundary(current, current.ip);

  if (current.stack_limit - (sp - 1) < ResultWords) {
    return trap(current, kTrapOperandStackCapacity);
  }
  std::uint64_t value = 0;
  for (std::uint32_t byte = 0; byte < Width; ++byte) {
    value |= static_cast<std::uint64_t>(
                 current.linear_memory_host_base[effective_address + byte])
             << (byte * 8);
  }
  if constexpr (Signed)
    value = sign_extend_memory_value<Width>(value);

  if constexpr (ResultWords == 2) {
    sp[-1] = static_cast<std::uint32_t>(value);
    *sp++ = static_cast<std::uint32_t>(value >> 32);
  } else {
    sp[-1] = static_cast<std::uint32_t>(value);
  }
  current.ip = next_pc;
  return true;
}

template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
[[gnu::always_inline]] inline bool
runtime_memory_load(execution_context *context, std::uint32_t *&sp,
                    std::uint32_t *&local_base,
                    [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto alignment = read_u32(rel);
  const auto offset = read_u32(rel);
  (void)alignment;
  return runtime_memory_load_decoded<Width, ResultWords, Signed>(
      context, sp, local_base,
      current.ip + encoded_instruction_size<2>(current), offset);
}

template <std::uint32_t Width, std::uint32_t ValueWords>
[[gnu::always_inline]] inline bool
runtime_memory_store_decoded(execution_context *context, std::uint32_t *&sp,
                             [[maybe_unused]] std::uint32_t *&local_base,
                             std::uint32_t next_pc, std::uint32_t offset) {
  auto &current = *context;
  if (sp < current.stack_base + ValueWords + 1)
    return trap(current, kTrapOperandStackCapacity);

  auto *address_stack_pointer = sp - ValueWords - 1;
  const auto address = sp[-static_cast<std::ptrdiff_t>(ValueWords + 1)];
  if ((address & 0x8000'0000u) != 0)
    return runtime_api_boundary(current, current.ip);
  const auto effective_address = static_cast<std::uint64_t>(address) + offset;
  if (!linear_memory_range_is_valid(current, effective_address, Width)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (current.linear_memory_host_base == nullptr)
    return runtime_api_boundary(current, current.ip);

  std::uint64_t value = sp[-static_cast<std::ptrdiff_t>(ValueWords)];
  if constexpr (ValueWords == 2) {
    value |= static_cast<std::uint64_t>(sp[-1]) << 32;
  }
  for (std::uint32_t byte = 0; byte < Width; ++byte) {
    current.linear_memory_host_base[effective_address + byte] =
        static_cast<std::uint8_t>(value >> (byte * 8));
  }
  sp = address_stack_pointer;
  current.ip = next_pc;
  return true;
}

template <std::uint32_t Width, std::uint32_t ValueWords>
[[gnu::always_inline]] inline bool
runtime_memory_store(execution_context *context, std::uint32_t *&sp,
                     std::uint32_t *&local_base,
                     [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto alignment = read_u32(rel);
  const auto offset = read_u32(rel);
  (void)alignment;
  return runtime_memory_store_decoded<Width, ValueWords>(
      context, sp, local_base,
      current.ip + encoded_instruction_size<2>(current), offset);
}

template <bool (*Operation)(execution_context &, std::uint32_t *&)>
[[gnu::always_inline]] inline bool runtime_fc_saturating_conversion_decoded(
    execution_context *context, std::uint32_t *&sp,
    [[maybe_unused]] std::uint32_t *&local_base, std::uint32_t next_pc,
    const std::uint8_t *&rel) {
  auto &current = *context;
  if (!Operation(current, sp))
    return trap(current, kTrapOperandStackCapacity);
  current.ip = next_pc;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

template <bool (*Operation)(execution_context &, std::uint32_t *&)>
[[gnu::always_inline]] inline bool
runtime_fc_saturating_conversion(execution_context *context, std::uint32_t *&sp,
                                 std::uint32_t *&local_base,
                                 [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto subopcode = read_u32(rel);
  if (subopcode > 7)
    return trap(current, kTrapUnreachable);
  return runtime_fc_saturating_conversion_decoded<Operation>(
      context, sp, local_base,
      current.ip + encoded_instruction_size<1>(current), rel);
}

[[gnu::always_inline]] inline bool
runtime_fc_memory_copy_decoded(execution_context *context, std::uint32_t *&sp,
                               [[maybe_unused]] std::uint32_t *&local_base,
                               std::uint32_t next_pc,
                               const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 3)
    return trap(current, kTrapOperandStackCapacity);

  auto *destination_stack_pointer = sp - 3;
  const auto destination = sp[-3];
  const auto source = sp[-2];
  const auto length = sp[-1];
  if (((destination | source) & 0x8000'0000u) != 0)
    return runtime_api_boundary(current, current.ip);
  if (!linear_memory_range_is_valid(current, destination, length) ||
      !linear_memory_range_is_valid(current, source, length)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (length != 0) {
    if (current.linear_memory_host_base == nullptr)
      return runtime_api_boundary(current, current.ip);
    auto *memory = current.linear_memory_host_base;
    std::memmove(memory + destination, memory + source, length);
  }
  sp = destination_stack_pointer;
  current.ip = next_pc;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_fc_memory_copy(execution_context *context, std::uint32_t *&sp,
                       std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto subopcode = read_u32(rel);
  const auto destination_memory = read_u32(rel);
  const auto source_memory = read_u32(rel);
  if (subopcode != 0x0A || destination_memory != 0 || source_memory != 0)
    return trap(current, kTrapUnreachable);
  return runtime_fc_memory_copy_decoded(
      context, sp, local_base,
      current.ip + encoded_instruction_size<3>(current), rel);
}

[[gnu::always_inline]] inline bool
runtime_fc_memory_fill_decoded(execution_context *context, std::uint32_t *&sp,
                               [[maybe_unused]] std::uint32_t *&local_base,
                               std::uint32_t next_pc,
                               const std::uint8_t *&rel) {
  auto &current = *context;
  if (sp < current.stack_base + 3)
    return trap(current, kTrapOperandStackCapacity);

  auto *destination_stack_pointer = sp - 3;
  const auto destination = sp[-3];
  const auto value = static_cast<std::uint8_t>(sp[-2]);
  const auto length = sp[-1];
  if (!linear_memory_range_is_valid(current, destination, length)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (length != 0) {
    if (current.linear_memory_host_base == nullptr)
      return runtime_api_boundary(current, current.ip);
    std::memset(current.linear_memory_host_base + destination, value, length);
  }
  sp = destination_stack_pointer;
  current.ip = next_pc;
  rel = interpreter_rel_for_current_block(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return true;
}

[[gnu::always_inline]] inline bool
runtime_fc_memory_fill(execution_context *context, std::uint32_t *&sp,
                       std::uint32_t *&local_base,
                       [[maybe_unused]] const std::uint8_t *&rel) {
  auto &current = *context;
  const auto subopcode = read_u32(rel);
  const auto memory_index = read_u32(rel);
  if (subopcode != 0x0B || memory_index != 0)
    return trap(current, kTrapUnreachable);
  return runtime_fc_memory_fill_decoded(
      context, sp, local_base,
      current.ip + encoded_instruction_size<2>(current), rel);
}

inline bool
runtime_memory_manager_api_boundary(execution_context *context,
                                    [[maybe_unused]] std::uint32_t *&sp,
                                    [[maybe_unused]] std::uint32_t *&local_base,
                                    const std::uint8_t *&rel) {
  (void)read_u32(rel);
  return runtime_api_boundary(*context, context->ip);
}

} // namespace

#endif

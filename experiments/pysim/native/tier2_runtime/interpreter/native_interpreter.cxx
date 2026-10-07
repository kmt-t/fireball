#include "../../../tier2_runtime/abi/interpreter_abi.hxx"
#include "../../../tier2_runtime/abi/native_abi.hxx"

#include <array>
#include <bit>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <limits>
#include <type_traits>

namespace {

#define FIREBALL_CPS_CALL FIREBALL_NATIVE_CPS_CALL

constexpr std::uint32_t kFallback = 0;
constexpr std::uint32_t kComplete = 1;
constexpr std::uint32_t kTrap = 2;
constexpr std::uint32_t kBlockBoundary = 3;
constexpr std::uint32_t kCallBoundary = 4;
constexpr std::uint32_t kStopAtBlockBoundaryFlag = 1u << 0;
constexpr std::uint32_t kStopAfterControlFlag = 1u << 1;
constexpr std::uint32_t kStopAtDefinedCallBoundaryFlag = 1u << 2;
constexpr std::uint32_t kPendingBlockHeadFlag = 1u << 5;
constexpr std::uint32_t kDispatchYield = 5;
constexpr std::uint32_t kNativeCallBoundary = 6;
constexpr std::uint32_t kDebugStop = 7;
// Internal Runtime API outcome. Only CPS handlers consume this value.
constexpr std::uint32_t kContinue = FIREBALL_NATIVE_OP_CONTINUE;
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
constexpr std::uint32_t kOpcodeFcPrefix = 0xFC;

using execution_context = fireball_execution_context_native;

static_assert(sizeof(void *) == 8,
              "native interpreter requires a 64-bit host ABI");
static_assert(sizeof(execution_context) == 96,
              "x86-64 execution_context ABI layout must remain 96 bytes");
static_assert(offsetof(execution_context, trap_code) == 0x58);

using op_result = fireball_op_result_native;

op_result fallback(execution_context &current, std::uint32_t ip,
                   std::uint32_t stack_height) {
  current.ip = ip;
  current.sp_offset = stack_height;
  current.stack_checkpoint = stack_height;
  current.trap_code = 0;
  return {kFallback};
}

op_result fallback(execution_context &current, std::uint32_t ip) {
  return fallback(current, ip, current.sp_offset);
}

op_result block_boundary(execution_context &current, std::uint32_t ip) {
  current.ip = ip;
  current.stack_checkpoint = current.sp_offset;
  current.trap_code = 0;
  return {kBlockBoundary};
}

op_result complete(execution_context &current) {
  current.ip = kSentinel;
  current.stack_checkpoint = current.sp_offset;
  current.trap_code = 0;
  return {kComplete};
}

op_result trap(execution_context &current, std::uint32_t code) {
  current.trap_code = code;
  return {kTrap};
}

op_result call_boundary(execution_context &current) {
  current.trap_code = 0;
  return {kCallBoundary};
}

op_result debug_stop(execution_context &current) {
  current.trap_code = 0;
  return {kDebugStop};
}

struct debug_context {
  execution_context execution;
  fb_native_debug_control *control;
};

static_assert(offsetof(debug_context, execution) == 0);
static_assert(offsetof(debug_context, control) == sizeof(execution_context));

struct debugger_aspect {
  [[gnu::noinline]] static bool before_instruction(execution_context &context) {
    auto &control = *reinterpret_cast<debug_context *>(&context)->control;
    if (control.enabled == 0)
      return false;
    const auto *frame =
        context.call_stack->size == 0
            ? nullptr
            : &context.call_stack->frames[context.call_stack->size - 1];
    if (frame == nullptr)
      return false;
    const auto pc = frame->function_view->code_pc_offset + context.ip;
    control.current_pc = pc;
    if (control.single_step != 0 && control.executed != 0) {
      control.stopped = 1;
      return true;
    }
    if (control.executed != 0) {
      std::uint32_t low = 0;
      std::uint32_t high = control.breakpoint_count;
      while (low < high) {
        const auto middle = low + (high - low) / 2;
        if (control.breakpoints[middle] < pc)
          low = middle + 1;
        else
          high = middle;
      }
      if (low < control.breakpoint_count && control.breakpoints[low] == pc) {
        control.stopped = 1;
        return true;
      }
    }
    control.executed = 1;
    return false;
  }
};

// Inlining this boundary makes each handler tail-jump through the opcode table.
template <typename Debugger = void>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL op_result
dispatch_next(execution_context *context, std::uint32_t *sp,
              std::uint32_t *local_base, std::uint32_t tos);

std::uint32_t top_value(const execution_context &current,
                        const std::uint32_t *sp) {
  return current.sp_offset == 0 ? 0 : sp[current.sp_offset - 1];
}

bool read_s32(const execution_context &current, std::uint32_t &ip,
              std::int32_t &value) {
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

[[gnu::noinline]] bool read_u32_remaining(const execution_context &current,
                                          std::uint32_t &ip,
                                          std::uint32_t &value,
                                          std::uint32_t result) {
  std::uint32_t shift = 7;
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

bool read_u32(const execution_context &current, std::uint32_t &ip,
              std::uint32_t &value) {
  if (ip >= current.code_size)
    return false;
  const auto first = current.code[ip++];
  if ((first & 0x80u) == 0) {
    value = first;
    return true;
  }
  return read_u32_remaining(current, ip, value, first & 0x7Fu);
}

bool pop(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
         std::uint32_t &value);
bool push(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
          std::uint32_t value);

bool read_s64(const execution_context &current, std::uint32_t &ip,
              std::int64_t &value) {
  std::uint64_t raw = 0;
  std::uint32_t shift = 0;
  while (ip < current.code_size && shift < 70) {
    const auto byte = current.code[ip++];
    raw |= static_cast<std::uint64_t>(byte & 0x7Fu) << shift;
    shift += 7;
    if ((byte & 0x80u) == 0) {
      if ((byte & 0x40u) != 0 && shift < 64)
        raw |= ~0ull << shift;
      value = static_cast<std::int64_t>(raw);
      return true;
    }
  }
  return false;
}

bool pop_u64(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
             std::uint64_t &value) {
  if (current.sp_offset < 2)
    return false;
  const auto low = sp[-2];
  const auto high = tos;
  current.sp_offset -= 2;
  sp -= 2;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  value = static_cast<std::uint64_t>(low) |
          (static_cast<std::uint64_t>(high) << 32);
  return true;
}

bool push_u64(execution_context &current, std::uint32_t *&sp,
              std::uint32_t &tos, std::uint64_t value) {
  if (current.sp_capacity < 2 || current.sp_offset > current.sp_capacity - 2)
    return false;
  *sp++ = static_cast<std::uint32_t>(value);
  tos = static_cast<std::uint32_t>(value >> 32);
  *sp++ = tos;
  current.sp_offset += 2;
  return true;
}

std::uint64_t combine_u64(std::uint32_t low, std::uint32_t high) {
  return static_cast<std::uint64_t>(low) |
         (static_cast<std::uint64_t>(high) << 32);
}

template <bool ResultIsBool>
void replace_u64_operands(execution_context &current, std::uint32_t *&sp,
                          std::uint32_t &tos, std::uint64_t result,
                          std::uint32_t operand_words) {
  constexpr auto result_words = ResultIsBool ? 1u : 2u;
  auto *target = sp - operand_words;
  current.sp_offset -= operand_words - result_words;
  sp = target + result_words;
  tos = static_cast<std::uint32_t>(result);
  target[0] = tos;
  if constexpr (!ResultIsBool) {
    tos = static_cast<std::uint32_t>(result >> 32);
    target[1] = tos;
  }
}

float f32_from_bits(std::uint32_t value) { return std::bit_cast<float>(value); }
std::uint32_t f32_to_bits(float value) {
  return std::bit_cast<std::uint32_t>(value);
}
double f64_from_bits(std::uint64_t value) {
  return std::bit_cast<double>(value);
}
std::uint64_t f64_to_bits(double value) {
  return std::bit_cast<std::uint64_t>(value);
}

bool pop_f32(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
             float &value) {
  std::uint32_t raw = 0;
  if (!pop(current, sp, tos, raw))
    return false;
  value = f32_from_bits(raw);
  return true;
}

bool push_f32(execution_context &current, std::uint32_t *&sp,
              std::uint32_t &tos, float value) {
  return push(current, sp, tos, f32_to_bits(value));
}

bool pop_f64(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
             double &value) {
  std::uint64_t raw = 0;
  if (!pop_u64(current, sp, tos, raw))
    return false;
  value = f64_from_bits(raw);
  return true;
}

bool push_f64(execution_context &current, std::uint32_t *&sp,
              std::uint32_t &tos, double value) {
  return push_u64(current, sp, tos, f64_to_bits(value));
}

const fireball_call_frame_native *
active_frame(const execution_context &current) {
  if (current.call_stack == nullptr || current.call_stack->size == 0 ||
      current.call_base >= current.call_stack->size) {
    return nullptr;
  }
  return &current.call_stack->frames[current.call_stack->size - 1];
}

bool finish_current_function(execution_context &current,
                             std::uint32_t *&local_base) {
  auto *stack = current.call_stack;
  if (stack == nullptr || stack->size <= current.call_base + 1) {
    current.ip = kSentinel;
    return true;
  }

  const auto finished = stack->frames[stack->size - 1];
  assert(finished.return_ip != kSentinel);
  assert(finished.return_func_index != kSentinel);
  --stack->size;
  current.local_offset = finished.local_base;
  current.control_stack->size = finished.control_base;

  const auto &caller = stack->frames[stack->size - 1];
  assert(caller.func_index == finished.return_func_index);
  assert(caller.function_view != nullptr);
  local_base += static_cast<std::ptrdiff_t>(caller.local_base) -
                static_cast<std::ptrdiff_t>(finished.local_base);
  current.code = caller.code;
  current.code_size = caller.code_size;
  current.control_base = caller.control_base;
  current.ip = finished.return_ip;
  return false;
}

bool signatures_match(const fireball_wasm_module_execution_view_native &module,
                      std::uint32_t expected_type, std::uint32_t actual_type) {
  if (expected_type >= module.type_count || actual_type >= module.type_count ||
      module.types == nullptr) {
    return false;
  }
  const auto &expected = module.types[expected_type];
  const auto &actual = module.types[actual_type];
  if (expected.param_count != actual.param_count ||
      expected.result_count != actual.result_count) {
    return false;
  }
  if (expected.param_count == 0 && expected.result_count == 0)
    return true;
  if (module.signature_bytes == nullptr)
    return false;
  for (std::uint32_t index = 0; index < expected.param_count; ++index) {
    if (module.signature_bytes[expected.param_offset + index] !=
        module.signature_bytes[actual.param_offset + index]) {
      return false;
    }
  }
  for (std::uint32_t index = 0; index < expected.result_count; ++index) {
    if (module.signature_bytes[expected.result_offset + index] !=
        module.signature_bytes[actual.result_offset + index]) {
      return false;
    }
  }
  return true;
}

template <bool Indirect>
[[gnu::always_inline]] inline op_result
runtime_call(execution_context *context, std::uint32_t *&sp,
             std::uint32_t *&local_stack, std::uint32_t &tos) {
  auto &current = *context;
  const auto source_ip = current.ip;
  auto operand_ip = source_ip + 1;
  std::uint32_t function_index = 0;
  std::uint32_t expected_type = kSentinel;
  std::uint32_t table_index = 0;
  std::uint32_t table_slot = 0;
  const auto *caller = active_frame(current);
  if (caller == nullptr || caller->function_view == nullptr ||
      caller->function_view->module_view == nullptr) {
    return trap(current, kTrapUnreachable);
  }
  const auto &module = *caller->function_view->module_view;

  if constexpr (Indirect) {
    if (!read_u32(current, operand_ip, expected_type) ||
        !read_u32(current, operand_ip, table_index)) {
      return trap(current, kTrapUnreachable);
    }
    if (table_index >= module.table_count || module.tables == nullptr) {
      return trap(current, kTrapTableIndexOutOfBounds);
    }
    const auto &table = module.tables[table_index];
    if (current.sp_offset == 0)
      return trap(current, kTrapOperandStackCapacity);
    table_slot = tos;
    if (table_slot >= table.size || table.function_indices == nullptr) {
      return trap(current, kTrapTableIndexOutOfBounds);
    }
    function_index = table.function_indices[table_slot];
    if (function_index == kSentinel)
      return trap(current, kTrapTableSlotUninitialized);
  } else if (!read_u32(current, operand_ip, function_index)) {
    return trap(current, kTrapUnreachable);
  }

  const auto &target = module.functions[function_index];
  if constexpr (Indirect) {
    if (!signatures_match(module, expected_type, target.type_index)) {
      return trap(current, kTrapIndirectCallTypeMismatch);
    }
  }
  // Imports cross the host-call boundary; defined guest functions stay in this
  // native dispatcher and never return to Python opcode handling.
  if (target.is_import != 0)
    return fallback(current, source_ip);
  if (current.call_stack->size >= FIREBALL_NATIVE_CALL_STACK_CAPACITY) {
    return trap(current, kTrapCallStackCapacity);
  }
  if (target.local_slot_count > current.local_capacity ||
      current.local_offset > current.local_capacity - target.local_slot_count) {
    return trap(current, kTrapLocalStackCapacity);
  }

  auto *local_area = local_stack - caller->local_base;
  const auto local_base = current.local_offset;
  const auto local_end = local_base + target.local_slot_count;
  for (std::uint32_t index = local_base + target.param_packed_slot_count;
       index < local_end; ++index) {
    local_area[index] = 0;
  }

  const auto argument_base =
      current.sp_offset - target.param_packed_slot_count - (Indirect ? 1u : 0u);
  const auto *arguments =
      sp - target.param_packed_slot_count - (Indirect ? 1u : 0u);
  auto *argument = arguments;
  for (std::uint32_t index = 0; index < target.param_count; ++index) {
    const auto offset = target.local_offsets[index];
    const auto width = target.local_sizes[index];
    for (std::uint32_t word = 0; word < width; ++word) {
      local_area[local_base + offset + word] = *argument++;
    }
  }

  auto &callee = current.call_stack->frames[current.call_stack->size];
  callee.func_index = function_index;
  callee.code = target.code;
  callee.code_size = target.code_size;
  callee.function_view = &target;
  callee.local_base = local_base;
  callee.local_count = target.local_count;
  callee.local_slot_count = target.local_slot_count;
  callee.local_offsets = target.local_offsets;
  callee.local_sizes = target.local_sizes;
  callee.param_count = target.param_count;
  callee.param_packed_slot_count = target.param_packed_slot_count;
  callee.result_arity = target.result_arity;
  callee.control_base = current.control_stack->size;
  callee.return_ip = operand_ip;
  callee.return_func_index = caller->func_index;
  callee.boundary_next_pc = kSentinel;
  callee.boundary_loops_to = kSentinel;

  sp -= current.sp_offset - argument_base;
  current.sp_offset = argument_base;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  current.local_offset = local_end;
  ++current.call_stack->size;
  current.ip = 0;
  current.code = target.code;
  current.code_size = target.code_size;
  current.control_base = callee.control_base;
  if ((current.runtime_flags & kStopAtDefinedCallBoundaryFlag) != 0) {
    return call_boundary(current);
  }
  local_stack = local_area + local_base;
  return {kContinue};
}

template <typename Debugger, bool Indirect>
FIREBALL_CPS_CALL op_result h_call(execution_context *context,
                                   std::uint32_t *sp,
                                   std::uint32_t *local_stack,
                                   std::uint32_t tos) {
  const auto outcome = runtime_call<Indirect>(context, sp, local_stack, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_stack,
                                                     tos);
}

bool is_requested_block_boundary(const execution_context &current) {
  const auto *frame = active_frame(current);
  if (frame == nullptr)
    return false;
  const auto pc = frame->function_view->code_pc_offset + current.ip;
  return pc == frame->boundary_next_pc || pc == frame->boundary_loops_to;
}

bool should_stop_after_control(const execution_context &current) {
  return (current.runtime_flags & kStopAfterControlFlag) != 0;
}

void record_loop_backedge(execution_context &current, std::uint32_t source_ip,
                          std::uint32_t target_ip) {
  if (target_ip < source_ip && current.loop_jump_count < UINT32_MAX) {
    ++current.loop_jump_count;
  }
}

const fireball_control_map_entry_native *
control_entry(const execution_context &current, std::uint32_t ip) {
  const auto *frame = active_frame(current);
  if (frame == nullptr || frame->function_view == nullptr ||
      frame->function_view->control_map == nullptr || ip >= current.code_size) {
    return nullptr;
  }
  return &frame->function_view->control_map[ip];
}

struct local_value_span {
  std::uint32_t offset;
  std::uint32_t width;
};

local_value_span local_span(const fireball_call_frame_native &frame,
                            std::uint32_t index) {
  const auto offset = static_cast<std::uint32_t>(frame.local_offsets[index]);
  const auto width = static_cast<std::uint32_t>(frame.local_sizes[index]);
  return {offset, width};
}

bool branch(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
            std::uint32_t depth, std::uint32_t &next_ip) {
  const auto frame_count = current.control_stack->size - current.control_base;
  if (depth > frame_count)
    return false;
  const auto *call_frame = active_frame(current);
  const auto outer_result_arity =
      call_frame == nullptr ? 0u : call_frame->result_arity;
  const auto target_index = frame_count - depth - 1;
  std::uint32_t result_arity = outer_result_arity;
  std::uint32_t saved_height = 0;
  std::uint32_t target_kind = 0;
  if (depth < frame_count) {
    const auto &target =
        current.control_stack->frames[current.control_base + target_index];
    saved_height = target.stack_height;
    target_kind = target.kind;
    result_arity = target_kind == 1u ? 0u : target.result_arity;
  }
  if (result_arity > 2 || saved_height > current.sp_offset ||
      result_arity > current.sp_capacity - saved_height ||
      result_arity > current.sp_offset - saved_height) {
    return false;
  }
  const auto result0 =
      result_arity >= 1 ? sp[-static_cast<std::ptrdiff_t>(result_arity)] : 0;
  const auto result1 = result_arity >= 2 ? sp[-1] : 0;
  sp -= current.sp_offset - saved_height;
  current.sp_offset = saved_height;
  if (result_arity >= 1)
    *sp++ = result0;
  if (result_arity >= 2)
    *sp++ = result1;
  current.sp_offset += result_arity;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  if (depth == frame_count) {
    current.control_stack->size = current.control_base;
    next_ip = kSentinel;
    return true;
  }
  if (target_kind == 1u) {
    current.control_stack->size = current.control_base + target_index + 1;
    next_ip = current.control_stack->frames[current.control_base + target_index]
                  .start +
              2;
  } else {
    current.control_stack->size = current.control_base + target_index;
    next_ip = current.control_stack->frames[current.control_base + target_index]
                  .match_end +
              1;
  }
  return true;
}

void prune_control_frames_after_static_jump(execution_context &current,
                                            std::uint32_t target_ip) {
  // A loader-resolved basic-block successor may skip one or more `end`
  // handlers. Drop lexical frames whose ranges no longer contain its target
  // before continuing native dispatch.
  while (current.control_stack->size > current.control_base) {
    const auto &frame =
        current.control_stack->frames[current.control_stack->size - 1];
    if (target_ip <= frame.match_end)
      return;
    --current.control_stack->size;
  }
}

bool pop(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
         std::uint32_t &value) {
  if (current.sp_offset == 0) {
    return false;
  }
  value = tos;
  --current.sp_offset;
  --sp;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  return true;
}

bool push(execution_context &current, std::uint32_t *&sp, std::uint32_t &tos,
          std::uint32_t value) {
  if (current.sp_offset >= current.sp_capacity) {
    return false;
  }
  *sp++ = value;
  ++current.sp_offset;
  tos = value;
  return true;
}

std::uint32_t i32_eqz(std::uint32_t value) { return value == 0; }
std::uint32_t i32_clz(std::uint32_t value) { return std::countl_zero(value); }
std::uint32_t i32_ctz(std::uint32_t value) { return std::countr_zero(value); }
std::uint32_t i32_popcnt(std::uint32_t value) { return std::popcount(value); }
std::uint32_t i32_extend8(std::uint32_t value) {
  return static_cast<std::uint32_t>(
      static_cast<std::int32_t>(static_cast<std::int8_t>(value)));
}
std::uint32_t i32_extend16(std::uint32_t value) {
  return static_cast<std::uint32_t>(
      static_cast<std::int32_t>(static_cast<std::int16_t>(value)));
}

template <std::uint32_t (*Operation)(std::uint32_t)>
[[gnu::always_inline]] inline op_result
runtime_i32_unary(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset == 0)
    return fallback(current, current.ip);
  tos = Operation(tos);
  sp[-1] = tos;
  ++current.ip;
  return {kContinue};
}

template <typename Debugger, std::uint32_t (*Operation)(std::uint32_t)>
FIREBALL_CPS_CALL op_result h_i32_unary(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome = runtime_i32_unary<Operation>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <std::uint32_t (*Operation)(std::uint32_t, std::uint32_t)>
bool evaluate_plain(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t &result,
                    std::uint32_t &trap_code) {
  result = Operation(lhs, rhs);
  trap_code = 0;
  return true;
}

constexpr std::uint32_t op_eq(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs == rhs;
}
constexpr std::uint32_t op_ne(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs != rhs;
}
constexpr std::uint32_t op_lt_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) < static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_lt_u(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs < rhs;
}
constexpr std::uint32_t op_gt_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) > static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_gt_u(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs > rhs;
}
constexpr std::uint32_t op_le_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) <= static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_le_u(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs <= rhs;
}
constexpr std::uint32_t op_ge_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::int32_t>(lhs) >= static_cast<std::int32_t>(rhs);
}
constexpr std::uint32_t op_ge_u(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs >= rhs;
}
constexpr std::uint32_t op_add(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs + rhs;
}
constexpr std::uint32_t op_sub(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs - rhs;
}
constexpr std::uint32_t op_mul(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs * rhs;
}
constexpr std::uint32_t op_and(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs & rhs;
}
constexpr std::uint32_t op_or(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs | rhs;
}
constexpr std::uint32_t op_xor(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs ^ rhs;
}
constexpr std::uint32_t op_shl(std::uint32_t lhs, std::uint32_t rhs) {
  return lhs << (rhs & 31u);
}
constexpr std::uint32_t op_shr_s(std::uint32_t lhs, std::uint32_t rhs) {
  return static_cast<std::uint32_t>(static_cast<std::int32_t>(lhs) >>
                                    (rhs & 31u));
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

bool evaluate_div_s(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t &result,
                    std::uint32_t &trap_code) {
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

bool evaluate_rem_s(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t &result,
                    std::uint32_t &trap_code) {
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

bool evaluate_div_u(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t &result,
                    std::uint32_t &trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs / rhs;
  trap_code = 0;
  return true;
}

bool evaluate_rem_u(std::uint32_t lhs, std::uint32_t rhs, std::uint32_t &result,
                    std::uint32_t &trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs % rhs;
  trap_code = 0;
  return true;
}

constexpr std::uint64_t i64_eq(std::uint64_t a, std::uint64_t b) {
  return a == b;
}
constexpr std::uint64_t i64_ne(std::uint64_t a, std::uint64_t b) {
  return a != b;
}
constexpr std::uint64_t i64_lt_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) < static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_lt_u(std::uint64_t a, std::uint64_t b) {
  return a < b;
}
constexpr std::uint64_t i64_gt_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) > static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_gt_u(std::uint64_t a, std::uint64_t b) {
  return a > b;
}
constexpr std::uint64_t i64_le_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) <= static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_le_u(std::uint64_t a, std::uint64_t b) {
  return a <= b;
}
constexpr std::uint64_t i64_ge_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::int64_t>(a) >= static_cast<std::int64_t>(b);
}
constexpr std::uint64_t i64_ge_u(std::uint64_t a, std::uint64_t b) {
  return a >= b;
}
constexpr std::uint64_t i64_add(std::uint64_t a, std::uint64_t b) {
  return a + b;
}
constexpr std::uint64_t i64_sub(std::uint64_t a, std::uint64_t b) {
  return a - b;
}
constexpr std::uint64_t i64_mul(std::uint64_t a, std::uint64_t b) {
  return a * b;
}
constexpr std::uint64_t i64_and(std::uint64_t a, std::uint64_t b) {
  return a & b;
}
constexpr std::uint64_t i64_or(std::uint64_t a, std::uint64_t b) {
  return a | b;
}
constexpr std::uint64_t i64_xor(std::uint64_t a, std::uint64_t b) {
  return a ^ b;
}
constexpr std::uint64_t i64_shl(std::uint64_t a, std::uint64_t b) {
  return a << (b & 63u);
}
constexpr std::uint64_t i64_shr_s(std::uint64_t a, std::uint64_t b) {
  return static_cast<std::uint64_t>(static_cast<std::int64_t>(a) >> (b & 63u));
}
constexpr std::uint64_t i64_shr_u(std::uint64_t a, std::uint64_t b) {
  return a >> (b & 63u);
}
constexpr std::uint64_t i64_rotl(std::uint64_t a, std::uint64_t b) {
  const auto shift = b & 63u;
  return (a << shift) | (a >> ((64u - shift) & 63u));
}
constexpr std::uint64_t i64_rotr(std::uint64_t a, std::uint64_t b) {
  const auto shift = b & 63u;
  return (a >> shift) | (a << ((64u - shift) & 63u));
}

bool i64_div_s(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t &result,
               std::uint32_t &trap_code) {
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
  if ((a < 0) != (b < 0))
    quotient = 0ull - quotient;
  result = quotient;
  trap_code = 0;
  return true;
}

bool i64_div_u(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t &result,
               std::uint32_t &trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs / rhs;
  trap_code = 0;
  return true;
}

bool i64_rem_s(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t &result,
               std::uint32_t &trap_code) {
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
    if (a < 0)
      remainder = 0ull - remainder;
    result = remainder;
  }
  trap_code = 0;
  return true;
}

bool i64_rem_u(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t &result,
               std::uint32_t &trap_code) {
  if (rhs == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = lhs % rhs;
  trap_code = 0;
  return true;
}

constexpr std::uint64_t i64_eqz(std::uint64_t value) { return value == 0; }
std::uint64_t i64_clz(std::uint64_t value) {
  return value == 0 ? 64 : __builtin_clzll(value);
}
std::uint64_t i64_ctz(std::uint64_t value) {
  return value == 0 ? 64 : __builtin_ctzll(value);
}
std::uint64_t i64_popcnt(std::uint64_t value) {
  return __builtin_popcountll(value);
}
std::uint64_t i64_extend8(std::uint64_t value) {
  return static_cast<std::uint64_t>(
      static_cast<std::int64_t>(static_cast<std::int8_t>(value)));
}
std::uint64_t i64_extend16(std::uint64_t value) {
  return static_cast<std::uint64_t>(
      static_cast<std::int64_t>(static_cast<std::int16_t>(value)));
}
std::uint64_t i64_extend32(std::uint64_t value) {
  return static_cast<std::uint64_t>(
      static_cast<std::int64_t>(static_cast<std::int32_t>(value)));
}

float f32_div(float a, float b) {
  if (b != 0.0f)
    return a / b;
  if (a == 0.0f || std::isnan(a))
    return std::numeric_limits<float>::quiet_NaN();
  return std::copysign(std::numeric_limits<float>::infinity(),
                       std::signbit(a) == std::signbit(b) ? 1.0f : -1.0f);
}
double f64_div(double a, double b) {
  if (b != 0.0)
    return a / b;
  if (a == 0.0 || std::isnan(a))
    return std::numeric_limits<double>::quiet_NaN();
  return std::copysign(std::numeric_limits<double>::infinity(),
                       std::signbit(a) == std::signbit(b) ? 1.0 : -1.0);
}

template <typename T> T float_min(T a, T b) {
  if (std::isnan(a) || std::isnan(b))
    return std::numeric_limits<T>::quiet_NaN();
  if (a == 0 && b == 0)
    return std::signbit(a) ? a : b;
  return a < b ? a : b;
}

template <typename T> T float_max(T a, T b) {
  if (std::isnan(a) || std::isnan(b))
    return std::numeric_limits<T>::quiet_NaN();
  if (a == 0 && b == 0)
    return std::signbit(a) ? b : a;
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

template <typename T>
bool truncate_i32(T value, bool is_signed, std::uint32_t &result) {
  if (!std::isfinite(value))
    return false;
  if (is_signed) {
    if (value < static_cast<T>(-2147483648.0) ||
        value >= static_cast<T>(2147483648.0)) {
      return false;
    }
    result = static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
    return true;
  }
  if (value <= static_cast<T>(-1.0) || value >= static_cast<T>(4294967296.0))
    return false;
  result = static_cast<std::uint32_t>(value);
  return true;
}

template <typename T>
bool truncate_i64(T value, bool is_signed, std::uint64_t &result) {
  if (!std::isfinite(value))
    return false;
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
  if (value <= static_cast<T>(-1.0) ||
      value >= static_cast<T>(18446744073709551616.0)) {
    return false;
  }
  result = static_cast<std::uint64_t>(value);
  return true;
}

template <typename T, bool Signed> std::uint32_t truncate_sat_i32(T value) {
  if (std::isnan(value))
    return 0;
  if constexpr (Signed) {
    constexpr auto lower = static_cast<T>(-2147483648.0);
    constexpr auto upper = static_cast<T>(2147483648.0);
    if (value <= lower)
      return 0x8000'0000u;
    if (value >= upper)
      return 0x7FFF'FFFFu;
    return static_cast<std::uint32_t>(static_cast<std::int32_t>(value));
  } else {
    constexpr auto upper = static_cast<T>(4294967296.0);
    if (value <= static_cast<T>(0))
      return 0;
    if (value >= upper)
      return 0xFFFF'FFFFu;
    return static_cast<std::uint32_t>(value);
  }
}

template <typename T, bool Signed> std::uint64_t truncate_sat_i64(T value) {
  if (std::isnan(value))
    return 0;
  if constexpr (Signed) {
    constexpr auto lower = static_cast<T>(-9223372036854775808.0);
    constexpr auto upper = static_cast<T>(9223372036854775808.0);
    if (value <= lower)
      return 0x8000'0000'0000'0000ull;
    if (value >= upper)
      return 0x7FFF'FFFF'FFFF'FFFFull;
    return static_cast<std::uint64_t>(static_cast<std::int64_t>(value));
  } else {
    constexpr auto upper = static_cast<T>(18446744073709551616.0);
    if (value <= static_cast<T>(0))
      return 0;
    if (value >= upper)
      return 0xFFFF'FFFF'FFFF'FFFFull;
    return static_cast<std::uint64_t>(value);
  }
}

template <typename T, bool Signed>
bool conversion_truncate_sat_i32(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &tos) {
  T value = 0;
  const bool popped = [&]() {
    if constexpr (sizeof(T) == sizeof(float)) {
      return pop_f32(current, sp, tos, value);
    } else {
      return pop_f64(current, sp, tos, value);
    }
  }();
  return popped && push(current, sp, tos, truncate_sat_i32<T, Signed>(value));
}

template <typename T, bool Signed>
bool conversion_truncate_sat_i64(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &tos) {
  T value = 0;
  const bool popped = [&]() {
    if constexpr (sizeof(T) == sizeof(float)) {
      return pop_f32(current, sp, tos, value);
    } else {
      return pop_f64(current, sp, tos, value);
    }
  }();
  return popped &&
         push_u64(current, sp, tos, truncate_sat_i64<T, Signed>(value));
}

bool linear_memory_range_is_valid(const execution_context &current,
                                  std::uint64_t offset, std::uint32_t length) {
  const auto wide_length = static_cast<std::uint64_t>(length);
  return offset <= current.linear_memory_size &&
         wide_length <= current.linear_memory_size - offset;
}

bool conversion_i32_wrap_i64(execution_context &current, std::uint32_t *&sp,
                             std::uint32_t &tos, std::uint32_t &trap_code) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, tos, value) ||
      !push(current, sp, tos, static_cast<std::uint32_t>(value))) {
    return false;
  }
  trap_code = 0;
  return true;
}

template <typename T, bool Signed>
bool conversion_truncate_i32(execution_context &current, std::uint32_t *&sp,
                             std::uint32_t &tos, std::uint32_t &trap_code) {
  T value = 0;
  const bool popped = [&]() {
    if constexpr (sizeof(T) == sizeof(float)) {
      return pop_f32(current, sp, tos, value);
    } else {
      return pop_f64(current, sp, tos, value);
    }
  }();
  std::uint32_t result = 0;
  if (!popped || !truncate_i32(value, Signed, result) ||
      !push(current, sp, tos, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

template <typename T, bool Signed>
bool conversion_truncate_i64(execution_context &current, std::uint32_t *&sp,
                             std::uint32_t &tos, std::uint32_t &trap_code) {
  T value = 0;
  const bool popped = [&]() {
    if constexpr (sizeof(T) == sizeof(float)) {
      return pop_f32(current, sp, tos, value);
    } else {
      return pop_f64(current, sp, tos, value);
    }
  }();
  std::uint64_t result = 0;
  if (!popped || !truncate_i64(value, Signed, result) ||
      !push_u64(current, sp, tos, result)) {
    trap_code = kTrapInvalidConversion;
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_extend_i32_s(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &tos, std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, tos, value) ||
      !push_u64(current, sp, tos,
                static_cast<std::uint64_t>(static_cast<std::int64_t>(
                    static_cast<std::int32_t>(value))))) {
    return false;
  }
  trap_code = 0;
  return true;
}

bool conversion_i64_extend_i32_u(execution_context &current, std::uint32_t *&sp,
                                 std::uint32_t &tos, std::uint32_t &trap_code) {
  std::uint32_t value = 0;
  if (!pop(current, sp, tos, value) || !push_u64(current, sp, tos, value))
    return false;
  trap_code = 0;
  return true;
}

template <typename T>
bool conversion_pop_i32(execution_context &current, std::uint32_t *&sp,
                        std::uint32_t &tos, std::uint32_t &, bool is_signed,
                        bool to_f32) {
  std::uint32_t value = 0;
  if (!pop(current, sp, tos, value))
    return false;
  const auto integer =
      is_signed ? static_cast<double>(static_cast<std::int32_t>(value))
                : static_cast<double>(value);
  if (to_f32)
    return push_f32(current, sp, tos, static_cast<float>(integer));
  return push_f64(current, sp, tos, integer);
}

template <typename T>
bool conversion_pop_i64(execution_context &current, std::uint32_t *&sp,
                        std::uint32_t &tos, std::uint32_t &, bool is_signed,
                        bool to_f32) {
  std::uint64_t value = 0;
  if (!pop_u64(current, sp, tos, value))
    return false;
  const auto integer =
      is_signed ? static_cast<long double>(static_cast<std::int64_t>(value))
                : static_cast<long double>(value);
  if (to_f32)
    return push_f32(current, sp, tos, static_cast<float>(integer));
  return push_f64(current, sp, tos, static_cast<double>(integer));
}

bool conversion_f32_convert_i32_s(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i32<float>(c, s, tos, t, true, true);
}
bool conversion_f32_convert_i32_u(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i32<float>(c, s, tos, t, false, true);
}
bool conversion_f64_convert_i32_s(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i32<double>(c, s, tos, t, true, false);
}
bool conversion_f64_convert_i32_u(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i32<double>(c, s, tos, t, false, false);
}
bool conversion_f32_convert_i64_s(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i64<float>(c, s, tos, t, true, true);
}
bool conversion_f32_convert_i64_u(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i64<float>(c, s, tos, t, false, true);
}
bool conversion_f64_convert_i64_s(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i64<double>(c, s, tos, t, true, false);
}
bool conversion_f64_convert_i64_u(execution_context &c, std::uint32_t *&s,
                                  std::uint32_t &tos, std::uint32_t &t) {
  return conversion_pop_i64<double>(c, s, tos, t, false, false);
}

bool conversion_f64_promote_f32(execution_context &current, std::uint32_t *&sp,
                                std::uint32_t &tos, std::uint32_t &trap_code) {
  float value = 0;
  if (!pop_f32(current, sp, tos, value) ||
      !push_f64(current, sp, tos, static_cast<double>(value)))
    return false;
  trap_code = 0;
  return true;
}

bool conversion_f32_demote_f64(execution_context &current, std::uint32_t *&sp,
                               std::uint32_t &tos, std::uint32_t &trap_code) {
  double value = 0;
  if (!pop_f64(current, sp, tos, value) ||
      !push_f32(current, sp, tos, static_cast<float>(value)))
    return false;
  trap_code = 0;
  return true;
}

bool conversion_identity(execution_context &, std::uint32_t *&, std::uint32_t &,
                         std::uint32_t &trap_code) {
  trap_code = 0;
  return true;
}

[[gnu::always_inline]] inline op_result
runtime_nop(execution_context *context) {
  auto &current = *context;
  current.ip += 1;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_nop(execution_context *context, std::uint32_t *sp,
                                  std::uint32_t *local_base,
                                  std::uint32_t tos) {
  const auto outcome = runtime_nop(context);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_else(execution_context *context) {
  auto &current = *context;
  if (current.control_stack->size <= current.control_base) {
    return fallback(current, current.ip);
  }
  const auto frame =
      current.control_stack->frames[current.control_stack->size - 1];
  current.control_stack->size -= 1;
  const auto *call_frame = active_frame(current);
  current.ip =
      call_frame != nullptr && call_frame->boundary_next_pc != kSentinel
          ? call_frame->boundary_next_pc -
                call_frame->function_view->code_pc_offset
          : frame.match_end + 1;
  prune_control_frames_after_static_jump(current, current.ip);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_else(execution_context *context,
                                   std::uint32_t *sp, std::uint32_t *local_base,
                                   std::uint32_t tos) {
  const auto outcome = runtime_else(context);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <bool IsLoop>
[[gnu::always_inline]] inline op_result
runtime_block(execution_context *context) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  if (entry == nullptr || entry->match_end == kSentinel ||
      current.control_stack->size >= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY) {
    return fallback(current, current.ip);
  }
  auto &frame = current.control_stack->frames[current.control_stack->size++];
  frame.kind = IsLoop ? 1u : 0u;
  frame.start = current.ip;
  frame.match_end = entry->match_end;
  frame.stack_height = current.sp_offset;
  frame.result_arity = static_cast<std::uint16_t>(entry->result_arity);
  current.ip = entry->next_pc;
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger, bool IsLoop>
FIREBALL_CPS_CALL op_result h_block(execution_context *context,
                                    std::uint32_t *sp,
                                    std::uint32_t *local_base,
                                    std::uint32_t tos) {
  const auto outcome = runtime_block<IsLoop>(context);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_if(execution_context *context, std::uint32_t *&sp, std::uint32_t &tos) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  std::uint32_t condition = 0;
  if (entry == nullptr || entry->match_end == kSentinel ||
      !pop(current, sp, tos, condition)) {
    return fallback(current, current.ip);
  }
  if (condition == 0 && entry->else_offset == kSentinel) {
    current.ip = entry->match_end + 1;
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return {kContinue};
  }
  if (current.control_stack->size >= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY) {
    return fallback(current, current.ip, current.sp_offset + 1);
  }
  auto &frame = current.control_stack->frames[current.control_stack->size++];
  frame.kind = 2u;
  frame.start = current.ip;
  frame.match_end = entry->match_end;
  frame.stack_height = current.sp_offset;
  frame.result_arity = static_cast<std::uint16_t>(entry->result_arity);
  current.ip = condition == 0 ? entry->else_offset + 1 : entry->next_pc;
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_if(execution_context *context, std::uint32_t *sp,
                                 std::uint32_t *local_base, std::uint32_t tos) {
  const auto outcome = runtime_if(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result runtime_br(execution_context *context,
                                                   std::uint32_t *&sp,
                                                   std::uint32_t *&local_base,
                                                   std::uint32_t &tos) {
  auto &current = *context;
  const auto source_ip = current.ip;
  auto operand_ip = current.ip + 1;
  std::uint32_t depth = 0;
  std::uint32_t next_ip = 0;
  if (!read_u32(current, operand_ip, depth) ||
      !branch(current, sp, tos, depth, next_ip)) {
    return fallback(current, current.ip);
  }
  if (next_ip == kSentinel) {
    const bool outermost = current.call_stack == nullptr ||
                           current.call_stack->size <= current.call_base + 1;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return {kContinue};
  }
  const auto *call_frame = active_frame(current);
  if (call_frame != nullptr && call_frame->boundary_next_pc != kSentinel) {
    next_ip = call_frame->boundary_next_pc -
              call_frame->function_view->code_pc_offset;
  }
  prune_control_frames_after_static_jump(current, next_ip);
  record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_br(execution_context *context, std::uint32_t *sp,
                                 std::uint32_t *local_base, std::uint32_t tos) {
  const auto outcome = runtime_br(context, sp, local_base, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_br_if(execution_context *context, std::uint32_t *&sp,
              std::uint32_t *&local_base, std::uint32_t &tos) {
  auto &current = *context;
  const auto source_ip = current.ip;
  auto operand_ip = current.ip + 1;
  std::uint32_t depth = 0;
  std::uint32_t condition = 0;
  if (!read_u32(current, operand_ip, depth) ||
      !pop(current, sp, tos, condition)) {
    return fallback(current, current.ip);
  }
  if (condition == 0) {
    current.ip = operand_ip;
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return {kContinue};
  }
  std::uint32_t next_ip = 0;
  if (!branch(current, sp, tos, depth, next_ip))
    return fallback(current, current.ip, current.sp_offset + 1);
  if (next_ip == kSentinel) {
    const bool outermost = current.call_stack == nullptr ||
                           current.call_stack->size <= current.call_base + 1;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return {kContinue};
  }
  const auto *call_frame = active_frame(current);
  if (call_frame != nullptr && call_frame->boundary_loops_to != kSentinel) {
    next_ip = call_frame->boundary_loops_to -
              call_frame->function_view->code_pc_offset;
  }
  prune_control_frames_after_static_jump(current, next_ip);
  record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_br_if(execution_context *context,
                                    std::uint32_t *sp,
                                    std::uint32_t *local_base,
                                    std::uint32_t tos) {
  const auto outcome = runtime_br_if(context, sp, local_base, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_local_get(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t *&local_base, std::uint32_t &tos,
                  const fireball_call_frame_native &frame, std::uint32_t index,
                  std::uint32_t operand_ip) {
  auto &current = *context;
  const auto [offset, width] = local_span(frame, index);
  if (width > current.sp_capacity - current.sp_offset)
    return trap(current, kTrapOperandStackCapacity);
  if (width == 1) {
    tos = local_base[offset];
    *sp++ = tos;
  } else {
    sp[0] = local_base[offset];
    tos = local_base[offset + 1];
    sp[1] = tos;
    sp += 2;
  }
  current.sp_offset += width;
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_local_get_wide(execution_context *context,
                                             std::uint32_t *sp,
                                             std::uint32_t *local_base,
                                             std::uint32_t tos) {
  auto &current = *context;
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  if (!read_u32(current, operand_ip, index))
    return fallback(current, current.ip);
  const auto outcome =
      runtime_local_get(context, sp, local_base, tos, frame, index, operand_ip);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_local_get(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  auto &current = *context;
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  auto operand_ip = current.ip + 1;
  assert(operand_ip < current.code_size);
  const auto encoded_index = current.code[operand_ip++];
  if ((encoded_index & 0x80u) != 0) [[unlikely]] {
    [[clang::musttail]] return h_local_get_wide<Debugger>(context, sp,
                                                          local_base, tos);
  }
  const auto index = encoded_index;
  const auto outcome =
      runtime_local_get(context, sp, local_base, tos, frame, index, operand_ip);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_local_set(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t *&local_base, std::uint32_t &tos,
                  const fireball_call_frame_native &frame, std::uint32_t index,
                  std::uint32_t operand_ip) {
  auto &current = *context;
  const auto [offset, width] = local_span(frame, index);
  if (current.sp_offset < width)
    return fallback(current, current.ip);
  if (width == 1) {
    local_base[offset] = tos;
  } else {
    local_base[offset] = sp[-2];
    local_base[offset + 1] = sp[-1];
  }
  current.sp_offset -= width;
  sp -= width;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_local_set_wide(execution_context *context,
                                             std::uint32_t *sp,
                                             std::uint32_t *local_base,
                                             std::uint32_t tos) {
  auto &current = *context;
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  if (!read_u32(current, operand_ip, index))
    return fallback(current, current.ip);
  const auto outcome =
      runtime_local_set(context, sp, local_base, tos, frame, index, operand_ip);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_local_set(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  auto &current = *context;
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  auto operand_ip = current.ip + 1;
  if (operand_ip >= current.code_size)
    return fallback(current, current.ip);
  const auto encoded_index = current.code[operand_ip++];
  if ((encoded_index & 0x80u) != 0) [[unlikely]] {
    [[clang::musttail]] return h_local_set_wide<Debugger>(context, sp,
                                                          local_base, tos);
  }
  const auto index = encoded_index;
  const auto outcome =
      runtime_local_set(context, sp, local_base, tos, frame, index, operand_ip);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_local_tee(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t *&local_base, std::uint32_t &tos,
                  const fireball_call_frame_native &frame, std::uint32_t index,
                  std::uint32_t operand_ip) {
  auto &current = *context;
  const auto [offset, width] = local_span(frame, index);
  if (current.sp_offset < width)
    return fallback(current, current.ip);
  if (width == 1) {
    local_base[offset] = tos;
  } else {
    local_base[offset] = sp[-2];
    local_base[offset + 1] = sp[-1];
  }
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_local_tee_wide(execution_context *context,
                                             std::uint32_t *sp,
                                             std::uint32_t *local_base,
                                             std::uint32_t tos) {
  auto &current = *context;
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  if (!read_u32(current, operand_ip, index))
    return fallback(current, current.ip);
  const auto outcome =
      runtime_local_tee(context, sp, local_base, tos, frame, index, operand_ip);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_local_tee(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  auto &current = *context;
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  auto operand_ip = current.ip + 1;
  if (operand_ip >= current.code_size)
    return fallback(current, current.ip);
  const auto encoded_index = current.code[operand_ip++];
  if ((encoded_index & 0x80u) != 0) [[unlikely]] {
    [[clang::musttail]] return h_local_tee_wide<Debugger>(context, sp,
                                                          local_base, tos);
  }
  const auto index = encoded_index;
  const auto outcome =
      runtime_local_tee(context, sp, local_base, tos, frame, index, operand_ip);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_global_get(execution_context *context, std::uint32_t *&sp,
                   std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  if (!read_u32(current, operand_ip, index))
    return fallback(current, current.ip);

  const auto *frame = active_frame(current);
  const auto *module = frame == nullptr || frame->function_view == nullptr
                           ? nullptr
                           : frame->function_view->module_view;
  if (module == nullptr || index >= module->global_count ||
      module->globals == nullptr || module->global_widths == nullptr) {
    return fallback(current, current.ip);
  }

  const auto width = module->global_widths[index];
  if ((width != 1u && width != 2u) || current.sp_offset > current.sp_capacity ||
      width > current.sp_capacity - current.sp_offset) {
    return fallback(current, current.ip);
  }
  const auto value = module->globals[index];
  *sp++ = static_cast<std::uint32_t>(value);
  if (width == 2u)
    *sp++ = static_cast<std::uint32_t>(value >> 32);
  current.sp_offset += width;
  tos = sp[-1];
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_global_get(execution_context *context,
                                         std::uint32_t *sp,
                                         std::uint32_t *local_base,
                                         std::uint32_t tos) {
  const auto outcome = runtime_global_get(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_global_set(execution_context *context, std::uint32_t *&sp,
                   std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t index = 0;
  if (!read_u32(current, operand_ip, index))
    return fallback(current, current.ip);

  const auto *frame = active_frame(current);
  const auto *module = frame == nullptr || frame->function_view == nullptr
                           ? nullptr
                           : frame->function_view->module_view;
  if (module == nullptr || index >= module->global_count ||
      module->globals == nullptr || module->global_widths == nullptr) {
    return fallback(current, current.ip);
  }

  const auto width = module->global_widths[index];
  if ((width != 1u && width != 2u) || current.sp_offset < width) {
    return fallback(current, current.ip);
  }
  auto value =
      static_cast<std::uint64_t>(sp[-static_cast<std::ptrdiff_t>(width)]);
  if (width == 2u)
    value |= static_cast<std::uint64_t>(sp[-1]) << 32;
  module->globals[index] = value;
  current.sp_offset -= width;
  sp -= width;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_global_set(execution_context *context,
                                         std::uint32_t *sp,
                                         std::uint32_t *local_base,
                                         std::uint32_t tos) {
  const auto outcome = runtime_global_set(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result runtime_drop(execution_context *context,
                                                     std::uint32_t *&sp,
                                                     std::uint32_t &tos) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  const auto width = entry == nullptr ? 0u : entry->operand_width;
  if (width == 0 || current.sp_offset < width)
    return fallback(current, current.ip);
  current.sp_offset -= width;
  sp -= width;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  current.ip += 1;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_drop(execution_context *context,
                                   std::uint32_t *sp, std::uint32_t *local_base,
                                   std::uint32_t tos) {
  const auto outcome = runtime_drop(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_select(execution_context *context, std::uint32_t *&sp,
               std::uint32_t &tos) {
  auto &current = *context;
  const auto *entry = control_entry(current, current.ip);
  const auto width = entry == nullptr ? 0u : entry->operand_width;
  if ((width != 1 && width != 2) || current.sp_offset < 1 + width * 2) {
    return fallback(current, current.ip);
  }
  const auto condition = tos;
  --current.sp_offset;
  --sp;
  if (condition == 0) {
    for (std::uint32_t word = 0; word < width; ++word)
      sp[word - static_cast<std::ptrdiff_t>(2 * width)] =
          sp[word - static_cast<std::ptrdiff_t>(width)];
  }
  current.sp_offset -= width;
  sp -= width;
  tos = sp[-1];
  current.ip += 1;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_select(execution_context *context,
                                     std::uint32_t *sp,
                                     std::uint32_t *local_base,
                                     std::uint32_t tos) {
  const auto outcome = runtime_select(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_end(execution_context *context, std::uint32_t *&local_base) {
  auto &current = *context;
  if (current.control_stack->size > current.control_base) {
    current.control_stack->size -= 1;
  }
  current.ip += 1;
  if (current.ip >= current.code_size) {
    const bool outermost = current.call_stack == nullptr ||
                           current.call_stack->size <= current.call_base + 1;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return {kContinue};
  }
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_end(execution_context *context, std::uint32_t *sp,
                                  std::uint32_t *local_base,
                                  std::uint32_t tos) {
  const auto outcome = runtime_end(context, local_base);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_return(execution_context *context, std::uint32_t *&local_stack) {
  auto &current = *context;
  const bool outermost = current.call_stack == nullptr ||
                         current.call_stack->size <= current.call_base + 1;
  if (outermost && should_stop_after_control(current))
    return block_boundary(current, kSentinel);
  if (finish_current_function(current, local_stack))
    return complete(current);
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_return(execution_context *context,
                                     std::uint32_t *sp,
                                     std::uint32_t *local_stack,
                                     std::uint32_t tos) {
  const auto outcome = runtime_return(context, local_stack);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_stack,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_br_table(execution_context *context, std::uint32_t *&sp,
                 std::uint32_t *&local_base, std::uint32_t &tos) {
  auto &current = *context;
  const auto source_ip = current.ip;
  auto operand_ip = current.ip + 1;
  std::uint32_t target_count = 0;
  std::uint32_t selector = 0;
  const auto *table = control_entry(current, source_ip);
  if (table == nullptr || table->br_table_targets == nullptr ||
      !read_u32(current, operand_ip, target_count) ||
      target_count + 1 != table->br_table_target_count) {
    return fallback(current, current.ip);
  }
  if (!pop(current, sp, tos, selector))
    return fallback(current, current.ip);

  const auto selected_index = selector < target_count ? selector : target_count;
  const auto selected_depth = table->br_table_targets[selected_index];

  std::uint32_t next_ip = 0;
  if (!branch(current, sp, tos, selected_depth, next_ip))
    return fallback(current, current.ip, current.sp_offset + 1);
  if (next_ip == kSentinel) {
    const bool outermost = current.call_stack == nullptr ||
                           current.call_stack->size <= current.call_base + 1;
    if (outermost && should_stop_after_control(current))
      return block_boundary(current, kSentinel);
    if (finish_current_function(current, local_base))
      return complete(current);
    if (should_stop_after_control(current))
      return block_boundary(current, current.ip);
    return {kContinue};
  }
  record_loop_backedge(current, source_ip, next_ip);
  current.ip = next_ip;
  if (should_stop_after_control(current)) {
    return block_boundary(current, current.ip);
  }
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_br_table(execution_context *context,
                                       std::uint32_t *sp,
                                       std::uint32_t *local_base,
                                       std::uint32_t tos) {
  const auto outcome = runtime_br_table(context, sp, local_base, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_unreachable(execution_context &context) {
  return trap(context, kTrapUnreachable);
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_unreachable(execution_context *context,
                                          std::uint32_t *, std::uint32_t *,
                                          std::uint32_t) {
  return runtime_unreachable(*context);
}

[[gnu::always_inline]] inline op_result
runtime_i32_const(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::int32_t value = 0;
  if (!read_s32(current, operand_ip, value))
    return fallback(current, current.ip);
  if (!push(current, sp, tos, static_cast<std::uint32_t>(value))) {
    return trap(current, kTrapOperandStackCapacity);
  }
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_i32_const(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome = runtime_i32_const(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <std::uint32_t (*Operation)(std::uint32_t, std::uint32_t)>
[[gnu::always_inline]] inline op_result
runtime_i32_binary(execution_context *context, std::uint32_t *&sp,
                   std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 2) {
    return fallback(current, current.ip);
  }
  const auto result = Operation(sp[-2], tos);
  --current.sp_offset;
  --sp;
  tos = result;
  sp[-1] = result;
  ++current.ip;
  return {kContinue};
}

template <typename Debugger,
          std::uint32_t (*Operation)(std::uint32_t, std::uint32_t)>
FIREBALL_CPS_CALL op_result h_i32_binary(execution_context *context,
                                         std::uint32_t *sp,
                                         std::uint32_t *local_base,
                                         std::uint32_t tos) {
  const auto outcome = runtime_i32_binary<Operation>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <bool (*Operation)(std::uint32_t, std::uint32_t, std::uint32_t &,
                            std::uint32_t &)>
[[gnu::always_inline]] inline op_result
runtime_i32_checked_binary(execution_context *context, std::uint32_t *&sp,
                           std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 2)
    return fallback(current, current.ip);
  std::uint32_t result = 0;
  std::uint32_t trap_code = 0;
  if (!Operation(sp[-2], tos, result, trap_code))
    return trap(current, trap_code);
  --current.sp_offset;
  --sp;
  tos = result;
  sp[-1] = result;
  ++current.ip;
  return {kContinue};
}

template <typename Debugger,
          bool (*Operation)(std::uint32_t, std::uint32_t, std::uint32_t &,
                            std::uint32_t &)>
FIREBALL_CPS_CALL op_result h_i32_checked_binary(execution_context *context,
                                                 std::uint32_t *sp,
                                                 std::uint32_t *local_base,
                                                 std::uint32_t tos) {
  const auto outcome = runtime_i32_checked_binary<Operation>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <std::uint64_t (*Operation)(std::uint64_t, std::uint64_t),
          bool ResultIsBool>
[[gnu::always_inline]] inline op_result
runtime_i64_binary(execution_context *context, std::uint32_t *&sp,
                   std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 4)
    return fallback(current, current.ip);
  const auto lhs = combine_u64(sp[-4], sp[-3]);
  const auto rhs = combine_u64(sp[-2], tos);
  const auto result = Operation(lhs, rhs);
  replace_u64_operands<ResultIsBool>(current, sp, tos, result, 4);
  ++current.ip;
  return {kContinue};
}

template <typename Debugger,
          std::uint64_t (*Operation)(std::uint64_t, std::uint64_t),
          bool ResultIsBool>
FIREBALL_CPS_CALL op_result h_i64_binary(execution_context *context,
                                         std::uint32_t *sp,
                                         std::uint32_t *local_base,
                                         std::uint32_t tos) {
  const auto outcome =
      runtime_i64_binary<Operation, ResultIsBool>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <bool (*Operation)(std::uint64_t, std::uint64_t, std::uint64_t &,
                            std::uint32_t &),
          bool ResultIsBool>
[[gnu::always_inline]] inline op_result
runtime_i64_checked_binary(execution_context *context, std::uint32_t *&sp,
                           std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 4)
    return fallback(current, current.ip);
  const auto lhs = combine_u64(sp[-4], sp[-3]);
  const auto rhs = combine_u64(sp[-2], tos);
  std::uint64_t result = 0;
  std::uint32_t trap_code = 0;
  if (!Operation(lhs, rhs, result, trap_code))
    return trap(current, trap_code);
  replace_u64_operands<ResultIsBool>(current, sp, tos, result, 4);
  ++current.ip;
  return {kContinue};
}

template <typename Debugger,
          bool (*Operation)(std::uint64_t, std::uint64_t, std::uint64_t &,
                            std::uint32_t &),
          bool ResultIsBool>
FIREBALL_CPS_CALL op_result h_i64_checked_binary(execution_context *context,
                                                 std::uint32_t *sp,
                                                 std::uint32_t *local_base,
                                                 std::uint32_t tos) {
  const auto outcome =
      runtime_i64_checked_binary<Operation, ResultIsBool>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <std::uint64_t (*Operation)(std::uint64_t), bool ResultIsBool>
[[gnu::always_inline]] inline op_result
runtime_i64_unary(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 2)
    return fallback(current, current.ip);
  const auto value = combine_u64(sp[-2], tos);
  const auto result = Operation(value);
  replace_u64_operands<ResultIsBool>(current, sp, tos, result, 2);
  ++current.ip;
  return {kContinue};
}

template <typename Debugger, std::uint64_t (*Operation)(std::uint64_t),
          bool ResultIsBool>
FIREBALL_CPS_CALL op_result h_i64_unary(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome =
      runtime_i64_unary<Operation, ResultIsBool>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <float (*Operation)(float, float), bool ResultIsBool>
[[gnu::always_inline]] inline op_result
runtime_f32_binary(execution_context *context, std::uint32_t *&sp,
                   std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 2)
    return fallback(current, current.ip);
  const auto result = Operation(f32_from_bits(sp[-2]), f32_from_bits(tos));
  --current.sp_offset;
  --sp;
  if constexpr (ResultIsBool)
    tos = static_cast<std::uint32_t>(result);
  else
    tos = f32_to_bits(result);
  sp[-1] = tos;
  ++current.ip;
  return {kContinue};
}

template <typename Debugger, float (*Operation)(float, float),
          bool ResultIsBool>
FIREBALL_CPS_CALL op_result h_f32_binary(execution_context *context,
                                         std::uint32_t *sp,
                                         std::uint32_t *local_base,
                                         std::uint32_t tos) {
  const auto outcome =
      runtime_f32_binary<Operation, ResultIsBool>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <double (*Operation)(double, double), bool ResultIsBool>
[[gnu::always_inline]] inline op_result
runtime_f64_binary(execution_context *context, std::uint32_t *&sp,
                   std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 4)
    return fallback(current, current.ip);
  const auto lhs = f64_from_bits(combine_u64(sp[-4], sp[-3]));
  const auto rhs = f64_from_bits(combine_u64(sp[-2], tos));
  const auto result = Operation(lhs, rhs);
  if constexpr (ResultIsBool)
    replace_u64_operands<true>(current, sp, tos,
                               static_cast<std::uint32_t>(result), 4);
  else
    replace_u64_operands<false>(current, sp, tos, f64_to_bits(result), 4);
  ++current.ip;
  return {kContinue};
}

template <typename Debugger, double (*Operation)(double, double),
          bool ResultIsBool>
FIREBALL_CPS_CALL op_result h_f64_binary(execution_context *context,
                                         std::uint32_t *sp,
                                         std::uint32_t *local_base,
                                         std::uint32_t tos) {
  const auto outcome =
      runtime_f64_binary<Operation, ResultIsBool>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <float (*Operation)(float)>
[[gnu::always_inline]] inline op_result
runtime_f32_unary(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset == 0) {
    return fallback(current, current.ip);
  }
  tos = f32_to_bits(Operation(f32_from_bits(tos)));
  sp[-1] = tos;
  ++current.ip;
  return {kContinue};
}

template <typename Debugger, float (*Operation)(float)>
FIREBALL_CPS_CALL op_result h_f32_unary(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome = runtime_f32_unary<Operation>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <double (*Operation)(double)>
[[gnu::always_inline]] inline op_result
runtime_f64_unary(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  if (current.sp_offset < 2) {
    return fallback(current, current.ip);
  }
  const auto value = f64_from_bits(combine_u64(sp[-2], tos));
  replace_u64_operands<false>(current, sp, tos, f64_to_bits(Operation(value)),
                              2);
  ++current.ip;
  return {kContinue};
}

template <typename Debugger, double (*Operation)(double)>
FIREBALL_CPS_CALL op_result h_f64_unary(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome = runtime_f64_unary<Operation>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <bool (*Operation)(execution_context &, std::uint32_t *&,
                            std::uint32_t &, std::uint32_t &)>
[[gnu::always_inline]] inline op_result
runtime_conversion(execution_context *context, std::uint32_t *&sp,
                   std::uint32_t &tos) {
  auto &current = *context;
  std::uint32_t trap_code = 0;
  const auto stack_height = current.sp_offset;
  if (!Operation(current, sp, tos, trap_code)) {
    return trap_code == 0 ? fallback(current, current.ip, stack_height)
                          : trap(current, trap_code);
  }
  ++current.ip;
  return {kContinue};
}

template <typename Debugger,
          bool (*Operation)(execution_context &, std::uint32_t *&,
                            std::uint32_t &, std::uint32_t &)>
FIREBALL_CPS_CALL op_result h_conversion(execution_context *context,
                                         std::uint32_t *sp,
                                         std::uint32_t *local_base,
                                         std::uint32_t tos) {
  const auto outcome = runtime_conversion<Operation>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_i64_const(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::int64_t value = 0;
  if (!read_s64(current, operand_ip, value))
    return fallback(current, current.ip);
  if (!push_u64(current, sp, tos, static_cast<std::uint64_t>(value))) {
    return trap(current, kTrapOperandStackCapacity);
  }
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_i64_const(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome = runtime_i64_const(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_f32_const(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  if (current.ip + 5 > current.code_size)
    return fallback(current, current.ip);
  const auto bits =
      static_cast<std::uint32_t>(current.code[current.ip + 1]) |
      (static_cast<std::uint32_t>(current.code[current.ip + 2]) << 8) |
      (static_cast<std::uint32_t>(current.code[current.ip + 3]) << 16) |
      (static_cast<std::uint32_t>(current.code[current.ip + 4]) << 24);
  if (!push(current, sp, tos, bits))
    return trap(current, kTrapOperandStackCapacity);
  current.ip += 5;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_f32_const(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome = runtime_f32_const(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_f64_const(execution_context *context, std::uint32_t *&sp,
                  std::uint32_t &tos) {
  auto &current = *context;
  if (current.ip + 9 > current.code_size)
    return fallback(current, current.ip);
  std::uint64_t bits = 0;
  for (std::uint32_t byte = 0; byte < 8; ++byte) {
    bits |= static_cast<std::uint64_t>(current.code[current.ip + 1 + byte])
            << (byte * 8);
  }
  if (!push_u64(current, sp, tos, bits))
    return trap(current, kTrapOperandStackCapacity);
  current.ip += 9;
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_f64_const(execution_context *context,
                                        std::uint32_t *sp,
                                        std::uint32_t *local_base,
                                        std::uint32_t tos) {
  const auto outcome = runtime_f64_const(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <std::uint32_t Width>
std::uint64_t sign_extend_memory_value(std::uint64_t value) {
  if constexpr (Width < sizeof(std::uint64_t)) {
    constexpr auto sign_bit = std::uint64_t{1} << (Width * 8 - 1);
    return (value ^ sign_bit) - sign_bit;
  }
  return value;
}

template <std::uint32_t Width, std::uint32_t ResultWords, bool Signed>
[[gnu::always_inline]] inline op_result
runtime_memory_load(execution_context *context, std::uint32_t *&sp,
                    std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t alignment = 0;
  std::uint32_t offset = 0;
  if (!read_u32(current, operand_ip, alignment) ||
      !read_u32(current, operand_ip, offset) || current.sp_offset == 0) {
    return fallback(current, current.ip);
  }
  (void)alignment;

  const auto address = tos;
  if ((address & 0x8000'0000u) != 0)
    return fallback(current, current.ip);
  const auto effective_address = static_cast<std::uint64_t>(address) + offset;
  if (!linear_memory_range_is_valid(current, effective_address, Width)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (current.linear_memory_host_base == nullptr)
    return fallback(current, current.ip);

  if (current.sp_offset - 1 + ResultWords > current.sp_capacity) {
    return fallback(current, current.ip);
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
    tos = static_cast<std::uint32_t>(value >> 32);
    *sp++ = tos;
    ++current.sp_offset;
  } else {
    tos = static_cast<std::uint32_t>(value);
    sp[-1] = tos;
  }
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger, std::uint32_t Width, std::uint32_t ResultWords,
          bool Signed>
FIREBALL_CPS_CALL op_result h_memory_load(execution_context *context,
                                          std::uint32_t *sp,
                                          std::uint32_t *local_base,
                                          std::uint32_t tos) {
  const auto outcome =
      runtime_memory_load<Width, ResultWords, Signed>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <std::uint32_t Width, std::uint32_t ValueWords>
[[gnu::always_inline]] inline op_result
runtime_memory_store(execution_context *context, std::uint32_t *&sp,
                     std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t alignment = 0;
  std::uint32_t offset = 0;
  if (!read_u32(current, operand_ip, alignment) ||
      !read_u32(current, operand_ip, offset) ||
      current.sp_offset < ValueWords + 1) {
    return fallback(current, current.ip);
  }
  (void)alignment;

  const auto address_index = current.sp_offset - ValueWords - 1;
  const auto address = sp[-static_cast<std::ptrdiff_t>(ValueWords + 1)];
  if ((address & 0x8000'0000u) != 0)
    return fallback(current, current.ip);
  const auto effective_address = static_cast<std::uint64_t>(address) + offset;
  if (!linear_memory_range_is_valid(current, effective_address, Width)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (current.linear_memory_host_base == nullptr)
    return fallback(current, current.ip);

  std::uint64_t value = sp[-static_cast<std::ptrdiff_t>(ValueWords)];
  if constexpr (ValueWords == 2) {
    value |= static_cast<std::uint64_t>(sp[-1]) << 32;
  }
  for (std::uint32_t byte = 0; byte < Width; ++byte) {
    current.linear_memory_host_base[effective_address + byte] =
        static_cast<std::uint8_t>(value >> (byte * 8));
  }
  current.sp_offset = address_index;
  sp -= ValueWords + 1;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  current.ip = operand_ip;
  return {kContinue};
}

template <typename Debugger, std::uint32_t Width, std::uint32_t ValueWords>
FIREBALL_CPS_CALL op_result h_memory_store(execution_context *context,
                                           std::uint32_t *sp,
                                           std::uint32_t *local_base,
                                           std::uint32_t tos) {
  const auto outcome =
      runtime_memory_store<Width, ValueWords>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

template <bool (*Operation)(execution_context &, std::uint32_t *&,
                            std::uint32_t &)>
[[gnu::always_inline]] inline op_result
runtime_fc_saturating_conversion(execution_context *context, std::uint32_t *&sp,
                                 std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t subopcode = 0;
  const auto stack_height = current.sp_offset;
  if (!read_u32(current, operand_ip, subopcode) || subopcode > 7 ||
      !Operation(current, sp, tos)) {
    return fallback(current, current.ip, stack_height);
  }
  current.ip = operand_ip;
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger,
          bool (*Operation)(execution_context &, std::uint32_t *&,
                            std::uint32_t &)>
FIREBALL_CPS_CALL op_result
h_fc_saturating_conversion(execution_context *context, std::uint32_t *sp,
                           std::uint32_t *local_base, std::uint32_t tos) {
  const auto outcome =
      runtime_fc_saturating_conversion<Operation>(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_fc_memory_copy(execution_context *context, std::uint32_t *&sp,
                       std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t subopcode = 0;
  std::uint32_t destination_memory = 0;
  std::uint32_t source_memory = 0;
  if (!read_u32(current, operand_ip, subopcode) || subopcode != 0x0A ||
      !read_u32(current, operand_ip, destination_memory) ||
      !read_u32(current, operand_ip, source_memory) ||
      destination_memory != 0 || source_memory != 0 || current.sp_offset < 3) {
    return fallback(current, current.ip);
  }

  const auto destination_index = current.sp_offset - 3;
  const auto destination = sp[-3];
  const auto source = sp[-2];
  const auto length = sp[-1];
  if (((destination | source) & 0x8000'0000u) != 0)
    return fallback(current, current.ip);
  if (!linear_memory_range_is_valid(current, destination, length) ||
      !linear_memory_range_is_valid(current, source, length)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (length != 0) {
    if (current.linear_memory_host_base == nullptr)
      return fallback(current, current.ip);
    auto *memory = current.linear_memory_host_base;
    std::memmove(memory + destination, memory + source, length);
  }
  current.sp_offset = destination_index;
  sp -= 3;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  current.ip = operand_ip;
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_fc_memory_copy(execution_context *context,
                                             std::uint32_t *sp,
                                             std::uint32_t *local_base,
                                             std::uint32_t tos) {
  const auto outcome = runtime_fc_memory_copy(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

[[gnu::always_inline]] inline op_result
runtime_fc_memory_fill(execution_context *context, std::uint32_t *&sp,
                       std::uint32_t &tos) {
  auto &current = *context;
  auto operand_ip = current.ip + 1;
  std::uint32_t subopcode = 0;
  std::uint32_t memory_index = 0;
  if (!read_u32(current, operand_ip, subopcode) || subopcode != 0x0B ||
      !read_u32(current, operand_ip, memory_index) || memory_index != 0 ||
      current.sp_offset < 3) {
    return fallback(current, current.ip);
  }

  const auto destination_index = current.sp_offset - 3;
  const auto destination = sp[-3];
  const auto value = static_cast<std::uint8_t>(sp[-2]);
  const auto length = sp[-1];
  if (!linear_memory_range_is_valid(current, destination, length)) {
    return trap(current, kTrapMemoryOutOfBounds);
  }
  if (length != 0) {
    if (current.linear_memory_host_base == nullptr)
      return fallback(current, current.ip);
    std::memset(current.linear_memory_host_base + destination, value, length);
  }
  current.sp_offset = destination_index;
  sp -= 3;
  tos = current.sp_offset == 0 ? 0 : sp[-1];
  current.ip = operand_ip;
  if (should_stop_after_control(current))
    return block_boundary(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_fc_memory_fill(execution_context *context,
                                             std::uint32_t *sp,
                                             std::uint32_t *local_base,
                                             std::uint32_t tos) {
  const auto outcome = runtime_fc_memory_fill(context, sp, tos);
  if (outcome.kind != kContinue)
    return outcome;
  [[clang::musttail]] return dispatch_next<Debugger>(context, sp, local_base,
                                                     tos);
}

constexpr std::uint8_t kOpcodeControlTerminator = 1u << 0;
constexpr std::uint8_t kOpcodeBlockBody = 1u << 2;

constexpr auto make_opcode_attributes() {
  std::array<std::uint8_t, 256> attributes{};
  const auto set_control_terminator = [&attributes](std::uint8_t opcode) {
    attributes[opcode] |= kOpcodeControlTerminator;
  };

  for (const auto opcode : {0x02u, 0x03u, 0x04u, 0x05u, 0x0Bu, 0x0Cu, 0x0Du,
                            0x0Eu, 0x0Fu, 0x10u, 0x11u}) {
    set_control_terminator(static_cast<std::uint8_t>(opcode));
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

bool opcode_is_control_terminator(std::uint8_t opcode) {
  return (kOpcodeAttributes[opcode] & kOpcodeControlTerminator) != 0;
}

bool opcode_is_block_body(std::uint8_t opcode) {
  return (kOpcodeAttributes[opcode] & kOpcodeBlockBody) != 0;
}

[[gnu::always_inline]] inline op_result
runtime_unsupported_opcode(execution_context &context) {
  return fallback(context, context.ip);
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_unsupported_opcode(execution_context *context,
                                                 std::uint32_t *,
                                                 std::uint32_t *,
                                                 std::uint32_t) {
  return runtime_unsupported_opcode(*context);
}

template <typename Debugger>
using handler_pointer = decltype(&h_unreachable<Debugger>);
static_assert(
    std::is_same_v<handler_pointer<void>, fireball_native_cps_handler>);

template <typename Debugger>
constexpr auto kFcOpcodeHandlers = [] {
  std::array<handler_pointer<Debugger>, 12> handlers{};
  handlers.fill(&h_unsupported_opcode<Debugger>);
  handlers[0x00] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i32<float, true>>;
  handlers[0x01] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i32<float, false>>;
  handlers[0x02] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i32<double, true>>;
  handlers[0x03] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i32<double, false>>;
  handlers[0x04] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i64<float, true>>;
  handlers[0x05] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i64<float, false>>;
  handlers[0x06] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i64<double, true>>;
  handlers[0x07] =
      &h_fc_saturating_conversion<Debugger,
                                  conversion_truncate_sat_i64<double, false>>;
  handlers[0x0A] = &h_fc_memory_copy<Debugger>;
  handlers[0x0B] = &h_fc_memory_fill<Debugger>;
  return handlers;
}();

[[gnu::always_inline]] inline op_result
runtime_fc_subopcode(execution_context &current, std::uint32_t &subopcode) {
  auto operand_ip = current.ip + 1;
  if (!read_u32(current, operand_ip, subopcode))
    return fallback(current, current.ip);
  return {kContinue};
}

template <typename Debugger>
FIREBALL_CPS_CALL op_result h_fc_dispatch(execution_context *context,
                                          std::uint32_t *sp,
                                          std::uint32_t *local_base,
                                          std::uint32_t tos) {
  std::uint32_t subopcode = 0;
  const auto outcome = runtime_fc_subopcode(*context, subopcode);
  if (outcome.kind != kContinue)
    return outcome;
  if (subopcode >= kFcOpcodeHandlers<Debugger>.size()) {
    [[clang::musttail]] return h_unsupported_opcode<Debugger>(context, sp,
                                                              local_base, tos);
  }
  [[clang::musttail]] return kFcOpcodeHandlers<Debugger>[subopcode](
      context, sp, local_base, tos);
}

template <typename Debugger>
constexpr auto kOpcodeHandlers = [] {
  std::array<handler_pointer<Debugger>, 256> handlers{};
  handlers.fill(&h_unsupported_opcode<Debugger>);
  handlers[0x00] = &h_unreachable<Debugger>;
  handlers[0x01] = &h_nop<Debugger>;
  handlers[0x02] = &h_block<Debugger, false>;
  handlers[0x03] = &h_block<Debugger, true>;
  handlers[0x04] = &h_if<Debugger>;
  handlers[0x05] = &h_else<Debugger>;
  handlers[0x0B] = &h_end<Debugger>;
  handlers[0x0C] = &h_br<Debugger>;
  handlers[0x0D] = &h_br_if<Debugger>;
  handlers[0x0E] = &h_br_table<Debugger>;
  handlers[0x0F] = &h_return<Debugger>;
  handlers[0x10] = &h_call<Debugger, false>;
  handlers[0x11] = &h_call<Debugger, true>;
  handlers[0x1A] = &h_drop<Debugger>;
  handlers[0x1B] = &h_select<Debugger>;
  handlers[0x20] = &h_local_get<Debugger>;
  handlers[0x21] = &h_local_set<Debugger>;
  handlers[0x22] = &h_local_tee<Debugger>;
  handlers[0x23] = &h_global_get<Debugger>;
  handlers[0x24] = &h_global_set<Debugger>;
  handlers[0x28] = &h_memory_load<Debugger, 4, 1, false>;
  handlers[0x29] = &h_memory_load<Debugger, 8, 2, false>;
  handlers[0x2A] = &h_memory_load<Debugger, 4, 1, false>;
  handlers[0x2B] = &h_memory_load<Debugger, 8, 2, false>;
  handlers[0x2C] = &h_memory_load<Debugger, 1, 1, true>;
  handlers[0x2D] = &h_memory_load<Debugger, 1, 1, false>;
  handlers[0x2E] = &h_memory_load<Debugger, 2, 1, true>;
  handlers[0x2F] = &h_memory_load<Debugger, 2, 1, false>;
  handlers[0x30] = &h_memory_load<Debugger, 1, 2, true>;
  handlers[0x31] = &h_memory_load<Debugger, 1, 2, false>;
  handlers[0x32] = &h_memory_load<Debugger, 2, 2, true>;
  handlers[0x33] = &h_memory_load<Debugger, 2, 2, false>;
  handlers[0x34] = &h_memory_load<Debugger, 4, 2, true>;
  handlers[0x35] = &h_memory_load<Debugger, 4, 2, false>;
  handlers[0x36] = &h_memory_store<Debugger, 4, 1>;
  handlers[0x37] = &h_memory_store<Debugger, 8, 2>;
  handlers[0x38] = &h_memory_store<Debugger, 4, 1>;
  handlers[0x39] = &h_memory_store<Debugger, 8, 2>;
  handlers[0x3A] = &h_memory_store<Debugger, 1, 1>;
  handlers[0x3B] = &h_memory_store<Debugger, 2, 1>;
  handlers[0x3C] = &h_memory_store<Debugger, 1, 2>;
  handlers[0x3D] = &h_memory_store<Debugger, 2, 2>;
  handlers[0x3E] = &h_memory_store<Debugger, 4, 2>;
  handlers[0x41] = &h_i32_const<Debugger>;
  handlers[0x42] = &h_i64_const<Debugger>;
  handlers[0x43] = &h_f32_const<Debugger>;
  handlers[0x44] = &h_f64_const<Debugger>;
  handlers[0x45] = &h_i32_unary<Debugger, i32_eqz>;
  handlers[0x46] = &h_i32_binary<Debugger, op_eq>;
  handlers[0x47] = &h_i32_binary<Debugger, op_ne>;
  handlers[0x48] = &h_i32_binary<Debugger, op_lt_s>;
  handlers[0x49] = &h_i32_binary<Debugger, op_lt_u>;
  handlers[0x4A] = &h_i32_binary<Debugger, op_gt_s>;
  handlers[0x4B] = &h_i32_binary<Debugger, op_gt_u>;
  handlers[0x4C] = &h_i32_binary<Debugger, op_le_s>;
  handlers[0x4D] = &h_i32_binary<Debugger, op_le_u>;
  handlers[0x4E] = &h_i32_binary<Debugger, op_ge_s>;
  handlers[0x4F] = &h_i32_binary<Debugger, op_ge_u>;
  handlers[0x50] = &h_i64_unary<Debugger, i64_eqz, true>;
  handlers[0x51] = &h_i64_binary<Debugger, i64_eq, true>;
  handlers[0x52] = &h_i64_binary<Debugger, i64_ne, true>;
  handlers[0x53] = &h_i64_binary<Debugger, i64_lt_s, true>;
  handlers[0x54] = &h_i64_binary<Debugger, i64_lt_u, true>;
  handlers[0x55] = &h_i64_binary<Debugger, i64_gt_s, true>;
  handlers[0x56] = &h_i64_binary<Debugger, i64_gt_u, true>;
  handlers[0x57] = &h_i64_binary<Debugger, i64_le_s, true>;
  handlers[0x58] = &h_i64_binary<Debugger, i64_le_u, true>;
  handlers[0x59] = &h_i64_binary<Debugger, i64_ge_s, true>;
  handlers[0x5A] = &h_i64_binary<Debugger, i64_ge_u, true>;
  handlers[0x5B] = &h_f32_binary<Debugger, f32_eq, true>;
  handlers[0x5C] = &h_f32_binary<Debugger, f32_ne, true>;
  handlers[0x5D] = &h_f32_binary<Debugger, f32_lt, true>;
  handlers[0x5E] = &h_f32_binary<Debugger, f32_gt, true>;
  handlers[0x5F] = &h_f32_binary<Debugger, f32_le, true>;
  handlers[0x60] = &h_f32_binary<Debugger, f32_ge, true>;
  handlers[0x61] = &h_f64_binary<Debugger, f64_eq, true>;
  handlers[0x62] = &h_f64_binary<Debugger, f64_ne, true>;
  handlers[0x63] = &h_f64_binary<Debugger, f64_lt, true>;
  handlers[0x64] = &h_f64_binary<Debugger, f64_gt, true>;
  handlers[0x65] = &h_f64_binary<Debugger, f64_le, true>;
  handlers[0x66] = &h_f64_binary<Debugger, f64_ge, true>;
  handlers[0x67] = &h_i32_unary<Debugger, i32_clz>;
  handlers[0x68] = &h_i32_unary<Debugger, i32_ctz>;
  handlers[0x69] = &h_i32_unary<Debugger, i32_popcnt>;
  handlers[0x6A] = &h_i32_binary<Debugger, op_add>;
  handlers[0x6B] = &h_i32_binary<Debugger, op_sub>;
  handlers[0x6C] = &h_i32_binary<Debugger, op_mul>;
  handlers[0x6D] = &h_i32_checked_binary<Debugger, evaluate_div_s>;
  handlers[0x6E] = &h_i32_checked_binary<Debugger, evaluate_div_u>;
  handlers[0x6F] = &h_i32_checked_binary<Debugger, evaluate_rem_s>;
  handlers[0x70] = &h_i32_checked_binary<Debugger, evaluate_rem_u>;
  handlers[0x71] = &h_i32_binary<Debugger, op_and>;
  handlers[0x72] = &h_i32_binary<Debugger, op_or>;
  handlers[0x73] = &h_i32_binary<Debugger, op_xor>;
  handlers[0x74] = &h_i32_binary<Debugger, op_shl>;
  handlers[0x75] = &h_i32_binary<Debugger, op_shr_s>;
  handlers[0x76] = &h_i32_binary<Debugger, op_shr_u>;
  handlers[0x77] = &h_i32_binary<Debugger, op_rotl>;
  handlers[0x78] = &h_i32_binary<Debugger, op_rotr>;
  handlers[0x79] = &h_i64_unary<Debugger, i64_clz, false>;
  handlers[0x7A] = &h_i64_unary<Debugger, i64_ctz, false>;
  handlers[0x7B] = &h_i64_unary<Debugger, i64_popcnt, false>;
  handlers[0x7C] = &h_i64_binary<Debugger, i64_add, false>;
  handlers[0x7D] = &h_i64_binary<Debugger, i64_sub, false>;
  handlers[0x7E] = &h_i64_binary<Debugger, i64_mul, false>;
  handlers[0x7F] = &h_i64_checked_binary<Debugger, i64_div_s, false>;
  handlers[0x80] = &h_i64_checked_binary<Debugger, i64_div_u, false>;
  handlers[0x81] = &h_i64_checked_binary<Debugger, i64_rem_s, false>;
  handlers[0x82] = &h_i64_checked_binary<Debugger, i64_rem_u, false>;
  handlers[0x83] = &h_i64_binary<Debugger, i64_and, false>;
  handlers[0x84] = &h_i64_binary<Debugger, i64_or, false>;
  handlers[0x85] = &h_i64_binary<Debugger, i64_xor, false>;
  handlers[0x86] = &h_i64_binary<Debugger, i64_shl, false>;
  handlers[0x87] = &h_i64_binary<Debugger, i64_shr_s, false>;
  handlers[0x88] = &h_i64_binary<Debugger, i64_shr_u, false>;
  handlers[0x89] = &h_i64_binary<Debugger, i64_rotl, false>;
  handlers[0x8A] = &h_i64_binary<Debugger, i64_rotr, false>;
  handlers[0x8B] = &h_f32_unary<Debugger, f32_abs>;
  handlers[0x8C] = &h_f32_unary<Debugger, f32_neg>;
  handlers[0x8D] = &h_f32_unary<Debugger, f32_ceil>;
  handlers[0x8E] = &h_f32_unary<Debugger, f32_floor>;
  handlers[0x8F] = &h_f32_unary<Debugger, f32_trunc>;
  handlers[0x90] = &h_f32_unary<Debugger, f32_nearest>;
  handlers[0x91] = &h_f32_unary<Debugger, f32_sqrt>;
  handlers[0x92] = &h_f32_binary<Debugger, f32_add, false>;
  handlers[0x93] = &h_f32_binary<Debugger, f32_sub, false>;
  handlers[0x94] = &h_f32_binary<Debugger, f32_mul, false>;
  handlers[0x95] = &h_f32_binary<Debugger, f32_div, false>;
  handlers[0x96] = &h_f32_binary<Debugger, f32_min, false>;
  handlers[0x97] = &h_f32_binary<Debugger, f32_max, false>;
  handlers[0x98] = &h_f32_binary<Debugger, f32_copysign, false>;
  handlers[0x99] = &h_f64_unary<Debugger, f64_abs>;
  handlers[0x9A] = &h_f64_unary<Debugger, f64_neg>;
  handlers[0x9B] = &h_f64_unary<Debugger, f64_ceil>;
  handlers[0x9C] = &h_f64_unary<Debugger, f64_floor>;
  handlers[0x9D] = &h_f64_unary<Debugger, f64_trunc>;
  handlers[0x9E] = &h_f64_unary<Debugger, f64_nearest>;
  handlers[0x9F] = &h_f64_unary<Debugger, f64_sqrt>;
  handlers[0xA0] = &h_f64_binary<Debugger, f64_add, false>;
  handlers[0xA1] = &h_f64_binary<Debugger, f64_sub, false>;
  handlers[0xA2] = &h_f64_binary<Debugger, f64_mul, false>;
  handlers[0xA3] = &h_f64_binary<Debugger, f64_div, false>;
  handlers[0xA4] = &h_f64_binary<Debugger, f64_min, false>;
  handlers[0xA5] = &h_f64_binary<Debugger, f64_max, false>;
  handlers[0xA6] = &h_f64_binary<Debugger, f64_copysign, false>;
  handlers[0xA7] = &h_conversion<Debugger, conversion_i32_wrap_i64>;
  handlers[0xA8] =
      &h_conversion<Debugger, conversion_truncate_i32<float, true>>;
  handlers[0xA9] =
      &h_conversion<Debugger, conversion_truncate_i32<float, false>>;
  handlers[0xAA] =
      &h_conversion<Debugger, conversion_truncate_i32<double, true>>;
  handlers[0xAB] =
      &h_conversion<Debugger, conversion_truncate_i32<double, false>>;
  handlers[0xAC] = &h_conversion<Debugger, conversion_i64_extend_i32_s>;
  handlers[0xAD] = &h_conversion<Debugger, conversion_i64_extend_i32_u>;
  handlers[0xAE] =
      &h_conversion<Debugger, conversion_truncate_i64<float, true>>;
  handlers[0xAF] =
      &h_conversion<Debugger, conversion_truncate_i64<float, false>>;
  handlers[0xB0] =
      &h_conversion<Debugger, conversion_truncate_i64<double, true>>;
  handlers[0xB1] =
      &h_conversion<Debugger, conversion_truncate_i64<double, false>>;
  handlers[0xB2] = &h_conversion<Debugger, conversion_f32_convert_i32_s>;
  handlers[0xB3] = &h_conversion<Debugger, conversion_f32_convert_i32_u>;
  handlers[0xB4] = &h_conversion<Debugger, conversion_f32_convert_i64_s>;
  handlers[0xB5] = &h_conversion<Debugger, conversion_f32_convert_i64_u>;
  handlers[0xB6] = &h_conversion<Debugger, conversion_f32_demote_f64>;
  handlers[0xB7] = &h_conversion<Debugger, conversion_f64_convert_i32_s>;
  handlers[0xB8] = &h_conversion<Debugger, conversion_f64_convert_i32_u>;
  handlers[0xB9] = &h_conversion<Debugger, conversion_f64_convert_i64_s>;
  handlers[0xBA] = &h_conversion<Debugger, conversion_f64_convert_i64_u>;
  handlers[0xBB] = &h_conversion<Debugger, conversion_f64_promote_f32>;
  handlers[0xBC] = &h_conversion<Debugger, conversion_identity>;
  handlers[0xBD] = &h_conversion<Debugger, conversion_identity>;
  handlers[0xBE] = &h_conversion<Debugger, conversion_identity>;
  handlers[0xBF] = &h_conversion<Debugger, conversion_identity>;
  handlers[0xC0] = &h_i32_unary<Debugger, i32_extend8>;
  handlers[0xC1] = &h_i32_unary<Debugger, i32_extend16>;
  handlers[0xC2] = &h_i64_unary<Debugger, i64_extend8, false>;
  handlers[0xC3] = &h_i64_unary<Debugger, i64_extend16, false>;
  handlers[0xC4] = &h_i64_unary<Debugger, i64_extend32, false>;
  handlers[kOpcodeFcPrefix] = &h_fc_dispatch<Debugger>;
  return handlers;
}();

template <typename Debugger>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL op_result
dispatch_next(execution_context *context, std::uint32_t *sp,
              std::uint32_t *local_base, std::uint32_t tos) {
  auto &current = *context;
  if ((current.runtime_flags & kStopAtBlockBoundaryFlag) != 0 &&
      is_requested_block_boundary(current))
    return block_boundary(current, current.ip);

  if constexpr (std::is_same_v<Debugger, debugger_aspect>) {
    if (Debugger::before_instruction(current))
      return debug_stop(current);
  }

  const auto opcode = current.code[current.ip];
  [[clang::musttail]] return kOpcodeHandlers<Debugger>[opcode](context, sp,
                                                               local_base, tos);
}

constexpr std::uint32_t kNoPc = 0xFFFF'FFFFu;

struct native_dispatch_call {
  const fb_native_execution_extension *extension = nullptr;
  const fb_native_dispatch_call *request = nullptr;
  std::uint32_t stack_size = 0;
  std::uint32_t stack_capacity = 0;
  std::uint32_t initial_ip = 0;
  std::uint32_t local_base = 0;
  std::uint32_t local_slots = 0;
  std::uint32_t control_base = 0;
  std::uint32_t yield_threshold = 0;
  fireball_execution_context_native *context = nullptr;
  fireball_control_stack_native *control_stack = nullptr;
  fireball_call_frame_native *call_frame = nullptr;
  std::uint32_t *stack = nullptr;
  std::uint32_t *local_stack = nullptr;
  const std::uint8_t *code = nullptr;
  std::uint32_t code_size = 0;
  std::uint32_t error_code = 0;

  explicit native_dispatch_call(const fb_native_dispatch_call &input)
      : extension(input.extension), request(&input),
        context(
            static_cast<fireball_execution_context_native *>(input.context)),
        control_stack(static_cast<fireball_control_stack_native *>(
            context->control_stack)),
        stack(input.stack), local_stack(input.locals) {
    stack_size = context->sp_offset;
    stack_capacity = context->sp_capacity;
    initial_ip = context->ip;
    control_base = context->control_base;
    yield_threshold = context->loop_jump_threshold;
    code = context->code;
    code_size = context->code_size;
    const auto *active = active_frame(*context);
    assert(active != nullptr);
    call_frame = const_cast<fireball_call_frame_native *>(active);
    local_base = active->local_base;
    local_slots = active->local_slot_count;
  }
};

void prepare_native_dispatch_call(native_dispatch_call &call) {
  assert(call.stack != nullptr && call.local_stack != nullptr);
  assert(call.control_stack != nullptr && call.call_frame != nullptr);
  [[maybe_unused]] const auto required_local_bytes =
      (static_cast<std::uint64_t>(call.local_base) + call.local_slots) *
      sizeof(std::uint32_t);
  assert(call.stack_capacity <= 128 && call.stack_size <= call.stack_capacity);
  assert(required_local_bytes <=
         static_cast<std::uint64_t>(call.context->local_capacity) *
             sizeof(std::uint32_t));
  assert(call.yield_threshold != 0 && call.code != nullptr);
  assert(call.control_stack->size <= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY);
  assert(call.control_base <= call.control_stack->size);
  assert(call.context->call_stack != nullptr);
  assert(call.context->call_stack->size != 0 &&
         call.context->call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  call.call_frame =
      &call.context->call_stack->frames[call.context->call_stack->size - 1];
  assert(call.initial_ip <= call.code_size);
  call.context->stack_checkpoint = call.stack_size;
}

bool refresh_native_dispatch_frame(native_dispatch_call &call,
                                   std::uint32_t &current_pc) {
  const auto *active = active_frame(*call.context);
  if (active == nullptr || active->function_view == nullptr)
    return false;
  call.call_frame = const_cast<fireball_call_frame_native *>(active);
  call.local_base = active->local_base;
  call.local_slots = active->local_slot_count;
  call.control_base = active->control_base;
  call.code = active->code;
  call.code_size = active->code_size;
  call.context->code = active->code;
  call.context->code_size = active->code_size;
  call.context->control_base = active->control_base;
  current_pc = active->function_view->code_pc_offset + call.context->ip;
  return true;
}

enum class dispatch_iteration { continue_dispatch, stop_dispatch, error };

struct native_dispatch_stats {
  std::uint32_t trace_count = 0;
  std::uint32_t body_count = 0;
  std::uint32_t dispatcher_trace_transitions = 0;
  std::uint32_t control_handler_count = 0;
  std::uint32_t interpreted_block_count = 0;
  bool control_handler_pending_trace = false;
};

struct native_dispatch_observations {
  std::uint32_t eligible_block_visits = 0;
};

struct empty_dispatch_metrics {};

// Clang 18 miscompiles the no-stats extension variant when these empty members
// use
// [[no_unique_address]], so keep their distinct storage.
template <bool CollectStats, bool WithExtension>
struct native_dispatch_metrics {
  std::conditional_t<CollectStats, native_dispatch_stats,
                     empty_dispatch_metrics>
      stats;
  std::conditional_t<WithExtension, native_dispatch_observations,
                     empty_dispatch_metrics>
      observations;
};

template <bool CollectStats, bool WithExtension,
          typename ResultType = fb_native_result>
struct native_dispatch_state {
  static_assert(!CollectStats || !std::is_same_v<ResultType, fb_native_result>);
  native_dispatch_call &call;
  std::uint32_t current_pc;
  std::uint32_t status = kFallback;
  std::uint32_t trap_code = 0;
  [[no_unique_address]] native_dispatch_metrics<
      CollectStats,
      WithExtension && !std::is_same_v<ResultType, fb_native_result>> metrics;
};

template <typename Debugger = void>
op_result execute_control_boundary(native_dispatch_call &call,
                                   std::uint32_t ip) {
  auto &context = *call.context;
  context.ip = ip;
  const auto previous_flags = context.runtime_flags;
  context.runtime_flags =
      (previous_flags & ~kStopAtBlockBoundaryFlag) | kStopAfterControlFlag;
  const auto result =
      dispatch_next<Debugger>(&context, call.stack + context.sp_offset,
                              call.local_stack + call.call_frame->local_base,
                              top_value(context, call.stack));
  context.runtime_flags = previous_flags;
  return result;
}

template <bool CollectStats, bool WithExtension, typename Debugger = void,
          typename ResultType = fb_native_result>
dispatch_iteration execute_interpreted_block(
    native_dispatch_state<CollectStats, WithExtension, ResultType> &state,
    std::uint32_t executed_bodies = 0) {
  auto &call = state.call;
  auto &context = *call.context;
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
  if (executed_bodies != 0)
    call.stack_size = context.sp_offset;
  if (executed_bodies != 0 && context.trap_code != 0) {
    state.trap_code = context.trap_code;
    state.status = kTrap;
    return dispatch_iteration::stop_dispatch;
  }
  if (context.ip >= call.code_size) {
    context.ip = kNoPc;
    state.status = kComplete;
    return dispatch_iteration::stop_dispatch;
  }

  const auto interpreter_ip =
      executed_bodies == 0
          ? state.current_pc - call.call_frame->function_view->code_pc_offset
          : context.ip;
  // Keep the PC established at the preceding control boundary. The first
  // body opcode may follow NOPs or other instructions inside it.
  const auto block_head_pc = state.current_pc;
  if (executed_bodies == 0)
    fb_native_record_block_execution(
        call.call_frame->function_view->module_view, block_head_pc);
  const auto previous_flags = context.runtime_flags;
  bool observed_interpreter_body = false;
  if constexpr (WithExtension) {
    observed_interpreter_body = executed_bodies == 0 &&
                                (previous_flags & kPendingBlockHeadFlag) != 0 &&
                                interpreter_ip < call.code_size &&
                                opcode_is_block_body(call.code[interpreter_ip]);
  }

  if (executed_bodies == 0) {
    call.call_frame->boundary_next_pc = kNoPc;
    call.call_frame->boundary_loops_to = kNoPc;
  }
  context.sp_offset = call.stack_size;
  context.stack_checkpoint = call.stack_size;
  context.code = call.call_frame->code;
  context.code_size = call.call_frame->code_size;
  context.control_base = call.call_frame->control_base;
  const auto result = execute_control_boundary<Debugger>(call, interpreter_ip);
  if constexpr (WithExtension) {
    if (observed_interpreter_body &&
        call.extension->observe(call.request, block_head_pc)) {
      if constexpr (!std::is_same_v<ResultType, fb_native_result>) {
        ++state.metrics.observations.eligible_block_visits;
      }
    }
  }
  context.runtime_flags = previous_flags & ~kPendingBlockHeadFlag;
  if constexpr (!std::is_void_v<Debugger>) {
    if (result.kind == kDebugStop) {
      call.stack_size = context.sp_offset;
      const auto *stopped_frame = active_frame(context);
      if (stopped_frame == nullptr || stopped_frame->function_view == nullptr) {
        call.error_code = kNativeErrorInternal;
        return dispatch_iteration::error;
      }
      state.current_pc =
          stopped_frame->function_view->code_pc_offset + context.ip;
      state.status = kDebugStop;
      return dispatch_iteration::stop_dispatch;
    }
  }
  if (result.kind == kCallBoundary) {
    context.runtime_flags |= kPendingBlockHeadFlag;
    call.stack_size = context.sp_offset;
    const auto *callee = active_frame(context);
    if (callee == nullptr) {
      call.error_code = kNativeErrorInternal;
      return dispatch_iteration::error;
    }
    if constexpr (CollectStats)
      ++state.metrics.stats.control_handler_count;
    state.current_pc = callee->function_view->code_pc_offset + context.ip;
    if constexpr (WithExtension) {
      if constexpr (CollectStats)
        state.metrics.stats.control_handler_pending_trace = true;
      if (context.loop_jump_count >= call.yield_threshold) {
        state.status = kDispatchYield;
        return dispatch_iteration::stop_dispatch;
      }
      return dispatch_iteration::continue_dispatch;
    } else {
      state.status = kNativeCallBoundary;
      return dispatch_iteration::stop_dispatch;
    }
  }
  if (result.kind == kFallback) {
    context.sp_offset = context.stack_checkpoint;
    call.stack_size = context.sp_offset;
    state.status = kFallback;
    return dispatch_iteration::stop_dispatch;
  }
  if (result.kind == kTrap) {
    context.runtime_flags &= ~kPendingBlockHeadFlag;
    state.trap_code = context.trap_code;
    call.stack_size = context.sp_offset;
    state.status = kTrap;
    return dispatch_iteration::stop_dispatch;
  }

  call.stack_size = context.sp_offset;
  if (result.kind == kComplete || context.ip == kNoPc ||
      context.ip == kSentinel) {
    if constexpr (CollectStats)
      ++state.metrics.stats.control_handler_count;
    context.runtime_flags &= ~kPendingBlockHeadFlag;
    context.ip = kNoPc;
    state.status = kComplete;
    return dispatch_iteration::stop_dispatch;
  }
  if (result.kind != kBlockBoundary) {
    call.error_code = kNativeErrorInternal;
    return dispatch_iteration::error;
  }
  context.runtime_flags |= kPendingBlockHeadFlag;

  if constexpr (CollectStats) {
    ++state.metrics.stats.control_handler_count;
    state.metrics.stats.control_handler_pending_trace = true;
  }
  call.stack_size = context.sp_offset;
  const auto *active = active_frame(context);
  if (active == nullptr) {
    call.error_code = kNativeErrorInternal;
    return dispatch_iteration::error;
  }
  const auto next_pc = active->function_view->code_pc_offset + context.ip;
  state.current_pc = next_pc;
  if (context.loop_jump_count >= call.yield_threshold) {
    state.status = kDispatchYield;
    return dispatch_iteration::stop_dispatch;
  }
  return dispatch_iteration::continue_dispatch;
}

template <bool CollectStats, bool WithExtension, typename ResultType>
void write_native_dispatch_result(
    const native_dispatch_state<CollectStats, WithExtension, ResultType> &state,
    ResultType &result) {
  result = {};
  result.status = state.status;
  state.call.context->trap_code = state.trap_code;
  if constexpr (CollectStats) {
    result.trace_count = state.metrics.stats.trace_count;
    result.body_count = state.metrics.stats.body_count;
    result.dispatcher_trace_transitions =
        state.metrics.stats.dispatcher_trace_transitions;
    result.control_handler_count = state.metrics.stats.control_handler_count;
    result.interpreted_block_count =
        state.metrics.stats.interpreted_block_count;
  }
  if constexpr (WithExtension &&
                !std::is_same_v<ResultType, fb_native_result>) {
    result.eligible_block_visits =
        state.metrics.observations.eligible_block_visits;
  }
}

int native_abi_error(fb_native_result *result, std::uint32_t error_code) {
  result->error_code = error_code == 0 ? kNativeErrorInternal : error_code;
  return 0;
}

template <bool CollectStats, bool WithExtension, typename Debugger = void,
          typename ResultType = fb_native_result>
int run_native_dispatch_abi(const fb_native_dispatch_call *input,
                            ResultType *result) {
  assert(result != nullptr && input != nullptr);
  assert(input->context != nullptr);
  *result = {};

  if constexpr (WithExtension) {
    assert(input->extension != nullptr);
    assert(input->extension->execute != nullptr);
    assert(input->extension->observe != nullptr);
  } else {
    assert(input->extension == nullptr);
  }

  if constexpr (!std::is_void_v<Debugger>) {
    [[maybe_unused]] auto *debug = static_cast<debug_context *>(input->context);
    assert(debug != nullptr && debug->control != nullptr);
    assert(debug->control->breakpoint_count == 0 ||
           debug->control->breakpoints != nullptr);
  }
  native_dispatch_call call(*input);
  prepare_native_dispatch_call(call);

  native_dispatch_state<CollectStats, WithExtension, ResultType> state{
      call,
      call.call_frame->function_view->code_pc_offset + call.initial_ip,
      kFallback,
      0,
      {}};
  const auto previous_runtime_flags = call.context->runtime_flags;
  if (call.initial_ip == 0)
    call.context->runtime_flags |= kPendingBlockHeadFlag;
  if constexpr (WithExtension) {
    call.context->runtime_flags |= kStopAtDefinedCallBoundaryFlag;
  }
  while (true) {
    if (!refresh_native_dispatch_frame(call, state.current_pc)) {
      call.error_code = kNativeErrorInternal;
      break;
    }
    dispatch_iteration outcome;
    if constexpr (std::is_void_v<Debugger>) {
      const auto ip = call.context->ip;
      std::uint32_t executed_bodies = 0;
      if constexpr (WithExtension) {
        if ((call.context->runtime_flags & kPendingBlockHeadFlag) != 0 &&
            ip < call.code_size &&
            !opcode_is_control_terminator(call.code[ip]) &&
            call.code[ip] != kOpcodeFcPrefix) {
          executed_bodies =
              call.extension->execute(call.request, state.current_pc);
        }
      }
      outcome = execute_interpreted_block<CollectStats, WithExtension, Debugger,
                                          ResultType>(state, executed_bodies);
    } else {
      outcome = execute_interpreted_block<CollectStats, WithExtension, Debugger,
                                          ResultType>(state);
    }
    if (outcome == dispatch_iteration::error)
      break;
    if (outcome == dispatch_iteration::stop_dispatch)
      break;
  }

  call.context->runtime_flags =
      (previous_runtime_flags & ~kPendingBlockHeadFlag) |
      (call.context->runtime_flags & kPendingBlockHeadFlag);
  if (call.error_code != 0)
    return native_abi_error(result, call.error_code);
  write_native_dispatch_result(state, *result);
  return 1;
}

int run_native_step_abi(const fb_native_step_call *call,
                        fb_native_result *output, bool direct_control) {
  assert(call != nullptr && output != nullptr);
  *output = {};
  assert(call->code != nullptr && call->context != nullptr);
  assert(call->stack != nullptr && call->locals != nullptr);
  assert(call->control_stack != nullptr);
  assert(call->code_bytes <= std::numeric_limits<std::uint32_t>::max());
  assert(call->stack_capacity <= 128 &&
         call->stack_size <= call->stack_capacity);
  assert(call->context_bytes >= sizeof(fireball_execution_context_native));
  assert(call->stack_bytes >= 128 * sizeof(std::uint32_t));
  assert(call->locals_bytes >=
         static_cast<std::uint64_t>(call->local_slots) * sizeof(std::uint32_t));
  assert(call->control_bytes >= sizeof(fireball_control_stack_native));
  assert(call->ip < call->code_bytes);

  const auto *raw_code = call->code;
  auto *execution_context =
      static_cast<fireball_execution_context_native *>(call->context);
  auto *control_stack =
      static_cast<fireball_control_stack_native *>(call->control_stack);
  auto *stack = call->stack;
  auto *locals = call->locals;
  execution_context->sp_capacity = call->stack_capacity;
  assert(control_stack->size <= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY);
  assert(call->control_base <= control_stack->size);
  if (execution_context->call_stack != nullptr) {
    assert(execution_context->call_stack->size <=
           FIREBALL_NATIVE_CALL_STACK_CAPACITY);
    assert(execution_context->call_base <= execution_context->call_stack->size);
  }

  execution_context->ip = call->ip;
  execution_context->code = raw_code;
  execution_context->code_size = static_cast<std::uint32_t>(call->code_bytes);
  execution_context->control_stack = control_stack;
  execution_context->control_base = call->control_base;
  execution_context->sp_offset = call->stack_size;
  execution_context->stack_checkpoint = call->stack_size;

  execution_context->trap_code = 0;
  const auto local_offset =
      execution_context->call_stack == nullptr ||
              execution_context->call_stack->size == 0
          ? 0u
          : execution_context->call_stack
                ->frames[execution_context->call_stack->size - 1]
                .local_base;
  auto *local_base = locals + local_offset;
  auto *sp = stack + execution_context->sp_offset;
  const auto tos = top_value(*execution_context, stack);
  op_result step{};
  if (direct_control) {
    assert(opcode_is_control_terminator(raw_code[call->ip]) ||
           raw_code[call->ip] == kOpcodeFcPrefix);
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    const auto previous_flags = execution_context->runtime_flags;
    execution_context->runtime_flags |= kStopAfterControlFlag;
    step = dispatch_next(execution_context, sp, local_base, tos);
    execution_context->runtime_flags = previous_flags;
  } else {
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    step = dispatch_next(execution_context, sp, local_base, tos);
  }

  if (step.kind == kFallback) {
    execution_context->sp_offset = execution_context->stack_checkpoint;
    output->status = kFallback;
  } else if (step.kind == kBlockBoundary) {
    execution_context->runtime_flags |= kPendingBlockHeadFlag;
    output->status = kBlockBoundary;
  } else if (step.kind == kTrap) {
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    output->status = kTrap;
  } else {
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    execution_context->ip = execution_context->code_size;
    output->status = kComplete;
  }
  return 1;
}

} // namespace

extern "C" int fb_native_run_step(const fb_native_step_call *call,
                                  fb_native_result *result) {
  return run_native_step_abi(call, result, false);
}

extern "C" int fb_native_run_control_step(const fb_native_step_call *call,
                                          fb_native_result *result) {
  return run_native_step_abi(call, result, true);
}

extern "C" int fb_native_run_dispatch(const fb_native_dispatch_call *call,
                                      fb_native_result *result) {
  return run_native_dispatch_abi<false, false>(call, result);
}

extern "C" int
fb_native_run_dispatch_extension(const fb_native_dispatch_call *call,
                                 fb_native_result *result) {
  return run_native_dispatch_abi<false, true>(call, result);
}

extern "C" int fb_native_run_debug_dispatch(const fb_native_dispatch_call *call,
                                            fb_native_result *result) {
  return run_native_dispatch_abi<false, false, debugger_aspect>(call, result);
}

#ifndef FIREBALL_NATIVE_INTERPRETER_UTILITIES_HXX
#define FIREBALL_NATIVE_INTERPRETER_UTILITIES_HXX

// CPS state, bytecode decoding, fixed-stack access, and control-flow
// primitives. Included before opcode handlers in the single native interpreter
// translation unit.
namespace {

constexpr std::uint32_t kComplete = 1;
constexpr std::uint32_t kTrap = 2;
constexpr std::uint32_t kBlockBoundary = 3;
constexpr std::uint32_t kCallBoundary = 4;
constexpr std::uint32_t kRuntimeApiBoundary = 8;
constexpr std::uint32_t kStopAtBlockBoundaryFlag = 1u << 0;
constexpr std::uint32_t kStopAfterControlFlag = 1u << 1;
constexpr std::uint32_t kPendingBlockHeadFlag = 1u << 5;
constexpr std::uint32_t kCpsExitShift = 8;
constexpr std::uint32_t kCpsExitMask = fb_native_cps_exit_mask;
constexpr std::uint32_t kDispatchYield = 5;
constexpr std::uint32_t kNativeCallBoundary = 6;
constexpr std::uint32_t kDebugStop = 7;
constexpr std::uint32_t kTrapLocalStackCapacity = 1;
constexpr std::uint32_t kTrapCallStackCapacity = 3;
constexpr std::uint32_t kTrapOperandStackCapacity = 4;
constexpr std::uint32_t kTrapControlFrameCapacity = 10;
constexpr std::uint32_t kNativeErrorInternal = 4;
constexpr std::uint32_t kTrapTableIndexOutOfBounds = 6;
constexpr std::uint32_t kTrapTableSlotUninitialized = 7;
constexpr std::uint32_t kTrapUnreachable = 9;
constexpr std::uint32_t kTrapMemoryOutOfBounds = 13;
constexpr std::uint32_t kTrapDivideByZero = 15;
constexpr std::uint32_t kTrapIntegerOverflow = 16;
constexpr std::uint32_t kTrapInvalidConversion = 17;
constexpr std::uint32_t kSentinel = 0xFFFF'FFFFu;
constexpr std::uint32_t kOpcodeFcPrefix = 0xFC;

using execution_context = fireball_execution_context_native;

[[gnu::always_inline]] inline std::uint32_t
instruction_offset(const execution_context &current, const std::uint8_t *ip);

[[gnu::always_inline]] inline fireball_call_frame_native &
active_frame(execution_context &current);
[[gnu::always_inline]] inline const fireball_call_frame_native &
active_frame(const execution_context &current);

static_assert(sizeof(void *) == 8,
              "native interpreter requires a 64-bit host ABI");
static_assert(sizeof(execution_context) == 160,
              "x86-64 execution_context ABI layout must remain 160 bytes");
static_assert(offsetof(execution_context, trap_code) == 0x88);
static_assert(offsetof(execution_context, local_base) == 0x90);

inline std::uint32_t
control_frame_index(const execution_context &current,
                    const fireball_control_frame_native *p) {
  assert(current.control_stack != nullptr);
  const auto *begin = current.control_stack->frames;
  assert(p >= begin && p <= begin + FIREBALL_NATIVE_CONTROL_STACK_CAPACITY);
  return static_cast<std::uint32_t>(p - begin);
}

inline std::uint32_t
control_frame_base_index(const execution_context &current) {
  return control_frame_index(current, current.control_frame_base);
}

inline std::uint32_t call_frame_index(const execution_context &current,
                                      const fireball_call_frame_native *p) {
  assert(current.call_stack != nullptr);
  const auto *begin = current.call_stack->frames;
  assert(p >= begin && p <= begin + FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  return static_cast<std::uint32_t>(p - begin);
}

inline void record_cps_exit(execution_context &current, std::uint32_t kind) {
  current.runtime_flags =
      (current.runtime_flags & ~kCpsExitMask) | ((kind + 1) << kCpsExitShift);
}

inline std::uint32_t cps_exit_kind(const execution_context &current) {
  const auto encoded = (current.runtime_flags & kCpsExitMask) >> kCpsExitShift;
  assert(encoded != 0 && encoded <= 9);
  return encoded - 1;
}

inline bool cps_exit_pending(const execution_context &current) {
  return (current.runtime_flags & kCpsExitMask) != 0;
}

inline void clear_cps_exit(execution_context &current) {
  current.runtime_flags &= ~kCpsExitMask;
}

inline bool runtime_api_boundary(execution_context &current, std::uint32_t ip) {
  current.ip = ip;
  current.trap_code = 0;
  record_cps_exit(current, kRuntimeApiBoundary);
  return false;
}

inline bool block_boundary(execution_context &current, std::uint32_t ip) {
  current.ip = ip;
  current.trap_code = 0;
  record_cps_exit(current, kBlockBoundary);
  return false;
}

inline bool complete(execution_context &current) {
  current.ip = kSentinel;
  current.trap_code = 0;
  record_cps_exit(current, kComplete);
  return false;
}

inline bool trap(execution_context &current, std::uint32_t code) {
  current.trap_code = code;
  record_cps_exit(current, kTrap);
  return false;
}

inline bool call_boundary(execution_context &current, std::uint32_t ip) {
  current.ip = ip;
  current.trap_code = 0;
  record_cps_exit(current, kCallBoundary);
  return false;
}

inline bool debug_stop(execution_context &current) {
  current.trap_code = 0;
  record_cps_exit(current, kDebugStop);
  return false;
}

struct debug_context {
  execution_context execution;
  fb_native_debug_control *control;
};

static_assert(offsetof(debug_context, execution) == 0);
static_assert(offsetof(debug_context, control) == sizeof(execution_context));

struct debugger_aspect {
  [[gnu::always_inline]] static inline bool
  before_instruction(execution_context &context, const std::uint8_t *ip) {
    auto &control = *reinterpret_cast<debug_context *>(&context)->control;
    if (control.enabled == 0)
      return false;
    const auto &frame = active_frame(context);
    const auto pc = frame.function_view->code_pc_offset +
                    instruction_offset(context, ip);
    control.current_pc = pc;
    if (control.single_step != 0 && control.executed != 0) {
      control.stopped = 1;
      return true;
    }
    if (control.executed != 0) {
      if (control.breakpoint_count != 0) {
        auto first = control.breakpoints;
        auto count = control.breakpoint_count;
        while (count != 0) {
          const auto step = count / 2;
          auto middle = first + step;
          if (*middle < pc) {
            first = middle + 1;
            count -= step + 1;
          } else {
            count = step;
          }
        }
        if (first != control.breakpoints + control.breakpoint_count &&
            *first == pc) {
          control.stopped = 1;
          return true;
        }
      }
    }
    control.executed = 1;
    return false;
  }
};

// Inlining this boundary makes each handler tail-jump through the opcode table.
template <typename Debugger = void>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
dispatch_next(execution_context *context, const std::uint8_t *ip,
              std::uint32_t *sp, const std::uint8_t *rel);

template <typename Debugger>
[[gnu::always_inline]] inline FIREBALL_CPS_CALL void
continue_cps(execution_context *context, const std::uint8_t *ip,
             std::uint32_t *sp, const std::uint8_t *rel) {
  [[clang::musttail]] return dispatch_next<Debugger>(context, ip, sp, rel);
}

[[gnu::always_inline]] inline std::uint32_t read_u32(const std::uint8_t *&rel) {
  std::uint32_t value;
  std::memcpy(&value, rel, sizeof(value));
  rel += sizeof(value);
  return value;
}

[[gnu::always_inline]] inline std::uint32_t
instruction_width(const std::uint8_t *rel) {
  if (*rel != 0xFFu)
    return *rel;
  std::uint32_t width;
  std::memcpy(&width, rel + 1, sizeof(width));
  assert(width >= 0xFFu);
  return width;
}

[[gnu::always_inline]] inline void
advance_instruction_ip(const std::uint8_t *&ip, const std::uint8_t *&rel) {
  if (*rel != 0xFFu) {
    ip += *rel++;
    return;
  }
  std::uint32_t width;
  std::memcpy(&width, rel + 1, sizeof(width));
  assert(width >= 0xFFu);
  ip += width;
  rel += 1 + sizeof(width);
}

[[gnu::always_inline]] inline void
consume_instruction_width(const std::uint8_t *&rel) {
  rel += *rel == 0xFFu ? 1 + sizeof(std::uint32_t) : 1;
}

[[gnu::always_inline]] inline std::uint32_t
instruction_offset(const execution_context &current, const std::uint8_t *ip) {
  assert(current.code != nullptr && ip >= current.code &&
         ip <= current.code + current.code_size);
  return static_cast<std::uint32_t>(ip - current.code);
}

[[gnu::always_inline]] inline std::uint32_t peek_u32(const std::uint8_t *rel) {
  std::uint32_t value = 0;
  std::memcpy(&value, rel, sizeof(value));
  return value;
}

inline bool pop(execution_context &current, std::uint32_t *&sp,
                std::uint32_t &value);
inline bool push(execution_context &current, std::uint32_t *&sp,
                 std::uint32_t value);

[[gnu::always_inline]] inline std::int32_t read_s32(const std::uint8_t *&rel) {
  return static_cast<std::int32_t>(read_u32(rel));
}

[[gnu::always_inline]] inline std::int64_t read_s64(const std::uint8_t *&rel) {
  std::uint64_t value;
  std::memcpy(&value, rel, sizeof(value));
  rel += sizeof(value);
  return static_cast<std::int64_t>(value);
}

inline bool pop_u64(execution_context &current, std::uint32_t *&sp,
                    std::uint64_t &value) {
  if (sp < current.stack_base + 2)
    return false;
  const auto low = sp[-2];
  const auto high = sp[-1];
  sp -= 2;
  value = static_cast<std::uint64_t>(low) |
          (static_cast<std::uint64_t>(high) << 32);
  return true;
}

inline bool push_u64(execution_context &current, std::uint32_t *&sp,
                     std::uint64_t value) {
  if (current.stack_limit - sp < 2)
    return false;
  *sp++ = static_cast<std::uint32_t>(value);
  *sp++ = static_cast<std::uint32_t>(value >> 32);
  return true;
}

inline std::uint64_t combine_u64(std::uint32_t low, std::uint32_t high) {
  return static_cast<std::uint64_t>(low) |
         (static_cast<std::uint64_t>(high) << 32);
}

template <bool ResultIsBool>
inline void replace_u64_operands(std::uint32_t *&sp, std::uint64_t result,
                                 std::uint32_t operand_words) {
  constexpr auto result_words = ResultIsBool ? 1u : 2u;
  auto *target = sp - operand_words;
  sp = target + result_words;
  target[0] = static_cast<std::uint32_t>(result);
  if constexpr (!ResultIsBool)
    target[1] = static_cast<std::uint32_t>(result >> 32);
}

inline float f32_from_bits(std::uint32_t value) {
  return std::bit_cast<float>(value);
}
inline std::uint32_t f32_to_bits(float value) {
  return std::bit_cast<std::uint32_t>(value);
}
inline double f64_from_bits(std::uint64_t value) {
  return std::bit_cast<double>(value);
}
inline std::uint64_t f64_to_bits(double value) {
  return std::bit_cast<std::uint64_t>(value);
}

inline bool pop_f32(execution_context &current, std::uint32_t *&sp,
                    float &value) {
  std::uint32_t raw = 0;
  if (!pop(current, sp, raw))
    return false;
  value = f32_from_bits(raw);
  return true;
}

inline bool push_f32(execution_context &current, std::uint32_t *&sp,
                     float value) {
  return push(current, sp, f32_to_bits(value));
}

inline bool pop_f64(execution_context &current, std::uint32_t *&sp,
                    double &value) {
  std::uint64_t raw = 0;
  if (!pop_u64(current, sp, raw))
    return false;
  value = f64_from_bits(raw);
  return true;
}

inline bool push_f64(execution_context &current, std::uint32_t *&sp,
                     double value) {
  return push_u64(current, sp, f64_to_bits(value));
}

[[gnu::always_inline]] inline fireball_call_frame_native &
active_frame(execution_context &current) {
  assert(current.call_stack != nullptr);
  assert(current.call_stack->size != 0);
  assert(current.call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(current.call_frame_base != nullptr);
  assert(current.call_frame_base >= current.call_stack->frames);
  assert(current.call_frame_base <
         current.call_stack->frames + current.call_stack->size);
  auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  assert(frame.function_view != nullptr);
  return frame;
}

[[gnu::always_inline]] inline const fireball_call_frame_native &
active_frame(const execution_context &current) {
  assert(current.call_stack != nullptr);
  assert(current.call_stack->size != 0);
  assert(current.call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(current.call_frame_base != nullptr);
  assert(current.call_frame_base >= current.call_stack->frames);
  assert(current.call_frame_base <
         current.call_stack->frames + current.call_stack->size);
  const auto &frame = current.call_stack->frames[current.call_stack->size - 1];
  assert(frame.function_view != nullptr);
  return frame;
}

[[gnu::always_inline]] inline const std::uint8_t *
interpreter_rel_for_block_index(const fireball_call_frame_native &frame,
                                std::uint32_t block_index) {
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  assert(function.interpreter_constant_pool != nullptr ||
         function.interpreter_constant_byte_count == 0);
  assert(function.module_view != nullptr);
  const auto &module = *function.module_view;
  assert(module.blocks != nullptr);
  assert(block_index < module.block_count);
  const auto &block = module.blocks[block_index];
  assert(block.function_index == frame.func_index);
  const auto offset = block.interpreter_constant_byte_offset;
  assert(offset != kSentinel);
  assert(offset <= function.interpreter_constant_byte_count);
  if (function.interpreter_constant_pool == nullptr) {
    assert(offset == 0 && function.interpreter_constant_byte_count == 0);
    return nullptr;
  }
  return function.interpreter_constant_pool + offset;
}

[[gnu::always_inline]] inline const std::uint8_t *
interpreter_rel_after_jit_trace(const fireball_call_frame_native &frame,
                                std::uint32_t block_index,
                                std::uint32_t consumed_bytes) {
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  assert(function.module_view != nullptr);
  const auto &module = *function.module_view;
  assert(module.blocks != nullptr);
  assert(block_index < module.block_count);
  assert(module.blocks[block_index].function_index == frame.func_index);
  const auto block_offset =
      module.blocks[block_index].interpreter_constant_byte_offset;
  assert(block_offset != kSentinel);
  assert(block_offset <= function.interpreter_constant_byte_count);
  assert(consumed_bytes <=
         function.interpreter_constant_byte_count - block_offset);
  const auto byte_offset = block_offset + consumed_bytes;
  if (function.interpreter_constant_pool == nullptr) {
    assert(byte_offset == 0 && function.interpreter_constant_byte_count == 0);
    return nullptr;
  }
  return function.interpreter_constant_pool + byte_offset;
}

[[gnu::always_inline]] inline bool
finish_current_function(execution_context &current,
                        std::uint32_t *&local_base,
                        const std::uint8_t *&instruction_ip) {
  auto *stack = current.call_stack;
  assert(stack != nullptr);
  assert(stack->size != 0);
  if (&active_frame(current) == current.call_frame_base) {
    current.ip = kSentinel;
    return true;
  }

  const auto finished = stack->frames[stack->size - 1];
  assert(finished.return_ip != kSentinel);
  --stack->size;
  current.local_stack_pointer = finished.local_base;
  current.control_stack->size =
      control_frame_index(current, finished.control_frame_base);

  auto &caller = stack->frames[stack->size - 1];
  assert(caller.function_view != nullptr);
  local_base = caller.local_base;
  current.local_base = local_base;
  current.code = caller.function_view->code;
  current.code_size = caller.function_view->code_size;
  current.control_frame_base = caller.control_frame_base;
  instruction_ip = current.code + finished.return_ip;
  return false;
}

inline bool is_requested_block_boundary(const execution_context &current,
                                        std::uint32_t ip) {
  const auto &frame = active_frame(current);
  const auto pc = frame.function_view->code_pc_offset + ip;
  return pc == frame.boundary_next_pc || pc == frame.boundary_loops_to;
}

inline bool should_stop_after_control(const execution_context &current) {
  return (current.runtime_flags & kStopAfterControlFlag) != 0;
}

inline bool record_loop_backedge(execution_context &current,
                                 std::uint32_t source_ip,
                                 std::uint32_t target_ip) {
  if (target_ip >= source_ip)
    return false;
  if (current.loop_jump_count < UINT32_MAX)
    ++current.loop_jump_count;
  return true;
}

inline const fireball_control_map_entry_native *
control_entry(const execution_context &current, std::uint32_t ip) {
  const auto &frame = active_frame(current);
  assert(frame.function_view->control_map != nullptr);
  if (ip >= current.code_size) {
    return nullptr;
  }
  return &frame.function_view->control_map[ip];
}

struct local_value_span {
  std::uint16_t offset;
  std::uint8_t width;
};

inline local_value_span local_span(const fireball_call_frame_native &frame,
                                   std::uint32_t index) {
  assert(frame.function_view != nullptr);
  const auto &function = *frame.function_view;
  assert(index < function.local_count);
  const auto offset = function.local_offsets[index];
  const auto width = function.local_sizes[index];
  assert(width == 1 || width == 2);
  assert(offset + width <= function.local_slot_count);
  return {offset, width};
}

[[gnu::always_inline]] inline bool branch(execution_context &current,
                                          std::uint32_t *&sp,
                                          std::uint32_t depth,
                                          std::uint32_t &next_ip) {
  const auto *control_end =
      current.control_stack->frames + current.control_stack->size;
  const auto frame_count =
      static_cast<std::uint32_t>(control_end - current.control_frame_base);
  if (depth > frame_count)
    return false;
  const auto &call_frame = active_frame(current);
  const auto outer_result_arity = call_frame.function_view->result_arity;
  const auto target_index = frame_count - depth - 1;
  std::uint32_t result_arity = outer_result_arity;
  auto *saved_height = current.stack_base;
  const fireball_control_map_entry_native *target_entry = nullptr;
  bool target_is_loop = false;
  if (depth < frame_count) {
    const auto &target = current.control_frame_base[target_index];
    target_entry = control_entry(current, target.start);
    if (target_entry == nullptr || target_entry->match_end == kSentinel)
      return false;
    assert(target.start < current.code_size);
    target_is_loop = current.code[target.start] == 0x03u;
    saved_height = target.stack_height;
    result_arity = target_is_loop ? 0u : target_entry->result_arity;
  }
  if (result_arity > 2 || saved_height > sp ||
      result_arity > current.stack_limit - saved_height ||
      result_arity > sp - saved_height) {
    return false;
  }
  const auto result0 =
      result_arity >= 1 ? sp[-static_cast<std::ptrdiff_t>(result_arity)] : 0;
  const auto result1 = result_arity >= 2 ? sp[-1] : 0;
  sp = saved_height;
  if (result_arity >= 1)
    *sp++ = result0;
  if (result_arity >= 2)
    *sp++ = result1;
  if (depth == frame_count) {
    current.control_stack->size = control_frame_base_index(current);
    next_ip = kSentinel;
    return true;
  }
  if (target_is_loop) {
    current.control_stack->size =
        control_frame_base_index(current) + target_index + 1;
    next_ip = target_entry->next_pc;
  } else {
    current.control_stack->size =
        control_frame_base_index(current) + target_index;
    next_ip = target_entry->match_end + 1;
  }
  return true;
}

inline void prune_control_frames_after_static_jump(execution_context &current,
                                                   std::uint32_t target_ip) {
  // A loader-resolved basic-block successor may skip one or more `end`
  // handlers. Drop lexical frames whose ranges no longer contain its target
  // before continuing native dispatch.
  while (current.control_stack->frames + current.control_stack->size >
         current.control_frame_base) {
    const auto &frame =
        current.control_stack->frames[current.control_stack->size - 1];
    const auto *entry = control_entry(current, frame.start);
    assert(entry != nullptr && entry->match_end != kSentinel);
    if (target_ip <= entry->match_end)
      return;
    --current.control_stack->size;
  }
}

inline bool pop(execution_context &current, std::uint32_t *&sp,
                std::uint32_t &value) {
  if (sp == current.stack_base) {
    return false;
  }
  value = sp[-1];
  --sp;
  return true;
}

inline bool push(execution_context &current, std::uint32_t *&sp,
                 std::uint32_t value) {
  if (sp >= current.stack_limit) {
    return false;
  }
  *sp++ = value;
  return true;
}

// Named opcode operations keep the API catalog readable and independently
// callable; no anonymous callable bodies are embedded in handler tables.
template <typename T> inline bool opcode_eqz(T value) { return value == 0; }

template <typename T> inline bool opcode_signed_lt(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) <
         static_cast<std::make_signed_t<T>>(right);
}

template <typename T> inline bool opcode_signed_gt(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) >
         static_cast<std::make_signed_t<T>>(right);
}

template <typename T> inline bool opcode_signed_le(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) <=
         static_cast<std::make_signed_t<T>>(right);
}

template <typename T> inline bool opcode_signed_ge(T left, T right) {
  return static_cast<std::make_signed_t<T>>(left) >=
         static_cast<std::make_signed_t<T>>(right);
}

template <typename T> inline T opcode_clz(T value) {
  return static_cast<T>(std::countl_zero(value));
}

template <typename T> inline T opcode_ctz(T value) {
  return static_cast<T>(std::countr_zero(value));
}

template <typename T> inline T opcode_popcount(T value) {
  return static_cast<T>(std::popcount(value));
}

template <typename T> inline T opcode_shift_left(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  return static_cast<T>(left << (right & shift_mask));
}

template <typename T> inline T opcode_shift_right_signed(T left, T right) {
  using signed_type = std::make_signed_t<T>;
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  return static_cast<T>(static_cast<signed_type>(left) >> (right & shift_mask));
}

template <typename T> inline T opcode_shift_right_unsigned(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  return static_cast<T>(left >> (right & shift_mask));
}

template <typename T> inline T opcode_rotate_left(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  const auto shift = static_cast<unsigned>(right & shift_mask);
  return static_cast<T>(
      (left << shift) |
      (left >> ((std::numeric_limits<T>::digits - shift) & shift_mask)));
}

template <typename T> inline T opcode_rotate_right(T left, T right) {
  constexpr auto shift_mask = std::numeric_limits<T>::digits - 1;
  const auto shift = static_cast<unsigned>(right & shift_mask);
  return static_cast<T>(
      (left >> shift) |
      (left << ((std::numeric_limits<T>::digits - shift) & shift_mask)));
}

template <typename T>
inline bool opcode_div_signed(T left, T right, T &result,
                              std::uint32_t &trap_code) {
  using signed_type = std::make_signed_t<T>;
  const auto signed_left = static_cast<signed_type>(left);
  const auto signed_right = static_cast<signed_type>(right);
  if (signed_right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  if (signed_left == std::numeric_limits<signed_type>::min() &&
      signed_right == -1) {
    trap_code = kTrapIntegerOverflow;
    return false;
  }
  result = static_cast<T>(signed_left / signed_right);
  trap_code = 0;
  return true;
}

template <typename T>
inline bool opcode_div_unsigned(T left, T right, T &result,
                                std::uint32_t &trap_code) {
  if (right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = left / right;
  trap_code = 0;
  return true;
}

template <typename T>
inline bool opcode_rem_signed(T left, T right, T &result,
                              std::uint32_t &trap_code) {
  using signed_type = std::make_signed_t<T>;
  const auto signed_left = static_cast<signed_type>(left);
  const auto signed_right = static_cast<signed_type>(right);
  if (signed_right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  if (signed_left == std::numeric_limits<signed_type>::min() &&
      signed_right == -1)
    result = 0;
  else
    result = static_cast<T>(signed_left % signed_right);
  trap_code = 0;
  return true;
}

template <typename T>
inline bool opcode_rem_unsigned(T left, T right, T &result,
                                std::uint32_t &trap_code) {
  if (right == 0) {
    trap_code = kTrapDivideByZero;
    return false;
  }
  result = left % right;
  trap_code = 0;
  return true;
}

template <typename T> inline T opcode_extend8_signed(T value) {
  return static_cast<T>(static_cast<std::int8_t>(value));
}

template <typename T> inline T opcode_extend16_signed(T value) {
  return static_cast<T>(static_cast<std::int16_t>(value));
}

inline std::uint64_t opcode_extend32_signed(std::uint64_t value) {
  return static_cast<std::uint64_t>(
      static_cast<std::int64_t>(static_cast<std::int32_t>(value)));
}

template <typename T> inline T opcode_float_abs(T value) {
  return std::fabs(value);
}

template <typename T> inline T opcode_float_neg(T value) { return -value; }

template <typename T> inline T opcode_float_ceil(T value) {
  return std::ceil(value);
}

template <typename T> inline T opcode_float_floor(T value) {
  return std::floor(value);
}

template <typename T> inline T opcode_float_trunc(T value) {
  return std::trunc(value);
}

template <typename T> inline T opcode_float_nearest(T value) {
  return std::nearbyint(value);
}

template <typename T> inline T opcode_float_sqrt(T value) {
  return std::sqrt(value);
}

template <typename T> inline T opcode_float_div(T left, T right) {
  if (right != 0)
    return left / right;
  if (left == 0 || std::isnan(left))
    return std::numeric_limits<T>::quiet_NaN();
  const auto sign = std::signbit(left) == std::signbit(right) ? T{1} : T{-1};
  return std::copysign(std::numeric_limits<T>::infinity(), sign);
}

template <typename T> inline T opcode_float_min(T left, T right) {
  if (std::isnan(left) || std::isnan(right))
    return std::numeric_limits<T>::quiet_NaN();
  if (left == 0 && right == 0)
    return std::signbit(left) ? left : right;
  return left < right ? left : right;
}

template <typename T> inline T opcode_float_max(T left, T right) {
  if (std::isnan(left) || std::isnan(right))
    return std::numeric_limits<T>::quiet_NaN();
  if (left == 0 && right == 0)
    return std::signbit(left) ? right : left;
  return left > right ? left : right;
}

template <typename T> inline T opcode_float_copysign(T left, T right) {
  return std::copysign(left, right);
}

} // namespace

#endif

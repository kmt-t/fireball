#ifndef FIREBALL_NATIVE_INTERPRETER_RUNTIME_HXX
#define FIREBALL_NATIVE_INTERPRETER_RUNTIME_HXX

// Native dispatch orchestration and Runtime API entry adapters.
// Depends on utilities and the opcode handler tables.
namespace {

constexpr std::uint32_t kNoPc = 0xFFFF'FFFFu;

#ifndef FB_CONF_NATIVE_BLOCK_SEARCH_CACHE
#define FB_CONF_NATIVE_BLOCK_SEARCH_CACHE 1
#endif

struct native_block_search_cache_entry {
  const fireball_wasm_module_execution_view_native *module = nullptr;
  std::uint32_t head_pc = kNoPc;
  std::uint32_t block_index = kNoPc;
};

static_assert(sizeof(native_block_search_cache_entry) == 16);

#if FB_CONF_JIT_BLOCK_COUNTERS
constexpr bool kCollectJitBlockCounters = true;
#else
constexpr bool kCollectJitBlockCounters = false;
#endif

template <bool CollectStats, bool WithJit, bool WithExtension,
          bool ReturnAtBlockBoundary, typename Debugger>
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
template <bool CollectStats, bool WithExtension>
struct native_dispatch_metrics {
  std::conditional_t<CollectStats, native_dispatch_stats,
                     empty_dispatch_metrics>
      stats;
  std::conditional_t<WithExtension, native_dispatch_observations,
                     empty_dispatch_metrics>
      observations;
};

template <bool WithExtension>
using dispatch_input_t =
    std::conditional_t<WithExtension, fb_native_dispatch_extension_call,
                       fb_native_dispatch_call>;

template <bool WithExtension>
inline const fb_native_dispatch_call &
base_dispatch_input(const dispatch_input_t<WithExtension> &input) {
  if constexpr (WithExtension)
    return input.call;
  else
    return input;
}

template <bool WithExtension>
inline const fb_native_execution_extension *
execution_extension(const dispatch_input_t<WithExtension> &input) {
  if constexpr (WithExtension)
    return input.extension;
  else
    return nullptr;
}

template <bool CollectStats, bool WithJit, bool WithExtension,
          bool ReturnAtBlockBoundary, typename ResultType = fb_native_result>
struct native_dispatch_state {
  static_assert(!CollectStats || !std::is_same_v<ResultType, fb_native_result>);
  const fb_native_dispatch_call &input;
  const fb_native_execution_extension *extension;
  const fb_native_jit_provider *jit_provider;
  execution_context &context;
  std::uint32_t status = kNativeErrorInternal;
  std::uint32_t trap_code = 0;
  std::uint32_t error_code = 0;
  [[no_unique_address]] native_dispatch_metrics<
      CollectStats,
      WithExtension && !std::is_same_v<ResultType, fb_native_result>> metrics;
  native_block_search_cache_entry
      block_search_cache[FIREBALL_NATIVE_BLOCK_SEARCH_CACHE_SIZE]{};
};

template <bool CollectStats, bool WithJit, bool WithExtension,
          bool ReturnAtBlockBoundary, typename ResultType>
[[gnu::always_inline]] inline std::uint32_t find_cached_block_index(
    native_dispatch_state<CollectStats, WithJit, WithExtension,
                          ReturnAtBlockBoundary, ResultType> &state,
    const fireball_wasm_function_execution_view_native &function,
    std::uint32_t ip) {
  assert(function.module_view != nullptr);
  const auto absolute_pc =
      static_cast<std::uint64_t>(function.code_pc_offset) + ip;
  assert(absolute_pc <= std::numeric_limits<std::uint32_t>::max());
  const auto head_pc = static_cast<std::uint32_t>(absolute_pc);
#if FB_CONF_NATIVE_BLOCK_SEARCH_CACHE
  constexpr auto kCacheSize = FIREBALL_NATIVE_BLOCK_SEARCH_CACHE_SIZE;
  static_assert(kCacheSize == 8);
  const auto slot =
      (head_pc ^ (head_pc >> 1) ^ std::rotr(head_pc, 13)) & (kCacheSize - 1);
  auto &entry = state.block_search_cache[slot];
  if (entry.module == function.module_view && entry.head_pc == head_pc) {
    assert(entry.block_index < function.module_view->block_count);
    assert(function.module_view->blocks[entry.block_index].head_pc == head_pc);
    if constexpr (CollectStats)
      ++state.metrics.stats.block_search_cache_hits;
    return entry.block_index;
  }
#endif
  const auto folded_pc = head_pc * UINT32_C(0x9E3779B1);
  const auto block_index = fb_native_find_block_index_folded(
      function.module_view, head_pc, folded_pc);
#if FB_CONF_NATIVE_BLOCK_SEARCH_CACHE
  entry = {function.module_view, head_pc, block_index};
#endif
  if constexpr (CollectStats) {
    ++state.metrics.stats.block_search_cache_misses;
  }
  return block_index;
}

template <bool CollectStats, bool WithJit, bool WithExtension,
          bool ReturnAtBlockBoundary, typename ResultType>
inline void prepare_native_dispatch(
    native_dispatch_state<CollectStats, WithJit, WithExtension,
                          ReturnAtBlockBoundary, ResultType> &state) {
  [[maybe_unused]] const auto &input = state.input;
  auto &context = state.context;
  assert(input.stack != nullptr && input.locals != nullptr);
  assert(context.control_stack != nullptr);
  assert(context.call_stack != nullptr && context.call_stack->size != 0 &&
         context.call_stack->size <= FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(context.stack_base == input.stack);
  assert(context.stack_pointer >= context.stack_base &&
         context.stack_pointer <= context.stack_limit);
  assert(context.loop_jump_threshold != 0 && context.code != nullptr);
  assert(context.control_stack->size <= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY);
  assert(context.control_frame_base >= context.control_stack->frames &&
         context.control_frame_base <=
             context.control_stack->frames + context.control_stack->size);
  assert(context.local_stack_base == input.locals);
  assert(context.local_stack_pointer >= context.local_stack_base &&
         context.local_stack_pointer <= context.local_stack_limit);
  const auto &frame = active_frame(context);
  const auto &function = *frame.function_view;
  assert(frame.local_base >= context.local_stack_base);
  assert(frame.local_base + function.local_slot_count <=
         context.local_stack_limit);
  assert(context.ip <= context.code_size);
  context.code = function.code;
  context.code_size = function.code_size;
  context.control_frame_base = frame.control_frame_base;
  context.local_base = frame.local_base;
}

template <bool CollectStats, bool WithExtension, bool ReturnAtBlockBoundary,
          typename ResultType>
inline std::uint32_t execute_jit_trace(
    native_dispatch_state<CollectStats, true, WithExtension,
                          ReturnAtBlockBoundary, ResultType> &state,
    fireball_call_frame_native &frame, std::uint32_t block_index,
    std::uint32_t pc, const std::uint8_t *&dispatch_rel) {
  auto &context = state.context;
  fb_native_jit_trace trace{};
  const auto lookup = state.jit_provider->lookup(state.jit_provider->owner,
                                                 block_index, pc, &trace);
  if (lookup < 0) {
    state.error_code = kNativeErrorInternal;
    return 0;
  }
  if (lookup == 0)
    return 0;
  assert(trace.entry_address != 0 && trace.rel != nullptr &&
         trace.body_count != 0);
  const auto *trace_exit = reinterpret_cast<const fb_native_jit_trace_exit *>(
      trace.rel - sizeof(fb_native_jit_trace_exit));
  assert(trace_exit->block_index == block_index);
  if (trace.stack_words >
      static_cast<std::uint32_t>(context.stack_limit - context.stack_pointer))
    return 0;
  const auto control_base_index = static_cast<std::uint32_t>(
      context.control_frame_base - context.control_stack->frames);
  if (trace.frame_depth >
      FIREBALL_NATIVE_CONTROL_STACK_CAPACITY - control_base_index)
    return 0;

  context.local_base = frame.local_base;
  context.code = frame.function_view->code;
  context.code_size = frame.function_view->code_size;
  context.runtime_flags &= ~fb_native_cps_exit_mask;
  // Control Runtime APIs consume the loader's static edge for this block.
  frame.boundary_next_pc = trace_exit->next_pc;
  frame.boundary_loops_to = trace_exit->loops_to;
  const auto completed = reinterpret_cast<fireball_native_jit_trace_entry>(
      trace.entry_address)(&context, context.code + context.ip,
                           context.stack_pointer, trace.rel);
  if constexpr (WithExtension) {
    state.extension->trace_executed(&state.input, trace.trace_cookie);
  }
  if (!completed) {
    assert(context.trap_code != 0 || cps_exit_pending(context));
    if (cps_exit_pending(context) &&
        cps_exit_kind(context) == kRuntimeApiBoundary) {
      if (context.ip == trace_exit->body_end_ip) {
        context.rel = interpreter_rel_after_jit_trace(
            frame, trace_exit->block_index, trace_exit->interpreter_rel_bytes);
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
  assert(completed_trace_exit->frame_depth <= trace.frame_depth);
  assert(completed_trace_exit->frame_depth <=
         FIREBALL_NATIVE_CONTROL_STACK_CAPACITY - control_base_index);
  const auto *expected_frame_end =
      context.control_frame_base + completed_trace_exit->frame_depth;
  if (context.control_stack->frames + context.control_stack->size >
      expected_frame_end) {
    context.control_stack->size = static_cast<std::uint32_t>(
        expected_frame_end - context.control_stack->frames);
  }
  frame.boundary_next_pc = completed_trace_exit->next_pc;
  frame.boundary_loops_to = completed_trace_exit->loops_to;
  assert(
      completed_trace_exit->result_words <=
      static_cast<std::uint32_t>(context.stack_limit - context.stack_pointer));
  context.stack_pointer += completed_trace_exit->result_words;
  dispatch_rel = interpreter_rel_after_jit_trace(
      frame, completed_trace_exit->block_index,
      completed_trace_exit->interpreter_rel_bytes);
  context.rel = dispatch_rel;
  return trace.body_count;
}

template <bool CollectStats, bool WithJit, bool WithExtension,
          bool ReturnAtBlockBoundary = false,
          typename ResultType = fb_native_result>
inline void record_dispatch_segment_stats(
    native_dispatch_state<CollectStats, WithJit, WithExtension,
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

template <bool CollectStats, bool WithJit, bool WithExtension,
          bool ReturnAtBlockBoundary = false, typename Debugger = void,
          typename ResultType = fb_native_result>
inline bool handle_runtime_exit(
    native_dispatch_state<CollectStats, WithJit, WithExtension,
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
      state.error_code = kNativeErrorInternal;
      return false;
    }
  case kCallBoundary: {
    // The Runtime API installed the callee frame. Resolve its entry BasicBlock
    // here so block metadata and the matching rel cursor are set together.
    context.runtime_flags |= kPendingBlockHeadFlag;
    if constexpr (CollectStats)
      ++state.metrics.stats.control_handler_count;
    if constexpr (CollectStats)
      state.metrics.stats.control_handler_pending_trace = true;
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
    context.runtime_flags &= ~kPendingBlockHeadFlag;
    state.error_code = kNativeErrorInternal;
    return false;
  }
}

template <bool CollectStats, bool WithJit, bool WithExtension,
          bool ReturnAtBlockBoundary = false, typename Debugger = void,
          typename ResultType = fb_native_result>
inline int run_native_dispatch_abi(const dispatch_input_t<WithExtension> *input,
                                   ResultType *result) {
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
    assert(jit_provider->lookup != nullptr &&
           jit_provider->observe_block != nullptr &&
           jit_provider->finish_boundary != nullptr);
    assert(jit_provider->card_shift < 32 &&
           (jit_provider->card_count == 0 ||
            jit_provider->card_states != nullptr));
  } else {
    assert(jit_provider == nullptr);
  }

  if constexpr (!std::is_void_v<Debugger>) {
    [[maybe_unused]] auto *debug = static_cast<debug_context *>(call.context);
    assert(debug != nullptr && debug->control != nullptr);
    assert(debug->control->breakpoint_count == 0 ||
           debug->control->breakpoints != nullptr);
  }
  auto &context = *static_cast<execution_context *>(call.context);
  native_dispatch_state<CollectStats, WithJit, WithExtension,
                        ReturnAtBlockBoundary, ResultType>
      state{call, extension, jit_provider, context, kNativeErrorInternal, 0,
            0,    {}};
  prepare_native_dispatch(state);
  // Boundary overrides are valid only within one Runtime dispatch.
  for (std::uint32_t index = 0; index < context.call_stack->size; ++index) {
    auto &frame = context.call_stack->frames[index];
    frame.boundary_next_pc = kNoPc;
    frame.boundary_loops_to = kNoPc;
  }

  const auto previous_runtime_flags = context.runtime_flags;
  if (context.ip == 0)
    context.runtime_flags |= kPendingBlockHeadFlag;
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
    auto block_index = kNoPc;
    const auto ip = context.ip;
    const auto block_head =
        (context.runtime_flags & kPendingBlockHeadFlag) != 0 &&
        ip < context.code_size;
    if (block_head)
      block_index = find_cached_block_index(state, *frame.function_view, ip);
    if constexpr (std::is_void_v<Debugger>) {
      if constexpr (WithJit) {
        if (block_head && opcode_is_block_body(context.code[ip])) {
          const auto pc = frame.function_view->code_pc_offset + ip;
          const auto card = pc >> state.jit_provider->card_shift;
          assert(card < state.jit_provider->card_count &&
                 state.jit_provider->card_states != nullptr);
          const auto card_state = (state.jit_provider->card_states[card >> 2] >>
                                   ((card & 3u) * 2u)) &
                                  3u;
          if (card_state == 3u)
            executed_bodies =
                execute_jit_trace<CollectStats, WithExtension,
                                  ReturnAtBlockBoundary, ResultType>(
                    state, frame, block_index, pc, dispatch_rel);
        }
      }
    }
    if (state.error_code != 0)
      break;
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
#if FB_CONF_JIT_BLOCK_COUNTERS
      if (executed_bodies == 0 &&
          (context.runtime_flags & kPendingBlockHeadFlag) != 0) {
        assert(block_index != kNoPc);
        fb_native_record_block_execution(frame.function_view->module_view,
                                         block_index);
      }
#endif
      [[maybe_unused]] bool observed_interpreter_body = false;
      if constexpr (WithExtension || WithJit) {
        observed_interpreter_body =
            executed_bodies == 0 &&
            (context.runtime_flags & kPendingBlockHeadFlag) != 0 &&
            interpreter_ip < context.code_size &&
            opcode_is_block_body(context.code[interpreter_ip]);
      }
      const std::uint8_t *rel = dispatch_rel;
      if (executed_bodies == 0) {
        if ((context.runtime_flags & kPendingBlockHeadFlag) != 0) {
          assert(block_index != kNoPc);
          context.rel = interpreter_rel_for_block_index(frame, block_index);
        }
        rel = context.rel;
      }
      // Every terminal handler writes a fresh CPS exit kind.
      dispatch_next<Debugger>(&context, context.code + context.ip,
                              context.stack_pointer, rel);
      const auto pc = frame.function_view->code_pc_offset + interpreter_ip;
      if constexpr (WithJit) {
        if (observed_interpreter_body &&
            state.jit_provider->observe_block(state.jit_provider->owner,
                                              block_index, pc) < 0) {
          state.error_code = kNativeErrorInternal;
          break;
        }
      }
      if constexpr (WithExtension) {
        if (observed_interpreter_body &&
            state.extension->observe(&state.input, pc)) {
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
    // False means this Runtime iteration stopped. Classify the reason from
    // state.error_code and state.status after leaving the dispatch loop.
    if (!handle_runtime_exit<CollectStats, WithJit, WithExtension,
                             ReturnAtBlockBoundary, Debugger, ResultType>(
            state, exit_kind))
      break;
  }

  context.runtime_flags = (previous_runtime_flags & ~kPendingBlockHeadFlag) |
                          (context.runtime_flags & kPendingBlockHeadFlag);
  if (state.error_code != 0) {
    result->error_code = state.error_code;
    return 0;
  }
  result->status = state.status;
  // Internal block boundaries stay in this loop; publish the stack only at the
  // Runtime handoff.
  context.stack_checkpoint = context.stack_pointer;
  context.trap_code = state.trap_code;
  if constexpr (CollectStats) {
    result->trace_count = state.metrics.stats.trace_count;
    result->body_count = state.metrics.stats.body_count;
    result->dispatcher_trace_transitions =
        state.metrics.stats.dispatcher_trace_transitions;
    result->control_handler_count = state.metrics.stats.control_handler_count;
    result->interpreted_block_count =
        state.metrics.stats.interpreted_block_count;
    result->block_search_cache_hits =
        state.metrics.stats.block_search_cache_hits;
    result->block_search_cache_misses =
        state.metrics.stats.block_search_cache_misses;
  }
  if constexpr (WithExtension &&
                !std::is_same_v<ResultType, fb_native_result>) {
    result->eligible_block_visits =
        state.metrics.observations.eligible_block_visits;
  }
  if constexpr (WithJit) {
    if (jit_provider->finish_boundary(jit_provider->owner, result->status,
                                      call.idle_budget) != 1) {
      result->error_code = kNativeErrorInternal;
      return 0;
    }
  }
  return 1;
}

inline int run_native_step_abi(const fb_native_step_call *call,
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
  execution_context->stack_base = stack;
  execution_context->stack_pointer = stack + call->stack_size;
  execution_context->stack_limit = stack + call->stack_capacity;
  execution_context->local_stack_base = locals;
  execution_context->local_stack_limit =
      locals + call->locals_bytes / sizeof(std::uint32_t);
  assert(control_stack->size <= FIREBALL_NATIVE_CONTROL_STACK_CAPACITY);
  auto *requested_control_base =
      static_cast<fireball_control_frame_native *>(call->control_frame_base);
  assert(requested_control_base >= control_stack->frames &&
         requested_control_base <= control_stack->frames + control_stack->size);
  assert(execution_context->call_stack != nullptr);
  assert(execution_context->call_stack->size != 0);
  assert(execution_context->call_stack->size <=
         FIREBALL_NATIVE_CALL_STACK_CAPACITY);
  assert(execution_context->call_frame_base != nullptr);
  assert(execution_context->call_frame_base >=
             execution_context->call_stack->frames &&
         execution_context->call_frame_base <
             execution_context->call_stack->frames +
                 execution_context->call_stack->size);

  execution_context->ip = call->ip;
  execution_context->code = raw_code;
  execution_context->code_size = static_cast<std::uint32_t>(call->code_bytes);
  execution_context->control_stack = control_stack;
  execution_context->control_frame_base = requested_control_base;

  execution_context->trap_code = 0;
  const auto &active = execution_context->call_stack
                           ->frames[execution_context->call_stack->size - 1];
  assert(active.function_view != nullptr);
  auto *local_base = active.local_base;
  auto *local_stack_pointer =
      active.local_base + active.function_view->local_slot_count;
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
    (void)dispatch_next(execution_context, raw_code + call->ip, sp, call->rel);
    execution_context->runtime_flags =
        previous_flags | (execution_context->runtime_flags & kCpsExitMask);
  } else {
    execution_context->runtime_flags &= ~kPendingBlockHeadFlag;
    (void)dispatch_next(execution_context, raw_code + call->ip, sp, call->rel);
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
    assert(false &&
           "single-step CPS returned at an invalid execution boundary");
    output->error_code = kNativeErrorInternal;
    clear_cps_exit(*execution_context);
    return 0;
  }
  execution_context->stack_checkpoint = execution_context->stack_pointer;
  clear_cps_exit(*execution_context);
  return 1;
}

} // namespace

#endif // FIREBALL_NATIVE_INTERPRETER_RUNTIME_HXX

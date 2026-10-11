#ifndef FIREBALL_PYSIM_NATIVE_ABI_HXX
#define FIREBALL_PYSIM_NATIVE_ABI_HXX

#include <cstdint>

#if defined(_WIN32)
#define FB_PYSIM_INTERPRETER_EXPORT __declspec(dllexport)
#else
#define FB_PYSIM_INTERPRETER_EXPORT __attribute__((visibility("default")))
#endif

extern "C" {

struct fb_native_dispatch_call;
struct fb_native_dispatch_extension_call;

// QA observation hooks are carried by a QA-only wrapper, not the production
// dispatch call.
struct fb_native_execution_extension {
  std::uintptr_t owner;
  bool (*observe)(const fb_native_dispatch_call *, std::uint32_t);
  void (*trace_executed)(const fb_native_dispatch_call *, std::uintptr_t);
};

// Metadata published through execution_context.rel after a JIT trace reaches
// an unlinked chain end. The Interpreter consumes it after a true return.
struct fb_native_jit_trace_exit {
  std::uint32_t body_end_ip;
  std::uint32_t next_pc;
  std::uint32_t loops_to;
  std::uint32_t interpreter_rel_bytes;
  std::uint32_t block_index;
  std::uint16_t frame_depth;
  std::uint8_t result_words;
  std::uint8_t reserved0;
};

static_assert(sizeof(fb_native_jit_trace_exit) == 24);

// Tier 2 native execution extension contract. The Interpreter owns invoking
// the returned trace entry; an extension returns only root-entry data.
struct fb_native_jit_trace {
  std::uintptr_t entry_address;
  const std::uint8_t *rel;
  std::uint32_t frame_depth;
  std::uint32_t stack_words;
  std::uint32_t body_count;
  std::uintptr_t trace_cookie;
};

struct fb_native_jit_provider {
  std::uintptr_t owner;
  // Borrowed two-bit card-state map. State 3 is COMPILED.
  const std::uint8_t *card_states;
  std::uint32_t card_count;
  std::uint32_t card_shift;
  // Return 1 for a resident root trace, 0 for a cache miss, and -1 on failure.
  // The Interpreter resolves the BasicBlock before checking its card and
  // calls lookup only when this PC's card state is COMPILED. The result does
  // not describe the chain endpoint. On normal completion, the terminal chain
  // dispatcher sets execution_context.ip and publishes exit metadata through
  // execution_context.rel before the trace entry returns true.
  int (*lookup)(std::uintptr_t owner, std::uint32_t block_index,
                std::uint32_t pc, fb_native_jit_trace *trace);
  // Record an interpreted basic-block visit using the resolved loader index.
  int (*observe_block)(std::uintptr_t owner, std::uint32_t block_index,
                       std::uint32_t pc);
  // Analyze profile state and process the bounded compile queue at a handoff.
  int (*finish_boundary)(std::uintptr_t owner, std::uint32_t status,
                         std::uint32_t budget);
};

struct fb_native_dispatch_call {
  void *context;
  std::uint32_t *stack;
  std::uint32_t *locals;
  const fb_native_jit_provider *jit_provider;
  std::uint32_t idle_budget;
};

struct fb_native_step_call {
  const std::uint8_t *code;
  std::uint64_t code_bytes;
  void *context;
  std::uint64_t context_bytes;
  std::uint32_t *stack;
  std::uint64_t stack_bytes;
  std::uint32_t *locals;
  std::uint64_t locals_bytes;
  void *control_stack;
  std::uint64_t control_bytes;
  std::uint32_t stack_size;
  std::uint32_t stack_capacity;
  std::uint32_t ip;
  std::uint32_t local_slots;
  void *control_frame_base;
  // Byte cursor passed to the native dispatch entry.
  const std::uint8_t *rel;
};

// A selected debugger owns this fixed record; normal contexts have no control
// slot.
struct fb_native_debug_control {
  const std::uint32_t *breakpoints;
  std::uint32_t breakpoint_count;
  std::uint32_t single_step;
  std::uint32_t executed;
  std::uint32_t stopped;
  std::uint32_t current_pc;
  std::uint32_t enabled;
};

struct fb_native_result {
  std::uint32_t status;
  std::uint32_t error_code;
};
static_assert(sizeof(fb_native_result) == 8);

FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_step(const fb_native_step_call *call, fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_control_step(const fb_native_step_call *call,
                           fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_dispatch(const fb_native_dispatch_call *call,
                       fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_dispatch_block(const fb_native_dispatch_call *call,
                             fb_native_result *result);

FB_PYSIM_INTERPRETER_EXPORT int
fb_native_run_debug_dispatch(const fb_native_dispatch_call *call,
                             fb_native_result *result);
}

inline constexpr std::uint32_t fb_native_cps_exit_mask = 0xFu << 8;

#endif

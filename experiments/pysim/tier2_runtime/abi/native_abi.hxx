#ifndef FIREBALL_PYSIM_NATIVE_ABI_HXX
#define FIREBALL_PYSIM_NATIVE_ABI_HXX

#include "integer_types.hxx"
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
  bool (*observe)(const fb_native_dispatch_call *, wasm_pc_t);
  void (*trace_executed)(const fb_native_dispatch_call *, std::uintptr_t);
};

// Metadata published through execution_context.rel after a JIT trace reaches
// an unlinked chain end. The Interpreter consumes it after a true return.
struct fb_native_jit_trace_exit {
  code_ip_t body_end_ip;
  wasm_pc_t next_pc;
  wasm_pc_t loops_to;
  annotation_offset_t interpreter_rel_bytes;
  // Loader-resolved block index for next_pc; invalid when no successor exists.
  block_index_t block_index;
  frame_depth_t frame_depth;
  operand_words_t result_words;
};

enum fb_native_jit_trace_exit_kind {
  FB_NATIVE_JIT_TRACE_NORMAL = 0,
  FB_NATIVE_JIT_TRACE_CALL_CPS = 1,
};

static_assert(sizeof(fb_native_jit_trace_exit) == 20);

// Tier 2 native execution extension contract. The Interpreter owns invoking
// the returned trace entry; an extension returns only root-entry data.
struct fb_native_jit_trace {
  std::uintptr_t entry_address;
  const std::uint8_t *rel;
  frame_depth_t frame_depth;
  jit_stack_words_t stack_words;
  jit_body_count_t body_count;
  std::uint8_t exit_kind;
  std::uintptr_t trace_cookie;
};

/** @brief Interpreterが借用するJIT接続。callbackの契約違反は呼出し直後でassertする。 */
struct fb_native_jit_provider {
  std::uintptr_t owner;
  // Borrowed two-bit card-state map. State 3 is COMPILED.
  const std::uint8_t *card_states;
  card_index_t card_count;
  std::uint8_t card_shift;
  std::uint8_t reserved0[3];
  // Return 1 for a resident root trace and 0 for a cache miss.
  // The Interpreter resolves the BasicBlock before checking its card and
  // calls lookup only when this PC's card state is COMPILED. The result does
  // not describe the chain endpoint. On normal completion, the terminal chain
  // dispatcher sets execution_context.ip and publishes exit metadata through
  // execution_context.rel before the trace entry returns true.
  int (*lookup)(std::uintptr_t owner, block_index_t block_index, wasm_pc_t pc,
                fb_native_jit_trace *trace);
  /** @brief 解決済みブロックを観測する。記録した場合は1、対象外は0を返す。 */
  int (*observe_block)(std::uintptr_t owner, block_index_t block_index, wasm_pc_t pc);
  /** @brief 境界の履歴と候補処理を実行し、完了を示す1を返す。 */
  int (*finish_boundary)(std::uintptr_t owner, std::uint32_t status);
};

struct fb_native_dispatch_call {
  void *context;
  std::uint32_t *stack;
  std::uint32_t *locals;
  const fb_native_jit_provider *jit_provider;
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
  std::uint32_t stack_size;
  std::uint32_t stack_capacity;
  code_ip_t ip;
  local_count_t local_slots;
  // Byte cursor passed to the native dispatch entry.
  const std::uint8_t *rel;
};

// A selected debugger owns this fixed record; normal contexts have no control
// slot.
struct fb_native_debug_control {
  const wasm_pc_t *breakpoints;
  std::uint32_t breakpoint_count;
  std::uint32_t single_step;
  std::uint32_t executed;
  std::uint32_t stopped;
  wasm_pc_t current_pc;
  std::uint32_t enabled;
};

struct fb_native_result {
  std::uint32_t status;
  std::uint32_t error_code;
};
static_assert(sizeof(fb_native_result) == 8);

FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_step(const fb_native_step_call *call,
                                                   fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_control_step(const fb_native_step_call *call,
                                                           fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_dispatch(const fb_native_dispatch_call *call,
                                                       fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_dispatch_block(const fb_native_dispatch_call *call,
                                                             fb_native_result *result);

FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_debug_dispatch(const fb_native_dispatch_call *call,
                                                             fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT const void *fb_native_jit_call_handlers();
}

inline constexpr std::uint32_t fb_native_cps_exit_mask = 0xFu << 8;

#endif

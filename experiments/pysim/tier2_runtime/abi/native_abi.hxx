#ifndef FIREBALL_PYSIM_NATIVE_ABI_HXX
#define FIREBALL_PYSIM_NATIVE_ABI_HXX

#include "integer_types.hxx"
#include <cstddef>
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
  // 1 when the Interpreter executes the opcode at body_end_ip after this trace (if, br, br_if,
  // call, call_indirect). Decided at compile time from that opcode.
  std::uint8_t interpreter_terminator_pending;
};

enum fb_native_jit_trace_exit_kind : std::uint8_t {
  FB_NATIVE_JIT_TRACE_NORMAL = 0,
  FB_NATIVE_JIT_TRACE_CALL_CPS = 1,
};

static_assert(sizeof(fb_native_jit_trace_exit) == 20);

// Header at the start of every resident trace blob, read by the Interpreter after a lookup hit.
// Its layout is fixed by the JIT compiler (jit_trace_header is an alias of this struct).
struct fb_native_jit_trace_header {
  std::uint64_t chain_target_address;
  std::int32_t common_code_relative;
  wasm_pc_t head_pc;
  code_offset_t blob_bytes;
  jit_stack_words_t stack_words;
  jit_stack_words_t chain_words;
  jit_body_count_t chain_body_count;
  frame_depth_t frame_depth;
  fb_native_jit_trace_exit_kind exit_kind;
  block_index_t block_index;
  code_offset_t const_rel_offset;
};
static_assert(offsetof(fb_native_jit_trace_header, common_code_relative) == 8);
static_assert(offsetof(fb_native_jit_trace_header, head_pc) == 12);
static_assert(offsetof(fb_native_jit_trace_header, blob_bytes) == 16);
static_assert(offsetof(fb_native_jit_trace_header, stack_words) == 20);
static_assert(offsetof(fb_native_jit_trace_header, chain_words) == 21);
static_assert(offsetof(fb_native_jit_trace_header, chain_body_count) == 22);
static_assert(offsetof(fb_native_jit_trace_header, frame_depth) == 23);
static_assert(offsetof(fb_native_jit_trace_header, exit_kind) == 24);
static_assert(offsetof(fb_native_jit_trace_header, block_index) == 26);
static_assert(offsetof(fb_native_jit_trace_header, const_rel_offset) == 28);
static_assert(sizeof(fb_native_jit_trace_header) == 32);

/** @brief Interpreterが借用するJIT接続。callbackの契約違反は呼出し直後でassertする。 */
struct fb_native_jit_provider {
  std::uintptr_t owner;
  // Borrowed two-bit card-state map. State 3 is COMPILED.
  const std::uint8_t *card_states;
  card_index_t card_count;
  std::uint8_t card_shift;
  std::uint8_t reserved0[3];
  // Returns the header of a resident root trace, or nullptr on a cache miss.
  // The Interpreter resolves the BasicBlock before checking its card and
  // calls lookup only when this PC's card state is COMPILED. The result does
  // not describe the chain endpoint. On normal completion, the terminal chain
  // dispatcher sets execution_context.ip and publishes exit metadata through
  // execution_context.rel before the trace entry returns true.
  const fb_native_jit_trace_header *(*lookup)(std::uintptr_t owner, block_index_t block_index,
                                              wasm_pc_t pc);
  /** @brief 解決済みブロックを観測する。記録した場合は1、対象外は0を返す。 */
  int (*observe_block)(std::uintptr_t owner, block_index_t block_index, wasm_pc_t pc);
  /** @brief 境界の履歴と候補処理を実行し、完了を示す1を返す。 */
  int (*finish_boundary)(std::uintptr_t owner, std::uint32_t status);
  // The common trace entry is shared by every trace; the Interpreter calls it for each root entry.
  std::uintptr_t trace_entry_address;
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
FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_dispatch(const fb_native_dispatch_call *call,
                                                       fb_native_result *result);

FB_PYSIM_INTERPRETER_EXPORT int fb_native_run_debug_dispatch(const fb_native_dispatch_call *call,
                                                             fb_native_result *result);
FB_PYSIM_INTERPRETER_EXPORT const void *fb_native_jit_call_handlers();
}

inline constexpr std::uint32_t fb_native_cps_exit_mask = 0xFu << 8;

#endif

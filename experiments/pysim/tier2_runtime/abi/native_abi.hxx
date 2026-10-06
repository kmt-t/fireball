#ifndef FIREBALL_PYSIM_NATIVE_ABI_HXX
#define FIREBALL_PYSIM_NATIVE_ABI_HXX

#include <cstdint>

#if defined(_WIN32)
#define FB_PYSIM_INTERPRETER_EXPORT __declspec(dllexport)
#else
#define FB_PYSIM_INTERPRETER_EXPORT __attribute__((visibility("default")))
#endif

extern "C" {

// An execution extension owns its implementation and updates the shared
// context.
struct fb_native_dispatch_call;

struct fb_native_execution_extension {
  std::uintptr_t owner;
  std::uint32_t (*execute)(const fb_native_dispatch_call *, std::uint32_t);
  bool (*observe)(const fb_native_dispatch_call *, std::uint32_t);
};

struct fb_native_dispatch_call {
  void *context;
  std::uint32_t *stack;
  std::uint32_t *locals;
  const fb_native_execution_extension *extension;
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
  std::uint32_t control_base;
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
fb_native_run_debug_dispatch(const fb_native_dispatch_call *call,
                             fb_native_result *result);
}

#endif

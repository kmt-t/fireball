#pragma once

#include <concepts>
#include <cstdint>
#include <utility>

#ifndef FB_CONF_JIT_ENABLED
#error "FB_CONF_JIT_ENABLED must be defined by the build configuration"
#endif

namespace fireball::runtime {

#if FB_CONF_JIT_ENABLED == 1

struct jit_lookup_result {
  std::uintptr_t native_entry;
  bool found;
};

template <typename LookupPolicy>
concept jit_lookup_policy = requires(LookupPolicy& lookup, std::uint32_t pc) {
  { lookup.lookup(pc) } -> std::same_as<jit_lookup_result>;
};

template <typename JitRuntime, typename Interpreter, typename LookupPolicy, typename... Args>
concept jit_runtime_executor = requires(JitRuntime& runtime, Interpreter& interpreter,
                                        LookupPolicy& lookup, Args&&... args) {
  runtime.call(interpreter, lookup, std::forward<Args>(args)...);
};

template <typename Interpreter, typename JitRuntime, jit_lookup_policy LookupPolicy>
class runtime_composer {
 public:
  runtime_composer(Interpreter& interpreter, JitRuntime& jit_runtime, LookupPolicy& lookup) noexcept
      : interpreter_(interpreter), jit_runtime_(jit_runtime), lookup_(lookup) {}

  template <typename... Args>
    requires jit_runtime_executor<JitRuntime, Interpreter, LookupPolicy, Args...>
  decltype(auto) call(Args&&... args) {
    return jit_runtime_.call(interpreter_, lookup_, std::forward<Args>(args)...);
  }

 private:
  Interpreter& interpreter_;
  JitRuntime& jit_runtime_;
  LookupPolicy& lookup_;
};

#elif FB_CONF_JIT_ENABLED == 0

template <typename Interpreter>
class runtime_composer {
 public:
  explicit runtime_composer(Interpreter& interpreter) noexcept : interpreter_(interpreter) {}

  template <typename... Args>
    requires requires(Interpreter& interpreter, Args&&... args) {
      interpreter.call(std::forward<Args>(args)...);
    }
  decltype(auto) call(Args&&... args) {
    return interpreter_.call(std::forward<Args>(args)...);
  }

 private:
  Interpreter& interpreter_;
};

#else
#error "FB_CONF_JIT_ENABLED must be defined as 0 or 1"
#endif

}  // namespace fireball::runtime

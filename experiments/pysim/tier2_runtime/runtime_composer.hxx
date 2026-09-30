#pragma once

#include <concepts>
#include <cstdint>
#include <utility>

namespace fireball::runtime {

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

struct interpreter_slot {};
struct jit_runtime_slot {};
struct lookup_policy_slot {};
struct debugger_slot {};
struct profiler_slot {};

template <typename Component, typename Slot>
class component_reference {
 public:
  explicit component_reference(Component& component) noexcept : component_(&component) {}

  Component& get() const noexcept { return *component_; }

 private:
  Component* component_;
};

template <typename Slot>
class component_reference<void, Slot> {};

template <typename Interpreter, typename JitRuntime = void, typename LookupPolicy = void,
          typename Debugger = void, typename Profiler = void>
struct runtime_configuration {};

template <typename Configuration>
struct runtime_harness;

template <typename Interpreter, typename JitRuntime, typename LookupPolicy, typename Debugger,
          typename Profiler>
struct runtime_harness<
    runtime_configuration<Interpreter, JitRuntime, LookupPolicy, Debugger, Profiler>> {
  [[no_unique_address]] component_reference<Interpreter, interpreter_slot> interpreter;
  [[no_unique_address]] component_reference<JitRuntime, jit_runtime_slot> jit_runtime;
  [[no_unique_address]] component_reference<LookupPolicy, lookup_policy_slot> lookup_policy;
  [[no_unique_address]] component_reference<Debugger, debugger_slot> debugger;
  [[no_unique_address]] component_reference<Profiler, profiler_slot> profiler;
};

template <typename Configuration>
class runtime_composer;

template <typename Interpreter, typename Debugger, typename Profiler>
class runtime_composer<runtime_configuration<Interpreter, void, void, Debugger, Profiler>> {
 public:
  using harness_type = runtime_harness<
      runtime_configuration<Interpreter, void, void, Debugger, Profiler>>;

  explicit runtime_composer(harness_type harness) noexcept : harness_(harness) {}

  harness_type& harness() noexcept { return harness_; }
  const harness_type& harness() const noexcept { return harness_; }

  template <typename... Args>
    requires requires(Interpreter& interpreter, Args&&... args) {
      interpreter.call(std::forward<Args>(args)...);
    }
  decltype(auto) call(Args&&... args) {
    return harness_.interpreter.get().call(std::forward<Args>(args)...);
  }

 private:
  harness_type harness_;
};

template <typename Interpreter, typename JitRuntime, typename LookupPolicy, typename Profiler>
  requires jit_lookup_policy<LookupPolicy>
class runtime_composer<
    runtime_configuration<Interpreter, JitRuntime, LookupPolicy, void, Profiler>> {
 public:
  using harness_type = runtime_harness<
      runtime_configuration<Interpreter, JitRuntime, LookupPolicy, void, Profiler>>;

  explicit runtime_composer(harness_type harness) noexcept : harness_(harness) {}

  harness_type& harness() noexcept { return harness_; }
  const harness_type& harness() const noexcept { return harness_; }

  template <typename... Args>
  requires jit_runtime_executor<JitRuntime, Interpreter, LookupPolicy, Args...>
  decltype(auto) call(Args&&... args) {
    return harness_.jit_runtime.get().call(harness_.interpreter.get(),
                                           harness_.lookup_policy.get(),
                                           std::forward<Args>(args)...);
  }

 private:
  harness_type harness_;
};

}  // namespace fireball::runtime

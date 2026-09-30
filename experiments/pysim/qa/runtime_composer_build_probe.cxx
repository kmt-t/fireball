#include "../tier2_runtime/runtime_composer.hxx"

#include <cstdint>
#include <type_traits>

struct interpreter_stub {
  std::uint32_t call(std::uint32_t value) noexcept { return value; }
};

struct lookup_stub {
  fireball::runtime::jit_lookup_result lookup(std::uint32_t pc) noexcept {
    return {static_cast<std::uintptr_t>(pc), pc != 0};
  }
};

struct jit_runtime_stub {
  std::uint32_t call(interpreter_stub& interpreter, lookup_stub& lookup,
                     std::uint32_t value) noexcept {
    const auto trace = lookup.lookup(value);
    return trace.found ? interpreter.call(value) + 1u : 0u;
  }
};

struct debugger_stub {};

struct profiler_stub {};

using jit_configuration =
    fireball::runtime::runtime_configuration<interpreter_stub, jit_runtime_stub, lookup_stub>;
using interpreter_configuration = fireball::runtime::runtime_configuration<interpreter_stub>;
using instrumented_interpreter_configuration = fireball::runtime::runtime_configuration<
    interpreter_stub, void, void, debugger_stub, profiler_stub>;
using jit_runtime = fireball::runtime::runtime_composer<jit_configuration>;
using interpreter_runtime = fireball::runtime::runtime_composer<interpreter_configuration>;
using instrumented_interpreter_runtime =
    fireball::runtime::runtime_composer<instrumented_interpreter_configuration>;
using interpreter_harness = fireball::runtime::runtime_harness<interpreter_configuration>;
using instrumented_interpreter_harness =
    fireball::runtime::runtime_harness<instrumented_interpreter_configuration>;
static_assert(fireball::runtime::jit_lookup_policy<lookup_stub>);
static_assert(sizeof(jit_runtime) >= 3 * sizeof(void*));
static_assert(sizeof(interpreter_runtime) == sizeof(interpreter_stub*));
static_assert(sizeof(instrumented_interpreter_runtime) >= 3 * sizeof(void*));
static_assert(sizeof(interpreter_harness) == sizeof(interpreter_stub*));

int main() {
  interpreter_stub interpreter;
  jit_runtime_stub jit;
  lookup_stub lookup;
  debugger_stub debugger;
  profiler_stub profiler;
  fireball::runtime::runtime_harness<jit_configuration> jit_harness{
      fireball::runtime::component_reference<interpreter_stub,
                                             fireball::runtime::interpreter_slot>{interpreter},
      fireball::runtime::component_reference<jit_runtime_stub,
                                             fireball::runtime::jit_runtime_slot>{jit},
      fireball::runtime::component_reference<lookup_stub,
                                             fireball::runtime::lookup_policy_slot>{lookup},
      {},
      {}};
  interpreter_harness interpreter_harness_instance{
      fireball::runtime::component_reference<interpreter_stub,
                                             fireball::runtime::interpreter_slot>{interpreter},
      {},
      {},
      {},
      {}};
  instrumented_interpreter_harness instrumented_harness{
      fireball::runtime::component_reference<interpreter_stub,
                                             fireball::runtime::interpreter_slot>{interpreter},
      {},
      {},
      fireball::runtime::component_reference<debugger_stub,
                                             fireball::runtime::debugger_slot>{debugger},
      fireball::runtime::component_reference<profiler_stub,
                                             fireball::runtime::profiler_slot>{profiler}};
  jit_runtime jit_instance(jit_harness);
  interpreter_runtime interpreter_instance(interpreter_harness_instance);
  instrumented_interpreter_runtime instrumented_instance(instrumented_harness);
  return jit_instance.call(1) == 2u && jit_instance.call(0) == 0u &&
                 interpreter_instance.call(0) == 0u &&
                 instrumented_instance.call(0) == 0u &&
                 &instrumented_instance.harness().debugger.get() == &debugger &&
                 &instrumented_instance.harness().profiler.get() == &profiler
             ? 0
             : 1;
}

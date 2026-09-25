#include "../tier2_runtime/runtime_composer.hxx"

#include <cstdint>
#include <type_traits>

struct interpreter_stub {
  std::uint32_t call(std::uint32_t value) noexcept { return value; }
};

#if FB_CONF_JIT_ENABLED == 1

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

using jit_runtime =
    fireball::runtime::runtime_composer<interpreter_stub, jit_runtime_stub, lookup_stub>;
static_assert(fireball::runtime::jit_lookup_policy<lookup_stub>);
static_assert(sizeof(jit_runtime) >= 3 * sizeof(void*));

#else

using interpreter_runtime = fireball::runtime::runtime_composer<interpreter_stub>;
static_assert(sizeof(interpreter_runtime) == sizeof(interpreter_stub*));

#endif

int main() {
  interpreter_stub interpreter;
#if FB_CONF_JIT_ENABLED == 1
  jit_runtime_stub jit;
  lookup_stub lookup;
  jit_runtime runtime(interpreter, jit, lookup);
  return runtime.call(1) == 2u && runtime.call(0) == 0u ? 0 : 1;
#else
  interpreter_runtime runtime(interpreter);
  return runtime.call(0) == 0u ? 0 : 1;
#endif
}

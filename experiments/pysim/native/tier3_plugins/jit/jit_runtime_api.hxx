#ifndef FIREBALL_PYSIM_JIT_RUNTIME_API_HXX
#define FIREBALL_PYSIM_JIT_RUNTIME_API_HXX

#include <cstdint>

#include "../../../tier2_runtime/abi/interpreter_abi.hxx"

inline constexpr std::uint32_t kJitDirectLocalCount = 128;

enum class jit_local_runtime_api_index : std::uint32_t {
  get,
  set,
  tee,
  count,
};

using jit_local_runtime_api = void (*)(fireball_execution_context_native *,
                                       std::uint32_t *, std::uint32_t *,
                                       std::uint32_t) noexcept;

extern "C" void
fb_jit_runtime_local_get(fireball_execution_context_native *context,
                         std::uint32_t *local_base, std::uint32_t *stack_words,
                         std::uint32_t local_index) noexcept;
extern "C" void
fb_jit_runtime_local_set(fireball_execution_context_native *context,
                         std::uint32_t *local_base, std::uint32_t *stack_words,
                         std::uint32_t local_index) noexcept;
extern "C" void
fb_jit_runtime_local_tee(fireball_execution_context_native *context,
                         std::uint32_t *local_base, std::uint32_t *stack_words,
                         std::uint32_t local_index) noexcept;

#endif

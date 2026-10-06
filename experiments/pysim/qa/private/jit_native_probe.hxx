#ifndef FIREBALL_PYSIM_QA_JIT_NATIVE_PROBE_HXX
#define FIREBALL_PYSIM_QA_JIT_NATIVE_PROBE_HXX

#include <cstdint>

#include "jit_types.hxx"

namespace fireball {
class JitRuntime;
}

extern "C" {
FB_JIT_QA_EXPORT int
fb_jit_runtime_bind_cards(fireball::JitRuntime *cache, std::uint8_t *states,
                          std::uint32_t state_bytes, std::uint8_t *dirty,
                          std::uint32_t dirty_bytes, std::uint32_t cards,
                          std::uint32_t shift, std::uint32_t *cursor,
                          std::uint32_t units, std::uint32_t scan_bytes);
FB_JIT_QA_EXPORT std::int64_t
fb_jit_card_index(std::uint64_t bytes, std::uint32_t shift, std::uint32_t pc);
FB_JIT_QA_EXPORT std::int64_t fb_jit_card_count(std::uint64_t bytes,
                                                std::uint32_t shift);
}

#endif

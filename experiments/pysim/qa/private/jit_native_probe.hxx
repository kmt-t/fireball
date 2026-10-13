#ifndef FIREBALL_PYSIM_QA_JIT_NATIVE_PROBE_HXX
#define FIREBALL_PYSIM_QA_JIT_NATIVE_PROBE_HXX

#include <cstdint>

#include "../../native/tier3_plugins/jit/jit_runtime.hxx"
#include "jit_measurements.hxx"

namespace fireball {
using qa_jit_runtime = jit_runtime<jit_measurements>;
}

extern "C" {
int fb_jit_runtime_bind_cards(fireball::qa_jit_runtime *cache, std::uint8_t *states,
                              std::uint32_t state_bytes, std::uint8_t *dirty,
                              std::uint32_t dirty_bytes, std::uint32_t cards, std::uint32_t shift,
                              std::uint32_t *cursor, std::uint32_t units, std::uint32_t scan_bytes);
std::int64_t fb_jit_card_index(std::uint64_t bytes, std::uint32_t shift, std::uint32_t pc);
std::int64_t fb_jit_card_count(std::uint64_t bytes, std::uint32_t shift);
}

#endif

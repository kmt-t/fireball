#ifndef FIREBALL_PYSIM_JIT_RUNTIME_ABI_HXX
#define FIREBALL_PYSIM_JIT_RUNTIME_ABI_HXX

#include <cstddef>
#include <cstdint>

#include "../../../tier2_runtime/abi/native_abi.hxx"
#include "executable_memory_abi.hxx"
#include "jit_types.hxx"
#include "trace_compiler_abi.hxx"

namespace fireball {
class JitRuntime;

enum class cache_field : std::uint32_t {
  active_bank = 0,
  warm_bank = 1,
  oldest_bank = 2,
  promotions = 3,
  evictions = 4,
  generation = 5,
  aging_steps = 6,
  aging_units = 7,
  aging_scanned = 8,
  bank_used = 9,
  bank_entry_capacity = 11,
  execution_count = 15,
  history_overwritten = 16,
  history_approximate = 17,
  trackable_generation = 18,
  queue_count = 19,
  resident_count = 20,
  rotations = 21,
  aging_ns = 22,
  compile_attempts = 23,
  compile_ns = 24,
};

// External descriptor lifetimes and compile/execution measurements only.
// Cache management and its notifications remain inside JitRuntime.
struct jit_cache_host {
  int (*release_external)(std::uint64_t token);
  int (*retired)(std::uint32_t pc, std::uint32_t count);
  int (*compiled)(std::uint32_t pc, int status, std::uint64_t elapsed_ns);
};
}  // namespace fireball

extern "C" {
FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_size();
FB_PYSIM_ABI_EXPORT std::size_t fb_jit_runtime_alignment();
FB_PYSIM_ABI_EXPORT fireball::JitRuntime* fb_jit_runtime_init(
    std::uint8_t* storage, std::size_t bytes, std::uint32_t bank_bytes,
    std::uint32_t entry_capacity, const std::uint32_t* bank_offsets, fireball::jit_cache_host host);
FB_PYSIM_ABI_EXPORT fireball::executable_memory* fb_jit_runtime_memory(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT fireball::jit_cache_trace* fb_jit_runtime_trace(fireball::JitRuntime* cache,
                                                                    std::uint64_t token);
FB_PYSIM_ABI_EXPORT void fb_jit_runtime_close(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_error(const fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_scalar(const fireball::JitRuntime* cache,
                                                        std::uint32_t field, std::uint32_t bank);
FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_find(fireball::JitRuntime* cache,
                                                      std::uint32_t pc);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_bank(fireball::JitRuntime* cache, std::uint32_t pc);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_has_token(const fireball::JitRuntime* cache,
                                                 std::uint64_t token);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_insert(fireball::JitRuntime* cache,
                                              fireball::jit_cache_trace* trace,
                                              std::uint64_t token);
FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_lookup(fireball::JitRuntime* cache,
                                                        std::uint32_t pc);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_rotate(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_flush(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_bind_cards(fireball::JitRuntime* cache, std::uint8_t* states,
                                                  std::uint32_t state_bytes, std::uint8_t* dirty,
                                                  std::uint32_t dirty_bytes, std::uint32_t cards,
                                                  std::uint32_t shift, std::uint32_t* cursor,
                                                  std::uint32_t units, std::uint32_t scan_bytes);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_age(fireball::JitRuntime* cache);
FB_PYSIM_ABI_EXPORT void fb_jit_runtime_auto_age(fireball::JitRuntime* cache, int enabled);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_snapshot(fireball::JitRuntime* cache,
                                                fb_native_trace_descriptor* output,
                                                std::uint32_t capacity);
FB_PYSIM_ABI_EXPORT void fb_jit_runtime_reset_counts(fireball::JitRuntime* cache);
// Profiling storage belongs to the caller; algorithms and decisions belong to C++.
FB_PYSIM_ABI_EXPORT int fb_jit_region_size(const std::uint32_t* lengths, const std::uint32_t* bases,
                                           std::uint32_t count, std::uint64_t* bytes);
FB_PYSIM_ABI_EXPORT std::int64_t fb_jit_card_index(std::uint64_t bytes, std::uint32_t shift,
                                                   std::uint32_t pc);
FB_PYSIM_ABI_EXPORT std::int64_t fb_jit_card_count(std::uint64_t bytes, std::uint32_t shift);
// operations: get, set, touch, decay, clear, read dirty byte, set cursor.
FB_PYSIM_ABI_EXPORT int fb_jit_bits(std::uint8_t* data, std::uint32_t count, std::uint32_t bits,
                                    std::uint32_t index, std::uint32_t operation,
                                    std::uint32_t value);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_bind_profile(fireball::JitRuntime* cache,
                                                    fireball::jit_profile profile);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_suppress(fireball::JitRuntime* cache, std::uint32_t pc);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_record(fireball::JitRuntime* cache, std::uint32_t pc);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_visits(fireball::JitRuntime* cache, const std::uint32_t* pcs,
                                              std::uint32_t capacity, std::uint64_t total);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_analyze(fireball::JitRuntime* cache, int yielded);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_compile(fireball::JitRuntime* cache, std::uint32_t budget);
FB_PYSIM_ABI_EXPORT std::uint64_t fb_jit_runtime_filtered_lookup(fireball::JitRuntime* cache,
                                                                 std::uint32_t pc);
FB_PYSIM_ABI_EXPORT int fb_jit_runtime_finish(fireball::JitRuntime* cache, int yielded,
                                              std::uint32_t budget);
}
#endif

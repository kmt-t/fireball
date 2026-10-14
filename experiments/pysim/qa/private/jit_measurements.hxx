#pragma once
/** @file @brief QAビルドで明示的に選択するJIT計測ポリシー。 */
#define FB_JIT_RUNTIME_MEASUREMENT fireball::jit_measurements

#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <limits>

#include "../../native/tier1_core/printk/printk.hxx"
#include "../../native/tier3_plugins/jit/jit_chain_diagnostics.hxx"
#include "../../native/tier3_plugins/jit/jit_types.hxx"

namespace fireball {
struct jit_measurements {
  static constexpr std::uint32_t no_pc = std::numeric_limits<std::uint32_t>::max();
  static constexpr std::size_t trace_capacity = 3u * FB_CONF_JIT_CACHE_BANK_ENTRY_CAPACITY;

  bool auto_age = true;
  bool owns_memory = true;
  struct profile_type : jit_profile {
    std::uint32_t compiler_enabled = 1;
    const jit_wasm_block *blocks = nullptr;
    std::uint32_t block_count = 0;
  };
  std::array<std::uint32_t, trace_capacity> compilation_order{};
  std::uint32_t compilation_count = 0;
  std::uint64_t evictions = 0, generation = 0, rotations = 0;
  std::uint64_t aging_steps = 0, aging_units = 0, aging_scanned = 0, aging_ns = 0;
  std::uint64_t compile_attempts = 0, compile_ns = 0;
  std::uint64_t lookup_calls = 0;
  std::uint64_t execution_count = 0, history_overwritten = 0, last_overwritten = 0;
  std::uint64_t history_dropped = 0;
  std::uint64_t trackable_generation = 0;
  bool history_approximate = false;
  std::array<std::uint64_t, static_cast<std::size_t>(jit_chain_reason::count)>
      chain_construction_reasons{};
  std::array<std::uint64_t, static_cast<std::size_t>(jit_chain_reason::count)>
      chain_runtime_reasons{};
  std::uint64_t chain_construction_attempts = 0;
  std::uint64_t chain_runtime_transitions = 0;
  std::uint64_t chain_runtime_unclassified = 0;
  std::uint64_t chain_runtime_unobserved_trace_exits = 0;
  block_index_t pending_runtime_source_block = std::numeric_limits<block_index_t>::max();
  jit_stack_words_t pending_runtime_result_words = 0;
  frame_depth_t pending_runtime_frame_depth = 0;

  static const jit_wasm_block *fixture_block(const profile_type &profile, std::uint32_t pc) {
    if (profile.block_count == 0) {
      return nullptr;
    }
    const auto *end = profile.blocks + profile.block_count;
    const auto *found = std::lower_bound(
        profile.blocks, end, pc,
        [](const jit_wasm_block &block, std::uint32_t value) { return block.head_pc < value; });
    return found != end && found->head_pc == pc ? found : nullptr;
  }

  bool write_measurements(const printk_writer &writer) const {
    return printk_u64(writer, "jit.evictions", evictions) &&
           printk_u64(writer, "jit.generation", generation) &&
           printk_u64(writer, "jit.rotations", rotations) &&
           printk_u64(writer, "jit.aging_steps", aging_steps) &&
           printk_u64(writer, "jit.aging_units", aging_units) &&
           printk_u64(writer, "jit.aging_scanned", aging_scanned) &&
           printk_u64(writer, "jit.aging_ns", aging_ns) &&
           printk_u64(writer, "jit.compile_attempts", compile_attempts) &&
           printk_u64(writer, "jit.compile_ns", compile_ns) &&
           printk_u64(writer, "jit.execution_count", execution_count) &&
           printk_u64(writer, "jit.history_overwritten", history_overwritten) &&
           printk_u64(writer, "jit.last_overwritten", last_overwritten) &&
           printk_u64(writer, "jit.trackable_generation", trackable_generation) &&
           write_chain_measurements(writer);
  }

  bool write_chain_measurements(const printk_writer &writer) const {
    if (!printk_u64(writer, "jit.chain_construction_attempts", chain_construction_attempts) ||
        !printk_u64(writer, "jit.chain_runtime_transitions", chain_runtime_transitions) ||
        !printk_u64(writer, "jit.chain_runtime_unclassified", chain_runtime_unclassified) ||
        !printk_u64(writer, "jit.chain_runtime_unobserved_trace_exits",
                    chain_runtime_unobserved_trace_exits)) {
      return false;
    }
    for (std::size_t i = 0; i < jit_chain_reason_names.size(); ++i) {
      const auto write_reason = [&writer, &i](const char *prefix, std::uint64_t value) {
        std::array<char, 64> label{};
        std::size_t offset = 0;
        while (prefix[offset] != '\0') {
          assert(offset + 1 < label.size());
          label[offset] = prefix[offset];
          ++offset;
        }
        for (const auto character : jit_chain_reason_names[i]) {
          assert(offset + 1 < label.size());
          label[offset] = character;
          ++offset;
        }
        label[offset] = '\0';
        return printk_u64(writer, label.data(), value);
      };
      if (!write_reason("jit.chain_construction.", chain_construction_reasons[i]) ||
          !write_reason("jit.chain_runtime.", chain_runtime_reasons[i])) {
        return false;
      }
    }
    return true;
  }

  void record_chain_construction(jit_chain_reason reason) {
    assert(reason < jit_chain_reason::count);
    ++chain_construction_attempts;
    ++chain_construction_reasons[static_cast<std::size_t>(reason)];
  }
  void record_chain_runtime(jit_chain_reason reason) {
    assert(reason < jit_chain_reason::count);
    ++chain_runtime_reasons[static_cast<std::size_t>(reason)];
  }
  void reset_chain_runtime() {
    chain_runtime_reasons.fill(0);
    chain_runtime_transitions = 0;
    chain_runtime_unclassified = 0;
    chain_runtime_unobserved_trace_exits = 0;
    pending_runtime_source_block = std::numeric_limits<block_index_t>::max();
    pending_runtime_result_words = 0;
    pending_runtime_frame_depth = 0;
  }

  struct compile_scope {
    jit_measurements &state;
    std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();
    std::uint64_t age_before;
    explicit compile_scope(jit_measurements &owner) : state(owner), age_before(owner.aging_ns) {}
    ~compile_scope() {
      ++state.compile_attempts;
      state.compile_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
                              std::chrono::steady_clock::now() - started)
                              .count() -
                          (state.aging_ns - age_before);
    }
  };
  struct aging_scope {
    jit_measurements &state;
    std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();
    explicit aging_scope(jit_measurements &owner) : state(owner) {}
    ~aging_scope() {
      state.aging_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now() - started)
                            .count();
    }
  };

  void trace_inserted(std::uint32_t pc) {
    if (compilation_count < compilation_order.size()) {
      compilation_order[compilation_count++] = pc;
    }
  }
  void lookup_called() { ++lookup_calls; }
  void trace_evicted(std::uint32_t) { ++evictions; }
  void mask_changed() { ++trackable_generation; }
  void cache_changed() { ++generation; }
  void aging_started() { ++aging_steps; }
  void aging_processed(std::uint32_t units, std::uint32_t scanned) {
    aging_units += units;
    aging_scanned += scanned;
  }
  void cache_rotated() { ++rotations; }
  void visits_recorded(std::uint64_t total) { execution_count += total; }
  void history_record_overwritten() {
    assert(history_dropped != std::numeric_limits<std::uint64_t>::max());
    ++history_dropped;
  }
  void history_analyzed() {
    history_overwritten = history_dropped;
    history_approximate = history_dropped > last_overwritten;
    last_overwritten = history_dropped;
  }
  void boundary_analyzed(bool yielded) {
    if (yielded) {
      execution_count = 0;
    }
  }
  void profile_bound() {
    execution_count = history_overwritten = last_overwritten = history_dropped = 0;
    history_approximate = false;
    compilation_order.fill(no_pc);
    compilation_count = 0;
  }
  template <class Runtime> void reset_trace_counts(Runtime &runtime) {
    const auto count = runtime.module_view == nullptr ? runtime.hotspots.configuration.block_count
                                                      : runtime.module_view->block_count;
    for (std::uint32_t i = 0; i < count; ++i) {
      void *extension = runtime.module_view == nullptr
                            ? runtime.hotspots.configuration.blocks[i].extension_data
                            : (runtime.module_view->block_execution_counts == nullptr
                                   ? nullptr
                                   : runtime.module_view->block_execution_counts + i);
      if (extension != nullptr) {
        *static_cast<std::uint32_t *>(extension) = 0;
      }
    }
  }
};
} // namespace fireball

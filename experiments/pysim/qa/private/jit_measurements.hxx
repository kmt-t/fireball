#pragma once
#include <chrono>
#include <limits>

#include "../../native/tier3_plugins/jit/jit_types.hxx"
#include "../../native/tier1_core/printk/printk.hxx"

namespace fireball {
struct jit_measurements {
  bool auto_age = true;
  bool owns_memory = true;
  struct trace_type : jit_cache_trace {
    std::uint32_t* exec_count = nullptr;
  };
  struct owned_trace {
    trace_type trace{};
    std::uint32_t executions = 0;
    bool used = false;
  };
  std::uint64_t promotions = 0, evictions = 0, generation = 0, rotations = 0;
  std::uint64_t aging_steps = 0, aging_units = 0, aging_scanned = 0, aging_ns = 0;
  std::uint64_t compile_attempts = 0, compile_ns = 0;
  std::uint64_t execution_count = 0, history_overwritten = 0, last_overwritten = 0;
  std::uint64_t history_dropped = 0;
  std::uint64_t trackable_generation = 0;
  bool history_approximate = false;

  bool write_measurements(const printk_writer& writer) const {
    return printk_u64(writer, "jit.promotions", promotions) &&
           printk_u64(writer, "jit.evictions", evictions) &&
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
           printk_u64(writer, "jit.trackable_generation", trackable_generation);
  }

  struct compile_scope {
    jit_measurements& state;
    std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();
    std::uint64_t age_before;
    explicit compile_scope(jit_measurements& owner) : state(owner), age_before(owner.aging_ns) {}
    ~compile_scope() {
      ++state.compile_attempts;
      state.compile_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now() - started).count() - (state.aging_ns - age_before);
    }
  };
  struct aging_scope {
    jit_measurements& state;
    std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();
    explicit aging_scope(jit_measurements& owner) : state(owner) {}
    ~aging_scope() {
      state.aging_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now() - started).count();
    }
  };
  static std::uint32_t* counter(const jit_cache_trace* trace) {
    return static_cast<const trace_type*>(trace)->exec_count;
  }
  void trace_created(owned_trace& slot) { slot.trace.exec_count = &slot.executions; }
  void mask_changed() { ++trackable_generation; }
  void cache_changed() { ++generation; }
  void aging_started() { ++aging_steps; }
  void aging_processed(std::uint32_t units, std::uint32_t scanned) {
    aging_units += units;
    aging_scanned += scanned;
  }
  void trace_evicted() { ++evictions; }
  void cache_rotated() { ++rotations; }
  void trace_promoted() { ++promotions; }
  void visits_recorded(std::uint64_t total) { execution_count += total; }
  bool history_record_overwritten() {
    if (history_dropped == std::numeric_limits<std::uint64_t>::max()) return false;
    ++history_dropped;
    return true;
  }
  void history_analyzed() {
    history_overwritten = history_dropped;
    history_approximate = history_dropped > last_overwritten;
    last_overwritten = history_dropped;
  }
  void boundary_analyzed(bool yielded) { if (yielded) execution_count = 0; }
  void profile_bound() {
    execution_count = history_overwritten = last_overwritten = history_dropped = 0;
    history_approximate = false;
  }
  template <class Runtime>
  void trace_executed(Runtime& runtime, jit_cache_trace& start) {
    auto* trace = &start;
    while (trace != nullptr) {
      auto* count = counter(trace);
      if (*count != std::numeric_limits<std::uint32_t>::max()) ++*count;
      trace = trace->chain_next_pc == std::numeric_limits<std::uint32_t>::max()
          ? nullptr : runtime.find(trace->chain_next_pc)->trace;
    }
  }
  template <class Runtime>
  void reset_trace_counts(Runtime& runtime) {
    for (auto& bank : runtime.banks)
      for (std::uint32_t i = 0; i < bank.count; ++i)
        if (auto* trace = bank.entries[i].trace; trace != nullptr && counter(trace) != nullptr)
          *counter(trace) = 0;
  }
};
}  // namespace fireball

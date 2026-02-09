/**
 * vSoC (Virtual System-on-Chip) Manager.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include "../fireball.hxx"
#include "harness.hxx"

namespace fireball::vsoc {

/**
 * @brief vSoC Execution Status.
 * Matches wit/vsoc.wit: vsoc-state
 */
enum class execution_state : std::uint32_t {
  IDLE,
  LOADING,
  READY,
  RUNNING,
  DEBUGGING,
  ERROR,
};

/**
 * @brief vSoC Instance Configuration.
 * Matches wit/vsoc.wit: vsoc-config and src/vsoc_dummy.cxx usage.
 */
struct instance_config {
  bool jit_enabled;
  std::uint32_t jit_cache_size;
  std::uint32_t ram_base_addr;
  std::uint32_t ram_size_bytes;
  std::uint32_t vmmio_base_addr;
  std::uint32_t vmmio_max_regions;
};

/**
 * @brief vSoC Runtime Context (Mutable State).
 * Matches docs/orders/components/vsoc.md: vsoc_context
 */
struct execution_context {
  execution_state state;
  interrupt_flags irq_flags;
  wasm_pc pc;
  // wasm_module_view* module_view;
};

/**
 * @brief vSoC Controller / Manager.
 * Orchestrates the vSoC lifecycle.
 */
class runtime {
public:
  explicit runtime(const instance_config& config);
  runtime(const instance_config& config, const harness& harness);
  ~runtime() = default;

  // Disable copy
  runtime(const runtime&) = delete;
  runtime& operator=(const runtime&) = delete;

  /**
   * @brief Loads a WASM module from binary data.
   */
  operation_result load(binary_view bin);

  /**
   * @brief Executes the guest code until yield, trap, or interrupt.
   */
  operation_result step();

  /**
   * @brief Stops the vSoC and releases associated resources.
   */
  void stop();

  /**
   * @brief Resets the vSoC to its initial state.
   */
  operation_result reset();

  /**
   * @brief Injects a virtual interrupt into the guest environment.
   */
  void notify_interrupt(std::uint32_t irq_id);

  // Getters
  execution_state get_state() const { return context_.state; }
  wasm_pc get_pc() const { return context_.pc; }

private:
  instance_config config_;
  execution_context context_;
  harness harness_;
};

} // namespace fireball::vsoc

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
 * @brief vSoC Configureation.
 * Matches wit/vsoc.wit: vsoc-config
 */
struct runtime_instance_config {
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
 *
 * @tparam Harness Policy type that provides access to system components.
 *                 Must be DefaultConstructible and satisfy the Harness interface.
 * @tparam Config Reference to the static configuration instance.
 *                Must be a compile-time constant or static duration object.
 */
template <typename Harness, const runtime_instance_config& Config>
class runtime {
public:
  // Using default constructor as config is a template parameter
  // and Harness is default constructed.
  runtime() : context_{}, harness_{} {
    context_.state = execution_state::IDLE;
    context_.pc = 0;
    context_.irq_flags = 0;
  }

  ~runtime() = default;

  // Disable copy
  runtime(const runtime&) = delete;
  runtime& operator=(const runtime&) = delete;

  /**
   * @brief Loads a WASM module from binary data.
   */
  operation_result load(binary_view bin) {
    (void)bin;
    context_.state = execution_state::READY;
    return {}; // Returns default success result (void)
  }

  /**
   * @brief Executes the guest code until yield, trap, or interrupt.
   */
  operation_result step() {
    if (context_.state != execution_state::READY &&
        context_.state != execution_state::RUNNING) {
      // TODO: Return actual error code when result type is fully defined
      return {}; 
    }

    context_.state = execution_state::RUNNING;

    // Access to dependencies via Harness Policy
    // auto* loader = harness_.loader();

    // Direct access to Config (Compile-time constant)
    // auto ram_base = Config.ram_base_addr; 

    // TODO: Implement execution loop

    context_.state = execution_state::READY;
    return {};
  }

  /**
   * @brief Stops the vSoC and releases associated resources.
   */
  void stop() { context_.state = execution_state::IDLE; }

  /**
   * @brief Resets the vSoC to its initial state.
   */
  operation_result reset() {
    context_.state = execution_state::IDLE;
    context_.pc = 0;
    context_.irq_flags = 0;
    return {};
  }

  /**
   * @brief Injects a virtual interrupt into the guest environment.
   */
  void notify_interrupt(std::uint32_t irq_id) {
    context_.irq_flags |= (1U << irq_id);
  }

  // Getters
  execution_state get_state() const { return context_.state; }
  wasm_pc get_pc() const { return context_.pc; }
  
  // Accessor for the static configuration
  static constexpr const runtime_instance_config& config() { return Config; }

private:
  execution_context context_;
  [[no_unique_address]] Harness harness_; // EBCO if Harness is empty
};

} // namespace fireball::vsoc

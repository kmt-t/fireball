#pragma once

#include <cstdint>
#include <span>
#include <optional>

#include "context.hxx"
#include "loader.hxx"
#include "executor.hxx"
#include "vmmio.hxx"
#include "debugger.hxx"

/**
 * @namespace fireball::vsoc
 * @brief Virtual System on Chip (vSoC) management and execution.
 */
namespace fireball::vsoc {

/**
 * @struct vsoc_config
 * @brief Static configuration for vSoC instance.
 * @details Usually placed in ROM to minimize RAM footprint. {ConfigurableSystem}
 */
struct vsoc_config {
  bool jit_enabled;           ///< Whether to enable JIT compilation.
  uint32_t code_cache_size;   ///< Size of JIT code cache (double buffer). {JIT_DoubleBuffer_Cache}
  uint32_t ram_base;          ///< Virtual base address of guest RAM.
  uint32_t ram_size;          ///< Size of guest RAM in bytes.
  uint32_t vmmio_base;        ///< Base address for vMMIO region.
};

/**
 * @enum vsoc_status
 * @brief Status codes for vSoC operations.
 */
enum class vsoc_status : uint8_t {
  ok = 0,
  error_invalid_binary,
  error_out_of_memory,
  error_not_ready,
  error_trap,
  error_yield
};

/**
 * @struct vsoc_harness
 * @brief Aggregate container for vSoC subcomponents.
 * @details Allows easy swapping of components for experimentation. {IoC}
 */
struct vsoc_harness {
  wasm_loader*      loader;
  executor*         engine;      ///< Unified Execution Engine (Interpreter + JIT)
  vmmio*            mmio;
  debugger*         dbg;
};

/**
 * @typedef vsoc_runtime
 * @brief Conceptually same as harness, used as an environment pointer within execution context.
 */
using vsoc_runtime = vsoc_harness;

/**
 * @class vsoc
 * @brief Interface for the vSoC manager.
 * @details Manages WASM execution, memory, and virtual hardware via subcomponents. {vSoC}
 */
class vsoc {
public:
  virtual ~vsoc() = default;

  /**
   * @brief Initializes the vSoC with a harness of subcomponents.
   * @param harness Set of subcomponents to use.
   * @return vsoc_status Result of initialization.
   */
  virtual vsoc_status init(const vsoc_harness& harness) = 0;

  /**
   * @brief Loads a WASM binary module into the vSoC.
   * @param binary The WASM binary data to load.
   * @return vsoc_status Result of the load operation.
   * @pre System must be initialized. Configuration must be valid.
   * @post internal module_view is constructed via loader. {MultiModule_Support}
   */
  virtual vsoc_status load_module(std::span<const uint8_t> binary) = 0;

  /**
   * @brief Executes the guest code for a certain period.
   * @return vsoc_status Result of the execution step.
   * @pre state must be Ready.
   * @post PC and guest registers are updated via executor. {ThreadedInterpreter} {JIT_CopyAndPatch}
   */
  virtual vsoc_status step() = 0;

  /**
   * @brief Notifies a virtual interrupt to the guest.
   * @param irq_id Identifier of the virtual interrupt.
   * @pre None.
   * @post Internal interrupt_flags are updated, reflecting in SYSCTL registers.
   */
  virtual void notify_interrupt(uint32_t irq_id) = 0;

  /**
   * @brief Gets the execution context (for testing/inspection).
   * @return reference to the current execution context.
   */
  virtual execution_context& get_context() = 0;
};

} // namespace fireball::vsoc

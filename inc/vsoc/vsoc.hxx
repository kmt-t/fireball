/**
 * @file vsoc.hxx
 * @brief Virtual System-on-Chip (vSoC) Interface
 *
 * vSoC manages the Wasm execution environment, integrating Loader, Interpreter,
 * JIT, vMMIO, and Debugger.
 */

#ifndef FIREBALL_VSOC_VSOC_HXX
#define FIREBALL_VSOC_VSOC_HXX

#include <fireball.hxx>
#include <utils/economic_function.hxx>
#include <cstdint>
#include <span>

namespace fireball {

// Forward declarations
class wasm_loader;
class interpreter;
class jit_compiler;
class debugger;
class vmmio_controller;
struct module_view;

/**
 * @brief vSoC Configuration
 * 
 * Defines the operating parameters of the vSoC.
 * This structure should reside in ROM.
 * {ConfigurableSystem} {Policy_ROM_Config}
 */
struct vsoc_config {
    bool jit_enabled;               ///< Enable JIT compilation
    std::uint32_t ram_base;         ///< Guest RAM base address
    std::uint32_t ram_size;         ///< Guest RAM size
    std::uint32_t vmmio_base;       ///< vMMIO region base address
    std::size_t code_cache_size;    ///< Size of JIT code cache
};

/**
 * @brief vMMIO Access Callback
 * 
 * Uses economic_function to support lambdas without heap allocation.
 */
using vmmio_callback = utils::economic_function<status_t(std::uint32_t addr, std::uint32_t data)>;

/**
 * @brief vSoC Manager
 * 
 * Manages the vSoC execution environment following Clean Architecture and IoC.
 * {LowLatencyJIT} {MemoryIsolation} {FaultIsolation} {EnvironmentPointer}
 */
class vsoc_manager {
public:
    /**
     * @brief Construct vSoC manager with static ROM configuration.
     * 
     * @param config Pointer to the configuration reside in ROM.
     * @pre config must not be null.
     */
    explicit vsoc_manager(const vsoc_config* config);

    /**
     * @brief Load a WASM module.
     * 
     * @param data Module data in binary format.
     * @pre System is initialized. data is a valid WASM binary.
     * @post The module is prepared for execution. State becomes Ready.
     * @return status_t ok on success, error/invalid_argument on failure.
     */
    status_t load(std::span<const std::uint8_t> data);

    /**
     * @brief Resume/Continue execution.
     * 
     * @pre Internal state is Ready.
     * @post Runs until yield, termination, or trap. PC and registers are updated.
     * @return status_t ok on yield, error on trap.
     */
    status_t step();

    /**
     * @brief Notify virtual interrupt.
     * 
     * @param id Virtual interrupt ID.
     * @pre None (safe to call from ISR).
     * @post A flag is set in the execution context for the guest.
     */
    void notify_interrupt(std::uint32_t id);

    /**
     * @brief Register a vMMIO hook.
     * 
     * @param hook_id Hook identifier (matching static map).
     * @param cb Callback function (can be a lambda concept).
     * @pre hook_id is valid.
     * @post The callback is registered and will be invoked on guest access.
     * @return status_t ok on success, not_found if hook_id is invalid.
     */
    status_t register_vmmio_hook(std::uint32_t hook_id, vmmio_callback cb);

private:
    const vsoc_config* config_;      ///< Static ROM config pointer {Policy_ROM_Config}
    
    wasm_loader* loader_;
    module_view* module_view_;
    interpreter* interpreter_;
    jit_compiler* jit_;
    debugger* debugger_;
    vmmio_controller* vmmio_;

    std::uint32_t interrupt_flags_; ///< Virtual interrupt flags {Challenge_InterruptSafety}
};

} // namespace fireball

#endif // FIREBALL_VSOC_VSOC_HXX

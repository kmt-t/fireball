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
#include <cstdint>
#include <span>
#include <functional>

namespace fireball {

// Forward declarations
class wasm_loader;
class interpreter;
class jit_compiler;
class debugger;
class vmmio_controller;
struct module_view;

// Type aliases (Placeholder if not defined in common headers)
// Note: These should ideally be in commons.hxx or fireball.hxx
using status = int;
using irq_id = std::uint32_t;
using module_data = std::span<const std::uint8_t>;
using vmmio_callback = std::function<status(std::uint32_t addr, std::uint32_t data)>;

/**
 * @brief vSoC Configuration
 * 
 * Defines the operating parameters of the vSoC.
 * {ConfigurableSystem}
 */
struct vsoc_config {
    bool jit_enabled;               ///< Enable JIT compilation
    std::size_t code_cache_size;    ///< Size of JIT code cache
    std::uint32_t ram_base;         ///< Guest RAM base address
    std::uint32_t ram_size;         ///< Guest RAM size
    std::uint32_t vmmio_base;       ///< vMMIO region base address
};

/**
 * @brief vSoC Manager
 * 
 * Manages the vSoC execution environment.
 * {LowLatencyJIT} {MemoryIsolation} {FaultIsolation} {EnvironmentPointer}
 */
class vsoc_manager {
public:
    /**
     * @brief Load a WASM module.
     * 
     * @param data Module data
     * @return status Execution result
     * @pre None
     * @post State becomes Ready
     */
    status load(const module_data& data);

    /**
     * @brief Resume/Continue execution.
     * 
     * @return status Execution result
     * @pre Ready state
     * @post Runs until yield or termination
     */
    status step();

    /**
     * @brief Notify virtual interrupt.
     * 
     * @param id Interrupt ID
     * @pre None
     * @post Flag is set in the context
     */
    void notify_interrupt(irq_id id);

    /**
     * @brief Register a vMMIO hook.
     * 
     * @param addr Start address
     * @param size Size
     * @param cb Callback function
     * @return status Execution result
     * @pre None
     * @post Hook becomes active
     */
    status register_vmmio_hook(std::uint32_t addr, std::uint32_t size, vmmio_callback cb);

private:
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

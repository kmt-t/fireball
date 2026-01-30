#pragma once

#include <cstdint>
#include <span>

namespace fireball::vsoc {

/**
 * @class vmmio_handler
 * @brief Hook interface for virtual MMIO access.
 */
class vmmio_handler {
public:
    virtual ~vmmio_handler() = default;

    /**
     * @brief Handles MMIO access (single or bulk).
     * @param offset Offset from the base address.
     * @param buffer Data buffer to read from or write to.
     * @param is_write True for write, False for read.
     * @return vsoc_status Result of the operation.
     */
    virtual vsoc_status handle_access(uint32_t offset, std::span<uint8_t> buffer, bool is_write) = 0;
};

/**
 * @class vmmio
 * @brief Interface for virtual MMIO dispatcher.
 * @details Manages hooks and traps memory accesses. {vMMIO_TrapAndEmulate}
 */
class vmmio {
public:
    virtual ~vmmio() = default;

    /**
     * @brief Registers a hook for a specific vMMIO region.
     * @param hook_id ROM-defined identifier for the vMMIO area.
     * @param handler Pointer to the handler.
     * @return vsoc_status Result of the registration.
     */
    virtual vsoc_status register_hook(uint32_t hook_id, vmmio_handler* handler) = 0;

    /**
     * @brief Dispatches a memory access to the appropriate hook.
     * @param ctx Current execution context.
     * @param addr Target address.
     * @param buffer Data buffer for bulk/single transfer.
     * @param is_write True if write, false if read.
     * @return vsoc_status Result of the dispatch.
     */
    virtual vsoc_status dispatch(execution_context& ctx, uint32_t addr, std::span<uint8_t> buffer, bool is_write) = 0;
};

} // namespace fireball::vsoc

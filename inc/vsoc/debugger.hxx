#pragma once

#include "context.hxx"

namespace fireball::vsoc {

/**
 * @class debugger
 * @brief Interface for vSoC debugger.
 * @details Handles GDB RSP commands and execution control. {Debug_Integrated}
 */
class debugger {
public:
    virtual ~debugger() = default;

    /**
     * @brief Attaches the debugger to an execution context.
     * @param ctx Context to debug.
     */
    virtual void attach(execution_context& ctx) = 0;

    /**
     * @brief Polls for debug commands and updates state.
     */
    virtual void poll() = 0;

    /**
     * @brief Detaches the debugger.
     */
    virtual void detach() = 0;
};

} // namespace fireball::vsoc

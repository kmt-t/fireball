#pragma once

#include "context.hxx"
#include "vsoc.hxx" // For vsoc_status

namespace fireball::vsoc {

/**
 * @class executor
 * @brief Unified interface for the execution engine (Interpreter + JIT).
 * @details Swappable execution unit that handles both interpretation and JIT logic. {ThreadedInterpreter} {JIT_CopyAndPatch}
 */
class executor {
public:
    virtual ~executor() = default;

    /**
     * @brief Executes the guest code for a certain period.
     * @param ctx Execution context to operate on.
     * @return vsoc_status Result of the execution step.
     * @post Context state (PC, registers) is updated.
     */
    virtual vsoc_status step(execution_context& ctx) = 0;
};

} // namespace fireball::vsoc

#pragma once

#include <cstdint>

namespace fireball::vsoc {

struct vsoc_runtime; // Forward declaration of the harness/runtime container

/**
 * @struct execution_context
 * @brief Shared execution state (registers) for WASM runtime.
 * @details Used by both Interpreter and JIT. {ContextPointerRegister}
 */
struct execution_context {
    uint32_t pc;                ///< Program Counter (WASM bytecode offset)
    uint8_t* stack_ptr;         ///< Operand stack pointer
    uint8_t* stack_base;        ///< Base of operand stack
    uint8_t* memory_base;       ///< Guest linear memory base
    uint32_t memory_size;       ///< Guest linear memory size
    uint32_t interrupt_flags;   ///< Virtual interrupt flags {Challenge_InterruptSafety}
    vsoc_runtime* env;          ///< Environment pointer for system access {EnvironmentPointer}
};

} // namespace fireball::vsoc

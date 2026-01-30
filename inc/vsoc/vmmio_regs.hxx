#pragma once

#include <cstdint>

namespace fireball::vsoc {

/**
 * @namespace fireball::vsoc::regs
 * @brief Register offsets within vMMIO regions.
 */
namespace regs {

// SYSCTL Registers (HOOK_ID_SYSCTL)
static constexpr uint32_t REG_SYS_CONTROL    = 0x00U; ///< 1: Yield, 2: Halt
static constexpr uint32_t REG_SYS_STATUS     = 0x04U; ///< Status flags
static constexpr uint32_t REG_IRQ_FLAGS      = 0x08U; ///< Virtual IRQ flags
static constexpr uint32_t REG_SYSCALL_ID     = 0x10U; ///< Service ID for fireball_call
static constexpr uint32_t REG_SYSCALL_ARG0   = 0x14U; ///< Arg 0 / Return value
static constexpr uint32_t REG_SYSCALL_ARG1   = 0x18U; ///< Arg 1
static constexpr uint32_t REG_SYSCALL_ARG2   = 0x1CU; ///< Arg 2

// VDMA Registers (HOOK_ID_VDMA)
static constexpr uint32_t REG_VDMA_SRC       = 0x00U; ///< Source address
static constexpr uint32_t REG_VDMA_DST       = 0x04U; ///< Destination address
static constexpr uint32_t REG_VDMA_COUNT     = 0x08U; ///< Byte count
static constexpr uint32_t REG_VDMA_CTRL      = 0x0CU; ///< Control (Bit0: START)

} // namespace regs

} // namespace fireball::vsoc

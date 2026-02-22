/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <optional>
#include <tuple>
#include <concepts>

namespace fireball {

/**
 * Configuration for VM setup.
 */
struct vm_setup_config {
  mem_address ram_base;
  mem_byte_count ram_size;
  mem_address entry_point;
  mem_address passthrough_base;
};

/**
 * Execution states for the virtual machine.
 */
enum class execution_state_category : uint8_t {
  READY,
  RUNNING,
  HALTED,
  TRAPPED,
  INTERRUPTED,
};

/**
 * Identifiers for Virtual MMIO hooks.
 */
enum class hook_category : uint8_t {
  TIMER,
  UART,
  DEBUG,
  RESERVED,
};

/**
 * Static 1:1 mapping entry: physical IRQ -> virtual IRQ ID.
 */
struct irq_mapping_entry {
  uint32_t physical_irq;
  uint32_t virtual_irq_id;
};

} // namespace fireball

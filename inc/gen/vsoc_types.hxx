/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <expected>
#include <optional>
#include <tuple>

namespace fireball {

/**
 * Virtual machine configuration.
 */
struct vm_config {
  address ram_base;
  byte_count ram_size;
  address entry_point;
  address passthrough_base;
};

/**
 * Execution states for the virtual machine.
 */
enum class execution_state : uint8_t {
  READY,
  RUNNING,
  HALTED,
  TRAPPED,
  INTERRUPTED,
};

/**
 * Identifiers for Virtual MMIO hooks.
 */
enum class vmmio_hook_id : uint8_t {
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

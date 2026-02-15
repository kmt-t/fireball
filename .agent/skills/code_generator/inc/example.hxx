// AUTO-GENERATED FILE - DO NOT EDIT
#pragma once

#include <cstdint>
#include "core/types.hxx"

namespace fireball::vsoc {

// Strategies for handling interrupts in vSoC
enum class interrupt_strategy : uint32_t {
  DIRECT = 0, // Direct delivery
  QUEUED = 1, // Queued via buffer
};

// Configuration for a virtual device
struct device_config {
  address base_address; // vMMIO base address
  byte_count region_size; // Size of MMIO region
  uint32_t irq_id; // Interrupt ID
};

} // namespace fireball::vsoc
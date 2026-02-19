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
 * Hardware device categories.
 */
enum class device_kind : uint8_t {
  STREAM_DEVICE,
  BLOCK,
  TRIGGER,
  TIMER,
};

/**
 * Configuration for the HAL buffer pool.
 */
struct pool_config {
  uint32_t block_size;
  uint32_t block_count;
};

/**
 * Supported debug transport layers.
 */
enum class debug_transport : uint8_t {
  UART,
  RTT,
};

} // namespace fireball

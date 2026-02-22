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
 * Hardware device categories.
 */
enum class device_category : uint8_t {
  STREAM_DEVICE,
  BLOCK,
  TRIGGER,
  TIMER,
};

/**
 * Configuration for the HAL buffer pool.
 */
struct pool_setup_record {
  uint32_t block_size;
  uint32_t block_count;
};

/**
 * Supported debug transport layers.
 */
enum class debug_transport_category : uint8_t {
  UART,
  RTT,
};

} // namespace fireball

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
#include <vector>
#include <tuple>

namespace fireball {

/**
 * Tier 3: Logging Engine
 * @inv: buffer_size is power of 2
 */
class engine {
public:
  engine() = default;
  ~engine() = default;

  /**
   * Records an event using dictionary offset and arguments.
   * @pre: dict_offset is valid within the ROM dictionary
   */
  void log_event(log_level level, uint32_t dict_offset, uint32_t arg0, uint32_t arg1, uint32_t arg2, uint32_t arg3) noexcept;

  /**
   * Flushes the internal ring buffer to physical transport.
   * @post: buffer is empty
   */
  void flush() noexcept;

  /**
   * Sets the minimum log level for physical output.
   */
  void set_threshold(log_level level) noexcept;

};

} // namespace fireball

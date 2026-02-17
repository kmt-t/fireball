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
 * @inv: buffer_full_policy == OVERWRITE (oldest entry discarded)
 */
class engine {
public:
  engine() = default;
  ~engine() = default;

  /**
   * Records an event using dictionary offset and arguments.
   * @pre: dict_offset is valid within the ROM dictionary
   * @post: entry queued in ring buffer. If buffer full, oldest entry overwritten.
   */
  void log_event(log_level level, uint32_t dict_offset, uint32_t arg0, uint32_t arg1, uint32_t arg2, uint32_t arg3) noexcept;

  /**
   * Flushes the internal ring buffer to physical transport.
   * Called by COOS idle hook when no tasks are READY.
   * @pre: transport is initialized
   * @post: all buffered entries transferred to transport. buffer is empty.
   * @derives: logging.md §4.1 BufferedLogging, §5.1 flush
   */
  void flush() noexcept;

  /**
   * Sets the minimum log level for physical output.
   * @post: only events with level >= threshold are output
   */
  void set_threshold(log_level level) noexcept;

};

} // namespace fireball

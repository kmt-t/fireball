/**
 * HAL: Timer Interface.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>
#include <hal/poll.hxx>

namespace fireball::hal {

struct timer_handle {
  virtual ~timer_handle() = default;

  /**
   * Returns the current monotonic time in nanoseconds.
   */
  virtual std::uint64_t now() = 0;

  /**
   * Creates a pollable that resolves after the specified duration.
   */
  virtual pollable& subscribe_duration(std::uint64_t nanos) = 0;
};

} // namespace fireball::hal

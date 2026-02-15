/**
 * HAL: Trigger (GPIO) Interface.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>

namespace fireball::hal {

struct trigger {
  virtual ~trigger() = default;

  /**
   * Sets the output value of a physical pin.
   */
  virtual operation_result set_pin(std::uint32_t pin, bool value) = 0;

  /**
   * Gets the current value of a physical pin.
   */
  virtual result<bool> get_pin(std::uint32_t pin) = 0;
};

} // namespace fireball::hal

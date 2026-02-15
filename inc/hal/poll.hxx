/**
 * HAL: Pollable Interface.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>

namespace fireball::hal {

struct pollable {
  virtual ~pollable() = default;

  /**
   * Returns true if the resource is ready.
   */
  virtual bool ready() = 0;

  /**
   * Blocks until the resource is ready.
   */
  virtual void block() = 0;
};

} // namespace fireball::hal

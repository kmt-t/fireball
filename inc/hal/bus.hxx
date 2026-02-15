/**
 * HAL: Bus Interface.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>
#include <hal/poll.hxx>

namespace fireball::hal {

struct bus_master {
  virtual ~bus_master() = default;

  /**
   * Performs a synchronous transfer (write then read or simultaneous).
   */
  virtual result<shm_handle> transfer(shm_handle tx_data, byte_count rx_len) = 0;
};

struct bus_slave {
  virtual ~bus_slave() = default;

  /**
   * Sets the response data for the next master read request.
   */
  virtual operation_result set_response(shm_handle data) = 0;

  /**
   * Gets the data received from the last master write request.
   */
  virtual result<shm_handle> get_received() = 0;

  /**
   * Subscribes to bus access events (e.g. addressed as slave).
   */
  virtual pollable& subscribe() = 0;
};

} // namespace fireball::hal

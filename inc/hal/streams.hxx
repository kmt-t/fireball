/**
 * HAL: Streams Interface.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>
#include <hal/poll.hxx>

namespace fireball::hal {

struct input_stream {
  virtual ~input_stream() = default;

  /**
   * Performs a non-blocking read.
   */
  virtual result<shm_handle> read(std::uint64_t len) = 0;

  /**
   * Performs a blocking read.
   */
  virtual result<shm_handle> blocking_read(std::uint64_t len) = 0;

  /**
   * Subscribes to readiness events for this stream.
   */
  virtual pollable& subscribe() = 0;
};

struct output_stream {
  virtual ~output_stream() = default;

  /**
   * Performs a non-blocking write.
   */
  virtual operation_result write(shm_handle data) = 0;

  /**
   * Performs a blocking write and flush.
   */
  virtual operation_result blocking_write(shm_handle data) = 0;

  /**
   * Subscribes to readiness events for this stream.
   */
  virtual pollable& subscribe() = 0;
};

} // namespace fireball::hal

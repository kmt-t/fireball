/**
 * HAL: Streaming Interface.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <fireball.hxx>
#include <hal/streams.hxx>

namespace fireball::hal {

struct streaming_master {
  virtual ~streaming_master() = default;

  /**
   * Gets the output stream for sending data.
   */
  virtual output_stream& get_output_stream() = 0;
};

struct streaming_slave {
  virtual ~streaming_slave() = default;

  /**
   * Gets the input stream for receiving data.
   */
  virtual input_stream& get_input_stream() = 0;
};

} // namespace fireball::hal

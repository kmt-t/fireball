/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/hal_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <expected>
#include <optional>
#include <tuple>

namespace fireball {

/**
 * Information about a physical device.
 */
struct device_metadata_record {
  hal_device_id id;
  device_category kind;
  std::string_view name;
  mem_byte_count unit_size;
  uint32_t reserved_pages;
};

/**
 * Information about an I/O buffer slot.
 */
struct buffer_metadata_record {
  mem_shm_id id;
  mem_byte_count size;
  mem_address base;
  bool is_active;
};

/**
 * Tier 1: Hardware Abstraction Layer Controller
 * @inv: device_id < FB_CONF_HAL_MAX_DEVICES
 * @inv: active_buffers <= FB_CONF_HAL_MAX_BUFFERS
 */
class device_controller {
public:
  device_controller() = default;
  ~device_controller() = default;

  /**
   * Initializes the HAL.
   */
  operation_result init_hal() noexcept;

  /**
   * Reads data from a physical device into shared memory.
   * @pre: id < FB_CONF_HAL_MAX_DEVICES
   * @pre: dest != 0
   * @post: result.is_ok() -> read_bytes <= dest.size
   */
  std::expected<uint32_t, sys_recovery_strategy> read(hal_device_id id, mem_shm_id dest) noexcept;

  /**
   * Writes data from shared memory to a physical device.
   * @pre: id < FB_CONF_HAL_MAX_DEVICES
   * @pre: src != 0
   */
  operation_result write(hal_device_id id, mem_shm_id src) noexcept;

  /**
   * Hardware specific control using Key-Value parameters.
   * @pre: id < FB_CONF_HAL_MAX_DEVICES
   * @param params Handle to a Key-Value message block.
   */
  operation_result control(hal_device_id id, uint32_t cmd, ipc_message params) noexcept;

  /**
   * Gets information about a device.
   */
  std::expected<device_metadata_record, bool> get_device_info(hal_device_id id) noexcept;

  /**
   * Gets information about a buffer.
   */
  std::expected<buffer_metadata_record, bool> get_buffer_info(mem_shm_id id) noexcept;

  /**
   * Acquires a fixed-size buffer for I/O.
   * @pre: size <= FB_CONF_HAL_BUFFER_SIZE
   * @post: result.is_ok() -> active_buffers == old(active_buffers) + 1
   */
  std::expected<mem_shm_id, sys_recovery_strategy> acquire_buffer(uint32_t size) noexcept;

  /**
   * Releases an I/O buffer.
   * @pre: id is valid and active
   * @post: active_buffers == old(active_buffers) - 1
   */
  void release_buffer(mem_shm_id id) noexcept;

};

/**
 * Tier 3: Physical GPIO / Interrupt Trigger
 */
class gpio_controller {
public:
  gpio_controller() = default;
  ~gpio_controller() = default;

  /**
   * Sets the state of a physical pin.
   */
  operation_result set_pin(uint32_t pin, bool value) noexcept;

  /**
   * Gets the state of a physical pin.
   */
  std::expected<bool, sys_recovery_strategy> get_pin(uint32_t pin) noexcept;

};

/**
 * Tier 3: Hardware System Timer
 */
class periodic_timer {
public:
  periodic_timer() = default;
  ~periodic_timer() = default;

  /**
   * Gets current system time in nanoseconds.
   */
  uint64_t get_now() noexcept;

  /**
   * Sets an alarm interrupt at a specific time.
   */
  mem_shm_id subscribe_timer(uint64_t nanos) noexcept;

};

/**
 * Tier 3: Master/Slave Bus (I2C/SPI) - Zero Copy
 */
class bus_master {
public:
  bus_master() = default;
  ~bus_master() = default;

  /**
   * Transfers data using shared memory buffers.
   * @pre: tx_data and rx_data are valid shm_id
   */
  operation_result transfer(mem_shm_id tx_buffer, mem_shm_id rx_buffer) noexcept;

};

class bus_slave {
public:
  bus_slave() = default;
  ~bus_slave() = default;

  /**
   * Sets the response buffer for the next master read.
   */
  operation_result set_response(mem_shm_id buffer) noexcept;

  /**
   * Gets the received buffer from the master write.
   */
  std::expected<mem_shm_id, sys_recovery_strategy> get_received() noexcept;

  /**
   * Subscribes to bus events.
   */
  mem_shm_id subscribe() noexcept;

};

/**
 * Tier 3: Streaming Data I/O - Zero Copy
 */
class streaming_master {
public:
  streaming_master() = default;
  ~streaming_master() = default;

  /**
   * Writes data from a shared memory buffer.
   */
  operation_result write(mem_shm_id buffer) noexcept;

};

/**
 * Tier 3: Streaming Data I/O - Zero Copy
 */
class streaming_slave {
public:
  streaming_slave() = default;
  ~streaming_slave() = default;

  /**
   * Reads data into a shared memory buffer.
   */
  operation_result read(mem_shm_id buffer) noexcept;

};

/**
 * Tier 3: GDB RSP Debug Server
 */
class debug_server {
public:
  debug_server() = default;
  ~debug_server() = default;

  operation_result poll_packet() noexcept;

  std::expected<mem_shm_id, sys_recovery_strategy> get_parsed_command() noexcept;

};

} // namespace fireball

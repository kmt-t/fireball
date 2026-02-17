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

using logger_engine = engine;

/**
 * Tier 1: URI-based Message Router
 * @inv: registry_count <= FB_CONF_ROUTER_MAX_SERVICES
 */
class ipc_router {
public:
  ipc_router() = default;
  ~ipc_router() = default;

  /**
   * Binds a service instance by its system index.
   * @pre: sid < FB_CONF_ROUTER_MAX_SERVICES
   */
  std::expected<channel_id, recovery_strategy> bind(service_id sid, uri_handle address) noexcept;

  /**
   * Connects to a service and gets its system-wide ID.
   * @pre: uri_handle is not empty
   */
  std::expected<service_id, recovery_strategy> connect(uri_handle address) noexcept;

  /**
   * Sends a Key-Value message block.
   * @pre: chan >= 100
   */
  operation_result send(channel_id chan, message msg) noexcept;

  /**
   * Receives a Key-Value message block.
   */
  std::expected<message, recovery_strategy> recv(channel_id chan) noexcept;

};

/**
 * Tier 3: Logger Dictionary
 */
class logger_dictionary {
public:
  logger_dictionary() = default;
  ~logger_dictionary() = default;

  void register_entry(uint32_t id, std::string_view format) noexcept;

  std::expected<std::string_view, bool> lookup(uint32_t id) noexcept;

};

/**
 * Tier 2: System-wide Logging Managed via Handle
 */
class system_logger {
public:
  system_logger() = default;
  ~system_logger() = default;

  /**
   * Initializes logger with injected engine.
   * @pre: engine is a valid resource handle
   * @post: logger is ready for use
   */
  operation_result initialize(uintptr_t engine) noexcept;

  /**
   * Logs a message (delegates to engine).
   * @pre: initialized
   */
  void log(log_level level, uint32_t dict_offset, uint32_t arg0, uint32_t arg1, uint32_t arg2, uint32_t arg3) noexcept;

};

} // namespace fireball

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
   * Ownership of message transfers from sender to channel or receiver.
   * @pre: chan >= 100 && owner(msg) == caller_task
   * @post(empty && recv_waiting): handoff — msg transferred directly to receiver.
   *       Both sender and receiver become RUNNING. No channel buffering.
   * @post(empty && !recv_waiting): msg moved to channel. Sender becomes BLOCKED_SEND.
   * @post(full): Sender becomes BLOCKED_SEND. Channel unchanged.
   * @derives: ipc_handoff.tla Send(t, msg) Cases 1-3
   */
  operation_result send(channel_id chan, message msg) noexcept;

  /**
   * Receives a Key-Value message block.
   * Ownership of message transfers from channel or sender to receiver.
   * @pre: caller task is RUNNING
   * @post(full): msg taken from channel. Channel becomes EMPTY.
   *       If sender is BLOCKED_SEND, sender becomes RUNNING.
   * @post(empty && send_waiting): handoff — msg transferred directly from sender.
   *       Both sender and receiver become RUNNING.
   * @post(empty && !send_waiting): Receiver becomes BLOCKED_RECV.
   * @derives: ipc_handoff.tla Recv(t) Cases 4-6
   */
  std::expected<message, recovery_strategy> recv(channel_id chan) noexcept;

};

/**
 * Tier 3: Logger Dictionary (ROM-resident)
 * @inv: entries are immutable after registration (ROM placement)
 * @inv: entry_count <= FB_CONF_LOG_DICT_MAX_ENTRIES
 * @note: Dictionary is ROM-resident. Host-side tool expands dict_offset + args into readable text.
 *        Entry format: { id: u32, format_string: null-terminated UTF-8 }
 */
class logger_dictionary {
public:
  logger_dictionary() = default;
  ~logger_dictionary() = default;

  /**
   * Registers a format string entry at build time.
   * @pre: id is unique within dictionary
   * @pre: format contains valid printf-style placeholders for up to 4 u32 args
   */
  void register_entry(uint32_t id, std::string_view format) noexcept;

  /**
   * Looks up a format string by ID.
   * @post(ok): returns format string for the given id
   * @post(err): id not found
   */
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

/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __COOS_CO_DEBUG_HXX__
#define __COOS_CO_DEBUG_HXX__

#include <commons.hxx>
#include <cstdint>
#include <functional>

namespace fireball { namespace coos {

/**
 * Debug event type
 */
enum class co_debug_event_type {
  coroutine_spawned,
  coroutine_started,
  coroutine_suspended,
  coroutine_resumed,
  coroutine_completed,
  context_switch,
  channel_send,
  channel_recv,
  channel_close,
  custom,
};

/**
 * Debug event information
 */
struct co_debug_event {
  co_debug_event_type type;
  uint32_t coro_id;
  uint32_t timestamp;  // milliseconds
  const char* message;
};

/**
 * Event-driven debugger interface
 *
 * Register lambda callbacks for debug events.
 * Callbacks are invoked when corresponding events occur.
 */
class co_debug {
 public:
  virtual ~co_debug() = default;

  /**
   * Register event callback
   */
  virtual void on_event(co_debug_event_type type,
                       std::function<void(const co_debug_event&)> callback) = 0;

  /**
   * Unregister event callback
   */
  virtual void off_event(co_debug_event_type type) = 0;

  /**
   * Fire debug event
   */
  virtual void fire_event(const co_debug_event& event) = 0;

  /**
   * Enable/disable event firing
   */
  virtual void set_enabled(bool enable) = 0;

  /**
   * Check if events are enabled
   */
  virtual bool is_enabled() const = 0;

  /**
   * Enable specific event type
   */
  virtual void enable_event_type(co_debug_event_type type) = 0;

  /**
   * Disable specific event type
   */
  virtual void disable_event_type(co_debug_event_type type) = 0;

  /**
   * Check if specific event type is enabled
   */
  virtual bool is_event_type_enabled(co_debug_event_type type) const = 0;

  /**
   * Get event count
   */
  virtual uint64_t get_event_count(co_debug_event_type type) const = 0;

  /**
   * Clear event counters
   */
  virtual void clear_counters() = 0;
};

} } // namespace fireball { namespace coos {

#endif // #ifndef __COOS_CO_DEBUG_HXX__

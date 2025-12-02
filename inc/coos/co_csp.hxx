/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __COOS_CO_CSP_HXX__
#define __COOS_CO_CSP_HXX__

#include <commons.hxx>
#include <coos/co_value.hxx>
#include <cstdint>
#include <optional>

namespace fireball { namespace coos {

/**
 * CSP channel state
 */
enum class csp_state {
  idle,           // Idle/waiting
  sender_ready,   // Sender placed value
  receiver_ready, // Receiver waiting
};

/**
 * CSP channel interface
 *
 * Values are transferred with ownership tracking via co_value<T>.
 * Once sent, the value is inaccessible from the sender.
 * All values moved by rvalue reference semantics.
 */
template <typename T>
class co_channel {
 public:
  virtual ~co_channel() = default;

  /**
   * Send value (blocking)
   */
  virtual void send(co_value<T> value) = 0;

  /**
   * Receive value (blocking)
   */
  virtual co_value<T> recv() = 0;

  /**
   * Try send (non-blocking)
   */
  virtual bool try_send(co_value<T> value) = 0;

  /**
   * Try receive (non-blocking)
   */
  virtual std::optional<co_value<T>> try_recv() = 0;

  /**
   * Get channel state
   */
  virtual csp_state get_state() const = 0;

  /**
   * Close channel
   */
  virtual void close() = 0;

  /**
   * Check if closed
   */
  virtual bool is_closed() const = 0;

  /**
   * Check if sender is waiting
   */
  virtual bool sender_waiting() const = 0;

  /**
   * Check if data is available
   */
  virtual bool has_data() const = 0;
};

/**
 * CSP select interface
 */
template <typename T>
class co_select {
 public:
  virtual ~co_select() = default;

  /**
   * Add receive channel
   */
  virtual void add_recv(co_channel<T>* ch) = 0;

  /**
   * Add send channel
   */
  virtual void add_send(co_channel<T>* ch) = 0;

  /**
   * Select from ready channels
   */
  virtual std::optional<std::pair<size_t, co_value<T>>> select() = 0;

  /**
   * Clear channels
   */
  virtual void clear() = 0;
};

/**
 * CSP wait group interface
 */
class co_wait_group {
 public:
  virtual ~co_wait_group() = default;

  /**
   * Add counter
   */
  virtual void add(size_t delta = 1) = 0;

  /**
   * Mark completion
   */
  virtual void done() = 0;

  /**
   * Check if all completed
   */
  virtual bool is_done() const = 0;

  /**
   * Get current counter
   */
  virtual size_t count() const = 0;
};

/**
 * CSP process interface
 */
class co_process {
 public:
  virtual ~co_process() = default;

  /**
   * Run process
   */
  virtual void run() = 0;

  /**
   * Get process name
   */
  virtual const char* get_name() const = 0;
};

} } // namespace fireball { namespace coos {

#endif // #ifndef __COOS_CO_CSP_HXX__

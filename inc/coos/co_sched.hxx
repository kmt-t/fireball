/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __COOS_CO_SCHED_HXX__
#define __COOS_CO_SCHED_HXX__

#include <commons.hxx>
#include <functional>
#include <cstdint>

namespace fireball { namespace coos {

/**
 * Scheduler interface
 * CSP-based cooperative multitasking
 */
class co_sched {
 public:
  virtual ~co_sched() = default;

  /**
   * Spawn coroutine
   */
  virtual uint32_t spawn(const std::function<void()>& proc) = 0;

  /**
   * Run event loop (until all coroutines completed)
   */
  virtual void run() = 0;

  /**
   * Single step execution
   */
  virtual bool step() = 0;

  /**
   * Stop scheduler
   */
  virtual void stop() = 0;

  /**
   * Check if scheduler is running
   */
  virtual bool is_running() const = 0;

  /**
   * Get pending coroutine count
   */
  virtual size_t pending_count() const = 0;

  /**
   * Get completed coroutine count
   */
  virtual size_t completed_count() const = 0;
};

} } // namespace fireball { namespace coos {

#endif // #ifndef __COOS_CO_SCHED_HXX__

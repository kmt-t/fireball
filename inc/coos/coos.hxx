/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __COOS_HXX__
#define __COOS_HXX__

#include <coos/coroutine.hxx>
#include <coos/co_csp.hxx>
#include <coos/co_sched.hxx>
#include <coos/co_mem.hxx>
#include <coos/co_debug.hxx>

namespace fireball { namespace coos {

/**
 * COOS (Cooperative Operating System)
 * CSP-based green thread system for single-threaded environments
 */
class coos_kernel {
 public:
  virtual ~coos_kernel() = default;

  /**
   * Initialize kernel
   */
  virtual void initialize() = 0;

  /**
   * Shutdown kernel
   */
  virtual void shutdown() = 0;

  /**
   * Get scheduler
   */
  virtual co_sched* get_scheduler() = 0;

  /**
   * Get memory manager
   */
  virtual co_mem* get_memory_manager() = 0;

  /**
   * Get stack allocator
   */
  virtual co_stack_allocator* get_stack_allocator() = 0;

  /**
   * Get debugger/tracer
   */
  virtual co_debug* get_debugger() = 0;

  /**
   * Create CSP channel
   * All synchronization is realized through CSP channels
   */
  template <typename T>
  virtual co_channel<T>* create_channel() = 0;

  /**
   * Destroy channel
   */
  template <typename T>
  virtual void destroy_channel(co_channel<T>* ch) = 0;

  /**
   * Yield execution (context switch)
   */
  virtual void yield() = 0;

  /**
   * Run main loop (until all coroutines completed)
   */
  virtual void run() = 0;

  /**
   * Single step execution
   */
  virtual bool step() = 0;

  /**
   * Stop kernel
   */
  virtual void stop() = 0;

  /**
   * Check if kernel is running
   */
  virtual bool is_running() const = 0;

  /**
   * Get version information
   */
  virtual const char* get_version() const = 0;
};

/**
 * Get global kernel instance
 */
coos_kernel* get_kernel();

/**
 * Set global kernel instance
 */
void set_kernel(coos_kernel* kernel);

} } // namespace fireball { namespace coos {

#endif // #ifndef __COOS_HXX__

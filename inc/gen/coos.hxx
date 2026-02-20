/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/coos_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <expected>
#include <optional>
#include <tuple>

namespace fireball {

/**
 * Tier 2: Cooperative Task Scheduler
 * @inv: current_task_id != 0 -> task_status[current_task_id] == running
 * @inv: ready_queue_count <= FB_CONF_MAX_TASKS
 * @constexpr: MAX_TASKS = FB_CONF_MAX_TASKS
 */
class task_scheduler {
public:
  static constexpr auto MAX_TASKS = FB_CONF_MAX_TASKS;
  task_scheduler() = default;
  ~task_scheduler() = default;

  /**
   * Initializes the scheduler with required dependencies.
   * @pre: !initialized
   * @pre: memory is a valid base address
   * @post: initialized
   */
  operation_result init_scheduler(mem_address memory) noexcept;

  /**
   * Spawns a new task from a WASM entry point with priority.
   * @pre: initialized
   * @pre: name is not empty && entry != 0
   * @post: result.is_ok() -> task_id > 0
   */
  std::expected<os_task_id, sys_recovery_strategy> spawn(std::string_view name, mem_address entry, uint8_t priority) noexcept;

  /**
   * Yields execution to the next ready task.
   * @pre: initialized
   * @post: current_task transitions to ready state
   */
  void yield() noexcept;

  /**
   * Blocks the current task until a notification is received.
   * @pre: initialized && current_task != 0
   * @post: current_task transitions to blocked state
   */
  operation_result wait() noexcept;

  /**
   * Terminates the specified task.
   * @pre: initialized && id is valid and active
   * @post: task state == terminated
   */
  void terminate(os_task_id id) noexcept;

  /**
   * Notifies the scheduler of an interrupt to wake up a task.
   * @pre: initialized
   * @post: task transitions to ready or interrupted state
   */
  void notify_interrupt(os_task_id id) noexcept;

  /**
   * Gets information about a task.
   * @pre: initialized
   */
  std::expected<task_context_record, bool> get_task_info(os_task_id id) noexcept;

  /**
   * Main scheduling loop.
   * @pre: initialized
   */
  void run() noexcept;

};

/**
 * Tier 2: Hoare CSP Channel
 * @inv: buffer_size <= FB_CONF_CHANNEL_SIZE
 */
class ipc_channel {
public:
  ipc_channel() = default;
  ~ipc_channel() = default;

  /**
   * Sends a message block to the channel (blocks if no receiver).
   * @pre: msg is valid
   * @post: msg ownership transferred to channel or receiver
   */
  operation_result send(ipc_message msg) noexcept;

  /**
   * Receives a message block from the channel (blocks if empty).
   * @post: msg ownership transferred to caller
   */
  std::expected<ipc_message, sys_recovery_strategy> receive() noexcept;

  /**
   * Gets channel usage information.
   */
  std::expected<uint32_t, bool> get_channel_info() noexcept;

};

/**
 * Tier 2: Task-independent Memory Partition
 * @inv: partition_size <= FB_CONF_TASK_HEAP_SIZE
 */
class partition_memory {
public:
  partition_memory() = default;
  ~partition_memory() = default;

  /**
   * Allocates a block from the task's partition.
   * @pre: size > 0
   * @post: result.is_ok() -> allocated_bytes == old(allocated_bytes) + size
   */
  std::expected<mem_address, sys_recovery_strategy> allocate_block(mem_byte_count size) noexcept;

  /**
   * Frees a previously allocated block.
   * @pre: ptr != 0
   * @post: allocated_bytes == old(allocated_bytes) - size_of(ptr)
   */
  void deallocate_block(mem_address ptr) noexcept;

};

} // namespace fireball

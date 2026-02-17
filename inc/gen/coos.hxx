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

/**
 * Tier 2: Cooperative Task Scheduler
 * @inv: current_task_id != 0 -> task_status[current_task_id] == running
 * @inv: ready_queue_count <= FB_CONF_MAX_TASKS
 */
class scheduler {
public:
  scheduler() = default;
  ~scheduler() = default;

  /**
   * Initializes the scheduler with required dependencies.
   * @pre: !initialized
   * @pre: timer is a valid resource handle, memory is a valid base address
   * @post: initialized
   */
  operation_result initialize(address timer, address memory) noexcept;

  /**
   * Spawns a new task from a WASM entry point with priority.
   * @pre: initialized
   * @pre: name is not empty && entry != 0
   * @post: result.is_ok() -> task_id > 0
   */
  std::expected<task_id, recovery_strategy> spawn(std::string_view name, address entry, uint8_t priority) noexcept;

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
  void terminate(task_id id) noexcept;

  /**
   * Gets information about a task.
   * @pre: initialized
   */
  std::expected<task_context_info, bool> query_task(task_id id) noexcept;

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
class channel {
public:
  channel() = default;
  ~channel() = default;

  /**
   * Sends a message block to the channel (blocks if no receiver).
   * @pre: msg is valid
   * @post: msg ownership transferred to channel or receiver
   */
  operation_result send(message msg) noexcept;

  /**
   * Receives a message block from the channel (blocks if empty).
   * @post: msg ownership transferred to caller
   */
  std::expected<message, recovery_strategy> recv() noexcept;

  /**
   * Gets channel usage information.
   */
  std::expected<uint32_t, bool> query() noexcept;

};

/**
 * Tier 2: Task-independent Memory Partition
 * @inv: partition_size <= FB_CONF_TASK_HEAP_SIZE
 */
class memory {
public:
  memory() = default;
  ~memory() = default;

  /**
   * Allocates a block from the task's partition.
   * @pre: size > 0
   * @post: result.is_ok() -> allocated_bytes == old(allocated_bytes) + size
   */
  std::expected<address, recovery_strategy> allocate(byte_count size) noexcept;

  /**
   * Frees a previously allocated block.
   * @pre: ptr != 0
   * @post: allocated_bytes == old(allocated_bytes) - size_of(ptr)
   */
  void deallocate(address ptr) noexcept;

};

} // namespace fireball

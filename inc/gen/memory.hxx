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

/**
 * Information about a memory partition.
 */
struct partition_info {
  partition_kind kind;
  address base;
  byte_count size;
  byte_count available_size;
};

/**
 * Shared memory block for IPC transfer (RAII).
 * Ownership is exclusive: exactly one task owns this at any time.
 * Drop (destructor) automatically deallocates the underlying memory.
 */
class shared_memory {
public:
  shared_memory() = default;
  ~shared_memory() = default;

  /**
   * Gets the local address for direct access via binary_view/span.
   */
  address get_address() noexcept;

  /**
   * Gets the size of this block.
   */
  byte_count get_size() noexcept;

  /**
   * Gets the current owner task.
   */
  task_id get_owner() noexcept;

  /**
   * Releases ownership and produces an IPC-transferable ID.
   * After this call, the resource handle becomes INVALID on the sender side.
   * Receiver must call memory-manager.claim(id) to take ownership.
   * @pre: caller_task_id == owner
   * @post: resource is consumed. Sender must not access address after this.
   * @post: returned shm-id encodes the block for IPC kv-pair transfer.
   */
  shm_id release() noexcept;

};

/**
 * Tier 2: COOS Memory Manager (co_mem)
 * @inv: total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE
 * @inv: active_allocations_count <= FB_CONF_MAX_ALLOCATIONS
 * 
 * Partition usage:
 *   kernel/task: local-only. allocate() returns address. No shm-id.
 *   shared:      IPC transfer data ONLY. allocate-shared() returns shared-memory resource.
 *   guest-ram:   WASM linear memory managed by loader.
 */
class memory_manager {
public:
  memory_manager() = default;
  ~memory_manager() = default;

  /**
   * Initializes the memory manager.
   * @pre: !initialized
   * @pre: pool-base != 0 && pool-size > 0
   * @post: initialized
   */
  operation_result initialize(address pool_base, byte_count pool_size) noexcept;

  /**
   * Allocates a local memory block (kernel or task partition).
   * Returns a raw address for immediate use via binary_view/span.
   * @pre: initialized
   * @pre: kind == kernel || kind == task
   * @pre: size > 0 && size <= FB_CONF_MAX_ALLOC_SIZE
   * @post: result.is_ok() -> block.owner == caller_task_id
   */
  std::expected<address, recovery_strategy> allocate(byte_count size, partition_kind kind) noexcept;

  /**
   * Allocates a shared memory block for IPC transfer.
   * Returns a shared-memory resource with RAII ownership semantics.
   * @pre: initialized
   * @pre: size > 0 && size <= FB_CONF_MAX_ALLOC_SIZE
   * @post: result.is_ok() -> resource.owner == caller_task_id
   */
  std::expected<uintptr_t, recovery_strategy> allocate_shared(byte_count size) noexcept;

  /**
   * Claims ownership of a shared memory block received via IPC.
   * @pre: id was produced by shared-memory.release()
   * @post: caller becomes the new owner. Returned resource is valid.
   */
  std::expected<uintptr_t, recovery_strategy> claim(shm_id id) noexcept;

  /**
   * Releases a local memory block (kernel/task) by address.
   * @pre: initialized && addr is an active allocation
   * @pre: block.kind != shared (use shared-memory drop instead)
   * @pre: caller_task_id == block.owner
   * @post: total_allocated_bytes decremented by block size
   */
  void deallocate(address addr) noexcept;

  /**
   * Gets information about a partition.
   * @pre: initialized
   */
  std::expected<partition_info, bool> query_partition(partition_kind kind) noexcept;

};

} // namespace fireball

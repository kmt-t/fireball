/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/memory_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <optional>
#include <tuple>
#include <concepts>

namespace fireball {

/**
 * Information about a memory partition.
 */
struct partition_metadata_record {
  partition_category category;
  mem_address base;
  mem_byte_count size;
  mem_byte_count available;
};

/**
 * Shared memory block for IPC transfer (RAII).
 * Ownership is exclusive: exactly one task owns this at any time.
 * Drop (destructor) automatically deallocates the underlying memory.
 */
class shared_block {
public:
  shared_block() = default;
  ~shared_block() = default;

  /**
   * Gets the local address for direct access via binary_view/span.
   */
  mem_address get_address() noexcept;

  /**
   * Gets the size of this block.
   */
  mem_byte_count get_size() noexcept;

  /**
   * Gets the current owner task.
   */
  os_task_id get_owner() noexcept;

  /**
   * Releases ownership and produces an IPC-transferable ID.
   * After this call, the resource handle becomes INVALID on the sender side.
   * Receiver must call memory-manager.claim(id) to take ownership.
   * @pre: caller_task_id == owner
   * @post: resource is consumed. Sender must not access address after this.
   * @post: returned shm-id encodes the block for IPC kv-pair transfer.
   */
  mem_shm_id release() noexcept;

};

/**
 * Tier 2: COOS Memory Manager (co_mem)
 * @inv: total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE
 * @inv: active_allocations_count <= FB_CONF_MAX_ALLOCATIONS
 * @constexpr: POOL_SIZE = FB_CONF_MEMORY_POOL_SIZE
 * @constexpr: MAX_ALLOCATIONS = FB_CONF_MAX_ALLOCATIONS
 * 
 * Partition usage:
 *   kernel/task: local-only. allocate() returns address. No shm-id.
 *   shared:      IPC transfer data ONLY. allocate-shared() returns shared-memory resource.
 *   guest-ram:   WASM linear memory managed by loader.
 */
class pool_manager {
public:
  static constexpr auto POOL_SIZE = FB_CONF_MEMORY_POOL_SIZE;
  static constexpr auto MAX_ALLOCATIONS = FB_CONF_MAX_ALLOCATIONS;
  pool_manager() = default;
  ~pool_manager() = default;

  /**
   * Initializes the memory manager.
   * @pre: !initialized
   * @pre: pool-base != 0 && pool-size > 0
   * @post: initialized
   */
  operation_result init_manager(mem_address pool_base, mem_byte_count pool_size) noexcept;

  /**
   * Allocates a local memory block (kernel or task partition).
   * Returns a raw address for immediate use via binary_view/span.
   * @pre: initialized
   * @pre: kind == kernel || kind == task
   * @pre: size > 0 && size <= FB_CONF_MAX_ALLOC_SIZE
   * @post: result.is_ok() -> block.owner == caller_task_id
   */
  result<mem_address, sys_recovery_strategy> allocate(mem_byte_count size, partition_category category) noexcept;

  /**
   * Allocates a shared memory block for IPC transfer.
   * Returns a shared-memory resource with RAII ownership semantics.
   * @pre: initialized
   * @pre: size > 0 && size <= FB_CONF_MAX_ALLOC_SIZE
   * @post: result.is_ok() -> resource.owner == caller_task_id
   */
  result<shared_block*, sys_recovery_strategy> allocate_shared(mem_byte_count size) noexcept;

  /**
   * Claims ownership of a shared memory block received via IPC.
   * @pre: id was produced by shared-memory.release()
   * @post: caller becomes the new owner. Returned resource is valid.
   */
  result<shared_block*, sys_recovery_strategy> claim(mem_shm_id id) noexcept;

  /**
   * Releases a local memory block (kernel/task) by address.
   * @pre: initialized && addr is an active allocation
   * @pre: block.kind != shared (use shared-memory drop instead)
   * @pre: caller_task_id == block.owner
   * @post: total_allocated_bytes decremented by block size
   */
  void deallocate(mem_address addr) noexcept;

  /**
   * Gets information about a partition.
   * @pre: initialized
   */
  result<partition_metadata_record, bool> get_partition_info(partition_category category) noexcept;

};

} // namespace fireball

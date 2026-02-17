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
 * Information about a memory partition.
 */
struct partition_info {
  partition_kind kind;
  address base;
  byte_count size;
  byte_count available_size;
};

/**
 * Tier 2: COOS Memory Manager (co_mem)
 * @inv: total_allocated_bytes <= FB_CONF_MEMORY_POOL_SIZE
 * @inv: active_allocations_count <= FB_CONF_MAX_ALLOCATIONS
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
   * Allocates a block of memory from the specified partition.
   * @pre: initialized
   * @pre: size > 0 && size <= FB_CONF_MAX_ALLOC_SIZE
   * @post: result.is_ok() -> total_allocated_bytes == old(total_allocated_bytes) + size
   */
  std::expected<shm_id, recovery_strategy> allocate(byte_count size, partition_kind kind) noexcept;

  /**
   * Releases a memory block.
   * @pre: initialized && id is a valid active shm_id
   * @post: total_allocated_bytes == old(total_allocated_bytes) - size_of(id)
   */
  void deallocate(shm_id id) noexcept;

  /**
   * Translates shm_id to a host virtual address (thin glue).
   * @pre: initialized && id exists
   */
  std::expected<address, bool> to_address(shm_id id) noexcept;

  /**
   * Reverse translation: address to shm_id.
   * @pre: initialized && addr != 0
   */
  std::expected<shm_id, bool> to_shm(address addr) noexcept;

  /**
   * Gets information about a memory block.
   * @pre: initialized && id exists
   */
  std::expected<memory_info, bool> query(shm_id id) noexcept;

  /**
   * Gets information about a partition.
   * @pre: initialized
   */
  std::expected<partition_info, bool> query_partition(partition_kind kind) noexcept;

  /**
   * Checks ownership of a memory block for a guest task.
   * @pre: initialized && id exists && task_id != 0
   */
  bool check_ownership(shm_id id, uint32_t task_id) noexcept;

};

} // namespace fireball

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
 * Memory partition categories for isolation.
 */
enum class partition_category : uint8_t {
  KERNEL,
  TASK,
  SHARED,
  GUEST_RAM,
};

/**
 * Information about a memory block.
 * @inv: owner != 0 (always associated with a task)
 */
struct block_metadata_record {
  mem_shm_id id;
  mem_byte_count size;
  partition_category category;
  os_task_id owner;
};

} // namespace fireball

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
 * Memory partition types for isolation.
 */
enum class partition_kind : uint8_t {
  KERNEL,
  TASK,
  SHARED,
  GUEST_RAM,
};

/**
 * Information about a memory block.
 * @inv: owner != 0 (always associated with a task)
 */
struct memory_info {
  shm_id id;
  byte_count size;
  partition_kind kind;
  task_id owner;
};

} // namespace fireball

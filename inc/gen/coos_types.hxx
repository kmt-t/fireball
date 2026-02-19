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
 * Task states in the cooperative scheduler.
 */
enum class task_state_category : uint8_t {
  READY,
  RUNNING,
  BLOCKED,
  INTERRUPTED,
  TERMINATED,
};

/**
 * Task context managed by the kernel.
 * @inv: id > 0 && id <= FB_CONF_MAX_TASKS
 */
struct task_context_record {
  os_task_id id;
  task_state_category state;
  std::string_view name;
};

} // namespace fireball

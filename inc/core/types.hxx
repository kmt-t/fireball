/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <optional>
#include <tuple>
#include <concepts>

namespace fireball {

/**
 * Device identifier for HAL.
 */
using hal_device_id = uint32_t;

/**
 * Shared memory identifier.
 */
using mem_shm_id = uint32_t;

/**
 * Channel identifier for IPC.
 */
using ipc_channel_id = uint32_t;

/**
 * Task identifier for OS.
 */
using os_task_id = uint32_t;

/**
 * System-wide service identifier.
 */
using sys_service_id = uint32_t;

/**
 * Memory address.
 */
using mem_address = uint32_t;

/**
 * Byte count for memory sizes.
 */
using mem_byte_count = uint32_t;

/**
 * Byte offset from a base.
 */
using mem_byte_offset = uint32_t;

/**
 * Entry count for tables or arrays.
 */
using mem_entry_count = uint32_t;

/**
 * Instruction count for execution.
 */
using wasm_instruction_count = uint32_t;

/**
 * Handle to a URI string.
 */
using sys_uri_handle = uint32_t;

/**
 * Binary view reference (read-only span).
 */
/**
 * Binary view reference (read-write span).
 */
/**
 * Log level for system messages.
 */
enum class sys_log_level : uint8_t {
  DEBUG,
  INFO,
  WARN,
  ERROR,
  FATAL,
};

/**
 * Recovery strategy for operation failures.
 */
enum class sys_recovery_strategy : uint8_t {
  IGNORE,
  RETRY,
  RESTART,
  PANIC,
};

/**
 * Result type for operations without success value.
 */
/**
 * Scope type for IPC messages (upper 3 bits of type_scope field).
 */
enum class ipc_scope_category : uint8_t {
  FUNCTIONAL,
  DICTIONARY,
  RESERVED_2,
  RESERVED_3,
  RESERVED_4,
  RESERVED_5,
  RESERVED_6,
  RESERVED_7,
};

/**
 * Data type for IPC messages (lower 5 bits of type_scope field).
 */
enum class ipc_data_category : uint8_t {
  IMMEDIATE,
  HANDLE,
  RESOURCE_ID,
  CONSTANT_REF,
  RESERVED_4,
  RESERVED_5,
  RESERVED_6,
  RESERVED_7,
  RESERVED_8,
  RESERVED_9,
  RESERVED_10,
  RESERVED_11,
  RESERVED_12,
  RESERVED_13,
  RESERVED_14,
  RESERVED_15,
  RESERVED_16,
  RESERVED_17,
  RESERVED_18,
  RESERVED_19,
  RESERVED_20,
  RESERVED_21,
  RESERVED_22,
  RESERVED_23,
  RESERVED_24,
  RESERVED_25,
  RESERVED_26,
  RESERVED_27,
  RESERVED_28,
  RESERVED_29,
  RESERVED_30,
  RESERVED_31,
};

/**
 * IPC Key-Value pair for structured messaging.
 * FINALIZED bitfield layout:
 *   [63:32] value  : u32 — payload or handle
 *   [31:8]  key    : u24 — service-defined key identifier
 *   [7:5]   scope  : u3  — ipc-scope-category (functional, dictionary, ...)
 *   [4:0]   dtype  : u5  — ipc-data-category  (immediate, handle, resource-id, ...)
 * @inv: sizeof(ipc-kv-pair) == 8 bytes
 */
struct ipc_kv_pair {
  uint64_t raw;
};

/**
 * IPC Message containing fixed maximum of 8 KV-pairs.
 * @inv: len(pairs) <= 8
 * @note FINALIZED: 8 * 8 = 64 bytes max per message. Fits in single cache line on most architectures.
 */
struct ipc_message {
  data_range<ipc_kv_pair> pairs;
};

} // namespace fireball

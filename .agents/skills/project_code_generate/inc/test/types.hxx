/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <fireball_config.hxx>
#include <cstdint>

namespace fireball {

using device_id = uint32_t;
using shm_id = uint32_t;
using channel_id = uint32_t;
using task_id = uint32_t;
using service_id = uint32_t;
using uri_handle = shm_id;
using byte_count = uint32_t;
using address = uint32_t;
using operation_result = operation_result;

/**
 * Recovery strategy for operation failures.
 */
enum class recovery_strategy : uint8_t {
  IGNORE,
  RETRY,
  RESTART,
  PANIC,
};

/**
 * Severity levels for system-wide logging.
 */
enum class log_level : uint8_t {
  TRACE,
  DEBUG,
  INFO,
  WARN,
  ERROR,
  FATAL,
};

/**
 * Scope type (upper 3 bits of type_scope field).
 */
enum class scope_type : uint8_t {
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
 * Data type (lower 5 bits of type_scope field).
 */
enum class data_type : uint8_t {
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
 * IPC Key-Value pair with bitfield structure.
 * Type scope field: upper 3 bits = scope type, lower 5 bits = data type
 */
struct kv_pair {
  uint64_t type_scope : 8;  // Bits 0-7
  uint64_t key : 24;  // Bits 8-31
  uint64_t value : 32;  // Bits 32-63
};
static_assert(sizeof(kv_pair) == 8, "kv_pair size mismatch");

/**
 * IPC Message containing up to 8 KV pairs.
 */
struct message {
  list<kv_pair> pairs;
};

} // namespace fireball

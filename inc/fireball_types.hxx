/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <stdint.h>
#include <variant>
#include <optional>
#include <span>
#include <fireball_config.hxx>

namespace fireball {

// ========================================
// Basic Types (WIT Mappings)
// ========================================
using u32 = uint32_t;
using u64 = uint64_t;

// ========================================
// Primitive Type Aliases (fireball_vocabulary)
// ========================================
using mem_address = uint32_t;
using mem_byte_offset = uint32_t;
using mem_byte_count = uint32_t;
using mem_entry_count = uint32_t;
using wasm_instruction_count = uint32_t;
using function_index = uint32_t;
using shift_amount = uint8_t;
using interrupt_flags = uint32_t;

// ========================================
// Composite Type Aliases
// ========================================
using binary_view = std::span<const uint8_t>;
using mutable_binary_view = std::span<uint8_t>;

template <typename T>
using data_range = std::span<T>;

// ========================================
// Result and Recovery (Infrastructure)
// ========================================

// These forward declarations will be resolved by including gen/types.hxx later
enum class sys_recovery_strategy : uint8_t;
enum class sys_log_level : uint8_t;

template <typename T, typename E = sys_recovery_strategy>
class [[nodiscard]] result {
public:
  result(T val) : data_(std::move(val)) {}
  result(E err) : data_(err) {}

  bool has_value() const noexcept { return std::holds_alternative<T>(data_); }
  bool has_error() const noexcept { return std::holds_alternative<E>(data_); }

  T& value() & { return std::get<T>(data_); }
  const T& value() const & { return std::get<T>(data_); }
  E& error() & { return std::get<E>(data_); }
  const E& error() const & { return std::get<E>(data_); }

  T& operator*() & { return value(); }
  const T& operator*() const & { return value(); }
  T* operator->() noexcept { return &value(); }
  const T* operator->() const noexcept { return &value(); }

private:
  std::variant<T, E> data_;
};

// Partial specialization for void
template <typename E>
class [[nodiscard]] result<void, E> {
public:
  result() : err_(std::nullopt) {}
  result(E err) : err_(err) {}

  bool has_value() const noexcept { return !err_.has_value(); }
  bool has_error() const noexcept { return err_.has_value(); }

  void value() const {}
  E& error() & { return *err_; }
  const E& error() const & { return *err_; }

private:
  std::optional<E> err_;
};

using operation_result = result<void, sys_recovery_strategy>;

/**
 * @brief Base interface for all system components.
 */
struct component {
  virtual ~component() = default;
  virtual operation_result initialize() = 0;
};

// ========================================
// Component Specific Aliases (Common)
// ========================================
using mem_shm_id = uint32_t;
using hal_device_id = uint32_t;
using ipc_channel_id = uint32_t;
using os_task_id = uint32_t;
using sys_service_id = uint32_t;
using wasm_pc = mem_byte_offset;
using ipc_message_handle = mem_shm_id;

} // namespace fireball

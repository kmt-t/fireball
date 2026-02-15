/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <cstdint>
#include <memory>
#include <span>

namespace fireball {

using guest_phys_addr = std::uint32_t;
using byte_offset = std::uint32_t;
using byte_count = std::uint32_t;
using entry_count = std::uint16_t;

enum struct recovery_strategy : std::uint8_t {
  NO_PROBLEM,
  RETRY,
  COMPONENT_REBOOT,
  SYSTEM_PANIC,
};

template <typename T = void> struct result {
  std::optional<T> value;
  recovery_strategy error;
};

template <> struct result<void> {
  recovery_strategy error;
};

struct co_mem;

struct shm_handle {
  co_mem* mem;
  byte_offset offset;
  byte_count size;
};

using operation_result = result<void>;
using binary_view = std::span<const std::uint8_t>;
using interrupt_flags = std::uint32_t;
using wasm_pc = std::uint32_t;

} // namespace fireball

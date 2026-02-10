/**
 * The Fireball is Wasm Hypervisor.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

namespace fireball {

enum struct recovery_strategy : std::uint8_t {
  SUCCEEDED,
  OPERATION_RETRY,
  COMPONENT_REBOOT,
  SYSTEM_PANIC,
}; // enum struct recovery_strategy : std::uint8_t

} // namespace fireball

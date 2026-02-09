/**
 * Fireball Central Type Definitions.
 * Based on fireball_vocabulary.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#pragma once

#include <cstdint>
#include <cstddef>
#include <span>
#include <string_view>

namespace fireball {

// ========================================
// Primitive Aliases
// ========================================
using address = std::uint32_t;
using offset = std::uint32_t;
using byte_count = std::uint32_t;
using entry_count = std::uint32_t;
using instruction_count = std::uint32_t;
using function_index = std::uint32_t;
using shift_amount = std::uint8_t;
using interrupt_flags = std::uint32_t;

// ========================================
// View Types (Non-owning)
// ========================================
using binary_view = std::span<const std::uint8_t>;
using mutable_binary_view = std::span<std::uint8_t>;
using string_view = std::string_view;

// ========================================
// Component Specific Aliases
// ========================================
using wasm_pc = offset;
using wasm_opcode = std::uint8_t;
using code_offset = std::uint16_t;
using card_index = std::uint16_t;
using module_id = std::uint32_t;
using task_id = std::uint16_t;
using channel_id = std::uint32_t;

// ========================================
// Error and Status (Based on wit/types.wit)
// ========================================
// ========================================
// Result and Strategy (Based on wit/types.wit)
// ========================================

// 簡易的な result 型の前方宣言。実体は別途定義が必要。
template <typename T, typename E> class result;

enum class recovery_strategy {
  retryable,
  fatal
};

using operation_result = result<void, recovery_strategy>;

} // namespace fireball
/**
 * Fireball Central Type Definitions.
 * Based on fireball_vocabulary.
 *
 * Copyright (c) 2025 Takuya Matsunaga.
 */
#ifndef __FIREBALL_TYPES_HXX__
#define __FIREBALL_TYPES_HXX__

#include <cstdint>
#include <cstddef>
#include <span>
#include <string_view>

namespace fireball {

// ========================================
// Primitive Aliases
// ========================================
using address_t           = std::uint32_t;
using offset_t            = std::uint32_t;
using byte_count_t        = std::uint32_t;
using entry_count_t       = std::uint32_t;
using instruction_count_t = std::uint32_t;
using function_index_t    = std::uint32_t;
using shift_amount_t      = std::uint8_t;
using interrupt_flags_t   = std::uint32_t;

// ========================================
// View Types (Non-owning)
// ========================================
using binary_view_t = std::span<const std::uint8_t>;
using mutable_binary_view_t = std::span<std::uint8_t>;

template<typename T>
using data_range_t = std::span<T>;

using string_view_t = std::string_view;

// ========================================
// Component Specific Aliases
// ========================================
using wasm_pc_t       = offset_t;
using wasm_opcode_t   = std::uint8_t;
using code_offset_t   = std::uint16_t;
using card_index_t    = std::uint16_t;
using module_id_t     = std::uint32_t;
using task_id_t       = std::uint16_t;
using channel_id_t    = std::uint32_t;

} // namespace fireball

#endif // __FIREBALL_TYPES_HXX__

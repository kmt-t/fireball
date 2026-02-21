/**
 * Auto-generated from WIT. Do not edit.
 */
#pragma once

#include <fireball_types.hxx>
#include <gen/types.hxx>
#include <fireball_config.hxx>
#include <cstdint>
#include <string_view>
#include <optional>
#include <tuple>
#include <concepts>

namespace fireball {

/**
 * Performs a low-level host call with raw arguments.
 * Variants for optimization based on argument count.
 */
void fireball_call0(uint32_t id) noexcept;

void fireball_call1(uint32_t id, uint32_t a0) noexcept;

void fireball_call2(uint32_t id, uint32_t a0, uint32_t a1) noexcept;

void fireball_call3(uint32_t id, uint32_t a0, uint32_t a1, uint32_t a2) noexcept;

void fireball_call4(uint32_t id, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3) noexcept;

void fireball_call5(uint32_t id, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, uint32_t a4) noexcept;

void fireball_call6(uint32_t id, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, uint32_t a4, uint32_t a5) noexcept;

} // namespace fireball

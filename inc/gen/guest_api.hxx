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

void log(log_level lvl, uint32_t dict_id, uint32_t value) noexcept;

void ipc_call(uint32_t chan, message data) noexcept;

} // namespace fireball

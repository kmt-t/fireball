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
 * JIT Entry linking WASM PC to native code offset.
 */
struct jit_entry {
  address wasm_pc;
  address native_offset;
};

/**
 * JIT configuration parameters.
 */
struct jit_config {
  byte_count bank_size;
  entry_count max_entries;
  uint8_t card_shift;
  uint8_t align_shift;
};

} // namespace fireball

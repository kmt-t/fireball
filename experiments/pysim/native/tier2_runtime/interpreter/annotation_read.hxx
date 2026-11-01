// Field readers of the Interpreter annotation pool. Handlers and the JIT trace compiler read the same
// annotation fields through these primitives, so a field's size is fixed by its reader.
#pragma once

#include <cstdint>
#include <cstring>

namespace fireball::wasm_runtime {

/** @brief Reads one u8 annotation field and advances the cursor past it. */
inline std::uint8_t read_u8(const std::uint8_t *&rel) { return *rel++; }

/** @brief Reads one fixed-width little-endian field and advances the cursor past it. */
template <typename T> inline T read_fixed(const std::uint8_t *&rel) {
  T value;
  std::memcpy(&value, rel, sizeof(value));
  rel += sizeof(value);
  return value;
}

/** @brief Reads one little-endian u16 annotation field and advances the cursor past it. */
inline std::uint16_t read_u16(const std::uint8_t *&rel) { return read_fixed<std::uint16_t>(rel); }

/** @brief Reads one little-endian u32 annotation field and advances the cursor past it. */
inline std::uint32_t read_u32(const std::uint8_t *&rel) { return read_fixed<std::uint32_t>(rel); }

/** @brief Reads one little-endian i32 annotation field and advances the cursor past it. */
inline std::int32_t read_s32(const std::uint8_t *&rel) {
  return static_cast<std::int32_t>(read_u32(rel));
}

/** @brief Reads one little-endian i64 annotation field and advances the cursor past it. */
inline std::int64_t read_s64(const std::uint8_t *&rel) {
  return static_cast<std::int64_t>(read_fixed<std::uint64_t>(rel));
}

/** @brief Reads the u8 at the cursor without advancing it. */
inline std::uint8_t peek_u8(const std::uint8_t *rel) { return *rel; }

} // namespace fireball::wasm_runtime
